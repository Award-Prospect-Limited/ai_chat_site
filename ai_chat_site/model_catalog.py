"""可选模型目录：前端下拉框、后端校验、思考参数映射都以这里为准。"""

from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class ModelInfo:
    id: str
    label: str
    desc: str
    kind: str = "chat"  # chat | image
    # gen3: thinking_level(low/medium/high)；gen25: thinking_budget；none: 不支持思考配置
    thinking: str = "gen3"
    tools: bool = True

    def to_dict(self) -> dict:
        return asdict(self)


CATALOG: list[ModelInfo] = [
    ModelInfo("gemini-3.8-flash", "Gemini 3.8 Flash", "最新 Flash，速度与质量均衡（推荐）"),
    ModelInfo("gemini-3.1-pro-preview", "Gemini 3.1 Pro", "最强推理，适合复杂分析、写代码、长文档"),
    ModelInfo("gemini-3.7-flash", "Gemini 3.7 Flash", "上一代 Flash"),
    ModelInfo("gemini-3.5-flash", "Gemini 3.5 Flash", "稳定版 Flash"),
    ModelInfo("gemini-3.5-flash-lite", "Gemini 3.5 Flash-Lite", "最快最省，适合翻译、简单问答"),
    ModelInfo("gemini-2.5-pro", "Gemini 2.5 Pro", "旧版 Pro", thinking="gen25"),
    ModelInfo("gemini-2.5-flash", "Gemini 2.5 Flash", "旧版 Flash", thinking="gen25"),
    ModelInfo("gemini-3.1-flash-image", "Nano Banana 2（绘图）", "文字生成图片 / 上传图片后修改", kind="image", thinking="none", tools=False),
    ModelInfo("gemini-3-pro-image", "Nano Banana Pro（绘图）", "更高画质、更擅长文字排版", kind="image", thinking="none", tools=False),
]

DEFAULT_CHAT_MODEL = "gemini-3.8-flash"
TITLE_MODEL = "gemini-3.5-flash-lite"
OCR_MODEL = "gemini-3.8-flash"
# 首选模型调用失败（且尚未输出任何内容）时依次尝试
FALLBACK_CHAIN = ["gemini-3.5-flash", "gemini-2.5-flash"]

# 2.5 系列用 token 预算表示思考深度；-1 表示由模型动态决定
GEN25_BUDGET = {"low": 1024, "medium": 8192, "high": -1}
THINKING_LEVELS = ("low", "medium", "high")
IMAGE_ASPECT_RATIOS = ("1:1", "16:9", "9:16", "4:3", "3:4", "3:2", "2:3")


def available_models(allowed_ids: list[str] | None) -> list[ModelInfo]:
    """allowed_ids 为空时返回整个目录；否则按目录顺序过滤，未知 id 以通用配置追加。"""
    if not allowed_ids:
        return list(CATALOG)
    wanted = [m.strip().lower() for m in allowed_ids if m.strip()]
    by_id = {m.id: m for m in CATALOG}
    out = [m for m in CATALOG if m.id in wanted]
    for mid in wanted:
        if mid not in by_id:
            out.append(ModelInfo(mid, mid, "自定义模型"))
    return out


def find_model(model_id: str, allowed_ids: list[str] | None) -> ModelInfo | None:
    mid = (model_id or "").strip().lower()
    for m in available_models(allowed_ids):
        if m.id == mid:
            return m
    return None
