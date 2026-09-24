from __future__ import annotations

import json
import os
import queue
import re
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from flask import (
    Blueprint,
    Response,
    abort,
    current_app,
    jsonify,
    render_template,
    request,
    send_file,
    stream_with_context,
)
from flask_login import current_user, login_required
from werkzeug.utils import secure_filename

from .. import limiter
from ..db import get_db
from ..files_service import (
    InlineBudget,
    extract_text,
    guess_mime,
    is_image,
    is_native,
    load_rows,
    parse_ids,
    public_info,
    save_file,
    upload_dir,
)
from ..gemini_service import ChatOptions, Turn, generate_title, ocr_file, stream_reply
from ..memory_service import recall, remember_message
from ..model_catalog import (
    DEFAULT_CHAT_MODEL,
    IMAGE_ASPECT_RATIOS,
    THINKING_LEVELS,
    available_models,
    find_model,
)


bp = Blueprint("chat", __name__)

HISTORY_LIMIT = 40
DEFAULT_TITLES = {"新对话", "默认对话"}
KEEPALIVE_SECONDS = 5
MAX_REPLY_CHARS = 200_000


def _uid() -> int:
    return int(current_user.id)


def _tz():
    tz_name = current_app.config.get("TIMEZONE") or os.getenv("TZ") or "Asia/Shanghai"
    try:
        return ZoneInfo(str(tz_name))
    except Exception:  # noqa: BLE001
        return timezone.utc


