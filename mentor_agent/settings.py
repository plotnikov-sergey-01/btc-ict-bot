from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _required(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"Set {name} in the mentor environment file")
    return value


def _parse_channel(value: str) -> int | str:
    value = value.strip()
    if not value:
        raise ValueError("empty channel")
    try:
        return int(value)
    except ValueError:
        return value.removeprefix("https://t.me/").removeprefix("@")


@dataclass(frozen=True)
class MentorSettings:
    api_id: int
    api_hash: str
    session: str
    channels: tuple[int | str, ...]
    db_path: Path
    media_dir: Path

    @classmethod
    def from_env(cls) -> "MentorSettings":
        raw_channels = _required("MENTOR_CHANNELS")
        channels = tuple(
            _parse_channel(item) for item in raw_channels.split(",") if item.strip()
        )
        if not channels:
            raise RuntimeError("MENTOR_CHANNELS must contain at least one channel")
        return cls(
            api_id=int(_required("TELEGRAM_API_ID")),
            api_hash=_required("TELEGRAM_API_HASH"),
            session=os.getenv("TELEGRAM_SESSION", "mentor_agent").strip(),
            channels=channels,
            db_path=Path(os.getenv("MENTOR_DB_PATH", "mentor_data/messages.sqlite3")),
            media_dir=Path(os.getenv("MENTOR_MEDIA_DIR", "mentor_data/media")),
        )
