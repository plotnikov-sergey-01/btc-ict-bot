from __future__ import annotations

import pandas as pd

from .structure import atr


def find_fvgs(df: pd.DataFrame, min_gap: float) -> pd.DataFrame:
    """
    Detect fair value gaps (3-candle imbalance).
    Bullish FVG: candle[i-2].high < candle[i].low
    Bearish FVG: candle[i-2].low > candle[i].high
    """
    records = []
    for i in range(2, len(df)):
        c0 = df.iloc[i - 2]
        c2 = df.iloc[i]
        ts = df.index[i]

        # Bullish FVG
        if c0["high"] < c2["low"]:
            gap_low = c0["high"]
            gap_high = c2["low"]
            gap_size = gap_high - gap_low
            if gap_size >= min_gap:
                records.append(
                    {
                        "timestamp": ts,
                        "direction": 1,
                        "gap_low": gap_low,
                        "gap_high": gap_high,
                        "ce": (gap_low + gap_high) / 2,
                        "gap_size": gap_size,
                    }
                )

        # Bearish FVG
        if c0["low"] > c2["high"]:
            gap_high = c0["low"]
            gap_low = c2["high"]
            gap_size = gap_high - gap_low
            if gap_size >= min_gap:
                records.append(
                    {
                        "timestamp": ts,
                        "direction": -1,
                        "gap_low": gap_low,
                        "gap_high": gap_high,
                        "ce": (gap_low + gap_high) / 2,
                        "gap_size": gap_size,
                    }
                )

    if not records:
        return pd.DataFrame(
            columns=["timestamp", "direction", "gap_low", "gap_high", "ce", "gap_size"]
        )
    return pd.DataFrame(records).set_index("timestamp")


def is_fvg_unmitigated(
    df: pd.DataFrame,
    fvg: pd.Series,
    at_ts: pd.Timestamp,
) -> bool:
    """FVG is valid if price hasn't fully closed through it before entry."""
    formed_at = fvg.name
    window = df.loc[formed_at:at_ts]
    if window.empty:
        return True

    direction = int(fvg["direction"])
    gap_low, gap_high = fvg["gap_low"], fvg["gap_high"]

    if direction == 1:
        # mitigated if any candle closes below gap_low
        return not (window["close"] < gap_low).any()
    # bearish: mitigated if close above gap_high
    return not (window["close"] > gap_high).any()


def active_fvg_at(
    df: pd.DataFrame,
    fvgs: pd.DataFrame,
    ts: pd.Timestamp,
    direction: int,
    min_gap: float,
    require_unmitigated: bool,
    after_ts: pd.Timestamp | None = None,
) -> pd.Series | None:
    """Return most recent valid FVG at timestamp for given direction."""
    if fvgs.empty:
        return None

    candidates = fvgs[(fvgs.index <= ts) & (fvgs["direction"] == direction)]
    if after_ts is not None:
        candidates = candidates[candidates.index >= after_ts]

    for fvg_ts, fvg in candidates.iloc[::-1].iterrows():
        if fvg["gap_size"] < min_gap:
            continue
        if require_unmitigated and not is_fvg_unmitigated(df, fvg, ts):
            continue
        return fvg
    return None


def compute_min_gap(df: pd.DataFrame, atr_period: int, atr_mult: float) -> float:
    return float(atr(df, atr_period).iloc[-1] * atr_mult)
