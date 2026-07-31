#!/usr/bin/env python3
"""
ICT antipattern research: enrich OOS trades, find weak buckets, A/B filter candidates.
"""

from __future__ import annotations

import json
import sys
from copy import deepcopy
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ict_bot.config import load_config
from ict_bot.runner import load_market_data, run_backtest
from ict_bot.structure import atr
from ict_bot.strategy import Signal, generate_signals

OUT = Path("results/ict_antipatterns")


def trades_to_df(trades) -> pd.DataFrame:
    rows = []
    for t in trades:
        rows.append(
            {
                "entry_time": pd.Timestamp(t.entry_time),
                "exit_time": pd.Timestamp(t.exit_time),
                "direction": t.direction,
                "entry": t.entry,
                "stop": t.stop,
                "take_profit": t.take_profit,
                "pnl_r": t.pnl_r,
                "outcome": t.outcome,
                "rr_planned": t.rr_planned,
            }
        )
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    df["win"] = df["pnl_r"] > 0
    df["hour"] = df["entry_time"].dt.hour
    df["weekday"] = df["entry_time"].dt.dayofweek
    df["risk_pct"] = (df["entry"] - df["stop"]).abs() / df["entry"] * 100
    df["rr_bucket"] = pd.cut(
        df["rr_planned"],
        bins=[0, 2.5, 4, 8, 1000],
        labels=["2_2.5", "2.5_4", "4_8", "8plus"],
    )
    return df


def attach_signal_meta(df: pd.DataFrame, signals: list[Signal]) -> pd.DataFrame:
    by_key = {}
    for s in signals:
        key = (pd.Timestamp(s.timestamp), "long" if s.direction == 1 else "short")
        by_key[key] = s
    metas = []
    for _, row in df.iterrows():
        s = by_key.get((row["entry_time"], row["direction"]))
        if s is None:
            # nearest same direction within 1 bar
            metas.append({})
            continue
        metas.append(s.meta or {})
    meta_df = pd.DataFrame(metas)
    return pd.concat([df.reset_index(drop=True), meta_df], axis=1)


def attach_market_feats(df: pd.DataFrame, df_15m: pd.DataFrame) -> pd.DataFrame:
    atr_s = atr(df_15m, 14)
    atr_pct = atr_s.rolling(2880, min_periods=100).rank(pct=True)
    vals = []
    for ts in df["entry_time"]:
        if ts not in df_15m.index:
            # asof
            loc = df_15m.index.searchsorted(ts, side="right") - 1
            if loc < 0:
                vals.append({"atr_pct": np.nan, "atr": np.nan})
                continue
            ts = df_15m.index[loc]
        vals.append(
            {
                "atr_pct": float(atr_pct.loc[ts]) if ts in atr_pct.index else np.nan,
                "atr": float(atr_s.loc[ts]) if ts in atr_s.index else np.nan,
            }
        )
    return pd.concat([df.reset_index(drop=True), pd.DataFrame(vals)], axis=1)


def bucket_stats(df: pd.DataFrame, col: str) -> dict:
    out = {}
    for key, g in df.groupby(col, dropna=False):
        out[str(key)] = {
            "n": int(len(g)),
            "win_rate": round(float(g["win"].mean()), 4),
            "avg_r": round(float(g["pnl_r"].mean()), 4),
            "sum_r": round(float(g["pnl_r"].sum()), 2),
            "share": round(len(g) / max(1, len(df)), 4),
        }
    return out


