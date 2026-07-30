#!/usr/bin/env python3
"""OOS only: fvg_touch limit 8 vs 16 (fees from config)."""

from __future__ import annotations

import sys
from copy import deepcopy
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ict_bot.config import load_config
from ict_bot.runner import load_market_data, run_backtest


def run(bars: int) -> None:
    base = load_config()
    c = deepcopy(base)
    c["fvg"]["entry_at_ce"] = False
    c["fvg"]["limit_valid_bars"] = bars
    df_15m, df_1h, df_4h, df_1d, funding = load_market_data(base)
    print(f"generating signals limit={bars}...", flush=True)
    _, _, _, sig = run_backtest(c, df_15m, df_1h, df_4h, df_1d, funding, sample_mode="full")
    _, _, m, _ = run_backtest(
        c, df_15m, df_1h, df_4h, df_1d, funding, signals=sig, sample_mode="out_of_sample"
    )
    print(
        f"limit{bars} OOS: trades={m['trades']} avgR/mo={m['avg_r_per_month']:.2f} "
        f"pct5R={m['pct_months_at_goal']:.0%} PF={m['profit_factor']:.2f} "
        f"ret={m['total_return_pct']:.1f}% DD={m['max_dd_pct']:.1f}%",
        flush=True,
    )


if __name__ == "__main__":
    for b in (8, 16):
        run(b)
