#!/usr/bin/env python3
"""In-sample slim grid + OOS validation of the winner."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import yaml

from ict_bot.config import load_config
from ict_bot.runner import (
    apply_overrides,
    build_grid_scenarios,
    load_market_data,
    run_backtest,
    run_grid,
    save_run_results,
)


def main() -> None:
    parser = argparse.ArgumentParser(description="In-sample grid + out-of-sample validation")
    parser.add_argument("--config", default="config.yaml")
    parser.add_argument("--grid", default="grid_config.yaml")
    args = parser.parse_args()

    grid_cfg = yaml.safe_load(open(args.grid, encoding="utf-8"))
    base_cfg = load_config(args.config)
    out_root = Path(grid_cfg.get("output_dir", "results/grid"))

    print("Step 1: Slim grid on in-sample...")
    in_summary = run_grid(args.config, args.grid, sample_mode="in_sample")
    best = in_summary.iloc[0]
    best_name = best["scenario"]
    print(f"\nBest in-sample scenario: {best_name}")

    overrides = None
    for name, ov in build_grid_scenarios(grid_cfg):
        if name == best_name:
            overrides = ov
            break
    if overrides is None:
        raise RuntimeError(f"Could not find overrides for scenario {best_name}")

    cfg = apply_overrides(base_cfg, overrides)
    df_15m, df_1h, df_4h, df_1d, funding = load_market_data(cfg)

    print("\nStep 2: Out-of-sample validation...")
    trades, equity, metrics, _ = run_backtest(
        cfg, df_15m, df_1h, df_4h, df_1d, funding, sample_mode="out_of_sample"
    )

    oos_dir = out_root / "validation" / best_name / "out_of_sample"
    save_run_results(trades, metrics, equity, oos_dir)

    report = {
        "best_in_sample": best.to_dict(),
        "out_of_sample": metrics,
        "overrides": overrides,
    }
    out_report = out_root / "validation" / best_name / "validation_report.json"
    out_report.parent.mkdir(parents=True, exist_ok=True)
    with open(out_report, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, default=str)

    print(f"\nOOS results saved to {oos_dir}")
    print(metrics)


if __name__ == "__main__":
    main()
