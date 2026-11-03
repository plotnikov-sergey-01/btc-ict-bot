#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json

from ict_bot.exchange_client import load_env_file
from mentor_agent.settings import MentorSettings
from mentor_agent.store import MessageStore


def _compact(row: dict, text_limit: int) -> dict:
    text = " ".join((row.get("text") or "").split())
    return {
        "channel": row.get("channel_title"),
        "message_id": row.get("message_id"),
        "posted_at": row.get("posted_at"),
        "reply_to": row.get("reply_to_message_id"),
        "album_id": row.get("grouped_id"),
        "forward_from_id": row.get("forward_from_id"),
        "forward_from_name": row.get("forward_from_name"),
        "media_kind": row.get("media_kind"),
        "media_path": row.get("media_path"),
        "fingerprint": (row.get("content_fingerprint") or "")[:12],
        "text": text[:text_limit],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Inspect collected mentor posts")
    parser.add_argument("--env-file", default=".env.mentor")
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument("--text-limit", type=int, default=500)
    args = parser.parse_args()

    load_env_file(args.env_file, override=True)
    settings = MentorSettings.from_env()
    store = MessageStore(settings.db_path)
    rows = [_compact(row, args.text_limit) for row in store.recent(args.limit)]
    print(json.dumps({"count": store.count(), "messages": rows}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
