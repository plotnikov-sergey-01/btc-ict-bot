# Live: local → VPS testnet → production

## Recommended path

| Phase | Where | Goal |
|-------|--------|------|
| **1. Wiring** | Local PC | Keys, CCXT testnet, `run_live.py --once`, Telegram test message |
| **2. Logic** | Local | Hook `generate_signals` + limit orders; compare fills vs backtest on recent bars |
| **3. Soak** | **VPS 24/7** | Same `.env`, `BINANCE_TESTNET=true`, heartbeat + alerts 1–2 weeks |
| **4. Go-live** | VPS | New API key (trade-only, IP whitelist), `BINANCE_TESTNET=false`, small size |

Do **not** skip phase 3: testnet on VPS catches restarts, clock, firewall, and missed 15m closes.

## Binance keys (paper)

**CCXT больше не поддерживает `sandbox` для USDM futures.** Используй один из вариантов:

### Вариант A — Demo Trading (рекомендуется)

1. Войди на [demo.binance.com](https://demo.binance.com/)
2. **API Management** → создай ключ (отдельный от mainnet и от старого testnet)
3. В `.env`:

```env
BINANCE_TESTNET=true
BINANCE_USE_DEMO=true
```

### Вариант B — старый Futures Testnet

1. Ключи с [testnet.binancefuture.com](https://testnet.binancefuture.com/)
2. В `.env`:

```env
BINANCE_TESTNET=true
BINANCE_USE_DEMO=false
```

### Mainnet (только после paper)

```env
BINANCE_TESTNET=false
```

Ключ **только Futures**, без withdraw, **IP whitelist** = IP VPS.

`.env` (copy from `.env.example`, never commit):

```env
BINANCE_API_KEY=...
BINANCE_API_SECRET=...
BINANCE_TESTNET=true
BINANCE_USE_DEMO=true

TELEGRAM_BOT_TOKEN=...
TELEGRAM_CHAT_ID=...
```

## Local smoke test

```powershell
# Single (legacy config.yaml) or dual A/B:
python run_live.py --config configs/ict_daily_on.yaml --env-file .env.daily_on --once
python run_live.py --config configs/ict_daily_on.yaml --env-file .env.daily_on --cycle-once --dry-run
python run_live.py --config configs/ict_daily_off.yaml --env-file .env.daily_off --cycle-once --dry-run
python run_live.py --config configs/ict_daily_on.yaml --env-file .env.daily_on   # 15m loop
```

Env: `LIVE_DRY_RUN=true` same as `--dry-run`.

State: `live.state_file` in config (defaults differ per bot: `data/live_state_daily_on.json` / `data_daily_off/...`).

Expect ping: `Ping OK`. Cycle logs `Cycle result` with `action`: `none`, `trade`, `skip_open_position`, etc.

**Speed:** candle fetch is incremental; `generate_signals` uses only last `live.signal_lookback_days` (default 90) from cache — full history stays on disk for backtests.

## Dual demo (daily ON vs OFF)

Two Binance demo API keys, two env files, two systemd units — see **[DEPLOY_DO.md](DEPLOY_DO.md)**.

| Bot | Config | Env | `mtf.require_daily` |
|-----|--------|-----|---------------------|
| daily_on | `configs/ict_daily_on.yaml` | `.env.daily_on` | `true` |
| daily_off | `configs/ict_daily_off.yaml` | `.env.daily_off` | `false` |

Telegram messages are prefixed `[daily_on]` / `[daily_off]`. Candle dirs are separate (`data/` vs `data_daily_off/`) to avoid parquet write races.

## VPS (DigitalOcean)

Пошагово: **[DEPLOY_DO.md](DEPLOY_DO.md)** (droplet, clone, два `.env`, два systemd).

Кратко: Ubuntu 24.04 → clone → venv → `.env.daily_on` + `.env.daily_off` → enable `btc-ict-daily-on` и `btc-ict-daily-off`.

Первый цикл качает историю в `data/*.parquet`; дальше — инкрементально (секунды–десятки секунд).

## Telegram notifications

1. [@BotFather](https://t.me/BotFather) → `/newbot` → copy **token**.
2. Write any message to your bot, then open  
   `https://api.telegram.org/bot<TOKEN>/getUpdates` → find `"chat":{"id":...}`.
3. Put token + chat id in `.env`.

What is sent:

| Event | Channel |
|--------|---------|
| Start / stop | `send_telegram` in `run_live.py` |
| Cycle failed (timeout etc.) | explicit + ERROR handler |
| Order placed | trade alert |
| Missed entry (gap / open position / leftover) | `⚠️ Missed entr…` |
| Uncaught crash | Telegram ERROR handler |
| Daily summary (PnL) | optional later |

Tips:

- Do not spam: heartbeat to Telegram **silent** or only on failure.
- For production, add a **dead-man** check (external cron hits health URL or expects daily ping).

## Production checklist

- [ ] Testnet soak on VPS ≥ 2 weeks, no silent gaps in logs
- [ ] Mainnet keys on VPS only, IP-restricted
- [ ] `BINANCE_TESTNET=false` only after explicit switch
- [ ] Risk caps in config match backtest (`risk_per_trade_pct`, max trades/day)
- [ ] Telegram alerts tested with forced error (`python -c "raise ..."` in dry run)

## Current code status

`run_live.py`: 15m bar loop → `generate_signals` → market entry + SL/TP. Trailing from backtest not yet on live. Incremental OHLCV cache in `data/*.parquet`.
