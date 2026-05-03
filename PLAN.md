# Stock Research Agent — Build Plan

Standalone AI agent for IDX discretionary swing trading.
Data-driven signal measurement system + LLM agent that narrates
opportunities, watches the watchlist, and surfaces what changed.

**Core principle:** pipeline does the heavy work (watch, filter, compute),
agent narrates the story, trader judges, plans, and executes. No composite
scores, no auto-trading. Base rates calibrate confidence, not determine action.

**LLM role:** describe, don't interpret. The agent narrates factual state
changes ("price reclaimed EMA50, volume 2.5x, approaching resistance at 4,400")
but never calls patterns or gives directional opinions.

**Signal philosophy:** a signal is a state change, not a state. "Price above
EMA50" is true for 150 stocks and is useless. "Price just reclaimed EMA50
after being below it" is something happening today. If nothing changed, the
stock doesn't show up.

**Broker philosophy:** no static "smart broker" lists. Community thesis debunked
across 6 dimensions. What works: broker significance — aggregate net flow from
individually significant brokers (|net| > 0.5% of turnover). At 3%+ net
significance: bullish +2.18% avg 10d return (2x baseline), bearish +0.13%.
Spread scales with significance level.

**Market constraint:** IDX is long-only (no shorting). Bearish signals are
exit alerts only. Signals with positive avg return despite bearish label are
"fake bearish" — removed from engine.

---

## Settled Decisions

- **Approach:** foundation-first — signal engine, scanner, base rates, then agent
- **Compute scope:** all 300 stocks in EOD pipeline (acceptable runtime)
- **Broker timing:** significance-based, always visible in output
- **Regime:** keep current macro.py logic as-is, treat as context label
- **Backfill:** 6-year SQLite DB on VPS, same schema, SSH copy to local
- **Architecture:** standalone agent, own Telegram bot, Anthropic SDK (Claude Sonnet)
- **Storage:** SQLite for everything, latest_eod.json as fast-read cache
- **Pipeline schedule:** 16:30 WIB weekdays (cron, no LLM)
- **Agent sessions:** short (10-15 turns), no sliding window
- **Edge metric:** avg net return (expectancy), NOT hit rate/win rate
- **Scanner filter:** avg_return_10d >= 1.0% (not hit rate >= 55%)
- **Signal threshold:** 2+ unique signal types to appear in scanner (was 3, reduced for 7-signal engine)

---

## Current State (what's built and working)

### Data Layer
- Data fetching: prices, broker_summary, bandar_detector, fundamentals, news (Stockbit API)
- 300-stock pool via scan_pool table (ranked by market cap)
- Technical indicators: EMA20/50/200, RSI, MACD, BB, ATR, volume_ratio
- S/R detection: swing points, clustering, touch counts
- Sector rotation: 11 IDX sectors, 5d/10d/20d ranks
- Macro regime: USD/IDR, US 10Y, BI Rate, foreign flow, capitulation detection
- Portfolio: trades, positions, avg cost, stop loss, tranches, capital tracking
- Charts: 3-panel candlestick (candles+EMA, RSI+foreign, whale score)
- Backfill: prices and brokers (resumable, rate-limit aware)
- CLI: 30+ commands via main.py

### Signal Engine (signal_engine.py) — 7 signals

Bandarmology (2):
1. **broker_significance** — aggregate net flow from individually significant brokers
   (|net| > 0.5% turnover), fires at 3%+ net significance. Bullish or bearish.
2. **buyer_seller_imbalance** — few buyers absorbing many sellers (ratio < 0.33)
   + price up. Bullish only (absorption pattern).

Technical (4 entry + 1 exit):
3. **ema_cross** — EMA20 crosses above EMA50 (golden cross). Bullish only.
4. **macd_histogram_flip** — MACD histogram flips positive. Bullish only.
5. **volume_spike** — volume_ratio > 2.0 on green candle. Bullish only.
6. **bb_squeeze_release** — BB width expanding after 5+ days compression,
   price above BB midline. Bullish only.
7. **sr_break** — support break with volume_ratio >= 2.0. Bearish only (exit alert).

### Scanner (scanner.py)
- Groups signals by symbol, counts unique signal types
- Filters: avg_return_10d >= 1.0% per signal (per-ticker base rate, fallback market-wide)
- Hard floor: 500M IDR avg daily value
- Threshold: 2+ signal types to appear
- Output: top 5 candidates sorted by signal count

### Supporting Modules
- **temporal.py** — computes smart_broker_streak (significance-based) and bb_squeeze_days
- **broker_narrative.py** — plain-text broker flow stories using significant brokers
- **changes.py** — diff today vs yesterday (price crossings, signal changes, foreign flow, broker streaks)
- **base_rates.py** — compute + query signal/broker performance stats, fill forward returns
- **backfill_signals.py** — replay history through signal engine (75K signals from 6 years)
- **whale.py** — foreign flow + significant broker accumulation composite score

### EOD Pipeline (run_eod.py)
Full pipeline: fetch → compute → signals → base_rates → assemble → charts → write JSON

Output (latest_eod.json):
```
macro, changes, watchlist (with signals + broker narrative + S/R),
scanner (candidates with signals + broker narrative),
portfolio, stop_warnings, sector_leaders
```

---

## Unresolved Quirks

### Legacy column name: `smart_broker_streak`
The indicators table column is still named `smart_broker_streak`. The computation
in temporal.py already uses significance-based filtering. Renaming requires SQLite
table recreation + full re-backfill. Functional impact: zero. Cosmetic only.

