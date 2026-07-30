from __future__ import annotations

import pandas as pd


def funding_at_time(funding_df: pd.DataFrame | None, ts: pd.Timestamp) -> float | None:
    if funding_df is None or funding_df.empty:
        return None
    subset = funding_df.loc[:ts]
    if subset.empty:
        return None
    return float(subset["funding_rate"].iloc[-1])


def funding_allows_trade(
    rate: float | None,
    direction: int,
    extreme_positive: float,
    extreme_negative: float,
    block_on_extreme: bool,
) -> tuple[bool, str]:
    """
    Returns (allowed, reason).
    Extreme positive funding = crowded longs; negative = crowded shorts.
    """
    if rate is None:
        return True, "no_funding_data"

    if direction == 1 and rate >= extreme_positive:
        if block_on_extreme:
            return False, f"funding_extreme_long_{rate:.6f}"
        return True, f"funding_warning_long_{rate:.6f}"

    if direction == -1 and rate <= extreme_negative:
        if block_on_extreme:
            return False, f"funding_extreme_short_{rate:.6f}"
        return True, f"funding_warning_short_{rate:.6f}"

    return True, "ok"
