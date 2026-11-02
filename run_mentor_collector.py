#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import logging

from ict_bot.exchange_client import load_env_file
from mentor_agent.collector import MentorCollector
from mentor_agent.settings import MentorSettings


def main() -> int:
    parser = argparse.ArgumentParser(description="Collect mentor Telegram posts")
    parser.add_argument("--env-file", default=".env.mentor")
    args = parser.parse_args()

    load_env_file(args.env_file, override=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    settings = MentorSettings.from_env()
    asyncio.run(MentorCollector(settings).run())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