Referenced in: db.py, temporal.py, backfill_signals.py, run_eod.py, changes.py.

### `_compute_excess_returns()` is a no-op
In base_rates.py:247 — computes baselines dict but never writes excess returns
back to broker_rankings. The function body ends after computing the baselines.
Impact: broker_rankings has raw avg_return but no excess-over-baseline column.

### `median_return_*` columns always 0
base_rates.py writes `0, 0, 0` for median_return_5d/10d/20d in all INSERT
statements. The schema has the columns but they're never computed. SQLite doesn't
have a native MEDIAN function — would need a custom approach.

### `relative_strength` table unpopulated
Schema exists in db.py (vs_ihsg, vs_sector columns) but no code writes to it.
Was planned but never implemented.

### Old modules still imported
- `signals.py` and `screener.py` still exist (old composite-score approach)
- `main.py:66` imports `from screener import run_screener`
- `reports.py:91-92` imports from both `screener` and `signals`
- These are CLI commands that still work but use the old logic
- Not called by run_eod.py (new pipeline bypasses them)

### `whale.py` composite score questionable
Whale score = 0.6 * foreign_flow + 0.4 * broker_accumulation. Our analysis showed
foreign flow is U-shaped (extreme = volatility proxy, not directional) and broker
accumulation streaks have no edge. The whale_scores table is still written but
may not add value. Currently used in charts (3rd panel).

### `run_eod.py` exposes `smart_broker_streak` in JSON output
Line 265: `"smart_broker_streak": price_row["smart_broker_streak"]` — the field
name in latest_eod.json is the legacy column name. Agent/consumer code would
reference this key.

### Broker rankings only computed for bullish
base_rates.py `compute_broker_rankings()` filters `trend = 'bullish'` only.
No ranking for bearish broker activity (sr_break is the only bearish signal
and it doesn't use broker_code). This is correct for now but worth noting.

---

### Agent Layer (Phase 4) — COMPLETE

Six modules built and wired:

- **memory.py** — ticker_thesis, session_summary, conversation_turns (SQLite). CRUD for theses, session management, turn storage.
- **context.py** — `build_context(cfg)` assembles ~500 token plain text from latest_eod.json + memory tables. 7 sections: macro, changes, portfolio, watchlist, scanner, theses, summaries.
- **system_prompt.py** — identity block (describe don't interpret, show base rates, flag risks), EOD brief instructions, chat instructions. Two builders: `build_eod_prompt()`, `build_chat_prompt()`.
- **tools.py** — 6 tools: ticker_deep_dive, chart, portfolio, query_db, note, recall. Anthropic SDK schemas + handler dispatch.
- **agent.py** — `generate_eod_brief(cfg)` (single Claude call, no tools), `generate_eod_brief_with_charts(cfg)` (with vision), `run_conversation(cfg, msg, session_id)` (multi-turn with tool dispatch, max 15 turns), `close_session()` (summary via Haiku).
- **bot.py** — python-telegram-bot v21+ async. Commands: /start, /brief, /portfolio, /note, /recall. Free-text → agent conversation. Session timeout (30 min). `trigger_eod_brief()` for pipeline delivery.

Integration:
- `run_eod.py --notify` sends EOD brief via Telegram after pipeline completes
- `main.py agent-chat` — interactive terminal chat for testing
- `main.py send-brief [--notify]` — generate brief, optionally send via Telegram
- `main.py bot` — start Telegram bot

Requires: `ANTHROPIC_API_KEY` and `TELEGRAM_BOT_TOKEN` env vars. `authorized_user_id` in config.yaml.

---

## What's Next

### Phase 5: Portfolio & Polish

- Position sizing: (entry - stop) x lots <= max risk (2% of capital)
- Tranche sizing: total position / planned tranches
- Portfolio heat: sum of open risk across all positions (cap 6-8%)
- Error handling (API failures, missing data, DB locks)
- Graceful degradation (if base rates empty, skip; if backfill incomplete, warn)
- Remove or repurpose whale.py (charts panel)
- Clean up old signals.py / screener.py imports
- Compute median returns (custom SQLite function or post-query)
- Implement relative_strength table

---

## Design Principles

- A signal is a state change, not a state
- Edge = avg net return (expectancy), not win rate
- No static broker lists — significance-based filtering everywhere
- IDX long-only: bullish signals for entry, bearish for exit only
- Full dataset for base rates, no train/test split (not enough history)
- Always show sample size — user judges confidence
- Regime shown as context label, not scoring modifier
- No composite scoring — system shows individual signals, agent narrates, user decides
- Per-ticker base rate where sample >= 15, fallback to market-wide
- System measures itself via signal log forward returns (closed loop)
- Start simple, iterate based on results

---

## Data Sources

**Stockbit API** (exodus.stockbit.com):
- Daily OHLCV + foreign flow (buy/sell volumes)
- Broker summary + bandar detector (top 1/3/5/10 accumulation)
- Insider/major holder filings
- Sectors, subsectors, companies with market cap
- Fundamentals (PE, PB, PS, PCF, EV/EBITDA, PEG, dividend yield)
- News stream

**External APIs:**
- Frankfurter API — USD/IDR historical rates
- Treasury.gov — US 10Y yield CSV

**Stock pool:** top 300 IDX stocks by market cap (scan_pool table, refreshed via fetch_companies + refresh_pool)

**Backfill:** 6-year SQLite DB on VPS, same schema as this project. SSH copy to local for development.
