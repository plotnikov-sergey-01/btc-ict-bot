#!/usr/bin/env python3
from __future__ import annotations

import argparse
import logging
from pathlib import Path

from ict_bot.exchange_client import load_env_file
from mentor_agent.settings import MentorSettings
from mentor_agent.store import MessageStore
from mentor_agent.worker import run_worker


def main() -> int:
    parser = argparse.ArgumentParser(description="Analyze queued mentor posts")
    parser.add_argument("--env-file", default=".env.mentor")
    parser.add_argument("--notify-env-file", default=".env.daily_on")
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()

    load_env_file(args.env_file, override=True)
    if args.notify_env_file and Path(args.notify_env_file).is_file():
        load_env_file(args.notify_env_file, override=False)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    settings = MentorSettings.from_env()
    run_worker(MessageStore(settings.db_path), once=args.once)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
