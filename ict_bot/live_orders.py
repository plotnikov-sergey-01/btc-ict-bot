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


def get_open_position(exchange, symbol: str) -> dict[str, Any] | None:
    """Return first non-zero position for symbol, or None if flat."""
    positions = exchange.fetch_positions([symbol])
    for p in positions:
        contracts = float(p.get("contracts") or 0)
        if contracts == 0:
            amt = float(p.get("info", {}).get("positionAmt", 0) or 0)
            contracts = abs(amt)
        if contracts <= 0:
            continue
        side = (p.get("side") or "").lower()
        info_amt = float(p.get("info", {}).get("positionAmt", 0) or 0)
        if not side:
            side = "long" if info_amt > 0 else "short"
        entry = float(p.get("entryPrice") or p.get("info", {}).get("entryPrice") or 0)
        return {
            "side": side,
            "direction": 1 if side == "long" else -1,
            "contracts": contracts,
            "entry": entry,
            "unrealized": float(p.get("unrealizedPnl") or 0),
            "raw": p,
        }
    return None


def has_open_position(exchange, symbol: str) -> bool:
    return get_open_position(exchange, symbol) is not None


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


def _market_id(exchange, symbol: str) -> str:
    market = exchange.market(symbol)
    return str(market["id"])


def count_open_orders(exchange, symbol: str) -> dict[str, int]:
    """Count regular + algo (conditional) open orders for diagnostics."""
    regular = 0
    algo = 0
    try:
        regular = len(exchange.fetch_open_orders(symbol) or [])
    except Exception as e:
        log.warning("fetch_open_orders count failed: %s", e)
    try:
        mid = _market_id(exchange, symbol)
        algo_resp = exchange.fapiPrivateGetOpenAlgoOrders({"symbol": mid})
        if isinstance(algo_resp, list):
            algo = len(algo_resp)
        elif isinstance(algo_resp, dict):
            orders = algo_resp.get("orders") or algo_resp.get("data") or []
            algo = len(orders) if isinstance(orders, list) else 0
    except Exception as e:
        log.warning("fetch open algo orders count failed: %s", e)
    return {"regular": regular, "algo": algo, "total": regular + algo}


def cancel_all_working_orders(exchange, symbol: str) -> dict[str, Any]:
    """
    Cancel BOTH regular and algo/conditional orders.

    Binance USDM places STOP_MARKET / TAKE_PROFIT_MARKET as algo orders.
    cancel_all_orders(symbol) alone does NOT cancel them — need params stop=True.
    """
    result = {"regular_ok": False, "algo_ok": False, "errors": []}
    before = count_open_orders(exchange, symbol)
    result["before"] = before

    try:
        exchange.cancel_all_orders(symbol)
        result["regular_ok"] = True
        log.info("cancel_all_orders (regular) OK for %s", symbol)
    except Exception as e:
        result["errors"].append(f"regular:{e}")
        log.warning("cancel_all_orders (regular) failed: %s", e)

    try:
        # CCXT routes stop/trigger/conditional → fapiPrivateDeleteAlgoOpenOrders
        exchange.cancel_all_orders(symbol, {"stop": True})
        result["algo_ok"] = True
        log.info("cancel_all_orders (algo/conditional) OK for %s", symbol)
    except Exception as e:
        result["errors"].append(f"algo:{e}")
        log.warning("cancel_all_orders (algo) failed: %s", e)
        # Direct fallback
        try:
            mid = _market_id(exchange, symbol)
            exchange.fapiPrivateDeleteAlgoOpenOrders({"symbol": mid})
            result["algo_ok"] = True
            log.info("fapiPrivateDeleteAlgoOpenOrders OK for %s", symbol)
        except Exception as e2:
            result["errors"].append(f"algo_direct:{e2}")
            log.warning("fapiPrivateDeleteAlgoOpenOrders failed: %s", e2)

    after = count_open_orders(exchange, symbol)
    result["after"] = after
    result["cancelled_est"] = max(0, before["total"] - after["total"])
    return result


def sweep_orphan_orders(exchange, symbol: str, *, retries: int = 2) -> dict[str, Any]:
    """
    If flat, cancel leftover SL/TP (regular + algo).

    Returns dict with cancelled_est, before/after counts, flat flag.
    Never cancels while a position is still open.
    """
    last_err: BaseException | None = None
    for attempt in range(retries + 1):
        try:
            if has_open_position(exchange, symbol):
                return {"flat": False, "cancelled_est": 0, "skipped": "position_open"}
            detail = cancel_all_working_orders(exchange, symbol)
            cancelled = int(detail.get("cancelled_est") or 0)
            if cancelled or detail.get("before", {}).get("total", 0):
                log.info(
                    "Orphan sweep flat=%s before=%s after=%s cancelled≈%s",
                    True,
                    detail.get("before"),
                    detail.get("after"),
                    cancelled,
                )
            return {
                "flat": True,
                "cancelled_est": cancelled,
                "before": detail.get("before"),
                "after": detail.get("after"),
                "errors": detail.get("errors") or [],
            }
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
            log.warning("Orphan sweep skipped: %s", e)
            return {"flat": None, "cancelled_est": 0, "error": str(e)}
    return {"flat": None, "cancelled_est": 0, "error": str(last_err)}


def place_protective_orders(
    exchange,
    symbol: str,
    *,
    direction: int,
    amount: float,
    stop: float,
    take_profit: float,
) -> dict[str, Any]:
    """Place reduce-only STOP_MARKET + TAKE_PROFIT_MARKET."""
    close_side = "sell" if direction == 1 else "buy"
    stop_p = _round_price(exchange, symbol, stop)
    tp_p = _round_price(exchange, symbol, take_profit)
    amt = float(exchange.amount_to_precision(symbol, amount))

    sl = exchange.create_order(
        symbol,
        "STOP_MARKET",
        close_side,
        amt,
        None,
        {"stopPrice": stop_p, "reduceOnly": True},
    )
    tp = exchange.create_order(
        symbol,
        "TAKE_PROFIT_MARKET",
        close_side,
        amt,
        None,
        {"stopPrice": tp_p, "reduceOnly": True},
    )
    return {
        "stop": stop_p,
        "take_profit": tp_p,
        "amount": amt,
        "stop_order_id": sl.get("id"),
        "tp_order_id": tp.get("id"),
    }


def replace_stop_order(
    exchange,
    symbol: str,
    *,
    direction: int,
    amount: float,
    new_stop: float,
    take_profit: float,
) -> dict[str, Any]:
    """
    Cancel all protective orders and re-place SL + TP at new stop.

    Safer than cancelling one order by id (algo vs regular id mismatch).
    """
    cancel_all_working_orders(exchange, symbol)
    return place_protective_orders(
        exchange,
        symbol,
        direction=direction,
        amount=amount,
        stop=new_stop,
        take_profit=take_profit,
    )


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

    plan = {
        "side": side,
        "amount": amount,
        "entry": sig.entry,
        "live_price": live_px,
        "adverse_pct": round(adverse, 4),
        "slip_usd": round(abs_move, 2),
        "stop": float(sig.stop),
        "take_profit": float(sig.take_profit),
        "rr": sig.rr,
        "balance": balance,
        "direction": sig.direction,
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

    protective = place_protective_orders(
        exchange,
        symbol,
        direction=sig.direction,
        amount=amount,
        stop=sig.stop,
        take_profit=sig.take_profit,
    )
    plan.update(protective)
    plan["initial_stop"] = float(sig.stop)
    plan["trail_active"] = False
    return {
        "entry_order_id": entry_order.get("id"),
        **plan,
    }
