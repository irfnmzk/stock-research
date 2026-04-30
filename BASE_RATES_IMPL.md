# Base Rates Implementation Plan

## Overview

Replace gut-feel signal scoring with measured base rates. Scan 6 years of historical data (2020-01-01 to present), detect every signal occurrence, measure forward returns, and produce evidence-based stats.

**Current state:** signals.py computes scores on the fly with hardcoded weights. No persistence, no measurement, no feedback loop.

**Target state:** every signal has a measured hit rate, avg return, and sample size per stock. System shows evidence, user decides.

---

## Prerequisites (DONE)

- [x] Price backfill: 410K rows, 310 symbols, 2020-01-01 to 2026-04-30
- [x] Broker backfill: 12.6M rows in broker_summary, 375K in bandar_detector, 1,652 dates
- [x] Indicator backfill: 408K rows, 300 stocks, full history
- [x] New tables added to db.py: signal_events, signal_base_rates, broker_rankings

---

## New Data Model

### signal_events

Raw signal occurrences with context and forward returns. This is the foundation. Every analytical query starts here.

```sql
CREATE TABLE IF NOT EXISTS signal_events (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol          TEXT NOT NULL,
    date            TEXT NOT NULL,
    signal_type     TEXT NOT NULL,    -- e.g. 'broker_timing', 'accumulation_streak'
    broker_code     TEXT,             -- real column, indexed, not buried in JSON
    magnitude       REAL,             -- signal strength (streak length, concentration %, RSI value)
    close           REAL,             -- price at signal time (denormalized)
    volume_ratio    REAL,             -- volume context (denormalized)
    regime          TEXT,             -- risk_on / cautious / risk_off (denormalized)
    trend           TEXT,             -- uptrend / downtrend / sideways from EMA stack
    fwd_5d          REAL,             -- % return after 5 trading days
    fwd_10d         REAL,             -- % return after 10 trading days
    fwd_20d         REAL,             -- % return after 20 trading days
    filled_through  INTEGER DEFAULT 0, -- 0/5/10/20, how far forward returns are filled
    meta            TEXT              -- JSON for rarely-queried extras
);

-- Indexes for common query patterns
CREATE INDEX idx_se_type_symbol ON signal_events(signal_type, symbol);
CREATE INDEX idx_se_broker ON signal_events(broker_code, symbol) WHERE broker_code IS NOT NULL;
CREATE INDEX idx_se_date ON signal_events(date);
CREATE INDEX idx_se_regime ON signal_events(regime, signal_type);
CREATE UNIQUE INDEX idx_se_dedup ON signal_events(symbol, date, signal_type, broker_code);
```

**Why denormalize context?** Slicing by regime/trend requires joining prices+macro+indicators on every query. With 2-4M events, that's expensive. Storing context at signal time makes WHERE clauses simple.

**Why `filled_through` instead of boolean?** Recent events may have 5d returns filled but not 20d yet. This tracks partial fills.

**Estimated size:** 2-4M rows for broker signals, 200-500K for other metrics. SQLite handles this fine.

### signal_base_rates

Aggregated stats cache. Recomputed from signal_events. Not the source of truth.

```sql
CREATE TABLE IF NOT EXISTS signal_base_rates (
    signal_type     TEXT NOT NULL,
    symbol          TEXT,             -- NULL = global stats across all stocks
    broker_code     TEXT,             -- NULL for non-broker signals
    sample_size     INTEGER,
    hit_rate_5d     REAL,             -- % of events where fwd return > 0
    hit_rate_10d    REAL,
    hit_rate_20d    REAL,
    avg_return_5d   REAL,
    avg_return_10d  REAL,
    avg_return_20d  REAL,
    median_return_5d  REAL,
    median_return_10d REAL,
    median_return_20d REAL,
    last_computed   TEXT,
    PRIMARY KEY (signal_type, symbol, broker_code)
);
```

### broker_rankings

Three-level hierarchy for smart broker identification. This is an INPUT for metrics 2, 6, 7, 8, 10.

