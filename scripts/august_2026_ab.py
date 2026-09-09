#!/usr/bin/env python3
"""August 2026 backtest: single position vs overlap (daily on/off)."""

from __future__ import annotations

import json
import sys
from copy import deepcopy
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ict_bot.backtest import compute_metrics
from ict_bot.config import load_config
from ict_bot.runner import load_market_data, run_backtest
from ict_bot.sample import filter_signals_by_window
from scripts.overlap_ab import _slice_ltf, simulate_multi

AUG_START = pd.Timestamp("2026-08-01", tz="UTC")
AUG_END = pd.Timestamp("2026-08-31 23:59:59", tz="UTC")


def run_window(
    cfg: dict,
    df_15m: pd.DataFrame,
    df_1h,
    df_4h,
    df_1d,
    funding,
    signals: list,
    *,
    max_open: int,
    mode: str,
) -> dict:
    win_sigs = filter_signals_by_window(signals, AUG_START, AUG_END)
    df_win = _slice_ltf(df_15m, AUG_START, AUG_END)
    trades, _, meta = simulate_multi(df_win, win_sigs, cfg, max_open=max_open)

    initial = float(cfg["backtest"]["initial_balance"])
    risk_pct = float(cfg["backtest"]["risk_per_trade_pct"]) / 100
    bal = initial
    if trades:
        for t in trades:
            bal *= 1 + t.pnl_r * risk_pct
    equity = pd.Series([initial, bal], index=[AUG_START, AUG_END])
    m = compute_metrics(trades, equity, initial, goal_r_per_month=5.0)
    total_r = sum(t.pnl_r for t in trades)
    return {
        "mode": mode,
        "max_open": max_open,
        "signals": len(win_sigs),
        "trades": m["trades"],
        "skipped_busy": meta["skipped_busy"],
        "win_rate": m["win_rate"],
        "profit_factor": m["profit_factor"],
        "total_r": round(total_r, 2),
        "avg_r": m["avg_r"],
        "return_pct": m["total_return_pct"],
        "max_dd_pct": m["max_dd_pct"],
        "trade_details": [
            {
                "entry": t.entry_time[:16],
                "dir": t.direction,
                "pnl_r": t.pnl_r,
                "outcome": t.outcome,
                "rr": t.rr_planned,
            }
            for t in trades
        ],
    }


def main() -> None:
    base = load_config("configs/ict_daily_on.yaml")
    df_15m, df_1h, df_4h, df_1d, funding = load_market_data(base)
    print(f"15m data: {df_15m.index[0]} -> {df_15m.index[-1]} ({len(df_15m)} bars)", flush=True)

    modes = [
        ("single", 1),
        ("overlap3", 3),
        ("overlap_all", 999),
    ]
    rows: list[dict] = []

    for daily, label in [(True, "daily_on"), (False, "daily_off")]:
        cfg = deepcopy(base)
        cfg.setdefault("mtf", {})["require_daily"] = daily
        print(f"\n=== generate signals {label} ===", flush=True)
        _, _, _, signals = run_backtest(
            cfg, df_15m, df_1h, df_4h, df_1d, funding, sample_mode="full"
        )
        print(f"  full history signals: {len(signals)}", flush=True)

        for mode_name, max_open in modes:
            print(f"  sim Aug 2026 {mode_name} (max_open={max_open})...", flush=True)
            row = run_window(
                cfg, df_15m, df_1h, df_4h, df_1d, funding, signals,
                max_open=max_open, mode=mode_name,
            )
            row["variant"] = label
            row["require_daily"] = daily
            rows.append(row)
            print(
                f"    signals={row['signals']} trades={row['trades']} "
                f"skip_busy={row['skipped_busy']} total_R={row['total_r']:+.2f} "
                f"WR={row['win_rate']:.0%} PF={row['profit_factor']:.2f} "
                f"ret={row['return_pct']:+.1f}%",
                flush=True,
            )

    out = Path("results/august_2026_ab")
    out.mkdir(parents=True, exist_ok=True)
    with open(out / "results.json", "w", encoding="utf-8") as f:
        json.dump(rows, f, indent=2)
    print(f"\nSaved {out / 'results.json'}", flush=True)


if __name__ == "__main__":
    main()
