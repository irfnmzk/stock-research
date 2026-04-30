# Database Schema

All data lives in a single SQLite database at `data/stock-research.db`.
(`data/market.db` exists but is unused; macro data is in the main DB.)

## Tables

### prices
Daily OHLCV + foreign flow. Primary data source for all analysis.

| Column | Type | Notes |
|--------|------|-------|
| symbol | TEXT | PK1 |
| date | TEXT | PK2, ISO format |
| open | REAL | |
| high | REAL | |
| low | REAL | |
| close | REAL | |
| volume | INTEGER | Share volume |
| value | REAL | Trade value (IDR) |
| frequency | INTEGER | Number of transactions |
| foreign_buy | REAL | Foreign buy value (IDR) |
| foreign_sell | REAL | Foreign sell value (IDR) |
| market_cap | REAL | |
| shares_outstanding | INTEGER | |
| freq_analyzer | TEXT | JSON frequency analysis from Stockbit |

Populated by: `fetch`, `fetch-pool`
Typical rows: ~38k (300 stocks × ~130 trading days)

---

### indicators
Computed technical indicators per symbol per day.

| Column | Type | Notes |
|--------|------|-------|
| symbol | TEXT | PK1 |
| date | TEXT | PK2 |
| ema20 | REAL | 20-day exponential moving average |
| ema50 | REAL | 50-day EMA |
| ema200 | REAL | 200-day EMA |
| rsi | REAL | 14-day RSI (0-100) |
| macd | REAL | MACD line |
| macd_signal | REAL | MACD signal line |
| macd_hist | REAL | MACD histogram (macd - signal) |
| bb_upper | REAL | Bollinger Band upper (20-day, 2σ) |
| bb_lower | REAL | Bollinger Band lower |
| bb_width | REAL | (upper - lower) / middle |
| atr | REAL | 14-day Average True Range |
| volume_ratio | REAL | Today's volume / 20-day avg volume |

Populated by: `indicators` command (reads from prices, writes here)
Depends on: prices

---

### broker_summary
Per-broker trading activity for each stock per day.

| Column | Type | Notes |
|--------|------|-------|
| symbol | TEXT | PK1 |
| date | TEXT | PK2 |
| broker_code | TEXT | PK3, e.g. "YP", "CC", "ZP" |
| broker_type | TEXT | "foreign" or "local" |
| buy_lot | INTEGER | |
| buy_value | REAL | |
| sell_lot | INTEGER | |
| sell_value | REAL | |
| net_lot | INTEGER | buy_lot - sell_lot |
| net_value | REAL | buy_value - sell_value |
| avg_price | REAL | |
| freq | INTEGER | Number of transactions |

Populated by: `fetch-brokers`
Key for: bandarmology (tracking smart broker accumulation/distribution)

---

### bandar_detector
Concentration metrics showing how much top brokers dominate trading.

| Column | Type | Notes |
|--------|------|-------|
| symbol | TEXT | PK1 |
| date | TEXT | PK2 |
| top1_net | REAL | Net value of #1 broker |
| top3_net | REAL | Combined net of top 3 |
| top5_net | REAL | Combined net of top 5 |
| top10_net | REAL | Combined net of top 10 |
| top1_accdist | REAL | Accumulation/distribution score, top 1 |
| top3_accdist | REAL | Acc/dist, top 3 |
| top5_accdist | REAL | Acc/dist, top 5 |
| top10_accdist | REAL | Acc/dist, top 10 |
| total_buyers | INTEGER | |
| total_sellers | INTEGER | |
| total_value | REAL | |

Populated by: `fetch-brokers`
Interpretation: High top5_net + few buyers = concentrated accumulation (bullish bandar signal)

---

### whale_scores
Composite whale/institutional activity scores per stock per day.

| Column | Type | Notes |
|--------|------|-------|
| symbol | TEXT | PK1 |
| date | TEXT | PK2 |
| foreign_flow_score | REAL | Normalized foreign net flow score |
| broker_score | REAL | Smart broker activity score |
| composite_score | REAL | Weighted combination |

Populated by: `indicators` command (whale scoring module)
Depends on: prices (foreign flow), broker_summary (smart broker tracking)

---

### support_resistance
Detected support and resistance price levels.

| Column | Type | Notes |
|--------|------|-------|
| symbol | TEXT | PK1 |
| level | REAL | PK2, price level |
| level_type | TEXT | PK3, "support" or "resistance" |
| touch_count | INTEGER | How many times price touched this level |
| last_touched | TEXT | Date of last touch |
| strength_score | REAL | Higher = more significant level |

Populated by: `sr` command
Depends on: prices

---

### companies
Stock universe with sector classification.

| Column | Type | Notes |
|--------|------|-------|
| symbol | TEXT | PK |
| name | TEXT | Company name |
| sector_id | INTEGER | Stockbit sector ID (1-51) |
| sector_name | TEXT | Indonesian sector name |
| subsector_id | INTEGER | |
| subsector_name | TEXT | |
| market_cap | REAL | Latest market cap |
| last_price | REAL | |
| avg_volume | INTEGER | |
| tradeable | INTEGER | 1 = actively traded |

