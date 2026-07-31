#!/usr/bin/env python3
"""
Reverse-mine 15m BTC moves: SL behind last swing, need ≥2R before stop.
Characterize winners vs opportunities — research only, not a live strategy.
"""

from __future__ import annotations

import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ict_bot.bias import map_bias_to_ltf_fast
from ict_bot.config import load_config
from ict_bot.data_loader import load_candles
from ict_bot.structure import atr, detect_structure_signals, find_swing_points

OUT = Path("results/reverse_mine")
MIN_RR = 2.0
PIVOT_L, PIVOT_R = 3, 3
SL_BUF_ATR = 0.3
SL_BUF_PCT = 0.001
# Cap forward scan (bars) so we don't wait forever
MAX_HOLD = 96 * 5  # 5 days of 15m


def _session_bucket(hour: int) -> str:
    if 8 <= hour < 14:
        return "EU_08_14"
    if 14 <= hour < 22:
        return "US_14_22"
    return "OFF_session"


def mine(df: pd.DataFrame, df_1h, df_4h, df_1d) -> pd.DataFrame:
    sh, sl = find_swing_points(df, PIVOT_L, PIVOT_R)
    atr_s = atr(df, 14)
    struct = detect_structure_signals(df, PIVOT_L, PIVOT_R)
    daily_b = map_bias_to_ltf_fast(df_1d, df.index, PIVOT_L, PIVOT_R)
    h4_b = map_bias_to_ltf_fast(df_4h, df.index, PIVOT_L, PIVOT_R)
    h1_b = map_bias_to_ltf_fast(df_1h, df.index, PIVOT_L, PIVOT_R)

    high = df["high"].values
    low = df["low"].values
    close = df["close"].values
    open_ = df["open"].values
    atr_v = atr_s.values
    idx = df.index

    # Rolling ATR percentile (approx 30d)
    look = 2880
    atr_rank = atr_s.rolling(look, min_periods=100).rank(pct=True)
    atr_pct = atr_rank.values

    bos_bull = struct["bos_bull"].values
    bos_bear = struct["bos_bear"].values
    choch_bull = struct["choch_bull"].values
    choch_bear = struct["choch_bear"].values
    bias_ltf = struct["structure_bias"].values

    # Recent structure in last 20 bars (prefix OR via rolling)
    def recent_any(arr, n=20):
        s = pd.Series(arr.astype(int))
        return s.rolling(n, min_periods=1).max().fillna(0).astype(bool).values

    recent_bull = recent_any(bos_bull | choch_bull)
    recent_bear = recent_any(bos_bear | choch_bear)

    rows = []
    n = len(df)
    # Swing confirmed at i+PIVOT_R → entry at bar e = i+PIVOT_R (close) — no lookahead beyond confirm
    for i in range(PIVOT_L, n - PIVOT_R - 2):
        e = i + PIVOT_R  # confirmation bar index
        if e + 1 >= n:
            continue
        entry_i = e  # enter on confirm close (conservative vs waiting next open)

        for direction, is_swing, swing_price in (
            (1, sl.iloc[i], float(df["low"].iloc[i])),
            (-1, sh.iloc[i], float(df["high"].iloc[i])),
        ):
            if not is_swing:
                continue

            entry = float(close[entry_i])
            a = float(atr_v[entry_i]) if not np.isnan(atr_v[entry_i]) else entry * 0.01
            buf = max(a * SL_BUF_ATR, entry * SL_BUF_PCT)
            if direction == 1:
                stop = swing_price - buf
                if entry <= stop:
                    continue
            else:
                stop = swing_price + buf
                if entry >= stop:
                    continue

            risk = abs(entry - stop)
            if risk <= 0 or risk / entry > 0.05:  # skip absurd >5% risk
                continue

            tp2 = entry + direction * risk * MIN_RR
            # Forward path
            hit_2r = False
            hit_sl = False
            mfe_r = 0.0
            mae_r = 0.0
            exit_j = None
            max_rr = 0.0
            for j in range(entry_i + 1, min(n, entry_i + 1 + MAX_HOLD)):
                if direction == 1:
                    mfe_r = max(mfe_r, (high[j] - entry) / risk)
                    mae_r = max(mae_r, (entry - low[j]) / risk)
                    if low[j] <= stop:
                        hit_sl = True
                        exit_j = j
                        break
                    if high[j] >= tp2:
                        hit_2r = True
                        exit_j = j
                        # continue a bit for max excursion same bar already counted
                        # track further until SL or end for max_rr
                        max_rr = mfe_r
                        # extend MFE until SL
                        for k in range(j, min(n, entry_i + 1 + MAX_HOLD)):
                            mfe_r = max(mfe_r, (high[k] - entry) / risk)
                            if low[k] <= stop:
                                break
                        max_rr = mfe_r
                        break
                else:
                    mfe_r = max(mfe_r, (entry - low[j]) / risk)
                    mae_r = max(mae_r, (high[j] - entry) / risk)
                    if high[j] >= stop:
                        hit_sl = True
                        exit_j = j
                        break
                    if low[j] <= tp2:
                        hit_2r = True
                        exit_j = j
                        for k in range(j, min(n, entry_i + 1 + MAX_HOLD)):
                            mfe_r = max(mfe_r, (entry - low[k]) / risk)
                            if high[k] >= stop:
                                break
                        max_rr = mfe_r
                        break

            if not hit_2r and not hit_sl:
                outcome = "timeout"
            elif hit_2r:
                outcome = "win_2r"
            else:
                outcome = "loss_sl"

            ts = idx[entry_i]
            hour = int(ts.hour)
            rows.append(
                {
                    "entry_time": ts.isoformat(),
                    "direction": "long" if direction == 1 else "short",
                    "entry": entry,
                    "stop": stop,
                    "swing": swing_price,
                    "risk_pct": risk / entry * 100,
                    "outcome": outcome,
                    "mfe_r": round(mfe_r, 3),
                    "mae_r": round(mae_r, 3),
                    "max_rr_after_2r": round(max_rr, 3) if hit_2r else None,
                    "bars_to_event": (exit_j - entry_i) if exit_j else None,
                    "hour_utc": hour,
                    "weekday": int(ts.dayofweek),
                    "session": _session_bucket(hour),
                    "atr_pct": None if np.isnan(atr_pct[entry_i]) else round(float(atr_pct[entry_i]), 3),
                    "daily_bias": int(daily_b.iloc[entry_i]),
                    "h4_bias": int(h4_b.iloc[entry_i]),
                    "h1_bias": int(h1_b.iloc[entry_i]),
                    "ltf_bias": int(bias_ltf[entry_i]),
                    "mtf_aligned": int(
                        (int(daily_b.iloc[entry_i]) == direction)
                        and (int(h4_b.iloc[entry_i]) in (0, direction))
                        and (int(h1_b.iloc[entry_i]) in (0, direction))
                    ),
                    "recent_struct_with_dir": bool(
                        recent_bull[entry_i] if direction == 1 else recent_bear[entry_i]
                    ),
                    "dist_to_round_1k": abs(entry - round(entry / 1000) * 1000) / entry * 100,
                }
            )
    return pd.DataFrame(rows)


