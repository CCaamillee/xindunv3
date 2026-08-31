from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CHAT_DATABASE_PATH = PROJECT_ROOT / "chat_history.db"
DEFAULT_MAX_MESSAGES = 100


def get_chat_database_path() -> Path:
    configured = os.getenv("CHAT_HISTORY_DB_PATH", "").strip()
    path = Path(configured).expanduser() if configured else DEFAULT_CHAT_DATABASE_PATH
    return (path if path.is_absolute() else PROJECT_ROOT / path).resolve()


def _connect(database_path: Path | None = None) -> sqlite3.Connection:
    path = (database_path or get_chat_database_path()).resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path, timeout=10)
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA busy_timeout=10000")
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS chat_messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            encounter_key TEXT NOT NULL,
            role TEXT NOT NULL,
            payload TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_chat_messages_encounter "
        "ON chat_messages(encounter_key, id)"
    )
    return connection


def load_encounter_chat(
    encounter_key: str,
    *,
    limit: int = DEFAULT_MAX_MESSAGES,
    database_path: Path | None = None,
) -> list[dict[str, Any]]:
    """Load the latest chat messages shared by one encounter."""
    bounded_limit = max(1, min(int(limit), DEFAULT_MAX_MESSAGES))
    with _connect(database_path) as connection:
        rows = connection.execute(
            """
            SELECT payload
            FROM chat_messages
            WHERE encounter_key = ?
            ORDER BY id DESC
            LIMIT ?
            """,
            (str(encounter_key), bounded_limit),
        ).fetchall()

    messages: list[dict[str, Any]] = []
    for (payload,) in reversed(rows):
        try:
            message = json.loads(payload)
        except (TypeError, json.JSONDecodeError):
            continue
        if isinstance(message, dict) and message.get("role") in {"user", "assistant"}:
            messages.append(message)
    return messages


def append_chat_message(
    encounter_key: str,
    message: dict[str, Any],
    *,
    database_path: Path | None = None,
    max_messages: int = DEFAULT_MAX_MESSAGES,
) -> None:
    role = str(message.get("role") or "").strip()
    content = message.get("content")
    if role not in {"user", "assistant"} or not isinstance(content, str):
        raise ValueError("聊天消息必须包含有效的 role 和 content")

    payload = json.dumps(message, ensure_ascii=False, default=str)
    bounded_max = max(2, min(int(max_messages), DEFAULT_MAX_MESSAGES))
    with _connect(database_path) as connection:
        connection.execute(
            "INSERT INTO chat_messages(encounter_key, role, payload) VALUES (?, ?, ?)",
            (str(encounter_key), role, payload),
        )
        connection.execute(
            """
            DELETE FROM chat_messages
            WHERE encounter_key = ?
              AND id NOT IN (
                  SELECT id
                  FROM chat_messages
                  WHERE encounter_key = ?
                  ORDER BY id DESC
                  LIMIT ?
              )
            """,
            (str(encounter_key), str(encounter_key), bounded_max),
        )


def clear_encounter_chat(
    encounter_key: str,
    *,
    database_path: Path | None = None,
) -> None:
    with _connect(database_path) as connection:
        connection.execute(
            "DELETE FROM chat_messages WHERE encounter_key = ?",
            (str(encounter_key),),
        )
