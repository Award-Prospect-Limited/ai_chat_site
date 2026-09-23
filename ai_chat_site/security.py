from __future__ import annotations

from flask import Response


def is_allowed_host(host: str, allowed_hosts: list[str]) -> bool:
    if not allowed_hosts:
        return True
    host = (host or "").lower()
    return host in allowed_hosts


def apply_security_headers(resp: Response, cfg: dict) -> Response:
    # Basic hardening headers.
    resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    resp.headers.setdefault("X-Frame-Options", "DENY")
    resp.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    resp.headers.setdefault("Permissions-Policy", "geolocation=(), microphone=(), camera=()")
    resp.headers.setdefault("Cross-Origin-Opener-Policy", "same-origin")
    resp.headers.setdefault("Cross-Origin-Resource-Policy", "same-origin")

    # HSTS only makes sense if we're actually on HTTPS at the edge.
    if cfg.get("FORCE_HTTPS"):
        resp.headers.setdefault("Strict-Transport-Security", "max-age=15552000; includeSubDomains")

    # CSP: no inline scripts; allow CDN CSS/fonts for Bootstrap/FontAwesome.
    # If Turnstile is enabled, allow challenges.cloudflare.com.
    turnstile_enabled = bool(cfg.get("TURNSTILE_SITE_KEY") and cfg.get("TURNSTILE_SECRET_KEY"))
    # cdn.jsdelivr.net：marked / DOMPurify / highlight.js / KaTeX（版本已固定在模板里）
    script_src = "script-src 'self' https://cdn.jsdelivr.net; "
    frame_src = ""
    connect_src = "connect-src 'self'; "

    if turnstile_enabled:
        script_src = "script-src 'self' https://cdn.jsdelivr.net https://challenges.cloudflare.com; "
        frame_src = "frame-src https://challenges.cloudflare.com; "
        connect_src = "connect-src 'self' https://challenges.cloudflare.com; "

    csp = (
        "default-src 'self'; "
        "base-uri 'self'; "
        "form-action 'self'; "
        "frame-ancestors 'none'; "
        "object-src 'none'; "
        f"{script_src}"
        # KaTeX 公式渲染依赖 style 属性，只能对样式放开 'unsafe-inline'（脚本仍然严格）
        "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net https://cdnjs.cloudflare.com https://fonts.googleapis.com; "
        "font-src 'self' https://cdnjs.cloudflare.com https://cdn.jsdelivr.net https://fonts.gstatic.com data:; "
        "img-src 'self' data: blob:; "
        f"{connect_src}"
        f"{frame_src}"
    )
    resp.headers.setdefault("Content-Security-Policy", csp)

    return resp
