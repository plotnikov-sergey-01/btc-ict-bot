#!/usr/bin/env python3
"""Run slim grid on in-sample and out-of-sample; rank by robustness."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd
import yaml

from ict_bot.config import load_config
from ict_bot.runner import apply_overrides, build_grid_scenarios, load_market_data, run_backtest


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="config.yaml")
    parser.add_argument("--grid", default="grid_config.yaml")
    args = parser.parse_args()

    grid_cfg = yaml.safe_load(open(args.grid, encoding="utf-8"))
    base_cfg = load_config(args.config)
    scenarios = build_grid_scenarios(grid_cfg)
    df_15m, df_1h, df_4h, df_1d, funding = load_market_data(base_cfg)

    out_root = Path(grid_cfg.get("output_dir", "results/grid"))
    out_root.mkdir(parents=True, exist_ok=True)

    signal_cache: dict[float, list] = {}
    rows = []

    for name, overrides in scenarios:
        cfg = apply_overrides(base_cfg, overrides)
        rr = cfg["risk"]["min_rr"]
        print(f"\n=== {name} ===")

        if rr not in signal_cache:
            print(f"  Generating signals (min_rr={rr})...")
            _, _, _, signals = run_backtest(cfg, df_15m, df_1h, df_4h, df_1d, funding, sample_mode="full")
            signal_cache[rr] = signals
        else:
            signals = signal_cache[rr]

        row = {
            "scenario": name,
            "min_rr": rr,
            "breakeven_at_rr": cfg["trade_management"].get("breakeven_at_rr"),
            "trailing_stop": cfg["trade_management"].get("trailing_stop"),
        }

        for mode in ("in_sample", "out_of_sample"):
            _, _, m, _ = run_backtest(
                cfg, df_15m, df_1h, df_4h, df_1d, funding, signals=signals, sample_mode=mode
            )
            prefix = "is" if mode == "in_sample" else "oos"
            row[f"{prefix}_trades"] = m["trades"]
            row[f"{prefix}_win_rate"] = m["win_rate"]
            row[f"{prefix}_pf"] = m["profit_factor"]
            row[f"{prefix}_return_pct"] = m["total_return_pct"]
            row[f"{prefix}_max_dd_pct"] = m["max_dd_pct"]
            row[f"{prefix}_avg_r"] = m["avg_r"]
            row[f"{prefix}_avg_r_per_month"] = m.get("avg_r_per_month", 0)
            row[f"{prefix}_pct_months_at_goal"] = m.get("pct_months_at_goal", 0)
            print(
                f"  {mode}: trades={m['trades']} PF={m['profit_factor']:.2f} "
                f"avgR/mo={m.get('avg_r_per_month', 0):.2f} return={m['total_return_pct']:.1f}%"
            )

        row["oos_profitable"] = row["oos_return_pct"] > 0 and row["oos_pf"] > 1.0
        row["robust_score"] = (
            float(row["oos_avg_r_per_month"]) * 20
            + float(row["oos_pf"]) * 20
            + float(row["oos_return_pct"]) * 0.2
            - abs(float(row["is_return_pct"]) - float(row["oos_return_pct"])) * 0.03
        )
        rows.append(row)

    df = pd.DataFrame(rows).sort_values("robust_score", ascending=False)
    path = out_root / "is_oos_comparison.csv"
    df.to_csv(path, index=False)
    with open(out_root / "is_oos_comparison.json", "w", encoding="utf-8") as f:
        json.dump(rows, f, indent=2, default=str)

    print("\n=== RANKING ===")
    cols = [
        "scenario",
        "oos_trades",
        "oos_avg_r_per_month",
        "oos_pct_months_at_goal",
        "oos_pf",
        "oos_return_pct",
        "oos_max_dd_pct",
        "robust_score",
    ]
    print(df[cols].to_string(index=False))
    print(f"\nSaved: {path}")
    print(f"Recommended: {df.iloc[0]['scenario']}")


if __name__ == "__main__":
    main()
