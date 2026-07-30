from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from .bias import map_bias_to_ltf_fast, mtf_aligned
from .filters import entry_filters_ok, map_4h_swings_to_ltf, precompute_atr_percentile
from .funding import funding_allows_trade, funding_at_time
from .fvg import find_fvgs
from .risk import build_trade_setup
from .session import in_trading_session
from .structure import atr, detect_structure_signals, find_swing_points


@dataclass
class Signal:
    timestamp: pd.Timestamp
    direction: int
    entry: float
    stop: float
    take_profit: float
    rr: float
    meta: dict = field(default_factory=dict)


@dataclass
class PendingLimit:
    direction: int
    entry: float
    formed_idx: int
    expire_idx: int
    event_ts: pd.Timestamp
    fvg_formed: pd.Timestamp
    gap_size: float
    gap_low: float
    gap_high: float


def _resolve_fill_price(
    pending: PendingLimit,
    row: pd.Series,
    entry_at_ce: bool,
) -> float | None:
    """Return fill price if this bar triggers the pending limit, else None."""
    low, high = float(row["low"]), float(row["high"])
    ce = float(pending.entry)
    gap_low, gap_high = pending.gap_low, pending.gap_high

    if entry_at_ce:
        if low <= ce <= high:
            return ce
        return None

    # Touch any part of FVG
    if high < gap_low or low > gap_high:
        return None
    if low <= ce <= high:
        return ce
    if pending.direction == 1:
        return float(min(gap_high, high))
    return float(max(gap_low, low))


def _rolling_structure_flag(signals: pd.DataFrame, direction: int, lookback: int) -> pd.Series:
    if direction == 1:
        raw = signals["bos_bull"] | signals["choch_bull"]
    else:
        raw = signals["bos_bear"] | signals["choch_bear"]
    return raw.rolling(lookback, min_periods=1).max().astype(bool)


def _last_event_ts_series(signals: pd.DataFrame, direction: int) -> pd.Series:
    if direction == 1:
        mask = signals["bos_bull"] | signals["choch_bull"]
    else:
        mask = signals["bos_bear"] | signals["choch_bear"]
    ts = pd.Series(np.where(mask, signals.index, pd.NaT), index=signals.index)
    return ts.ffill()


def _precompute_last_swings(df: pd.DataFrame, pivot_left: int, pivot_right: int) -> tuple[pd.Series, pd.Series]:
    swing_high, swing_low = find_swing_points(df, pivot_left, pivot_right)
    last_sh = df["high"].where(swing_high).ffill()
    last_sl = df["low"].where(swing_low).ffill()
    return last_sh, last_sl


def _fvg_still_valid(
    df_ltf: pd.DataFrame,
    direction: int,
    gap_low: float,
    gap_high: float,
    fvg_formed: pd.Timestamp,
    ts: pd.Timestamp,
    require_unmitigated: bool,
) -> bool:
    if not require_unmitigated:
        return True
    window = df_ltf.loc[fvg_formed:ts]
    if direction == 1:
        return not (window["close"] < gap_low).any()
    return not (window["close"] > gap_high).any()


def _find_fvg_on_bar(
    fvg_pool: pd.DataFrame,
    event_ts: pd.Timestamp,
    ts: pd.Timestamp,
    min_gap: float,
    df_ltf: pd.DataFrame,
    direction: int,
    require_unmitigated: bool,
) -> pd.Series | None:
    """Return FVG that formed exactly on bar `ts`."""
    if fvg_pool.empty or fvg_pool.index.max() < ts:
        return None

    if ts not in fvg_pool.index:
        return None

    cand = fvg_pool.loc[ts]
    if isinstance(cand, pd.DataFrame):
        cand = cand.iloc[-1]

    if cand["gap_size"] < min_gap:
        return None
    if ts < event_ts:
        return None
    if not _fvg_still_valid(df_ltf, direction, cand["gap_low"], cand["gap_high"], ts, ts, require_unmitigated):
        return None
    return cand


