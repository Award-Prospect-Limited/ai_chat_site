from __future__ import annotations

from flask import Blueprint, flash, redirect, render_template, request, session, url_for, current_app
from flask_login import current_user, login_user, logout_user
import secrets

from ..db import get_db
from ..models import User
from .. import limiter


bp = Blueprint("auth", __name__, url_prefix="/auth")


def _safe_next_url() -> str | None:
    nxt = (request.args.get("next") or "").strip()
    if not nxt:
        return None
    if "://" in nxt or nxt.startswith("//"):
        return None
    if not nxt.startswith("/"):
        return None
    return nxt


def _client_ip() -> str:
    # ProxyFix is enabled by default; request.remote_addr should be the real client.
    return (request.remote_addr or "").strip() or "unknown"


def _turnstile_verify() -> bool:
    secret = current_app.config.get("TURNSTILE_SECRET_KEY")
    if not secret:
        return True

    token = (request.form.get("cf-turnstile-response") or "").strip()
    if not token:
        return False

    try:
        import requests

        resp = requests.post(
            "https://challenges.cloudflare.com/turnstile/v0/siteverify",
            data={"secret": secret, "response": token, "remoteip": _client_ip()},
            timeout=6,
        )
        data = resp.json()
        return bool(data.get("success"))
    except Exception:
        return False


def _is_locked(identifier: str) -> bool:
    db = get_db()
    ip = _client_ip()
    row = db.execute(
        "SELECT locked_until FROM login_lockouts WHERE identifier=? AND ip=?",
        (identifier.lower(), ip),
    ).fetchone()
    if not row:
        return False
    check = db.execute("SELECT datetime('now') < datetime(?) AS locked", (row["locked_until"],)).fetchone()
    return bool(check["locked"]) if check else False


def _record_failure(identifier: str):
    db = get_db()
    ip = _client_ip()
    identifier_norm = identifier.lower()
    db.execute(
        "INSERT INTO login_failures(identifier, ip) VALUES(?, ?)",
        (identifier_norm, ip),
    )

    window = int(current_app.config.get("LOCKOUT_WINDOW_SECONDS") or 900)
    max_fails = int(current_app.config.get("LOCKOUT_MAX_FAILS") or 8)
    lock_seconds = int(current_app.config.get("LOCKOUT_SECONDS") or 1800)

    row = db.execute(
        """
        SELECT COUNT(*) AS c
        FROM login_failures
        WHERE identifier = ? AND ip = ?
          AND failed_at >= datetime('now', ?)
        """,
        (identifier_norm, ip, f"-{window} seconds"),
    ).fetchone()
    fails = int(row["c"]) if row else 0
    if fails >= max_fails:
        db.execute(
            """
            INSERT INTO login_lockouts(identifier, ip, locked_until)
            VALUES(?, ?, datetime('now', ?))
            ON CONFLICT(identifier, ip) DO UPDATE SET locked_until=excluded.locked_until, updated_at=CURRENT_TIMESTAMP
            """,
            (identifier_norm, ip, f"+{lock_seconds} seconds"),
        )

    db.commit()


def _clear_lockout(identifier: str):
    db = get_db()
    db.execute(
        "DELETE FROM login_lockouts WHERE identifier=? AND ip=?",
        (identifier.lower(), _client_ip()),
    )
    # Optionally keep failures for forensics; purge only old entries via external rotation if needed.
    db.commit()


def _reserve_invite(code: str) -> str | None:
    """
    Reserve an invite code atomically to prevent concurrent reuse.
    Returns a reservation_token if successful.
    """
    db = get_db()
    code = (code or "").strip().upper()
    if not code:
        return None

    token = secrets.token_hex(16)
    cur = db.execute(
        """
        UPDATE invite_codes
        SET reserved_at = CURRENT_TIMESTAMP, reservation_token = ?
        WHERE code = ?
          AND disabled = 0
          AND used_at IS NULL
          AND reserved_at IS NULL
        """,
        (token, code),
    )
    db.commit()
    if cur.rowcount != 1:
        return None
    return token


