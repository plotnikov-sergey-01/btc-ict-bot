from __future__ import annotations

import copy
import itertools
import json
from pathlib import Path
from typing import Any

import pandas as pd
import yaml

from .backtest import compute_metrics, simulate_trades
from .config import load_config
from .data_loader import load_candles, load_funding
from .reports import monthly_breakdown, quarterly_breakdown, yearly_breakdown
from .sample import filter_signals_by_window, filter_trades_by_entry_window, resolve_sample_window, sample_label
from .strategy import generate_signals


def apply_override(cfg: dict, dotted_key: str, value: Any) -> None:
    parts = dotted_key.split(".")
    node = cfg
    for part in parts[:-1]:
        node = node[part]
    node[parts[-1]] = value


def apply_overrides(cfg: dict, overrides: dict[str, Any]) -> dict:
    cfg = copy.deepcopy(cfg)
    for key, value in overrides.items():
        apply_override(cfg, key, value)
    return cfg


def scenario_name(overrides: dict[str, Any]) -> str:
    rr = overrides.get("risk.min_rr", "?")
    be = overrides.get("trade_management.breakeven_at_rr")
    be_str = "noBE" if be is None else f"BE{be}"
    trail = overrides.get("trade_management.trailing_stop", False)
    trail_str = "trail1.5" if trail else "noTrail"
    return f"rr{str(rr).replace('.0','')}_{be_str}_{trail_str}"


def save_run_results(
    trades,
    metrics: dict,
    equity: pd.Series,
    results_dir: str | Path,
) -> None:
    from .backtest import _to_native, save_results as _save_base

    results_dir = Path(results_dir)
    results_dir.mkdir(parents=True, exist_ok=True)

    _save_base(trades, metrics, equity, results_dir)

    yearly = yearly_breakdown(trades)
    quarterly = quarterly_breakdown(trades)
    monthly = monthly_breakdown(trades)

    summary = {
        "metrics": _to_native(metrics),
        "yearly": yearly.to_dict(orient="records"),
        "quarterly": quarterly.to_dict(orient="records"),
        "monthly": monthly.to_dict(orient="records"),
    }
    with open(results_dir / "full_report.json", "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2)


def run_backtest(
    cfg: dict,
    df_15m: pd.DataFrame,
    df_1h: pd.DataFrame,
    df_4h: pd.DataFrame,
    df_1d: pd.DataFrame,
    funding: pd.DataFrame | None,
    signals: list | None = None,
    sample_mode: str | None = None,
) -> tuple[list, pd.Series, dict, list]:
    if signals is None:
        signals = generate_signals(df_15m, df_1h, df_4h, df_1d, funding, cfg)

    all_signals = signals
    start, end, mode = resolve_sample_window(cfg, sample_mode)
    signals = filter_signals_by_window(all_signals, start, end)

    trades, equity = simulate_trades(df_15m, signals, cfg)
    trades = filter_trades_by_entry_window(trades, start, end)

    if start is not None or end is not None:
        initial = cfg["backtest"]["initial_balance"]
        risk_pct = cfg["backtest"]["risk_per_trade_pct"] / 100
        if trades:
            balance = initial
            equity_times = [pd.Timestamp(trades[0].entry_time)]
            equity_vals = [initial]
            for t in trades:
                balance *= 1 + t.pnl_r * risk_pct
                equity_times.append(pd.Timestamp(t.exit_time))
                equity_vals.append(balance)
            equity = pd.Series(equity_vals, index=pd.to_datetime(equity_times, utc=True))
        else:
            equity = pd.Series([initial], index=[start or df_15m.index[0]])

    metrics = compute_metrics(
        trades,
        equity,
        cfg["backtest"]["initial_balance"],
        goal_r_per_month=float(cfg.get("goals", {}).get("min_r_per_month", 5.0)),
    )
    metrics["sample_mode"] = mode
    metrics["sample_window"] = sample_label(start, end, mode)
    return trades, equity, metrics, all_signals


def load_market_data(cfg: dict) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame | None]:
    data_dir = cfg["data"]["dir"]
    symbol = cfg["symbol"]
    df_15m = load_candles(data_dir, symbol, cfg["timeframes"]["ltf"])
    df_1h = load_candles(data_dir, symbol, "1h")
    df_4h = load_candles(data_dir, symbol, "4h")
    df_1d = load_candles(data_dir, symbol, cfg["timeframes"]["htf"])
    funding = load_funding(data_dir, symbol)
    return df_15m, df_1h, df_4h, df_1d, funding