```sql
CREATE TABLE IF NOT EXISTS broker_rankings (
    symbol          TEXT,             -- stock ticker (NULL for global)
    sector          TEXT,             -- sector name (NULL for per-ticker and global)
    broker_code     TEXT NOT NULL,
    level           TEXT NOT NULL,    -- 'ticker' / 'sector' / 'global'
    hit_rate_5d     REAL,
    hit_rate_10d    REAL,
    avg_return_5d   REAL,
    avg_return_10d  REAL,
    sample_size     INTEGER,
    rank            INTEGER,          -- 1 = best timing at this level
    is_smart        INTEGER DEFAULT 0,
    last_computed   TEXT,
    PRIMARY KEY (level, symbol, sector, broker_code)
);

CREATE INDEX idx_br_smart ON broker_rankings(symbol) WHERE is_smart = 1;
```

**Fallback hierarchy for smart broker lookup:**
1. Per-ticker (if sample_size >= 30)
2. Per-sector (if sample_size >= 50)
3. Global (if sample_size >= 100)
4. No ranking (insufficient data)

**is_smart threshold:** hit_rate_5d > 55% AND sample_size >= threshold for level AND avg_return > 0. Calibrate after seeing actual distribution.

---

## New File: base_rates.py

Single module with all computation logic. Structure:

