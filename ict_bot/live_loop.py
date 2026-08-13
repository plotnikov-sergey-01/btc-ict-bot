"""15m bar-close strategy loop for demo/live."""

from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

from .config import load_config
from .live_data import last_closed_bar, load_live_market_data, slice_for_live_signals
from .live_orders import (
    EntryDeviationError,
    has_open_position,
    open_signal_trade,
    sweep_orphan_orders,
)
from .notify import send_telegram
from .strategy import Signal, generate_signals

log = logging.getLogger(__name__)


def _state_path(cfg: dict | None) -> Path:
    live = (cfg or {}).get("live") or {}
    return Path(live.get("state_file") or "data/live_state.json")


def signal_key(sig: Signal) -> str:
    return f"{sig.timestamp.isoformat()}_{sig.direction}_{sig.entry:.4f}_{sig.stop:.4f}"


def _bot_label(cfg: dict | None) -> str:
    live = (cfg or {}).get("live") or {}
    return str(live.get("bot_label") or "ict")


def _fmt_signal(sig: Signal) -> str:
    side = "LONG" if sig.direction == 1 else "SHORT"
    return (
        f"{side} @ {sig.timestamp.isoformat()}\n"
        f"entry≈{sig.entry:.2f} SL={sig.stop:.2f} TP={sig.take_profit:.2f} R:R={sig.rr:.2f}"
    )


def load_state(cfg: dict | None = None) -> dict:
    path = _state_path(cfg)
    if not path.exists():
        return {"executed": [], "last_bar": None, "missed": []}
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def save_state(state: dict, cfg: dict | None = None) -> None:
    path = _state_path(cfg)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
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


def notify_missed(sigs: list[Signal], reason: str, cfg: dict | None = None) -> None:
    if not sigs:
        return
    label = _bot_label(cfg)
    lines = [f"⚠️ [{label}] Missed entr{'y' if len(sigs) == 1 else 'ies'} ({reason})"]
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
    label = _bot_label(cfg)
    if not dry_run:
        n_orphans = sweep_orphan_orders(exchange, symbol)
        if n_orphans:
            send_telegram(
                f"🧹 [{label}] Cancelled {n_orphans} leftover order(s) (position flat)"
            )
    df_15m, df_1h, df_4h, df_1d, funding = load_live_market_data(exchange, cfg, symbol)
    bar_ts = last_closed_bar(df_15m, 15)

    state = load_state(cfg)
    if state.get("last_bar") == bar_ts.isoformat():
        log.info("[%s] Bar %s already processed", label, bar_ts)
        return {"bar": bar_ts.isoformat(), "action": "skip_duplicate", "bot": label}

    lookback_days = int(cfg.get("live", {}).get("signal_lookback_days", 90))
    df_15m, df_1h, df_4h, df_1d, funding = slice_for_live_signals(
        df_15m, df_1h, df_4h, df_1d, funding, lookback_days
    )

    log.info("[%s] Running signals for closed bar %s", label, bar_ts)
    t0 = time.perf_counter()
    all_signals = generate_signals(df_15m, df_1h, df_4h, df_1d, funding, cfg)
    log.info(
        "[%s] generate_signals done in %.1fs (%s signals in window)",
        label,
        time.perf_counter() - t0,
        len(all_signals),
    )
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
            notify_missed(gap_missed, f"skipped bars after {prev_raw}", cfg)
            for s in gap_missed:
                missed_set.add(signal_key(s))

    result: dict = {
        "bot": label,
        "bar": bar_ts.isoformat(),
        "signals_on_bar": len(bar_signals),
        "gap_missed": len(gap_missed),
        "action": "none",
    }

    if not bar_signals:
        state["last_bar"] = bar_ts.isoformat()
        state["missed"] = sorted(missed_set)[-500:]
        save_state(state, cfg)
        return result

    if has_open_position(exchange, symbol):
        log.info("[%s] Open position exists — skip new entries", label)
        result["action"] = "skip_open_position"
        notify_missed(bar_signals, "open position already exists", cfg)
        for s in bar_signals:
            missed_set.add(signal_key(s))
        state["last_bar"] = bar_ts.isoformat()
        state["missed"] = sorted(missed_set)[-500:]
        save_state(state, cfg)
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
            fill = trade.get("fill") or trade.get("live_price") or sig.entry
            slip = trade.get("fill_slip_usd", trade.get("slip_usd", 0))
            msg = (
                f"{'🧪 DRY' if dry_run else '📈'} [{label}] {side} BTC\n"
                f"signal≈{sig.entry:.2f} fill≈{float(fill):.2f} (Δ${slip})\n"
                f"SL={sig.stop:.2f} TP={sig.take_profit:.2f}\n"
                f"R:R={sig.rr:.2f} amt={trade.get('amount')}"
            )
            send_telegram(msg)
            break  # one trade per cycle (max 1 open position)
        except EntryDeviationError as e:
            log.warning("[%s] Skip entry (price moved): %s", label, e)
            send_telegram(f"⚠️ [{label}] Missed {side} (price moved)\n{e}")
            result["action"] = "skip_deviation"
            result["error"] = str(e)
            missed_set.add(key)
            break
        except Exception as e:
            log.exception("[%s] Trade failed", label)
            send_telegram(f"🔴 [{label}] Trade failed {side}\n{e}")
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
        notify_missed(leftover, reason, cfg)
        for s in leftover:
            missed_set.add(signal_key(s))

    state["executed"] = sorted(executed_set)[-500:]
    state["missed"] = sorted(missed_set)[-500:]
    state["last_bar"] = bar_ts.isoformat()
    save_state(state, cfg)
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


def sleep_until_next_15m_with_sweep(
    exchange,
    symbol: str,
    cfg: dict | None = None,
    *,
    dry_run: bool = False,
    poll_sec: int = 30,
    buffer_sec: int = 5,
) -> None:
    """Wait for next 15m close; while waiting, cancel leftover SL/TP if flat."""
    label = _bot_label(cfg)
    wait = seconds_until_next_15m_close(buffer_sec)
    log.info("Sleep %.0fs until next 15m close (sweep leftovers every %ss)", wait, poll_sec)
    deadline = time.monotonic() + wait
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return
        time.sleep(min(poll_sec, remaining))
        if dry_run or remaining <= poll_sec:
            continue
        try:
            n = sweep_orphan_orders(exchange, symbol)
            if n:
                send_telegram(
                    f"🧹 [{label}] Cancelled {n} leftover order(s) (position flat)"
                )
        except Exception:
            log.exception("[%s] Orphan-order sweep failed", label)
