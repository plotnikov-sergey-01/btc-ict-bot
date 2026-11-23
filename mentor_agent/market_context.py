from __future__ import annotations

import os
from typing import Any

import ccxt
import pandas as pd

from ict_bot.bias import timeframe_bias
from ict_bot.structure import (
    atr,
    detect_structure_signals,
    last_swing_high_before,
    last_swing_low_before,
    recent_structure_signal,
)


MACRO_SYMBOLS = {
    "BTC.D", "USDT.D", "TOTAL", "TOTAL2", "TOTAL3", "OTHERS", "OTHERS.D",
    "ETHBTC", "ETH/BTC", "SOLETH", "SOL/ETH",
}


def is_macro_symbol(raw: str | None) -> bool:
    if not raw:
        return False
    return raw.upper().replace(" ", "") in MACRO_SYMBOLS


def public_exchange():
    exchange = ccxt.binanceusdm(
        {
            "enableRateLimit": True,
            "timeout": 120000,
            "options": {"defaultType": "future", "fetchCurrencies": False},
        }
    )
    exchange.load_markets()
    return exchange


def resolve_symbol(exchange, raw: str | None) -> str | None:
    if not raw or is_macro_symbol(raw):
        return None
    token = raw.upper().replace(" ", "").replace("-", "/")
    token = token.replace("/USDT:USDT", "").replace("/USDT", "").replace("USDT", "")
    for candidate in (f"{token}/USDT:USDT", f"{token}/USDC:USDC"):
        if candidate in exchange.markets:
            return candidate
    return None


def _frame(rows: list[list[float]]) -> pd.DataFrame:
    frame = pd.DataFrame(
        rows, columns=["timestamp", "open", "high", "low", "close", "volume"]
    )
    frame["timestamp"] = pd.to_datetime(frame["timestamp"], unit="ms", utc=True)
    frame = frame.drop_duplicates("timestamp").set_index("timestamp").sort_index()
    # Binance includes the currently forming candle. Structure calculations use closed bars.
    return frame.iloc[:-1] if len(frame) > 1 else frame.iloc[0:0]


def _numeric_entry(candidate: dict[str, Any]) -> float | None:
    if candidate.get("entry_price") is not None:
        return float(candidate["entry_price"])
    low, high = candidate.get("entry_low"), candidate.get("entry_high")
    if low is not None and high is not None:
        return (float(low) + float(high)) / 2.0
    return None


def enrich_candidates(result: dict[str, Any], exchange=None) -> None:
    """Add deterministic Binance structure context and executable levels.

    Every independently valid candidate stays eligible; this function intentionally has no
    portfolio, overlap, correlation, or maximum-open-position limit.
    """
    candidates = result.get("trade_candidates") or []
    if not candidates:
        return
    exchange = exchange or public_exchange()
    min_rr = float(os.getenv("MENTOR_MIN_RR", "2"))
    pivot_left = int(os.getenv("MENTOR_PIVOT_LEFT", "3"))
    pivot_right = int(os.getenv("MENTOR_PIVOT_RIGHT", "3"))
    signal_lookback = int(os.getenv("MENTOR_ENTRY_SIGNAL_LOOKBACK", "8"))
    stop_buffer_atr = float(os.getenv("MENTOR_STOP_BUFFER_ATR", "0.10"))
    frames: dict[str, dict[str, pd.DataFrame]] = {}

    for candidate in candidates:
        raw_symbol = candidate.get("symbol") or result.get("symbol")
        candidate["symbol"] = raw_symbol
        candidate["eligible"] = False
        if is_macro_symbol(raw_symbol):
            candidate["eligibility_reason"] = "macro_context"
            continue
        symbol = resolve_symbol(exchange, raw_symbol)
        candidate["resolved_symbol"] = symbol
        if symbol is None:
            candidate["eligibility_reason"] = "symbol_unavailable"
            continue
        if symbol not in frames:
            frames[symbol] = {
                "5m": _frame(exchange.fetch_ohlcv(symbol, "5m", limit=500)),
                "1h": _frame(exchange.fetch_ohlcv(symbol, "1h", limit=500)),
                "4h": _frame(exchange.fetch_ohlcv(symbol, "4h", limit=500)),
            }
        f5, f1, f4 = frames[symbol]["5m"], frames[symbol]["1h"], frames[symbol]["4h"]
        if min(len(f5), len(f1), len(f4)) < pivot_left + pivot_right + 10:
            candidate["eligibility_reason"] = "insufficient_market_data"
            continue

        direction = 1 if candidate.get("side") == "long" else -1
        h4_bias = timeframe_bias(f4, pivot_left, pivot_right)
        h1_bias = timeframe_bias(f1, pivot_left, pivot_right)
        candidate["htf_4h_bias"] = h4_bias
        candidate["htf_1h_bias"] = h1_bias
        # 4H defines the directional regime. 1H may be neutral while an entry forms,
        # but an explicitly opposite 1H structure invalidates the candidate.
        if h4_bias != direction or h1_bias not in (0, direction):
            candidate["htf_status"] = "rejected"
            candidate["eligibility_reason"] = "htf_rejected"
            continue
        candidate["htf_status"] = "aligned"

        entry = _numeric_entry(candidate)
        if entry is None:
            signals = detect_structure_signals(f5, pivot_left, pivot_right)
            if not recent_structure_signal(signals, f5.index[-1], direction, signal_lookback):
                candidate["eligibility_reason"] = "waiting_5m_structure_trigger"
                candidate["status"] = "waiting_trigger"
                continue
            entry = float(f5.close.iloc[-1])
            candidate["entry_price"] = entry
            candidate["entry_rule"] = "Last closed 5m close after matching CHoCH/BOS."

        volatility = float(atr(f5, 14).iloc[-1])
        stop = candidate.get("stop_price")
        if stop is None:
            if direction == 1:
                swing = last_swing_low_before(f5, f5.index[-1], pivot_left, pivot_right)
                stop = swing - volatility * stop_buffer_atr if swing is not None else None
            else:
                swing = last_swing_high_before(f5, f5.index[-1], pivot_left, pivot_right)
                stop = swing + volatility * stop_buffer_atr if swing is not None else None
            candidate["stop_price"] = stop
            candidate["stop_rule"] = "Last confirmed 5m swing plus 0.10 ATR safety buffer."
        if stop is None or (direction == 1 and stop >= entry) or (direction == -1 and stop <= entry):
            candidate["eligibility_reason"] = "invalid_or_missing_stop"
            continue

        target = candidate.get("take_profit_price")
        risk = abs(entry - float(stop))
        valid_target = target is not None and (
            (direction == 1 and float(target) > entry) or
            (direction == -1 and float(target) < entry)
        )
        rr = abs(float(target) - entry) / risk if valid_target and risk else 0.0
        if not valid_target:
            target = entry + direction * risk * min_rr
            candidate["take_profit_price"] = target
            candidate["take_profit_rule"] = f"Fallback minimum {min_rr:g}R target."
            rr = min_rr
        candidate["estimated_rr"] = round(rr, 2)
        candidate["eligible"] = rr >= min_rr
        candidate["eligibility_reason"] = "eligible" if candidate["eligible"] else "below_min_rr"
        candidate["status"] = "ready" if candidate["eligible"] else "provisional"
        candidate["market_enriched"] = True

