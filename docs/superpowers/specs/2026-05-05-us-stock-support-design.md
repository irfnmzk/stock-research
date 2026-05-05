# US Stock Support — Design Spec

## Overview

Add US stock scanning and analysis to the existing IDX stock research agent. The US system is fully decoupled from IDX — separate DB, separate modules, separate signal logic. The agent auto-detects market from ticker context and routes accordingly.

## Data Sources

| Source | Data | Frequency |
|--------|------|-----------|
| Pluang API | OHLC (no volume) | Daily |
| yfinance | sector, industry, quoteType | One-time seed, quarterly refresh |

Pluang universe: 625 active assets (stocks + ETFs). CFD-based, no volume data available.

## Architecture

### New Files

| File | Purpose |
|------|---------|
| `pluang.py` | API client — auth, OHLC endpoint, rate limiting |
| `fetcher_us.py` | Fetch orchestration — prices for all assets, yfinance sector seed |
| `indicators_us.py` | EMA10/21/50/200, RSI, MACD, BB, ATR, ADR% |
| `signal_engine_us.py` | Technical + RS signals |
| `scanner_us.py` | RS-first funnel → top 10-15 tickers |
| `support_resistance_us.py` | S/R level detection from OHLC |

### Modified Files

| File | Change |
|------|--------|
| `db.py` | Add `get_us_db()` + `init_us_db()` |
| `tools.py` | Add US tools (scan_us, analyze_us), ticker auto-detection |
| `system_prompt.py` | Describe US capabilities |

### Database

Separate SQLite file: `data/us.db`

No shared tables with IDX. No cross-market joins needed.

## Schema

### assets

| Column | Type | Notes |
|--------|------|-------|
| pluang_id | INTEGER | PK, internal Pluang asset ID |
| ticker | TEXT | UNIQUE, e.g. "AAPL" |
| name | TEXT | Company name |
| quote_type | TEXT | "EQUITY" or "ETF" |
| sector | TEXT | From yfinance, e.g. "Technology" |
| industry | TEXT | From yfinance, e.g. "Semiconductors" |
| sector_etf | TEXT | Mapped benchmark ETF ticker |
| active | INTEGER | 1 = tradeable on Pluang |

### prices

| Column | Type | Notes |
|--------|------|-------|
| ticker | TEXT | PK1 |
| date | TEXT | PK2, ISO format |
| open | REAL | |
| high | REAL | |
| low | REAL | |
| close | REAL | |

### indicators

| Column | Type | Notes |
|--------|------|-------|
| ticker | TEXT | PK1 |
| date | TEXT | PK2 |
| ema10 | REAL | 10-day EMA |
| ema21 | REAL | 21-day EMA |
| ema50 | REAL | 50-day EMA |
| ema200 | REAL | 200-day EMA |
| rsi | REAL | 14-day RSI |
| macd | REAL | MACD line |
| macd_signal | REAL | Signal line |
| macd_hist | REAL | Histogram |
| bb_upper | REAL | Bollinger upper (20d, 2σ) |
| bb_lower | REAL | Bollinger lower |
| bb_width | REAL | (upper - lower) / close |
| atr | REAL | 14-day ATR |
| adr_pct | REAL | ATR / close * 100 |

### support_resistance

| Column | Type | Notes |
|--------|------|-------|
| ticker | TEXT | PK1 |
| level | REAL | PK2 |
| level_type | TEXT | PK3, "support" or "resistance" |
| touch_count | INTEGER | |
| last_touched | TEXT | Date |
| strength_score | REAL | |

### relative_strength

| Column | Type | Notes |
|--------|------|-------|
| ticker | TEXT | PK1 |
| date | TEXT | PK2 |
| rs_vs_spy_10d | REAL | 10-day return vs SPY |
| rs_vs_spy_20d | REAL | 20-day return vs SPY |
| rs_vs_sector_10d | REAL | 10-day return vs sector ETF |
| rs_vs_sector_20d | REAL | 20-day return vs sector ETF |

### sector_rotation

| Column | Type | Notes |
|--------|------|-------|
| sector_etf | TEXT | PK1, e.g. "XLF" |
| date | TEXT | PK2 |
| pct_5d | REAL | 5-day return % |
| pct_10d | REAL | 10-day return % |
| pct_20d | REAL | 20-day return % |
| momentum | REAL | Composite score |
| rank | INTEGER | 1 = best |

### signal_events

| Column | Type | Notes |
|--------|------|-------|
| id | INTEGER | PK, auto-increment |
| ticker | TEXT | |
| date | TEXT | |
| signal_type | TEXT | e.g. "ema_cross", "rs_breakout" |
| direction | TEXT | "bullish" or "bearish" |
| magnitude | REAL | Signal strength value |
| close | REAL | Price at signal |
| meta | TEXT | JSON extra data |

## Pluang API Client

### Endpoint

```
GET /api/v4/asset/global-stock/price/ohlcStatsByDateRangeWithAlias/{pluang_id}
    ?timeFrame=DAILY
    &startDate=2024-01-01T00:00:00.000Z
    &endDate=2025-05-05T00:00:00.000Z
```

### Headers

```
Authorization: Bearer {token}
x-platform: desktop-web
x-device-id: web-{uuid}
x-language-code: id
Referer: https://trade.pluang.com/
Origin: https://trade.pluang.com
```

