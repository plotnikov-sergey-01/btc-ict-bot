#!/usr/bin/env python3
"""Three entry-widen A/B tests vs baseline (OOS only)."""

from __future__ import annotations

import json
import sys
from copy import deepcopy
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ict_bot.config import load_config
from ict_bot.runner import load_market_data, run_backtest


def apply_variant(cfg: dict, name: str) -> dict:
    c = deepcopy(cfg)
    # Ensure quality filters off
    for k in ("volatility", "displacement", "premium_discount", "fibonacci_ote"):
        c.setdefault("filters", {}).setdefault(k, {})["enabled"] = False

    if name == "baseline":
        c["fvg"]["limit_valid_bars"] = 8
        c["fvg"]["entry_at_ce"] = True
        c["mtf"]["require_4h"] = True
    elif name == "limit16":
        c["fvg"]["limit_valid_bars"] = 16
        c["fvg"]["entry_at_ce"] = True
        c["mtf"]["require_4h"] = True
    elif name == "soft_mtf":
        c["fvg"]["limit_valid_bars"] = 8
        c["fvg"]["entry_at_ce"] = True
        c["mtf"]["require_4h"] = False
    elif name == "fvg_touch":
        c["fvg"]["limit_valid_bars"] = 8
        c["fvg"]["entry_at_ce"] = False
        c["mtf"]["require_4h"] = True
    else:
        raise ValueError(name)
    return c


VARIANTS = ["baseline", "limit16", "soft_mtf", "fvg_touch"]


def main() -> None:
    base = load_config()
    df_15m, df_1h, df_4h, df_1d, funding = load_market_data(base)
    out = Path("results/entry_widen")
    out.mkdir(parents=True, exist_ok=True)

    rows = []
    for name in VARIANTS:
        print(f"\n=== {name} ===")
        cfg = apply_variant(base, name)
        _, _, m, s = run_backtest(
            cfg, df_15m, df_1h, df_4h, df_1d, funding, sample_mode="out_of_sample"
        )
        row = {
            "name": name,
            "signals": len(s),
            "trades": m["trades"],
            "win_rate": m["win_rate"],
            "profit_factor": m["profit_factor"],
            "total_return_pct": m["total_return_pct"],
            "max_dd_pct": m["max_dd_pct"],
            "avg_r": m["avg_r"],
            "avg_r_per_month": m["avg_r_per_month"],
            "median_r_per_month": m["median_r_per_month"],
            "pct_months_at_goal": m["pct_months_at_goal"],
        }
        rows.append(row)
        print(
            f"  trades={m['trades']} avgR/mo={m['avg_r_per_month']:.2f} "
            f"pct5R={m['pct_months_at_goal']:.0%} PF={m['profit_factor']:.2f} "
            f"ret={m['total_return_pct']:.1f}% DD={m['max_dd_pct']:.1f}%"
        )

    with open(out / "oos_results.json", "w", encoding="utf-8") as f:
        json.dump(rows, f, indent=2)

    print("\n=== SUMMARY (OOS) ===")
    base_r = rows[0]["avg_r_per_month"]
    for r in rows:
        delta = r["avg_r_per_month"] - base_r
        print(
            f"{r['name']:12} trades={r['trades']:3} avgR/mo={r['avg_r_per_month']:6.2f} "
            f"({delta:+.2f}) pct5R={r['pct_months_at_goal']:.0%} "
            f"PF={r['profit_factor']:.2f} ret={r['total_return_pct']:.1f}%"
        )
    print(f"\nSaved: {out / 'oos_results.json'}")


if __name__ == "__main__":
    main()
