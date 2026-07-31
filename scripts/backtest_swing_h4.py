#!/usr/bin/env python3
"""OOS (+IS) backtest: swing confirm/retest + 4H/1H (no daily)."""

from __future__ import annotations

import json
import sys
from copy import deepcopy
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ict_bot.config import load_config
from ict_bot.runner import load_market_data, run_backtest
from ict_bot.strategies.swing_h4 import generate_swing_h4_signals
from ict_bot.strategy import generate_signals as generate_ict_signals

OUT = Path("results/swing_h4_backtest")


def prep_cfg(base: dict) -> dict:
    """Same risk/trail/fees/session as prod ICT; no FVG path — signals prebuilt."""
    c = deepcopy(base)
    # Round TP not used (fixed 2R in signal gen); keep trail like ICT
    return c


def run_variant(name: str, signals, cfg, df_15m, df_1h, df_4h, df_1d, funding, mode: str) -> dict:
    trades, _, m, _ = run_backtest(
        cfg, df_15m, df_1h, df_4h, df_1d, funding, signals=signals, sample_mode=mode
    )
    row = {
        "variant": name,
        "sample": mode,
        "signals_in_window": len([s for s in signals]),  # filtered inside run_backtest
        "trades": m["trades"],
        "win_rate": m["win_rate"],
        "profit_factor": m["profit_factor"],
        "avg_r": m["avg_r"],
        "avg_r_per_month": m["avg_r_per_month"],
        "median_r_per_month": m.get("median_r_per_month"),
        "pct_months_at_goal": m["pct_months_at_goal"],
        "total_return_pct": m["total_return_pct"],
        "max_dd_pct": m["max_dd_pct"],
    }
    print(
        f"  {mode}: trades={m['trades']} avgR/mo={m['avg_r_per_month']:.2f} "
        f"pct5R={m['pct_months_at_goal']:.0%} PF={m['profit_factor']:.2f} "
        f"avgR={m['avg_r']:.3f} ret={m['total_return_pct']:.1f}% DD={m['max_dd_pct']:.1f}%",
        flush=True,
    )
    return row


def main() -> None:
    base = load_config()
    cfg = prep_cfg(base)
    df_15m, df_1h, df_4h, df_1d, funding = load_market_data(cfg)
    OUT.mkdir(parents=True, exist_ok=True)
    rows = []

    variants = [
        ("swing_confirm_h4_1h", "confirm"),
        ("swing_retest_h4_1h", "retest"),
    ]

    for name, mode in variants:
        print(f"\n=== {name} (gen signals) ===", flush=True)
        signals = generate_swing_h4_signals(
            df_15m, df_1h, df_4h, cfg, entry_mode=mode
        )
        print(f"  signals total: {len(signals)}", flush=True)
        for sample in ("in_sample", "out_of_sample"):
            rows.append(
                run_variant(name, signals, cfg, df_15m, df_1h, df_4h, df_1d, funding, sample)
            )

    # Reference: current ICT prod config
    print("\n=== ict_fvg (reference) ===", flush=True)
    ict_signals = generate_ict_signals(df_15m, df_1h, df_4h, df_1d, funding, cfg)
    print(f"  signals total: {len(ict_signals)}", flush=True)
    for sample in ("in_sample", "out_of_sample"):
        rows.append(
            run_variant("ict_fvg_ref", ict_signals, cfg, df_15m, df_1h, df_4h, df_1d, funding, sample)
        )

    with open(OUT / "results.json", "w", encoding="utf-8") as f:
        json.dump(rows, f, indent=2)
    print(f"\nSaved -> {OUT / 'results.json'}", flush=True)


if __name__ == "__main__":
    main()
