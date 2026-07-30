#!/usr/bin/env python3
"""Download Binance USDM futures OHLCV + funding rate history."""

from __future__ import annotations

import argparse
import time
from datetime import datetime, timezone
from pathlib import Path

import ccxt
import pandas as pd
import requests


def fetch_ohlcv(
    exchange: ccxt.Exchange,
    symbol: str,
    timeframe: str,
    since_ms: int,
) -> pd.DataFrame:
    all_rows = []
    since = since_ms
    limit = 1000

    while True:
        batch = exchange.fetch_ohlcv(symbol, timeframe, since=since, limit=limit)
        if not batch:
            break
        all_rows.extend(batch)
        since = batch[-1][0] + 1
        if len(batch) < limit:
            break
        time.sleep(exchange.rateLimit / 1000)

    df = pd.DataFrame(all_rows, columns=["timestamp", "open", "high", "low", "close", "volume"])
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
    return df.drop_duplicates(subset=["timestamp"]).sort_values("timestamp")


def fetch_funding_history(symbol: str, start_ms: int) -> pd.DataFrame:
    """Binance USDM funding rate history (public endpoint)."""
    pair = symbol.replace("/", "")
    url = "https://fapi.binance.com/fapi/v1/fundingRate"
    rows = []
    start_time = start_ms

    while True:
        resp = requests.get(
            url,
            params={"symbol": pair, "startTime": start_time, "limit": 1000},
            timeout=30,
        )
        resp.raise_for_status()
        batch = resp.json()
        if not batch:
            break
        rows.extend(batch)
        start_time = batch[-1]["fundingTime"] + 1
        if len(batch) < 1000:
            break
        time.sleep(0.2)

    if not rows:
        return pd.DataFrame(columns=["timestamp", "funding_rate"])

    df = pd.DataFrame(rows)
    df["timestamp"] = pd.to_datetime(df["fundingTime"], unit="ms", utc=True)
    df["funding_rate"] = pd.to_numeric(df["fundingRate"])
    return df[["timestamp", "funding_rate"]].drop_duplicates(subset=["timestamp"])


def main() -> None:
    parser = argparse.ArgumentParser(description="Download BTC/USDT futures data from Binance")
    parser.add_argument("--symbol", default="BTC/USDT")
    parser.add_argument("--start", default="2022-01-01")
    parser.add_argument("--out", default="data")
    parser.add_argument("--timeframes", nargs="+", default=["15m", "1h", "4h", "1d"])
    args = parser.parse_args()

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    exchange = ccxt.binanceusdm({"enableRateLimit": True})
    since_ms = int(datetime.strptime(args.start, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp() * 1000)
    safe = args.symbol.replace("/", "")

    print(f"Downloading {args.symbol} futures from {args.start}...")
    for tf in args.timeframes:
        print(f"  {tf}...", end=" ", flush=True)
        df = fetch_ohlcv(exchange, args.symbol, tf, since_ms)
        path = out_dir / f"{safe}_{tf}.parquet"
        df.to_parquet(path, index=False)
        print(f"{len(df)} candles -> {path}")

    print("  funding rate...", end=" ", flush=True)
    funding = fetch_funding_history(args.symbol, since_ms)
    fpath = out_dir / f"{safe}_funding.parquet"
    funding.to_parquet(fpath, index=False)
    print(f"{len(funding)} records -> {fpath}")

    print("Done.")


if __name__ == "__main__":
    main()
