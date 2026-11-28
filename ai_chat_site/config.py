import os
from dataclasses import dataclass

from dotenv import load_dotenv


def _truthy(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


def _split_csv(value: str | None) -> list[str]:
    if not value:
        return []
    return [part.strip().lower() for part in value.split(",") if part.strip()]


@dataclass(frozen=True)
class Config:
    SECRET_KEY: str
    DATABASE_PATH: str
    GEMINI_API_KEY: str | None
    GEMINI_MODEL: str
    GEMINI_ALLOWED_MODELS: list[str]
    ALLOWED_HOSTS: list[str]
    TRUST_PROXY_HEADERS: bool
    FORCE_HTTPS: bool
    TURNSTILE_SITE_KEY: str | None
    TURNSTILE_SECRET_KEY: str | None
    ADMIN_USERNAMES: list[str]
    MAX_MESSAGE_CHARS: int

    MEMORY_ENABLED_DEFAULT: bool
    MEMORY_EMBED_MODEL: str
    MEMORY_TOP_K: int
    MEMORY_MAX_ITEMS: int
    MEMORY_EMBED_DIM: int

    LOCKOUT_MAX_FAILS: int
    LOCKOUT_WINDOW_SECONDS: int
    LOCKOUT_SECONDS: int

    SESSION_COOKIE_NAME: str = "ai_chat_site_session"
    SESSION_COOKIE_HTTPONLY: bool = True
    SESSION_COOKIE_SAMESITE: str = "Lax"
    SESSION_COOKIE_SECURE: bool = True

    REMEMBER_COOKIE_NAME: str = "ai_chat_site_remember"
    REMEMBER_COOKIE_HTTPONLY: bool = True
    REMEMBER_COOKIE_SAMESITE: str = "Lax"
    REMEMBER_COOKIE_SECURE: bool = True

    MAX_CONTENT_LENGTH: int = 25 * 1024 * 1024

    UPLOAD_DIR: str = "/data/uploads"
    MAX_UPLOAD_BYTES: int = 20 * 1024 * 1024
    UPLOAD_ALLOWED_EXT: list[str] = None  # type: ignore[assignment]
    UPLOAD_MAX_FILES: int = 10

    WTF_CSRF_ENABLED: bool = True
    WTF_CSRF_TIME_LIMIT: int | None = 3600 * 24

    @staticmethod
    def from_env() -> dict:
        # 从 /app/.env 加载敏感凭据（不在 docker env 中明文存储）
        load_dotenv('/app/.env')
        secret = os.getenv("AI_CHAT_SITE_SECRET_KEY") or os.getenv("SECRET_KEY") or ""
        db_path = os.getenv("DATABASE_PATH") or "/data/ai_chat_site.sqlite3"
        gemini_key = os.getenv("GEMINI_API_KEY") or os.getenv("AI_CHAT_SITE_GEMINI_API_KEY")
        model = (os.getenv("GEMINI_MODEL") or "gemini-3.8-flash").strip().lower()
        # 留空表示使用 model_catalog.CATALOG 的全部模型
        allowed_models = _split_csv(os.getenv("GEMINI_ALLOWED_MODELS"))

        allowed_hosts = _split_csv(os.getenv("AI_CHAT_SITE_ALLOWED_HOSTS"))
        trust_proxy = _truthy(os.getenv("TRUST_PROXY_HEADERS"), default=True)
        force_https = _truthy(os.getenv("FORCE_HTTPS"), default=True)

        memory_enabled_default = _truthy(os.getenv("MEMORY_ENABLED_DEFAULT"), default=True)
        memory_embed_model = (os.getenv("MEMORY_EMBED_MODEL") or "gemini-embedding-001").strip()
        if memory_embed_model == "text-embedding-004":
            # Google 已下线该模型，旧配置自动迁移
            memory_embed_model = "gemini-embedding-001"
        memory_top_k = int(os.getenv("MEMORY_TOP_K") or "5")
        memory_max_items = int(os.getenv("MEMORY_MAX_ITEMS") or "2000")

        upload_allowed_ext = _split_csv(os.getenv("UPLOAD_ALLOWED_EXT")) or [
            "txt",
            "md",
            "csv",
            "json",
            "log",
            "pdf",
            "docx",
            "png",
            "jpg",
            "jpeg",
            "webp",
            "heic",
            "xlsx",
            "pptx",
            "html",
            "xml",
            "yaml",
            "yml",
            "py",
            "js",
            "ts",
            "java",
            "go",
            "rs",
            "sql",
            "sh",
            "mp3",
            "wav",
            "m4a",
            "mp4",
            "mov",
        ]

        return {
            "SECRET_KEY": secret,
            "DATABASE_PATH": db_path,
            "GEMINI_API_KEY": gemini_key,
            "GEMINI_MODEL": model,
            "GEMINI_ALLOWED_MODELS": allowed_models,
            "ALLOWED_HOSTS": allowed_hosts,
            "TRUST_PROXY_HEADERS": trust_proxy,
            "FORCE_HTTPS": force_https,
            "TURNSTILE_SITE_KEY": os.getenv("TURNSTILE_SITE_KEY") or None,
            "TURNSTILE_SECRET_KEY": os.getenv("TURNSTILE_SECRET_KEY") or None,
            "MEMORY_ENABLED_DEFAULT": memory_enabled_default,
            "MEMORY_EMBED_MODEL": memory_embed_model,
            "MEMORY_TOP_K": memory_top_k,
            "MEMORY_MAX_ITEMS": memory_max_items,
            "MEMORY_EMBED_DIM": int(os.getenv("MEMORY_EMBED_DIM") or "768"),
            "ADMIN_USERNAMES": _split_csv(os.getenv("ADMIN_USERNAMES")),
            "MAX_MESSAGE_CHARS": int(os.getenv("MAX_MESSAGE_CHARS") or "32000"),
            "LOCKOUT_MAX_FAILS": int(os.getenv("LOCKOUT_MAX_FAILS") or "8"),
            "LOCKOUT_WINDOW_SECONDS": int(os.getenv("LOCKOUT_WINDOW_SECONDS") or "900"),
            "LOCKOUT_SECONDS": int(os.getenv("LOCKOUT_SECONDS") or "1800"),
            "WTF_CSRF_TIME_LIMIT": int(os.getenv("WTF_CSRF_TIME_LIMIT") or str(3600 * 24)),
            "MAX_CONTENT_LENGTH": int(os.getenv("MAX_CONTENT_LENGTH") or str(25 * 1024 * 1024)),
            "UPLOAD_DIR": (os.getenv("UPLOAD_DIR") or "/data/uploads").strip() or "/data/uploads",
            "MAX_UPLOAD_BYTES": int(os.getenv("MAX_UPLOAD_BYTES") or str(20 * 1024 * 1024)),
            "UPLOAD_ALLOWED_EXT": upload_allowed_ext,
            "UPLOAD_MAX_FILES": int(os.getenv("UPLOAD_MAX_FILES") or "10"),
        }
