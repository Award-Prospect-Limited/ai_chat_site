import re
from dataclasses import dataclass

from email_validator import validate_email, EmailNotValidError
from flask import current_app
from flask_login import UserMixin
from werkzeug.security import check_password_hash, generate_password_hash

from .db import get_db


_USERNAME_RE = re.compile(r"^[a-zA-Z0-9_]{3,32}$")


@dataclass
class User(UserMixin):
    id: int
    username: str
    email: str
    password_hash: str
    disabled: bool = False

    @property
    def is_admin(self) -> bool:
        admins = current_app.config.get("ADMIN_USERNAMES") or []
        return self.username.lower() in admins

    @staticmethod
    def get(user_id: str | int):
        db = get_db()
        row = db.execute("SELECT * FROM users WHERE id = ?", (str(user_id),)).fetchone()
        return User._from_row(row)

    @staticmethod
    def get_by_username_or_email(identifier: str):
        db = get_db()
        row = db.execute(
            "SELECT * FROM users WHERE lower(username)=lower(?) OR lower(email)=lower(?)",
            (identifier, identifier),
        ).fetchone()
        return User._from_row(row)

    @staticmethod
    def create(username: str, email: str, password: str):
        if not _USERNAME_RE.match(username or ""):
            raise ValueError("用户名需为 3-32 位字母/数字/下划线")

        try:
            normalized_email = validate_email(email, check_deliverability=False).email
        except EmailNotValidError:
            raise ValueError("邮箱格式不正确")

        if len(password or "") < 10:
            raise ValueError("密码长度至少 10 位")

        db = get_db()
        password_hash = generate_password_hash(password, method="scrypt")
        cur = db.execute(
            "INSERT INTO users(username, email, password_hash) VALUES(?,?,?)",
            (username, normalized_email, password_hash),
        )
        db.commit()
        return User.get(cur.lastrowid)

    def verify_password(self, password: str) -> bool:
        return check_password_hash(self.password_hash, password)

    @staticmethod
    def _from_row(row):
        if not row:
            return None
        return User(
            id=int(row["id"]),
            username=str(row["username"]),
            email=str(row["email"]),
            password_hash=str(row["password_hash"]),
            disabled=bool(row["disabled"]) if "disabled" in row.keys() else False,
        )

