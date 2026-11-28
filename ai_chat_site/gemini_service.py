"""Gemini 调用层：把 SDK 的流式响应转换为前端可消费的事件。

事件（dict，"type" 字段区分）：
  model      {"id"}                      实际使用的模型（可能因回退而变化）
  thought    {"text"}                    思考摘要增量
  text       {"text"}                    正文增量（代码执行块已转为 Markdown）
  image      {"mime", "data": bytes}     生成的图片
  grounding  {"queries", "sources"}      联网搜索的查询词与来源
  usage      {"prompt", "completion", "thoughts", "total"}
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Iterator

from google import genai
from google.genai import types

from .model_catalog import FALLBACK_CHAIN, GEN25_BUDGET, OCR_MODEL, TITLE_MODEL, ModelInfo

_NO_AFC = types.AutomaticFunctionCallingConfig(disable=True)

# 每个 API Key 复用一个客户端（内部带连接池）
_clients: dict[str, genai.Client] = {}
_clients_lock = threading.Lock()


def get_client(api_key: str) -> genai.Client:
    with _clients_lock:
        client = _clients.get(api_key)
        if client is None:
            client = genai.Client(api_key=api_key)
            _clients[api_key] = client
        return client


@dataclass
class Turn:
    """一轮对话。files 里每项：{"mime": str, "data": bytes} 或 {"text": str}。"""

    role: str  # user | model
    text: str = ""
    files: list[dict] = field(default_factory=list)


@dataclass
class ChatOptions:
    model: ModelInfo
    system_instruction: str | None = None
    thinking_level: str | None = None  # low | medium | high
    tools: set[str] = field(default_factory=set)  # search | url | code
    aspect_ratio: str | None = None


def build_contents(turns: list[Turn]) -> list[types.Content]:
    contents: list[types.Content] = []
    for t in turns:
        parts: list[types.Part] = []
        for f in t.files:
            if f.get("data") is not None:
                parts.append(types.Part.from_bytes(data=f["data"], mime_type=f["mime"]))
            elif f.get("text"):
                parts.append(types.Part.from_text(text=f["text"]))
        if t.text:
            parts.append(types.Part.from_text(text=t.text))
        if not parts:
            continue
        role = "user" if t.role == "user" else "model"
        # Gemini 要求 user/model 交替；相邻同角色合并
        if contents and contents[-1].role == role:
            contents[-1].parts.extend(parts)
        else:
            contents.append(types.Content(role=role, parts=parts))
    return contents


def _build_config(opts: ChatOptions) -> types.GenerateContentConfig:
    kw: dict = {"automatic_function_calling": _NO_AFC}
    if opts.system_instruction:
        kw["system_instruction"] = opts.system_instruction

    m = opts.model
    if m.kind == "image":
        kw["response_modalities"] = ["TEXT", "IMAGE"]
        if opts.aspect_ratio:
            kw["image_config"] = types.ImageConfig(aspect_ratio=opts.aspect_ratio)
        return types.GenerateContentConfig(**kw)

    level = opts.thinking_level
    if m.thinking == "gen3":
        kw["thinking_config"] = types.ThinkingConfig(include_thoughts=True, thinking_level=level or None)
    elif m.thinking == "gen25":
        budget = GEN25_BUDGET.get(level or "", -1)
        kw["thinking_config"] = types.ThinkingConfig(include_thoughts=True, thinking_budget=budget)

    if m.tools and opts.tools:
        tools: list[types.Tool] = []
        if "search" in opts.tools:
            tools.append(types.Tool(google_search=types.GoogleSearch()))
        if "url" in opts.tools:
            tools.append(types.Tool(url_context=types.UrlContext()))
        if "code" in opts.tools:
            tools.append(types.Tool(code_execution=types.ToolCodeExecution()))
        if tools:
            kw["tools"] = tools
    return types.GenerateContentConfig(**kw)


def _grounding_event(gm) -> dict | None:
    if not gm:
        return None
    queries = [q for q in (getattr(gm, "web_search_queries", None) or []) if q]
    sources = []
    seen = set()
    for ch in getattr(gm, "grounding_chunks", None) or []:
        web = getattr(ch, "web", None)
        if not web or not getattr(web, "uri", None) or web.uri in seen:
            continue
        seen.add(web.uri)
        sources.append({"title": web.title or web.uri, "uri": web.uri})
    if not queries and not sources:
        return None
    return {"type": "grounding", "queries": queries, "sources": sources}


def _usage_event(um) -> dict | None:
    if not um:
        return None
    return {
        "type": "usage",
        "prompt": getattr(um, "prompt_token_count", None),
        "completion": getattr(um, "candidates_token_count", None),
        "thoughts": getattr(um, "thoughts_token_count", None),
        "total": getattr(um, "total_token_count", None),
    }


def _events_from_chunk(chunk) -> Iterator[dict]:
    for cand in getattr(chunk, "candidates", None) or []:
        content = getattr(cand, "content", None)
        for p in (getattr(content, "parts", None) or []) if content else []:
            if getattr(p, "executable_code", None):
                ec = p.executable_code
                lang = str(getattr(ec, "language", "") or "python").lower()
                if "python" in lang:
                    lang = "python"
                yield {"type": "text", "text": f"\n\n```{lang}\n{ec.code or ''}\n```\n"}
            elif getattr(p, "code_execution_result", None):
                out = (p.code_execution_result.output or "").rstrip()
                if out:
                    yield {"type": "text", "text": f"\n**运行结果：**\n\n```text\n{out}\n```\n\n"}
            elif getattr(p, "inline_data", None) and p.inline_data.data:
                if not p.thought:  # 思考阶段的草图不展示
                    yield {"type": "image", "mime": p.inline_data.mime_type or "image/png", "data": p.inline_data.data}
            elif p.text:
                yield {"type": "thought" if p.thought else "text", "text": p.text}
        ev = _grounding_event(getattr(cand, "grounding_metadata", None))
        if ev:
            yield ev


def stream_reply(
    *,
    api_key: str,
    opts: ChatOptions,
    turns: list[Turn],
    cancel: threading.Event | None = None,
) -> Iterator[dict]:
    client = get_client(api_key)
    contents = build_contents(turns)
    config = _build_config(opts)

    candidates = [opts.model.id]
    if opts.model.kind == "chat":
        candidates += [m for m in FALLBACK_CHAIN if m != opts.model.id]

    last_exc: Exception | None = None
    for model_id in candidates:
        emitted = False
        last_usage = None
        last_grounding = None
        try:
            if model_id != opts.model.id and config.thinking_config is not None:
                # 回退模型可能不认 thinking_level，去掉思考配置最稳妥
                config = config.model_copy(update={"thinking_config": None})
            stream = client.models.generate_content_stream(model=model_id, contents=contents, config=config)
            for chunk in stream:
                if cancel is not None and cancel.is_set():
                    break
                if not emitted:
                    emitted = True
                    yield {"type": "model", "id": model_id}
                for ev in _events_from_chunk(chunk):
                    if ev["type"] == "grounding":
                        last_grounding = ev  # 以最后一个为准（累计的最全）
                    else:
                        yield ev
                last_usage = getattr(chunk, "usage_metadata", None) or last_usage
            if last_grounding:
                yield last_grounding
            ev = _usage_event(last_usage)
            if ev:
                yield ev
            return
        except Exception as e:  # noqa: BLE001
            if emitted:
                raise
            last_exc = e
            continue
    if last_exc:
        raise last_exc


def generate_title(*, api_key: str, first_message: str, reply: str) -> str | None:
    prompt = (
        "根据下面这段对话，起一个不超过 16 个字的中文标题。只输出标题本身，不要引号和标点结尾。\n\n"
        f"用户：{first_message[:1500]}\n\n助手：{reply[:1500]}"
    )
    try:
        resp = get_client(api_key).models.generate_content(
            model=TITLE_MODEL,
            contents=prompt,
            config=types.GenerateContentConfig(
                automatic_function_calling=_NO_AFC,
                thinking_config=types.ThinkingConfig(thinking_level="low"),
                max_output_tokens=64,
            ),
        )
    except Exception:  # noqa: BLE001
        return None
    title = (getattr(resp, "text", None) or "").strip().strip("\"'“”《》").splitlines()
    title = title[0].strip() if title else ""
    return title[:40] or None


OCR_PROMPT = (
    "你是 OCR 引擎。逐字识别这份文件中的全部文字，按原有阅读顺序输出。要求：\n"
    "1. 不翻译、不总结、不补充、不纠正原文错别字；\n"
    "2. 保留段落与换行；标题用 Markdown 标题；表格转换为 Markdown 表格；\n"
    "3. 多页文件在每页开头标注「--- 第 N 页 ---」；\n"
    "4. 看不清的字用 □ 代替；没有任何文字时只输出「（未识别到文字）」。\n"
    "直接输出识别结果，不要任何开场白。"
)


def ocr_file(*, api_key: str, mime: str, data: bytes) -> tuple[str, int | None]:
    """返回 (识别文字, 消耗 token)。"""
    resp = get_client(api_key).models.generate_content(
        model=OCR_MODEL,
        contents=[types.Content(role="user", parts=[types.Part.from_bytes(data=data, mime_type=mime), types.Part.from_text(text=OCR_PROMPT)])],
        config=types.GenerateContentConfig(
            automatic_function_calling=_NO_AFC,
            thinking_config=types.ThinkingConfig(thinking_level="low"),
            max_output_tokens=32768,
        ),
    )
    um = getattr(resp, "usage_metadata", None)
    return (getattr(resp, "text", None) or "").strip(), getattr(um, "total_token_count", None)
