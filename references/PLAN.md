# IDX Research Assistant - Project Plan

## Goal

Automated research assistant for Indonesian stock market (IDX). Collects data, computes signals, tracks whale activity and sector rotation, delivers reports and alerts via Telegram. User reviews and makes trading decisions manually.

## Architecture: Hybrid

Two layers working together:

**Python service (runs independently via system crontab)**
- Fetches all data on schedule from Stockbit API
- Computes technical indicators, scores, and signals
- Stores daily data in SQLite (intraday queried on-demand, not stored)
- Renders chart images (candlestick + indicators)
- Sends urgent intraday alerts directly via Telegram bot API

**Nanobot LLM layer (triggered by nanobot cron, 2-3x daily)**
- Reads computed data and chart images from SQLite/filesystem
- Interprets price action patterns visually from chart images
- Connects signals with news and macro context
- Generates morning brief, EOD report, weekly summary
- Answers on-demand questions ("what's happening with BBRI?")

---

## Data Sources

### Stockbit API (exodus.stockbit.com)

Auth: refresh_token → `POST /login/refresh` → access_token (auto-rotates refresh_token).
User manually seeds refresh_token from browser localStorage. Token manager persists rotated tokens.

| # | Endpoint | Purpose | Storage |
|---|----------|---------|---------|
| 1 | `POST /login/refresh` | Auth, get access_token | Token file |
| 2 | `GET /chartbit/{symbol}/price/daily` | Daily OHLCV + foreign flow + market cap | `prices` table |
| 3 | `GET /chartbit/{symbol}/price/intraday` | Intraday OHLCV (per-minute) | On-demand only |
| 4 | `GET /marketdetectors/{symbol}` | Broker summary + bandar detector | `broker_summary` + `bandar_detector` |
| 5 | `GET /order-trade/running-trade/chart/{symbol}` | Running trade / order flow | On-demand only |
| 6 | `GET /order-trade/broker/activity-chart` | Broker reverse lookup (chart) | On-demand only |
| 7 | `GET /order-trade/broker/activity` | Broker reverse lookup (table) | On-demand only |
| 8 | `GET /insider/company/majorholder` | Insider / major holder filings | `insider` table |
| 9 | `GET /emitten/sectors` | Sector list | `companies` table |
| 10 | `GET /emitten/sectors/{id}/subsectors` | Subsector list | `companies` table |
| 11 | `GET /emitten/v3/sector/{id}/subsector/{id}/company` | Stock list with price/mcap | `companies` table |
| 12 | `GET /keystats/ratio/v1/{symbol}` | Fundamentals (PE, PBV, etc.) | `fundamentals` table |
| 13 | `GET /stream/v3/symbol/{symbol}?category=STREAM_CATEGORY_NEWS` | News stream | `news` table |

API quirks:
- Daily price `to` param = start date, `from` param = end date (swapped)
- Intraday uses unix timestamps for to/from
- Board enum inconsistent: `MARKET_BOARD_REGULER` vs `BOARD_TYPE_REGULAR` vs `MARKET_TYPE_REGULER`
- Broker summary `type` field: "Asing"/"Lokal" on marketdetectors, "BROKER_TYPE_LOCAL"/"BROKER_TYPE_FOREIGN" on activity

### Deferred data sources
- Global indices (S&P 500, Nasdaq, Nikkei) - source TBD
- Commodity prices (coal, CPO, nickel, gold) - source TBD
- USD/IDR exchange rate - source TBD

---

## Data Model (SQLite)

### prices (daily only, stocks + indices)
```
symbol, date, open, high, low, close, volume, value, frequency,
foreign_buy, foreign_sell, market_cap, shares_outstanding, freq_analyzer
PK: (symbol, date)
```
Stores stocks (BBRI, BBCA), sector indices (IDXFINANCE, IDXENERGY), and IHSG.

### broker_summary
```
symbol, date, broker_code, broker_type (Asing/Lokal),
buy_lot, buy_value, sell_lot, sell_value, net_lot, net_value,
avg_price, freq
PK: (symbol, date, broker_code)
```

### bandar_detector (daily snapshot)
```
symbol, date,
top1_net, top3_net, top5_net, top10_net,
top1_accdist, top3_accdist, top5_accdist, top10_accdist,
total_buyers, total_sellers, total_value
PK: (symbol, date)
```

### insider
```
id (auto), symbol, name, date, action_type,
previous_shares, current_shares, change_shares,
price, nationality, badge
UNIQUE: (symbol, name, date)
```

### companies (stock universe, refreshed periodically)
```
symbol, name, sector_id, sector_name, subsector_id, subsector_name,
market_cap, last_price, avg_volume, tradeable
PK: symbol
```

