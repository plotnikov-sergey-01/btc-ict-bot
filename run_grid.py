#!/usr/bin/env python3
"""Run all parameter grid combinations (see grid_config.yaml)."""

from __future__ import annotations

import argparse

from ict_bot.runner import run_grid


def main() -> None:
    parser = argparse.ArgumentParser(description="Run backtest grid search")
    parser.add_argument("--config", default="config.yaml")
    parser.add_argument("--grid", default="grid_config.yaml")
    parser.add_argument(
        "--sample",
        choices=["full", "in_sample", "out_of_sample"],
        default="in_sample",
        help="Which date window to score (default: in_sample for optimization)",
    )
    args = parser.parse_args()

    summary = run_grid(args.config, args.grid, sample_mode=args.sample)
    print(f"\n=== GRID RANKING ({args.sample}) ===")
    print(
        summary[
            [
                "scenario",
                "sample_window",
                "min_rr",
                "breakeven_at_rr",
                "trailing_stop",
                "trades",
                "win_rate",
                "profit_factor",
                "max_dd_pct",
                "total_return_pct",
            ]
        ].to_string(index=False)
    )


if __name__ == "__main__":
    main()
