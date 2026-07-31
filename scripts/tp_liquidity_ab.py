#!/usr/bin/env python3
"""A/B: TP liquidity sources — round numbers vs swings (OOS)."""

from __future__ import annotations

import json
import sys
from copy import deepcopy
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ict_bot.config import load_config
from ict_bot.runner import load_market_data, run_backtest


VARIANTS = {
    "baseline_rounds": {
        "use_round_numbers": True,
        "use_equal_levels": True,
        "use_swing_levels": False,
    },
    "no_rounds": {
        "use_round_numbers": False,
        "use_equal_levels": True,
        "use_swing_levels": False,
    },
    "swings_only": {
        "use_round_numbers": False,
        "use_equal_levels": False,
        "use_swing_levels": True,
    },
    "swings_no_rounds": {
        "use_round_numbers": False,
        "use_equal_levels": True,
        "use_swing_levels": True,
    },
    "swings_and_rounds": {
        "use_round_numbers": True,
        "use_equal_levels": True,
        "use_swing_levels": True,
    },
}


def apply(cfg: dict, flags: dict) -> dict:
    c = deepcopy(cfg)
    c.setdefault("liquidity", {}).update(flags)
    return c


def main() -> None:
    base = load_config()
    df_15m, df_1h, df_4h, df_1d, funding = load_market_data(base)
    out = Path("results/tp_liquidity_ab")
    out.mkdir(parents=True, exist_ok=True)

    rows = []
    for name, flags in VARIANTS.items():
        print(f"\n=== {name} ===", flush=True)
        cfg = apply(base, flags)
        trades, _, m, _ = run_backtest(
            cfg, df_15m, df_1h, df_4h, df_1d, funding, sample_mode="out_of_sample"
        )
        planned = [t.rr_planned for t in trades]
        avg_planned = sum(planned) / len(planned) if planned else 0
        med_planned = sorted(planned)[len(planned) // 2] if planned else 0
        row = {
            "name": name,
            **flags,
            "trades": m["trades"],
            "win_rate": m["win_rate"],
            "profit_factor": m["profit_factor"],
            "avg_r": m["avg_r"],
            "avg_r_per_month": m["avg_r_per_month"],
            "pct_months_at_goal": m["pct_months_at_goal"],
            "total_return_pct": m["total_return_pct"],
            "max_dd_pct": m["max_dd_pct"],
            "avg_rr_planned": round(avg_planned, 2),
            "median_rr_planned": round(med_planned, 2),
        }
        rows.append(row)
        print(
            f"  trades={m['trades']} avgR/mo={m['avg_r_per_month']:.2f} "
            f"pct5R={m['pct_months_at_goal']:.0%} PF={m['profit_factor']:.2f} "
            f"avg_rr_plan={avg_planned:.2f} med_rr_plan={med_planned:.2f} "
            f"ret={m['total_return_pct']:.1f}%",
            flush=True,
        )

    with open(out / "oos_results.json", "w", encoding="utf-8") as f:
        json.dump(rows, f, indent=2)
    print(f"\nSaved {out / 'oos_results.json'}", flush=True)


if __name__ == "__main__":
    main()
