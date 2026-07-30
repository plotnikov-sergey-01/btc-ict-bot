"""Order placement and position helpers for live trading."""

from __future__ import annotations

import logging
from typing import Any

from .strategy import Signal

log = logging.getLogger(__name__)


def fetch_usdt_balance(exchange) -> float:
    bal = exchange.fetch_balance()
    usdt = bal.get("USDT") or {}
    if isinstance(usdt, dict):
        return float(usdt.get("free") or usdt.get("total") or 0)
    return float(bal.get("free", {}).get("USDT", 0) or 0)


def has_open_position(exchange, symbol: str) -> bool:
    positions = exchange.fetch_positions([symbol])
    for p in positions:
        contracts = float(p.get("contracts") or p.get("contractSize") or 0)
        if contracts == 0:
            amt = float(p.get("info", {}).get("positionAmt", 0) or 0)
            contracts = abs(amt)
        if contracts > 0:
            return True
    return False


def calc_amount(
    exchange,
    symbol: str,
    balance: float,
    risk_pct: float,
    entry: float,
    stop: float,
) -> float | None:
    risk_usd = balance * (risk_pct / 100.0)
    dist = abs(entry - stop)
    if dist <= 0 or risk_usd <= 0:
        return None
    amount = risk_usd / dist
    return float(exchange.amount_to_precision(symbol, amount))


def _round_price(exchange, symbol: str, price: float) -> str:
    return exchange.price_to_precision(symbol, price)


def open_signal_trade(
    exchange,
    symbol: str,
    sig: Signal,
    cfg: dict,
    *,
    dry_run: bool = False,
) -> dict[str, Any]:
    """
    Market entry + STOP_MARKET + TAKE_PROFIT_MARKET (reduce-only).
    Matches backtest entry at signal bar; trailing deferred to later version.
    """
    balance = fetch_usdt_balance(exchange)
    risk_pct = float(cfg["backtest"]["risk_per_trade_pct"])
    amount = calc_amount(exchange, symbol, balance, risk_pct, sig.entry, sig.stop)
    if amount is None or amount <= 0:
        raise RuntimeError("position size is zero")

    side = "buy" if sig.direction == 1 else "sell"
    close_side = "sell" if sig.direction == 1 else "buy"
    stop_p = _round_price(exchange, symbol, sig.stop)
    tp_p = _round_price(exchange, symbol, sig.take_profit)

    plan = {
        "side": side,
        "amount": amount,
        "entry": sig.entry,
        "stop": stop_p,
        "take_profit": tp_p,
        "rr": sig.rr,
        "balance": balance,
    }
    log.info("Trade plan: %s", plan)

    if dry_run:
        return {"dry_run": True, **plan}

    try:
        exchange.set_leverage(10, symbol)
    except Exception as e:
        log.warning("set_leverage: %s", e)

    entry_order = exchange.create_order(symbol, "market", side, amount)
    log.info("Entry order: %s", entry_order.get("id"))

    sl = exchange.create_order(
        symbol,
        "STOP_MARKET",
        close_side,
        amount,
        None,
        {"stopPrice": stop_p, "reduceOnly": True},
    )
    tp = exchange.create_order(
        symbol,
        "TAKE_PROFIT_MARKET",
        close_side,
        amount,
        None,
        {"stopPrice": tp_p, "reduceOnly": True},
    )
    return {
        "entry_order_id": entry_order.get("id"),
        "stop_order_id": sl.get("id"),
        "tp_order_id": tp.get("id"),
        **plan,
    }
