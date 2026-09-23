import os
import sqlite3
from pathlib import Path

from flask import current_app, g


def _connect(path: str) -> sqlite3.Connection:
    db = sqlite3.connect(path, detect_types=sqlite3.PARSE_DECLTYPES, check_same_thread=False)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys = ON")
    try:
        db.execute("PRAGMA journal_mode = WAL")
    except sqlite3.OperationalError:
        pass
    return db


def get_db() -> sqlite3.Connection:
    if "db" not in g:
        db_path = current_app.config["DATABASE_PATH"]
        Path(os.path.dirname(db_path) or ".").mkdir(parents=True, exist_ok=True)
        g.db = _connect(db_path)
    return g.db


def close_db(_e=None):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def _add_columns(db: sqlite3.Connection, table: str, columns: dict[str, str]):
    existing = {row[1] for row in db.execute(f"PRAGMA table_info({table})").fetchall()}
    for name, ddl in columns.items():
        if name not in existing:
            db.execute(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}")


def ensure_tables(db: sqlite3.Connection):
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS users (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          username TEXT UNIQUE NOT NULL,
          email TEXT UNIQUE NOT NULL,
          password_hash TEXT NOT NULL,
          created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )

    db.execute(
        """
        CREATE TABLE IF NOT EXISTS conversations (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          user_id INTEGER NOT NULL,
          title TEXT NOT NULL,
          model_name TEXT,
          memory_enabled INTEGER NOT NULL DEFAULT 1,
          created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
        )
        """
    )
    db.execute("CREATE INDEX IF NOT EXISTS idx_conversations_user_updated ON conversations(user_id, updated_at DESC)")
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS invite_codes (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          code TEXT UNIQUE NOT NULL,
          created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          reserved_at TEXT,
          reservation_token TEXT,
          used_at TEXT,
          used_by_user_id INTEGER,
          disabled INTEGER NOT NULL DEFAULT 0,
          FOREIGN KEY(used_by_user_id) REFERENCES users(id) ON DELETE SET NULL
        )
        """
    )
    db.execute("CREATE INDEX IF NOT EXISTS idx_invite_codes_code ON invite_codes(code)")
    db.execute("CREATE INDEX IF NOT EXISTS idx_invite_codes_res_token ON invite_codes(reservation_token)")

    columns = {row[1] for row in db.execute("PRAGMA table_info(invite_codes)").fetchall()}
    if "reserved_at" not in columns:
        db.execute("ALTER TABLE invite_codes ADD COLUMN reserved_at TEXT")
    if "reservation_token" not in columns:
        db.execute("ALTER TABLE invite_codes ADD COLUMN reservation_token TEXT")
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS chat_messages (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          user_id INTEGER NOT NULL,
          conversation_id INTEGER,
          role TEXT NOT NULL CHECK(role IN ('user','model')),
          content TEXT NOT NULL,
          model_name TEXT,
          prompt_tokens INTEGER,
          completion_tokens INTEGER,
          total_tokens INTEGER,
          created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE,
          FOREIGN KEY(conversation_id) REFERENCES conversations(id) ON DELETE CASCADE
        )
        """
    )
    db.execute("CREATE INDEX IF NOT EXISTS idx_chat_messages_user_id_id ON chat_messages(user_id, id)")

    msg_cols = {row[1] for row in db.execute("PRAGMA table_info(chat_messages)").fetchall()}
    if "conversation_id" not in msg_cols:
        db.execute("ALTER TABLE chat_messages ADD COLUMN conversation_id INTEGER")
    if "model_name" not in msg_cols:
        db.execute("ALTER TABLE chat_messages ADD COLUMN model_name TEXT")
    if "prompt_tokens" not in msg_cols:
        db.execute("ALTER TABLE chat_messages ADD COLUMN prompt_tokens INTEGER")
    if "completion_tokens" not in msg_cols:
        db.execute("ALTER TABLE chat_messages ADD COLUMN completion_tokens INTEGER")
    if "total_tokens" not in msg_cols:
        db.execute("ALTER TABLE chat_messages ADD COLUMN total_tokens INTEGER")

    db.execute("CREATE INDEX IF NOT EXISTS idx_chat_messages_user_conv_id ON chat_messages(user_id, conversation_id, id)")

    db.execute(
        """
        CREATE TABLE IF NOT EXISTS login_failures (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          identifier TEXT NOT NULL,
          ip TEXT NOT NULL,
          failed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    db.execute("CREATE INDEX IF NOT EXISTS idx_login_failures_ident_ip_at ON login_failures(identifier, ip, failed_at)")

    db.execute(
        """
        CREATE TABLE IF NOT EXISTS login_lockouts (
          identifier TEXT NOT NULL,
          ip TEXT NOT NULL,
          locked_until TEXT NOT NULL,
          updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          PRIMARY KEY(identifier, ip)
        )
        """
    )

    db.execute(
        """
        CREATE TABLE IF NOT EXISTS memory_items (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          user_id INTEGER NOT NULL,
          role TEXT NOT NULL CHECK(role IN ('user','model')),
          content TEXT NOT NULL,
          embedding_json TEXT,
          source_conversation_id INTEGER,
          source_message_id INTEGER,
          created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
        )
        """
    )
    db.execute("CREATE INDEX IF NOT EXISTS idx_memory_items_user_id_id ON memory_items(user_id, id)")

    db.execute(
        """
        CREATE TABLE IF NOT EXISTS uploaded_files (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          user_id INTEGER NOT NULL,
          original_name TEXT NOT NULL,
          storage_path TEXT NOT NULL,
          mime_type TEXT,
          size_bytes INTEGER NOT NULL,
          extracted_text TEXT,
          created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
        )
        """
    )
    db.execute("CREATE INDEX IF NOT EXISTS idx_uploaded_files_user_id_id ON uploaded_files(user_id, id)")

    # v2：只加列，不改不删，旧数据保持可读。
    _add_columns(
        db,
        "users",
        {"disabled": "INTEGER NOT NULL DEFAULT 0", "last_seen_at": "TEXT"},
    )
    _add_columns(
        db,
        "conversations",
        {"system_prompt": "TEXT", "pinned": "INTEGER NOT NULL DEFAULT 0"},
    )
    _add_columns(
        db,
        "chat_messages",
        {
            # JSON 数组：本条消息关联的 uploaded_files.id（上传的附件或生成的图片）
            "attachments_json": "TEXT",
            "thoughts": "TEXT",
            # JSON：{"queries": [...], "sources": [{"title","uri"}]}
            "grounding_json": "TEXT",
            # JSON：{"thinking": "...", "tools": [...], "elapsed_ms": ..., "aspect_ratio": ...}
            "meta_json": "TEXT",
            "thoughts_tokens": "INTEGER",
        },
    )
    _add_columns(db, "uploaded_files", {"source": "TEXT NOT NULL DEFAULT 'upload'", "ocr_text": "TEXT"})
    _add_columns(db, "memory_items", {"embed_model": "TEXT"})
    db.execute("CREATE INDEX IF NOT EXISTS idx_chat_messages_created ON chat_messages(created_at)")

    # Backfill: ensure each existing user has at least one conversation and old messages are linked.
    user_rows = db.execute("SELECT id FROM users").fetchall()
    for r in user_rows:
        user_id = int(r["id"])
        row = db.execute(
            "SELECT id FROM conversations WHERE user_id=? ORDER BY updated_at DESC, id DESC LIMIT 1",
            (user_id,),
        ).fetchone()
        if row:
            conv_id = int(row["id"])
        else:
            cur = db.execute(
                "INSERT INTO conversations(user_id, title) VALUES(?,?)",
                (user_id, "默认对话"),
            )
            conv_id = int(cur.lastrowid)

        db.execute(
            "UPDATE chat_messages SET conversation_id=? WHERE user_id=? AND conversation_id IS NULL",
            (conv_id, user_id),
        )
        db.execute(
            """
            UPDATE conversations
            SET updated_at=COALESCE((SELECT MAX(created_at) FROM chat_messages WHERE conversation_id=conversations.id), updated_at)
            WHERE id=?
            """,
            (conv_id,),
        )


def init_app(app):
    app.teardown_appcontext(close_db)
    with app.app_context():
        db = get_db()
        ensure_tables(db)
        db.commit()
