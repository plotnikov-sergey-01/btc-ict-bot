from __future__ import annotations

from dataclasses import dataclass

from .liquidity import liquidity_targets, nearest_liquidity_tp
from .structure import atr


@dataclass
class TradeSetup:
    direction: int
    entry: float
    stop: float
    take_profit: float
    rr: float
    fvg_ce: float
    reason: str


def compute_stop(
    direction: int,
    df_ltf,
    ts,
    pivot_left: int,
    pivot_right: int,
    atr_period: int,
    sl_buffer_atr_mult: float,
    sl_buffer_pct: float,
    last_swing_low: float | None = None,
    last_swing_high: float | None = None,
) -> float | None:
    atr_val = float(atr(df_ltf.loc[:ts], atr_period).iloc[-1])
    buffer = max(atr_val * sl_buffer_atr_mult, df_ltf.loc[ts, "close"] * sl_buffer_pct)

    if direction == 1:
        swing = last_swing_low
        if swing is None:
            return None
        return swing - buffer

    swing = last_swing_high
    if swing is None:
        return None
    return swing + buffer


def build_trade_setup(
    direction: int,
    entry: float,
    df_ltf,
    ts,
    cfg: dict,
    last_swing_low: float | None = None,
    last_swing_high: float | None = None,
) -> TradeSetup | None:
    struct = cfg["structure"]
    risk = cfg["risk"]
    liq = cfg["liquidity"]

    stop = compute_stop(
        direction,
        df_ltf,
        ts,
        struct["pivot_left"],
        struct["pivot_right"],
        cfg["fvg"]["atr_period"],
        risk["sl_buffer_atr_mult"],
        risk["sl_buffer_pct"],
        last_swing_low=last_swing_low,
        last_swing_high=last_swing_high,
    )
    if stop is None:
        return None

    risk_dist = abs(entry - stop)
    if risk_dist <= 0:
        return None

    targets = []
    if liq.get("enabled", True):
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

    min_rr = risk["min_rr"]
    if direction == 1:
        fallback_tp = entry + risk_dist * min_rr
    else:
        fallback_tp = entry - risk_dist * min_rr

    tp = nearest_liquidity_tp(targets, entry, stop, direction, min_rr)
    if tp is None:
        tp = fallback_tp

    reward = abs(tp - entry)
    rr = reward / risk_dist
    if rr < min_rr:
        return None

    return TradeSetup(
        direction=direction,
        entry=entry,
        stop=stop,
        take_profit=tp,
        rr=rr,
        fvg_ce=entry,
        reason="fvg_ce_entry",
    )
