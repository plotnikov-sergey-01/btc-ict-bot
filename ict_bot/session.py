from __future__ import annotations

from datetime import time

import pandas as pd


def parse_hhmm(value: str) -> time:
    h, m = value.split(":")
    return time(int(h), int(m))


def _in_window(t: time, start: time, end: time) -> bool:
    if start <= end:
        return start <= t <= end
    return t >= start or t <= end


def in_trading_session(ts: pd.Timestamp, session_cfg: dict) -> bool:
    """Check if timestamp falls in any configured session window."""
    if not session_cfg.get("enabled", True):
        return True

    ts = ts.tz_convert("UTC") if ts.tzinfo else ts.tz_localize("UTC")

    if session_cfg.get("skip_weekends", True) and ts.weekday() >= 5:
        return False

    windows = session_cfg.get("windows")
    if windows:
        t = ts.time()
        for w in windows:
            start = parse_hhmm(w["start_utc"])
            end = parse_hhmm(w["end_utc"])
            if _in_window(t, start, end):
                return True
        return False

    # Legacy single-window config
    start = parse_hhmm(session_cfg["start_utc"])
    end = parse_hhmm(session_cfg["end_utc"])
    return _in_window(ts.time(), start, end)