def analyze(df: pd.DataFrame) -> dict:
    wins = df[df["win"]]
    losses = df[~df["win"]]
    report = {
        "n": len(df),
        "win_rate": round(float(df["win"].mean()), 4),
        "avg_r": round(float(df["pnl_r"].mean()), 4),
        "sum_r": round(float(df["pnl_r"].sum()), 2),
        "wins_avg_rr_planned": round(float(wins["rr_planned"].mean()), 3) if len(wins) else None,
        "losses_avg_rr_planned": round(float(losses["rr_planned"].mean()), 3) if len(losses) else None,
        "wins_avg_risk_pct": round(float(wins["risk_pct"].mean()), 4) if len(wins) else None,
        "losses_avg_risk_pct": round(float(losses["risk_pct"].mean()), 4) if len(losses) else None,
        "by_hour": bucket_stats(df, "hour"),
        "by_weekday": bucket_stats(df, "weekday"),
        "by_direction": bucket_stats(df, "direction"),
        "by_rr_bucket": bucket_stats(df, "rr_bucket"),
        "by_outcome": bucket_stats(df, "outcome"),
    }
    if "atr_pct" in df.columns:
        df = df.copy()
        df["atr_bucket"] = pd.cut(
            df["atr_pct"],
            bins=[-0.01, 0.3, 0.7, 1.01],
            labels=["low", "mid", "high"],
        )
        report["by_atr_bucket"] = bucket_stats(df, "atr_bucket")
    if "h1_bias" in df.columns:
        # h1 against trade direction
        def h1_rel(row):
            d = 1 if row["direction"] == "long" else -1
            h = row.get("h1_bias")
            if pd.isna(h):
                return "na"
            h = int(h)
            if h == d:
                return "with"
            if h == 0:
                return "neutral"
            return "against"

        df = df.copy()
        df["h1_rel"] = df.apply(h1_rel, axis=1)
        report["by_h1_rel"] = bucket_stats(df, "h1_rel")
    if "fvg_size" in df.columns and "atr" in df.columns:
        df = df.copy()
        df["fvg_atr"] = df["fvg_size"] / df["atr"].replace(0, np.nan)
        df["fvg_atr_bucket"] = pd.cut(
            df["fvg_atr"],
            bins=[0, 0.5, 1.0, 2.0, 100],
            labels=["tiny", "norm", "large", "xlarge"],
        )
        report["by_fvg_atr"] = bucket_stats(df, "fvg_atr_bucket")

    # Candidate antipatterns: buckets with n>=15 and avg_r < 0 (and worse than overall)
    overall = report["avg_r"]
    cands = []
    for dim, table in report.items():
        if not dim.startswith("by_"):
            continue
        for key, st in table.items():
            if st["n"] < 15:
                continue
            if st["avg_r"] < min(0.0, overall - 0.15):
                cands.append(
                    {
                        "dim": dim,
                        "key": key,
                        "n": st["n"],
                        "avg_r": st["avg_r"],
                        "win_rate": st["win_rate"],
                        "sum_r": st["sum_r"],
                        "share": st["share"],
                    }
                )
    cands.sort(key=lambda x: x["avg_r"])
    report["weak_buckets"] = cands[:20]
    return report


def filter_signals(signals: list[Signal], predicate) -> list[Signal]:
    return [s for s in signals if predicate(s)]


def metrics_row(name: str, sample: str, m: dict) -> dict:
    return {
        "variant": name,
        "sample": sample,
        **{k: m[k] for k in (
            "trades", "win_rate", "profit_factor", "avg_r", "avg_r_per_month",
            "pct_months_at_goal", "total_return_pct", "max_dd_pct",
        )},
    }


