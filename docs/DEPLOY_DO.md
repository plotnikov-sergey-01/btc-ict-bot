# DigitalOcean VPS — btc-ict-bot

Минимальный дроплет: **Ubuntu 24.04**, **1 vCPU / 1 GB RAM**, регион ближе к тебе (или Frankfurt/Amsterdam).

## 1. Создать дроплет

1. [DigitalOcean](https://cloud.digitalocean.com/) → **Create** → **Droplets**
2. Image: Ubuntu 24.04 LTS
3. Plan: Basic → Regular → **$6/mo** (1 GB)
4. Auth: SSH key (лучше) или password
5. Hostname: `btc-ict-bot`
6. Create → скопируй **Public IPv4**

## 2. Первый вход и пользователь

С Windows (PowerShell), подставь IP:

```powershell
ssh root@YOUR_DROPLET_IP
```

На сервере:

```bash
adduser ict
usermod -aG sudo ict
# если входил по SSH-ключу root — скопируй ключ пользователю:
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

## 4. `.env` на сервере

```bash
cp .env.example .env
nano .env
```

Пример (demo paper):

```env
BINANCE_API_KEY=...
BINANCE_API_SECRET=...
BINANCE_TESTNET=true
BINANCE_USE_DEMO=true
TELEGRAM_BOT_TOKEN=...
TELEGRAM_CHAT_ID=...
CCXT_TIMEOUT_MS=120000
```

Права:

```bash
chmod 600 .env
```

В Binance Demo API при желании добавь **IP whitelist** = IP дроплета.

## 5. Smoke-тест

```bash
cd ~/btc-ict-bot
source .venv/bin/activate
python run_live.py --once
python run_live.py --cycle-once --dry-run
```

Ожидай Telegram: `🟢 ICT bot starting`, затем результат цикла.  
**Первый** цикл может занять несколько минут (холодный кэш свечей).  
**Следующие** — обычно десятки секунд (инкрементальный parquet в `data/`).

## 6. systemd (автозапуск 24/7)

```bash
sudo nano /etc/systemd/system/btc-ict-bot.service
```

```ini
[Unit]
Description=btc-ict-bot live/demo
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=ict
WorkingDirectory=/home/ict/btc-ict-bot
EnvironmentFile=/home/ict/btc-ict-bot/.env
ExecStart=/home/ict/btc-ict-bot/.venv/bin/python run_live.py
Restart=always
RestartSec=15

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now btc-ict-bot
sudo systemctl status btc-ict-bot
journalctl -u btc-ict-bot -f
```

Полезное:

```bash
sudo systemctl restart btc-ict-bot
sudo systemctl stop btc-ict-bot
```

## 7. Обновление кода с GitHub

```bash
cd ~/btc-ict-bot
git pull
source .venv/bin/activate
pip install -r requirements.txt
sudo systemctl restart btc-ict-bot
```

Кэш свечей (`data/*.parquet`) и `data/live_state.json` на сервере **не** в git — после `pull` они остаются.

## 8. Перед mainnet

- [ ] Demo на VPS ≥ несколько дней без тихих пропусков циклов
- [ ] Telegram: start / trade / missed / cycle failed
- [ ] Новый API key Futures-only, IP = VPS, без withdraw
- [ ] `BINANCE_TESTNET=false`, `BINANCE_USE_DEMO=false`
- [ ] Малый `risk_per_trade_pct` на старте

## Firewall (опционально)

Бот сам ходит наружу (Binance/Telegram); входящие порты кроме SSH не нужны:

```bash
sudo ufw allow OpenSSH
sudo ufw enable
```
