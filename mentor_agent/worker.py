from __future__ import annotations

import json
import logging
import os
import time

from ict_bot.notify import send_telegram

from .analyzer import analyze_message
from .store import MessageStore

log = logging.getLogger(__name__)


def _summary(output: dict) -> str:
    result = output["result"]
    usage = output["usage"]
    lines = [
        "🧠 [mentor] New post analysis",
        f"{result.get('symbol') or '?'} {str(result.get('direction')).upper()} · {result.get('classification')}",
        f"action={result.get('actionability')} stage={result.get('setup_stage')}",
        str(result.get("summary") or ""),
        f"monitor={result.get('monitor_market')} trade_plan={result.get('allow_trade_plan')} execution=False",
        f"confidence={result.get('confidence')} tokens={usage.get('total_tokens')}",
    ]
    missing = result.get("missing_information") or []
    if missing:
        lines.append("Missing: " + "; ".join(str(x) for x in missing[:4]))
    return "\n".join(lines)


def process_one(store: MessageStore) -> bool:
    max_daily = int(os.getenv("MENTOR_MAX_ANALYSES_PER_DAY", "20"))
    if store.analyses_last_24h() >= max_daily:
        log.warning("Daily analysis limit reached (%s); waiting", max_daily)
        return False
    job = store.claim_analysis_job()
    if job is None:
        return False
    try:
        output = analyze_message(
            store,
            chat_id=job["chat_id"],
            message_id=job["message_id"],
        )
        store.finish_analysis_jobs(job["job_ids"])
        log.info("Analysis complete: %s", json.dumps(output, ensure_ascii=False))
        send_telegram(_summary(output))
    except Exception as exc:
        store.fail_analysis_jobs(job["job_ids"], f"{type(exc).__name__}: {exc}")
        log.exception(
            "Analysis failed chat=%s message=%s",
            job["chat_id"],
            job["message_id"],
        )
    return True


def run_worker(store: MessageStore, *, once: bool = False) -> None:
    poll_sec = max(5, int(os.getenv("MENTOR_ANALYSIS_POLL_SEC", "15")))
    while True:
        processed = process_one(store)
        if once:
            return
        if not processed:
            time.sleep(poll_sec)