```python
# --- Helpers ---
def get_trading_dates(db, symbol)
    # Returns sorted list of trading dates for a symbol from prices table

def forward_return(prices_by_date, date, days)
    # Given a dict of {date: close}, compute % return N trading days forward
    # Returns None if not enough future data

def get_trend(ema20, ema50, ema200)
    # EMA stack: ema20 > ema50 > ema200 = uptrend, reverse = downtrend, else sideways

def get_regime(db, date)
    # Look up macro regime for a date from macro table
    # Cache results since regime changes infrequently

def insert_events(db, events)
    # Batch insert into signal_events with INSERT OR IGNORE (dedup index handles duplicates)

# --- Forward Return Filler ---
def fill_forward_returns(db, signal_type=None)
    # For all events where filled_through < 20, compute missing forward returns
    # Uses prices table, handles partial fills (5d available but not 20d)

# --- Aggregation ---
def aggregate_base_rates(db, signal_type=None)
    # Group signal_events by (signal_type, symbol, broker_code)
    # Compute hit_rate, avg_return, median_return, sample_size
    # Write to signal_base_rates
    # Also compute global stats (symbol=NULL)

# --- Metric 1: Broker Timing ---
def scan_broker_timing(db, cfg)
    # For every (broker, stock, date) in broker_summary where net_value > 0:
    #   - magnitude = net_value / avg_daily_value (normalized)
    #   - context: close, volume_ratio, regime, trend from that date
    #   - Insert into signal_events with signal_type='broker_timing'
    # This scans ALL brokers, not just the static smart list

def compute_broker_rankings(db, cfg)
    # After broker_timing events have forward returns filled:
    # 1. Per-ticker: group by (symbol, broker_code), compute stats, rank, mark is_smart
    # 2. Per-sector: group by (sector, broker_code), compute stats, rank, mark is_smart
    # 3. Global: group by (broker_code), compute stats, rank, mark is_smart
    # Write all to broker_rankings table

def get_smart_brokers(db, symbol, sector=None)
    # Fallback lookup: ticker -> sector -> global
    # Returns list of broker_codes marked is_smart

# --- Metric 2: Accumulation Streak ---
def scan_accumulation_streak(db, cfg)
    # For each smart broker (from broker_rankings) on each stock:
    #   - Find consecutive net buy days (net_value > 0)
    #   - Signal fires on day 2+ of streak
    #   - magnitude = streak length
    #   - meta: cumulative net_value over streak
    #   - signal_type = 'accumulation_streak'

# --- Metric 3: Broker Concentration ---
def scan_broker_concentration(db, cfg)
    # For each (symbol, date) in bandar_detector:
    #   - concentration = abs(top1_net) / total_value
    #   - Signal fires when concentration > threshold (e.g. 0.20)
    #   - magnitude = concentration ratio
    #   - broker_code = NULL (aggregate signal)
    #   - signal_type = 'broker_concentration'

# --- Metric 4: Buyer Seller Imbalance ---
def scan_buyer_seller_imbalance(db, cfg)
    # For each (symbol, date) in bandar_detector:
    #   - ratio = total_buyers / total_sellers (handle zero)
    #   - Signal fires when ratio > 2.0 (buy imbalance) or < 0.5 (sell imbalance)
    #   - magnitude = ratio
    #   - signal_type = 'buyer_seller_imbalance'

# --- Metric 5: Acc/Dist Phase ---
def scan_accdist_phase(db, cfg)
    # Map accdist labels to numeric:
    #   Big Acc=3, Normal Acc=2, Small Acc=1, Neutral=0,
    #   Small Dist=-1, Normal Dist=-2, Big Dist=-3
    # For each (symbol, date), compute slope of top5_accdist over last 10 days
    # Signal fires on phase TRANSITION (slope flips sign)
    #   - magnitude = slope value
    #   - meta: from_phase, to_phase
    #   - signal_type = 'accdist_phase_change'

# --- Metric 6: Silent Accumulation ---
def scan_silent_accumulation(db, cfg)
    # For each (symbol, date):
    #   - Check if smart brokers (from broker_rankings) are net buying
    #   - Check if price change over last 10 days < 2%
    #   - Signal fires when both conditions met
    #   - magnitude = cumulative smart broker net_value during flat period
    #   - signal_type = 'silent_accumulation'

# --- Metric 7: Distribution Warning ---
def scan_distribution_warning(db, cfg)
    # For each (symbol, date):
    #   - Check if smart brokers are net selling
    #   - Check if price is within 5% of 20-day high
    #   - Signal fires when both conditions met
    #   - magnitude = net sell value
    #   - signal_type = 'distribution_warning'

# --- Metric 8: Broker Agreement ---
def scan_broker_agreement(db, cfg)
    # For each (symbol, date):
    #   - Count smart brokers with net_value > 0 (buy side)
    #   - Count smart brokers with net_value < 0 (sell side)
    #   - Signal fires when buy_count >= 3 or sell_count >= 3
    #   - magnitude = count on dominant side
    #   - meta: list of agreeing broker codes, direction (buy/sell)
    #   - signal_type = 'broker_agreement'

# --- Metric 9: Order Flow Profile ---
def scan_order_flow_profile(db, cfg)
    # For each (broker, stock, date) in broker_summary:
    #   - avg_txn_size = (buy_value + sell_value) / freq
    #   - Classify: institutional (top 10th percentile of avg_txn_size for that stock)
    #   - Signal fires when institutional-sized flow detected with net direction
    #   - magnitude = avg_txn_size
    #   - signal_type = 'institutional_flow'
    # This can feed into metric 1 as a filter (only count timing when flow is institutional)

# --- Metric 10: Foreign vs Domestic Alignment ---
def scan_foreign_domestic_alignment(db, cfg)
    # For each (symbol, date):
    #   - foreign_net = foreign_buy - foreign_sell (from prices)
    #   - smart_net = sum of net_value for smart brokers (from broker_summary)
    #   - Signal fires when they DISAGREE (one positive, one negative)
    #   - magnitude = abs difference
    #   - meta: foreign_direction, smart_direction, foreign_net, smart_net
    #   - signal_type = 'foreign_domestic_divergence'
    # Forward returns tell us who was right

# --- Main Pipeline ---
def compute_all_base_rates(db, cfg)
    # Full pipeline:
    # 1. scan_broker_timing (all brokers)
    # 2. fill_forward_returns for broker_timing
    # 3. compute_broker_rankings (produces smart broker lists)
    # 4. scan metrics 3, 4, 5 (independent)
    # 5. scan metrics 2, 6, 7, 8, 9, 10 (depend on smart broker rankings)
    # 6. fill_forward_returns for all remaining
    # 7. aggregate_base_rates for all signal types
```

