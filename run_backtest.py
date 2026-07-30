#!/usr/bin/env python3
"""Run ICT strategy backtest and write results/ with yearly + quarterly reports."""

from __future__ import annotations

import argparse

from ict_bot.config import load_config
from ict_bot.runner import load_market_data, run_backtest, save_run_results
from ict_bot.sample import filter_signals_by_window, resolve_sample_window, sample_label


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="config.yaml")
    parser.add_argument("--output", default="results")
    parser.add_argument(
        "--sample",
        choices=["full", "in_sample", "out_of_sample"],
        default=None,
        help="Override backtest.sample.mode from config",
    )
    args = parser.parse_args()

    cfg = load_config(args.config)
    df_15m, df_1h, df_4h, df_1d, funding = load_market_data(cfg)

    start, end, mode = resolve_sample_window(cfg, args.sample)
    print(f"15m bars loaded: {len(df_15m)} ({df_15m.index[0]} -> {df_15m.index[-1]})")
    print(f"Sample mode: {mode} ({sample_label(start, end, mode)})")

    print("Generating signals...")
    trades, equity, metrics, signals = run_backtest(
        cfg, df_15m, df_1h, df_4h, df_1d, funding, sample_mode=args.sample
    )
    print(f"Signals total: {len(signals)}")
    print(f"Signals in window: {len(filter_signals_by_window(signals, start, end))}")

    out = args.output
    if mode != "full":
        out = f"{args.output}/{mode}"

    save_run_results(trades, metrics, equity, out)
    print(f"Results saved to {out}/")
    print("  metrics.json, trades.csv, equity.csv, yearly.csv, quarterly.csv")
    print(metrics)


if __name__ == "__main__":
    main()
