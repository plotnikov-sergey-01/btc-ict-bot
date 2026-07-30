# BTC ICT Futures Bot

Каркас для бэктеста и оптимизации ICT-стратегии на **Binance USDM Futures** (BTC/USDT).

## Документация для сессий

Краткий контекст стратегии: [`docs/STRATEGY.md`](docs/STRATEGY.md) — цели (5R/мес), baseline, IS/OOS, фильтры, команды.

Live / VPS: [`docs/LIVE.md`](docs/LIVE.md) · DigitalOcean: [`docs/DEPLOY_DO.md`](docs/DEPLOY_DO.md)

## Быстрый старт

```powershell
cd C:\Users\PC\Projects\btc-ict-bot
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

## Скачивание данных

### Вариант 1 — скрипт проекта (рекомендуется)

```powershell
python download_data.py --start 2022-01-01
```

Скачает в `data/`:
- `BTCUSDT_15m.parquet`
- `BTCUSDT_1h.parquet`
- `BTCUSDT_4h.parquet`
- `BTCUSDT_1d.parquet`
- `BTCUSDT_funding.parquet`

Используется **Binance USDM Futures API** через CCXT + публичный endpoint funding rate.

### Вариант 2 — bulk CSV (без API-лимитов)

[Binance Data Vision](https://data.binance.vision/?prefix=data/futures/um/monthly/klines/BTCUSDT/)

- `15m`, `1h`, `4h`, `1d` — monthly klines
- Конвертируйте в parquet с колонками: `timestamp, open, high, low, close, volume`
- Положите в `data/` с именами `BTCUSDT_{timeframe}.parquet`

### Вариант 3 — платные (tick / order book)

- [Tardis.dev](https://tardis.dev/) — для точных sweep внутри свечи
- [CoinAPI](https://www.coinapi.io/) — REST/WebSocket

Для текущего каркаса достаточно **Варианта 1**.

## In-sample / Out-of-sample

В `config.yaml`:

```yaml
backtest:
  sample:
    mode: full
    in_sample:
      start: "2020-01-01"
      end: "2022-12-31"
    out_of_sample:
      start: "2023-01-01"
      end: null
```

Команды:

```powershell
# Один прогон на выбранном окне
python run_backtest.py --sample in_sample
python run_backtest.py --sample out_of_sample

# Grid только на in-sample (оптимизация)
python run_grid.py --sample in_sample

# Grid in-sample + проверка лучшего сценария на out-of-sample
python run_validate_oos.py
```

Отчёты сохраняются в `results/in_sample/`, `results/out_of_sample/` или `results/grid/in_sample/`.

## Live / testnet / VPS

См. [`docs/LIVE.md`](docs/LIVE.md): локально `python run_live.py --once`, затем тот же код на VPS с testnet, Telegram при падениях.


## Запуск бэктеста

```powershell
python run_backtest.py
```

Результаты:
- `results/metrics.json` — компактные метрики для агента
- `results/trades.csv` — все сделки
- `results/equity.csv` — кривая капитала

## Настройка стратегии

Все параметры в `config.yaml`:

| Секция | Что настраивает |
|--------|-----------------|
| `risk.min_rr` | Минимум R:R (2 или 3 для теста) |
| `mtf` | Согласование Daily / 4H / 1H |
| `session` | 14:00–22:00 UTC, без выходных |
| `funding` | Фильтр по funding rate |
| `liquidity` | TP до ближайшей ликвидности |
| `trade_management.breakeven_at_rr` | Breakeven на +1R (`null` = выкл) |

## Логика (кратко)

1. **Daily + 4H + 1H** — bias в одну сторону
2. **15m** — CHoCH **или** BOS (достаточно одного)
3. **FVG** после события → вход на 50% (CE)
4. **SL** — за последний swing HL/LH + ATR-буфер
5. **TP** — ближайшая ликвидность, но не меньше `min_rr`
6. Сессия: будни, 14–22 UTC; выходные пропускаются

## Цикл оптимизации агентом

1. Агент читает `results/metrics.json`
2. Меняет 1–2 параметра в `config.yaml`
3. Запускает `python run_backtest.py`
4. Повторяет

## Структура проекта

```
btc-ict-bot/
├── config.yaml
├── download_data.py
├── run_backtest.py
├── ict_bot/
│   ├── bias.py
│   ├── structure.py
│   ├── fvg.py
│   ├── liquidity.py
│   ├── funding.py
│   ├── session.py
│   ├── risk.py
│   ├── strategy.py
│   └── backtest.py
├── data/
└── results/
```
