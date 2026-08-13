#!/usr/bin/env python3
"""
Live / demo runner.

  python run_live.py --once
  python run_live.py --cycle-once --dry-run
  python run_live.py --config configs/ict_daily_on.yaml --env-file .env.daily_on
  python run_live.py --config configs/ict_daily_off.yaml --env-file .env.daily_off
"""

from __future__ import annotations

import argparse
import logging
import sys
import traceback
from datetime import datetime, timezone

from ict_bot.config import load_config
from ict_bot.exchange_client import _truthy, load_env_file, make_exchange, ping
from ict_bot.live_loop import run_strategy_cycle, sleep_until_next_15m_with_sweep
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


def run_ping(symbol: str, env_file: str | None) -> None:
    ex = make_exchange(env_file=env_file)
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
    parser.add_argument("--config", default="config.yaml", help="Strategy YAML path")
    parser.add_argument(
        "--env-file",
        default=None,
        help="Optional .env path (overrides; systemd EnvironmentFile also works)",
    )
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

    if args.env_file:
        load_env_file(args.env_file, override=True)
    else:
        load_env_file()

    setup_logging()

    dry_run = args.dry_run or _truthy("LIVE_DRY_RUN", default=False)
    cfg = load_config(args.config)
    bot = str((cfg.get("live") or {}).get("bot_label") or "ict")
    daily = cfg.get("mtf", {}).get("require_daily")

    try:
        if not send_telegram(
            f"🟢 [{bot}] ICT bot starting\n"
            f"UTC {datetime.now(timezone.utc).isoformat()}\n"
            f"mode={mode_label()} daily={daily} dry_run={dry_run}\n"
            f"config={args.config}",
        ):
            log.warning(
                "Startup Telegram not sent (check TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID)"
            )

        if args.once:
            run_ping(args.symbol, args.env_file)
            return 0

        exchange = make_exchange(env_file=args.env_file)

        def do_cycle() -> None:
            r = run_strategy_cycle(exchange, args.symbol, cfg, dry_run=dry_run)
            log.info("Cycle result: %s", r)

        if args.cycle_once:
            do_cycle()
            return 0

        while True:
            sleep_until_next_15m_with_sweep(
                exchange, args.symbol, cfg, dry_run=dry_run
            )
            try:
                do_cycle()
            except Exception as e:
                log.exception("[%s] Cycle failed", bot)
                send_telegram(
                    f"⚠️ [{bot}] Cycle failed (bot still running)\n"
                    f"{type(e).__name__}: {e}\n"
                    f"Check demo-fapi.binance.com / network; will retry next 15m bar."
                )

    except KeyboardInterrupt:
        log.info("Stopped by user")
        send_telegram(f"🟡 [{bot}] ICT bot stopped (KeyboardInterrupt)")
        return 0
    except Exception:
        log.exception("Fatal error")
        send_telegram(f"🔴 [{bot}] ICT bot crashed\n{traceback.format_exc()[-3500:]}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
