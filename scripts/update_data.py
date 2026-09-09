#!/usr/bin/env python3
"""Append latest candles to local parquet cache."""
from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import ccxt
import pandas as pd

from ict_bot.data_loader import _normalize_ohlcv, load_candles

symbol = "BTC/USDT"
out = Path("data")
safe = "BTCUSDT"
ex = ccxt.binanceusdm({"enableRateLimit": True})

for tf in ["15m", "1h", "4h", "1d"]:
    local = load_candles(out, symbol, tf)
    since = int(local.index[-1].timestamp() * 1000) + 1
    rows: list = []
    while True:
        batch = ex.fetch_ohlcv(symbol, tf, since=since, limit=1000)
        if not batch:
            break
        rows.extend(batch)
        since = batch[-1][0] + 1
        if len(batch) < 1000:
            break
        time.sleep(0.2)

    if rows:
        raw = pd.DataFrame(rows, columns=["timestamp", "open", "high", "low", "close", "volume"])
        raw["timestamp"] = pd.to_datetime(raw["timestamp"], unit="ms", utc=True)
        df = _normalize_ohlcv(raw)
        merged = pd.concat([local, df]).sort_index()
        merged = merged[~merged.index.duplicated(keep="last")]
        merged.reset_index().to_parquet(out / f"{safe}_{tf}.parquet", index=False)
        print(f"{tf}: +{len(df)} bars, total {len(merged)}, end {merged.index[-1]}")
    else:
        print(f"{tf}: up to date ({local.index[-1]})")
