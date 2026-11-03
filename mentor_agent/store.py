from __future__ import annotations

import sqlite3
import json
from typing import Any
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
    media_sha256: str | None
    content_fingerprint: str
    reply_to_message_id: int | None
    reply_to_top_id: int | None
    forward_from_id: int | None
    forward_from_name: str | None
    forward_date: datetime | None


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
    media_sha256 TEXT,
    content_fingerprint TEXT NOT NULL DEFAULT '',
    reply_to_message_id INTEGER,
    reply_to_top_id INTEGER,
    forward_from_id INTEGER,
    forward_from_name TEXT,
    forward_date TEXT,
    collected_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (chat_id, message_id)
);
CREATE TABLE IF NOT EXISTS mentor_analysis_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id INTEGER NOT NULL,
    message_id INTEGER NOT NULL,
    model TEXT NOT NULL,
    prompt_version TEXT NOT NULL,
    response_id TEXT,
    input_tokens INTEGER,
    output_tokens INTEGER,
    total_tokens INTEGER,
    result_json TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
"""

MIGRATION_COLUMNS = {
    "media_sha256": "TEXT",
    "content_fingerprint": "TEXT NOT NULL DEFAULT ''",
    "reply_to_message_id": "INTEGER",
    "reply_to_top_id": "INTEGER",
    "forward_from_id": "INTEGER",
    "forward_from_name": "TEXT",
    "forward_date": "TEXT",
}


class MessageStore:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.executescript(SCHEMA)
            existing = {
                row[1] for row in conn.execute("PRAGMA table_info(telegram_messages)")
            }
            for name, sql_type in MIGRATION_COLUMNS.items():
                if name not in existing:
                    conn.execute(
                        f"ALTER TABLE telegram_messages ADD COLUMN {name} {sql_type}"
                    )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_messages_group "
                "ON telegram_messages(chat_id, grouped_id)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_messages_posted "
                "ON telegram_messages(posted_at)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_messages_fingerprint "
                "ON telegram_messages(content_fingerprint)"
            )

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
            message.media_sha256,
            message.content_fingerprint,
            message.reply_to_message_id,
            message.reply_to_top_id,
            message.forward_from_id,
            message.forward_from_name,
            message.forward_date.isoformat() if message.forward_date else None,
        )
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO telegram_messages (
                    chat_id, message_id, grouped_id, channel_title,
                    channel_username, posted_at, edited_at, text,
                    media_path, media_kind, media_sha256, content_fingerprint,
                    reply_to_message_id, reply_to_top_id, forward_from_id,
                    forward_from_name, forward_date
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(chat_id, message_id) DO UPDATE SET
                    grouped_id=excluded.grouped_id,
                    channel_title=excluded.channel_title,
                    channel_username=excluded.channel_username,
                    edited_at=excluded.edited_at,
                    text=excluded.text,
                    media_path=COALESCE(excluded.media_path, telegram_messages.media_path),
                    media_kind=COALESCE(excluded.media_kind, telegram_messages.media_kind),
                    media_sha256=COALESCE(excluded.media_sha256, telegram_messages.media_sha256),
                    content_fingerprint=excluded.content_fingerprint,
                    reply_to_message_id=excluded.reply_to_message_id,
                    reply_to_top_id=excluded.reply_to_top_id,
                    forward_from_id=excluded.forward_from_id,
                    forward_from_name=excluded.forward_from_name,
                    forward_date=excluded.forward_date
                """,
                values,
            )

    def count(self) -> int:
        with self._connect() as conn:
            row = conn.execute("SELECT COUNT(*) FROM telegram_messages").fetchone()
        return int(row[0])

    def recent(self, limit: int = 20) -> list[dict[str, Any]]:
        with self._connect() as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                """
                SELECT chat_id, message_id, grouped_id, channel_title,
                       posted_at, edited_at, text, media_path, media_kind,
                       content_fingerprint, reply_to_message_id,
                       reply_to_top_id, forward_from_id, forward_from_name,
                       forward_date
                FROM telegram_messages
                ORDER BY posted_at DESC, message_id DESC
                LIMIT ?
                """,
                (max(1, int(limit)),),
            ).fetchall()
        return [dict(row) for row in rows]

    def get(self, chat_id: int, message_id: int) -> dict[str, Any] | None:
        with self._connect() as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT * FROM telegram_messages WHERE chat_id=? AND message_id=?",
                (chat_id, message_id),
            ).fetchone()
        return dict(row) if row else None

    def find_message(self, message_id: int) -> dict[str, Any]:
        with self._connect() as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT * FROM telegram_messages WHERE message_id=?",
                (message_id,),
            ).fetchall()
        if not rows:
            raise LookupError(f"message {message_id} not found")
        if len(rows) > 1:
            raise LookupError(
                f"message {message_id} exists in multiple channels; pass --chat-id"
            )
        return dict(rows[0])

    def context_chain(self, chat_id: int, message_id: int) -> list[dict[str, Any]]:
        current = self.get(chat_id, message_id)
        if current is None:
            raise LookupError(f"message {message_id} not found in chat {chat_id}")
        chain = [current]
        seen = {message_id}
        while current.get("reply_to_message_id"):
            parent_id = int(current["reply_to_message_id"])
            if parent_id in seen:
                break
            parent = self.get(chat_id, parent_id)
            if parent is None:
                break
            chain.append(parent)
            seen.add(parent_id)
            current = parent
        return list(reversed(chain))

    def save_analysis(
        self,
        *,
        chat_id: int,
        message_id: int,
        model: str,
        prompt_version: str,
        response_id: str | None,
        input_tokens: int | None,
        output_tokens: int | None,
        total_tokens: int | None,
        result: dict[str, Any],
    ) -> int:
        with self._connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO mentor_analysis_runs (
                    chat_id, message_id, model, prompt_version, response_id,
                    input_tokens, output_tokens, total_tokens, result_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    chat_id,
                    message_id,
                    model,
                    prompt_version,
                    response_id,
                    input_tokens,
                    output_tokens,
                    total_tokens,
                    json.dumps(result, ensure_ascii=False),
                ),
            )
            return int(cursor.lastrowid)
