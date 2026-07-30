from __future__ import annotations

"""Entry filters: volatility, displacement, premium/discount, optional Fib OTE."""

import pandas as pd

from .bias import map_bias_to_ltf_fast
from .structure import atr, find_swing_points


def precompute_atr_percentile(
    df: pd.DataFrame,
    atr_period: int,
    lookback_bars: int,
) -> pd.Series:
    atr_s = atr(df, atr_period)

    def _pct(window: pd.Series) -> float:
        if len(window) < 2:
            return 50.0
        ranked = window.rank(pct=True)
        return float(ranked.iloc[-1] * 100)

    return atr_s.rolling(lookback_bars, min_periods=max(20, lookback_bars // 10)).apply(_pct, raw=False)


def map_4h_swings_to_ltf(
    df_4h: pd.DataFrame,
    ltf_index: pd.DatetimeIndex,
    pivot_left: int,
    pivot_right: int,
) -> tuple[pd.Series, pd.Series]:
    swing_high, swing_low = find_swing_points(df_4h, pivot_left, pivot_right)
    last_sh = df_4h["high"].where(swing_high).ffill()
    last_sl = df_4h["low"].where(swing_low).ffill()

    sh_df = last_sh.reset_index()
    sh_df.columns = ["timestamp", "swing_high"]
    sl_df = last_sl.reset_index()
    sl_df.columns = ["timestamp", "swing_low"]
    ltf_df = pd.DataFrame({"timestamp": ltf_index})

    msh = pd.merge_asof(
        ltf_df.sort_values("timestamp"),
        sh_df.sort_values("timestamp"),
        on="timestamp",
        direction="backward",
    )
    msl = pd.merge_asof(
        ltf_df.sort_values("timestamp"),
        sl_df.sort_values("timestamp"),
        on="timestamp",
        direction="backward",
    )
    return (
        pd.Series(msh["swing_high"].values, index=ltf_index),
        pd.Series(msl["swing_low"].values, index=ltf_index),
    )


def displacement_ok(
    df_ltf: pd.DataFrame,
    event_ts: pd.Timestamp,
    atr_period: int,
    min_body_atr_mult: float,
) -> bool:
    if event_ts not in df_ltf.index:
        return False
    row = df_ltf.loc[event_ts]
    body = abs(float(row["close"]) - float(row["open"]))
    atr_val = float(atr(df_ltf.loc[:event_ts], atr_period).iloc[-1])
    return body >= atr_val * min_body_atr_mult


def premium_discount_ok(
    direction: int,
    entry: float,
    range_high: float,
    range_low: float,
) -> bool:
    if pd.isna(range_high) or pd.isna(range_low) or range_high <= range_low:
        return True
    eq = (range_high + range_low) / 2
    if direction == 1:
        return entry <= eq
    return entry >= eq


def fib_ote_ok(
    direction: int,
    entry: float,
    range_high: float,
    range_low: float,
    min_retrace: float,
    max_retrace: float,
) -> bool:
    """OTE: entry in 62–79% retrace of last 4H dealing range (simplified)."""
    if pd.isna(range_high) or pd.isna(range_low) or range_high <= range_low:
        return True
    span = range_high - range_low
    if direction == 1:
        low_band = range_low + span * (1 - max_retrace)
        high_band = range_low + span * (1 - min_retrace)
        return low_band <= entry <= high_band
    low_band = range_low + span * min_retrace
    high_band = range_low + span * max_retrace
    return low_band <= entry <= high_band


def entry_filters_ok(
    cfg: dict,
    df_ltf: pd.DataFrame,
    i: int,
    ts: pd.Timestamp,
    direction: int,
    entry: float,
    event_ts: pd.Timestamp,
    atr_pct: pd.Series,
    h4_sh: pd.Series,
    h4_sl: pd.Series,
) -> bool:
    flt = cfg.get("filters", {})
    fvg_cfg = cfg["fvg"]

    vol = flt.get("volatility", {})
    if vol.get("enabled", False):
        lookback_days = int(vol.get("lookback_days", 30))
        bars_per_day = 96  # 15m
        lookback_bars = lookback_days * bars_per_day
        if i >= len(atr_pct) or pd.isna(atr_pct.iloc[i]):
            return False
        if float(atr_pct.iloc[i]) < float(vol.get("min_percentile", 20)):
            return False

    disp = flt.get("displacement", {})
    if disp.get("enabled", False):
        if not displacement_ok(
            df_ltf,
            event_ts,
            fvg_cfg["atr_period"],
            float(disp.get("min_body_atr_mult", 1.5)),
        ):
            return False

    rh = float(h4_sh.iloc[i])
    rl = float(h4_sl.iloc[i])

    pd_cfg = flt.get("premium_discount", {})
    if pd_cfg.get("enabled", False):
        if not premium_discount_ok(direction, entry, rh, rl):
            return False

    fib = flt.get("fibonacci_ote", {})
    if fib.get("enabled", False):
        if not fib_ote_ok(
            direction,
            entry,
            rh,
            rl,
            float(fib.get("min_retrace", 0.62)),
            float(fib.get("max_retrace", 0.786)),
        ):
            return False

    return True