def _now_boundaries_utc() -> tuple[str, str]:
    now_local = datetime.now(tz=_tz())
    week_start_local = (now_local - timedelta(days=now_local.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
    month_start_local = now_local.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    fmt = "%Y-%m-%d %H:%M:%S"
    return week_start_local.astimezone(timezone.utc).strftime(fmt), month_start_local.astimezone(timezone.utc).strftime(fmt)


def _sum_tokens(user_id: int, *, conversation_id: int | None = None, since_utc: str | None = None) -> int:
    sql = "SELECT COALESCE(SUM(COALESCE(total_tokens,0)),0) AS s FROM chat_messages WHERE user_id=?"
    params: list = [int(user_id)]
    if conversation_id is not None:
        sql += " AND conversation_id=?"
        params.append(int(conversation_id))
    if since_utc is not None:
        sql += " AND created_at>=?"
        params.append(str(since_utc))
    row = get_db().execute(sql, tuple(params)).fetchone()
    return int(row["s"] or 0) if row else 0


def _stats(user_id: int, conversation_id: int) -> dict:
    week_start_utc, month_start_utc = _now_boundaries_utc()
    return {
        "conversation_id": conversation_id,
        "current_chat_tokens": _sum_tokens(user_id, conversation_id=conversation_id),
        "week_tokens": _sum_tokens(user_id, since_utc=week_start_utc),
        "month_tokens": _sum_tokens(user_id, since_utc=month_start_utc),
        "total_tokens": _sum_tokens(user_id),
    }


def _get_or_create_default_conversation(user_id: int) -> int:
    db = get_db()
    row = db.execute(
        "SELECT id FROM conversations WHERE user_id=? ORDER BY updated_at DESC, id DESC LIMIT 1",
        (user_id,),
    ).fetchone()
    if row:
        return int(row["id"])
    cur = db.execute("INSERT INTO conversations(user_id, title) VALUES(?,?)", (user_id, "新对话"))
    db.commit()
    return int(cur.lastrowid)


def _require_conversation(user_id: int, conversation_id) -> int:
    try:
        conversation_id = int(conversation_id or 0)
    except (TypeError, ValueError):
        conversation_id = 0
    if not conversation_id:
        return _get_or_create_default_conversation(user_id)
    row = get_db().execute(
        "SELECT id FROM conversations WHERE id=? AND user_id=?",
        (conversation_id, user_id),
    ).fetchone()
    if row:
        return int(row["id"])
    return _get_or_create_default_conversation(user_id)


def _conversation_or_404(conversation_id: int):
    row = get_db().execute(
        "SELECT * FROM conversations WHERE id=? AND user_id=?",
        (conversation_id, _uid()),
    ).fetchone()
    if not row:
        abort(404)
    return row


def _json_or_none(v):
    if not v:
        return None
    try:
        return json.loads(v)
    except ValueError:
        return None


def _message_dicts(user_id: int, rows) -> list[dict]:
    all_ids: list[int] = []
    for r in rows:
        all_ids += parse_ids(r["attachments_json"])
    files = load_rows(user_id, all_ids)
    out = []
    for r in rows:
        d = {
            "id": int(r["id"]),
            "role": r["role"],
            "content": r["content"],
            "created_at": r["created_at"],
            "model_name": r["model_name"],
            "total_tokens": r["total_tokens"],
            "thoughts": r["thoughts"] or "",
            "grounding": _json_or_none(r["grounding_json"]),
            "meta": _json_or_none(r["meta_json"]) or {},
            "attachments": [public_info(files[i]) for i in parse_ids(r["attachments_json"]) if i in files],
        }
        out.append(d)
    return out


# ---------------------------------------------------------------- 页面


@bp.get("/chat")
@login_required
def index():
    allowed = current_app.config.get("GEMINI_ALLOWED_MODELS") or []
    models = [m.to_dict() for m in available_models(allowed)]
    default_model = current_app.config.get("GEMINI_MODEL") or DEFAULT_CHAT_MODEL
    if not find_model(default_model, allowed):
        default_model = models[0]["id"] if models else DEFAULT_CHAT_MODEL
    return render_template(
        "chat/index.html",
        boot={
            "models": models,
            "defaultModel": default_model,
            "thinkingLevels": list(THINKING_LEVELS),
            "aspectRatios": list(IMAGE_ASPECT_RATIOS),
            "memoryDefault": bool(current_app.config.get("MEMORY_ENABLED_DEFAULT", True)),
            "maxFiles": int(current_app.config.get("UPLOAD_MAX_FILES") or 10),
            "maxUploadBytes": int(current_app.config.get("MAX_UPLOAD_BYTES") or 0),
            "maxMessageChars": int(current_app.config.get("MAX_MESSAGE_CHARS") or 32000),
            "allowedExt": list(current_app.config.get("UPLOAD_ALLOWED_EXT") or []),
            "username": current_user.username,
            "isAdmin": bool(getattr(current_user, "is_admin", False)),
        },
    )


# ---------------------------------------------------------------- 会话


@bp.get("/api/conversations")
@login_required
def api_conversations():
    q = (request.args.get("q") or "").strip()
    sql = """
        SELECT c.id, c.title, c.updated_at, c.pinned,
          COALESCE((SELECT SUM(COALESCE(m.total_tokens, 0)) FROM chat_messages m
                    WHERE m.user_id=c.user_id AND m.conversation_id=c.id), 0) AS total_tokens
        FROM conversations c
        WHERE c.user_id=?
    """
    params: list = [_uid()]
    if q:
        like = f"%{q.replace('%', '').replace('_', '')}%"
        sql += """ AND (c.title LIKE ? OR EXISTS (
                    SELECT 1 FROM chat_messages m WHERE m.conversation_id=c.id AND m.user_id=c.user_id AND m.content LIKE ?))"""
        params += [like, like]
    sql += " ORDER BY c.pinned DESC, c.updated_at DESC, c.id DESC LIMIT 300"
    rows = get_db().execute(sql, tuple(params)).fetchall()
    return jsonify({"conversations": [dict(r) for r in rows]})


@bp.post("/api/conversations")
@login_required
@limiter.limit("20 per minute")
def api_conversation_create():
    data = request.get_json(silent=True) or {}
    title = (data.get("title") or "").strip()[:80] or "新对话"
    system_prompt = (data.get("system_prompt") or "").strip()[:8000] or None
    db = get_db()
    cur = db.execute(
        "INSERT INTO conversations(user_id, title, system_prompt) VALUES(?,?,?)",
        (_uid(), title, system_prompt),
    )
    db.commit()
    return jsonify({"id": int(cur.lastrowid), "title": title})


@bp.get("/api/conversations/<int:conversation_id>")
@login_required
def api_conversation_get(conversation_id: int):
    c = _conversation_or_404(conversation_id)
    return jsonify(
        {
            "id": int(c["id"]),
            "title": c["title"],
            "system_prompt": c["system_prompt"] or "",
            "pinned": bool(c["pinned"]),
        }
    )


@bp.patch("/api/conversations/<int:conversation_id>")
@login_required
@limiter.limit("60 per minute")
def api_conversation_update(conversation_id: int):
    _conversation_or_404(conversation_id)
    data = request.get_json(silent=True) or {}
    sets, params = [], []
    if "title" in data:
        title = (data.get("title") or "").strip()[:80]
        if not title:
            return jsonify({"error": "标题不能为空"}), 400
        sets.append("title=?")
        params.append(title)
    if "system_prompt" in data:
        sets.append("system_prompt=?")
        params.append((data.get("system_prompt") or "").strip()[:8000] or None)
    if "pinned" in data:
        sets.append("pinned=?")
        params.append(1 if data.get("pinned") else 0)
    if not sets:
        return jsonify({"error": "没有要修改的内容"}), 400
    db = get_db()
    db.execute(f"UPDATE conversations SET {', '.join(sets)} WHERE id=? AND user_id=?", (*params, conversation_id, _uid()))
    db.commit()
    return jsonify({"ok": True})


@bp.delete("/api/conversations/<int:conversation_id>")
@login_required
@limiter.limit("30 per minute")
def api_conversation_delete(conversation_id: int):
    db = get_db()
    cur = db.execute("DELETE FROM conversations WHERE id=? AND user_id=?", (conversation_id, _uid()))
    db.commit()
    if cur.rowcount != 1:
        return jsonify({"error": "会话不存在"}), 404
    return jsonify({"ok": True})


@bp.get("/api/conversations/<int:conversation_id>/messages")
@login_required
def api_conversation_messages(conversation_id: int):
    _conversation_or_404(conversation_id)
    rows = get_db().execute(
        """
        SELECT id, role, content, created_at, model_name, total_tokens,
               thoughts, grounding_json, meta_json, attachments_json
        FROM chat_messages
        WHERE user_id=? AND conversation_id=?
        ORDER BY id ASC
        LIMIT 1000
        """,
        (_uid(), conversation_id),
    ).fetchall()
    return jsonify({"messages": _message_dicts(_uid(), rows)})


@bp.get("/api/conversations/<int:conversation_id>/export")
@login_required
def api_conversation_export(conversation_id: int):
    c = _conversation_or_404(conversation_id)
    rows = get_db().execute(
        """
        SELECT id, role, content, created_at, model_name, total_tokens,
               thoughts, grounding_json, meta_json, attachments_json
        FROM chat_messages WHERE user_id=? AND conversation_id=? ORDER BY id ASC
        """,
        (_uid(), conversation_id),
    ).fetchall()
    lines = [f"# {c['title']}", ""]
    if c["system_prompt"]:
        lines += ["> 系统提示词：" + c["system_prompt"].replace("\n", "\n> "), ""]
    for m in _message_dicts(_uid(), rows):
        who = "🧑 我" if m["role"] == "user" else f"🤖 {m['model_name'] or 'Gemini'}"
        lines += [f"## {who}  ·  {m['created_at']} UTC", ""]
        for a in m["attachments"]:
            lines.append(f"- 附件：{a['name']}")
        lines += [m["content"] or "", ""]
        for s in (m["grounding"] or {}).get("sources", []):
            lines.append(f"- 来源：[{s['title']}]({s['uri']})")
        lines.append("")
    body = "\n".join(lines)
    fname = re.sub(r"[\\/:*?\"<>|]+", "_", c["title"])[:60] or "chat"
    resp = Response(body, mimetype="text/markdown; charset=utf-8")
    resp.headers["Content-Disposition"] = f"attachment; filename*=UTF-8''{_quote(fname)}.md"
    return resp


def _quote(s: str) -> str:
    from urllib.parse import quote

    return quote(s)


@bp.get("/api/stats")
@login_required
def api_stats():
    conv_id = _require_conversation(_uid(), request.args.get("conversation_id", type=int))
    return jsonify(_stats(_uid(), conv_id))


@bp.post("/api/chat/clear")
@login_required
@limiter.limit("10 per minute")
def api_clear():
    data = request.get_json(silent=True) or {}
    conversation_id = _require_conversation(_uid(), data.get("conversation_id"))
    db = get_db()
    db.execute("DELETE FROM chat_messages WHERE user_id=? AND conversation_id=?", (_uid(), conversation_id))
    db.execute("UPDATE conversations SET updated_at=CURRENT_TIMESTAMP WHERE id=? AND user_id=?", (conversation_id, _uid()))
    db.commit()
    return jsonify({"ok": True})


# ---------------------------------------------------------------- 文件


@bp.post("/api/upload")
@login_required
@limiter.limit("40 per minute")
def api_upload():
    file = request.files.get("file")
    if not file or not file.filename:
        return jsonify({"error": "未找到文件"}), 400

    # secure_filename 会丢掉中文，只用它取扩展名，原名仅做展示
    original = os.path.basename(file.filename).strip()[:200] or "file"
    ext = os.path.splitext(secure_filename(original) or original)[1].lower().lstrip(".")
    if not ext:
        ext = os.path.splitext(original)[1].lower().lstrip(".")
    allowed = {str(x).lower().lstrip(".") for x in (current_app.config.get("UPLOAD_ALLOWED_EXT") or [])}
    if allowed and ext not in allowed:
        return jsonify({"error": f"不支持的文件类型：.{ext}"}), 400

    data = file.read()
    if not data:
        return jsonify({"error": "文件为空"}), 400
    max_bytes = int(current_app.config.get("MAX_UPLOAD_BYTES") or 0)
    if max_bytes and len(data) > max_bytes:
        return jsonify({"error": f"文件过大（上限 {max_bytes // 1024 // 1024}MB）"}), 413

    mime = guess_mime(original, file.mimetype)
    extracted = "" if is_native(mime) and ext != "pdf" else extract_text(data, original, mime)
    file_id = save_file(user_id=_uid(), data=data, filename=original, mime=mime, extracted=extracted)
    return jsonify({"id": file_id, "name": original, "size": len(data), "mime": mime, "is_image": is_image(mime)})


@bp.delete("/api/upload/<int:file_id>")
@login_required
@limiter.limit("60 per minute")
def api_upload_delete(file_id: int):
    db = get_db()
    row = db.execute("SELECT storage_path FROM uploaded_files WHERE id=? AND user_id=?", (file_id, _uid())).fetchone()
    if not row:
        return jsonify({"error": "文件不存在"}), 404
    # 已发送过的附件保留（历史消息还引用它），只删除尚未使用的
    used = db.execute(
        "SELECT 1 FROM chat_messages WHERE user_id=? AND attachments_json LIKE ? LIMIT 1",
        (_uid(), f"%{file_id}%"),
    ).fetchone()
    if not used:
        try:
            Path(row["storage_path"]).unlink(missing_ok=True)
        except OSError:
            pass
        db.execute("DELETE FROM uploaded_files WHERE id=? AND user_id=?", (file_id, _uid()))
        db.commit()
    return jsonify({"ok": True})


@bp.get("/api/files/<int:file_id>")
@login_required
def api_file(file_id: int):
    row = get_db().execute(
        "SELECT original_name, storage_path, mime_type FROM uploaded_files WHERE id=? AND user_id=?",
        (file_id, _uid()),
    ).fetchone()
    if not row:
        abort(404)
    path = Path(row["storage_path"]).resolve()
    if not str(path).startswith(str(upload_dir().resolve())) or not path.is_file():
        abort(404)
    download = request.args.get("download") == "1" or not is_image(row["mime_type"])
    resp = send_file(
        path,
        mimetype=row["mime_type"] or "application/octet-stream",
        as_attachment=download,
        download_name=row["original_name"],
        max_age=86400,
    )
    resp.headers["Cache-Control"] = "private, max-age=86400"
    return resp


@bp.post("/api/files/<int:file_id>/ocr")
@login_required
@limiter.limit("20 per minute")
def api_file_ocr(file_id: int):
    db = get_db()
    row = db.execute(
        "SELECT original_name, storage_path, mime_type, extracted_text, ocr_text FROM uploaded_files WHERE id=? AND user_id=?",
        (file_id, _uid()),
    ).fetchone()
    if not row:
        return jsonify({"error": "文件不存在"}), 404
    mime = str(row["mime_type"] or "")
    if not (is_image(mime) or mime == "application/pdf"):
        return jsonify({"error": "只支持图片和 PDF"}), 400
    if row["ocr_text"] and request.args.get("force") != "1":
        return jsonify({"text": row["ocr_text"], "cached": True})
    api_key = current_app.config.get("GEMINI_API_KEY")
    try:
        data = Path(row["storage_path"]).read_bytes()
        text, tokens = ocr_file(api_key=api_key, mime=mime, data=data)
    except Exception as e:  # noqa: BLE001
        current_app.logger.warning("ocr failed", exc_info=True)
        return jsonify({"error": _friendly_error(e)}), 502
    # 扫描版 PDF / 图片：把 OCR 结果当作文字备份，超出内联预算时仍能作为文字发送
    extracted = row["extracted_text"] or ""
    if len(extracted.strip()) < 50:
        extracted = text
    db.execute("UPDATE uploaded_files SET ocr_text=?, extracted_text=? WHERE id=?", (text, extracted, file_id))
    db.commit()
    return jsonify({"text": text, "tokens": tokens, "cached": False})


# ---------------------------------------------------------------- 对话（流式）


def _system_instruction(conv_prompt: str | None, memory_snippets: list[str], is_image_model: bool) -> str | None:
    parts: list[str] = []
    if not is_image_model:
        now = datetime.now(tz=_tz())
        parts.append(
            f"当前时间：{now:%Y-%m-%d %H:%M}（{now.tzname()}），星期{'一二三四五六日'[now.weekday()]}。"
            "回答使用 Markdown 排版；数学公式用 $...$（行内）或 $$...$$（独立行）。"
            "默认使用用户提问所用的语言回答。"
        )
    if conv_prompt:
        parts.append("以下是用户为本对话设定的要求，请严格遵循：\n" + conv_prompt)
    if memory_snippets:
        parts.append(
            "以下是用户过往对话中的相关记忆（可能不准确或与当前问题无关；不相关请忽略，不要主动提及“记忆”）：\n"
            + "\n".join(f"- {s}" for s in memory_snippets[:20])
        )
    return "\n\n".join(parts) or None


def _build_turns(user_id: int, conversation_id: int, before_id: int, current_files: list[dict], budget: InlineBudget) -> list[Turn]:
    rows = get_db().execute(
        """
        SELECT id, role, content, attachments_json FROM chat_messages
        WHERE user_id=? AND conversation_id=? AND id<?
        ORDER BY id DESC LIMIT ?
        """,
        (user_id, conversation_id, before_id, HISTORY_LIMIT),
    ).fetchall()
    rows = list(reversed(rows))
    files = load_rows(user_id, [i for r in rows for i in parse_ids(r["attachments_json"])])
    # 当前轮的文件优先占用预算，然后历史从新到旧
    budget_parts: dict[int, list[dict]] = {}
    for r in reversed(rows):
        ids = [i for i in parse_ids(r["attachments_json"]) if i in files]
        if ids:
            budget_parts[int(r["id"])] = budget.to_parts([files[i] for i in ids])
    turns = [
        Turn(role="user" if r["role"] == "user" else "model", text=r["content"] or "", files=budget_parts.get(int(r["id"]), []))
        for r in rows
    ]
    return turns


def _sse(ev: dict) -> str:
    return f"data: {json.dumps(ev, ensure_ascii=False)}\n\n"


@bp.post("/api/chat/stream")
@login_required
@limiter.limit("20 per minute")
def api_chat_stream():
    data = request.get_json(silent=True) or {}
    user_id = _uid()
    db = get_db()
    cfg = current_app.config

    api_key = cfg.get("GEMINI_API_KEY")
    if not api_key:
        return jsonify({"error": "服务端未配置 GEMINI_API_KEY"}), 500

    conversation_id = _require_conversation(user_id, data.get("conversation_id"))
    conv = db.execute("SELECT * FROM conversations WHERE id=?", (conversation_id,)).fetchone()

    msg = (data.get("message") or "").strip()
    file_ids = parse_ids(data.get("file_ids") or [])
    truncate_from = int(data.get("truncate_from") or 0)

    # 幂等：客户端每次发送带唯一 client_id，网络卡住自动重发时不会重复处理
    client_id = str(data.get("client_id") or "")[:64]
    if client_id and re.fullmatch(r"[A-Za-z0-9_-]{8,64}", client_id):
        dup = db.execute(
            "SELECT id FROM chat_messages WHERE user_id=? AND conversation_id=? AND role='user' AND meta_json LIKE ?",
            (user_id, conversation_id, f'%"client_id": "{client_id}"%'),
        ).fetchone()
        if dup:
            return jsonify({"status": "already_received", "user_message_id": int(dup["id"])}), 409
    else:
        client_id = ""

    if data.get("regenerate"):
        last_user = db.execute(
            "SELECT id, content, attachments_json FROM chat_messages WHERE user_id=? AND conversation_id=? AND role='user' ORDER BY id DESC LIMIT 1",
            (user_id, conversation_id),
        ).fetchone()
        if not last_user:
            return jsonify({"error": "没有可以重新生成的消息"}), 400
        msg = last_user["content"] or ""
        file_ids = parse_ids(last_user["attachments_json"])
        truncate_from = int(last_user["id"])

    max_files = int(cfg.get("UPLOAD_MAX_FILES") or 10)
    if len(file_ids) > max_files:
        return jsonify({"error": f"最多附带 {max_files} 个文件"}), 400
    if not msg and not file_ids:
        return jsonify({"error": "请输入内容或上传文件"}), 400
    max_chars = int(cfg.get("MAX_MESSAGE_CHARS") or 32000)
    if len(msg) > max_chars:
        return jsonify({"error": f"内容过长（最多 {max_chars} 字）"}), 400

    allowed = cfg.get("GEMINI_ALLOWED_MODELS") or []
    model = find_model(data.get("model") or "", allowed) or find_model(cfg.get("GEMINI_MODEL") or DEFAULT_CHAT_MODEL, allowed)
    if model is None:
        models = available_models(allowed)
        if not models:
            return jsonify({"error": "没有可用模型"}), 500
        model = models[0]

    thinking = data.get("thinking") if data.get("thinking") in THINKING_LEVELS and model.thinking != "none" else None
    tools = {t for t in (data.get("tools") or []) if t in {"search", "url", "code"}} if model.tools else set()
    aspect = data.get("aspect_ratio") if data.get("aspect_ratio") in IMAGE_ASPECT_RATIOS else None
    memory_enabled = data.get("memory_enabled")
    memory_enabled = bool(cfg.get("MEMORY_ENABLED_DEFAULT", True)) if memory_enabled is None else bool(memory_enabled)

    files = load_rows(user_id, file_ids)
    file_ids = [i for i in file_ids if i in files]
    if not msg:
        msg = "请根据附件生成/修改图片。" if model.kind == "image" else "请分析我上传的文件。"

    if truncate_from:
        # 编辑 / 重新生成：删掉该消息及其之后的内容
        db.execute(
            "DELETE FROM chat_messages WHERE user_id=? AND conversation_id=? AND id>=?",
            (user_id, conversation_id, truncate_from),
        )

    cur = db.execute(
        "INSERT INTO chat_messages(user_id, conversation_id, role, content, model_name, attachments_json, meta_json) VALUES(?,?,?,?,?,?,?)",
        (user_id, conversation_id, "user", msg, model.id, json.dumps(file_ids) if file_ids else None,
         json.dumps({"client_id": client_id}) if client_id else None),
    )
    user_message_id = int(cur.lastrowid)
    db.execute("UPDATE users SET last_seen_at=CURRENT_TIMESTAMP WHERE id=?", (user_id,))
    db.commit()

    budget = InlineBudget()
    current_parts = budget.to_parts([files[i] for i in file_ids])
    turns = _build_turns(user_id, conversation_id, user_message_id, current_parts, budget)
    turns.append(Turn(role="user", text=msg, files=current_parts))

    embed_model = str(cfg.get("MEMORY_EMBED_MODEL") or "gemini-embedding-001")
    embed_dim = int(cfg.get("MEMORY_EMBED_DIM") or 768)
    memory_snippets: list[str] = []
    if memory_enabled and model.kind == "chat":
        for h in recall(user_id=user_id, api_key=api_key, embed_model=embed_model, query=msg,
                        top_k=int(cfg.get("MEMORY_TOP_K") or 5), embed_dim=embed_dim):
            t = (h.content or "").strip()
            t = t[:400] + "…" if len(t) > 400 else t
            if t and t not in memory_snippets:
                memory_snippets.append(t)

    opts = ChatOptions(
        model=model,
        system_instruction=_system_instruction(conv["system_prompt"], memory_snippets, model.kind == "image"),
        thinking_level=thinking,
        tools=tools,
        aspect_ratio=aspect,
    )
    is_first_exchange = (conv["title"] in DEFAULT_TITLES) and not db.execute(
        "SELECT 1 FROM chat_messages WHERE conversation_id=? AND role='model' LIMIT 1", (conversation_id,)
    ).fetchone()

    app = current_app._get_current_object()
    events: queue.Queue = queue.Queue()
    cancel = threading.Event()
    _DONE = object()

    def producer():
        try:
            for ev in stream_reply(api_key=api_key, opts=opts, turns=turns, cancel=cancel):
                events.put(ev)
                if cancel.is_set():
                    break
        except Exception as e:  # noqa: BLE001
            app.logger.warning("gemini stream failed: %s", e, exc_info=True)
            events.put({"type": "error", "message": _friendly_error(e)})
        finally:
            events.put(_DONE)

    threading.Thread(target=producer, name="gemini-stream", daemon=True).start()

    def generate():
        # 视图返回后请求级连接已被 teardown 关闭；流式阶段重新获取
        db = get_db()
        started = time.monotonic()
        text_parts: list[str] = []
        thought_parts: list[str] = []
        image_ids: list[int] = []
        grounding = None
        usage: dict = {}
        used_model = model.id
        error = None
        stopped = False
        # 先发 2KB 填充：部分企业网关 / 杀毒软件按大小缓冲，攒不满不转发
        yield ":" + " " * 2048 + "\n\n"
        yield _sse({"type": "start", "user_message_id": user_message_id, "conversation_id": conversation_id,
                    "memory_used": bool(memory_snippets)})
        try:
            while True:
                try:
                    ev = events.get(timeout=KEEPALIVE_SECONDS)
                except queue.Empty:
                    yield ": keepalive\n\n"  # 防止 Cloudflare 空闲断开（模型长时间思考时）
                    continue
                if ev is _DONE:
                    break
                t = ev["type"]
                if t == "text":
                    text_parts.append(ev["text"])
                elif t == "thought":
                    thought_parts.append(ev["text"])
                elif t == "model":
                    used_model = ev["id"]
                elif t == "grounding":
                    grounding = {"queries": ev["queries"], "sources": ev["sources"]}
                elif t == "usage":
                    usage = ev
                elif t == "error":
                    error = ev["message"]
                elif t == "image":
                    ext = ".png" if "png" in ev["mime"] else ".jpg"
                    fid = save_file(user_id=user_id, data=ev["data"], filename=f"gemini-{int(time.time())}{ext}",
                                    mime=ev["mime"], source="generated")
                    image_ids.append(fid)
                    ev = {"type": "image", "file": public_info(load_rows(user_id, [fid])[fid])}
                yield _sse(ev)
        except GeneratorExit:
            stopped = True  # 客户端断开或点击了停止
            cancel.set()
        finally:
            reply = "".join(text_parts).strip()
            if len(reply) > MAX_REPLY_CHARS:
                reply = reply[:MAX_REPLY_CHARS] + "\n\n（已截断）"
            if not reply and not image_ids:
                reply = "（已停止）" if stopped else (f"⚠️ {error}" if error else "（模型返回为空）")
            meta = {
                "thinking": thinking,
                "tools": sorted(tools),
                "elapsed_ms": int((time.monotonic() - started) * 1000),
            }
            if aspect and model.kind == "image":
                meta["aspect_ratio"] = aspect
            if stopped:
                meta["stopped"] = True
            if error:
                meta["error"] = error
            if memory_snippets:
                meta["memory_used"] = True
            cur2 = db.execute(
                """
                INSERT INTO chat_messages(user_id, conversation_id, role, content, model_name,
                  prompt_tokens, completion_tokens, total_tokens, thoughts_tokens,
                  thoughts, grounding_json, meta_json, attachments_json)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    user_id, conversation_id, "model", reply, used_model,
                    usage.get("prompt"), usage.get("completion"), usage.get("total"), usage.get("thoughts"),
                    "".join(thought_parts).strip() or None,
                    json.dumps(grounding, ensure_ascii=False) if grounding else None,
                    json.dumps(meta, ensure_ascii=False),
                    json.dumps(image_ids) if image_ids else None,
                ),
            )
            model_message_id = int(cur2.lastrowid)
            db.execute("UPDATE conversations SET updated_at=CURRENT_TIMESTAMP WHERE id=?", (conversation_id,))
            db.commit()

        if stopped:
            return
        yield _sse({
            "type": "done",
            "message_id": model_message_id,
            "model": used_model,
            "meta": meta,
            "usage": usage,
            "stats": _stats(user_id, conversation_id),
        })

        if is_first_exchange and not error:
            title = generate_title(api_key=api_key, first_message=msg, reply=reply or "（图片）")
            if title:
                db.execute("UPDATE conversations SET title=? WHERE id=? AND user_id=?", (title, conversation_id, user_id))
                db.commit()
                yield _sse({"type": "title", "conversation_id": conversation_id, "title": title})

        if memory_enabled and model.kind == "chat" and not error:
            # 写记忆要调两次向量接口，放到后台，流立即结束
            threading.Thread(
                target=_remember_in_background,
                args=(app, user_id, api_key, embed_model, embed_dim, int(cfg.get("MEMORY_MAX_ITEMS") or 2000),
                      conversation_id, ((("user", msg, user_message_id)), ("model", reply, model_message_id))),
                name="memory-write",
                daemon=True,
            ).start()

    resp = Response(stream_with_context(generate()), mimetype="text/event-stream")
    resp.headers["Cache-Control"] = "no-cache, no-transform"
    resp.headers["X-Accel-Buffering"] = "no"
    return resp


def _remember_in_background(app, user_id, api_key, embed_model, embed_dim, max_items, conversation_id, items):
    with app.app_context():
        for role, content, mid in items:
            try:
                remember_message(
                    user_id=user_id, role=role, content=content, api_key=api_key,
                    embed_model=embed_model, embed_dim=embed_dim, max_items=max_items,
                    source_conversation_id=conversation_id, source_message_id=mid,
                )
            except Exception:  # noqa: BLE001
                app.logger.warning("memory write failed", exc_info=True)


def _friendly_error(e: Exception) -> str:
    s = str(e)
    if "429" in s or "RESOURCE_EXHAUSTED" in s:
        return "调用太频繁或额度已用完，请稍后再试"
    if "SAFETY" in s or "blocked" in s.lower():
        return "内容被安全策略拦截，请换个说法"
    if "400" in s and "INVALID_ARGUMENT" in s:
        return "请求参数有误（可能是附件格式不受该模型支持）：" + s[:160]
    if "503" in s or "UNAVAILABLE" in s or "overloaded" in s.lower():
        return "模型当前繁忙，请稍后重试或切换模型"
    return "Gemini 调用失败：" + s[:160]
