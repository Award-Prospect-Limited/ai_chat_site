from __future__ import annotations

import json
import logging
import math
import threading
import time
from dataclasses import dataclass

from google import genai
from google.genai import types

from .db import get_db


@dataclass(frozen=True)
class MemoryHit:
    content: str
    score: float


def _cosine(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return -1.0
    dot = 0.0
    na = 0.0
    nb = 0.0
    for x, y in zip(a, b):
        dot += x * y
        na += x * x
        nb += y * y
    den = math.sqrt(na) * math.sqrt(nb)
    if den <= 0:
        return -1.0
    return dot / den


log = logging.getLogger(__name__)


def embed_texts(*, api_key: str, model: str, texts: list[str], dim: int = 768) -> list[list[float]]:
    client = genai.Client(api_key=api_key)
    config = types.EmbedContentConfig(output_dimensionality=dim) if dim else None
    resp = client.models.embed_content(model=model, contents=texts, config=config)
    out: list[list[float]] = []
    for emb_obj in getattr(resp, "embeddings", None) or []:
        values = getattr(emb_obj, "values", None) or []
        out.append([float(x) for x in values])
    return out


def embed_text(*, api_key: str, model: str, text: str, dim: int = 768) -> list[float]:
    embs = embed_texts(api_key=api_key, model=model, texts=[text], dim=dim)
    return embs[0] if embs else []


def remember_message(
    *,
    user_id: int,
    role: str,
    content: str,
    api_key: str,
    embed_model: str,
    max_items: int,
    embed_dim: int = 768,
    source_conversation_id: int | None = None,
    source_message_id: int | None = None,
):
    content = (content or "").strip()
    if len(content) < 12:
        return
    if len(content) > 4000:
        content = content[:4000]

    try:
        emb = embed_text(api_key=api_key, model=embed_model, text=content, dim=embed_dim)
    except Exception:
        log.warning("memory embed failed", exc_info=True)
        emb = []

    db = get_db()
    db.execute(
        """
        INSERT INTO memory_items(user_id, role, content, embedding_json, source_conversation_id, source_message_id, embed_model)
        VALUES(?,?,?,?,?,?,?)
        """,
        (
            int(user_id),
            "user" if role == "user" else "model",
            content,
            json.dumps(emb) if emb else None,
            int(source_conversation_id) if source_conversation_id else None,
            int(source_message_id) if source_message_id else None,
            embed_model if emb else None,
        ),
    )

    # Prune oldest.
    try:
        max_items = int(max_items)
    except Exception:
        max_items = 2000
    if max_items > 0:
        db.execute(
            """
            DELETE FROM memory_items
            WHERE user_id=?
              AND id NOT IN (
                SELECT id FROM memory_items WHERE user_id=? ORDER BY id DESC LIMIT ?
              )
            """,
            (int(user_id), int(user_id), int(max_items)),
        )

    db.commit()


def recall(
    *,
    user_id: int,
    api_key: str,
    embed_model: str,
    query: str,
    top_k: int,
    embed_dim: int = 768,
) -> list[MemoryHit]:
    query = (query or "").strip()
    if not query:
        return []

    try:
        qv = embed_text(api_key=api_key, model=embed_model, text=query, dim=embed_dim)
    except Exception:
        log.warning("memory recall embed failed", exc_info=True)
        return []
    if not qv:
        return []

    db = get_db()
    rows = db.execute(
        """
        SELECT content, embedding_json
        FROM memory_items
        WHERE user_id=? AND embedding_json IS NOT NULL AND embed_model=?
        ORDER BY id DESC
        LIMIT 1500
        """,
        (int(user_id), embed_model),
    ).fetchall()

    hits: list[MemoryHit] = []
    for r in rows:
        try:
            ev = json.loads(r["embedding_json"] or "[]")
            if not isinstance(ev, list) or not ev:
                continue
            ev = [float(x) for x in ev]
        except Exception:
            continue
        score = _cosine(qv, ev)
        if score <= 0.15:
            continue
        hits.append(MemoryHit(content=str(r["content"]), score=score))

    hits.sort(key=lambda x: x.score, reverse=True)
    try:
        top_k = int(top_k)
    except Exception:
        top_k = 5
    return hits[: max(0, min(top_k, 20))]



def backfill_embeddings(app, *, batch: int = 50, max_rows: int = 20000) -> int:
    """给缺向量（或向量来自旧模型）的记忆补算 embedding。返回处理条数。"""
    api_key = app.config.get("GEMINI_API_KEY")
    model = str(app.config.get("MEMORY_EMBED_MODEL") or "gemini-embedding-001")
    dim = int(app.config.get("MEMORY_EMBED_DIM") or 768)
    if not api_key:
        return 0
    done = 0
    with app.app_context():
        db = get_db()
        while done < max_rows:
            rows = db.execute(
                """
                SELECT id, content FROM memory_items
                WHERE embed_model IS NULL OR embed_model != ?
                ORDER BY id DESC LIMIT ?
                """,
                (model, batch),
            ).fetchall()
            if not rows:
                break
            texts = [str(r["content"] or "")[:4000] or "-" for r in rows]
            try:
                embs = embed_texts(api_key=api_key, model=model, texts=texts, dim=dim)
            except Exception:
                log.warning("memory backfill failed; will retry on next start", exc_info=True)
                break
            if len(embs) != len(rows):
                break
            for r, emb in zip(rows, embs):
                db.execute(
                    "UPDATE memory_items SET embedding_json=?, embed_model=? WHERE id=?",
                    (json.dumps(emb), model, int(r["id"])),
                )
            db.commit()
            done += len(rows)
            time.sleep(0.5)
    if done:
        log.info("memory backfill: %s rows re-embedded with %s", done, model)
    return done


def start_backfill_thread(app):
    if not app.config.get("MEMORY_BACKFILL_ON_START", True) or app.config.get("TESTING"):
        return
    threading.Thread(target=backfill_embeddings, args=(app,), name="memory-backfill", daemon=True).start()
