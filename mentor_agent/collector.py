from __future__ import annotations

import logging
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

    async def _save(self, event) -> None:
        message: Message = event.message
        chat = await event.get_chat()
        chat_id = int(event.chat_id)
        media_path: str | None = None
        kind = _media_kind(message)

        if message.media:
            channel_dir = self.settings.media_dir / str(chat_id)
            channel_dir.mkdir(parents=True, exist_ok=True)
            target = channel_dir / f"{message.id}"
            downloaded = await message.download_media(file=str(target))
            if downloaded:
                media_path = str(Path(downloaded).resolve())

        record = StoredMessage(
            chat_id=chat_id,
            message_id=int(message.id),
            grouped_id=int(message.grouped_id) if message.grouped_id else None,
            channel_title=str(getattr(chat, "title", None) or chat_id),
            channel_username=getattr(chat, "username", None),
            posted_at=message.date,
            edited_at=message.edit_date,
            text=message.raw_text or "",
            media_path=media_path,
            media_kind=kind,
        )
        self.store.upsert(record)
        log.info(
            "Saved channel=%s message=%s group=%s media=%s total=%s",
            record.channel_title,
            record.message_id,
            record.grouped_id,
            record.media_kind,
            self.store.count(),
        )

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
