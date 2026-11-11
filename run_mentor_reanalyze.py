#!/usr/bin/env python3
from __future__ import annotations

import argparse

from ict_bot.exchange_client import load_env_file
from mentor_agent.settings import MentorSettings
from mentor_agent.store import MessageStore


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Queue recent mentor posts for analysis with the current prompt"
    )
    parser.add_argument("--env-file", default=".env.mentor")
    parser.add_argument("--limit", type=int, default=5)
    args = parser.parse_args()

    load_env_file(args.env_file, override=True)
    settings = MentorSettings.from_env()
    store = MessageStore(settings.db_path)
    targets = store.recent_analysis_targets(args.limit)
    queued = 0
    for chat_id, message_id in targets:
        message = store.get(chat_id, message_id)
        if message is None:
            continue
        store.enqueue_analysis(
            chat_id=chat_id,
            message_id=message_id,
            grouped_id=message.get("grouped_id"),
            delay_sec=0,
        )
        queued += 1
        print(f"queued chat={chat_id} message={message_id}")
    print(f"queued_total={queued}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
