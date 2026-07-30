#!/usr/bin/env python3
"""
Diagnose entry funnel + trade quality on OOS window.

1) Count why potential BOS/CHoCH + FVG setups are rejected
2) Profile winning vs losing trades (gap size, RR, session hour, etc.)
"""

from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ict_bot.bias import map_bias_to_ltf_fast, mtf_aligned
from ict_bot.config import load_config
from ict_bot.funding import funding_allows_trade, funding_at_time
from ict_bot.fvg import find_fvgs
from ict_bot.risk import build_trade_setup
from ict_bot.runner import load_market_data, run_backtest
from ict_bot.sample import filter_signals_by_window, resolve_sample_window
from ict_bot.session import in_trading_session
from ict_bot.structure import atr, detect_structure_signals, find_swing_points


def precompute_swings(df, left, right):
    sh, sl = find_swing_points(df, left, right)
    return df["high"].where(sh).ffill(), df["low"].where(sl).ffill()


def rolling_struct(signals, direction, lookback):
    if direction == 1:
        raw = signals["bos_bull"] | signals["choch_bull"]
    else:
        raw = signals["bos_bear"] | signals["choch_bear"]
    return raw.rolling(lookback, min_periods=1).max().astype(bool)


def last_event_ts(signals, direction):
    if direction == 1:
        mask = signals["bos_bull"] | signals["choch_bull"]
    else:
        mask = signals["bos_bear"] | signals["choch_bear"]
    ts = pd.Series(np.where(mask, signals.index, pd.NaT), index=signals.index)
    return ts.ffill()


