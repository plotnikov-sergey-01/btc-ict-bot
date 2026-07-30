from __future__ import annotations

from typing import Any

import pandas as pd


def trades_to_dataframe(trades: list[Any]) -> pd.DataFrame:
    if not trades:
        return pd.DataFrame()
    df = pd.DataFrame([t.__dict__ for t in trades])
    df["entry_time"] = pd.to_datetime(df["entry_time"], utc=True)
    df["exit_time"] = pd.to_datetime(df["exit_time"], utc=True)
    return df


def _period_stats(grp: pd.DataFrame) -> dict:
    wins = grp[grp["pnl_r"] > 0]
    losses = grp[grp["pnl_r"] < 0]
    gp = float(wins["pnl_r"].sum())
    gl = abs(float(losses["pnl_r"].sum()))
    return {
        "trades": len(grp),
        "win_rate": round(len(wins) / len(grp), 4) if len(grp) else 0,
        "profit_factor": round(gp / gl, 4) if gl > 0 else None,
        "total_r": round(float(grp["pnl_r"].sum()), 4),
        "avg_r": round(float(grp["pnl_r"].mean()), 4) if len(grp) else 0,
    }


def yearly_breakdown(trades: list[Any]) -> pd.DataFrame:
    df = trades_to_dataframe(trades)
    if df.empty:
        return pd.DataFrame()

    rows = []
    for year, grp in df.groupby(df["entry_time"].dt.year):
        row = _period_stats(grp)
        row["period"] = str(year)
        rows.append(row)
    return pd.DataFrame(rows)[["period", "trades", "win_rate", "profit_factor", "total_r", "avg_r"]]


def monthly_breakdown(trades: list[Any]) -> pd.DataFrame:
    df = trades_to_dataframe(trades)
    if df.empty:
        return pd.DataFrame()

    months = df["entry_time"].dt.to_period("M").astype(str)
    rows = []
    for period, grp in df.groupby(months):
        row = _period_stats(grp)
        row["period"] = period
        row["meets_5r_goal"] = row["total_r"] >= 5.0
        rows.append(row)
    return pd.DataFrame(rows)[
        [
            "period",
            "trades",
            "win_rate",
            "profit_factor",
            "total_r",
            "avg_r",
            "meets_5r_goal",
        ]
    ]


def monthly_r_summary(trades: list[Any], goal_r: float = 5.0) -> dict:
    monthly = monthly_breakdown(trades)
    if monthly.empty:
        return {
            "goal_r_per_month": goal_r,
            "months": 0,
            "avg_r_per_month": 0,
            "median_r_per_month": 0,
            "pct_months_at_goal": 0,
            "months_below_goal": 0,
        }
    return {
        "goal_r_per_month": goal_r,
        "months": len(monthly),
        "avg_r_per_month": round(float(monthly["total_r"].mean()), 4),
        "median_r_per_month": round(float(monthly["total_r"].median()), 4),
        "pct_months_at_goal": round(float((monthly["total_r"] >= goal_r).mean()), 4),
        "months_below_goal": int((monthly["total_r"] < goal_r).sum()),
    }


def quarterly_breakdown(trades: list[Any]) -> pd.DataFrame:
    df = trades_to_dataframe(trades)
    if df.empty:
        return pd.DataFrame()

    quarters = df["entry_time"].dt.tz_convert("UTC").dt.to_period("Q").astype(str)
    rows = []
    for period, grp in df.groupby(quarters):
        row = _period_stats(grp)
        row["period"] = period
        rows.append(row)
    return pd.DataFrame(rows)[["period", "trades", "win_rate", "profit_factor", "total_r", "avg_r"]]
