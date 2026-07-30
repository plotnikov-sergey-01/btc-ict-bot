#!/usr/bin/env python3
"""Long vs short stats from trades.csv."""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def pf(series: pd.Series) -> float:
    wins = series[series > 0].sum()
    losses = abs(series[series < 0].sum())
    return wins / losses if losses > 0 else float("inf")


def summarize(df: pd.DataFrame, label: str) -> None:
    print(f"\n=== {label} (n={len(df)}) ===")
    for d in ("long", "short"):
        sub = df[df["direction"] == d]
        if sub.empty:
            print(f"  {d}: 0 trades")
            continue
        wr = (sub["pnl_r"] > 0).mean()
        print(
            f"  {d:5}: trades={len(sub):4}  win%={wr:.1%}  "
            f"avgR={sub['pnl_r'].mean():+.3f}  sumR={sub['pnl_r'].sum():+.1f}  PF={pf(sub['pnl_r']):.2f}"
        )


def main() -> None:
    paths = [
        "results/trades.csv",
        "results/out_of_sample/trades.csv",
        "results/in_sample/trades.csv",
    ]
    for path in paths:
        p = Path(path)
        if not p.exists():
            continue
        summarize(pd.read_csv(p), path)


if __name__ == "__main__":
    main()