def summarize(df: pd.DataFrame) -> dict:
    wins = df[df["outcome"] == "win_2r"]
    losses = df[df["outcome"] == "loss_sl"]
    all_n = len(df)
    report: dict = {
        "n_opportunities": all_n,
        "n_win_2r": len(wins),
        "n_loss_sl": len(losses),
        "n_timeout": int((df["outcome"] == "timeout").sum()),
        "win_rate_among_resolved": round(len(wins) / max(1, len(wins) + len(losses)), 4),
        "pct_opps_hit_2r": round(len(wins) / max(1, all_n), 4),
    }

    def rate_table(col, subset=None):
        base = subset if subset is not None else df
        w = base[base["outcome"] == "win_2r"]
        out = {}
        for key, g in base.groupby(col):
            gw = g[g["outcome"] == "win_2r"]
            out[str(key)] = {
                "n": int(len(g)),
                "wins": int(len(gw)),
                "hit_2r_rate": round(len(gw) / max(1, len(g)), 4),
                "share_of_wins": round(len(gw) / max(1, len(w)), 4),
            }
        return out

    report["by_session"] = rate_table("session")
    report["by_direction"] = rate_table("direction")
    report["by_weekday"] = rate_table("weekday")
    report["by_hour"] = rate_table("hour_utc")
    report["by_mtf_aligned"] = rate_table("mtf_aligned")
    report["by_recent_struct"] = rate_table("recent_struct_with_dir")
    report["by_daily_bias_match"] = {}
    # daily bias matches direction
    df = df.copy()
    df["daily_match"] = (
        ((df["direction"] == "long") & (df["daily_bias"] == 1))
        | ((df["direction"] == "short") & (df["daily_bias"] == -1))
    ).astype(int)
    report["by_daily_match"] = rate_table("daily_match", df)

    if len(wins) and "atr_pct" in wins.columns:
        wa = wins["atr_pct"].dropna()
        la = losses["atr_pct"].dropna()
        report["atr_pct_wins"] = {
            "median": float(wa.median()) if len(wa) else None,
            "p25": float(wa.quantile(0.25)) if len(wa) else None,
            "p75": float(wa.quantile(0.75)) if len(wa) else None,
        }
        report["atr_pct_losses"] = {
            "median": float(la.median()) if len(la) else None,
        }
        # buckets
        def bucket_atr(x):
            if pd.isna(x):
                return "na"
            if x < 0.3:
                return "low_<30"
            if x < 0.7:
                return "mid_30_70"
            return "high_>70"

        df["atr_bucket"] = df["atr_pct"].map(bucket_atr)
        report["by_atr_bucket"] = rate_table("atr_bucket", df)

    if len(wins):
        report["wins_mfe_r"] = {
            "median": float(wins["mfe_r"].median()),
            "p75": float(wins["mfe_r"].quantile(0.75)),
            "p90": float(wins["mfe_r"].quantile(0.90)),
            "pct_reach_3r": round(float((wins["mfe_r"] >= 3).mean()), 4),
            "pct_reach_4r": round(float((wins["mfe_r"] >= 4).mean()), 4),
        }
        report["wins_bars_to_2r"] = {
            "median": float(wins["bars_to_event"].median()),
            "p75": float(wins["bars_to_event"].quantile(0.75)),
        }

    # Simple "cluster" scores: combinations with elevated hit rate & enough samples
    combos = []
    for sess in df["session"].unique():
        for mtf in (0, 1):
            for rs in (False, True):
                for dmatch in (0, 1):
                    mask = (
                        (df["session"] == sess)
                        & (df["mtf_aligned"] == mtf)
                        & (df["recent_struct_with_dir"] == rs)
                        & (df["daily_match"] == dmatch)
                    )
                    g = df[mask]
                    if len(g) < 80:
                        continue
                    gw = g[g["outcome"] == "win_2r"]
                    rate = len(gw) / len(g)
                    combos.append(
                        {
                            "session": sess,
                            "mtf_aligned": mtf,
                            "recent_struct": rs,
                            "daily_match": dmatch,
                            "n": int(len(g)),
                            "wins": int(len(gw)),
                            "hit_2r_rate": round(rate, 4),
                        }
                    )
    combos.sort(key=lambda x: (-x["hit_2r_rate"], -x["n"]))
    report["top_combos"] = combos[:15]
    baseline = report["pct_opps_hit_2r"]
    report["baseline_hit_2r_rate"] = baseline
    report["lift_vs_baseline"] = [
        {**c, "lift": round(c["hit_2r_rate"] / baseline, 2) if baseline else None}
        for c in combos[:10]
    ]
    return report


