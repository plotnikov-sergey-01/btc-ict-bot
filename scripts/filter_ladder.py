"""A/B: vol+P/D, +disp@1.2, +Fib OTE — IS and OOS."""

from __future__ import annotations

import json
import sys
from copy import deepcopy
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ict_bot.config import load_config
from ict_bot.runner import load_market_data, run_backtest


def apply_filters(cfg, vol, pd_, disp, disp_mult, fib):
    c = deepcopy(cfg)
    c["filters"]["volatility"]["enabled"] = vol
    c["filters"]["premium_discount"]["enabled"] = pd_
    c["filters"]["displacement"]["enabled"] = disp
    c["filters"]["displacement"]["min_body_atr_mult"] = disp_mult
    c["filters"]["fibonacci_ote"]["enabled"] = fib
    return c


SETS = [
    ("baseline_no_filters", False, False, False, 1.2, False),
    ("vol_pd", True, True, False, 1.2, False),
    ("vol_pd_disp1.2", True, True, True, 1.2, False),
    ("vol_pd_disp1.2_fib", True, True, True, 1.2, True),
]


def main() -> None:
    cfg = load_config()
    df_15m, df_1h, df_4h, df_1d, funding = load_market_data(cfg)
    out = Path("results/filter_ab")
    out.mkdir(parents=True, exist_ok=True)

    rows = []
    for name, vol, pd_, disp, mult, fib in SETS:
        print(f"\n=== {name} ===")
        c = apply_filters(cfg, vol, pd_, disp, mult, fib)
        # generate signals once per filter set
        _, _, _, signals = run_backtest(c, df_15m, df_1h, df_4h, df_1d, funding, sample_mode="full")
        row = {"name": name, "signals_total": len(signals)}
        for mode in ("in_sample", "out_of_sample"):
            _, _, m, _ = run_backtest(
                c, df_15m, df_1h, df_4h, df_1d, funding, signals=signals, sample_mode=mode
            )
            prefix = "is" if mode == "in_sample" else "oos"
            row[f"{prefix}_trades"] = m["trades"]
            row[f"{prefix}_pf"] = m["profit_factor"]
            row[f"{prefix}_return_pct"] = m["total_return_pct"]
            row[f"{prefix}_max_dd_pct"] = m["max_dd_pct"]
            row[f"{prefix}_avg_r_per_month"] = m["avg_r_per_month"]
            row[f"{prefix}_median_r_per_month"] = m["median_r_per_month"]
            row[f"{prefix}_pct_months_at_goal"] = m["pct_months_at_goal"]
            row[f"{prefix}_months_below_goal"] = m["months_below_goal"]
            print(
                f"  {mode}: trades={m['trades']} PF={m['profit_factor']:.2f} "
                f"avgR/mo={m['avg_r_per_month']:.2f} pct>=5R={m['pct_months_at_goal']:.0%} "
                f"ret={m['total_return_pct']:.1f}%"
            )
        rows.append(row)

    with open(out / "filter_ladder.json", "w", encoding="utf-8") as f:
        json.dump(rows, f, indent=2)

    print("\n=== SUMMARY (OOS) ===")
    for r in rows:
        print(
            f"{r['name']:22} trades={r['oos_trades']:3} "
            f"avgR/mo={r['oos_avg_r_per_month']:6.2f} "
            f"pct5R={r['oos_pct_months_at_goal']:.0%} "
            f"PF={r['oos_pf']:.2f} ret={r['oos_return_pct']:.1f}%"
        )
    print(f"\nSaved: {out / 'filter_ladder.json'}")


if __name__ == "__main__":
    main()
