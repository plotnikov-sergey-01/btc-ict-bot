# Reverse-mine: 4H+1H (no daily) + retest entry

Research only (`scripts/reverse_mine_h4_retest.py` → `results/reverse_mine_h4_retest/`).

## MTF without daily

**Aligned** if:
- 4H bias ∈ {trade direction, neutral}
- 1H bias ∈ {trade direction, neutral}
- not both neutral

Daily is logged for comparison but **not required**.

## Entry hypotheses

### A — Confirm
On swing confirmation bar close (pivot L=R=3). SL behind swing + ATR buffer. Success = price hits **2R** before SL.

### B — Retest (hypothesis)
1. Swing forms and confirms.
2. Impulse = range from swing to extreme between swing and confirm.
3. Within **16** bars (4h on 15m), wait for pullback into **50%** of that impulse toward the swing.
4. Enter when candle **closes back** through the zone (rejection).
5. Invalidate if price pierces swing stop before fill.
6. Same SL / 2R success rule.

**Why try retest:** confirm entry often chases; retest aims closer to swing → smaller risk or better R location; fewer fills.

## How to read results

Compare `hit_2r_rate` for:
- `all` vs `h4_1h_only` vs `mtf_full_with_daily`
- `confirm` vs `retest`

If retest + h4_1h lifts rate and keeps enough `n`, that is a candidate for a second strategy backtest.