### fundamentals
```
symbol, date, pe_ttm, pe_forward, pbv, ps_ttm, pcf_ttm,
ev_ebitda, peg, earnings_yield, dividend_yield
PK: (symbol, date)
```

### news (from Stockbit stream)
```
stream_id (PK), symbol_queried, title, content, source, url,
published_at, topics (comma-separated), total_likes
```

### indicators (computed)
```
symbol, date, ema20, ema50, ema200, rsi,
macd, macd_signal, macd_hist,
bb_upper, bb_lower, bb_width, atr, volume_ratio
PK: (symbol, date)
```

### support_resistance (computed)
```
symbol, level, level_type, touch_count, last_touched, strength_score
PK: (symbol, level, level_type)
```

### whale_scores (computed)
```
symbol, date, foreign_flow_score, broker_score, composite_score
PK: (symbol, date)
```

### sector_rotation (computed from index prices)
```
sector, date, pct_5d, pct_10d, pct_20d,
rank_5d, rank_10d, rank_20d, momentum
PK: (sector, date)
```

### signals
```
id (auto), symbol, date, signal_type, direction, score, description
```

### alerts_sent (dedup)
```
id (auto), symbol, alert_type, sent_at, message
```

---

## What Gets Computed (Python side)

### Technical indicators (per stock)
- EMA 20, 50, 200
- RSI (14-period) + divergence detection
- MACD (12, 26, 9) + histogram
- Bollinger Bands (20, 2) + bandwidth (squeeze detection)
- ATR (14-period) for volatility
- Volume ratio: current volume / 20-day average volume

### Support & resistance
- Local minima/maxima detection via scipy argrelextrema (window=5)
- Cluster nearby levels within 1-2% tolerance
- Weight by touch count and recency
- Volume confirmation at each level

### Breakout detection
- Price close above resistance or below support
- Volume confirmation: volume ratio > 1.5x
- Consolidation tightness: Bollinger bandwidth or ATR contraction before breakout
- Flag as confirmed after 2 consecutive closes beyond level

### Bandarmology / whale tracking
- Broker summary scoring:
  - Track known institutional broker codes (YP, CC, ZP, AK, RX, KS, MS, etc.)
  - Net buy/sell per broker per stock per day
  - Rolling accumulation: consistent buying over 3-5 days
- Foreign flow (from daily price data):
  - Daily net foreign buy/sell per stock
  - Cumulative foreign flow over 5d, 10d, 20d windows
  - Divergence: foreign buying while price flat/down = accumulation
- Bandar detector:
  - Top1/3/5/10 broker accumulation/distribution scores
  - Total buyer vs seller count
- Whale score (composite):
  - Foreign flow direction and magnitude
  - Smart broker accumulation pattern
  - Consistency over multiple days

### Sector rotation
- Sector index % change over 5d, 10d, 20d windows
- Rank sectors by performance in each window
- Detect rank changes: sector moving up in short window vs long window = fresh inflow
- Momentum rate of change per sector

### Screener rules engine
- Composable filters, examples:
  - Volume ratio > 2 AND price above EMA 20 AND RSI < 70
  - Whale score > threshold AND price near support
  - Sector outperforming AND foreign net buy > X
- Rules stored in config.yaml, adjustable without code changes

### Chart rendering
- mplfinance candlestick chart per symbol
- 3-panel layout: candles + MAs + S/R lines (top), volume (middle), RSI (bottom)
- Annotate support/resistance levels on chart
- Save as PNG for LLM visual analysis

---

## Outputs

### Morning Brief (07:00 WIB)
Delivered by nanobot LLM via Telegram.
- Overnight global recap (deferred until global data source found)
- Macro events today: earnings releases, ex-dates
- Watchlist update: stocks with setups forming, ranked by signal strength
- Sector mood: which sectors look strong/weak based on rotation data
- Top whale activity from previous day: notable accumulation/distribution

### Intraday Alerts (09:00-15:00 WIB, every 30 min)
Sent directly by Python service via Telegram bot API. Only when triggered.
- Volume anomaly: volume ratio > 2.5x during market hours
- Breakout: price breaks key S/R level with volume confirmation
- Whale alert: large single-broker net buy/sell (> threshold)
- Foreign flow spike: unusual foreign activity
- Screener hit: stock passes screener rules for first time today

### EOD Report (16:00 WIB)
Delivered by nanobot LLM via Telegram.
- Market summary: IHSG performance, top gainers/losers
- What moved and why: connect price action to news/flow
- Screener results: all stocks that passed filters today
- Whale activity summary: notable accumulation/distribution patterns
- Sector rotation update: any shifts detected
- Chart analysis: visual read of key watchlist stocks
- Watchlist adjustments: new candidates in, stale ones out

