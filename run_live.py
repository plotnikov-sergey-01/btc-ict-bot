#!/usr/bin/env python3
"""
Live / demo runner.

  python run_live.py --once              # connectivity ping
  python run_live.py --cycle-once        # one 15m strategy cycle
  python run_live.py --cycle-once --dry-run
  python run_live.py                     # loop: wait for 15m close → cycle
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
import traceback
from datetime import datetime, timezone

from ict_bot.config import load_config
from ict_bot.exchange_client import _truthy, load_env_file, make_exchange, ping
from ict_bot.live_loop import run_strategy_cycle, sleep_until_next_15m_close
from ict_bot.notify import TelegramHandler, send_telegram, telegram_configured

log = logging.getLogger("ict_live")


def setup_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    if telegram_configured():
        tg = TelegramHandler()
        tg.setLevel(logging.ERROR)
        tg.setFormatter(logging.Formatter("%(message)s"))
        logging.getLogger().addHandler(tg)


def run_ping(symbol: str) -> None:
    ex = make_exchange()
    info = ping(ex, symbol)
    log.info("Ping OK: %s", info)
    send_telegram(
        f"✅ ICT bot ping\n{symbol} last={info.get('last')}\nUSDT free={info.get('usdt_free')}",
        silent=True,
    )


def mode_label() -> str:
    if _truthy("BINANCE_USE_DEMO", default=False):
        return "demo"
    if _truthy("BINANCE_TESTNET", default=False):
        return "testnet"
    return "MAINNET"


def main() -> int:
    parser = argparse.ArgumentParser(description="BTC ICT live/demo runner")
    parser.add_argument("--symbol", default="BTC/USDT:USDT", help="CCXT linear perp symbol")
    parser.add_argument("--once", action="store_true", help="Connectivity ping only")
    parser.add_argument("--cycle-once", action="store_true", help="Single 15m strategy cycle")
    parser.add_argument("--dry-run", action="store_true", help="Log plan only, no orders")
    parser.add_argument(
        "--interval",
        type=int,
        default=60,
        help="Unused in trade loop (15m bar sync); legacy heartbeat fallback",
    )
    args = parser.parse_args()
    load_env_file()
    setup_logging()

    dry_run = args.dry_run or _truthy("LIVE_DRY_RUN", default=False)
    cfg = load_config()

    try:
        if not send_telegram(
            f"🟢 ICT bot starting\nUTC {datetime.now(timezone.utc).isoformat()}\n"
            f"mode={mode_label()} dry_run={dry_run}",
        ):
            log.warning(
                "Startup Telegram not sent (check TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID in .env)"
            )

        if args.once:
            run_ping(args.symbol)
            return 0

        exchange = make_exchange()

        def do_cycle() -> None:
            r = run_strategy_cycle(exchange, args.symbol, cfg, dry_run=dry_run)
            log.info("Cycle result: %s", r)

        if args.cycle_once:
            do_cycle()
            return 0

        while True:
            sleep_until_next_15m_close()
            try:
                do_cycle()
            except Exception as e:
                log.exception("Cycle failed")
                send_telegram(
                    f"⚠️ Cycle failed (bot still running)\n"
                    f"{type(e).__name__}: {e}\n"
                    f"Check demo-fapi.binance.com / network; will retry next 15m bar."
                )

    except KeyboardInterrupt:
        log.info("Stopped by user")
        send_telegram("🟡 ICT bot stopped (KeyboardInterrupt)")
        return 0
    except Exception:
        log.exception("Fatal error")
        send_telegram(f"🔴 ICT bot crashed\n{traceback.format_exc()[-3500:]}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