def _try_emit_signal(
    *,
    i: int,
    ts: pd.Timestamp,
    direction: int,
    entry: float,
    pending: PendingLimit,
    df_ltf: pd.DataFrame,
    daily_b: int,
    h4_b: int,
    h1_b: int,
    last_sh: pd.Series,
    last_sl: pd.Series,
    funding_df: pd.DataFrame | None,
    funding_cfg: dict,
    cfg: dict,
    seen_days: dict[str, int],
    max_per_day: int,
) -> Signal | None:
    day_key = ts.strftime("%Y-%m-%d")
    if seen_days.get(day_key, 0) >= max_per_day:
        return None

    ctx_df = df_ltf.iloc[: i + 1]
    setup = build_trade_setup(
        direction,
        entry,
        ctx_df,
        ts,
        cfg,
        last_swing_low=float(last_sl.iloc[i]) if direction == 1 else None,
        last_swing_high=float(last_sh.iloc[i]) if direction == -1 else None,
    )
    if setup is None:
        return None

    if funding_cfg.get("enabled", False):
        rate = funding_at_time(funding_df, ts)
        allowed, freason = funding_allows_trade(
            rate,
            direction,
            funding_cfg["extreme_positive"],
            funding_cfg["extreme_negative"],
            funding_cfg.get("block_on_extreme", False),
        )
        if not allowed:
            return None
    else:
        freason = "disabled"

    seen_days[day_key] = seen_days.get(day_key, 0) + 1
    return Signal(
        timestamp=ts,
        direction=direction,
        entry=setup.entry,
        stop=setup.stop,
        take_profit=setup.take_profit,
        rr=setup.rr,
        meta={
            "daily_bias": daily_b,
            "h4_bias": h4_b,
            "h1_bias": h1_b,
            "funding": freason,
            "fvg_size": pending.gap_size,
            "limit_fill": True,
            "fvg_formed": pending.fvg_formed.isoformat(),
        },
    )


