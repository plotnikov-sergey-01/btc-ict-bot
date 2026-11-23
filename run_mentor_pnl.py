#!/usr/bin/env python3
from __future__ import annotations

import argparse
from collections import Counter
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import ccxt
import pandas as pd

from ict_bot.exchange_client import load_env_file
from mentor_agent.settings import MentorSettings
from mentor_agent.market_context import resolve_symbol
from mentor_agent.store import MessageStore


def _public_exchange():
    exchange = ccxt.binanceusdm(
        {
            "enableRateLimit": True,
            "timeout": 120000,
            "options": {"defaultType": "future", "fetchCurrencies": False},
        }
    )
    exchange.load_markets()
    return exchange


def _fetch_5m(exchange, symbol: str, start: pd.Timestamp, end: pd.Timestamp) -> pd.DataFrame:
    since = int(start.timestamp() * 1000)
    until = int(end.timestamp() * 1000)
    rows: list[list[float]] = []
    while since < until:
        batch = exchange.fetch_ohlcv(symbol, "5m", since=since, limit=1000)
        if not batch:
            break
        rows.extend(row for row in batch if row[0] < until)
        next_since = int(batch[-1][0]) + 1
        if next_since <= since:
            break
        since = next_since
        if len(batch) < 1000:
            break
        time.sleep(exchange.rateLimit / 1000)
    if not rows:
        return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
    frame = pd.DataFrame(
        rows, columns=["timestamp", "open", "high", "low", "close", "volume"]
    )
    frame["timestamp"] = pd.to_datetime(frame["timestamp"], unit="ms", utc=True)
    return frame.drop_duplicates("timestamp").set_index("timestamp").sort_index()


def _candidate_entry(candidate: dict[str, Any]) -> float | None:
    if candidate.get("entry_price") is not None:
        return float(candidate["entry_price"])
    low, high = candidate.get("entry_low"), candidate.get("entry_high")
    if low is not None and high is not None:
        return (float(low) + float(high)) / 2.0
    return None