### Response

```json
{"statusCode": 200, "data": [
  {"si": 10003, "o": 19.79, "h": 20.05, "l": 19.79, "c": 20.04, "st": "2013-12-31T00:00:00.000+00:00", "et": "..."}
]}
```

Field mapping: `o`=open, `h`=high, `l`=low, `c`=close, `st`=date.

### Rate Limiting

Pluang has no documented rate limit. Use conservative 0.1s delay between requests. 625 assets × 0.1s = ~63 seconds for a full daily fetch.

### Token Management

Bearer token from Pluang web session. Store in `data/.tokens.json` alongside Stockbit token. Token refresh mechanism TBD (may need periodic manual re-auth or reverse-engineer refresh flow).

## Sector ETF Mapping

yfinance sector → Pluang sector ETF:

| yfinance sector | ETF | Available |
|-----------------|-----|-----------|
| Technology | XLK | No → use QQQ |
| Financial Services | XLF | Yes |
| Energy | XLE | Yes |
| Consumer Cyclical | XLY | Yes |
| Consumer Defensive | XLP | Yes |
| Healthcare | XLV | Yes |
| Utilities | XLU | Yes |
| Basic Materials | XLB | Yes |
| Communication Services | XLC | No → use QQQ |
| Industrials | XLI | No → use SPY |
| Real Estate | XLRE | No → use SPY |

## Signal Engine

### Signals (7 total)

**Technical (4):**
1. **EMA cross** — EMA10 crosses above EMA21 (short-term golden cross)
2. **MACD histogram flip** — histogram goes negative → positive
3. **BB squeeze release** — bb_width expanding after contraction, price above midline
4. **S/R break** — price breaks above resistance level (bullish only)

**Relative Strength (3):**
5. **RS breakout vs SPY** — rs_vs_spy_10d crosses from negative to positive
6. **RS breakout vs sector** — rs_vs_sector_10d crosses from negative to positive
7. **Sector momentum shift** — stock's sector ETF enters top 3 rank

### Signal Evaluation

Each signal fires on state change (transition day), not while condition persists. Compare today vs yesterday.

All signals are bullish-only for the scanner. No bearish signals in v1 (discretionary trader decides exits).

## Scanner Funnel

### Input
All assets where `quote_type = 'EQUITY'` and sufficient price history (200+ days).

### Gate 1 — RS Filter (hard)
`rs_vs_spy_10d > 0` — stock outperforming SPY over last 10 days.

### Gate 2 — Signal Recency
At least 1 signal fired within last 5 trading days.

### Scoring
```
score = (signal_count × 1.0) + (rs_vs_spy_10d × 1.5) + (rs_vs_sector_10d × 1.0)
```

RS values are percentages (e.g. 4.2 means +4.2% outperformance). Signal count is integer.

### Tiers
- **Pick**: RS gate pass + 2 or more fresh signals
- **Notable**: RS gate pass + 1 signal

### Output
Top 10-15 tickers sorted by score. Picks first, then notables.

## Agent Integration

### Ticker Auto-Detection

The agent recognizes market from ticker:
- IDX tickers: 4-letter uppercase, exist in `stock-research.db` companies table
- US tickers: exist in `us.db` assets table
- Ambiguous: agent asks user

### Tools Added

- `scan_us()` — run scanner, return top picks with briefs
- `analyze_us(ticker)` — deep dive on a single US ticker

### Output Format (per ticker in scan)

```
NVDA — Uptrend (above all EMAs), golden cross fired 2d ago.
RS +4.2% vs SPY, +2.1% vs XLK. Near ATH, no obvious resistance. ADR 3.8%.
```

### Output Format (deep dive)

Structured analysis covering:
- Trend structure (EMA alignment, slope)
- Key levels (nearest S/R)
- Signal history (what fired recently)
- RS context (vs SPY, vs sector, sector rank)
- Risk factors (near resistance, extended RSI, high ADR)

## Data Pipeline

### One-time seed
1. Parse `references/pluiang-stock.json` → populate `assets` table with pluang_id, ticker, name
2. Fetch sector/industry from yfinance for all equities → update `assets`
3. Map sector → sector_etf → update `assets`

### Daily pipeline
1. Fetch OHLC for all 625 active assets from Pluang → `prices`
2. Compute indicators → `indicators`
3. Compute S/R levels → `support_resistance`
4. Compute relative strength (needs SPY + sector ETF prices) → `relative_strength`
5. Compute sector rotation → `sector_rotation`
6. Evaluate signals → `signal_events`
7. Run scanner → output to agent/telegram

### Backfill
Pluang supports historical data back to 2013. Initial backfill: 1 year (for EMA200 warmup). Fetch in 1-year chunks to avoid large responses.

## Known Limitations (v1)

- No volume data — can't filter by liquidity or confirm breakouts with volume
- No earnings calendar — scanner may surface stocks before binary events
- No market cap data — can't filter micro-caps
- Token auth is manual — may expire, needs monitoring
- Pluang is CFD prices — may have slight differences from actual exchange prices

## Future Enhancements (not in v1)

- Earnings calendar integration (yfinance)
- Market cap tier from yfinance
- Base/consolidation detection (BB width percentile)
- 52-week high proximity field
- Trade journaling for US positions
- EOD brief for US (like IDX scheduled brief)
