"""Order placement and position helpers for live trading."""

from __future__ import annotations

import logging
import time
from typing import Any

from .strategy import Signal

log = logging.getLogger(__name__)


class EntryDeviationError(RuntimeError):
    """Live price moved too far from signal entry (adverse slippage)."""


def _is_transient_network(exc: BaseException) -> bool:
    name = type(exc).__name__
    msg = str(exc).lower()
    needles = (
        "remotedisconnected",
        "connection aborted",
        "connection reset",
        "timed out",
        "timeout",
        "temporarily unavailable",
        "network",
        "broken pipe",
        "connectionerror",
        "requesttimeout",
        "exchange not available",
    )
    if any(n in name.lower() for n in ("network", "timeout", "connection", "request")):
        return True
    return any(n in msg for n in needles)


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


def fetch_last_price(exchange, symbol: str) -> float:
    ticker = exchange.fetch_ticker(symbol)
    mark = ticker.get("mark") or (ticker.get("info") or {}).get("markPrice")
    last = ticker.get("last") or ticker.get("close") or mark
    if last is None:
        raise RuntimeError(f"no ticker price for {symbol}")
    return float(last)


def adverse_deviation_pct(direction: int, signal_entry: float, live: float) -> float:
    """How much worse live is vs signal, in percent (0 if price moved in our favor)."""
    if signal_entry <= 0:
        return 0.0
    if direction == 1:
        return max(0.0, (live - signal_entry) / signal_entry * 100.0)
    return max(0.0, (signal_entry - live) / signal_entry * 100.0)


def cancel_open_orders(exchange, symbol: str) -> int:
    """Cancel all working orders for symbol. Returns number cancelled (best-effort)."""
    try:
        open_orders = exchange.fetch_open_orders(symbol)
    except Exception as e:
        log.warning("fetch_open_orders failed: %s", e)
        try:
            exchange.cancel_all_orders(symbol)
            return -1
        except Exception as e2:
            log.warning("cancel_all_orders failed: %s", e2)
            return 0
    n = 0
    for o in open_orders or []:
        oid = o.get("id")
        if not oid:
            continue
        try:
            exchange.cancel_order(oid, symbol)
            n += 1
            log.info("Cancelled leftover order %s type=%s", oid, o.get("type"))
        except Exception as e:
            log.warning("cancel_order %s failed: %s", oid, e)
    return n


def sweep_orphan_orders(exchange, symbol: str, *, retries: int = 2) -> int:
    """
    If flat, cancel leftover SL/TP (Binance does not OCO-cancel the sibling).

    On transient network errors: retry a few times, then return 0 without
    cancelling (never cancel if we could not confirm the position is flat).
    """
    last_err: BaseException | None = None
    for attempt in range(retries + 1):
        try:
            if has_open_position(exchange, symbol):
                return 0
            n = cancel_open_orders(exchange, symbol)
            if n:
                log.info("Swept %s leftover order(s) while flat", n)
            return n if n >= 0 else 0
        except Exception as e:
            last_err = e
            if attempt < retries and _is_transient_network(e):
                delay = 1.5 * (attempt + 1)
                log.warning(
                    "Orphan sweep network blip (attempt %s/%s), retry in %.1fs: %s",
                    attempt + 1,
                    retries + 1,
                    delay,
                    e,
                )
                time.sleep(delay)
                continue
            # Non-transient or retries exhausted — do not cancel blindly
            log.warning("Orphan sweep skipped: %s", e)
            return 0
    if last_err:
        log.warning("Orphan sweep skipped: %s", last_err)
    return 0


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

    Skips if live price has moved adversely vs signal entry by more than
    live.max_entry_deviation_pct (default 0.25% ≈ $160 at 64k).
    """
    live_px = fetch_last_price(exchange, symbol)
    max_dev = float((cfg.get("live") or {}).get("max_entry_deviation_pct", 0.25))
    adverse = adverse_deviation_pct(sig.direction, sig.entry, live_px)
    abs_move = abs(live_px - sig.entry)

    if max_dev > 0 and adverse > max_dev:
        raise EntryDeviationError(
            f"live={live_px:.2f} vs signal={sig.entry:.2f} "
            f"adverse={adverse:.3f}% (${abs_move:.0f}) > max {max_dev}%"
        )

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
        "live_price": live_px,
        "adverse_pct": round(adverse, 4),
        "slip_usd": round(abs_move, 2),
        "stop": stop_p,
        "take_profit": tp_p,
        "rr": sig.rr,
        "balance": balance,
    }
    log.info("Trade plan: %s", plan)

    if dry_run:
        return {"dry_run": True, **plan}

    # Old SL/TP stay working after the sibling fills — cancel before a new ticket
    sweep_orphan_orders(exchange, symbol)

    try:
        exchange.set_leverage(10, symbol)
    except Exception as e:
        log.warning("set_leverage: %s", e)

    entry_order = exchange.create_order(symbol, "market", side, amount)
    fill = float(
        entry_order.get("average")
        or entry_order.get("price")
        or live_px
    )
    log.info("Entry order: %s fill≈%s", entry_order.get("id"), fill)
    plan["fill"] = fill
    plan["fill_slip_usd"] = round(abs(fill - sig.entry), 2)

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
