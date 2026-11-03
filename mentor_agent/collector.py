from __future__ import annotations

import logging
import hashlib
import os
from pathlib import Path

from telethon import TelegramClient, events
from telethon.tl.custom.message import Message

from .settings import MentorSettings
from .store import MessageStore, StoredMessage

log = logging.getLogger(__name__)


def _media_kind(message: Message) -> str | None:
    if message.photo:
        return "photo"
    if message.video:
        return "video"
    if message.document:
        return "document"
    if message.media:
        return type(message.media).__name__
    return None


def _sha256_file(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _peer_id(peer) -> int | None:
    if peer is None:
        return None
    for name in ("channel_id", "chat_id", "user_id"):
        value = getattr(peer, name, None)
        if value is not None:
            return int(value)
    return None


def _fingerprint(text: str, media_sha256: str | None) -> str:
    normalized = " ".join(text.casefold().split())
    payload = f"{normalized}\n{media_sha256 or ''}".encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


class MentorCollector:
    def __init__(self, settings: MentorSettings):
        self.settings = settings
        self.store = MessageStore(settings.db_path)
        settings.media_dir.mkdir(parents=True, exist_ok=True)
        self.client = TelegramClient(
            settings.session,
            settings.api_id,
            settings.api_hash,
        )

    async def _save_message(
        self, message: Message, chat, chat_id: int, *, enqueue: bool
    ) -> None:
        media_path: str | None = None
        media_sha256: str | None = None
        kind = _media_kind(message)

        if message.media:
            channel_dir = self.settings.media_dir / str(chat_id)
            channel_dir.mkdir(parents=True, exist_ok=True)
            target = channel_dir / f"{message.id}"
            downloaded = await message.download_media(file=str(target))
            if downloaded:
                media_path = str(Path(downloaded).resolve())
                media_sha256 = _sha256_file(media_path)

        forward = message.fwd_from
        reply = message.reply_to
        text = message.raw_text or ""

        record = StoredMessage(
            chat_id=chat_id,
            message_id=int(message.id),
            grouped_id=int(message.grouped_id) if message.grouped_id else None,
            channel_title=str(getattr(chat, "title", None) or chat_id),
            channel_username=getattr(chat, "username", None),
            posted_at=message.date,
            edited_at=message.edit_date,
            text=text,
            media_path=media_path,
            media_kind=kind,
            media_sha256=media_sha256,
            content_fingerprint=_fingerprint(text, media_sha256),
            reply_to_message_id=int(message.reply_to_msg_id) if message.reply_to_msg_id else None,
            reply_to_top_id=int(reply.reply_to_top_id) if reply and reply.reply_to_top_id else None,
            forward_from_id=_peer_id(forward.from_id) if forward else None,
            forward_from_name=getattr(forward, "from_name", None) if forward else None,
            forward_date=getattr(forward, "date", None) if forward else None,
        )
        self.store.upsert(record)
        if enqueue:
            self.store.enqueue_analysis(
                chat_id=chat_id,
                message_id=record.message_id,
                grouped_id=record.grouped_id,
                delay_sec=int(os.getenv("MENTOR_ANALYSIS_DELAY_SEC", "90")),
            )
        log.info(
            "Saved channel=%s message=%s group=%s media=%s total=%s",
            record.channel_title,
            record.message_id,
            record.grouped_id,
            record.media_kind,
            self.store.count(),
        )

    async def _save(self, event) -> None:
        message: Message = event.message
        chat = await event.get_chat()
        chat_id = int(event.chat_id)
        await self._save_message(message, chat, chat_id, enqueue=True)

        # A reply may point to a post created before the collector started.
        # Save that direct parent as context without recursively importing history.
        if message.reply_to_msg_id:
            parent = await message.get_reply_message()
            if parent is not None:
                await self._save_message(parent, chat, chat_id, enqueue=False)

    async def run(self) -> None:
        await self.client.start()
        entities = []
        for channel in self.settings.channels:
            entity = await self.client.get_entity(channel)
            entities.append(entity)
            log.info(
                "Watching %s (%s)",
                getattr(entity, "title", channel),
                getattr(entity, "id", "unknown"),
            )

        self.client.add_event_handler(
            self._save,
            events.NewMessage(chats=entities),
        )
        self.client.add_event_handler(
            self._save,
            events.MessageEdited(chats=entities),
        )
        log.info("Collector ready; existing history is not imported automatically")
        await self.client.run_until_disconnected()
