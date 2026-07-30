from __future__ import annotations

import numpy as np
import pandas as pd

from .structure import find_swing_points


def round_number_levels(price: float, step: int, count: int = 5) -> list[float]:
    base = round(price / step) * step
    return [base + i * step for i in range(-count, count + 1)]


def equal_levels(prices: np.ndarray, tolerance_pct: float) -> list[float]:
    """Find price clusters (equal highs/lows) within tolerance."""
    if len(prices) == 0:
        return []
    levels = []
    used = np.zeros(len(prices), dtype=bool)
    for i, p in enumerate(prices):
        if used[i]:
            continue
        tol = p * tolerance_pct
        cluster = prices[(prices >= p - tol) & (prices <= p + tol)]
        if len(cluster) >= 2:
            levels.append(float(cluster.mean()))
            for j, pj in enumerate(prices):
                if abs(pj - p) <= tol:
                    used[j] = True
    return levels


def liquidity_targets(
    df: pd.DataFrame,
    ts: pd.Timestamp,
    direction: int,
    entry: float,
    pivot_left: int,
    pivot_right: int,
    lookback_bars: int,
    round_step: int,
    tolerance_pct: float,
) -> list[float]:
    """Collect liquidity levels in trade direction from entry."""
    subset = df.loc[:ts].tail(lookback_bars)
    swing_high, swing_low = find_swing_points(subset, pivot_left, pivot_right)

    sh_prices = subset.loc[swing_high.reindex(subset.index, fill_value=False), "high"].values
    sl_prices = subset.loc[swing_low.reindex(subset.index, fill_value=False), "low"].values

    levels: list[float] = []
    levels.extend(equal_levels(sh_prices, tolerance_pct))
    levels.extend(equal_levels(sl_prices, tolerance_pct))
    levels.extend(round_number_levels(entry, round_step))

    if direction == 1:
        candidates = sorted({lv for lv in levels if lv > entry})
    else:
        candidates = sorted({lv for lv in levels if lv < entry}, reverse=True)

    return candidates


def nearest_liquidity_tp(
    targets: list[float],
    entry: float,
    stop: float,
    direction: int,
    min_rr: float,
) -> float | None:
    """First liquidity target that satisfies minimum R:R."""
    risk = abs(entry - stop)
    if risk <= 0:
        return None

    for tp in targets:
        reward = abs(tp - entry)
        if reward / risk >= min_rr:
            return tp
    return None