def main() -> None:
    cfg = load_config()
    data_dir = cfg["data"]["dir"]
    symbol = cfg["symbol"]
    print("Loading...", flush=True)
    df_15m = load_candles(data_dir, symbol, "15m")
    df_1h = load_candles(data_dir, symbol, "1h")
    df_4h = load_candles(data_dir, symbol, "4h")
    df_1d = load_candles(data_dir, symbol, "1d")

    # Focus OOS-like window (same as strategy validation)
    start = pd.Timestamp("2023-01-01", tz="UTC")
    df_15m = df_15m.loc[df_15m.index >= start]
    print(f"Mining {len(df_15m)} 15m bars from {df_15m.index[0]}...", flush=True)

    mined = mine(df_15m, df_1h, df_4h, df_1d)
    OUT.mkdir(parents=True, exist_ok=True)
    mined.to_csv(OUT / "opportunities.csv", index=False)
    wins = mined[mined["outcome"] == "win_2r"]
    wins.to_csv(OUT / "wins_2r.csv", index=False)

    report = summarize(mined)
    with open(OUT / "summary.json", "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2)

    print("\n=== SUMMARY ===", flush=True)
    print(json.dumps({k: report[k] for k in (
        "n_opportunities", "n_win_2r", "n_loss_sl", "pct_opps_hit_2r",
        "win_rate_among_resolved", "by_session", "by_direction",
        "by_mtf_aligned", "by_recent_struct", "by_daily_match",
        "wins_mfe_r", "top_combos",
    ) if k in report}, indent=2), flush=True)
    print(f"\nSaved -> {OUT}", flush=True)


if __name__ == "__main__":
    main()
