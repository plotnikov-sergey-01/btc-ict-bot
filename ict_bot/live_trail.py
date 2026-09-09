"""Live trailing-stop management (mirrors backtest swing trail)."""

from __future__ import annotations

import logging
from typing import Any

import pandas as pd

from .live_orders import get_open_position, replace_stop_order
from .trade_management import precompute_swing_levels, swing_trail_stop

log = logging.getLogger(__name__)


def _mfe_rr(direction: int, entry: float, initial_stop: float, bar_high: float, bar_low: float) -> float:
    risk = abs(entry - initial_stop)
    if risk <= 0:
        return 0.0
    if direction == 1:
        return max(0.0, (bar_high - entry) / risk)
    return max(0.0, (entry - bar_low) / risk)


def manage_live_trailing(
    exchange,
    symbol: str,
    df_ltf: pd.DataFrame,
    cfg: dict,
    open_trade: dict | None,
    *,
    dry_run: bool = False,
) -> dict[str, Any]:
    """
    If trailing is enabled and position is open, tighten SL after activate_rr.

    Returns status dict; may mutate open_trade in-place (stop, trail_active).
    """
    tm = cfg.get("trade_management") or {}
    if not tm.get("trailing_stop", False):
        return {"action": "trail_disabled"}

    pos = get_open_position(exchange, symbol)
    if pos is None:
        return {"action": "flat"}

    if not open_trade:
        # Recover minimal state from live position (stop unknown → skip trail)
        log.warning("Open position but no open_trade in state — trail skipped this bar")
        return {"action": "no_state", "position": pos}

    direction = int(open_trade.get("direction") or pos["direction"])
    entry = float(open_trade.get("fill") or open_trade.get("entry") or pos["entry"])
    initial_stop = float(open_trade.get("initial_stop") or open_trade.get("stop"))
    current_stop = float(open_trade.get("stop") or initial_stop)
    take_profit = float(open_trade.get("take_profit"))
    amount = float(open_trade.get("amount") or pos["contracts"])

    struct = cfg["structure"]
    last_sh, last_sl = precompute_swing_levels(
        df_ltf, struct["pivot_left"], struct["pivot_right"]
    )
    i = len(df_ltf) - 1
    row = df_ltf.iloc[i]
    ts = df_ltf.index[i]

    activate_rr = float(tm.get("trail_activate_at_rr", 1.5))
    mfe = _mfe_rr(direction, entry, initial_stop, float(row["high"]), float(row["low"]))
    trail_active = bool(open_trade.get("trail_active"))

    if not trail_active and mfe < activate_rr:
        return {
            "action": "wait_activate",
            "mfe_rr": round(mfe, 3),
            "need_rr": activate_rr,
            "bar": str(ts),
        }

    open_trade["trail_active"] = True
    new_stop = swing_trail_stop(direction, i, df_ltf, last_sh, last_sl, cfg)
    if new_stop is None:
        return {"action": "no_swing", "mfe_rr": round(mfe, 3), "trail_active": True}

    improved = (direction == 1 and new_stop > current_stop) or (
        direction == -1 and new_stop < current_stop
    )
    if not improved:
        return {
            "action": "no_improve",
            "mfe_rr": round(mfe, 3),
            "stop": current_stop,
            "candidate": new_stop,
            "trail_active": True,
        }

    # Never move stop past entry in a way that worsens; also keep stop behind entry for long
    # (swing trail can be above entry after +1.5R — that's intended for locking profit)

    log.info(
        "Trail tighten: stop %.2f → %.2f (mfe=%.2fR bar=%s)",
        current_stop,
        new_stop,
        mfe,
        ts,
    )

    if dry_run:
        open_trade["stop"] = new_stop
        return {
            "action": "trail_dry",
            "old_stop": current_stop,
            "new_stop": new_stop,
            "mfe_rr": round(mfe, 3),
        }

    try:
        placed = replace_stop_order(
            exchange,
            symbol,
            direction=direction,
            amount=amount,
            new_stop=new_stop,
            take_profit=take_profit,
        )
        open_trade["stop"] = float(placed["stop"])
        open_trade["stop_order_id"] = placed.get("stop_order_id")
        open_trade["tp_order_id"] = placed.get("tp_order_id")
        return {
            "action": "trail_moved",
            "old_stop": current_stop,
            "new_stop": float(placed["stop"]),
            "mfe_rr": round(mfe, 3),
            "bar": str(ts),
        }
    except Exception as e:
        log.exception("Failed to replace stop for trail")
        return {"action": "trail_error", "error": str(e), "mfe_rr": round(mfe, 3)}
