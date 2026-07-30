#!/usr/bin/env python3
"""Quick IS/OOS: fvg_touch vs fvg_touch+limit16 (fees on)."""

from __future__ import annotations

import json
import sys
from copy import deepcopy
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ict_bot.config import load_config
from ict_bot.runner import load_market_data, run_backtest


def cfg_for(name: str, base: dict) -> dict:
    c = deepcopy(base)
    for k in ("volatility", "displacement", "premium_discount", "fibonacci_ote"):
        c.setdefault("filters", {}).setdefault(k, {})["enabled"] = False
    c["fvg"]["entry_at_ce"] = False
    c["fvg"]["limit_valid_bars"] = 16 if "limit16" in name else 8
    c["mtf"]["require_4h"] = True
    return c


def main() -> None:
    base = load_config()
    df_15m, df_1h, df_4h, df_1d, funding = load_market_data(base)
    out = Path("results/final_validation")
    out.mkdir(parents=True, exist_ok=True)
    rows = []

    for name in ("fvg_touch", "fvg_touch_limit16"):
        cfg = cfg_for(name, base)
        print(f"\n=== {name} (signal gen) ===", flush=True)
        _, _, _, signals = run_backtest(cfg, df_15m, df_1h, df_4h, df_1d, funding, sample_mode="full")
        for mode in ("in_sample", "out_of_sample"):
            _, _, m, _ = run_backtest(
                cfg, df_15m, df_1h, df_4h, df_1d, funding, signals=signals, sample_mode=mode
            )
            row = {"variant": name, "sample": mode, **{k: m[k] for k in (
                "trades", "avg_r_per_month", "pct_months_at_goal", "profit_factor",
                "total_return_pct", "max_dd_pct",
            )}}
            rows.append(row)
            print(
                f"  {mode}: trades={m['trades']} avgR/mo={m['avg_r_per_month']:.2f} "
                f"pct5R={m['pct_months_at_goal']:.0%} PF={m['profit_factor']:.2f} "
                f"ret={m['total_return_pct']:.1f}% DD={m['max_dd_pct']:.1f}%",
                flush=True,
            )

    with open(out / "combo_quick.json", "w", encoding="utf-8") as f:
        json.dump(rows, f, indent=2)
    print(f"\nSaved {out / 'combo_quick.json'}", flush=True)


if __name__ == "__main__":
    main()
