# Signal Scoring Algorithm

The signal scoring engine (`signals.py`) computes a single numeric score per stock by evaluating 7 signal categories with configurable weights, then applying a macro regime modifier.

## Scoring Categories

### 1. Whale / Bandar Accumulation (weight: 1.0)

Source: `bandar_detector` + `whale_scores` tables

| Condition | Points |
|-----------|--------|
| Top-5 broker net buy (latest day) | +1.0 |
| Multi-day accumulation (2+ consecutive days net buy) | +0.5 |
| Whale composite score > 0.7 | +0.5 |
| Whale composite score < 0.3 | warning (no points) |
| Top-5 broker distributing | warning (no points) |

### 2. Foreign Flow (weight: 1.5)

Source: `prices` table (foreign_buy, foreign_sell)

| Condition | Points |
|-----------|--------|
| 5-day net foreign buy > 0 | +1.5 |
| 3-day net positive but 5-day still negative (turning) | +0.45 (1.5 × 0.3) |
| Single-day foreign flow spike ≥ 50B IDR (inflow) | +0.45 (1.5 × 0.3) |
| 5-day net sell > 5B IDR | warning |

### 3. RSI (weight: 0.5)

Source: `indicators` table

| Condition | Points |
|-----------|--------|
| RSI < 30 (oversold) | +1.0 (0.5 × 2) |
| RSI 30-40 (low) | +0.5 |
| RSI > 70 (overbought) | warning |

### 4. Volume (weight: 0.5)

Source: `indicators` table (volume_ratio)

| Condition | Points |
|-----------|--------|
| Volume ratio > 2.5x | +1.0 (0.5 × 2) |
| Volume ratio > 2.0x | +0.5 |

### 5. MACD Momentum

Source: `indicators` table (macd_hist, 2-day comparison)

| Condition | Points |
|-----------|--------|
| MACD histogram turning up from negative | +0.3 |

### 6. Price vs Support/Resistance (weight: 1.0)

Source: `support_resistance` + `prices` tables

| Condition | Points |
|-----------|--------|
| Price within 3% above nearest support | +1.0 |
| Price within 1% above support | +0.5 bonus |
| Breakout above resistance on volume (ratio > 1.5x) | +0.5 |

### 7. EMA Alignment

Source: `indicators` table (ema20, ema50)

| Condition | Points |
|-----------|--------|
| Price testing EMA20 (within 2%) | +0.3 |
| Price testing EMA50 (within 2%) | +0.3 |

### 8. Sector Momentum (weight: 0.5)

Source: `sector_rotation` table

| Condition | Points |
|-----------|--------|
| Stock's sector ranked top-3 (5-day) | +0.5 |
| Sector momentum score > 3 | +0.25 (0.5 × 0.5) |

### 9. Macro Regime Modifier

Applied last, shifts the total score based on overall market environment.

| Regime | Modifier |
|--------|----------|
| risk_on | +0.5 |
| cautious | 0.0 |
| risk_off | -1.0 |

## Score Calculation

```
raw_score = sum of all category points
final_score = max(0, raw_score + macro_modifier)
final_score = round to nearest 0.5
```

## Score Interpretation

| Score | Action |
|-------|--------|
| ≥ 4.0 (threshold + 1) | STRONG BUY signal - consider entry |
| ≥ 3.0 (threshold) | BUY signal - good entry if fits plan |
| ≥ 2.0 (threshold - 1) | WATCH - building but not ready |
| ≥ 1.0 | HOLD - weak signal, wait for confirmation |
| < 1.0 | NO SIGNAL - stay away or wait |

The threshold is configurable via `signals.score_threshold` in config.yaml (default: 3.0).

## Maximum Theoretical Score

If all signals fire at maximum:
- Whale: 1.0 + 0.5 + 0.5 = 2.0
- Foreign: 1.5 + 0.45 = 1.95
- RSI: 1.0
- Volume: 1.0
- MACD: 0.3
- Support: 1.0 + 0.5 = 1.5
- EMA: 0.3 + 0.3 = 0.6
- Sector: 0.5 + 0.25 = 0.75
- Macro: 0.5

**Max = ~9.55** (rounded to 9.5)

In practice, scores above 5.0 are rare and very strong signals.

## Display Format

```
BBNI [Perbankan]: ★★★★☆ (4.0)
  ✓ Foreign net buy 5d: +12.3B
  ✓ Bandar net buy +5.2B
  ✓ Multi-day accumulation (2+ days)
  ✓ RSI low (38)
  ✗ Macro cautious penalty (0)
  → BUY signal - good entry if fits plan
```

Stars (★/☆) represent filled/empty out of 5. Signals (✓) are bullish factors, warnings (✗) are bearish factors.