def diagnose_funnel(cfg, df_ltf, df_1h, df_4h, df_1d, funding, start, end) -> dict:
    struct = cfg["structure"]
    fvg_cfg = cfg["fvg"]
    session_cfg = cfg["session"]
    funding_cfg = cfg["funding"]
    mtf_cfg = cfg["mtf"]
    pivot_l, pivot_r = struct["pivot_left"], struct["pivot_right"]
    lookback = struct["signal_lookback_bars"]
    limit_bars = int(fvg_cfg.get("limit_valid_bars", 8))

    atr_s = atr(df_ltf, fvg_cfg["atr_period"])
    min_gaps = atr_s * fvg_cfg["min_size_atr_mult"]
    ltf_sig = detect_structure_signals(df_ltf, pivot_l, pivot_r)
    daily_b = map_bias_to_ltf_fast(df_1d, df_ltf.index, pivot_l, pivot_r)
    h4_b = map_bias_to_ltf_fast(df_4h, df_ltf.index, pivot_l, pivot_r)
    h1_b = map_bias_to_ltf_fast(df_1h, df_ltf.index, pivot_l, pivot_r)
    bull_ok = rolling_struct(ltf_sig, 1, lookback)
    bear_ok = rolling_struct(ltf_sig, -1, lookback)
    bull_ev = last_event_ts(ltf_sig, 1)
    bear_ev = last_event_ts(ltf_sig, -1)
    last_sh, last_sl = precompute_swings(df_ltf, pivot_l, pivot_r)
    fvgs = find_fvgs(df_ltf, min_gap=0)
    fvg_bull = fvgs[fvgs["direction"] == 1] if not fvgs.empty else fvgs
    fvg_bear = fvgs[fvgs["direction"] == -1] if not fvgs.empty else fvgs

    reasons = Counter()
    large_misses = []  # large FVG that died in funnel
    warmup = max(50, pivot_l + pivot_r + 20)

    for i in range(warmup, len(df_ltf)):
        ts = df_ltf.index[i]
        if start is not None and ts < start:
            continue
        if end is not None and ts > end:
            continue

        reasons["bars_in_window"] += 1
        if not in_trading_session(ts, session_cfg):
            reasons["reject_session"] += 1
            continue
        reasons["bars_in_session"] += 1

        row = df_ltf.iloc[i]
        db, h4, h1 = int(daily_b.iloc[i]), int(h4_b.iloc[i]), int(h1_b.iloc[i])
        min_gap = float(min_gaps.iloc[i])

        for direction, sok, ev_s, pool in (
            (1, bull_ok.iloc[i], bull_ev.iloc[i], fvg_bull),
            (-1, bear_ok.iloc[i], bear_ev.iloc[i], fvg_bear),
        ):
            if not sok:
                reasons["reject_no_recent_bos_choch"] += 1
                continue
            reasons["has_structure"] += 1

            if not mtf_aligned(
                db, h4, h1, direction,
                mtf_cfg["require_daily"], mtf_cfg["require_4h"], mtf_cfg["allow_1h_neutral"],
            ):
                reasons["reject_mtf"] += 1
                continue
            reasons["mtf_ok"] += 1

            if pd.isna(ev_s):
                reasons["reject_no_event_ts"] += 1
                continue
            event_ts = pd.Timestamp(ev_s)

            # any FVG after event within lookback window of formation on this bar?
            if pool.empty or ts not in pool.index:
                # also count "FVG exists after event but not formed this bar"
                after = pool[(pool.index >= event_ts) & (pool.index <= ts)] if not pool.empty else pool
                after = after[after["gap_size"] >= min_gap] if not after.empty else after
                if after.empty:
                    reasons["reject_no_fvg_after_event"] += 1
                else:
                    reasons["reject_fvg_not_formed_this_bar"] += 1
                    # large unfilled opportunity nearby
                    best = after.iloc[-1]
                    if float(best["gap_size"]) >= min_gap * 2:
                        large_misses.append(
                            {
                                "ts": str(ts),
                                "direction": "long" if direction == 1 else "short",
                                "reason": "fvg_not_on_this_bar",
                                "gap_size": float(best["gap_size"]),
                                "gap_atr_mult": float(best["gap_size"] / max(min_gap, 1e-9)),
                            }
                        )
                continue

            cand = pool.loc[ts]
            if isinstance(cand, pd.DataFrame):
                cand = cand.iloc[-1]
            if cand["gap_size"] < min_gap:
                reasons["reject_fvg_too_small"] += 1
                continue
            if ts < event_ts:
                reasons["reject_fvg_before_event"] += 1
                continue
            reasons["fvg_candidate"] += 1

            if funding_cfg.get("enabled") and funding_cfg.get("block_on_extreme"):
                rate = funding_at_time(funding, ts)
                allowed, _ = funding_allows_trade(
                    rate, direction,
                    funding_cfg["extreme_positive"],
                    funding_cfg["extreme_negative"],
                    True,
                )
                if not allowed:
                    reasons["reject_funding"] += 1
                    if float(cand["gap_size"]) >= min_gap * 2:
                        large_misses.append(
                            {
                                "ts": str(ts),
                                "direction": "long" if direction == 1 else "short",
                                "reason": "funding",
                                "gap_size": float(cand["gap_size"]),
                            }
                        )
                    continue

            entry = float(cand["ce"])
            setup = build_trade_setup(
                direction, entry, df_ltf.iloc[: i + 1], ts, cfg,
                last_swing_low=float(last_sl.iloc[i]) if direction == 1 else None,
                last_swing_high=float(last_sh.iloc[i]) if direction == -1 else None,
            )
            if setup is None:
                reasons["reject_rr_or_stop"] += 1
                if float(cand["gap_size"]) >= min_gap * 2:
                    large_misses.append(
                        {
                            "ts": str(ts),
                            "direction": "long" if direction == 1 else "short",
                            "reason": "rr_or_stop",
                            "gap_size": float(cand["gap_size"]),
                            "entry": entry,
                        }
                    )
                continue
            reasons["setup_ok"] += 1

            # Limit fill within next limit_bars?
            filled = False
            for j in range(i, min(i + limit_bars, len(df_ltf))):
                r = df_ltf.iloc[j]
                if r["low"] <= entry <= r["high"]:
                    filled = True
                    break
            if filled:
                reasons["limit_filled"] += 1
            else:
                reasons["reject_limit_not_filled"] += 1
                if float(cand["gap_size"]) >= min_gap * 2:
                    large_misses.append(
                        {
                            "ts": str(ts),
                            "direction": "long" if direction == 1 else "short",
                            "reason": "limit_not_filled_8bars",
                            "gap_size": float(cand["gap_size"]),
                            "entry": entry,
                        }
                    )

    return {
        "reasons": dict(reasons),
        "large_misses_count": len(large_misses),
        "large_misses_sample": large_misses[:40],
        "large_miss_reasons": dict(Counter(m["reason"] for m in large_misses)),
    }


