"""15m bar-close strategy loop for demo/live."""

from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

from .config import load_config
from .live_data import last_closed_bar, load_live_market_data
from .live_orders import has_open_position, open_signal_trade
from .notify import send_telegram
from .strategy import Signal, generate_signals

log = logging.getLogger(__name__)

STATE_PATH = Path("data/live_state.json")


def signal_key(sig: Signal) -> str:
    return f"{sig.timestamp.isoformat()}_{sig.direction}_{sig.entry:.4f}_{sig.stop:.4f}"


def _fmt_signal(sig: Signal) -> str:
    side = "LONG" if sig.direction == 1 else "SHORT"
    return (
        f"{side} @ {sig.timestamp.isoformat()}\n"
        f"entry≈{sig.entry:.2f} SL={sig.stop:.2f} TP={sig.take_profit:.2f} R:R={sig.rr:.2f}"
    )


def load_state() -> dict:
    if not STATE_PATH.exists():
        return {"executed": [], "last_bar": None, "missed": []}
    with open(STATE_PATH, encoding="utf-8") as f:
        return json.load(f)


def save_state(state: dict) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(STATE_PATH, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2)


def signals_on_bar(signals: list[Signal], bar_ts: pd.Timestamp) -> list[Signal]:
    return [s for s in signals if s.timestamp == bar_ts]


def signals_between(
    signals: list[Signal],
    after_ts: pd.Timestamp,
    before_ts: pd.Timestamp,
) -> list[Signal]:
    """Signals on bars strictly after after_ts and before before_ts (skipped while offline)."""
    return [s for s in signals if after_ts < s.timestamp < before_ts]


def notify_missed(sigs: list[Signal], reason: str) -> None:
    if not sigs:
        return
    lines = [f"⚠️ Missed entr{'y' if len(sigs) == 1 else 'ies'} ({reason})"]
    for s in sigs[:8]:
        lines.append(_fmt_signal(s))
    if len(sigs) > 8:
        lines.append(f"… +{len(sigs) - 8} more")
    msg = "\n".join(lines)
    log.warning(msg.replace("\n", " | "))
    send_telegram(msg)


def run_strategy_cycle(
    exchange,
    symbol: str,
    cfg: dict | None = None,
    *,
    dry_run: bool = False,
) -> dict:
    cfg = cfg or load_config()
    df_15m, df_1h, df_4h, df_1d, funding = load_live_market_data(exchange, cfg, symbol)
    bar_ts = last_closed_bar(df_15m, 15)

    state = load_state()
    if state.get("last_bar") == bar_ts.isoformat():
        log.info("Bar %s already processed", bar_ts)
        return {"bar": bar_ts.isoformat(), "action": "skip_duplicate"}

    log.info("Running signals for closed bar %s", bar_ts)
    all_signals = generate_signals(df_15m, df_1h, df_4h, df_1d, funding, cfg)
    bar_signals = signals_on_bar(all_signals, bar_ts)
    executed_set = set(state.get("executed", []))
    missed_set = set(state.get("missed", []))

    # Bars we never processed (downtime / failed cycles)
    prev_raw = state.get("last_bar")
    gap_missed: list[Signal] = []
    if prev_raw:
        prev_ts = pd.Timestamp(prev_raw)
        if prev_ts.tzinfo is None:
            prev_ts = prev_ts.tz_localize("UTC")
        gap_missed = [
            s
            for s in signals_between(all_signals, prev_ts, bar_ts)
            if signal_key(s) not in executed_set and signal_key(s) not in missed_set
        ]
        if gap_missed:
            notify_missed(gap_missed, f"skipped bars after {prev_raw}")
            for s in gap_missed:
                missed_set.add(signal_key(s))

    result: dict = {
        "bar": bar_ts.isoformat(),
        "signals_on_bar": len(bar_signals),
        "gap_missed": len(gap_missed),
        "action": "none",
    }

    if not bar_signals:
        state["last_bar"] = bar_ts.isoformat()
        state["missed"] = sorted(missed_set)[-500:]
        save_state(state)
        return result

    if has_open_position(exchange, symbol):
        log.info("Open position exists — skip new entries")
        result["action"] = "skip_open_position"
        notify_missed(bar_signals, "open position already exists")
        for s in bar_signals:
            missed_set.add(signal_key(s))
        state["last_bar"] = bar_ts.isoformat()
        state["missed"] = sorted(missed_set)[-500:]
        save_state(state)
        return result

    traded = False
    for sig in bar_signals:
        key = signal_key(sig)
        if key in executed_set:
            continue
        side = "LONG" if sig.direction == 1 else "SHORT"
        try:
            trade = open_signal_trade(exchange, symbol, sig, cfg, dry_run=dry_run)
            result["action"] = "trade"
            result["trade"] = trade
            executed_set.add(key)
            traded = True
            msg = (
                f"{'🧪 DRY' if dry_run else '📈'} {side} BTC\n"
                f"entry≈{sig.entry:.2f} SL={sig.stop:.2f} TP={sig.take_profit:.2f}\n"
                f"R:R={sig.rr:.2f} amt={trade.get('amount')}"
            )
            send_telegram(msg)
            break  # one trade per cycle (max 1 open position)
        except Exception as e:
            log.exception("Trade failed")
            send_telegram(f"🔴 Trade failed {side}\n{e}")
            result["action"] = "error"
            result["error"] = str(e)
            missed_set.add(key)
            break

    # Extra signals on same bar not taken (max 1 position / already executed)
    leftover = [
        s
        for s in bar_signals
        if signal_key(s) not in executed_set and signal_key(s) not in missed_set
    ]
    if leftover and (traded or result["action"] == "none"):
        reason = "already took one trade this cycle" if traded else "not executed"
        notify_missed(leftover, reason)
        for s in leftover:
            missed_set.add(signal_key(s))

    state["executed"] = sorted(executed_set)[-500:]
    state["missed"] = sorted(missed_set)[-500:]
    state["last_bar"] = bar_ts.isoformat()
    save_state(state)
    return result


def seconds_until_next_15m_close(buffer_sec: int = 5) -> float:
    now = datetime.now(timezone.utc)
    minute = now.minute
    next_slot = (minute // 15 + 1) * 15
    target = now.replace(second=0, microsecond=0)
    if next_slot >= 60:
        target = target.replace(minute=0) + timedelta(hours=1)
    else:
        target = target.replace(minute=next_slot)
    target = target + timedelta(seconds=buffer_sec)
    wait = (target - now).total_seconds()
    return max(wait, 1.0)


def sleep_until_next_15m_close(buffer_sec: int = 5) -> None:
    wait = seconds_until_next_15m_close(buffer_sec)
    log.info("Sleep %.0fs until next 15m close + buffer", wait)
    time.sleep(wait)
