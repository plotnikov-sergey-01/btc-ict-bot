from __future__ import annotations

from pathlib import Path

import pandas as pd


def _normalize_ohlcv(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    if "timestamp" in df.columns:
        df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
        df = df.set_index("timestamp")
    elif not isinstance(df.index, pd.DatetimeIndex):
        df.index = pd.to_datetime(df.index, utc=True)
    else:
        df.index = df.index.tz_convert("UTC") if df.index.tz else df.index.tz_localize("UTC")

    cols = ["open", "high", "low", "close", "volume"]
    for c in cols:
        if c not in df.columns:
            raise ValueError(f"Missing column: {c}")
        df[c] = pd.to_numeric(df[c], errors="coerce")

    return df[cols].sort_index()


def load_candles(data_dir: str | Path, symbol: str, timeframe: str) -> pd.DataFrame:
    """Load parquet or csv saved by download_data.py."""
    data_dir = Path(data_dir)
    safe_symbol = symbol.replace("/", "")
    for ext in (".parquet", ".csv"):
        path = data_dir / f"{safe_symbol}_{timeframe}{ext}"
        if path.exists():
            df = pd.read_parquet(path) if ext == ".parquet" else pd.read_csv(path)
            return _normalize_ohlcv(df)
    raise FileNotFoundError(f"No data for {symbol} {timeframe} in {data_dir}")


def load_funding(data_dir: str | Path, symbol: str) -> pd.DataFrame | None:
    data_dir = Path(data_dir)
    safe_symbol = symbol.replace("/", "")
    path = data_dir / f"{safe_symbol}_funding.parquet"
    if not path.exists():
        return None
    df = pd.read_parquet(path)
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
    return df.set_index("timestamp").sort_index()
