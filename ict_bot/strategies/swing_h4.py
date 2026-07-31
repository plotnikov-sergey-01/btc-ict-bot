"""Swing + 4H/1H (no daily) mini-strategy — research signals."""

from __future__ import annotations

import numpy as np
import pandas as pd

from ..bias import map_bias_to_ltf_fast
from ..session import in_trading_session
from ..strategy import Signal
from ..structure import atr, find_swing_points

PIVOT_L, PIVOT_R = 3, 3
RETEST_BARS = 16
RETEST_FRAC = 0.5


def h4_1h_aligned(h4: int, h1: int, direction: int) -> bool:
    if h4 not in (0, direction):
        return False
    if h1 not in (0, direction):
        return False
    if h4 == 0 and h1 == 0:
        return False
    return True


def _tp_at_rr(entry: float, stop: float, direction: int, min_rr: float) -> float:
    risk = abs(entry - stop)
    return entry + direction * risk * min_rr


def generate_swing_h4_signals(
    df_ltf: pd.DataFrame,
    df_1h: pd.DataFrame,
    df_4h: pd.DataFrame,
    cfg: dict,
    *,
    entry_mode: str = "confirm",  # confirm | retest
) -> list[Signal]:
    """
    entry_mode=confirm: enter on swing confirmation close if 4H+1H aligned.
    entry_mode=retest: after confirm, pullback into 50% impulse within 16 bars.
    Daily bias is ignored. TP = min_rr * risk (fixed).
    """
    if entry_mode not in ("confirm", "retest"):
        raise ValueError(entry_mode)

    risk_cfg = cfg["risk"]
    fvg_cfg = cfg["fvg"]
    session_cfg = cfg["session"]
    min_rr = float(risk_cfg["min_rr"])
    sl_atr = float(risk_cfg["sl_buffer_atr_mult"])
    sl_pct = float(risk_cfg["sl_buffer_pct"])
    max_per_day = int(cfg["trade_management"].get("max_trades_per_day", 99))

    sh, sl = find_swing_points(df_ltf, PIVOT_L, PIVOT_R)
    atr_s = atr(df_ltf, int(fvg_cfg["atr_period"]))
    h4_b = map_bias_to_ltf_fast(df_4h, df_ltf.index, PIVOT_L, PIVOT_R)
    h1_b = map_bias_to_ltf_fast(df_1h, df_ltf.index, PIVOT_L, PIVOT_R)

    high = df_ltf["high"].values
    low = df_ltf["low"].values
    close = df_ltf["close"].values
    atr_v = atr_s.values
    idx = df_ltf.index
    n = len(df_ltf)

    signals: list[Signal] = []
    seen_days: dict[str, int] = {}

    def try_emit(entry_i: int, direction: int, entry: float, stop: float, meta: dict) -> None:
        ts = idx[entry_i]
        if session_cfg.get("enabled", False) and not in_trading_session(ts, session_cfg):
            return
        day_key = ts.strftime("%Y-%m-%d")
        if seen_days.get(day_key, 0) >= max_per_day:
            return
        if abs(entry - stop) <= 0 or abs(entry - stop) / entry > 0.05:
            return
        if not h4_1h_aligned(int(h4_b.iloc[entry_i]), int(h1_b.iloc[entry_i]), direction):
            return
        tp = _tp_at_rr(entry, stop, direction, min_rr)
        seen_days[day_key] = seen_days.get(day_key, 0) + 1
        signals.append(
            Signal(
                timestamp=ts,
                direction=direction,
                entry=entry,
                stop=stop,
                take_profit=tp,
                rr=min_rr,
                meta=meta,
            )
        )

    for i in range(PIVOT_L, n - PIVOT_R - 2):
        confirm_i = i + PIVOT_R
        for direction, is_swing, swing_price in (
            (1, bool(sl.iloc[i]), float(df_ltf["low"].iloc[i])),
            (-1, bool(sh.iloc[i]), float(df_ltf["high"].iloc[i])),
        ):
            if not is_swing:
                continue

            if entry_mode == "confirm":
                entry = float(close[confirm_i])
                a = float(atr_v[confirm_i]) if not np.isnan(atr_v[confirm_i]) else entry * 0.01
                buf = max(a * sl_atr, entry * sl_pct)
                stop = swing_price - buf if direction == 1 else swing_price + buf
                if direction == 1 and entry <= stop:
                    continue
                if direction == -1 and entry >= stop:
                    continue
                try_emit(
                    confirm_i,
                    direction,
                    entry,
                    stop,
                    {"mode": "confirm", "swing": swing_price, "strategy": "swing_h4"},
                )
                continue

            # retest
            if direction == 1:
                impulse_ext = float(np.max(high[i : confirm_i + 1]))
                rng = impulse_ext - swing_price
                if rng <= 0:
                    continue
                zone = swing_price + RETEST_FRAC * rng
            else:
                impulse_ext = float(np.min(low[i : confirm_i + 1]))
                rng = swing_price - impulse_ext
                if rng <= 0:
                    continue
                zone = swing_price - RETEST_FRAC * rng

            filled = False
            for j in range(confirm_i + 1, min(n, confirm_i + 1 + RETEST_BARS)):
                a = float(atr_v[j]) if not np.isnan(atr_v[j]) else close[j] * 0.01
                buf = max(a * sl_atr, close[j] * sl_pct)
                if direction == 1:
                    stop = swing_price - buf
                    if low[j] <= stop:
                        break
                    if low[j] <= zone and close[j] > zone:
                        entry = float(close[j])
                        if entry > stop:
                            try_emit(
                                j,
                                direction,
                                entry,
                                stop,
                                {
                                    "mode": "retest",
                                    "swing": swing_price,
                                    "strategy": "swing_h4",
                                    "bars_delay": j - confirm_i,
                                },
                            )
                            filled = True
                        break
                else:
                    stop = swing_price + buf
                    if high[j] >= stop:
                        break
                    if high[j] >= zone and close[j] < zone:
                        entry = float(close[j])
                        if entry < stop:
                            try_emit(
                                j,
                                direction,
                                entry,
                                stop,
                                {
                                    "mode": "retest",
                                    "swing": swing_price,
                                    "strategy": "swing_h4",
                                    "bars_delay": j - confirm_i,
                                },
                            )
                            filled = True
                        break
                if filled:
                    break

    signals.sort(key=lambda s: s.timestamp)
    return signals
