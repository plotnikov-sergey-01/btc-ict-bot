#!/usr/bin/env python3
"""A/B: exit style — liquidity TP (prod) vs fixed 1R/2R, with/without trail (OOS + IS)."""

from __future__ import annotations

import json
import sys
from copy import deepcopy
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ict_bot.config import load_config
from ict_bot.runner import apply_overrides, load_market_data, run_backtest

# liquidity on + min_rr → TP at nearest liquidity ≥ min_rr (prod-like)
# liquidity off + min_rr → TP fixed at exactly min_rr * risk
VARIANTS: list[tuple[str, dict]] = [
    (
        "prod_liq_rr2_trail1.5",
        {
            "liquidity.enabled": True,
            "risk.min_rr": 2.0,
            "trade_management.trailing_stop": True,
            "trade_management.trail_activate_at_rr": 1.5,
            "trade_management.extend_tp_on_trail": False,
        },
    ),
    (
        "prod_liq_rr2_no_trail",
        {
            "liquidity.enabled": True,
            "risk.min_rr": 2.0,
            "trade_management.trailing_stop": False,
        },
    ),
    (
        "fixed_1r_no_trail",
        {
            "liquidity.enabled": False,
            "risk.min_rr": 1.0,
            "trade_management.trailing_stop": False,
        },
    ),
    (
        "fixed_1r_trail1.0",
        {
            "liquidity.enabled": False,
            "risk.min_rr": 1.0,
            "trade_management.trailing_stop": True,
            "trade_management.trail_activate_at_rr": 1.0,
            "trade_management.extend_tp_on_trail": False,
        },
    ),
    (
        "fixed_1r_trail1.5",
        {
            "liquidity.enabled": False,
            "risk.min_rr": 1.0,
            "trade_management.trailing_stop": True,
            "trade_management.trail_activate_at_rr": 1.5,
            "trade_management.extend_tp_on_trail": False,
        },
    ),
    (
        "fixed_2r_no_trail",
        {
            "liquidity.enabled": False,
            "risk.min_rr": 2.0,
            "trade_management.trailing_stop": False,
        },
    ),
    (
        "fixed_2r_trail1.5",
        {
            "liquidity.enabled": False,
            "risk.min_rr": 2.0,
            "trade_management.trailing_stop": True,
            "trade_management.trail_activate_at_rr": 1.5,
            "trade_management.extend_tp_on_trail": False,
        },
    ),
]


def main() -> None:
    base = load_config()
    df_15m, df_1h, df_4h, df_1d, funding = load_market_data(base)
    out = Path("results/tp_exit_ab")
    out.mkdir(parents=True, exist_ok=True)

    signal_cache: dict[tuple[bool, float], list] = {}
    rows: list[dict] = []

    for name, overrides in VARIANTS:
        cfg = apply_overrides(base, overrides)
        liq = bool(cfg["liquidity"]["enabled"])
        rr = float(cfg["risk"]["min_rr"])
        key = (liq, rr)
        if key not in signal_cache:
            print(f"\n=== signals min_rr={rr} liq={liq} ===", flush=True)
            _, _, _, signals = run_backtest(
                cfg, df_15m, df_1h, df_4h, df_1d, funding, sample_mode="full"
            )
            signal_cache[key] = signals
        else:
            signals = signal_cache[key]
            print(f"\n=== {name} (reuse signals n={len(signals)}) ===", flush=True)

        for mode in ("in_sample", "out_of_sample"):
            trades, _, m, _ = run_backtest(
                cfg, df_15m, df_1h, df_4h, df_1d, funding, signals=signals, sample_mode=mode
            )
            planned = [t.rr_planned for t in trades]
            durations_h = []
            for t in trades:
                et = __import__("pandas").Timestamp(t.entry_time)
                xt = __import__("pandas").Timestamp(t.exit_time)
                durations_h.append((xt - et).total_seconds() / 3600)
            med_h = sorted(durations_h)[len(durations_h) // 2] if durations_h else 0

            row = {
                "variant": name,
                "sample": mode,
                "liquidity": liq,
                "min_rr": rr,
                "trail": cfg["trade_management"].get("trailing_stop"),
                "trail_at": cfg["trade_management"].get("trail_activate_at_rr"),
                "signals_full": len(signals),
                "trades": m["trades"],
                "win_rate": m["win_rate"],
                "profit_factor": m["profit_factor"],
                "avg_r": m["avg_r"],
                "avg_r_per_month": m["avg_r_per_month"],
                "pct_months_at_goal": m["pct_months_at_goal"],
                "total_return_pct": m["total_return_pct"],
                "max_dd_pct": m["max_dd_pct"],
                "median_hold_hours": round(med_h, 1),
                "avg_rr_planned": round(sum(planned) / len(planned), 2) if planned else 0,
            }
            rows.append(row)
            print(
                f"  {mode}: trades={m['trades']} avgR/mo={m['avg_r_per_month']:.2f} "
                f"pct5R={m['pct_months_at_goal']:.0%} PF={m['profit_factor']:.2f} "
                f"WR={m['win_rate']:.0%} med_hold={med_h:.1f}h ret={m['total_return_pct']:.1f}%",
                flush=True,
            )

    with open(out / "results.json", "w", encoding="utf-8") as f:
        json.dump(rows, f, indent=2)
    print(f"\nSaved {out / 'results.json'}", flush=True)


if __name__ == "__main__":
    main()
