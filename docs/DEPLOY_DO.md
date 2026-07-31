# DigitalOcean VPS — btc-ict-bot (dual demo)

Минимальный дроплет: **Ubuntu 24.04**, **1 vCPU / 1 GB RAM** (два процесса лёгкие; при нехватке RAM — 2 GB).

**Регион:** для Binance USDM лучше **Asia** — **Singapore** или **Tokyo**. Mainland China к Binance не подходит. Latency для 15m-стратегии не критична.

На одном VPS крутятся **два независимых демо**:

| Сервис | Config | Env | Daily bias | Свечи / state |
|--------|--------|-----|------------|---------------|
| `btc-ict-daily-on` | `configs/ict_daily_on.yaml` | `.env.daily_on` | ON | `data/` |
| `btc-ict-daily-off` | `configs/ict_daily_off.yaml` | `.env.daily_off` | OFF | `data_daily_off/` |

Нужны **два разных** Binance Demo API key (два аккаунта/ключа). Один аккаунт на оба бота — конфликт позиций/ордеров.

## 1. Создать дроплет

1. [DigitalOcean](https://cloud.digitalocean.com/) → **Create** → **Droplets**
2. Image: Ubuntu 24.04 LTS
3. Plan: Basic → Regular → **$6/mo** (1 GB) или **$12** (2 GB)
4. Region: **Singapore** или **Tokyo**
5. Auth: SSH key
6. Hostname: `btc-ict-bot`
7. Create → скопируй **Public IPv4**

## 2. Первый вход и пользователь

```powershell
ssh root@YOUR_DROPLET_IP
```

```bash
adduser ict
usermod -aG sudo ict
rsync --archive --chown=ict:ict ~/.ssh /home/ict
su - ict
```

## 3. Python + репозиторий

```bash
sudo apt update
sudo apt install -y python3 python3-venv python3-pip git

cd ~
git clone https://github.com/plotnikov-sergey-01/btc-ict-bot.git
cd btc-ict-bot

python3 -m venv .venv
source .venv/bin/activate
pip install -U pip
pip install -r requirements.txt
```

## 4. Два `.env` на сервере

```bash
cp .env.daily_on.example .env.daily_on
cp .env.daily_off.example .env.daily_off
nano .env.daily_on   # API key аккаунта A
nano .env.daily_off  # API key аккаунта B (другой!)
chmod 600 .env.daily_on .env.daily_off
```

Оба файла:

```env
BINANCE_API_KEY=...
BINANCE_API_SECRET=...
BINANCE_USE_DEMO=true
BINANCE_TESTNET=false
TELEGRAM_BOT_TOKEN=...
TELEGRAM_CHAT_ID=...
CCXT_TIMEOUT_MS=120000
```

Telegram может быть общий — в сообщениях префикс `[daily_on]` / `[daily_off]`.

В Binance Demo API при желании whitelist IP = IP дроплета.

### Кэш свечей (опционально, ускоряет первый цикл)

Если локально уже есть parquet в `data/`, можно один раз скопировать на off-бот (отдельная папка, чтобы не было гонок записи):

```bash
# на VPS после первого успешного цикла daily_on, либо scp с машины:
cp -a data/*.parquet data_daily_off/ 2>/dev/null || true
```

Иначе каждый бот сам догрузит историю в свой `data` / `data_daily_off`.

## 5. Smoke-тест обоих

```bash
cd ~/btc-ict-bot
source .venv/bin/activate

python run_live.py --config configs/ict_daily_on.yaml --env-file .env.daily_on --once
python run_live.py --config configs/ict_daily_on.yaml --env-file .env.daily_on --cycle-once --dry-run

python run_live.py --config configs/ict_daily_off.yaml --env-file .env.daily_off --once
python run_live.py --config configs/ict_daily_off.yaml --env-file .env.daily_off --cycle-once --dry-run
```

Ожидай Telegram: `🟢 [daily_on] …` и `🟢 [daily_off] …`.  
Первый цикл может занять несколько минут (холодный кэш).

## 6. systemd — оба сервиса

```bash
sudo cp deploy/systemd/btc-ict-daily-on.service /etc/systemd/system/
sudo cp deploy/systemd/btc-ict-daily-off.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now btc-ict-daily-on btc-ict-daily-off
sudo systemctl status btc-ict-daily-on btc-ict-daily-off
```

Логи:

```bash
journalctl -u btc-ict-daily-on -f
journalctl -u btc-ict-daily-off -f
```

Перезапуск / стоп:

```bash
sudo systemctl restart btc-ict-daily-on btc-ict-daily-off
sudo systemctl stop btc-ict-daily-on btc-ict-daily-off
```

## 7. Обновление кода

```bash
cd ~/btc-ict-bot
git pull
source .venv/bin/activate
pip install -r requirements.txt
sudo systemctl restart btc-ict-daily-on btc-ict-daily-off
```

Кэш свечей и `live_state_*.json` на сервере **не** в git — после `pull` остаются.

## 8. Перед mainnet

- [ ] Оба demo ≥ несколько дней без тихих пропусков циклов
- [ ] Telegram: start / trade / missed / cycle failed с нужными префиксами
- [ ] Сравнить daily ON vs OFF (сделки, DD, PF)
- [ ] Новый API key Futures-only, IP = VPS, без withdraw
- [ ] `BINANCE_USE_DEMO=false`, `BINANCE_TESTNET=false`
- [ ] Малый `risk_per_trade_pct` на старте

## Firewall (опционально)

```bash
sudo ufw allow OpenSSH
sudo ufw enable
```
