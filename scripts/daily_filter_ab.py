#!/usr/bin/env python3
"""A/B: require_daily on/off for current ICT (OOS + IS)."""

from __future__ import annotations

import json
import sys
from copy import deepcopy
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ict_bot.config import load_config
from ict_bot.runner import load_market_data, run_backtest


def main() -> None:
    base = load_config()
    df_15m, df_1h, df_4h, df_1d, funding = load_market_data(base)
    out = Path("results/daily_filter_ab")
    out.mkdir(parents=True, exist_ok=True)
    rows = []

    for daily_on in (True, False):
        name = "daily_on" if daily_on else "daily_off"
        print(f"\n=== {name} ===", flush=True)
        cfg = deepcopy(base)
        cfg.setdefault("mtf", {})["require_daily"] = daily_on
        # keep require_4h as in prod
        _, _, _, signals = run_backtest(
            cfg, df_15m, df_1h, df_4h, df_1d, funding, sample_mode="full"
        )
        for mode in ("in_sample", "out_of_sample"):
            _, _, m, _ = run_backtest(
                cfg, df_15m, df_1h, df_4h, df_1d, funding, signals=signals, sample_mode=mode
            )
            row = {
                "variant": name,
                "require_daily": daily_on,
                "sample": mode,
                "signals_full": len(signals),
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

    with open(out / "results.json", "w", encoding="utf-8") as f:
        json.dump(rows, f, indent=2)
    print(f"\nSaved {out / 'results.json'}", flush=True)


if __name__ == "__main__":
    main()
