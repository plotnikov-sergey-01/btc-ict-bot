from __future__ import annotations

"""Trailing stop and take-profit extension helpers for backtest simulation."""

import pandas as pd

from .liquidity import liquidity_targets, nearest_liquidity_tp
from .structure import atr, find_swing_points


def precompute_swing_levels(
    df_ltf: pd.DataFrame,
    pivot_left: int,
    pivot_right: int,
) -> tuple[pd.Series, pd.Series]:
    swing_high, swing_low = find_swing_points(df_ltf, pivot_left, pivot_right)
    last_sh = df_ltf["high"].where(swing_high).ffill()
    last_sl = df_ltf["low"].where(swing_low).ffill()
    return last_sh, last_sl


def swing_trail_stop(
    direction: int,
    i: int,
    df_ltf: pd.DataFrame,
    last_sh: pd.Series,
    last_sl: pd.Series,
    cfg: dict,
) -> float | None:
    struct = cfg["structure"]
    risk = cfg["risk"]
    fvg = cfg["fvg"]

    atr_val = float(atr(df_ltf.iloc[: i + 1], fvg["atr_period"]).iloc[-1])
    close = float(df_ltf.iloc[i]["close"])
    buffer = max(atr_val * risk["sl_buffer_atr_mult"], close * risk["sl_buffer_pct"])

    if direction == 1:
        swing = last_sl.iloc[i]
        if pd.isna(swing):
            return None
        return float(swing) - buffer

    swing = last_sh.iloc[i]
    if pd.isna(swing):
        return None
    return float(swing) + buffer


def maybe_extend_take_profit(
    direction: int,
    entry: float,
    stop: float,
    current_tp: float,
    df_ltf: pd.DataFrame,
    ts: pd.Timestamp,
    cfg: dict,
) -> float:
    tm = cfg["trade_management"]
    if not tm.get("extend_tp_on_trail", False):
        return current_tp

    struct = cfg["structure"]
    risk = cfg["risk"]
    liq = cfg["liquidity"]

    if not liq.get("enabled", True):
        return current_tp

    targets = liquidity_targets(
        df_ltf,
        ts,
        direction,
        entry,
        struct["pivot_left"],
        struct["pivot_right"],
        liq["lookback_bars"],
        liq["round_number_step"],
        liq["equal_level_tolerance_pct"],
    )

    if direction == 1:
        beyond = [t for t in targets if t > current_tp]
    else:
        beyond = [t for t in targets if t < current_tp]

    if not beyond:
        return current_tp

    extended = nearest_liquidity_tp(beyond, entry, stop, direction, risk["min_rr"])
    if extended is None:
        return current_tp
    return extended


def apply_trailing(
    ot: dict,
    row: pd.Series,
    i: int,
    ts: pd.Timestamp,
    df_ltf: pd.DataFrame,
    last_sh: pd.Series,
    last_sl: pd.Series,
    cfg: dict,
) -> None:
    tm = cfg["trade_management"]
    if not tm.get("trailing_stop", False):
        return
    if tm.get("trail_by", "swing") != "swing":
        return

    direction = ot["direction"]
    entry = ot["entry"]
    risk = abs(entry - ot["initial_stop"])
    if risk <= 0:
        return

    activate_rr = tm.get("trail_activate_at_rr", 1.0)
    if direction == 1:
        reached = row["high"] >= entry + risk * activate_rr
    else:
        reached = row["low"] <= entry - risk * activate_rr

    if not ot.get("trail_active") and not reached:
        return

    ot["trail_active"] = True

    new_stop = swing_trail_stop(direction, i, df_ltf, last_sh, last_sl, cfg)
    if new_stop is not None:
        if direction == 1 and new_stop > ot["stop"]:
            ot["stop"] = new_stop
        elif direction == -1 and new_stop < ot["stop"]:
            ot["stop"] = new_stop

    ot["take_profit"] = maybe_extend_take_profit(
        direction,
        entry,
        ot["stop"],
        ot["take_profit"],
        df_ltf,
        ts,
        cfg,
    )
