from __future__ import annotations

from typing import Any

import pandas as pd


def _parse_ts(value: str | None) -> pd.Timestamp | None:
    if value is None:
        return None
    ts = pd.Timestamp(value)
    if ts.tz is None:
        ts = ts.tz_localize("UTC")
    else:
        ts = ts.tz_convert("UTC")
    return ts


def resolve_sample_window(cfg: dict, mode: str | None = None) -> tuple[pd.Timestamp | None, pd.Timestamp | None, str]:
    """
    Return (start, end, mode) for backtest evaluation window.
    mode: full | in_sample | out_of_sample
    """
    bt = cfg.get("backtest", {})
    sample = bt.get("sample", {})
    active = mode or sample.get("mode", "full")

    if active == "full":
        return None, None, "full"

    if active not in ("in_sample", "out_of_sample"):
        raise ValueError(f"Unknown sample mode: {active}")

    block = sample.get(active, {})
    start = _parse_ts(block.get("start"))
    end = _parse_ts(block.get("end"))
    return start, end, active


def in_window(ts: pd.Timestamp, start: pd.Timestamp | None, end: pd.Timestamp | None) -> bool:
    ts = ts.tz_convert("UTC") if ts.tzinfo else ts.tz_localize("UTC")
    if start is not None and ts < start:
        return False
    if end is not None and ts > end:
        return False
    return True


def filter_signals_by_window(signals: list[Any], start: pd.Timestamp | None, end: pd.Timestamp | None) -> list[Any]:
    if start is None and end is None:
        return signals
    return [s for s in signals if in_window(s.timestamp, start, end)]


def filter_trades_by_entry_window(trades: list[Any], start: pd.Timestamp | None, end: pd.Timestamp | None) -> list[Any]:
    if start is None and end is None:
        return trades
    kept = []
    for t in trades:
        entry = pd.Timestamp(t.entry_time)
        if in_window(entry, start, end):
            kept.append(t)
    return kept


def sample_label(start: pd.Timestamp | None, end: pd.Timestamp | None, mode: str) -> str:
    if mode == "full":
        return "full"
    s = start.date().isoformat() if start else "start"
    e = end.date().isoformat() if end else "end"
    return f"{mode}_{s}_{e}"
