from __future__ import annotations

import pandas as pd

from .structure import detect_structure_signals


def timeframe_bias(df: pd.DataFrame, pivot_left: int, pivot_right: int) -> int:
    signals = detect_structure_signals(df, pivot_left, pivot_right)
    if signals.empty:
        return 0
    return int(signals["structure_bias"].iloc[-1])


def bias_at_time(
    df: pd.DataFrame,
    ts: pd.Timestamp,
    pivot_left: int,
    pivot_right: int,
) -> int:
    subset = df.loc[:ts]
    if len(subset) < pivot_left + pivot_right + 5:
        return 0
    return timeframe_bias(subset, pivot_left, pivot_right)


def map_bias_to_ltf(
    htf_df: pd.DataFrame,
    ltf_index: pd.DatetimeIndex,
    pivot_left: int,
    pivot_right: int,
) -> pd.Series:
    """Forward-map HTF structure bias onto LTF bars (fast, computed once)."""
    signals = detect_structure_signals(htf_df, pivot_left, pivot_right)
    bias = signals["structure_bias"].astype(int)
    mapped = pd.Series(index=ltf_index, dtype="int64")
    for ts in ltf_index:
        mapped.loc[ts] = int(bias.loc[:ts].iloc[-1]) if not bias.loc[:ts].empty else 0
    return mapped


def map_bias_to_ltf_fast(
    htf_df: pd.DataFrame,
    ltf_index: pd.DatetimeIndex,
    pivot_left: int,
    pivot_right: int,
) -> pd.Series:
    """merge_asof version — O(n) instead of per-bar slice."""
    signals = detect_structure_signals(htf_df, pivot_left, pivot_right)
    bias_df = signals[["structure_bias"]].reset_index()
    bias_df.columns = ["timestamp", "bias"]
    ltf_df = pd.DataFrame({"timestamp": ltf_index})
    merged = pd.merge_asof(
        ltf_df.sort_values("timestamp"),
        bias_df.sort_values("timestamp"),
        on="timestamp",
        direction="backward",
    )
    return pd.Series(merged["bias"].fillna(0).astype(int).values, index=ltf_index)


def mtf_aligned(
    daily_bias: int,
    h4_bias: int,
    h1_bias: int,
    trade_direction: int,
    require_daily: bool,
    require_4h: bool,
    allow_1h_neutral: bool,
) -> bool:
    if trade_direction == 0:
        return False

    if require_daily and daily_bias == 0:
        return False
    if require_daily and daily_bias != trade_direction:
        return False

    if require_4h:
        if h4_bias not in (0, trade_direction):
            return False
    # When require_4h is false, 4H bias is ignored (Daily still gates direction)

    if not allow_1h_neutral and h1_bias != trade_direction:
        return False
    if h1_bias not in (0, trade_direction) and h1_bias != 0:
        return False

    return True
