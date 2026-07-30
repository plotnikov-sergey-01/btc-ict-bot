#!/usr/bin/env python3
"""Compare trailing variants and yearly/seasonal breakdown."""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pandas as pd
import yaml

from ict_bot.backtest import compute_metrics, simulate_trades
from ict_bot.config import load_config
from ict_bot.data_loader import load_candles, load_funding
from ict_bot.strategy import generate_signals


VARIANTS = {
    "trail_1.5R_extend": {
        "trail_activate_at_rr": 1.5,
        "extend_tp_on_trail": True,
    },
    "trail_2.0R_extend": {
        "trail_activate_at_rr": 2.0,
        "extend_tp_on_trail": True,
    },
    "trail_1.5R_no_extend": {
        "trail_activate_at_rr": 1.5,
        "extend_tp_on_trail": False,
    },
    "no_trail_baseline": {
        "trailing_stop": False,
        "trail_activate_at_rr": 1.0,
        "extend_tp_on_trail": False,
    },
}


def yearly_breakdown(trades_df: pd.DataFrame, initial_balance: float = 10000) -> pd.DataFrame:
    trades_df = trades_df.copy()
    trades_df["entry_time"] = pd.to_datetime(trades_df["entry_time"], utc=True)
    trades_df["year"] = trades_df["entry_time"].dt.year
    trades_df["quarter"] = trades_df["entry_time"].dt.to_period("Q").astype(str)

    rows = []
    for year, grp in trades_df.groupby("year"):
        wins = grp[grp["pnl_r"] > 0]
        losses = grp[grp["pnl_r"] < 0]
        gp = wins["pnl_r"].sum()
        gl = abs(losses["pnl_r"].sum())
        rows.append(
            {
                "period": str(year),
                "trades": len(grp),
                "win_rate": round(len(wins) / len(grp), 3) if len(grp) else 0,
                "profit_factor": round(gp / gl, 2) if gl > 0 else None,
                "total_r": round(grp["pnl_r"].sum(), 2),
                "avg_r": round(grp["pnl_r"].mean(), 3),
            }
        )
    return pd.DataFrame(rows)


def seasonal_breakdown(trades_df: pd.DataFrame) -> pd.DataFrame:
    trades_df = trades_df.copy()
    trades_df["entry_time"] = pd.to_datetime(trades_df["entry_time"], utc=True)
    trades_df["quarter"] = trades_df["entry_time"].dt.to_period("Q").astype(str)

    rows = []
    for q, grp in trades_df.groupby("quarter"):
        wins = grp[grp["pnl_r"] > 0]
        losses = grp[grp["pnl_r"] < 0]
        gp = wins["pnl_r"].sum()
        gl = abs(losses["pnl_r"].sum())
        rows.append(
            {
                "period": q,
                "trades": len(grp),
                "win_rate": round(len(wins) / len(grp), 3) if len(grp) else 0,
                "profit_factor": round(gp / gl, 2) if gl > 0 else None,
                "total_r": round(grp["pnl_r"].sum(), 2),
            }
        )
    return pd.DataFrame(rows)


def run_variant(name: str, tm_overrides: dict, cfg: dict, signals, df_15m) -> dict:
    cfg = copy.deepcopy(cfg)
    cfg["trade_management"]["trailing_stop"] = tm_overrides.get("trailing_stop", True)
    cfg["trade_management"]["trail_activate_at_rr"] = tm_overrides.get("trail_activate_at_rr", 1.0)
    cfg["trade_management"]["extend_tp_on_trail"] = tm_overrides.get("extend_tp_on_trail", True)

    trades, equity = simulate_trades(df_15m, signals, cfg)
    metrics = compute_metrics(trades, equity, cfg["backtest"]["initial_balance"])
    trades_df = pd.DataFrame([t.__dict__ for t in trades])

    out_dir = Path("results") / "variants"
    out_dir.mkdir(parents=True, exist_ok=True)
    trades_df.to_csv(out_dir / f"{name}_trades.csv", index=False)
    with open(out_dir / f"{name}_metrics.json", "w", encoding="utf-8") as f:
        json.dump(metrics, f, indent=2)

    yearly = yearly_breakdown(trades_df) if not trades_df.empty else pd.DataFrame()
    seasonal = seasonal_breakdown(trades_df) if not trades_df.empty else pd.DataFrame()
    if not yearly.empty:
        yearly.to_csv(out_dir / f"{name}_yearly.csv", index=False)
    if not seasonal.empty:
        seasonal.to_csv(out_dir / f"{name}_seasonal.csv", index=False)

    return {"name": name, "metrics": metrics, "yearly": yearly, "seasonal": seasonal}


def main() -> None:
    cfg = load_config()
    data_dir = cfg["data"]["dir"]
    symbol = cfg["symbol"]

    print("Loading data and generating signals (once)...")
    df_15m = load_candles(data_dir, symbol, cfg["timeframes"]["ltf"])
    df_1h = load_candles(data_dir, symbol, "1h")
    df_4h = load_candles(data_dir, symbol, "4h")
    df_1d = load_candles(data_dir, symbol, cfg["timeframes"]["htf"])
    funding = load_funding(data_dir, symbol)
    signals = generate_signals(df_15m, df_1h, df_4h, df_1d, funding, cfg)
    print(f"Signals: {len(signals)}\n")

    results = []
    for name, overrides in VARIANTS.items():
        print(f"Running {name}...")
        results.append(run_variant(name, overrides, cfg, signals, df_15m))

    print("\n=== VARIANT COMPARISON ===")
    header = f"{'Variant':<22} {'Trades':>6} {'WR%':>6} {'PF':>6} {'DD%':>7} {'Return%':>9} {'AvgR':>6}"
    print(header)
    print("-" * len(header))
    for r in results:
        m = r["metrics"]
        print(
            f"{r['name']:<22} {m['trades']:>6} {m['win_rate']*100:>5.1f}% "
            f"{m['profit_factor']:>6.2f} {m['max_dd_pct']:>6.1f}% "
            f"{m['total_return_pct']:>8.1f}% {m['avg_r']:>6.3f}"
        )

    best = max(results, key=lambda r: r["metrics"]["total_return_pct"])
    print(f"\nBest by return: {best['name']}")

    print("\n=== YEARLY BREAKDOWN (all variants) ===")
    for r in results:
        if r["yearly"].empty:
            continue
        print(f"\n--- {r['name']} ---")
        print(r["yearly"].to_string(index=False))

    print("\n=== QUARTERLY / SEASONAL (best variant) ===")
    if not best["seasonal"].empty:
        print(best["seasonal"].to_string(index=False))

    # Cross-variant yearly total R comparison
    print("\n=== TOTAL R BY YEAR (cross-variant) ===")
    yearly_pivot = {}
    for r in results:
        if r["yearly"].empty:
            continue
        yearly_pivot[r["name"]] = r["yearly"].set_index("period")["total_r"]
    if yearly_pivot:
        pivot = pd.DataFrame(yearly_pivot).fillna(0)
        print(pivot.to_string())


if __name__ == "__main__":
    main()