Populated by: `fetch-companies`
Rows: ~1966 (full IDX universe)

---

### scan_pool
Top 300 stocks by market cap, refreshed weekly.

| Column | Type | Notes |
|--------|------|-------|
| symbol | TEXT | PK |
| market_cap | REAL | Market cap at ranking time |
| rank | INTEGER | 1 = largest |
| updated_at | TEXT | When pool was last refreshed |

Populated by: `refresh-pool`
Used by: `fetch-pool`, `screen --pool`

---

### sector_rotation
Sector performance and momentum rankings.

| Column | Type | Notes |
|--------|------|-------|
| sector | TEXT | PK1, IDX index name (e.g. IDXFINANCE) |
| date | TEXT | PK2 |
| pct_5d | REAL | 5-day sector return % |
| pct_10d | REAL | 10-day return % |
| pct_20d | REAL | 20-day return % |
| rank_5d | INTEGER | Rank among sectors (1 = best) |
| rank_10d | INTEGER | |
| rank_20d | INTEGER | |
| momentum | REAL | Composite momentum score |

Populated by: `indicators` command (sector module)
Note: Uses IDX index names, mapped from companies.sector_name via dict in screener.py

---

### fundamentals
Valuation ratios per stock.

| Column | Type | Notes |
|--------|------|-------|
| symbol | TEXT | PK1 |
| date | TEXT | PK2 |
| pe_ttm | REAL | Price/Earnings trailing 12m |
| pe_forward | REAL | Forward P/E |
| pbv | REAL | Price/Book Value |
| ps_ttm | REAL | Price/Sales |
| pcf_ttm | REAL | Price/Cash Flow |
| ev_ebitda | REAL | Enterprise Value / EBITDA |
| peg | REAL | P/E to Growth |
| earnings_yield | REAL | |
| dividend_yield | REAL | |

Populated by: `fetch-fundamentals`

---

### macro
Macro indicators (USD/IDR, US 10Y yield, BI Rate, aggregate foreign flow).

| Column | Type | Notes |
|--------|------|-------|
| date | TEXT | PK1 |
| indicator | TEXT | PK2: "usdidr", "us10y", "bi_rate", "agg_foreign_net" |
| value | REAL | |

Populated by: `fetch-macro` (USD/IDR from Frankfurter API, US 10Y from Treasury.gov), `set-bi-rate` (manual)
Aggregate foreign flow computed from prices table during macro-signals

---

### news
News articles from Stockbit stream.

| Column | Type | Notes |
|--------|------|-------|
| id | INTEGER | PK, auto-increment |
| symbol_queried | TEXT | Which symbol the news was fetched for |
| title | TEXT | |
| source | TEXT | |
| url | TEXT | |
| published_at | TEXT | |
| body | TEXT | Article body (may be truncated) |

Populated by: `fetch-news`

---

### signals
Generated trading signals (currently unused, scoring done in-memory).

| Column | Type | Notes |
|--------|------|-------|
| id | INTEGER | PK |
| symbol | TEXT | |
| date | TEXT | |
| signal_type | TEXT | e.g. "foreign_flow", "macd_cross" |
| direction | TEXT | "bullish" or "bearish" |
| score | REAL | |
| description | TEXT | |

Populated by: signals module (currently 0 rows, scoring is done in-memory and returned as JSON)

---

### relative_strength
Relative performance vs IHSG and sector (currently unused).

| Column | Type | Notes |
|--------|------|-------|
| symbol | TEXT | PK1 |
| date | TEXT | PK2 |
| vs_ihsg_5d | REAL | 5-day return vs IHSG |
| vs_ihsg_10d | REAL | |
| vs_ihsg_20d | REAL | |
| vs_sector_5d | REAL | 5-day return vs own sector |
| vs_sector_10d | REAL | |
| vs_sector_20d | REAL | |

Populated by: not yet implemented (0 rows)

---

### insider
Insider/major holder filings.

| Column | Type | Notes |
|--------|------|-------|
| id | INTEGER | PK |
| symbol | TEXT | |
| date | TEXT | Filing date |
| insider_name | TEXT | |
| action | TEXT | "buy" or "sell" |
| shares | INTEGER | |
| price | REAL | |
| value | REAL | |

Populated by: `fetch-insider`

---

### trades
User's trade journal.

| Column | Type | Notes |
|--------|------|-------|
| id | INTEGER | PK |
| symbol | TEXT | |
| date | TEXT | |
| action | TEXT | "buy" or "sell" |
| lots | INTEGER | 1 lot = 100 shares |
| price | REAL | Per-share price |
| fees | REAL | |
| notes | TEXT | |

Populated by: `buy`, `sell` commands

---

### positions
Derived from trades. Current open positions.