def _evaluate(
    candidate: dict[str, Any], candles: pd.DataFrame, min_rr: float = 2.0
) -> dict[str, Any]:
    side = candidate.get("side")
    entry = _candidate_entry(candidate)
    stop = candidate.get("stop_price")
    target = candidate.get("take_profit_price")
    if side not in {"long", "short"} or entry is None or stop is None or target is None:
        return {"outcome": "not_testable", "r": 0.0, "reason": "numeric entry/SL/TP required"}
    entry, stop, target = float(entry), float(stop), float(target)
    valid = (side == "long" and stop < entry < target) or (
        side == "short" and target < entry < stop
    )
    if not valid:
        return {"outcome": "invalid_geometry", "r": 0.0, "reason": "entry/SL/TP geometry"}
    risk = abs(entry - stop)
    planned_rr = abs(target - entry) / risk
    if planned_rr < min_rr:
        return {
            "outcome": "below_min_rr",
            "r": 0.0,
            "planned_rr": round(planned_rr, 2),
            "reason": f"planned RR is below {min_rr:g}",
        }
    fill_pos = None
    for pos, (_, bar) in enumerate(candles.iterrows()):
        if float(bar.low) <= entry <= float(bar.high):
            fill_pos = pos
            break
    if fill_pos is None:
        observed_low = float(candles.low.min())
        observed_high = float(candles.high.max())
        if entry < observed_low:
            closest_distance = observed_low - entry
        elif entry > observed_high:
            closest_distance = entry - observed_high
        else:
            closest_distance = 0.0
        return {
            "outcome": "not_triggered",
            "r": 0.0,
            "entry": entry,
            "stop": stop,
            "target": target,
            "planned_rr": round(planned_rr, 2),
            "observed_low": observed_low,
            "observed_high": observed_high,
            "closest_distance": round(closest_distance, 8),
            "closest_distance_pct": round(closest_distance / entry * 100.0, 4),
        }

    fill_time = candles.index[fill_pos]
    entry_bar = candles.iloc[fill_pos]
    entry_exit_touch = (
        float(entry_bar.low) <= stop <= float(entry_bar.high)
        or float(entry_bar.low) <= target <= float(entry_bar.high)
    )
    if entry_exit_touch:
        return {
            "outcome": "ambiguous_entry_bar",
            "r": -1.0,
            "entry": entry,
            "fill_time": fill_time.isoformat(),
            "planned_rr": round(planned_rr, 2),
            "reason": "entry and SL/TP touched within the same 5m candle; conservative loss",
        }

    for ts, bar in candles.iloc[fill_pos + 1 :].iterrows():
        stop_hit = float(bar.low) <= stop <= float(bar.high)
        target_hit = float(bar.low) <= target <= float(bar.high)
        if stop_hit and target_hit:
            return {
                "outcome": "ambiguous_exit_bar",
                "r": -1.0,
                "entry": entry,
                "fill_time": fill_time.isoformat(),
                "exit_time": ts.isoformat(),
                "planned_rr": round(planned_rr, 2),
            }
        if stop_hit:
            return {
                "outcome": "stop",
                "r": -1.0,
                "entry": entry,
                "fill_time": fill_time.isoformat(),
                "exit_time": ts.isoformat(),
                "planned_rr": round(planned_rr, 2),
            }
        if target_hit:
            return {
                "outcome": "target",
                "r": round(planned_rr, 2),
                "entry": entry,
                "fill_time": fill_time.isoformat(),
                "exit_time": ts.isoformat(),
                "planned_rr": round(planned_rr, 2),
            }

    last = float(candles.iloc[-1].close)
    open_r = (last - entry) / risk if side == "long" else (entry - last) / risk
    return {
        "outcome": "open",
        "r": round(open_r, 2),
        "entry": entry,
        "fill_time": fill_time.isoformat(),
        "last_price": last,
        "planned_rr": round(planned_rr, 2),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate mentor trade candidates on Binance 5m")
    parser.add_argument("--env-file", default=".env.mentor")
    parser.add_argument("--max-days", type=int, default=7)
    parser.add_argument("--equity", type=float, default=5000.0)
    parser.add_argument("--risk-pct", type=float, default=1.0)
    parser.add_argument("--min-rr", type=float, default=2.0)
    parser.add_argument("--output", default="results/mentor_candidate_pnl.json")
    parser.add_argument("--print-trades", action="store_true")
    args = parser.parse_args()

    load_env_file(args.env_file, override=True)
    settings = MentorSettings.from_env()
    store = MessageStore(settings.db_path)
    runs = store.trade_candidate_runs("mentor_v")
    exchange = _public_exchange()
    cache: dict[tuple[str, str, str], pd.DataFrame] = {}
    trades: list[dict[str, Any]] = []

    now = pd.Timestamp.now(tz="UTC").floor("5min")
    for run in runs:
        result = run["result"]
        for index, candidate in enumerate(result.get("trade_candidates") or [], start=1):
            raw_symbol = candidate.get("symbol") or result.get("symbol")
            symbol = candidate.get("resolved_symbol") or resolve_symbol(exchange, raw_symbol)
            base = {
                "analysis_run_id": run["id"],
                "message_id": run["message_id"],
                "channel": run["channel_title"],
                "prompt_version": run["prompt_version"],
                "symbol_raw": raw_symbol,
                "symbol": symbol,
                "candidate_index": index,
                "side": candidate.get("side"),
                "analysis_created_at": run["created_at"],
            }
            eligibility_reason = candidate.get("eligibility_reason")
            if eligibility_reason in {"macro_context", "htf_rejected", "below_min_rr"}:
                trades.append({**base, "outcome": eligibility_reason, "r": 0.0, "candidate": candidate})
                continue
            if symbol is None:
                trades.append({**base, "outcome": "symbol_unavailable", "r": 0.0})
                continue
            start = pd.Timestamp(run["created_at"], tz="UTC").ceil("5min")
            end = min(now, start + pd.Timedelta(days=max(1, args.max_days)))
            if end <= start:
                trades.append({**base, "outcome": "no_elapsed_window", "r": 0.0})
                continue
            key = (symbol, start.isoformat(), end.isoformat())
            if key not in cache:
                cache[key] = _fetch_5m(exchange, symbol, start, end)
            candles = cache[key]
            evaluated = (
                {"outcome": "no_candles", "r": 0.0}
                if candles.empty
                else _evaluate(candidate, candles, args.min_rr)
            )
            trades.append({**base, **evaluated, "candidate": candidate})

    excluded = {
        "not_testable", "invalid_geometry", "symbol_unavailable", "no_candles",
        "no_elapsed_window", "below_min_rr", "macro_context", "htf_rejected",
    }
    testable = [t for t in trades if t["outcome"] not in excluded]
    realized = [t for t in testable if t["outcome"] not in {"open", "not_triggered"}]
    outcome_counts = Counter(str(t["outcome"]) for t in trades)
    realized_r = round(sum(float(t["r"]) for t in realized), 2)
    open_mark_r = round(
        sum(float(t["r"]) for t in testable if t["outcome"] == "open"), 2
    )
    total_r = round(realized_r + open_mark_r, 2)
    risk_usd = args.equity * args.risk_pct / 100.0
    realized_wins = sum(t["outcome"] == "target" for t in realized)
    def grouped(field: str) -> dict[str, dict[str, Any]]:
        output: dict[str, dict[str, Any]] = {}
        for value in sorted({str(t.get(field) or "unknown") for t in trades}):
            members = [t for t in trades if str(t.get(field) or "unknown") == value]
            member_testable = [t for t in members if t["outcome"] not in excluded]
            member_realized = [t for t in member_testable if t["outcome"] not in {"open", "not_triggered"}]
            wins = sum(t["outcome"] == "target" for t in member_realized)
            group_r = round(sum(float(t["r"]) for t in member_realized), 2)
            output[value] = {
                "candidates": len(members),
                "testable": len(member_testable),
                "realized": len(member_realized),
                "targets": wins,
                "stops_or_ambiguous": sum(
                    t["outcome"] in {"stop", "ambiguous_entry_bar", "ambiguous_exit_bar"}
                    for t in member_realized
                ),
                "realized_r": group_r,
                "win_rate_pct": round(wins / len(member_realized) * 100, 2) if member_realized else None,
            }
        return output

    summary = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "window_max_days": args.max_days,
        "equity_assumption": args.equity,
        "risk_pct_assumption": args.risk_pct,
        "min_rr": args.min_rr,
        "candidates": len(trades),
        "testable": len(testable),
        "realized": len(realized),
        "targets": sum(t["outcome"] == "target" for t in trades),
        "stops_or_conservative_ambiguous": sum(t["outcome"] in {"stop", "ambiguous_entry_bar", "ambiguous_exit_bar"} for t in trades),
        "open": sum(t["outcome"] == "open" for t in trades),
        "not_triggered": sum(t["outcome"] == "not_triggered" for t in trades),
        "not_testable": sum(t["outcome"] == "not_testable" for t in trades),
        "outcome_counts": dict(sorted(outcome_counts.items())),
        "realized_r": realized_r,
        "open_mark_r": open_mark_r,
        "total_r_including_open_mark": total_r,
        "realized_win_rate_pct": (
            round(realized_wins / len(realized) * 100.0, 2) if realized else None
        ),
        "average_realized_r": (
            round(realized_r / len(realized), 2) if realized else None
        ),
        "realized_pnl_usd_fixed_risk": round(realized_r * risk_usd, 2),
        "open_pnl_usd_fixed_risk": round(open_mark_r * risk_usd, 2),
        "potential_pnl_usd_fixed_risk": round(total_r * risk_usd, 2),
        "by_side": grouped("side"),
        "by_symbol": grouped("symbol"),
    }
    report = {"summary": summary, "trades": trades}
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if args.print_trades:
        compact = [
            {
                "message_id": t.get("message_id"),
                "symbol": t.get("symbol") or t.get("symbol_raw"),
                "candidate": t.get("candidate_index"),
                "side": t.get("side"),
                "outcome": t.get("outcome"),
                "r": t.get("r"),
                "planned_rr": t.get("planned_rr"),
                "entry": t.get("entry"),
                "stop": t.get("stop"),
                "target": t.get("target"),
                "observed_low": t.get("observed_low"),
                "observed_high": t.get("observed_high"),
                "closest_distance_pct": t.get("closest_distance_pct"),
                "fill_time": t.get("fill_time"),
                "exit_time": t.get("exit_time"),
                "reason": t.get("reason"),
            }
            for t in trades
        ]
        print(json.dumps({"trade_details": compact}, ensure_ascii=False, indent=2))
    print(f"report={output.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
