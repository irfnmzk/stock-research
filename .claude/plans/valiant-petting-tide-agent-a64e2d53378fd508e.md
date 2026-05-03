# Implementation Plan: backtest_scanner.py

## Summary

A standalone script that replays scanner logic against historical `signal_events` data to measure whether multi-signal stock-days outperform the baseline. Pure SQL against SQLite — no Signal object reconstruction needed.

---

## Key Design Decisions

### 1. Forward return per stock-day

**Confirmed:** All signals for the same (symbol, date) have identical `fwd_10d` values (verified: 0 stock-days with differing fwd_10d). The backtest can safely pick any row's fwd return per stock-day — use `MIN(fwd_10d)` or just group by.

### 2. Baseline comparison

Use **"all signal stock-days on the same date"** as the baseline. This answers: "does picking stocks with 2+ signals beat picking any stock that had at least one signal that day?" This is the most honest comparison because:
- It controls for market-wide momentum (good/bad days affect both)
- It uses the same universe (stocks active enough to fire signals)
- Overall baseline: +1.74% avg fwd_10d across 61K stock-days

Also compute a secondary "unconditional" baseline: avg fwd_10d across ALL stock-days in the period (for context).

### 3. Liquidity filter — historical ADV

Compute the 20-day trailing average daily value (`prices.value`) as of each signal date. This is the correct approach because:
- The prices table has `value` data from 2020-01-02 to 2026-04-30
- Using current ADV would introduce look-ahead bias
- Query: `AVG(value) FROM prices WHERE symbol=? AND date <= ? ORDER BY date DESC LIMIT 20`

For performance, pre-compute ADV per (symbol, date) in a single pass rather than per-row lookups.

### 4. Thresholds to test

Test signal count thresholds: **2+, 3+, 4+** (and optionally 5+ though n=19 is too small for significance).

### 5. Base rate filtering mode

Two modes, matching scanner.py:
- **Simple mode:** count all unique signal types, threshold applies
- **Base rate filtered mode:** only count signals where the signal_type+direction has `avg_return_10d >= 1.0%` in `signal_base_rates` (full-period rates, per settled decision)

From the market-wide base rates, the signals that pass the 1.0% filter are:
- bb_squeeze_release (bullish): +1.79%
- broker_significance (bullish): +2.39%
- buyer_seller_imbalance (bullish): +2.18%
- ema_cross (bullish): +1.26%
- macd_histogram_flip (bullish): +1.26%
- sr_break (bearish): +1.50%
- volume_spike (bullish): +2.45%

All pass except `broker_significance bearish` (+0.01%). So the base rate filter mainly removes bearish broker_significance signals from the count.

### 6. Top-N selection

The live scanner returns top 5 by signal count. The backtest should:
- First: report stats for ALL stocks meeting the threshold (no top-N cap) — this gives the full picture
- Second: also report stats for "top 5 per day" to match live scanner behavior

### 7. Output format

Print a summary table to stdout + write a CSV with per-pick detail for further analysis.

---

## Implementation Structure

```
backtest_scanner.py
├── CLI (argparse): --config, --mode (simple|base_rate), --top-n, --csv
├── _load_config()
├── _get_signal_base_rates(db) → dict of (signal_type, direction) → avg_return_10d
├── _compute_historical_adv(db) → dict of (symbol, date) → adv_20d
├── _build_stock_days(db, mode, base_rates) → DataFrame/list of scanner picks
├── _compute_baseline(db) → per-date baseline returns
├── _run_backtest(picks, baseline) → metrics dict
├── _print_results(metrics)
├── _write_csv(picks, path)
└── main()
```

---

## Step-by-Step Implementation

### Step 1: Boilerplate and CLI

Follow `backfill_signals.py` pattern:
- argparse with `--config` (default config.yaml), `--mode` (simple|base_rate, default simple), `--top-n` (default 5, 0=no cap), `--csv` (optional output path), `--min-signals` (default 2)
- Load config, open DB via `get_db(cfg)`

### Step 2: Load base rates (for base_rate mode)

```python
def _get_signal_base_rates(db):
    """Load market-wide base rates. Returns {(signal_type, direction): avg_return_10d}."""
    rows = db.execute("""
        SELECT signal_type, direction, avg_return_10d
        FROM signal_base_rates
        WHERE (symbol IS NULL OR symbol = '') AND (broker_code IS NULL OR broker_code = '')
    """).fetchall()
    return {(r["signal_type"], r["direction"]): r["avg_return_10d"] for r in rows}
```

For per-ticker fallback (matching scanner.py logic): also load per-ticker rates and use them when available with sample_size >= 15.

### Step 3: Compute historical ADV

Rather than computing per stock-day (expensive), use a window approach:

```python
def _compute_historical_adv(db):
    """Compute 20-day trailing ADV for each (symbol, date) that has signal_events.
    Returns dict: (symbol, date) → avg_daily_value."""
```

Strategy: For each unique (symbol, date) in signal_events, compute the trailing 20-day avg of `prices.value`. This can be done efficiently with a single SQL query using a correlated subquery or by fetching all prices and computing in Python with pandas rolling.