### Weekly Summary (Sunday 19:00 WIB)
Delivered by nanobot LLM via Telegram.
- Week in review: sector rotation trends, money flow direction
- Top performers and laggards with context
- Whale positioning: who's been accumulating/distributing over the week
- 3-5 stock ideas with reasoning (chart + flow + catalyst)
- Watchlist refresh

---

## Schedule (system crontab, WIB)

| Time | Job | Runner |
|------|-----|--------|
| 06:00 | Fetch overnight global data (deferred) | Python |
| 06:30 | Fetch news stream for watchlist | Python |
| 07:00 | Generate and deliver morning brief | Nanobot LLM |
| 08:55 | Pre-market data fetch | Python |
| 09:00-15:00 | Every 30 min: fetch intraday (on-demand), run screener, check alerts | Python |
| 15:30 | Fetch EOD daily data, compute all indicators, render charts | Python |
| 16:00 | Generate and deliver EOD report | Nanobot LLM |
| Sunday 18:00 | Compute weekly aggregates | Python |
| Sunday 19:00 | Generate and deliver weekly summary | Nanobot LLM |

---

## Project Structure

```
stock-research/
├── config.yaml              # watchlist, screener rules, thresholds, schedule
├── pyproject.toml           # uv project, dependencies
├── main.py                  # CLI entry point (python main.py fetch/indicators/sr/screen/chart/alert-check)
├── db.py                    # SQLite schema, migrations, helpers
├── stockbit.py              # Stockbit API client + token manager (TO BUILD)
├── fetcher.py               # data fetching orchestration
├── indicators.py            # EMA, RSI, MACD, BB, ATR, volume ratio
├── support_resistance.py    # S/R detection, breakout logic
├── whale.py                 # broker summary scoring, whale score
├── sector.py                # sector rotation tracking
├── screener.py              # rules engine, composable filters
├── charts.py                # mplfinance rendering, 3-panel layout
├── news.py                  # Stockbit news stream fetcher (TO REWRITE)
├── alerts.py                # Telegram bot, alert dedup
├── reports.py               # morning brief / EOD / weekly data gatherers
└── data/
    ├── stock-research.db    # SQLite database
    └── charts/              # rendered chart PNGs
```

---

## Build Status

### Done
- [x] Project scaffolding with uv
- [x] config.yaml with watchlist, indicator params, screener rules, schedule
- [x] SQLite schema + DB helper (db.py) — 13 tables
- [x] CLI entry point (main.py) with all subcommands
- [x] Stockbit API client with token manager (stockbit.py)
- [x] Data fetcher using Stockbit API (fetcher.py) — prices, brokers, bandar, insider, companies, fundamentals
- [x] News fetcher from Stockbit stream (news.py)
- [x] Technical indicators module (indicators.py)
- [x] Support/resistance detection + breakout logic (support_resistance.py)
- [x] Screener rules engine with fundamental filters (screener.py)
- [x] Chart rendering with foreign flow + whale + S/R annotations (charts.py)
- [x] Telegram alerts with dedup (alerts.py)
- [x] Whale score computation using real broker data (whale.py)
- [x] Sector rotation from real index prices (sector.py)
- [x] Report data gatherers (reports.py)
- [x] All Stockbit API endpoints tested with real data
- [x] Full pipeline tested: fetch -> compute -> screen -> chart -> alert

### Done: Phase 3 - Delivery
- [x] Skill file created at skills/stock-research/SKILL.md
- [x] Cron: morning brief 07:00 WIB (Mon-Fri)
- [x] Cron: intraday alerts every 30 min 09:00-14:30 WIB (Mon-Fri)
- [x] Cron: EOD fetch 15:30 WIB (Mon-Fri)
- [x] Cron: EOD report 16:00 WIB (Mon-Fri)
- [x] Cron: weekly summary Sunday 19:00 WIB
- [x] DONE: persist STOCKBIT_TOKEN via data/.env (auto-loaded by stockbit.py)

### Phase 4 - Tuning
- [ ] Adjust screener rules based on real results
- [ ] Tune alert thresholds to reduce noise
- [ ] Refine whale scoring weights
- [ ] Add global indices / commodity data source

---

## Tech Stack

- Python 3.12, managed with uv
- pandas, pandas_ta (technical analysis)
- scipy (peak detection for S/R)
- mplfinance (chart rendering)
- httpx (HTTP for Stockbit API)
- sqlite3 (built-in)
- System crontab + nanobot cron (scheduling)
- Telegram bot API via httpx (alerts and reports)

---

## Open Items

- [ ] Telegram bot token for direct Python alerts
- [ ] Global indices data source (S&P 500, Nasdaq, Nikkei)
- [ ] Commodity prices data source (coal, CPO, nickel)
- [ ] USD/IDR exchange rate source
- [ ] User to seed initial STOCKBIT_REFRESH_TOKEN from browser
