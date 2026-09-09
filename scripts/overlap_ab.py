#!/usr/bin/env python3
"""A/B: one open position vs allow concurrent adds (daily on/off, OOS+IS).

max_open=1  — current prod (skip signals while in a trade)
max_open=N  — open up to N independent tickets, each risking risk_per_trade_pct
              of balance at entry (correlated risk in a dump).
"""

from __future__ import annotations

import json
import sys
from copy import deepcopy
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ict_bot.backtest import TradeResult, _apply_execution_costs, _classify_outcome, compute_metrics
from ict_bot.config import load_config
from ict_bot.runner import load_market_data, run_backtest
from ict_bot.sample import filter_signals_by_window, resolve_sample_window
from ict_bot.trade_management import apply_trailing, precompute_swing_levels


def simulate_multi(
    df_ltf: pd.DataFrame,
    signals: list,
    cfg: dict,
    *,
    max_open: int = 1,
    same_direction_only: bool = True,
) -> tuple[list[TradeResult], pd.Series, dict]:
    """Independent concurrent tickets; each risks risk_per_trade_pct of current balance."""
    bt = cfg["backtest"]
    tm = cfg["trade_management"]
    struct = cfg["structure"]
    balance = float(bt["initial_balance"])
    risk_pct = float(bt["risk_per_trade_pct"]) / 100
    equity = [balance]
    equity_times = [df_ltf.index[0]]
    trades: list[TradeResult] = []
    open_trades: list[dict] = []
    skipped_busy = 0
    skipped_dir = 0
    opened = 0

    last_sh, last_sl = precompute_swing_levels(
        df_ltf, struct["pivot_left"], struct["pivot_right"]
    )

    # index signals by bar timestamp for O(1) lookup
    by_ts: dict = {}
    for sig in signals:
        by_ts.setdefault(sig.timestamp, []).append(sig)

    for i in range(len(df_ltf)):
        ts = df_ltf.index[i]
        row = df_ltf.iloc[i]

        # Manage exits (copy list — may shrink)
        still_open: list[dict] = []
        for ot in open_trades:
            direction = ot["direction"]
            entry = ot["entry"]
            be_rr = tm.get("breakeven_at_rr")
            risk = abs(entry - ot["initial_stop"])
            if be_rr is not None and not ot.get("be_moved"):
                if direction == 1 and row["high"] >= entry + risk * be_rr:
                    ot["stop"] = entry
                    ot["be_moved"] = True
                elif direction == -1 and row["low"] <= entry - risk * be_rr:
                    ot["stop"] = entry
                    ot["be_moved"] = True

            apply_trailing(ot, row, i, ts, df_ltf, last_sh, last_sl, cfg)

            stop = ot["stop"]
            tp = ot["take_profit"]
            exit_price = None
            if direction == 1:
                if row["low"] <= stop:
                    exit_price = stop
                elif row["high"] >= tp:
                    exit_price = tp
            else:
                if row["high"] >= stop:
                    exit_price = stop
                elif row["low"] <= tp:
                    exit_price = tp

            if exit_price is None:
                still_open.append(ot)
                continue

            _, pnl_r = _apply_execution_costs(
                direction, entry, exit_price, ot["initial_stop"], cfg
            )
            balance *= 1 + pnl_r * risk_pct
            equity.append(balance)
            equity_times.append(ts)
            outcome = _classify_outcome(
                direction, entry, exit_price, pnl_r, ot.get("trail_active", False)
            )
            if ot.get("be_moved") and abs(exit_price - entry) < 1e-9:
                outcome = "breakeven"
            trades.append(
                TradeResult(
                    entry_time=ot["entry_time"].isoformat(),
                    exit_time=ts.isoformat(),
                    direction="long" if direction == 1 else "short",
                    entry=entry,
                    exit=exit_price,
                    stop=ot["initial_stop"],
                    take_profit=tp,
                    pnl_pct=round(pnl_r * risk_pct * 100, 4),
                    pnl_r=round(pnl_r, 4),
                    outcome=outcome,
                    rr_planned=ot["rr"],
                )
            )
        open_trades = still_open

        # New entries this bar
        for sig in by_ts.get(ts, []):
            if len(open_trades) >= max_open:
                skipped_busy += 1
                continue
            if same_direction_only and open_trades:
                if any(ot["direction"] != sig.direction for ot in open_trades):
                    skipped_dir += 1
                    continue
                # same direction → allow add
            open_trades.append(
                {
                    "direction": sig.direction,
                    "entry": sig.entry,
                    "stop": sig.stop,
                    "initial_stop": sig.stop,
                    "take_profit": sig.take_profit,
                    "rr": sig.rr,
                    "entry_time": ts,
                    "be_moved": False,
                    "trail_active": False,
                }
            )
            opened += 1

    equity_curve = pd.Series(equity, index=pd.DatetimeIndex(equity_times, tz="UTC"))
    meta = {
        "opened": opened,
        "skipped_busy": skipped_busy,
        "skipped_dir": skipped_dir,
        "max_concurrent_seen": None,  # filled below from trades overlap if needed
    }
    return trades, equity_curve, meta


