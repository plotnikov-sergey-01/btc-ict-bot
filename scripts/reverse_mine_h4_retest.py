#!/usr/bin/env python3
"""
Reverse-mine v2:
  - MTF = 4H + 1H only (no daily gate)
  - Entry A: confirm close (as before)
  - Entry B: retest after swing (pullback into lower/upper half of impulse)

Research only. Outputs results/reverse_mine_h4_retest/
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
from ict_bot.structure import atr, detect_structure_signals, find_swing_points

OUT = Path("results/reverse_mine_h4_retest")
MIN_RR = 2.0
PIVOT_L, PIVOT_R = 3, 3
SL_BUF_ATR = 0.3
SL_BUF_PCT = 0.001
MAX_HOLD = 96 * 5
RETEST_BARS = 16  # 4h window on 15m
RETEST_FRAC = 0.5  # pullback into 50% of impulse range


def _session_bucket(hour: int) -> str:
    if 8 <= hour < 14:
        return "EU_08_14"
    if 14 <= hour < 22:
        return "US_14_22"
    return "OFF_session"


def _h4_1h_ok(h4: int, h1: int, direction: int) -> bool:
    """4H must agree or be neutral; 1H same (allow neutral). No daily."""
    if h4 not in (0, direction):
        return False
    if h1 not in (0, direction):
        return False
    # At least one of 4H/1H must be actively with the trade (not both neutral)
    if h4 == 0 and h1 == 0:
        return False
    return True


def _simulate_forward(
    direction: int,
    entry: float,
    stop: float,
    entry_i: int,
    high: np.ndarray,
    low: np.ndarray,
    n: int,
) -> dict:
    risk = abs(entry - stop)
    if risk <= 0:
        return {"outcome": "invalid", "mfe_r": 0.0, "bars_to_event": None}
    tp2 = entry + direction * risk * MIN_RR
    mfe_r = 0.0
    exit_j = None
    hit_2r = hit_sl = False
    for j in range(entry_i + 1, min(n, entry_i + 1 + MAX_HOLD)):
        if direction == 1:
            mfe_r = max(mfe_r, (high[j] - entry) / risk)
            if low[j] <= stop:
                hit_sl = True
                exit_j = j
                break
            if high[j] >= tp2:
                hit_2r = True
                exit_j = j
                for k in range(j, min(n, entry_i + 1 + MAX_HOLD)):
                    mfe_r = max(mfe_r, (high[k] - entry) / risk)
                    if low[k] <= stop:
                        break
                break
        else:
            mfe_r = max(mfe_r, (entry - low[j]) / risk)
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
                break
    if hit_2r:
        outcome = "win_2r"
    elif hit_sl:
        outcome = "loss_sl"
    else:
        outcome = "timeout"
    return {
        "outcome": outcome,
        "mfe_r": round(mfe_r, 3),
        "bars_to_event": (exit_j - entry_i) if exit_j is not None else None,
    }


def _find_retest_entry(
    direction: int,
    swing_i: int,
    confirm_i: int,
    swing_price: float,
    high: np.ndarray,
    low: np.ndarray,
    close: np.ndarray,
    atr_v: np.ndarray,
    n: int,
) -> tuple[int, float, float] | None:
    """
    After swing confirm, wait for pullback into RETEST_FRAC of impulse, then close back
    in trade direction. SL behind swing + buffer at entry time.
    Invalidated if stop level is pierced before entry.
    """
    # Impulse extreme between swing and confirm
    if direction == 1:
        impulse_ext = float(np.max(high[swing_i : confirm_i + 1]))
        rng = impulse_ext - swing_price
        if rng <= 0:
            return None
        zone = swing_price + RETEST_FRAC * rng
    else:
        impulse_ext = float(np.min(low[swing_i : confirm_i + 1]))
        rng = swing_price - impulse_ext
        if rng <= 0:
            return None
        zone = swing_price - RETEST_FRAC * rng

    for j in range(confirm_i + 1, min(n, confirm_i + 1 + RETEST_BARS)):
        a = float(atr_v[j]) if not np.isnan(atr_v[j]) else close[j] * 0.01
        buf = max(a * SL_BUF_ATR, close[j] * SL_BUF_PCT)
        if direction == 1:
            stop_probe = swing_price - buf
            if low[j] <= stop_probe:
                return None  # stopped before retest fill
            # Touched retest zone and closed back up
            if low[j] <= zone and close[j] > zone:
                entry = float(close[j])
                stop = swing_price - buf
                if entry <= stop:
                    return None
                return j, entry, stop
        else:
            stop_probe = swing_price + buf
            if high[j] >= stop_probe:
                return None
            if high[j] >= zone and close[j] < zone:
                entry = float(close[j])
                stop = swing_price + buf
                if entry >= stop:
                    return None
                return j, entry, stop
    return None  # no retest in window


def mine(df: pd.DataFrame, df_1h, df_4h, df_1d) -> pd.DataFrame:
    sh, sl = find_swing_points(df, PIVOT_L, PIVOT_R)
    atr_s = atr(df, 14)
    struct = detect_structure_signals(df, PIVOT_L, PIVOT_R)
    daily_b = map_bias_to_ltf_fast(df_1d, df.index, PIVOT_L, PIVOT_R)
    h4_b = map_bias_to_ltf_fast(df_4h, df.index, PIVOT_L, PIVOT_R)
    h1_b = map_bias_to_ltf_fast(df_1h, df.index, PIVOT_L, PIVOT_R)

    high, low, close = df["high"].values, df["low"].values, df["close"].values
    atr_v = atr_s.values
    idx = df.index
    atr_pct = atr_s.rolling(2880, min_periods=100).rank(pct=True).values

    bos_bull = struct["bos_bull"].values
    bos_bear = struct["bos_bear"].values
    choch_bull = struct["choch_bull"].values
    choch_bear = struct["choch_bear"].values

    def recent_any(arr, n=20):
        return pd.Series(arr.astype(int)).rolling(n, min_periods=1).max().fillna(0).astype(bool).values

    recent_bull = recent_any(bos_bull | choch_bull)
    recent_bear = recent_any(bos_bear | choch_bear)

    rows: list[dict] = []
    n = len(df)

    for i in range(PIVOT_L, n - PIVOT_R - 2):
        confirm_i = i + PIVOT_R
        for direction, is_swing, swing_price in (
            (1, sl.iloc[i], float(df["low"].iloc[i])),
            (-1, sh.iloc[i], float(df["high"].iloc[i])),
        ):
            if not is_swing:
                continue

            h4 = int(h4_b.iloc[confirm_i])
            h1 = int(h1_b.iloc[confirm_i])
            daily = int(daily_b.iloc[confirm_i])
            h4_1h = int(_h4_1h_ok(h4, h1, direction))
            daily_match = int(daily == direction)
            # Old full MTF (daily required + 4h/1h)
            mtf_full = int(daily_match == 1 and h4 in (0, direction) and h1 in (0, direction) and not (h4 == 0 and h1 == 0 and daily == 0))
            # Stricter: daily must match AND h4_1h
            mtf_full = int(daily == direction and _h4_1h_ok(h4, h1, direction))

            # --- A) confirm entry ---
            entry_c = float(close[confirm_i])
            a = float(atr_v[confirm_i]) if not np.isnan(atr_v[confirm_i]) else entry_c * 0.01
            buf = max(a * SL_BUF_ATR, entry_c * SL_BUF_PCT)
            if direction == 1:
                stop_c = swing_price - buf
                valid_c = entry_c > stop_c
            else:
                stop_c = swing_price + buf
                valid_c = entry_c < stop_c

            if valid_c and abs(entry_c - stop_c) / entry_c <= 0.05:
                sim = _simulate_forward(direction, entry_c, stop_c, confirm_i, high, low, n)
                ts = idx[confirm_i]
                rows.append(
                    {
                        "entry_mode": "confirm",
                        "entry_time": ts.isoformat(),
                        "direction": "long" if direction == 1 else "short",
                        "entry": entry_c,
                        "stop": stop_c,
                        "risk_pct": abs(entry_c - stop_c) / entry_c * 100,
                        "outcome": sim["outcome"],
                        "mfe_r": sim["mfe_r"],
                        "bars_to_event": sim["bars_to_event"],
                        "hour_utc": int(ts.hour),
                        "session": _session_bucket(int(ts.hour)),
                        "h4_1h_aligned": h4_1h,
                        "daily_match": daily_match,
                        "mtf_full_with_daily": mtf_full,
                        "recent_struct": bool(
                            recent_bull[confirm_i] if direction == 1 else recent_bear[confirm_i]
                        ),
                        "atr_pct": None if np.isnan(atr_pct[confirm_i]) else round(float(atr_pct[confirm_i]), 3),
                        "h4_bias": h4,
                        "h1_bias": h1,
                        "daily_bias": daily,
                    }
                )

            # --- B) retest entry ---
            retest = _find_retest_entry(
                direction, i, confirm_i, swing_price, high, low, close, atr_v, n
            )
            if retest is None:
                continue
            entry_i, entry_r, stop_r = retest
            if abs(entry_r - stop_r) / entry_r > 0.05:
                continue
            # Refresh MTF at retest bar
            h4r = int(h4_b.iloc[entry_i])
            h1r = int(h1_b.iloc[entry_i])
            dailyr = int(daily_b.iloc[entry_i])
            h4_1h_r = int(_h4_1h_ok(h4r, h1r, direction))
            daily_match_r = int(dailyr == direction)
            mtf_full_r = int(dailyr == direction and _h4_1h_ok(h4r, h1r, direction))

            sim = _simulate_forward(direction, entry_r, stop_r, entry_i, high, low, n)
            ts = idx[entry_i]
            rows.append(
                {
                    "entry_mode": "retest",
                    "entry_time": ts.isoformat(),
                    "direction": "long" if direction == 1 else "short",
                    "entry": entry_r,
                    "stop": stop_r,
                    "risk_pct": abs(entry_r - stop_r) / entry_r * 100,
                    "outcome": sim["outcome"],
                    "mfe_r": sim["mfe_r"],
                    "bars_to_event": sim["bars_to_event"],
                    "hour_utc": int(ts.hour),
                    "session": _session_bucket(int(ts.hour)),
                    "h4_1h_aligned": h4_1h_r,
                    "daily_match": daily_match_r,
                    "mtf_full_with_daily": mtf_full_r,
                    "recent_struct": bool(
                        recent_bull[entry_i] if direction == 1 else recent_bear[entry_i]
                    ),
                    "atr_pct": None if np.isnan(atr_pct[entry_i]) else round(float(atr_pct[entry_i]), 3),
                    "h4_bias": h4r,
                    "h1_bias": h1r,
                    "daily_bias": dailyr,
                    "bars_confirm_to_entry": entry_i - confirm_i,
                }
            )

    return pd.DataFrame(rows)


def rate(df: pd.DataFrame) -> dict:
    if df.empty:
        return {"n": 0, "wins": 0, "hit_2r_rate": None}
    w = df[df["outcome"] == "win_2r"]
    return {
        "n": int(len(df)),
        "wins": int(len(w)),
        "hit_2r_rate": round(len(w) / len(df), 4),
        "avg_mfe_wins": round(float(w["mfe_r"].mean()), 3) if len(w) else None,
        "median_mfe_wins": round(float(w["mfe_r"].median()), 3) if len(w) else None,
    }


def summarize(df: pd.DataFrame) -> dict:
    report: dict = {"hypothesis_retest": {
        "rule": (
            "After swing confirm (L=R=3), within 16 bars wait for pullback into "
            "50% of impulse range [swing → impulse extreme]; enter on close back "
            "through zone; SL behind swing+ATR buffer; success = 2R before SL."
        ),
        "mtf_h4_1h": (
            "Aligned if 4H in {dir, neutral}, 1H in {dir, neutral}, "
            "and not both neutral. Daily ignored."
        ),
    }}

    for mode in ("confirm", "retest"):
        sub = df[df["entry_mode"] == mode]
        block = {
            "all": rate(sub),
            "h4_1h_only": rate(sub[sub["h4_1h_aligned"] == 1]),
            "h4_1h_off": rate(sub[sub["h4_1h_aligned"] == 0]),
            "daily_match": rate(sub[sub["daily_match"] == 1]),
            "mtf_full_with_daily": rate(sub[sub["mtf_full_with_daily"] == 1]),
            "h4_1h_and_session_EU_US": rate(
                sub[(sub["h4_1h_aligned"] == 1) & (sub["session"].isin(["EU_08_14", "US_14_22"]))]
            ),
            "h4_1h_off_session": rate(
                sub[(sub["h4_1h_aligned"] == 1) & (sub["session"] == "OFF_session")]
            ),
        }
        if not sub.empty and len(sub[sub["outcome"] == "win_2r"]):
            w = sub[sub["outcome"] == "win_2r"]
            block["wins_mfe"] = {
                "median": round(float(w["mfe_r"].median()), 3),
                "p75": round(float(w["mfe_r"].quantile(0.75)), 3),
                "pct_ge_3r": round(float((w["mfe_r"] >= 3).mean()), 4),
                "pct_ge_4r": round(float((w["mfe_r"] >= 4).mean()), 4),
            }
        if mode == "retest" and "bars_confirm_to_entry" in sub.columns and len(sub):
            block["retest_delay_bars"] = {
                "median": float(sub["bars_confirm_to_entry"].median()),
                "p75": float(sub["bars_confirm_to_entry"].quantile(0.75)),
                "fill_rate_note": "rows only where retest filled; many swings never retest",
            }
        report[mode] = block

    # Lift tables
    for mode in ("confirm", "retest"):
        base = report[mode]["all"]["hit_2r_rate"] or 0
        report[mode]["lifts_vs_all"] = {}
        for k in ("h4_1h_only", "daily_match", "mtf_full_with_daily", "h4_1h_and_session_EU_US"):
            r = report[mode][k]["hit_2r_rate"]
            report[mode]["lifts_vs_all"][k] = None if not r or not base else round(r / base, 3)

    return report


def main() -> None:
    cfg = load_config()
    data_dir, symbol = cfg["data"]["dir"], cfg["symbol"]
    print("Loading...", flush=True)
    df_15m = load_candles(data_dir, symbol, "15m")
    df_1h = load_candles(data_dir, symbol, "1h")
    df_4h = load_candles(data_dir, symbol, "4h")
    df_1d = load_candles(data_dir, symbol, "1d")
    start = pd.Timestamp("2023-01-01", tz="UTC")
    df_15m = df_15m.loc[df_15m.index >= start]
    print(f"Mining {len(df_15m)} bars...", flush=True)

    mined = mine(df_15m, df_1h, df_4h, df_1d)
    OUT.mkdir(parents=True, exist_ok=True)
    mined.to_csv(OUT / "opportunities.csv", index=False)
    report = summarize(mined)
    with open(OUT / "summary.json", "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2)

    print("\n=== CONFIRM ===", flush=True)
    print(json.dumps(report["confirm"], indent=2), flush=True)
    print("\n=== RETEST ===", flush=True)
    print(json.dumps(report["retest"], indent=2), flush=True)
    print(f"\nSaved -> {OUT}", flush=True)


if __name__ == "__main__":
    main()