def main() -> None:
    cfg = load_config()
    df_15m, df_1h, df_4h, df_1d, funding = load_market_data(cfg)
    OUT.mkdir(parents=True, exist_ok=True)

    print("Generating ICT signals...", flush=True)
    signals = generate_signals(df_15m, df_1h, df_4h, df_1d, funding, cfg)
    print(f"  signals={len(signals)}", flush=True)

    print("OOS baseline backtest...", flush=True)
    trades, _, m_base, _ = run_backtest(
        cfg, df_15m, df_1h, df_4h, df_1d, funding, signals=signals, sample_mode="out_of_sample"
    )
    print(
        f"  baseline OOS: trades={m_base['trades']} avgR/mo={m_base['avg_r_per_month']:.2f} "
        f"PF={m_base['profit_factor']:.2f} DD={m_base['max_dd_pct']:.1f}%",
        flush=True,
    )

    df = trades_to_df(trades)
    df = attach_signal_meta(df, signals)
    df = attach_market_feats(df, df_15m)
    df.to_csv(OUT / "oos_trades_enriched.csv", index=False)

    report = analyze(df)
    with open(OUT / "analysis.json", "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, default=str)

    print("\n=== Weak buckets (avg_r) ===", flush=True)
    for c in report["weak_buckets"][:12]:
        print(
            f"  {c['dim']}={c['key']}: n={c['n']} avgR={c['avg_r']:.3f} "
            f"WR={c['win_rate']:.0%} sumR={c['sum_r']:.1f} share={c['share']:.0%}",
            flush=True,
        )

    # Build filter A/B from strongest actionable antipatterns
    # Always test: skip rr_planned >= 8; skip worst hours if any; skip high risk_pct
    ab_rows = [metrics_row("baseline", "out_of_sample", m_base)]

    def run_filtered(name: str, pred) -> None:
        nonlocal ab_rows
        sig_f = filter_signals(signals, pred)
        _, _, m, _ = run_backtest(
            cfg, df_15m, df_1h, df_4h, df_1d, funding, signals=sig_f, sample_mode="out_of_sample"
        )
        ab_rows.append(metrics_row(name, "out_of_sample", m))
        print(
            f"  A/B {name}: trades={m['trades']} avgR/mo={m['avg_r_per_month']:.2f} "
            f"PF={m['profit_factor']:.2f} DD={m['max_dd_pct']:.1f}% ret={m['total_return_pct']:.1f}%",
            flush=True,
        )
        # IS check for overfitting
        _, _, m_is, _ = run_backtest(
            cfg, df_15m, df_1h, df_4h, df_1d, funding, signals=sig_f, sample_mode="in_sample"
        )
        ab_rows.append(metrics_row(name, "in_sample", m_is))

    print("\n=== A/B filters (OOS) ===", flush=True)

    run_filtered("skip_rr_ge_8", lambda s: s.rr < 8)
    run_filtered("skip_rr_ge_4", lambda s: s.rr < 4)

    # Hours with clearly negative avg_r and enough trades from analysis
    bad_hours = [
        int(c["key"])
        for c in report["weak_buckets"]
        if c["dim"] == "by_hour" and c["avg_r"] < 0 and c["n"] >= 15
    ]
    if bad_hours:
        bad_set = set(bad_hours)
        run_filtered(
            f"skip_hours_{'_'.join(map(str, sorted(bad_set)))}",
            lambda s, bh=bad_set: s.timestamp.hour not in bh,
        )

    # Skip large risk stops (> median*1.5 of losses if available)
    risk_cut = float(df["risk_pct"].quantile(0.9))
    run_filtered(
        f"skip_risk_pct_gt_{risk_cut:.3f}",
        lambda s, cut=risk_cut: abs(s.entry - s.stop) / s.entry * 100 <= cut,
    )

    # Combine: skip far TP and worst hours
    if bad_hours:
        bad_set = set(bad_hours)
        run_filtered(
            "skip_rr_ge_8_and_bad_hours",
            lambda s, bh=bad_set: s.rr < 8 and s.timestamp.hour not in bh,
        )

    # Require h1 with trade (stricter than neutral allowed)
    run_filtered(
        "require_h1_with",
        lambda s: int(s.meta.get("h1_bias", 0)) == s.direction,
    )

    with open(OUT / "ab_results.json", "w", encoding="utf-8") as f:
        json.dump(ab_rows, f, indent=2)

    # Pick best OOS by avg_r_per_month among filters that don't kill too many trades
    oos = [r for r in ab_rows if r["sample"] == "out_of_sample"]
    base_r = next(r["avg_r_per_month"] for r in oos if r["variant"] == "baseline")
    ranked = sorted(oos, key=lambda r: r["avg_r_per_month"], reverse=True)
    summary = {
        "baseline_avg_r_per_month": base_r,
        "ranked_oos": ranked,
        "weak_buckets": report["weak_buckets"][:10],
    }
    with open(OUT / "summary.json", "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2)

    print("\n=== OOS ranking (avgR/mo) ===", flush=True)
    for r in ranked:
        delta = r["avg_r_per_month"] - base_r
        print(
            f"  {r['variant']:40} {r['avg_r_per_month']:6.2f} ({delta:+.2f}) "
            f"trades={r['trades']} PF={r['profit_factor']:.2f} DD={r['max_dd_pct']:.1f}%",
            flush=True,
        )
    print(f"\nSaved -> {OUT}", flush=True)


if __name__ == "__main__":
    main()