def generate_signals(
    df_ltf: pd.DataFrame,
    df_1h: pd.DataFrame,
    df_4h: pd.DataFrame,
    df_1d: pd.DataFrame,
    funding_df: pd.DataFrame | None,
    cfg: dict,
) -> list[Signal]:
    struct_cfg = cfg["structure"]
    fvg_cfg = cfg["fvg"]
    session_cfg = cfg["session"]
    funding_cfg = cfg["funding"]
    mtf_cfg = cfg["mtf"]
    limit_valid_bars = int(fvg_cfg.get("limit_valid_bars", 1))
    entry_at_ce = bool(fvg_cfg.get("entry_at_ce", True))

    pivot_left = struct_cfg["pivot_left"]
    pivot_right = struct_cfg["pivot_right"]
    lookback = struct_cfg["signal_lookback_bars"]

    atr_series = atr(df_ltf, fvg_cfg["atr_period"])
    min_gaps = atr_series * fvg_cfg["min_size_atr_mult"]

    vol_cfg = cfg.get("filters", {}).get("volatility", {})
    lookback_days = int(vol_cfg.get("lookback_days", 30))
    atr_pct = precompute_atr_percentile(
        df_ltf,
        fvg_cfg["atr_period"],
        lookback_days * 96,
    )
    h4_sh, h4_sl = map_4h_swings_to_ltf(df_4h, df_ltf.index, pivot_left, pivot_right)

    ltf_signals = detect_structure_signals(df_ltf, pivot_left, pivot_right)
    daily_bias_s = map_bias_to_ltf_fast(df_1d, df_ltf.index, pivot_left, pivot_right)
    h4_bias_s = map_bias_to_ltf_fast(df_4h, df_ltf.index, pivot_left, pivot_right)
    h1_bias_s = map_bias_to_ltf_fast(df_1h, df_ltf.index, pivot_left, pivot_right)

    bull_struct = _rolling_structure_flag(ltf_signals, 1, lookback)
    bear_struct = _rolling_structure_flag(ltf_signals, -1, lookback)
    bull_event_ts = _last_event_ts_series(ltf_signals, 1)
    bear_event_ts = _last_event_ts_series(ltf_signals, -1)

    last_sh, last_sl = _precompute_last_swings(df_ltf, pivot_left, pivot_right)

    fvgs = find_fvgs(df_ltf, min_gap=0)
    fvg_bull = fvgs[fvgs["direction"] == 1] if not fvgs.empty else fvgs
    fvg_bear = fvgs[fvgs["direction"] == -1] if not fvgs.empty else fvgs

    signals: list[Signal] = []
    seen_days: dict[str, int] = {}
    pendings: list[PendingLimit] = []
    warmup = max(50, pivot_left + pivot_right + 20)
    max_per_day = cfg["trade_management"].get("max_trades_per_day", 99)

    for i in range(warmup, len(df_ltf)):
        ts = df_ltf.index[i]
        row = df_ltf.iloc[i]
        in_session = in_trading_session(ts, session_cfg)

        daily_b = int(daily_bias_s.iloc[i])
        h4_b = int(h4_bias_s.iloc[i])
        h1_b = int(h1_bias_s.iloc[i])
        min_gap = float(min_gaps.iloc[i])

        # 1) Try to fill active limit orders
        if in_session:
            still_active: list[PendingLimit] = []
            for pending in pendings:
                if i > pending.expire_idx:
                    continue

                fill_price = _resolve_fill_price(pending, row, entry_at_ce)
                if fill_price is None:
                    still_active.append(pending)
                    continue

                if not mtf_aligned(
                    daily_b,
                    h4_b,
                    h1_b,
                    pending.direction,
                    mtf_cfg["require_daily"],
                    mtf_cfg["require_4h"],
                    mtf_cfg["allow_1h_neutral"],
                ):
                    still_active.append(pending)
                    continue

                if not _fvg_still_valid(
                    df_ltf,
                    pending.direction,
                    pending.gap_low,
                    pending.gap_high,
                    pending.fvg_formed,
                    ts,
                    fvg_cfg["require_unmitigated"],
                ):
                    continue

                if not entry_filters_ok(
                    cfg,
                    df_ltf,
                    i,
                    ts,
                    pending.direction,
                    fill_price,
                    pending.event_ts,
                    atr_pct,
                    h4_sh,
                    h4_sl,
                ):
                    still_active.append(pending)
                    continue

                sig = _try_emit_signal(
                    i=i,
                    ts=ts,
                    direction=pending.direction,
                    entry=fill_price,
                    pending=pending,
                    df_ltf=df_ltf,
                    daily_b=daily_b,
                    h4_b=h4_b,
                    h1_b=h1_b,
                    last_sh=last_sh,
                    last_sl=last_sl,
                    funding_df=funding_df,
                    funding_cfg=funding_cfg,
                    cfg=cfg,
                    seen_days=seen_days,
                    max_per_day=max_per_day,
                )
                if sig is not None:
                    signals.append(sig)
                # Filled or invalidated by R:R — do not keep pending
            pendings = still_active

        if not in_session:
            pendings = [p for p in pendings if i <= p.expire_idx]
            continue

        day_key = ts.strftime("%Y-%m-%d")
        if seen_days.get(day_key, 0) >= max_per_day:
            continue

        # 2) Register new limit orders for FVG formed on this bar
        for direction, struct_ok, event_ts_s, fvg_pool in (
            (1, bull_struct.iloc[i], bull_event_ts.iloc[i], fvg_bull),
            (-1, bear_struct.iloc[i], bear_event_ts.iloc[i], fvg_bear),
        ):
            if not struct_ok:
                continue
            if not mtf_aligned(
                daily_b,
                h4_b,
                h1_b,
                direction,
                mtf_cfg["require_daily"],
                mtf_cfg["require_4h"],
                mtf_cfg["allow_1h_neutral"],
            ):
                continue

            event_ts = pd.Timestamp(event_ts_s) if pd.notna(event_ts_s) else None
            if event_ts is None:
                continue

            fvg = _find_fvg_on_bar(
                fvg_pool,
                event_ts,
                ts,
                min_gap,
                df_ltf,
                direction,
                fvg_cfg["require_unmitigated"],
            )
            if fvg is None:
                continue

            if funding_cfg.get("enabled", False) and funding_cfg.get("block_on_extreme", False):
                rate = funding_at_time(funding_df, ts)
                allowed, _ = funding_allows_trade(
                    rate,
                    direction,
                    funding_cfg["extreme_positive"],
                    funding_cfg["extreme_negative"],
                    True,
                )
                if not allowed:
                    continue

            entry = float(fvg["ce"])

            if not entry_filters_ok(
                cfg,
                df_ltf,
                i,
                ts,
                direction,
                entry,
                event_ts,
                atr_pct,
                h4_sh,
                h4_sl,
            ):
                continue

            pendings = [p for p in pendings if p.direction != direction]
            new_pending = PendingLimit(
                direction=direction,
                entry=entry,
                formed_idx=i,
                expire_idx=i + limit_valid_bars - 1,
                event_ts=event_ts,
                fvg_formed=ts,
                gap_size=float(fvg["gap_size"]),
                gap_low=float(fvg["gap_low"]),
                gap_high=float(fvg["gap_high"]),
            )
            pendings.append(new_pending)

            # Same-bar fill
            fill_price = _resolve_fill_price(new_pending, row, entry_at_ce)
            if fill_price is not None:
                sig = _try_emit_signal(
                    i=i,
                    ts=ts,
                    direction=direction,
                    entry=fill_price,
                    pending=new_pending,
                    df_ltf=df_ltf,
                    daily_b=daily_b,
                    h4_b=h4_b,
                    h1_b=h1_b,
                    last_sh=last_sh,
                    last_sl=last_sl,
                    funding_df=funding_df,
                    funding_cfg=funding_cfg,
                    cfg=cfg,
                    seen_days=seen_days,
                    max_per_day=max_per_day,
                )
                if sig is not None:
                    signals.append(sig)
                    pendings = [p for p in pendings if p is not new_pending]

    return signals