def profile_trades(trades_df: pd.DataFrame) -> dict:
    if trades_df.empty:
        return {}
    trades_df = trades_df.copy()
    trades_df["entry_time"] = pd.to_datetime(trades_df["entry_time"], utc=True)
    trades_df["hour"] = trades_df["entry_time"].dt.hour
    trades_df["is_win"] = trades_df["pnl_r"] > 0
    trades_df["is_loss"] = trades_df["pnl_r"] < 0

    def side(mask):
        g = trades_df[mask]
        if g.empty:
            return {}
        return {
            "n": len(g),
            "avg_r": round(float(g["pnl_r"].mean()), 3),
            "avg_rr_planned": round(float(g["rr_planned"].mean()), 3),
            "median_rr_planned": round(float(g["rr_planned"].median()), 3),
            "pct_long": round(float((g["direction"] == "long").mean()), 3),
            "top_hours": g["hour"].value_counts().head(5).to_dict(),
            "outcomes": g["outcome"].value_counts().to_dict(),
        }

    # Worst losses
    worst = trades_df.nsmallest(15, "pnl_r")[
        ["entry_time", "direction", "entry", "pnl_r", "rr_planned", "outcome"]
    ]
    best = trades_df.nlargest(15, "pnl_r")[
        ["entry_time", "direction", "entry", "pnl_r", "rr_planned", "outcome"]
    ]

    return {
        "wins": side(trades_df["is_win"]),
        "losses": side(trades_df["is_loss"]),
        "breakeven_or_zero": side(trades_df["pnl_r"] == 0),
        "worst_15": worst.assign(entry_time=worst["entry_time"].astype(str)).to_dict(orient="records"),
        "best_15": best.assign(entry_time=best["entry_time"].astype(str)).to_dict(orient="records"),
        "by_hour_avg_r": trades_df.groupby("hour")["pnl_r"].mean().round(3).to_dict(),
        "by_direction": trades_df.groupby("direction")["pnl_r"].agg(["count", "mean", "sum"]).round(3).to_dict(),
    }


def main() -> None:
    cfg = load_config()
    # ensure filters off for diagnostic of baseline funnel
    for k in ("volatility", "displacement", "premium_discount", "fibonacci_ote"):
        cfg.setdefault("filters", {}).setdefault(k, {})["enabled"] = False

    df_15m, df_1h, df_4h, df_1d, funding = load_market_data(cfg)
    start, end, mode = resolve_sample_window(cfg, "out_of_sample")
    print(f"Funnel window: {mode} {start} -> {end}")

    print("Running backtest for trade profile...")
    trades, _, metrics, signals = run_backtest(
        cfg, df_15m, df_1h, df_4h, df_1d, funding, sample_mode="out_of_sample"
    )
    trades_df = pd.DataFrame([t.__dict__ for t in trades])
    sig_in = filter_signals_by_window(signals, start, end)
    print(f"Signals in window: {len(sig_in)}, trades: {len(trades)}")

    print("Scanning funnel (this takes a few minutes)...")
    funnel = diagnose_funnel(cfg, df_15m, df_1h, df_4h, df_1d, funding, start, end)
    profile = profile_trades(trades_df)

    out = Path("results/diagnostics")
    out.mkdir(parents=True, exist_ok=True)
    report = {
        "metrics": metrics,
        "funnel": funnel,
        "trade_profile": profile,
    }
    with open(out / "entry_funnel_oos.json", "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, default=str)

    print("\n=== FUNNEL (reject counts) ===")
    for k, v in sorted(funnel["reasons"].items(), key=lambda x: -x[1]):
        print(f"  {k}: {v}")
    print("\n=== LARGE FVG MISSES ===")
    print(funnel["large_miss_reasons"])
    print("\n=== TRADE PROFILE ===")
    print("wins:", profile.get("wins"))
    print("losses:", profile.get("losses"))
    print(f"\nSaved: {out / 'entry_funnel_oos.json'}")


if __name__ == "__main__":
    main()
