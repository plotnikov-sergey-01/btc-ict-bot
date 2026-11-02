from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


@dataclass(frozen=True)
class StoredMessage:
    chat_id: int
    message_id: int
    grouped_id: int | None
    channel_title: str
    channel_username: str | None
    posted_at: datetime
    edited_at: datetime | None
    text: str
    media_path: str | None
    media_kind: str | None


SCHEMA = """
CREATE TABLE IF NOT EXISTS telegram_messages (
    chat_id INTEGER NOT NULL,
    message_id INTEGER NOT NULL,
    grouped_id INTEGER,
    channel_title TEXT NOT NULL,
    channel_username TEXT,
    posted_at TEXT NOT NULL,
    edited_at TEXT,
    text TEXT NOT NULL,
    media_path TEXT,
    media_kind TEXT,
    collected_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (chat_id, message_id)
);
CREATE INDEX IF NOT EXISTS idx_messages_group
    ON telegram_messages(chat_id, grouped_id);
CREATE INDEX IF NOT EXISTS idx_messages_posted
    ON telegram_messages(posted_at);
"""


class MessageStore:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.executescript(SCHEMA)

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.path)

    def upsert(self, message: StoredMessage) -> None:
        values = (
            message.chat_id,
            message.message_id,
            message.grouped_id,
            message.channel_title,
            message.channel_username,
            message.posted_at.isoformat(),
            message.edited_at.isoformat() if message.edited_at else None,
            message.text,
            message.media_path,
            message.media_kind,
        )
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO telegram_messages (
                    chat_id, message_id, grouped_id, channel_title,
                    channel_username, posted_at, edited_at, text,
                    media_path, media_kind
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(chat_id, message_id) DO UPDATE SET
                    grouped_id=excluded.grouped_id,
                    channel_title=excluded.channel_title,
                    channel_username=excluded.channel_username,
                    edited_at=excluded.edited_at,
                    text=excluded.text,
                    media_path=COALESCE(excluded.media_path, telegram_messages.media_path),
                    media_kind=COALESCE(excluded.media_kind, telegram_messages.media_kind)
                """,
                values,
            )

    def count(self) -> int:
        with self._connect() as conn:
            row = conn.execute("SELECT COUNT(*) FROM telegram_messages").fetchone()
        return int(row[0])