def _release_invite(reservation_token: str):
    if not reservation_token:
        return
    db = get_db()
    db.execute(
        "UPDATE invite_codes SET reserved_at=NULL, reservation_token=NULL WHERE reservation_token=? AND used_at IS NULL",
        (reservation_token,),
    )
    db.commit()


def _consume_invite(reservation_token: str, user_id: int):
    db = get_db()
    cur = db.execute(
        """
        UPDATE invite_codes
        SET used_at=CURRENT_TIMESTAMP,
            used_by_user_id=?,
            reserved_at=NULL
        WHERE reservation_token=?
          AND used_at IS NULL
        """,
        (user_id, reservation_token),
    )
    db.commit()
    if cur.rowcount != 1:
        raise RuntimeError("invite consume failed")


@bp.route("/register", methods=["GET", "POST"])
@limiter.limit("10 per minute")
def register():
    if current_user.is_authenticated:
        return redirect(url_for("chat.index"))

    if request.method == "POST":
        if not _turnstile_verify():
            flash("人机校验失败，请重试", "danger")
            return redirect(url_for("auth.register"))

        username = (request.form.get("username") or "").strip()
        email = (request.form.get("email") or "").strip()
        password = request.form.get("password") or ""
        password2 = request.form.get("password2") or ""
        invite_code = (request.form.get("invite_code") or "").strip()

        if password != password2:
            flash("两次输入的密码不一致", "danger")
            return redirect(url_for("auth.register"))

        reservation_token = _reserve_invite(invite_code)
        if not reservation_token:
            flash("邀请码无效或已被使用", "danger")
            return redirect(url_for("auth.register"))

        try:
            user = User.create(username=username, email=email, password=password)
        except Exception as e:
            _release_invite(reservation_token)
            # Avoid leaking DB errors; provide a reasonable message.
            msg = str(e) if isinstance(e, ValueError) else "注册失败（用户名或邮箱可能已存在）"
            flash(msg, "danger")
            return redirect(url_for("auth.register"))

        try:
            _consume_invite(reservation_token, int(user.id))
        except Exception:
            # Extremely unlikely; still allow the user to exist but warn operator via flash.
            flash("邀请码状态更新失败，请联系管理员", "warning")

        session.clear()
        login_user(user, remember=True)
        return redirect(url_for("chat.index"))

    return render_template(
        "auth/register.html",
        turnstile_site_key=current_app.config.get("TURNSTILE_SITE_KEY"),
    )


@bp.route("/login", methods=["GET", "POST"])
@limiter.limit("10 per minute")
def login():
    if current_user.is_authenticated:
        return redirect(url_for("chat.index"))

    if request.method == "POST":
        if not _turnstile_verify():
            flash("人机校验失败，请重试", "danger")
            return redirect(url_for("auth.login"))

        identifier = (request.form.get("identifier") or "").strip()
        password = request.form.get("password") or ""
        remember = bool(request.form.get("remember"))

        if _is_locked(identifier):
            flash("尝试次数过多，请稍后再试", "danger")
            return redirect(url_for("auth.login"))

        user = User.get_by_username_or_email(identifier)
        if not user or not user.verify_password(password):
            _record_failure(identifier)
            flash("账号或密码错误", "danger")
            return redirect(url_for("auth.login"))
        if user.disabled:
            flash("账号已被停用，请联系管理员", "danger")
            return redirect(url_for("auth.login"))

        _clear_lockout(identifier)
        session.clear()
        login_user(user, remember=remember)
        return redirect(_safe_next_url() or url_for("chat.index"))

    return render_template(
        "auth/login.html",
        turnstile_site_key=current_app.config.get("TURNSTILE_SITE_KEY"),
    )


@bp.get("/logout")
def logout():
    if current_user.is_authenticated:
        logout_user()
    session.clear()
    return redirect(url_for("auth.login"))


@bp.post("/delete_account")
@limiter.limit("2 per minute")
def delete_account():
    if not current_user.is_authenticated:
        return redirect(url_for("auth.login"))

    db = get_db()
    db.execute("DELETE FROM users WHERE id = ?", (current_user.id,))
    db.commit()
    logout_user()
    session.clear()
    flash("账号已删除", "success")
    return redirect(url_for("auth.register"))
