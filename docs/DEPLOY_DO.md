# DigitalOcean VPS — btc-ict-bot (daily_on)

Минимальный дроплет: **Ubuntu 24.04**, **1 vCPU / 1 GB RAM**.

**Регион:** для Binance USDM лучше **Asia** — **Singapore** или **Tokyo**.

Сейчас в прод-демо крутим **один** бот — **daily ON** (`configs/ict_daily_on.yaml`).  
`daily_off` оставлен в репо для исследований, на VPS не нужен.

| Сервис | Config | Env | Daily bias |
|--------|--------|-----|------------|
| `btc-ict-daily-on` | `configs/ict_daily_on.yaml` | `.env.daily_on` | ON |

## 1–3. Droplet, user, Python

Как раньше: Ubuntu 24.04 → user `ict` → clone → venv → `pip install -r requirements.txt`.

## 4. `.env` (один файл)

```bash
cp .env.daily_on.example .env.daily_on
nano .env.daily_on
chmod 600 .env.daily_on
```

```env
BINANCE_API_KEY=...
BINANCE_API_SECRET=...
BINANCE_USE_DEMO=true
BINANCE_TESTNET=false
TELEGRAM_BOT_TOKEN=...
TELEGRAM_CHAT_ID=...
CCXT_TIMEOUT_MS=120000
```

## 5. Smoke-тест

```bash
cd ~/btc-ict-bot
source .venv/bin/activate
python run_live.py --config configs/ict_daily_on.yaml --env-file .env.daily_on --once
python run_live.py --config configs/ict_daily_on.yaml --env-file .env.daily_on --cycle-once --dry-run
```

## 6. systemd (только daily_on)

```bash
sudo cp deploy/systemd/btc-ict-daily-on.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now btc-ict-daily-on
sudo systemctl status btc-ict-daily-on
journalctl -u btc-ict-daily-on -f
```

Если раньше был включён `btc-ict-daily-off`:

```bash
sudo systemctl disable --now btc-ict-daily-off
```

## 7. Обновление

```bash
cd ~/btc-ict-bot
git pull
source .venv/bin/activate
pip install -r requirements.txt
sudo systemctl restart btc-ict-daily-on
```

## Live behaviour (важно)

- **Orphan SL/TP:** после закрытия позиции бот снимает и обычные, и **algo/conditional** ордера (`cancel_all_orders` + `stop=True`).
- **Trail:** на каждом новом 15m баре при открытой позиции — swing trail после `trail_activate_at_rr` (1.5R), как в бэктесте. Telegram: `🪢 Trail SL …`.
- State: `data/live_state_daily_on.json` хранит `open_trade` (entry/stop/TP/trail_active).

## 8. Перед mainnet

- [ ] Demo ≥ несколько недель **с трейлом**
- [ ] Orphan-ордера не остаются после закрытий
- [ ] Новый API key Futures-only, IP = VPS, без withdraw
- [ ] `BINANCE_USE_DEMO=false`
- [ ] Малый `risk_per_trade_pct` на старте
