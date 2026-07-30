"""Optional Telegram alerts (errors, restarts, trade events)."""

from __future__ import annotations

import logging
import os
from typing import Any

import requests

log = logging.getLogger(__name__)

TELEGRAM_API = "https://api.telegram.org/bot{token}/sendMessage"
MAX_LEN = 4000


def telegram_configured() -> bool:
    return bool(os.getenv("TELEGRAM_BOT_TOKEN") and os.getenv("TELEGRAM_CHAT_ID"))


def send_telegram(text: str, *, silent: bool = False) -> bool:
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        log.warning("Telegram skipped: TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID not set")
        return False

    payload: dict[str, Any] = {
        "chat_id": chat_id,
        "text": text[:MAX_LEN],
        "disable_notification": silent,
    }
    try:
        r = requests.post(
            TELEGRAM_API.format(token=token),
            json=payload,
            timeout=15,
        )
        r.raise_for_status()
        return True
    except Exception as e:
        log.warning("Telegram send failed: %s", e)
        return False


class TelegramHandler(logging.Handler):
    """Log ERROR+ to Telegram (rate-limit via logging filter in app if needed)."""

    def emit(self, record: logging.LogRecord) -> None:
        if record.levelno < logging.ERROR:
            return
        try:
            msg = self.format(record)
            if record.exc_info:
                import traceback

                msg = msg + "\n" + "".join(traceback.format_exception(*record.exc_info))[-2500:]
            send_telegram(f"🔴 {record.levelname}\n{msg}")
        except Exception:
            self.handleError(record)
