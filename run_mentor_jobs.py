#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json

from ict_bot.exchange_client import load_env_file
from mentor_agent.settings import MentorSettings
from mentor_agent.store import MessageStore


def main() -> int:
    parser = argparse.ArgumentParser(description="Inspect or cancel mentor analysis jobs")
    parser.add_argument("--env-file", default=".env.mentor")
    parser.add_argument(
        "--cancel-pending",
        action="store_true",
        help="Cancel all queued jobs; a currently processing job is allowed to finish",
    )
    args = parser.parse_args()

    load_env_file(args.env_file, override=True)
    store = MessageStore(MentorSettings.from_env().db_path)
    before = store.analysis_job_counts()
    cancelled = store.cancel_pending_analysis_jobs() if args.cancel_pending else 0
    output = {
        "before": before,
        "cancelled": cancelled,
        "after": store.analysis_job_counts(),
    }
    print(json.dumps(output, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
