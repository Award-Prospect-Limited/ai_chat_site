import os
from urllib.parse import urlparse

from flask import Flask, redirect, request, url_for, abort
from flask_login import LoginManager
from flask_wtf.csrf import CSRFProtect, generate_csrf
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from werkzeug.middleware.proxy_fix import ProxyFix

from .config import Config
from .db import get_db, init_app as init_db
from .models import User
from .security import apply_security_headers, is_allowed_host


limiter = Limiter(key_func=get_remote_address, storage_uri="memory://")


def create_app() -> Flask:
    app = Flask(__name__, instance_relative_config=False)
    app.config.from_mapping(Config.from_env())

    if not app.config.get("SECRET_KEY"):
        raise RuntimeError("AI_CHAT_SITE_SECRET_KEY (or SECRET_KEY) is required")

    if not app.config.get("ALLOWED_HOSTS"):
        raise RuntimeError("AI_CHAT_SITE_ALLOWED_HOSTS is required (comma-separated)")

    # Local HTTP debugging support: when FORCE_HTTPS=0, cookies must not be Secure-only.
    secure_cookie = bool(app.config.get("FORCE_HTTPS"))
    app.config["SESSION_COOKIE_SECURE"] = secure_cookie
    app.config["REMEMBER_COOKIE_SECURE"] = secure_cookie

    if app.config.get("TRUST_PROXY_HEADERS"):
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)

    limiter.init_app(app)
    csrf = CSRFProtect(app)

    login_manager = LoginManager()
    login_manager.login_view = "auth.login"
    login_manager.session_protection = "strong"
    login_manager.init_app(app)

    @login_manager.user_loader
    def _load_user(user_id: str):
        user = User.get(user_id)
        if user is None or user.disabled:
            return None
        return user

    init_db(app)

    @app.before_request
    def _host_and_https_guards():
        # /healthz 给服务器本机的发布脚本和巡检用（http://127.0.0.1:端口/healthz，不经 Cloudflare）：
        # 不做域名白名单和 https 跳转，否则本机探测永远是 400 / 302。它只回固定字段，不含任何用户数据。
        if request.path == "/healthz":
            return None

        host = request.host.split(":", 1)[0].strip().lower()
        if not is_allowed_host(host, app.config.get("ALLOWED_HOSTS", [])):
            abort(400)

        if app.config.get("FORCE_HTTPS") and not request.is_secure:
            if request.headers.get("X-Forwarded-Proto", "").lower() != "https":
                return redirect(request.url.replace("http://", "https://", 1), code=302)

        if request.path.startswith("/api/") and request.method in {"POST", "PUT", "PATCH", "DELETE"}:
            origin = (request.headers.get("Origin") or "").strip()
            referer = (request.headers.get("Referer") or "").strip()
            origin_host = ""
            if origin:
                origin_host = urlparse(origin).hostname or ""
            elif referer:
                origin_host = urlparse(referer).hostname or ""
            if not origin_host or not is_allowed_host(origin_host.lower(), app.config.get("ALLOWED_HOSTS", [])):
                abort(403)

    @app.after_request
    def _security_headers(resp):
        return apply_security_headers(resp, app.config)

    @app.context_processor
    def _inject_csrf():
        return {"csrf_token": generate_csrf}

    @app.get("/")
    def _root():
        from flask_login import current_user

        if current_user.is_authenticated:
            return redirect(url_for("chat.index"))
        return redirect(url_for("auth.login"))

    @app.get("/healthz")
    def healthz():
        # 应用能响应 + 数据库能查询才算健康；commit 是镜像构建时写入的提交号（发布后核对版本用）
        try:
            get_db().execute("SELECT 1").fetchone()
        except Exception:  # noqa: BLE001
            app.logger.exception("healthz: 数据库查询失败")
            return {"ok": False, "db": "error"}, 503
        return {"ok": True, "db": "ok", "commit": (os.getenv("APP_COMMIT") or "")[:7]}

    @app.get("/healthz/stream")
    @limiter.limit("6 per minute")
    def healthz_stream():
        # 诊断反代 / CDN 是否缓冲流式响应：每秒推一条，客户端应逐条收到
        import time

        from flask import Response

        def gen():
            for i in range(5):
                yield f"data: {i} {time.time():.1f}\n\n"
                time.sleep(1)

        resp = Response(gen(), mimetype="text/event-stream")
        resp.headers["Cache-Control"] = "no-cache, no-transform"
        resp.headers["X-Accel-Buffering"] = "no"
        return resp

    from .routes.auth import bp as auth_bp
    from .routes.chat import bp as chat_bp
    from .routes.admin import bp as admin_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(chat_bp)
    app.register_blueprint(admin_bp)
    # JSON API 靠 Origin/Referer 同源校验（见 _host_and_https_guards）+ SameSite Cookie 防 CSRF
    csrf.exempt(chat_bp)
    csrf.exempt(admin_bp)

    from .memory_service import start_backfill_thread

    start_backfill_thread(app)

    # Ensure JSON CSRF header names are accepted (Flask-WTF default includes these)
    _ = csrf

    return app
