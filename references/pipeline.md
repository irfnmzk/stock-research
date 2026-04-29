# Pipeline Reference

End-to-end data flow from raw API data to analyst reports.

## Overview

```
Stockbit API ──> fetch commands ──> SQLite ──> compute commands ──> SQLite ──> pipeline commands ──> JSON ──> LLM interprets
```

Two layers:
- **Python layer**: fetches, stores, computes, outputs structured JSON
- **LLM layer**: reads JSON + chart images, generates narrative reports

## Data Flow

### Stage 1: Fetch (raw data in)

| Command | Source | Writes to | Frequency |
|---------|--------|-----------|-----------|
| `fetch-companies` | Stockbit companies API | companies | Weekly or on first setup |
| `refresh-pool` | companies table | scan_pool | Weekly (ranks top 300 by market cap) |
| `fetch-pool --days N` | Stockbit chartbit API | prices, broker_summary, bandar_detector, fundamentals, news | Daily (all 300 pool stocks) |
| `fetch --symbols X Y` | Stockbit chartbit API | prices | On demand |
| `fetch-brokers --symbols X Y` | Stockbit broker API | broker_summary, bandar_detector | On demand |
| `fetch-fundamentals --symbols X Y` | Stockbit fundamentals API | fundamentals | On demand |
| `fetch-insider --symbols X Y` | Stockbit insider API | insider | On demand |
| `fetch-news --symbols X Y` | Stockbit news stream | news | On demand |
| `fetch-macro --days N` | Frankfurter API, Treasury.gov | macro | Daily |
| `set-bi-rate RATE` | Manual input | macro | When BI announces rate changes |
| `fetch-all --days N` | All of the above | All tables | Daily (combines fetch-pool + fetch-macro) |

### Stage 2: Compute (derived data)

| Command | Reads from | Writes to | What it computes |
|---------|-----------|-----------|------------------|
| `indicators --symbols X Y` | prices, broker_summary | indicators, whale_scores, sector_rotation | EMA, RSI, MACD, BB, ATR, volume ratio, whale scores, sector momentum |
| `sr --symbols X Y` | prices | support_resistance | Support/resistance levels via peak/trough detection |
| `screen --pool` | prices, indicators, whale_scores, support_resistance, companies | stdout (JSON) | Applies screener rules, returns matching stocks |
| `macro-signals` | macro, prices | stdout (JSON) | Macro regime assessment + aggregate foreign flow |

Note: `indicators` is the main compute step. It processes prices into all technical indicators, whale scores, and sector rotation in one pass.

### Stage 3: Pipeline (structured output for LLM)

| Command | What it gathers | Output |
|---------|----------------|--------|
| `pipeline-morning` | macro regime, sector leaders, watchlist snapshot (price + indicators + S/R + whale), portfolio stop warnings, recent news | JSON dict |
| `pipeline-eod --days N` | Everything in morning + screener hits (full pool), signal scores, portfolio P&L, tranche suggestions | JSON dict |
| `chart SYMBOL --days N` | prices, indicators | PNG image saved to data/charts/ |

### Stage 4: LLM Interpretation

The agent reads pipeline JSON output and chart images, then generates:
- **Morning brief** (07:00): macro regime, key levels to watch, portfolio alerts, commute-friendly format
- **EOD report** (16:00): full analysis with screener discoveries, signal interpretation, divergence spotting, actionable bottom line
- **Weekly rotation** (Saturday): pool refresh, sector rotation shifts, watchlist adds/drops with rationale

## Computed Data Details

### Technical Indicators (indicators.py)

From raw OHLCV prices, computes:
- **EMA 20/50/200**: trend direction and strength. Price above EMA20 > EMA50 = uptrend.
- **RSI (14)**: momentum oscillator. <30 oversold, >70 overbought.
- **MACD (12,26,9)**: trend momentum. Histogram crossing zero = momentum shift.
- **Bollinger Bands (20,2)**: volatility. Width contracting = squeeze, potential breakout.
- **ATR (14)**: volatility in price terms. Used for stop loss sizing.
- **Volume Ratio**: today's volume / 20-day average. >1.5 = unusual activity.

### Whale Scores (whale.py)

Combines two signals into a composite score:
- **Foreign flow score**: normalized net foreign buy/sell over recent days. Weighted 1.5x in final scoring.
- **Broker score**: activity of smart brokers (YP, CC, ZP, AK, RX, KS, MS) relative to total volume.
- **Composite**: weighted combination. Higher = more institutional accumulation.

### Bandar Detector (fetcher.py)

Measures broker concentration:
- **top1/3/5/10_net**: net value traded by top N brokers. High concentration = one player dominating.
- **top1/3/5/10_accdist**: accumulation/distribution pattern of top brokers.
- **total_buyers vs total_sellers**: market breadth at broker level.

### Support/Resistance (support_resistance.py)

Peak/trough detection on price history:
- **level**: price point where reversals occurred
- **touch_count**: more touches = stronger level
- **strength_score**: combines touch count, recency, and volume at level

### Sector Rotation (sector.py)

Ranks IDX sectors by momentum:
- **pct_5d/10d/20d**: sector return over period
- **momentum**: composite score combining all timeframes
- **rank**: relative position among all sectors

### Signal Scoring (signals.py)

Combines all signals into a single actionable score per stock:
- Foreign flow (1.5x weight)
- Technical (RSI, MACD, EMA alignment)
- Whale/bandar activity
- Sector momentum
- Proximity to support

Score > threshold (configurable in config.yaml) = actionable signal.

### Macro Regime (macro.py)

Assesses overall market environment:
- **USD/IDR**: weakening rupiah = risk-off for IDX
- **US 10Y yield**: rising yields = capital outflow from emerging markets
- **BI Rate**: monetary policy stance
- **Aggregate foreign flow**: net foreign activity across all stocks
- **Regime output**: "risk_on", "neutral", or "risk_off"

## Typical Daily Workflow

```
# Morning (before market open)
fetch-macro                          # get latest macro data
macro-signals                        # assess regime
pipeline-morning                     # gather morning brief data
# -> LLM generates morning brief

# After market close
fetch-all                            # fetch all daily data
indicators                           # compute all indicators
sr --symbols <watchlist>             # update S/R levels
pipeline-eod                         # gather EOD report data
chart <symbol> --days 60             # render charts for watchlist
# -> LLM generates EOD report with charts

# Weekly (Saturday)
refresh-pool                         # re-rank top 300 by market cap
fetch-companies                      # refresh company data
# -> LLM reviews sector rotation, suggests watchlist changes
```
