#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json

from ict_bot.exchange_client import load_env_file
from mentor_agent.analyzer import analyze_message
from mentor_agent.settings import MentorSettings
from mentor_agent.store import MessageStore


def main() -> int:
    parser = argparse.ArgumentParser(description="Analyze one collected mentor thread")
    parser.add_argument("message_id", type=int)
    parser.add_argument("--chat-id", type=int)
    parser.add_argument("--env-file", default=".env.mentor")
    parser.add_argument("--model", default=None)
    args = parser.parse_args()

    load_env_file(args.env_file, override=True)
    settings = MentorSettings.from_env()
    store = MessageStore(settings.db_path)
    if args.chat_id is None:
        message = store.find_message(args.message_id)
        chat_id = int(message["chat_id"])
    else:
        chat_id = args.chat_id
    output = analyze_message(
        store,
        chat_id=chat_id,
        message_id=args.message_id,
        model=args.model,
    )
    print(json.dumps(output, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