---

## Bandarmology Metrics Detail

### Metric 1: Broker Timing Score

**Purpose:** Determine which brokers are actually good at timing entries on which stocks. Foundation for all other broker-dependent metrics.

**Detection logic:**
- Scan broker_summary for every row where net_value > 0 (net buy)
- One event per (broker, stock, date) combination
- magnitude = net_value / stock's 20-day avg daily value (normalized so 0.5 means the broker's net buy was half the stock's average daily turnover)

**Context captured:**
- close: closing price that day
- volume_ratio: from indicators table
- regime: from macro table (risk_on/cautious/risk_off)
- trend: from EMA stack (ema20 vs ema50 vs ema200)

**Output:** ~2-4M events. After forward return fill, aggregate into broker_rankings at three levels.

**Estimated runtime:** Scanning 12.6M broker_summary rows, filtering net_value > 0, joining indicators + macro for context. Batch insert. ~5-10 minutes.

### Metric 2: Accumulation Streak

**Purpose:** Does conviction (consecutive buying days) predict better returns than single-day buys?

**Detection logic:**
- For each smart broker (from broker_rankings) on each stock
- Track consecutive days where net_value > 0
- Signal fires on day 2, 3, 4, 5+ of streak (one event per day of streak)
- magnitude = current streak length
- meta: cumulative_net_value over the streak

**Key question answered:** Is a 5-day streak meaningfully better than a 2-day streak? Or does the move already happen by day 3?

**Depends on:** broker_rankings (metric 1 must complete first)

### Metric 3: Broker Concentration

**Purpose:** When one broker dominates the day's flow, is that predictive?

**Detection logic:**
- For each (symbol, date) in bandar_detector
- concentration = abs(top1_net) / total_value
- Signal fires when concentration > 0.20 (top broker is >20% of total flow)
- magnitude = concentration ratio (0.0 to 1.0)
- meta: direction (top1_net positive = buy concentration, negative = sell concentration)

**Independent:** Uses bandar_detector only, no smart broker list needed.

**Note:** total_value can be 0 on very low-volume days. Skip those.

### Metric 4: Buyer Seller Imbalance

**Purpose:** When buyer count vastly exceeds seller count (or vice versa), does it predict direction?

**Detection logic:**
- For each (symbol, date) in bandar_detector
- ratio = total_buyers / max(total_sellers, 1)
- Signal fires when ratio > 2.0 (buy imbalance) or ratio < 0.5 (sell imbalance)
- magnitude = ratio
- meta: direction ('buy_imbalance' or 'sell_imbalance'), total_buyers, total_sellers

**Independent:** Uses bandar_detector only.

**Interesting case:** High buyer count + flat price = supply absorption (accumulation). High buyer count + price up = momentum. Base rates will differentiate.

### Metric 5: Acc/Dist Phase Change

**Purpose:** Does a transition from distribution to accumulation (or vice versa) predict price moves?

**Detection logic:**
- Map accdist labels to numeric scale:
  - Big Acc = 3, Normal Acc = 2, Small Acc = 1
  - Neutral = 0
  - Small Dist = -1, Normal Dist = -2, Big Dist = -3
- For each (symbol, date), look at top5_accdist over last 10 days
- Compute slope (linear regression or simple difference)
- Signal fires when slope flips sign (phase transition)
- magnitude = new slope value (positive = shifting to accumulation)
- meta: from_label, to_label

**Independent:** Uses bandar_detector only.

**Note:** accdist values in the DB are stored as TEXT labels, not numbers. The mapping happens in code.

### Metric 6: Silent Accumulation

**Purpose:** Smart money building positions while price is quiet. The "setup before the move" signal.