| Column | Type | Notes |
|--------|------|-------|
| symbol | TEXT | PK |
| avg_cost | REAL | Weighted average cost per share |
| total_lots | INTEGER | |
| stop_loss | REAL | |
| tranches_planned | INTEGER | Default 4 |
| tranches_done | INTEGER | Number of buy tranches executed |
| notes | TEXT | |

Populated by: automatically recalculated on every buy/sell

---

### alerts_sent
Deduplication table for sent alerts.

| Column | Type | Notes |
|--------|------|-------|
| id | INTEGER | PK |
| symbol | TEXT | |
| alert_type | TEXT | |
| sent_at | TEXT | |
| message | TEXT | |

Populated by: alert system (currently 0 rows)

---

### capital
Single-row table tracking total trading capital and risk parameters.

| Column | Type | Notes |
|--------|------|-------|
| id | INTEGER | PK, CHECK (id = 1) — always one row |
| total | REAL | Total capital (IDR) |
| risk_per_trade | REAL | Default 0.02 (2%) |
| max_heat | REAL | Default 0.08 (8%) — max portfolio risk |
| updated_at | TEXT | Last update timestamp |

Populated by: manual update on deposit/withdrawal
Current: 500,000 IDR initial deposit

---

### capital_log
Audit trail for capital changes (deposits, withdrawals, adjustments).

| Column | Type | Notes |
|--------|------|-------|
| id | INTEGER | PK |
| date | TEXT | |
| action | TEXT | "deposit", "withdraw", "adjust" |
| amount | REAL | |
| balance_after | REAL | |
| notes | TEXT | |

Populated by: logged on every capital change

---

## Common Query Patterns

### Latest price + indicators + whale score for a symbol
```sql
SELECT p.close, p.volume, p.foreign_buy, p.foreign_sell,
       i.rsi, i.volume_ratio, i.ema20, i.ema50, i.macd_hist,
       w.composite_score as whale_score
FROM prices p
LEFT JOIN indicators i ON p.symbol = i.symbol AND p.date = i.date
LEFT JOIN whale_scores w ON p.symbol = w.symbol AND p.date = w.date
WHERE p.symbol = 'BBNI'
ORDER BY p.date DESC LIMIT 1
```

### Foreign flow trend over N days
```sql
SELECT date, foreign_buy - foreign_sell as net_foreign
FROM prices
WHERE symbol = 'BBNI'
ORDER BY date DESC LIMIT 10
```

### Smart broker accumulation (bandarmology)
```sql
SELECT bs.date, bs.broker_code, bs.net_value, bs.net_lot
FROM broker_summary bs
WHERE bs.symbol = 'BBNI'
  AND bs.broker_code IN ('YP','CC','ZP','AK','RX','KS','MS')
ORDER BY bs.date DESC, bs.net_value DESC
```

### Stocks with highest foreign inflow today
```sql
SELECT p.symbol, p.close, p.foreign_buy - p.foreign_sell as net_foreign,
       p.volume, c.sector_name
FROM prices p
JOIN companies c ON p.symbol = c.symbol
WHERE p.date = (SELECT MAX(date) FROM prices)
ORDER BY net_foreign DESC
LIMIT 20
```

### Screener: oversold + foreign buying + whale accumulation
```sql
SELECT p.symbol, p.close, i.rsi, i.volume_ratio,
       p.foreign_buy - p.foreign_sell as net_foreign,
       w.composite_score, c.sector_name
FROM prices p
JOIN indicators i ON p.symbol = i.symbol AND p.date = i.date
JOIN whale_scores w ON p.symbol = w.symbol AND p.date = w.date
JOIN companies c ON p.symbol = c.symbol
WHERE p.date = (SELECT MAX(date) FROM prices)
  AND i.rsi < 35
  AND (p.foreign_buy - p.foreign_sell) > 0
  AND w.composite_score > 0.5
ORDER BY w.composite_score DESC
```

### Sector rotation leaders
```sql
SELECT sector, pct_5d, pct_20d, momentum, rank_5d
FROM sector_rotation
WHERE date = (SELECT MAX(date) FROM sector_rotation)
ORDER BY momentum DESC
```

### Price near support with volume spike
```sql
SELECT p.symbol, p.close, sr.level as support, i.volume_ratio,
       ABS(p.close - sr.level) / p.close * 100 as distance_pct
FROM prices p
JOIN support_resistance sr ON p.symbol = sr.symbol
JOIN indicators i ON p.symbol = i.symbol AND p.date = i.date
WHERE p.date = (SELECT MAX(date) FROM prices)
  AND sr.level_type = 'support'
  AND ABS(p.close - sr.level) / p.close < 0.03
  AND i.volume_ratio > 1.5
ORDER BY i.volume_ratio DESC
```

### Broker concentration (bandar detection)
```sql
SELECT symbol, date, top5_net, total_value,
       top5_net / NULLIF(total_value, 0) as concentration
FROM bandar_detector
WHERE date = (SELECT MAX(date) FROM bandar_detector)
  AND top5_net > 0
ORDER BY concentration DESC
LIMIT 20
```
