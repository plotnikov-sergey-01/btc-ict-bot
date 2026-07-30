from __future__ import annotations

import numpy as np
import pandas as pd


def atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    high, low, close = df["high"], df["low"], df["close"]
    prev_close = close.shift(1)
    tr = pd.concat(
        [high - low, (high - prev_close).abs(), (low - prev_close).abs()],
        axis=1,
    ).max(axis=1)
    return tr.ewm(alpha=1 / period, adjust=False).mean()


def find_swing_points(
    df: pd.DataFrame,
    left: int = 3,
    right: int = 3,
) -> tuple[pd.Series, pd.Series]:
    """Return boolean series for swing highs and swing lows (pivot points)."""
    highs = df["high"].values
    lows = df["low"].values
    n = len(df)
    swing_high = np.zeros(n, dtype=bool)
    swing_low = np.zeros(n, dtype=bool)

    for i in range(left, n - right):
        window_h = highs[i - left : i + right + 1]
        window_l = lows[i - left : i + right + 1]
        if highs[i] == window_h.max() and np.sum(window_h == highs[i]) == 1:
            swing_high[i] = True
        if lows[i] == window_l.min() and np.sum(window_l == lows[i]) == 1:
            swing_low[i] = True

    idx = df.index
    return pd.Series(swing_high, index=idx), pd.Series(swing_low, index=idx)


def detect_structure_signals(
    df: pd.DataFrame,
    pivot_left: int = 3,
    pivot_right: int = 3,
) -> pd.DataFrame:
    """
    Detect BOS and CHoCH on each bar.
    Returns columns: bos_bull, bos_bear, choch_bull, choch_bear, structure_bias
    """
    swing_high, swing_low = find_swing_points(df, pivot_left, pivot_right)

    last_sh = np.nan
    last_sl = np.nan
    prev_sh = np.nan
    prev_sl = np.nan
    trend = 0  # 1 bull, -1 bear, 0 unknown

    bos_bull = []
    bos_bear = []
    choch_bull = []
    choch_bear = []
    bias = []

    for i, (ts, row) in enumerate(df.iterrows()):
        if swing_high.iloc[i]:
            prev_sh = last_sh
            last_sh = row["high"]
        if swing_low.iloc[i]:
            prev_sl = last_sl
            last_sl = row["low"]

        close = row["close"]
        b_bull = b_bear = c_bull = c_bear = False

        if not np.isnan(last_sh) and close > last_sh:
            if trend == 1:
                b_bull = True
            else:
                c_bull = True
            trend = 1

        if not np.isnan(last_sl) and close < last_sl:
            if trend == -1:
                b_bear = True
            else:
                c_bear = True
            trend = -1

        bos_bull.append(b_bull)
        bos_bear.append(b_bear)
        choch_bull.append(c_bull)
        choch_bear.append(c_bear)
        bias.append(trend)

    return pd.DataFrame(
        {
            "bos_bull": bos_bull,
            "bos_bear": bos_bear,
            "choch_bull": choch_bull,
            "choch_bear": choch_bear,
            "structure_bias": bias,
        },
        index=df.index,
    )


def recent_structure_signal(
    signals: pd.DataFrame,
    ts: pd.Timestamp,
    direction: int,
    lookback_bars: int,
) -> bool:
    """True if CHoCH or BOS in given direction within lookback on LTF."""
    subset = signals.loc[:ts].tail(lookback_bars)
    if subset.empty:
        return False
    if direction == 1:
        return bool(subset["bos_bull"].any() or subset["choch_bull"].any())
    if direction == -1:
        return bool(subset["bos_bear"].any() or subset["choch_bear"].any())
    return False


def last_swing_low_before(df: pd.DataFrame, ts: pd.Timestamp, pivot_left: int, pivot_right: int) -> float | None:
    swing_high, swing_low = find_swing_points(df, pivot_left, pivot_right)
    subset = df.loc[:ts]
    lows = subset.loc[swing_low.reindex(subset.index, fill_value=False)]
    if lows.empty:
        return None
    return float(lows["low"].iloc[-1])


def last_swing_high_before(df: pd.DataFrame, ts: pd.Timestamp, pivot_left: int, pivot_right: int) -> float | None:
    swing_high, swing_low = find_swing_points(df, pivot_left, pivot_right)
    subset = df.loc[:ts]
    highs = subset.loc[swing_high.reindex(subset.index, fill_value=False)]
    if highs.empty:
        return None
    return float(highs["high"].iloc[-1])
