#!/usr/bin/env python3
"""
Last mining pass: characterize setups that reach MFE>=4R (swing SL),
then full OOS/IS backtest of the best simple rule (15m confirm + optional 1H swing).

Research only.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ict_bot.bias import map_bias_to_ltf_fast
from ict_bot.config import load_config
from ict_bot.data_loader import load_candles
from ict_bot.runner import load_market_data, run_backtest
from ict_bot.session import in_trading_session
from ict_bot.strategy import Signal
from ict_bot.structure import atr, detect_structure_signals, find_swing_points

OUT = Path("results/mfe4_mine")
PIVOT_L, PIVOT_R = 3, 3
SL_BUF_ATR = 0.3
SL_BUF_PCT = 0.001
MAX_HOLD = 96 * 5
MFE_TARGET = 4.0


def h4_1h_ok(h4: int, h1: int, d: int) -> bool:
    if h4 not in (0, d) or h1 not in (0, d):
        return False
    return not (h4 == 0 and h1 == 0)


def session_bucket(hour: int) -> str:
    if 8 <= hour < 14:
        return "EU"
    if 14 <= hour < 22:
        return "US"
    return "OFF"


def simulate_mfe(direction, entry, stop, entry_i, high, low, n):
    risk = abs(entry - stop)
    if risk <= 0:
        return None
    mfe = 0.0
    for j in range(entry_i + 1, min(n, entry_i + 1 + MAX_HOLD)):
        if direction == 1:
            mfe = max(mfe, (high[j] - entry) / risk)
            if low[j] <= stop:
                return {"mfe_r": mfe, "hit_sl_first": mfe < MFE_TARGET, "bars": j - entry_i}
        else:
            mfe = max(mfe, (entry - low[j]) / risk)
            if high[j] >= stop:
                return {"mfe_r": mfe, "hit_sl_first": mfe < MFE_TARGET, "bars": j - entry_i}
        if mfe >= MFE_TARGET:
            # continue until SL for full MFE
            for k in range(j, min(n, entry_i + 1 + MAX_HOLD)):
                if direction == 1:
                    mfe = max(mfe, (high[k] - entry) / risk)
                    if low[k] <= stop:
                        break
                else:
                    mfe = max(mfe, (entry - low[k]) / risk)
                    if high[k] >= stop:
                        break
            return {"mfe_r": mfe, "hit_sl_first": False, "bars": j - entry_i}
    return {"mfe_r": mfe, "hit_sl_first": True, "bars": None}


def displacement(close, open_, atr_v, i, lookback=3):
    """Max body/ATR in last lookback bars ending at i."""
    best = 0.0
    for j in range(max(0, i - lookback + 1), i + 1):
        a = atr_v[j] if not np.isnan(atr_v[j]) and atr_v[j] > 0 else 1e-9
        best = max(best, abs(close[j] - open_[j]) / a)
    return best


def mine_15m(df, df_1h, df_4h, df_1d):
    sh, sl = find_swing_points(df, PIVOT_L, PIVOT_R)
    atr_s = atr(df, 14)
    struct = detect_structure_signals(df, PIVOT_L, PIVOT_R)
    daily_b = map_bias_to_ltf_fast(df_1d, df.index, PIVOT_L, PIVOT_R)
    h4_b = map_bias_to_ltf_fast(df_4h, df.index, PIVOT_L, PIVOT_R)
    h1_b = map_bias_to_ltf_fast(df_1h, df.index, PIVOT_L, PIVOT_R)
    atr_pct = atr_s.rolling(2880, min_periods=100).rank(pct=True).values

    high, low, close, open_ = df["high"].values, df["low"].values, df["close"].values, df["open"].values
    atr_v = atr_s.values
    idx = df.index
    n = len(df)

    bos_b = (struct["bos_bull"] | struct["choch_bull"]).astype(int)
    bos_s = (struct["bos_bear"] | struct["choch_bear"]).astype(int)
    recent_bull = bos_b.rolling(20, min_periods=1).max().fillna(0).astype(bool).values
    recent_bear = bos_s.rolling(20, min_periods=1).max().fillna(0).astype(bool).values

    rows = []
    for i in range(PIVOT_L, n - PIVOT_R - 2):
        e = i + PIVOT_R
        for direction, is_sw, swing_px in (
            (1, sl.iloc[i], float(df["low"].iloc[i])),
            (-1, sh.iloc[i], float(df["high"].iloc[i])),
        ):
            if not is_sw:
                continue
            entry = float(close[e])
            a = float(atr_v[e]) if not np.isnan(atr_v[e]) else entry * 0.01
            buf = max(a * SL_BUF_ATR, entry * SL_BUF_PCT)
            stop = swing_px - buf if direction == 1 else swing_px + buf
            if direction == 1 and entry <= stop:
                continue
            if direction == -1 and entry >= stop:
                continue
            if abs(entry - stop) / entry > 0.05:
                continue

            sim = simulate_mfe(direction, entry, stop, e, high, low, n)
            if sim is None:
                continue
            big = sim["mfe_r"] >= MFE_TARGET and not sim["hit_sl_first"]
            # actually if mfe>=4 even if later SL, count as fat winner path
            fat = sim["mfe_r"] >= MFE_TARGET

            h4, h1, daily = int(h4_b.iloc[e]), int(h1_b.iloc[e]), int(daily_b.iloc[e])
            disp = displacement(close, open_, atr_v, e, 3)
            ts = idx[e]
            rows.append(
                {
                    "entry_time": ts.isoformat(),
                    "direction": "long" if direction == 1 else "short",
                    "mfe_r": round(sim["mfe_r"], 3),
                    "fat_4r": int(fat),
                    "bars_to_4r_or_sl": sim["bars"],
                    "hour": int(ts.hour),
                    "session": session_bucket(int(ts.hour)),
                    "weekday": int(ts.dayofweek),
                    "h4_1h": int(h4_1h_ok(h4, h1, direction)),
                    "daily_match": int(daily == direction),
                    "mtf_full": int(daily == direction and h4_1h_ok(h4, h1, direction)),
                    "recent_struct": int(
                        recent_bull[e] if direction == 1 else recent_bear[e]
                    ),
                    "disp_atr": round(disp, 3),
                    "atr_pct": None if np.isnan(atr_pct[e]) else round(float(atr_pct[e]), 3),
                    "risk_pct": abs(entry - stop) / entry * 100,
                }
            )
    return pd.DataFrame(rows)


def rate(df):
    if df.empty:
        return {"n": 0, "fat": 0, "fat_rate": None}
    fat = int(df["fat_4r"].sum())
    return {"n": int(len(df)), "fat": fat, "fat_rate": round(fat / len(df), 4)}


def summarize(df):
    base = rate(df)
    report = {"baseline": base, "by_filter": {}}
    filters = {
        "h4_1h": df["h4_1h"] == 1,
        "daily": df["daily_match"] == 1,
        "mtf_full": df["mtf_full"] == 1,
        "recent_struct": df["recent_struct"] == 1,
        "disp_ge_1.2": df["disp_atr"] >= 1.2,
        "disp_ge_1.5": df["disp_atr"] >= 1.5,
        "h4_1h_and_disp_1.2": (df["h4_1h"] == 1) & (df["disp_atr"] >= 1.2),
        "h4_1h_and_disp_1.5": (df["h4_1h"] == 1) & (df["disp_atr"] >= 1.5),
        "h4_1h_and_recent": (df["h4_1h"] == 1) & (df["recent_struct"] == 1),
        "h4_1h_disp_recent": (df["h4_1h"] == 1) & (df["disp_atr"] >= 1.2) & (df["recent_struct"] == 1),
        "session_EU_US": df["session"].isin(["EU", "US"]),
        "h4_1h_EU_US": (df["h4_1h"] == 1) & (df["session"].isin(["EU", "US"])),
        "h4_1h_disp_EU_US": (df["h4_1h"] == 1) & (df["disp_atr"] >= 1.2) & (df["session"].isin(["EU", "US"])),
    }
    for name, mask in filters.items():
        r = rate(df[mask])
        r["lift"] = round(r["fat_rate"] / base["fat_rate"], 3) if r["fat_rate"] and base["fat_rate"] else None
        report["by_filter"][name] = r

    # Among fat winners, feature rates
    fat = df[df["fat_4r"] == 1]
    thin = df[df["fat_4r"] == 0]
    report["fat_vs_thin"] = {
        "fat_n": len(fat),
        "thin_n": len(thin),
        "fat_pct_h4_1h": round(float(fat["h4_1h"].mean()), 4) if len(fat) else None,
        "thin_pct_h4_1h": round(float(thin["h4_1h"].mean()), 4) if len(thin) else None,
        "fat_pct_disp_1.2": round(float((fat["disp_atr"] >= 1.2).mean()), 4) if len(fat) else None,
        "thin_pct_disp_1.2": round(float((thin["disp_atr"] >= 1.2).mean()), 4) if len(thin) else None,
        "fat_median_disp": round(float(fat["disp_atr"].median()), 3) if len(fat) else None,
        "thin_median_disp": round(float(thin["disp_atr"].median()), 3) if len(thin) else None,
        "fat_by_session": fat["session"].value_counts(normalize=True).round(3).to_dict() if len(fat) else {},
    }
    ranked = sorted(
        [{"name": k, **v} for k, v in report["by_filter"].items() if v["n"] >= 200],
        key=lambda x: (-(x["fat_rate"] or 0), -x["n"]),
    )
    report["top_filters"] = ranked[:12]
    return report


def generate_candidate_signals(df_15m, df_1h, df_4h, cfg, *, need_disp=1.2, need_recent=True, session_only=True):
    """15m swing confirm + h4/1h + displacement (+ optional recent struct / session)."""
    sh, sl = find_swing_points(df_15m, PIVOT_L, PIVOT_R)
    atr_s = atr(df_15m, 14)
    struct = detect_structure_signals(df_15m, PIVOT_L, PIVOT_R)
    h4_b = map_bias_to_ltf_fast(df_4h, df_15m.index, PIVOT_L, PIVOT_R)
    h1_b = map_bias_to_ltf_fast(df_1h, df_15m.index, PIVOT_L, PIVOT_R)
    close, open_ = df_15m["close"].values, df_15m["open"].values
    atr_v = atr_s.values
    idx = df_15m.index
    n = len(df_15m)
    min_rr = float(cfg["risk"]["min_rr"])
    max_day = int(cfg["trade_management"].get("max_trades_per_day", 2))
    session_cfg = cfg["session"]

    bos_b = (struct["bos_bull"] | struct["choch_bull"]).astype(int)
    bos_s = (struct["bos_bear"] | struct["choch_bear"]).astype(int)
    recent_bull = bos_b.rolling(20, min_periods=1).max().fillna(0).astype(bool).values
    recent_bear = bos_s.rolling(20, min_periods=1).max().fillna(0).astype(bool).values

    signals = []
    seen = {}
    for i in range(PIVOT_L, n - PIVOT_R - 2):
        e = i + PIVOT_R
        ts = idx[e]
        if session_only and session_cfg.get("enabled") and not in_trading_session(ts, session_cfg):
            continue
        day = ts.strftime("%Y-%m-%d")
        if seen.get(day, 0) >= max_day:
            continue
        for direction, is_sw, swing_px in (
            (1, bool(sl.iloc[i]), float(df_15m["low"].iloc[i])),
            (-1, bool(sh.iloc[i]), float(df_15m["high"].iloc[i])),
        ):
            if not is_sw:
                continue
            if not h4_1h_ok(int(h4_b.iloc[e]), int(h1_b.iloc[e]), direction):
                continue
            if need_recent:
                ok = recent_bull[e] if direction == 1 else recent_bear[e]
                if not ok:
                    continue
            if displacement(close, open_, atr_v, e, 3) < need_disp:
                continue
            entry = float(close[e])
            a = float(atr_v[e]) if not np.isnan(atr_v[e]) else entry * 0.01
            buf = max(a * SL_BUF_ATR, entry * SL_BUF_PCT)
            stop = swing_px - buf if direction == 1 else swing_px + buf
            if direction == 1 and entry <= stop:
                continue
            if direction == -1 and entry >= stop:
                continue
            risk = abs(entry - stop)
            if risk <= 0 or risk / entry > 0.05:
                continue
            tp = entry + direction * risk * min_rr
            seen[day] = seen.get(day, 0) + 1
            signals.append(
                Signal(
                    timestamp=ts,
                    direction=direction,
                    entry=entry,
                    stop=stop,
                    take_profit=tp,
                    rr=min_rr,
                    meta={"strategy": "mfe4_disp_h4", "disp": True},
                )
            )
            break  # one signal attempt per bar for day cap simplicity
    signals.sort(key=lambda s: s.timestamp)
    return signals


def generate_1h_swing_15m_entry(df_15m, df_1h, df_4h, cfg):
    """1H swing confirm → enter next 15m close in session if 4H aligns (1H bias from structure)."""
    sh, sl = find_swing_points(df_1h, PIVOT_L, PIVOT_R)
    atr_1h = atr(df_1h, 14)
    h4_b = map_bias_to_ltf_fast(df_4h, df_15m.index, PIVOT_L, PIVOT_R)
    min_rr = float(cfg["risk"]["min_rr"])
    max_day = int(cfg["trade_management"].get("max_trades_per_day", 2))
    session_cfg = cfg["session"]
    signals = []
    seen = {}

    # Map each 1H confirm to 15m index
    for i in range(PIVOT_L, len(df_1h) - PIVOT_R - 1):
        confirm_i = i + PIVOT_R
        for direction, is_sw, swing_px in (
            (1, bool(sl.iloc[i]), float(df_1h["low"].iloc[i])),
            (-1, bool(sh.iloc[i]), float(df_1h["high"].iloc[i])),
        ):
            if not is_sw:
                continue
            ts_1h = df_1h.index[confirm_i]
            # first 15m bar at or after 1H confirm close
            loc = df_15m.index.searchsorted(ts_1h, side="left")
            # use bar that closes with the 1H candle: typically same timestamp if aligned
            if loc >= len(df_15m):
                continue
            # entry on 15m bar at confirm_i hour end — find 15m equal to 1h ts
            if ts_1h in df_15m.index:
                e_ts = ts_1h
            else:
                e_ts = df_15m.index[min(loc, len(df_15m) - 1)]
            e = df_15m.index.get_loc(e_ts)
            if isinstance(e, slice):
                e = e.start
            ts = df_15m.index[e]
            if session_cfg.get("enabled") and not in_trading_session(ts, session_cfg):
                continue
            h4 = int(h4_b.iloc[e])
            if h4 not in (0, direction):
                continue
            # light displacement on 1H confirm
            a1 = float(atr_1h.iloc[confirm_i]) if not np.isnan(atr_1h.iloc[confirm_i]) else 0
            body = abs(float(df_1h["close"].iloc[confirm_i]) - float(df_1h["open"].iloc[confirm_i]))
            if a1 > 0 and body / a1 < 0.8:
                continue
            entry = float(df_15m["close"].iloc[e])
            buf = max(a1 * SL_BUF_ATR if a1 else entry * 0.002, entry * SL_BUF_PCT)
            stop = swing_px - buf if direction == 1 else swing_px + buf
            if direction == 1 and entry <= stop:
                continue
            if direction == -1 and entry >= stop:
                continue
            risk = abs(entry - stop)
            if risk <= 0 or risk / entry > 0.08:
                continue
            day = ts.strftime("%Y-%m-%d")
            if seen.get(day, 0) >= max_day:
                continue
            tp = entry + direction * risk * min_rr
            seen[day] = seen.get(day, 0) + 1
            signals.append(
                Signal(
                    timestamp=ts,
                    direction=direction,
                    entry=entry,
                    stop=stop,
                    take_profit=tp,
                    rr=min_rr,
                    meta={"strategy": "h1_swing_15m"},
                )
            )
    signals.sort(key=lambda s: s.timestamp)
    return signals


def bt(name, signals, cfg, dfs):
    df_15m, df_1h, df_4h, df_1d, funding = dfs
    rows = []
    print(f"\n=== BACKTEST {name} signals={len(signals)} ===", flush=True)
    for mode in ("in_sample", "out_of_sample"):
        _, _, m, _ = run_backtest(
            cfg, df_15m, df_1h, df_4h, df_1d, funding, signals=signals, sample_mode=mode
        )
        row = {
            "variant": name,
            "sample": mode,
            "trades": m["trades"],
            "win_rate": m["win_rate"],
            "profit_factor": m["profit_factor"],
            "avg_r": m["avg_r"],
            "avg_r_per_month": m["avg_r_per_month"],
            "pct_months_at_goal": m["pct_months_at_goal"],
            "total_return_pct": m["total_return_pct"],
            "max_dd_pct": m["max_dd_pct"],
        }
        rows.append(row)
        print(
            f"  {mode}: trades={m['trades']} avgR/mo={m['avg_r_per_month']:.2f} "
            f"pct5R={m['pct_months_at_goal']:.0%} PF={m['profit_factor']:.2f} "
            f"ret={m['total_return_pct']:.1f}% DD={m['max_dd_pct']:.1f}%",
            flush=True,
        )
    return rows


def main():
    cfg = load_config()
    data_dir, symbol = cfg["data"]["dir"], cfg["symbol"]
    print("Loading...", flush=True)
    df_15m = load_candles(data_dir, symbol, "15m")
    df_1h = load_candles(data_dir, symbol, "1h")
    df_4h = load_candles(data_dir, symbol, "4h")
    df_1d = load_candles(data_dir, symbol, "1d")
    start = pd.Timestamp("2023-01-01", tz="UTC")
    df_oos = df_15m.loc[df_15m.index >= start]

    print(f"Mining fat legs on {len(df_oos)} bars...", flush=True)
    mined = mine_15m(df_oos, df_1h, df_4h, df_1d)
    OUT.mkdir(parents=True, exist_ok=True)
    mined.to_csv(OUT / "opportunities.csv", index=False)
    report = summarize(mined)
    with open(OUT / "mine_summary.json", "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2)

    print("\n=== FAT 4R mine ===", flush=True)
    print(json.dumps({"baseline": report["baseline"], "top_filters": report["top_filters"], "fat_vs_thin": report["fat_vs_thin"]}, indent=2), flush=True)

    # Full data for backtest windows
    dfs = load_market_data(cfg)
    df_15m, df_1h, df_4h, df_1d, funding = dfs

    bt_rows = []
    # Best mining-inspired rules
    for name, kwargs in (
        ("disp1.2_h4_session", {"need_disp": 1.2, "need_recent": False, "session_only": True}),
        ("disp1.2_h4_recent_session", {"need_disp": 1.2, "need_recent": True, "session_only": True}),
        ("disp1.5_h4_session", {"need_disp": 1.5, "need_recent": False, "session_only": True}),
    ):
        sigs = generate_candidate_signals(df_15m, df_1h, df_4h, cfg, **kwargs)
        bt_rows.extend(bt(name, sigs, cfg, dfs))

    sigs_h1 = generate_1h_swing_15m_entry(df_15m, df_1h, df_4h, cfg)
    bt_rows.extend(bt("h1_swing_15m_entry", sigs_h1, cfg, dfs))

    with open(OUT / "backtest_results.json", "w", encoding="utf-8") as f:
        json.dump(bt_rows, f, indent=2)
    print(f"\nSaved -> {OUT}", flush=True)


if __name__ == "__main__":
    main()
