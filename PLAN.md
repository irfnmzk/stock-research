# Signal Scoring System - Data Model & Plan

## Goal

Build a data-driven signal scoring system for IDX stocks. Replace gut-feel and arbitrary thresholds with measured base rates. System provides odds, user applies discretion.

## Architecture

Closed loop. System collects data, computes metrics, scores signals, logs output, tracks its own accuracy. User reads ranked signals with base rates, overlays macro view, and makes the call.

## Data Foundation

- `prices` -- OHLCV, foreign flow, volume, frequency (Jan 2020 to now)
- `broker_summary` -- per broker per stock per day (buy/sell lots, value, avg price, frequency)
- `bandar_detector` -- top 1/3/5/10 concentration, accdist, buyer/seller count

---

## Metrics (15 total)

### Core Bandarmology Metrics

1. **Broker Timing Score**
   - Source: broker_summary + prices
   - Per (broker, ticker) pair, rolling hit rate and avg forward return after net buy days
   - Answers: which brokers are actually smart on which stocks?

2. **Accumulation Streak**
   - Source: broker_summary
   - Consecutive net buy days per broker per stock, cumulative lots/value
   - Answers: is this conviction or noise?

3. **Broker Concentration**
   - Source: broker_summary + bandar_detector
   - Top broker net value / total value
   - Answers: is someone intentionally positioning?

4. **Buyer Seller Imbalance**
   - Source: bandar_detector
   - Buyer/seller ratio + price direction
   - Answers: is strong hand absorbing supply?

5. **Acc/Dist Phase**
   - Source: bandar_detector accdist over time
   - Slope of top5 accdist line over 5/10/20 days
   - Answers: accumulation or distribution phase?

6. **Silent Accumulation**
   - Source: broker_summary + prices
   - Smart broker buying + price flat (< 2% change)
   - Answers: is smart money building before a move?

7. **Distribution Warning**
   - Source: broker_summary + prices
   - Smart broker flips to net sell near recent high
   - Answers: is it time to exit?

8. **Broker Agreement**
   - Source: broker_summary
   - Count of top N brokers on same side
   - Answers: is this one broker or consensus?

9. **Order Flow Profile**
   - Source: broker_summary (freq, value)
   - Avg value per transaction (value / freq) per broker
   - Answers: institutional or retail flow?

10. **Foreign vs Domestic Alignment**
    - Source: prices (foreign_buy/sell) + broker_summary
    - Foreign net direction vs smart broker direction, measure who predicted correctly
    - Answers: when they disagree, who wins?

### Gap-Filling Metrics

11. **Liquidity Gate & Discount**
    - Two roles: pool gate + score multiplier
    - **Hard floor:** 500M IDR avg daily value (safety net for dead/suspended stocks only)
    - **Percentile rank:** rank all 300 stocks by 20-day avg daily value, convert to percentile (0-100)
    - **Liquidity tiers** (multipliers calibrated from backtest):
      - Top quartile (75-100th pctl): 1.0x (no discount)
      - 50-75th pctl: ~0.85x
      - 25-50th pctl: ~0.65x
      - Bottom quartile (0-25th pctl): ~0.4x or exclude
    - Percentile-based so it adapts to market volume shifts over time (no hardcoded IDR thresholds except the safety floor)
    - Also normalizes broker signal interpretation: 500 lots on BBCA is noise, 500 lots on a small cap is the whole market

12. **Cross-Stock Broker Flow**
    - Detect when a broker sells stock A and buys stock B on the same day
    - Answers: is this an exit or rotation?
    - Changes signal interpretation entirely

13. **Corporate Action Adjustment**
    - Flag dates with stock splits, rights issues, dividends that distort price/volume
    - Answers: is this signal real or an artifact?

14. **Market-Wide Broker Sentiment**
    - Aggregate smart broker net buy/sell across all stocks
    - Answers: is this a stock-specific signal or a macro call?

