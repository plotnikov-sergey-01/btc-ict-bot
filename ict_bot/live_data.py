"""Fetch and merge OHLCV for live strategy runs (incremental disk cache)."""

from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
from ccxt.base.errors import NetworkError, RequestTimeout

from .data_loader import _normalize_ohlcv, load_candles, load_funding

log = logging.getLogger(__name__)

MAX_FETCH_RETRIES = 4
RETRY_SLEEP_SEC = 5
# Overlap so last forming/closed bar can refresh
OVERLAP_BARS = 3


def fetch_ohlcv_ccxt(
    exchange,
    symbol: str,
    timeframe: str,
    since_ms: int,
    limit: int = 1000,
) -> pd.DataFrame:
    rows: list = []
    since = since_ms
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    if since > now_ms:
        log.warning(
            "since_ms %s is in the future for %s; clamping to 7d lookback",
            since,
            timeframe,
        )
        since = now_ms - 7 * 86400 * 1000

    while True:
        batch = None
        for attempt in range(1, MAX_FETCH_RETRIES + 1):
            try:
                batch = exchange.fetch_ohlcv(symbol, timeframe, since=since, limit=limit)
                break
            except (RequestTimeout, NetworkError) as e:
                log.warning(
                    "OHLCV fetch timeout %s %s (attempt %s/%s): %s",
                    symbol,
                    timeframe,
                    attempt,
                    MAX_FETCH_RETRIES,
                    e,
                )
                if attempt == MAX_FETCH_RETRIES:
                    raise
                time.sleep(RETRY_SLEEP_SEC * attempt)
        if not batch:
            break
        rows.extend(batch)
        since = batch[-1][0] + 1
        if len(batch) < limit:
            break
        time.sleep(exchange.rateLimit / 1000)
    if not rows:
        return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
    df = pd.DataFrame(rows, columns=["timestamp", "open", "high", "low", "close", "volume"])
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
    return _normalize_ohlcv(df)


def _merge_local_and_remote(local: pd.DataFrame | None, remote: pd.DataFrame) -> pd.DataFrame:
    if local is None or local.empty:
        return remote
    if remote.empty:
        return local
    out = pd.concat([local, remote])
    return out[~out.index.duplicated(keep="last")].sort_index()


def _cache_path(data_dir: str | Path, spot_symbol: str, timeframe: str) -> Path:
    safe = spot_symbol.replace("/", "")
    return Path(data_dir) / f"{safe}_{timeframe}.parquet"


def save_candles(data_dir: str | Path, spot_symbol: str, timeframe: str, df: pd.DataFrame) -> None:
    if df is None or df.empty:
        return
    path = _cache_path(data_dir, spot_symbol, timeframe)
    path.parent.mkdir(parents=True, exist_ok=True)
    out = df.copy()
    out = out.reset_index()
    if out.columns[0] != "timestamp":
        out = out.rename(columns={out.columns[0]: "timestamp"})
    out.to_parquet(path, index=False)
    log.info("Cached %s %s → %s (%s bars)", spot_symbol, timeframe, path.name, len(df))


def _tf_ms(timeframe: str) -> int:
    unit = timeframe[-1]
    n = int(timeframe[:-1])
    mult = {"m": 60_000, "h": 3_600_000, "d": 86_400_000}.get(unit)
    if mult is None:
        raise ValueError(f"Unsupported timeframe: {timeframe}")
    return n * mult


def load_timeframe(
    exchange,
    cfg: dict,
    symbol: str,
    timeframe: str,
    lookback_days: int,
) -> pd.DataFrame:
    data_dir = cfg["data"]["dir"]
    spot_symbol = cfg["symbol"]
    local = None
    try:
        local = load_candles(data_dir, spot_symbol, timeframe)
    except FileNotFoundError:
        pass

    if local is not None and not local.empty:
        # Incremental: only refresh from last bar − overlap
        since_ms = int(local.index[-1].timestamp() * 1000) - OVERLAP_BARS * _tf_ms(timeframe)
        log.info(
            "Incremental %s fetch since %s (%s local bars)",
            timeframe,
            pd.Timestamp(since_ms, unit="ms", tz="UTC"),
            len(local),
        )
    else:
        since = datetime.now(timezone.utc) - timedelta(days=lookback_days)
        since_ms = int(since.timestamp() * 1000)
        log.info("Cold %s fetch lookback=%sd", timeframe, lookback_days)

    remote = fetch_ohlcv_ccxt(exchange, symbol, timeframe, since_ms)
    merged = _merge_local_and_remote(local, remote)
    if merged.empty:
        raise RuntimeError(f"No OHLCV for {timeframe}")
    save_candles(data_dir, spot_symbol, timeframe, merged)
    return merged


def load_live_market_data(exchange, cfg: dict, symbol: str) -> tuple[pd.DataFrame, ...]:
    """15m / 1h / 4h / 1d + funding. First cycle may be slow; later cycles incremental."""
    # Cold start needs enough history for MTF/bias; warm = tiny tail refresh
    data_dir = cfg["data"]["dir"]
    spot_symbol = cfg["symbol"]
    has_local = False
    try:
        probe = load_candles(data_dir, spot_symbol, cfg["timeframes"]["ltf"])
        has_local = probe is not None and not probe.empty
    except FileNotFoundError:
        pass

    if has_local:
        lb_15m, lb_1h, lb_4h, lb_1d = 7, 30, 60, 200
    else:
        lb_15m, lb_1h, lb_4h, lb_1d = 120, 400, 400, 800

    df_15m = load_timeframe(exchange, cfg, symbol, cfg["timeframes"]["ltf"], lookback_days=lb_15m)
    df_1h = load_timeframe(exchange, cfg, symbol, "1h", lookback_days=lb_1h)
    df_4h = load_timeframe(exchange, cfg, symbol, "4h", lookback_days=lb_4h)
    df_1d = load_timeframe(exchange, cfg, symbol, cfg["timeframes"]["htf"], lookback_days=lb_1d)
    funding = None
    try:
        funding = load_funding(cfg["data"]["dir"], cfg["symbol"])
    except FileNotFoundError:
        pass
    return df_15m, df_1h, df_4h, df_1d, funding


def last_closed_bar(df: pd.DataFrame, timeframe_minutes: int = 15) -> pd.Timestamp:
    """Last fully closed candle (exclude forming bar)."""
    if df.empty:
        raise RuntimeError("empty dataframe")
    last_ts = df.index[-1]
    now = pd.Timestamp.now(tz="UTC")
    bar_end = last_ts + pd.Timedelta(minutes=timeframe_minutes)
    if bar_end > now:
        if len(df) < 2:
            raise RuntimeError("only forming bar available")
        return df.index[-2]
    return last_ts
