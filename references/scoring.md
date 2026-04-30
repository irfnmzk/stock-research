# Signal Base Rate System

The old composite scoring system (weighted points, macro modifier, star ratings) has been replaced with a base rate measurement approach. The system measures signal performance from historical data and shows raw stats. User evaluates and decides.

## Philosophy

- System measures, user scores
- No composite score, no magic numbers
- Show hit rate, avg return, sample size per signal
- Rank stocks by number of active signals with decent hit rate (>55%)
- Context (trend, regime) shown as labels, not modifiers

## Signal Categories

### Broker Signals (from broker_summary + bandar_detector)

1. **Broker Timing Score** — per (broker, stock) pair, rolling hit rate and avg forward return after net buy days
2. **Accumulation Streak** — consecutive net buy days per broker per stock
3. **Broker Concentration** — top broker net value / total value
4. **Buyer Seller Imbalance** — buyer/seller ratio + price direction
5. **Acc/Dist Phase** — slope of top5 accdist over 5/10/20 days
6. **Silent Accumulation** — smart broker buying + price flat (< 2% change)
7. **Distribution Warning** — smart broker flips to net sell near recent high
8. **Broker Agreement** — count of top N brokers on same side
9. **Order Flow Profile** — avg value per transaction (institutional vs retail)
10. **Foreign vs Domestic Alignment** — foreign net vs smart broker direction

### Technical Signals (from indicators + support_resistance)

1. **RSI** — oversold (< 30) / overbought (> 70)
2. **MACD** — signal line crossover
3. **EMA Crossover** — EMA20/50 golden cross / death cross
4. **Volume Spike** — volume ratio > 2x average
5. **Bollinger Band** — squeeze then expansion/breakout
6. **S/R Break** — price breaks computed support/resistance levels

### Gap-Filling Metrics

- **Liquidity Gate & Discount** — 500M IDR hard floor + percentile-based multiplier within pool
- **Cross-Stock Broker Flow** — broker sells A, buys B same day (rotation detection)
- **Corporate Action Adjustment** — flag splits, rights, dividends that distort signals
- **Market-Wide Broker Sentiment** — aggregate smart broker net across all stocks

## How Base Rates Are Measured

For each (signal, stock) pair:
1. Scan full history (Jan 2020 to present)
2. Find every occurrence of the signal
3. Measure 5/10/20 day forward returns after each occurrence
4. Output: hit rate (% positive), avg return, sample size (n)

Signal combinations (e.g. broker accumulation + volume spike) measured as their own signal.

## Context Layers (not scored, shown as labels)

### Market Regime
- risk_on / cautious / risk_off
- Based on: IHSG trend, foreign flow direction, USD/IDR, US 10Y

### Stock Trend (EMA stack)
- ↗️ uptrend: EMA20 > EMA50 > EMA200
- ↘️ downtrend: EMA20 < EMA50 < EMA200
- ➡️ sideways: mixed

## Screener Output Format

```
📡 Scanner — 30 Apr 2026
Regime: risk_off | IHSG 6,940 | Foreign -5.2T/5d

$BBNI ↗️ — 3 signals
• ZP accumulating 5d
• Volume spike + broker buy
• RSI oversold (32)

$TAPG ➡️ — 2 signals
• CC net buy 3d
• BB squeeze breakout
```

Only signals with >55% hit rate shown. Ranked by signal count.
Low sample size (n < 15) gets ⚠️ warning.

## Drill-Down (on demand)

When user asks for detail on a stock, show full stats per signal:
```
ZP accumulating on BBNI
→ 47 past occurrences, 66% up after 10d, avg +2.8%
```

## Liquidity Tiers

Percentile rank within 300-stock pool by 20-day avg daily value:
- Top quartile (75-100th pctl): no discount
- 50-75th: mild discount (~0.85x)
- 25-50th: moderate discount (~0.65x)
- Bottom quartile: heavy discount (~0.4x) or exclude
- Hard floor: 500M IDR/day (safety net for dead stocks)

Multipliers to be calibrated from backtest results.

## Signal Log (self-tracking)

Every scanner output logged with:
- date, stock, active signals, trend state, regime
- Auto-filled with 5/10/20 day forward returns as days pass
- Answers: does the system actually surface good opportunities?