### System Metrics

15. **Signal Log**
    - Every signal trigger logged with date, stock, score, signals fired, regime
    - Auto-filled with 5/10/20 day forward returns as days pass
    - Answers: does the scoring system actually work?

---

## Technical Signals (6 total)

Simple, widely-used indicators that most IDX traders watch. Self-reinforcing because of adoption.

1. **RSI** -- oversold (< 30) / overbought (> 70)
2. **MACD** -- signal line crossover (bullish/bearish)
3. **Moving Average Crossover** -- EMA20/50 golden cross / death cross
4. **Volume Spike** -- volume ratio > 2x average
5. **Bollinger Band** -- squeeze (low BB width) then expansion/breakout
6. **Support/Resistance Break** -- price breaks computed S/R levels

All already computed in the `indicators` and `support_resistance` tables. No new data needed.

### Measuring Technical Base Rates

Same method as broker signals. For each (signal, stock) pair, scan full dataset, find every occurrence, measure 5/10/20 day forward returns. Output: hit rate, avg return, sample size.

### Measuring Signal Base Rates

For each (signal, stock) pair, scan full history, find every occurrence, measure 5/10/20 day forward returns. Output per signal: hit rate, avg return, sample size.

Also measure common combinations (e.g. broker accumulation + volume spike) as their own signal to see if combos improve hit rate over individual signals.

---

## Screener Output

**Old:** Pass/fail filters with arbitrary thresholds, flat list of matches.

**New:** Show active signals per stock, minimal format. Stats available on drill-down.

Daily scan format (Telegram):
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

Ranked by signal count. Only signals with >55% hit rate shown. Trend emoji per stock, regime at top. Low sample warnings where needed.

On-demand drill-down per stock: full stats (hit rate, avg return, sample size), chart, broker breakdown.

---

## Smart Broker List

**Old:** Static global list (YP, CC, ZP, AK, RX, KS, MS).

**New:** Per-ticker rolling ranking based on measured timing accuracy. ZP might be smart on BBCA but noise on INCO. List updates itself as data accumulates.

---

## Portfolio & Risk Management

Track capital, exposure, and risk per position. System computes sizing, user decides entries.

- **Capital:** stored and updated on deposit/withdrawal
- **Risk per trade:** fixed % of capital (default 2%, adjustable)
- **Position sizing:** derived from stop distance and risk budget. (entry - stop) × lots ≤ max risk per trade
- **Tranche sizing:** total position / planned tranches
- **Exposure %:** deployed capital / total capital
- **Portfolio heat:** sum of open risk across all positions (cap at 6-8%)
- **Cash available:** capital - deployed

When scanner flags a stock, system shows: suggested lot size per tranche based on stop level and remaining risk budget.

---



- Survivorship bias in pool (current top 300, not historical). Mitigated by pool tenure filter.
- No intraday data (can't see open vs close buying)
- No coordinated broker detection (handoff patterns)
- No wash trading detection (partially possible)
- No sector rotation at broker level (possible to add later)

---

## Execution Order

1. Price backfill (done)
2. Broker backfill (done -- 1,652 dates, 2020-01-01 to 2026-04-30)
3. Compute signal base rates (broker signals per stock)
4. Compute technical signal base rates
5. Measure signal combinations
6. Build screener (active signals + stats display)
7. Add signal log (track forward returns)
8. Integrate into daily EOD pipeline
9. Iterate based on results

---

## Design Principles

- Full dataset for base rates, no train/test split (not enough history)
- Always show sample size so user can judge confidence
- Regime and trend shown as context labels, not scoring modifiers. User interprets context.
- Stock-level trend detection via EMA20/50/200 stack (uptrend, downtrend, sideways).
- No composite scoring. System shows signal stats, user decides.
- Per-ticker broker ranking where sample size allows, fall back to sector or global when it doesn't
- Start simple, iterate based on results
- System measures itself via signal log forward returns
