"""管理后台：用户、用量、邀请码。仅 ADMIN_USERNAMES 中的用户可访问。"""

from __future__ import annotations

from functools import wraps

from flask import Blueprint, abort, jsonify, render_template, request
from flask_login import current_user, login_required

from .. import limiter
from ..db import get_db
from ..invite_tool import generate_invite_code
from .chat import _now_boundaries_utc

bp = Blueprint("admin", __name__)


def admin_required(fn):
    @wraps(fn)
    @login_required
    def wrapper(*args, **kwargs):
        if not getattr(current_user, "is_admin", False):
            abort(403)
        return fn(*args, **kwargs)

    return wrapper


@bp.get("/admin")
@admin_required
def index():
    return render_template("admin/index.html")


@bp.get("/api/admin/overview")
@admin_required
def overview():
    db = get_db()
    week, month = _now_boundaries_utc()
    users = db.execute(
        """
        SELECT u.id, u.username, u.email, u.created_at, u.last_seen_at, u.disabled,
          (SELECT COUNT(*) FROM conversations c WHERE c.user_id=u.id) AS conversations,
          (SELECT COUNT(*) FROM chat_messages m WHERE m.user_id=u.id AND m.role='user') AS messages,
          (SELECT COALESCE(SUM(total_tokens),0) FROM chat_messages m WHERE m.user_id=u.id AND m.created_at>=?) AS week_tokens,
          (SELECT COALESCE(SUM(total_tokens),0) FROM chat_messages m WHERE m.user_id=u.id AND m.created_at>=?) AS month_tokens,
          (SELECT COALESCE(SUM(total_tokens),0) FROM chat_messages m WHERE m.user_id=u.id) AS total_tokens,
          (SELECT MAX(created_at) FROM chat_messages m WHERE m.user_id=u.id) AS last_message_at
        FROM users u ORDER BY u.id
        """,
        (week, month),
    ).fetchall()
    by_model = db.execute(
        """
        SELECT model_name, COUNT(*) AS replies, COALESCE(SUM(total_tokens),0) AS tokens
        FROM chat_messages WHERE role='model' AND created_at>=?
        GROUP BY model_name ORDER BY tokens DESC
        """,
        (month,),
    ).fetchall()
    daily = db.execute(
        """
        SELECT substr(created_at,1,10) AS day, COUNT(*) AS replies, COALESCE(SUM(total_tokens),0) AS tokens
        FROM chat_messages WHERE role='model' AND created_at>=date('now','-29 day')
        GROUP BY day ORDER BY day
        """
    ).fetchall()
    invites = db.execute(
        """
        SELECT i.id, i.code, i.created_at, i.used_at, i.disabled, u.username AS used_by
        FROM invite_codes i LEFT JOIN users u ON u.id=i.used_by_user_id
        ORDER BY i.id DESC LIMIT 200
        """
    ).fetchall()
    return jsonify(
        {
            "me": current_user.username,
            "users": [dict(r) for r in users],
            "by_model": [dict(r) for r in by_model],
            "daily": [dict(r) for r in daily],
            "invites": [dict(r) for r in invites],
        }
    )


@bp.post("/api/admin/invites")
@admin_required
@limiter.limit("20 per minute")
def create_invites():
    count = max(1, min(int((request.get_json(silent=True) or {}).get("count") or 1), 20))
    db = get_db()
    codes = []
    for _ in range(count):
        code = generate_invite_code()
        db.execute("INSERT INTO invite_codes(code) VALUES(?)", (code,))
        codes.append(code)
    db.commit()
    return jsonify({"codes": codes})


@bp.patch("/api/admin/invites/<int:invite_id>")
@admin_required
def update_invite(invite_id: int):
    disabled = 1 if (request.get_json(silent=True) or {}).get("disabled") else 0
    db = get_db()
    db.execute("UPDATE invite_codes SET disabled=? WHERE id=? AND used_at IS NULL", (disabled, invite_id))
    db.commit()
    return jsonify({"ok": True})


@bp.patch("/api/admin/users/<int:user_id>")
@admin_required
def update_user(user_id: int):
    if user_id == int(current_user.id):
        return jsonify({"error": "不能停用自己"}), 400
    disabled = 1 if (request.get_json(silent=True) or {}).get("disabled") else 0
    db = get_db()
    db.execute("UPDATE users SET disabled=? WHERE id=?", (disabled, user_id))
    db.commit()
    return jsonify({"ok": True})