**Detection logic:**
- For each (symbol, date):
  1. Get smart brokers for this stock from broker_rankings
  2. Sum their net_value from broker_summary for the last 10 days
  3. Check if cumulative smart net > 0 (they're buying)
  4. Check if price change over last 10 days is < 2% (price is flat)
  5. Signal fires when both conditions met
- magnitude = cumulative smart broker net_value during flat period
- meta: price_change_pct, smart_broker_list, days_flat

**Depends on:** broker_rankings (metric 1)

**This is the highest-conviction signal in bandarmology theory.** If base rates confirm it, it should be weighted heavily.

### Metric 7: Distribution Warning

**Purpose:** Smart money exiting near highs. Sell/avoid signal.

**Detection logic:**
- For each (symbol, date):
  1. Get smart brokers from broker_rankings
  2. Sum their net_value for the last 5 days
  3. Check if cumulative smart net < 0 (they're selling)
  4. Check if current price is within 5% of 20-day high
  5. Signal fires when both conditions met
- magnitude = abs(cumulative smart net sell value)
- meta: price_vs_high_pct, smart_broker_list

**Depends on:** broker_rankings (metric 1)

**Note:** Forward returns here should be NEGATIVE if the signal works. Hit rate = % of times price dropped.

### Metric 8: Broker Agreement

**Purpose:** Does consensus among smart brokers improve signal quality?

**Detection logic:**
- For each (symbol, date):
  1. Get smart brokers from broker_rankings
  2. For each, check broker_summary net_value direction
  3. Count buy-side (net > 0) and sell-side (net < 0)
  4. Signal fires when max(buy_count, sell_count) >= 3
- magnitude = count on dominant side
- meta: direction ('buy'/'sell'), agreeing_brokers list, total_smart_active

**Depends on:** broker_rankings (metric 1)

**Key question:** Is 5/7 brokers agreeing meaningfully better than 3/7? Or is 3 already enough?

### Metric 9: Order Flow Profile

**Purpose:** Distinguish institutional from retail flow. Institutional flow is presumably more informed.

**Detection logic:**
- For each (broker, stock, date) in broker_summary where freq > 0:
  - avg_txn_size = (buy_value + sell_value) / freq
- Compute 90th percentile of avg_txn_size per stock (across all dates and brokers)
- Signal fires when a broker's avg_txn_size exceeds the 90th percentile AND has a net direction
- magnitude = avg_txn_size
- meta: percentile_rank, net_direction, broker_code

**Can feed into metric 1:** Filter broker_timing events to only institutional-sized flow and see if hit rates improve.

### Metric 10: Foreign vs Domestic Divergence

**Purpose:** When foreign investors and smart domestic brokers disagree, who's right?

**Detection logic:**
- For each (symbol, date):
  1. foreign_net = foreign_buy - foreign_sell (from prices table)
  2. smart_net = sum of net_value for smart brokers (from broker_summary)
  3. Signal fires when they disagree: (foreign_net > 0 AND smart_net < 0) OR (foreign_net < 0 AND smart_net > 0)
- magnitude = abs(foreign_net - smart_net)
- meta: foreign_net, smart_net, foreign_direction, smart_direction

**Depends on:** broker_rankings (metric 1)

**Output tells you:** For each stock, when they disagree, does following foreign or domestic produce better returns? This calibrates the weight of foreign flow vs bandarmology per ticker.

---

## CLI Commands

Add to main.py:

```
compute-base-rates [--signal TYPE] [--symbol SYM]
    Run full base rate pipeline or specific signal type.
    Without args: runs all metrics in order.
    --signal broker_timing: only metric 1
    --signal independent: metrics 3, 4, 5
    --signal dependent: metrics 2, 6, 7, 8, 9, 10

broker-rank SYMBOL
    Show per-ticker broker rankings with stats.
    Falls back to sector/global if insufficient per-ticker data.

base-rates [--signal TYPE] [--symbol SYM] [--min-sample N]
    Show aggregated base rate stats.
    Filter by signal type, symbol, minimum sample size.
```

---

## Execution Order

```
1. scan_broker_timing()           -- all brokers, all stocks, all dates
2. fill_forward_returns('broker_timing')
3. compute_broker_rankings()      -- produces smart broker lists at 3 levels
4. scan_broker_concentration()    -- metric 3 (independent)
5. scan_buyer_seller_imbalance()  -- metric 4 (independent)
6. scan_accdist_phase()           -- metric 5 (independent)
7. scan_accumulation_streak()     -- metric 2 (needs rankings)
8. scan_silent_accumulation()     -- metric 6 (needs rankings)
9. scan_distribution_warning()    -- metric 7 (needs rankings)
10. scan_broker_agreement()       -- metric 8 (needs rankings)
11. scan_order_flow_profile()     -- metric 9
12. scan_foreign_domestic()       -- metric 10 (needs rankings)
13. fill_forward_returns()        -- all remaining events
14. aggregate_base_rates()        -- compute stats for all signal types
```

**Estimated total runtime:** 15-30 minutes (mostly step 1 scanning 12.6M rows).

---

## Context Computation

For each signal event, we denormalize these fields:

**close:** From prices table for that (symbol, date).

**volume_ratio:** From indicators table for that (symbol, date).

**regime:** From macro table. Computed as:
- Look up USD/IDR trend, US 10Y, foreign flow
- Map to risk_on / cautious / risk_off
- Cache by date since regime is the same for all stocks on a given day

**trend:** From indicators table. EMA stack:
- ema20 > ema50 > ema200 = 'uptrend'
- ema20 < ema50 < ema200 = 'downtrend'
- else = 'sideways'

---

## Forward Return Computation

For a signal on date D for symbol S:
1. Get sorted list of trading dates for S from prices table
2. Find index of D in the list
3. fwd_5d = (close[D+5] - close[D]) / close[D] * 100
4. fwd_10d = (close[D+10] - close[D]) / close[D] * 100
5. fwd_20d = (close[D+20] - close[D]) / close[D] * 100

Where D+N means N trading days forward (skip weekends/holidays).

If D+N doesn't exist (recent events), leave as NULL and set filled_through accordingly.

---

## Aggregation Logic

For each group (signal_type, symbol, broker_code):

```python
hit_rate_Nd = count(fwd_Nd > 0) / count(fwd_Nd IS NOT NULL) * 100
avg_return_Nd = mean(fwd_Nd) where fwd_Nd IS NOT NULL
median_return_Nd = median(fwd_Nd) where fwd_Nd IS NOT NULL
sample_size = count(fwd_5d IS NOT NULL)  # use shortest window for sample size
```

Also compute global stats (symbol=NULL) for each signal_type.

---

## Smart Broker Threshold

After seeing the actual distribution from metric 1, calibrate:

**Per-ticker level:**
- sample_size >= 30
- hit_rate_5d > 55%
- avg_return_5d > 0

**Per-sector level:**
- sample_size >= 50
- hit_rate_5d > 55%
- avg_return_5d > 0

**Global level:**
- sample_size >= 100
- hit_rate_5d > 53% (lower bar since signal is diluted)
- avg_return_5d > 0

These are starting points. Adjust after inspecting the distribution.

---

## Design Decisions

1. **Store raw events, not just aggregates.** Enables slicing by regime, trend, time period, combinations. Aggregates are a cache.

2. **Compute metric 1 for ALL brokers, not just the static 7.** The static list is an assumption. Data should validate or replace it.

3. **Three-level broker ranking with fallback.** Per-ticker is most precise but sample-limited. Sector and global provide fallbacks.

4. **Denormalize context into signal_events.** Avoids expensive joins on every analytical query.

5. **Fixed forward return columns (5/10/20d).** Simpler than a separate table. Unlikely to need other windows.

6. **Dedup index on (symbol, date, signal_type, broker_code).** Prevents duplicate events on re-runs. INSERT OR IGNORE handles gracefully.

---

## Files to Create/Modify

**New:**
- `base_rates.py` -- all computation logic (helpers, scanners, aggregation, CLI entry points)

**Modified:**
- `db.py` -- new tables (DONE)
- `main.py` -- wire CLI commands (compute-base-rates, broker-rank, base-rates)
- `config.yaml` -- add base_rates section with thresholds (optional, can hardcode initially)