Given 61K stock-days and 410K price rows, the pandas approach is faster:
1. Load all prices (symbol, date, value) into a DataFrame
2. Sort by (symbol, date), groupby symbol, rolling(20).mean() on value
3. Join back to signal_events dates

### Step 4: Build scanner picks

```python
def _build_stock_days(db, mode, base_rates, adv_lookup, liquidity_floor, min_signals):
    """
    For each trading date, identify stocks that would appear in scanner.
    
    Returns list of dicts:
        {symbol, date, signal_count, signal_types, fwd_5d, fwd_10d, fwd_20d, adv}
    """
```

Core query:
```sql
SELECT symbol, date,
       GROUP_CONCAT(DISTINCT signal_type) as signal_types,
       COUNT(DISTINCT signal_type) as signal_count,
       MIN(fwd_5d) as fwd_5d,
       MIN(fwd_10d) as fwd_10d,
       MIN(fwd_20d) as fwd_20d
FROM signal_events
WHERE filled_through >= 20
GROUP BY symbol, date
```

Then in Python:
- Filter by ADV >= liquidity_floor (500M)
- In base_rate mode: recount signal_types keeping only those with avg_return_10d >= 1.0%
- Filter by signal_count >= min_signals
- Optionally cap to top-N per date (sorted by signal_count desc)

### Step 5: Compute baseline

```python
def _compute_baseline(db):
    """Per-date average fwd return across all signal stock-days (deduplicated to symbol-date).
    Returns dict: date → {avg_fwd_5d, avg_fwd_10d, avg_fwd_20d, n}."""
```

```sql
SELECT date, AVG(fwd_10d) as avg_fwd10, AVG(fwd_5d) as avg_fwd5, AVG(fwd_20d) as avg_fwd20, COUNT(*) as n
FROM (
    SELECT DISTINCT symbol, date, fwd_5d, fwd_10d, fwd_20d
    FROM signal_events WHERE filled_through >= 20
)
GROUP BY date
```

### Step 6: Compute metrics

For each threshold (2+, 3+, 4+):

```python
def _compute_metrics(picks, baseline):
    """
    Returns:
        avg_fwd_5d, avg_fwd_10d, avg_fwd_20d  — raw avg return of picks
        hit_rate_5d, hit_rate_10d, hit_rate_20d — % of picks with positive return
        excess_vs_baseline_10d — avg(pick_fwd_10d - same_day_baseline_fwd_10d)
        picks_per_day — avg number of picks per trading day
        total_picks — total stock-days
        active_days — days with at least one pick
        sharpe_like — mean(daily_excess) / std(daily_excess) * sqrt(252)
    """
```

The Sharpe-like ratio: for each day that has picks, compute the average pick return minus that day's baseline. Then take mean/std of those daily excess returns, annualize.

### Step 7: Print results

```
Scanner Backtest Results
========================
Period: 2020-01-02 to 2026-04-01 (1,503 trading days)
Mode: simple | Liquidity floor: 500M IDR | Top-N: 5

Threshold  Picks   Days   Picks/Day  Avg 5d   Avg 10d  Avg 20d  Hit10d  Excess10d  Sharpe
─────────  ─────   ────   ─────────  ──────   ───────  ───────  ──────  ─────────  ──────
2+         10,449  1,478  7.1        +X.XX%   +2.30%   +X.XX%   XX.X%   +0.55%     X.XX
3+          1,894    XXX  X.X        +X.XX%   +1.41%   +X.XX%   XX.X%   +X.XX%     X.XX
4+            265    XXX  X.X        +X.XX%   +0.75%   +X.XX%   XX.X%   +X.XX%     X.XX

Baseline (all signal stock-days): avg_fwd_10d = +1.74%, n = 61,163

With top-5 cap per day:
Threshold  Picks   Days   Picks/Day  Avg 5d   Avg 10d  Avg 20d  Hit10d  Excess10d  Sharpe
─────────  ─────   ────   ─────────  ──────   ───────  ───────  ──────  ─────────  ──────
2+         X,XXX   X,XXX  X.X        ...
```

### Step 8: CSV output (optional)

Columns: `date, symbol, signal_count, signal_types, fwd_5d, fwd_10d, fwd_20d, adv, baseline_fwd_10d, excess_10d`

---

## Performance Considerations

- The entire dataset fits in memory (61K stock-days, 410K price rows for ADV)
- Use pandas for the rolling ADV computation and final metrics — it's already a dependency
- Single DB connection, read-only queries
- Expected runtime: < 5 seconds

## Edge Cases

- Days with no picks at a given threshold: skip in per-day metrics (don't divide by zero)
- Stocks without 20 days of price history for ADV: exclude (they wouldn't pass liquidity floor anyway)
- The 20 most recent trading days (2026-04-01 to 2026-04-30) lack fwd_20d — already filtered by `filled_through >= 20`

## File Location

`/home/zuck/Work/personal/stock-research/backtest_scanner.py` — alongside the other standalone scripts.

---

## Dependencies

All already available:
- `sqlite3` (stdlib)
- `pandas` (in pyproject.toml)
- `yaml` (pyyaml in pyproject.toml)
- `argparse`, `pathlib`, `time` (stdlib)

No new dependencies needed.
