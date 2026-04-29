---
name: stock-research
description: >
  IDX (Indonesian stock exchange) research assistant. Fetches market data from
  Stockbit API, computes technical indicators, whale/bandarmology tracking,
  sector rotation, screens for entry signals, tracks portfolio, and generates
  daily analyst reports. Designed for LLM agents to run pipelines and interpret
  results.
license: MIT
compatibility: >
  Requires Python 3.12+ and uv package manager. Linux/macOS/Windows.
  Network access to exodus.stockbit.com (Stockbit API) and api.exa.ai (research).
  Stockbit JWT token required (manual browser extraction).
---

# Stock Research

IDX research assistant pipeline. An LLM agent runs CLI commands, reads the
structured output, and generates human-readable reports with interpretation.

## Quick Start

### 1. Install uv (if not present)

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

### 2. Install dependencies

```bash
cd <skill-directory>
uv sync
```

### 3. Set up credentials

Create `data/.env` with:

```
STOCKBIT_JWT=<jwt-token-from-stockbit-browser-session>
EXA_API_KEY=<exa-api-key-for-research>
```

The Stockbit JWT must be manually extracted from browser localStorage after
logging into stockbit.com. It expires roughly every 30 days and needs manual
refresh. See [references/api-quirks.md](references/api-quirks.md) for details.

The Exa API key is optional but needed for the research module (global macro
news, ticker-specific news).

### 4. Initialize data

```bash
# Fetch sector/company universe and build scan pool
uv run python main.py fetch-companies
uv run python main.py refresh-pool

# Fetch price data for the full pool (first run fetches 180 days)
uv run python main.py fetch-pool
```

After this, the SQLite database at `data/stock-research.db` is ready.

### 5. Verify

```bash
uv run python main.py screen --pool
```

This runs the screener against the full pool. If it prints results, everything works.

## Commands

All commands are run via `uv run python main.py <command>`.

### Data Fetching

| Command | Description |
|---------|-------------|
| `fetch --symbols SYM1 SYM2 --days N` | Fetch daily prices (default: watchlist, 180 days) |
| `fetch-brokers --symbols SYM1 SYM2` | Fetch broker summary + bandar detector |
| `fetch-insider --symbols SYM1 SYM2` | Fetch insider/major holder filings |
| `fetch-companies` | Fetch full stock universe (sectors, subsectors) |
| `refresh-pool` | Rank stocks by market cap, build top-300 scan pool |
| `fetch-pool --days N` | Fetch all data for the scan pool |
| `fetch-fundamentals --symbols SYM1 SYM2` | Fetch PE, PBV, dividend yield, etc. |
| `fetch-news --symbols SYM1 SYM2` | Fetch news from Stockbit stream |
| `fetch-all --days N` | Run full daily fetch pipeline |
| `fetch-macro --days N` | Fetch macro indicators (USD/IDR, US 10Y) |

### Analysis

| Command | Description |
|---------|-------------|
| `indicators --symbols SYM1 SYM2` | Compute EMA, RSI, MACD, BB, ATR, volume ratio |
| `sr --symbols SYM1 SYM2` | Detect support/resistance levels |
| `screen --rule RULE --pool` | Run screener (optional: specific rule, full pool) |
| `chart SYMBOL --days N` | Render candlestick chart with indicators |
| `macro-signals` | Show macro signals dashboard |
| `set-bi-rate RATE --date DATE` | Manually set BI Rate |

### Pipelines (for agent workflows)

| Command | Description |
|---------|-------------|
| `pipeline-morning` | Full morning brief: macro + watchlist + portfolio data as JSON |
| `pipeline-eod --days N` | Full EOD: indicators, whale, S/R, sector, screener, charts, JSON |

Pipeline commands output structured JSON that the LLM agent reads and interprets
into narrative reports.

### Portfolio

| Command | Description |
|---------|-------------|
| `buy SYMBOL LOTS PRICE` | Record a buy trade (options: --fees, --date, --stop, --tranches) |
| `sell SYMBOL LOTS PRICE` | Record a sell trade |
| `portfolio` | Show current positions and P&L |
| `trades --symbol SYM` | Show trade history |
| `set-stop SYMBOL PRICE` | Set stop loss for a position |

## Configuration

`config.yaml` controls watchlist, screener rules, indicator parameters, whale
tracking settings, and signal scoring weights. See the file for all options.

Key sections:
- `watchlist` - symbols for daily monitoring and reports
- `pool.size` - scan pool size (default 300)
- `whale.smart_brokers` - broker codes to track for bandarmology
- `screener.rules` - composable filter rules
- `signals.weights` - signal scoring weights (foreign_flow weighted 1.5x)

## Architecture

Two-layer design:

**Python layer** (this skill): fetches data, computes indicators, stores in
SQLite, renders charts, outputs structured JSON.

**LLM layer** (the agent): reads JSON output and chart images, interprets
patterns, connects signals with news/macro context, generates narrative reports.

See [references/pipeline.md](references/pipeline.md) for full architecture, data flow,
and computed data details.

## Key References

- [references/schema.md](references/schema.md) - Database schema (19 tables), column definitions, common SQL query patterns
- [references/pipeline.md](references/pipeline.md) - End-to-end data flow, computed data details, daily workflow
- [references/config.md](references/config.md) - All config.yaml options, screener rule syntax, available fields
- [references/scoring.md](references/scoring.md) - Signal scoring algorithm, weights, thresholds, interpretation
- [references/api-quirks.md](references/api-quirks.md) - Stockbit API gotchas
- [references/analysis-log-format.md](references/analysis-log-format.md) - Analysis log format and maintenance

For complex analysis beyond CLI commands, read `references/schema.md` for direct SQL queries against the SQLite database.

## Runtime Data

These files are created at runtime and not tracked in git:

- `data/stock-research.db` - SQLite database (prices, indicators, signals, etc.)
- `data/market.db` - Macro data SQLite
- `data/.env` - API credentials
- `data/.tokens.json` - Stockbit token rotation state
- `data/charts/` - Rendered chart PNGs
- `data/analysis_log.md` - Rolling analysis journal (see references/analysis-log-format.md)
