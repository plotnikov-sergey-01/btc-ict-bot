# Strategy context (for humans + AI sessions)

## Goal
- **Target:** ≥ **5R / month** average.
- Track: `monthly.csv`, `avg_r_per_month`, `pct_months_at_goal`.

## Current baseline (prod-like backtest, **fees + slippage ON**)
- RR2, no BE, trail 1.5R, sessions 08–22 UTC weekdays
- **`entry_at_ce: false`** — fill on **any FVG touch** (CE if reached, else edge)
- `limit_valid_bars: 8` (combo with 16 tested — see below)
- Daily MTF required, `require_4h: true`, quality filters OFF
- **Costs:** `commission_pct: 0.04` per side, `slippage_pct: 0.01` per side (`ict_bot/backtest.py`)
- IS 2020–2022 / OOS 2023+

**OOS fvg_touch (fees on):** ~**5.57R/mo**, **51%** months ≥5R, PF 2.37, DD ~-6.3%  
**IS fvg_touch (fees on):** ~**5.47R/mo**, 44% months ≥5R, PF 2.58  

Without fees (legacy runs): OOS ~6.71R/mo — use **fees on** for go-live expectations.

## Final validation (2026-07-28)
Script: `scripts/final_validation.py`, quick combo: `scripts/combo_quick.py`  
Raw: `results/final_validation/` (run `oos_limit_compare.py` for limit 8 vs 16 OOS)

| Variant | Sample | Fees | avgR/mo | %≥5R | Trades | PF |
|---------|--------|------|--------:|-----:|-------:|---:|
| baseline CE | OOS | on | 1.23 | 19% | 242 | 1.34 |
| **fvg_touch** | OOS | on | **5.57** | **51%** | 370 | 2.37 |
| fvg_touch | IS | on | 5.47 | 44% | 291 | 2.58 |
| fvg_touch + limit16 | OOS | on | **5.57** | **51%** | 370 | 2.37 |

**Combo limit16:** идентично `fvg_touch` + limit 8 (IS/OOS, fees on/off) — при `entry_at_ce: false` окно лимита не меняет fills. Prod: **`limit_valid_bars: 8`**.

Полная матрица: `results/final_validation/results.json`, quick: `combo_quick.json`.

## Live / hosting

Пошагово: [`docs/LIVE.md`](LIVE.md) (local → VPS testnet → prod, systemd, Telegram).

- **Dev & paper:** локальный ПК — `python run_live.py --once`
- **24/7 testnet/prod:** VPS + `.env` + `systemd` + Telegram на ERROR/crash
- Не держать live на ноутбуке с sleep/VPN; droplet $6–12/mo достаточно

## Slim grid (3 scenarios only)
`grid_config.yaml` → `rr2_noBE_trail1.5` | `rr2_noBE_noTrail` | `rr2_noBE_trail2.0`

## Entry-widen A/B (OOS 2023+, 2026-07-27)

| Variant | Trades | avgR/mo | %≥5R | PF | Return | DD |
|---------|-------:|--------:|-----:|---:|-------:|---:|
| baseline (CE only) | 242 | 2.44 | 29% | 1.77 | 167% | -10.0% |
| **limit16** | 271 | **3.53** | 31% | 2.04 | 317% | -9.6% |
| soft_mtf (`require_4h:false`)* | 326 | 2.77 | 31% | 1.63 | 203% | — |
| **fvg_touch** | **370** | **6.71** | **58%** | **2.85** | **1555%** | **-5.4%** |

\*First soft_mtf run matched baseline due to bug in `mtf_aligned` (4H always enforced). Fixed; numbers above are post-fix.

IS check: limit16 also strong (10.3R/mo IS); fvg_touch consistent IS/OOS.

Raw: `results/entry_widen/oos_results.json`

## Funnel (why misses) — see `results/diagnostics/entry_funnel_oos.json`
Biggest: session, no BOS, **MTF**, FVG not on bar, **limit not filled ~50%**.

## Next (1–2 tests only)
1. Binance Futures **testnet** live script (limit orders mirroring backtest)
2. Soft hour filter as score, not hard kill

## Commands
```powershell
python run_backtest.py --sample out_of_sample
python run_compare_is_oos.py
python scripts/final_validation.py
python scripts/combo_quick.py
python scripts/oos_limit_compare.py
python scripts/entry_widen_ab.py
python scripts/diagnose_entries.py
```

## Data
- `data/BTCUSDT_*.parquet` from 2020-01-01