def build_grid_scenarios(grid_cfg: dict) -> list[tuple[str, dict[str, Any]]]:
    """
    Returns list of (name, overrides).
    Prefer explicit `scenarios:` list; fall back to Cartesian `parameters:`.
    """
    baseline = grid_cfg.get("baseline_overrides") or {}
    trailing_defaults = grid_cfg.get("trailing_defaults") or {}

    if "scenarios" in grid_cfg:
        out = []
        for sc in grid_cfg["scenarios"]:
            overrides = {**baseline, **sc.get("overrides", {})}
            if overrides.get("trade_management.trailing_stop"):
                for k, v in trailing_defaults.items():
                    overrides.setdefault(k, v)
            name = sc.get("name") or scenario_name(overrides)
            out.append((name, overrides))
        return out

    params = grid_cfg["parameters"]
    keys = list(params.keys())
    values = [params[k] for k in keys]
    out = []
    for combo in itertools.product(*values):
        overrides = {**baseline, **dict(zip(keys, combo))}
        if overrides.get("trade_management.trailing_stop"):
            for k, v in trailing_defaults.items():
                overrides.setdefault(k, v)
        out.append((scenario_name(overrides), overrides))
    return out


def run_grid(
    base_config_path: str = "config.yaml",
    grid_config_path: str = "grid_config.yaml",
    sample_mode: str | None = None,
) -> pd.DataFrame:
    grid_cfg = yaml.safe_load(open(grid_config_path, encoding="utf-8"))
    base_cfg = load_config(base_config_path)
    _, _, mode = resolve_sample_window(base_cfg, sample_mode)
    out_root = Path(grid_cfg.get("output_dir", "results/grid")) / mode
    out_root.mkdir(parents=True, exist_ok=True)

    scenarios = build_grid_scenarios(grid_cfg)
    df_15m, df_1h, df_4h, df_1d, funding = load_market_data(base_cfg)

    # Cache signals by min_rr (only signal-affecting param in this grid)
    signal_cache: dict[float, list] = {}

    rows = []
    for name, overrides in scenarios:
        cfg = apply_overrides(base_cfg, overrides)
        print(f"\n=== {name} ===")

        rr = cfg["risk"]["min_rr"]
        if rr not in signal_cache:
            print(f"  Generating signals for min_rr={rr}...")
            _, _, _, signals = run_backtest(
                cfg, df_15m, df_1h, df_4h, df_1d, funding, sample_mode=sample_mode or "full"
            )
            signal_cache[rr] = signals
        else:
            signals = signal_cache[rr]
            print(f"  Reusing signals for min_rr={rr} ({len(signals)} signals)")

        trades, equity, metrics, _ = run_backtest(
            cfg, df_15m, df_1h, df_4h, df_1d, funding, signals=signals, sample_mode=sample_mode
        )
        scenario_dir = out_root / name
        save_run_results(trades, metrics, equity, scenario_dir)

        row = {
            "scenario": name,
            "sample_mode": metrics.get("sample_mode"),
            "sample_window": metrics.get("sample_window"),
            "min_rr": rr,
            "breakeven_at_rr": cfg["trade_management"].get("breakeven_at_rr"),
            "trailing_stop": cfg["trade_management"].get("trailing_stop"),
            **metrics,
        }
        rows.append(row)
        print(f"  Return: {metrics['total_return_pct']}% | PF: {metrics['profit_factor']} | Trades: {metrics['trades']}")

    summary = pd.DataFrame(rows).sort_values("total_return_pct", ascending=False)
    summary.to_csv(out_root / "grid_summary.csv", index=False)

    with open(out_root / "grid_summary.json", "w", encoding="utf-8") as f:
        json.dump(rows, f, indent=2)

    print(f"\nGrid complete. Summary: {out_root / 'grid_summary.csv'}")
    return summary
