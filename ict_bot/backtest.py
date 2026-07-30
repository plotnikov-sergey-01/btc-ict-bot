from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np
import pandas as pd

from .reports import monthly_breakdown, monthly_r_summary, quarterly_breakdown, yearly_breakdown
from .strategy import Signal
from .trade_management import apply_trailing, precompute_swing_levels


@dataclass
class TradeResult:
    entry_time: str
    exit_time: str
    direction: str
    entry: float
    exit: float
    stop: float
    take_profit: float
    pnl_pct: float
    pnl_r: float
    outcome: str
    rr_planned: float


def _classify_outcome(direction: int, entry: float, exit_price: float, pnl_r: float, trail_active: bool) -> str:
    if pnl_r > 0:
        return "trail_win" if trail_active else "win"
    if pnl_r < 0:
        return "trail_loss" if trail_active else "loss"
    return "breakeven"


def _apply_execution_costs(
    direction: int,
    entry: float,
    exit_price: float,
    initial_stop: float,
    cfg: dict,
) -> tuple[float, float]:
    """Adjust entry/exit for slippage; return (exit_for_pnl, fee_drag_in_r)."""
    bt = cfg["backtest"]
    slip = float(bt.get("slippage_pct", 0) or 0) / 100
    comm = float(bt.get("commission_pct", 0) or 0) / 100

    if direction == 1:
        entry_eff = entry * (1 + slip)
        exit_eff = exit_price * (1 - slip)
    else:
        entry_eff = entry * (1 - slip)
        exit_eff = exit_price * (1 + slip)

    risk_dist = abs(entry_eff - initial_stop)
    if risk_dist <= 0:
        risk_dist = abs(entry - initial_stop)
    if risk_dist <= 0:
        return exit_price, 0.0

    # Round-trip commission on notional ≈ entry; express as R multiples
    fee_r = 2 * comm * entry / risk_dist

    pnl_r = ((exit_eff - entry_eff) / risk_dist) * direction - fee_r
    exit_for_record = exit_price
    return exit_for_record, pnl_r


def simulate_trades(
    df_ltf: pd.DataFrame,
    signals: list[Signal],
    cfg: dict,
) -> tuple[list[TradeResult], pd.Series]:
    bt = cfg["backtest"]
    tm = cfg["trade_management"]
    struct = cfg["structure"]
    balance = bt["initial_balance"]
    equity = [balance]
    equity_times = [df_ltf.index[0]]
    trades: list[TradeResult] = []

    last_sh, last_sl = precompute_swing_levels(
        df_ltf,
        struct["pivot_left"],
        struct["pivot_right"],
    )

    signal_idx = 0
    open_trade = None

    for i in range(len(df_ltf)):
        ts = df_ltf.index[i]
        row = df_ltf.iloc[i]

        if open_trade is not None:
            ot = open_trade
            direction = ot["direction"]
            entry = ot["entry"]

            # Breakeven (optional)
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

            if exit_price is not None:
                risk_pct = bt["risk_per_trade_pct"] / 100
                _, pnl_r = _apply_execution_costs(
                    direction,
                    entry,
                    exit_price,
                    ot["initial_stop"],
                    cfg,
                )
                pnl_pct = pnl_r * risk_pct * 100
                balance *= 1 + pnl_r * risk_pct
                equity.append(balance)
                equity_times.append(ts)

                outcome = _classify_outcome(
                    direction,
                    entry,
                    exit_price,
                    pnl_r,
                    ot.get("trail_active", False),
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
                        pnl_pct=round(pnl_pct, 4),
                        pnl_r=round(pnl_r, 4),
                        outcome=outcome,
                        rr_planned=ot["rr"],
                    )
                )
                open_trade = None

        while open_trade is None and signal_idx < len(signals):
            sig = signals[signal_idx]
            if sig.timestamp > ts:
                break
            if sig.timestamp == ts:
                open_trade = {
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
            signal_idx += 1
            if open_trade is not None:
                break

    equity_curve = pd.Series(equity, index=pd.DatetimeIndex(equity_times, tz="UTC"))
    return trades, equity_curve


def compute_metrics(
    trades: list[TradeResult],
    equity: pd.Series,
    initial_balance: float,
    goal_r_per_month: float = 5.0,
) -> dict:
    if not trades:
        return {
            "trades": 0,
            "win_rate": 0,
            "profit_factor": 0,
            "max_dd_pct": 0,
            "total_return_pct": 0,
            "avg_r": 0,
            "sharpe_approx": 0,
        }

    wins = [t for t in trades if t.pnl_r > 0]
    losses = [t for t in trades if t.pnl_r < 0]
    gross_profit = sum(t.pnl_r for t in wins)
    gross_loss = abs(sum(t.pnl_r for t in losses))

    eq = equity / initial_balance
    rolling_max = eq.cummax()
    dd = (eq - rolling_max) / rolling_max
    max_dd = float(dd.min()) * 100

    returns = eq.pct_change().dropna()
    sharpe = 0.0
    if len(returns) > 1 and returns.std() > 0:
        sharpe = float(returns.mean() / returns.std() * np.sqrt(252 * 24 * 4))

    goal = goal_r_per_month
    monthly_stats = monthly_r_summary(trades, goal_r=goal)

    return {
        "trades": len(trades),
        "win_rate": round(len(wins) / len(trades), 4),
        "profit_factor": round(gross_profit / gross_loss, 4) if gross_loss > 0 else float("inf"),
        "max_dd_pct": round(max_dd, 2),
        "total_return_pct": round(float(equity.iloc[-1] / initial_balance - 1) * 100, 2),
        "avg_r": round(sum(t.pnl_r for t in trades) / len(trades), 4),
        "sharpe_approx": round(sharpe, 4),
        **monthly_stats,
    }


def _to_native(obj):
    if isinstance(obj, (np.floating, np.integer)):
        return obj.item()
    if isinstance(obj, dict):
        return {k: _to_native(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_to_native(v) for v in obj]
    return obj


def save_results(
    trades: list[TradeResult],
    metrics: dict,
    equity: pd.Series,
    results_dir: str | Path = "results",
) -> None:
    results_dir = Path(results_dir)
    results_dir.mkdir(parents=True, exist_ok=True)

    with open(results_dir / "metrics.json", "w", encoding="utf-8") as f:
        json.dump(_to_native(metrics), f, indent=2)

    pd.DataFrame([asdict(t) for t in trades]).to_csv(results_dir / "trades.csv", index=False)
    equity.to_csv(results_dir / "equity.csv", header=["balance"])

    yearly = yearly_breakdown(trades)
    quarterly = quarterly_breakdown(trades)
    monthly = monthly_breakdown(trades)
    if not yearly.empty:
        yearly.to_csv(results_dir / "yearly.csv", index=False)
    if not quarterly.empty:
        quarterly.to_csv(results_dir / "quarterly.csv", index=False)
    if not monthly.empty:
        monthly.to_csv(results_dir / "monthly.csv", index=False)
