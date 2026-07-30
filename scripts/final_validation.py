#!/usr/bin/env python3
"""Final validation: combo fvg_touch+limit16, IS/OOS, fees on vs off."""

from __future__ import annotations

import json
import sys
from copy import deepcopy
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ict_bot.config import load_config
from ict_bot.runner import load_market_data, run_backtest


def variant_cfg(base: dict, name: str) -> dict:
    c = deepcopy(base)
    for k in ("volatility", "displacement", "premium_discount", "fibonacci_ote"):
        c.setdefault("filters", {}).setdefault(k, {})["enabled"] = False

    presets = {
        "baseline_ce": {"entry_at_ce": True, "limit_valid_bars": 8},
        "fvg_touch": {"entry_at_ce": False, "limit_valid_bars": 8},
        "limit16_ce": {"entry_at_ce": True, "limit_valid_bars": 16},
        "fvg_touch_limit16": {"entry_at_ce": False, "limit_valid_bars": 16},
    }
    p = presets[name]
    c["fvg"]["entry_at_ce"] = p["entry_at_ce"]
    c["fvg"]["limit_valid_bars"] = p["limit_valid_bars"]
    c["mtf"]["require_4h"] = True
    return c


def run_one(cfg, df_15m, df_1h, df_4h, df_1d, funding, mode, signals=None):
    return run_backtest(cfg, df_15m, df_1h, df_4h, df_1d, funding, signals=signals, sample_mode=mode)


def main() -> None:
    base = load_config()
    df_15m, df_1h, df_4h, df_1d, funding = load_market_data(base)
    out = Path("results/final_validation")
    out.mkdir(parents=True, exist_ok=True)

    variants = ["baseline_ce", "fvg_touch", "limit16_ce", "fvg_touch_limit16"]
    rows = []

    for vname in variants:
        cfg = variant_cfg(base, vname)
        print(f"\n======== {vname} ========")
        _, _, _, signals = run_one(cfg, df_15m, df_1h, df_4h, df_1d, funding, "full")

        for fees_label, fees_on in (("fees_on", True), ("fees_off", False)):
            c = deepcopy(cfg)
            if not fees_on:
                c["backtest"]["commission_pct"] = 0
                c["backtest"]["slippage_pct"] = 0

            for mode in ("in_sample", "out_of_sample"):
                _, _, m, _ = run_one(
                    c, df_15m, df_1h, df_4h, df_1d, funding, mode, signals=signals
                )
                row = {
                    "variant": vname,
                    "fees": fees_label,
                    "sample": mode,
                    "signals": len(signals),
                    **{k: m[k] for k in (
                        "trades", "win_rate", "profit_factor", "total_return_pct",
                        "max_dd_pct", "avg_r", "avg_r_per_month", "median_r_per_month",
                        "pct_months_at_goal",
                    )},
                }
                rows.append(row)
                print(
                    f"  {fees_label} {mode}: trades={m['trades']} avgR/mo={m['avg_r_per_month']:.2f} "
                    f"pct5R={m['pct_months_at_goal']:.0%} PF={m['profit_factor']:.2f} "
                    f"ret={m['total_return_pct']:.1f}% DD={m['max_dd_pct']:.1f}%"
                )

    with open(out / "results.json", "w", encoding="utf-8") as f:
        json.dump(rows, f, indent=2)

    print("\n=== OOS with fees (prod-like) ===")
    for r in rows:
        if r["fees"] == "fees_on" and r["sample"] == "out_of_sample":
            print(
                f"{r['variant']:20} trades={r['trades']:3} avgR/mo={r['avg_r_per_month']:6.2f} "
                f"pct5R={r['pct_months_at_goal']:.0%} PF={r['profit_factor']:.2f} ret={r['total_return_pct']:.1f}%"
            )
    print(f"\nSaved: {out / 'results.json'}")

    # Recommend best OOS fees_on by avg_r_per_month
    oos = [r for r in rows if r["fees"] == "fees_on" and r["sample"] == "out_of_sample"]
    best = max(oos, key=lambda x: x["avg_r_per_month"])
    print(f"\nRecommended prod baseline (OOS, fees on): {best['variant']}")


if __name__ == "__main__":
    main()
