"""Binance USDM Futures via CCXT (testnet or mainnet)."""

from __future__ import annotations

import os
from typing import Any

import ccxt


def load_env_file(path: str = ".env") -> None:
    """Minimal .env loader (no extra dependency)."""
    p = os.path.abspath(path)
    if not os.path.isfile(p):
        return
    with open(p, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            key = key.strip()
            val = val.strip().strip('"').strip("'")
            os.environ.setdefault(key, val)


def _truthy(name: str, default: bool = False) -> bool:
    v = os.getenv(name)
    if v is None:
        return default
    return v.strip().lower() in ("1", "true", "yes", "on")


def make_exchange(*, testnet: bool | None = None, demo: bool | None = None) -> ccxt.binanceusdm:
    """
    Mainnet, Binance Demo (recommended paper), or legacy testnet.binancefuture.com URLs.

    CCXT no longer supports set_sandbox_mode() for USDM futures; use demo or manual test URLs.
    """
    load_env_file()
    demo = _truthy("BINANCE_USE_DEMO", default=False) if demo is None else demo
    testnet = _truthy("BINANCE_TESTNET", default=False) if testnet is None else testnet

    api_key = os.getenv("BINANCE_API_KEY", "")
    api_secret = os.getenv("BINANCE_API_SECRET", "")
    if not api_key or not api_secret:
        raise RuntimeError("Set BINANCE_API_KEY and BINANCE_API_SECRET in .env")

    opts: dict[str, Any] = {
        "enableRateLimit": True,
        "timeout": int(os.getenv("CCXT_TIMEOUT_MS", "120000")),
        "apiKey": api_key,
        "secret": api_secret,
        "options": {
            "defaultType": "future",
            # Demo/testnet keys are invalid on mainnet sapi (capital/config/getall)
            "fetchCurrencies": False,
        },
    }
    exchange = ccxt.binanceusdm(opts)

    if demo:
        exchange.enable_demo_trading(True)
        mode = "demo"
    elif testnet:
        # Keys from https://testnet.binancefuture.com
        exchange.urls["api"] = exchange.deep_extend(exchange.urls["api"], exchange.urls["test"])
        exchange.options["sandboxMode"] = False
        mode = "testnet"
    else:
        mode = "mainnet"
        if _truthy("BINANCE_FETCH_CURRENCIES", default=False):
            exchange.options["fetchCurrencies"] = True

    exchange.options["ictTradingMode"] = mode
    fapi = exchange.urls.get("api", {}).get("fapiPrivateV3", "")
    exchange.options["ictFapiBase"] = fapi

    try:
        exchange.load_markets()
    except ccxt.AuthenticationError as e:
        hint = (
            f"API auth failed in mode={mode!r} (fapi base: {fapi}). "
            "Use demo keys from demo.binance.com with BINANCE_USE_DEMO=true "
            "(BINANCE_TESTNET can be false). "
            "testnet.binancefuture.com: BINANCE_USE_DEMO=false and BINANCE_TESTNET=true. "
            "Mainnet: both false."
        )
        raise RuntimeError(hint) from e
    return exchange


def ping(exchange: ccxt.binanceusdm, symbol: str = "BTC/USDT:USDT") -> dict[str, Any]:
    """Connectivity check: balance + last price."""
    ticker = exchange.fetch_ticker(symbol)
    balance = exchange.fetch_balance()
    usdt = balance.get("USDT") or balance.get("total", {})
    free = usdt.get("free") if isinstance(usdt, dict) else None
    return {
        "symbol": symbol,
        "last": ticker.get("last"),
        "usdt_free": free,
        "mode": exchange.options.get("ictTradingMode", "unknown"),
    }