def peak_concurrent(trades: list[TradeResult]) -> int:
    if not trades:
        return 0
    events: list[tuple] = []
    for t in trades:
        events.append((pd.Timestamp(t.entry_time), 1))
        events.append((pd.Timestamp(t.exit_time), -1))
    events.sort(key=lambda x: (x[0], -x[1]))  # entries before exits at same ts
    cur = peak = 0
    for _, d in events:
        cur += d
        peak = max(peak, cur)
    return peak


def _slice_ltf(df: pd.DataFrame, start, end) -> pd.DataFrame:
    """Bars from window start (with small warmup) through end — much faster multi-sim."""
    out = df
    if start is not None:
        warm = pd.Timestamp(start) - pd.Timedelta(days=5)
        out = out[out.index >= warm]
    if end is not None:
        out = out[out.index <= pd.Timestamp(end)]
    return out


def main() -> None:
    base = load_config()
    df_15m, df_1h, df_4h, df_1d, funding = load_market_data(base)
    out = Path("results/overlap_ab")
    out.mkdir(parents=True, exist_ok=True)

    # OOS-first (fast enough for a clear answer); IS optional via --is
    modes = ["out_of_sample"]
    if "--is" in sys.argv:
        modes = ["in_sample", "out_of_sample"]

    variants = [
        ("single_daily_on", True, 1),
        ("single_daily_off", False, 1),
        ("overlap3_daily_on", True, 3),
        ("overlap3_daily_off", False, 3),
        ("overlap5_daily_on", True, 5),
        ("overlap5_daily_off", False, 5),
    ]

    signal_cache: dict[bool, list] = {}
    rows = []

    for name, daily, max_open in variants:
        cfg = deepcopy(base)
        cfg.setdefault("mtf", {})["require_daily"] = daily
        if daily not in signal_cache:
            print(f"\n=== generate signals daily={daily} ===", flush=True)
            _, _, _, sigs = run_backtest(
                cfg, df_15m, df_1h, df_4h, df_1d, funding, sample_mode="full"
            )
            signal_cache[daily] = sigs
        else:
            sigs = signal_cache[daily]
            print(f"\n=== {name} (reuse signals n={len(sigs)}) ===", flush=True)

        for mode in modes:
            start, end, _ = resolve_sample_window(cfg, mode)
            win_sigs = filter_signals_by_window(sigs, start, end)
            df_win = _slice_ltf(df_15m, start, end)
            print(f"  sim {mode} bars={len(df_win)} sigs={len(win_sigs)} max_open={max_open}...", flush=True)
            trades, _, meta = simulate_multi(
                df_win, win_sigs, cfg, max_open=max_open, same_direction_only=True
            )
            # Rebuild equity from window trades only (fair DD/return vs full-history curve)
            initial = float(cfg["backtest"]["initial_balance"])
            risk_pct = float(cfg["backtest"]["risk_per_trade_pct"]) / 100
            if trades:
                bal = initial
                eq_t = [pd.Timestamp(trades[0].entry_time)]
                eq_v = [initial]
                for t in trades:
                    bal *= 1 + t.pnl_r * risk_pct
                    eq_t.append(pd.Timestamp(t.exit_time))
                    eq_v.append(bal)
                equity = pd.Series(eq_v, index=pd.to_datetime(eq_t, utc=True))
            else:
                equity = pd.Series([initial], index=[start or df_15m.index[0]])
            m = compute_metrics(
                trades,
                equity,
                initial,
                goal_r_per_month=float(cfg.get("goals", {}).get("min_r_per_month", 5.0)),
            )
            peak = peak_concurrent(trades)
            wins = sum(1 for t in trades if t.pnl_r > 0)
            losses = sum(1 for t in trades if t.pnl_r < 0)
            row = {
                "variant": name,
                "require_daily": daily,
                "max_open": max_open,
                "sample": mode,
                "signals_in_window": len(win_sigs),
                "trades": m["trades"],
                "opened": meta["opened"],
                "skipped_busy": meta["skipped_busy"],
                "peak_concurrent": peak,
                "win_rate": m["win_rate"],
                "profit_factor": m["profit_factor"],
                "avg_r": m["avg_r"],
                "avg_r_per_month": m["avg_r_per_month"],
                "pct_months_at_goal": m["pct_months_at_goal"],
                "total_return_pct": m["total_return_pct"],
                "max_dd_pct": m["max_dd_pct"],
                "wins": wins,
                "losses": losses,
            }
            rows.append(row)
            print(
                f"  {mode}: trades={m['trades']} skip_busy={meta['skipped_busy']} "
                f"peak={peak} avgR/mo={m['avg_r_per_month']:.2f} "
                f"PF={m['profit_factor']:.2f} DD={m['max_dd_pct']:.1f}% "
                f"ret={m['total_return_pct']:.1f}%",
                flush=True,
            )

    with open(out / "results.json", "w", encoding="utf-8") as f:
        json.dump(rows, f, indent=2)
    print(f"\nSaved {out / 'results.json'}", flush=True)


if __name__ == "__main__":
    main()
