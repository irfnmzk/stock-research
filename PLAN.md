# Stock Research Agent — Unified Build Plan

Standalone AI agent for IDX discretionary trading (US stocks TBD).
Combines a data-driven signal measurement system with an LLM agent that
narrates opportunities, watches the watchlist, and surfaces what changed.

**Core principle:** pipeline does the heavy work (watch, filter, compute),
agent narrates the story, trader judges, plans, and executes. No composite
scores, no auto-trading. Base rates calibrate confidence, not determine action.

**LLM role:** describe, don't interpret. The agent narrates factual state
changes ("price reclaimed EMA50, volume 2.5x, approaching resistance at 4,400")
but never calls patterns or gives directional opinions ("bull flag forming,
looks ready to break out"). Vision describes charts factually — the trader
brings discretionary judgment.

**Signal philosophy:** a signal is a state change, not a state. "Price above
EMA50" is true for 150 stocks and is useless. "Price just reclaimed EMA50
after being below it" is something happening today. If nothing changed, the
stock doesn't show up.

---

## Architecture

```
run_eod.py (cron, 16:30 WIB, no LLM)
├── fetch (prices, brokers, fundamentals, news — 300 stocks)
├── compute (ALL 300 stocks, not just watchlist)
│   ├── technical indicators (EMA, RSI, MACD, BB, ATR, volume ratio)
│   ├── bandarmology metrics (10 metrics — see Signal System below)
│   ├── support/resistance levels
│   ├── sector rotation
│   └── temporal derived fields (streaks, squeezes, reversals)
├── scanner funnel
│   ├── evaluate all ~17 signals on all 300 stocks
│   ├── count signals firing per stock
│   ├── filter by base rate reliability (>55% hit rate per signal per ticker)
│   ├── flag/discount illiquid stocks (liquidity percentile)
│   └── rank by reliable signal count → top 5-10 candidates
├── change detection (diff today vs yesterday — watchlist + scanner hits)
├── broker narrative (smart broker activity in plain text)
├── signal logging (record what fired today for forward return tracking)
├── assemble → latest_eod.json
└── charts (watchlist + top scanner hits)

agent (Telegram bot, Anthropic SDK — Claude Sonnet)
├── reads latest_eod.json + memory (theses, last session)
├── auto vision on watchlist charts during EOD brief
├── narrates: changes → signals → base rates → chart read → recommendation
└── tools for deep dives, portfolio actions, research
```

---

## Part 1: Signal Measurement System

The foundation. Every signal is measured independently with forward returns
so the agent (and trader) knows what's historically reliable and what's noise.

### 1A. Bandarmology Signals (4)

Independent signals computed from broker_summary and bandar_detector.
Each gets base rates in signal_history.

```
1. Smart Broker Net Buy
   Source: broker_summary (filtered by broker timing ranking)
   Ranked broker(s) are net buying today. Size measured relative
   to stock's daily turnover for significance.
   Concentration from bandar_detector qualifies the signal:
   high concentration = few large clients = stronger signal.

2. Accumulation Streak
   Source: broker_summary
   Consecutive days of smart broker net buying (thresholds: 3+, 5+).
   Cumulative lots/value tracked.
   → Answers: is this conviction or noise?

3. Broker Agreement
   Source: broker_summary
   3+ smart brokers on the same side on the same day.
   May indicate one large player splitting across channels,
   or multiple informed clients converging independently.
   → Answers: is this one broker or consensus?

4. Buyer/Seller Imbalance
   Source: bandar_detector
   Few buyers absorbing many sellers (or vice versa) + price direction.
   → Answers: is strong hand absorbing supply?
```

### 1B. Technical Signals (8)

State-change signals — each fires on the day the transition happens,
not while the condition persists. Default parameters (what everyone sees).

```
1. EMA Reclaim/Lose            — price crosses above/below EMA20 or EMA50
                                 (fires on cross day only, not while above/below)
2. EMA200 Touch                — price touches EMA200 (rare event, everyone watches)
3. EMA Cross                   — EMA20 crosses EMA50 (golden cross / death cross)
4. RSI Exit Oversold/Overbought — RSI crosses back above 30 / below 70
                                  (the exit, not the entry into the zone)
5. MACD Histogram Flip         — histogram changes sign (momentum direction changed)
6. Volume Spike                — volume ratio > 2x 20-day SMA (today's event)
7. BB Squeeze Release          — bandwidth was contracting, now expanding
                                 (not low bandwidth, but the transition)
8. S/R Proximity + Break       — price within 2% of strong S/R zone (proximity),
                                 or price breaks through S/R with volume 2x+ (break)
```

Compound context (not separate signals, but noted in narrative):
- Volume spike at a key S/R level — extra attention
- Smart broker activity with volume spike — they're active on a big day

### 1C. Signal Combinations (5+)

Common co-occurring patterns measured as their own signal type.
Each gets base rates in signal_history.

```
1. Silent Accumulation          — accumulation streak + price flat (<2% change)
2. Distribution Warning         — smart broker selling streak + near recent highs
3. Foreign/Domestic Alignment   — foreign flow + smart broker flow same direction
4. Accumulation + Volume Spike  — streak then volume expansion ("loaded then moved")
5. Coiled Spring                — silent accumulation + BB squeeze
```

NOT combined probabilities. Each combo is just another signal type with its
own track record. Agent references: "this coiled spring pattern has 63% hit
rate at 10d on BBNI (n=15)."

More combos can be added later based on what patterns emerge from the data.

### 1D. Support/Resistance Calculation

Deterministic S/R levels that approximate what most traders would draw.

```
Step 1 — Swing point detection
  A swing high: bar where high > highs of N bars on each side (N=5).
  A swing low: bar where low < lows of N bars on each side (N=5).
  N=5 captures weekly-scale swings on daily charts.
  Lookback: 6-12 months (captures levels visible on a standard daily chart).

Step 2 — Cluster nearby points
  Group swing points within 1.5-2% of each other into a single zone.
  IDX tick sizes vary by price level, but flat 2% works as starting point.
  Cluster center = average of all points in the cluster.

Step 3 — Rank by touches
  More swing points in a cluster = more obvious level = more traders watching.
  4+ touches = strong level. 1-2 touches = weak.

Step 4 — Output nearest zones
  Return 2-3 resistance zones above current price and 2-3 support zones below.
  Include: zone range, touch count, last touch date.

Additional S/R sources (noted but not clustered separately):
  - Round numbers (1000, 1500, 2000, etc.) — IDX stocks respect these
  - EMA50/200 as dynamic S/R — noted in narrative when price is near them
```

### 1E. Infrastructure & Qualifiers

Not signals — not counted in scanner funnel, no base rates.
These make the signals above more accurate, or provide context for narration.

```
Broker Timing Score (ranking system)
  Source: broker_summary + bandar_detector + prices
  Per (broker, ticker) pair. Measures whether flow through a broker
  channel predicts moves better than random entry on the same stock.

  Components:
  a) Excess return over baseline
     Broker net buy days avg forward return MINUS random days avg
     forward return on the same stock in the same period.
     Controls for stock trend — a broker buying during an uptrend
     looks "smart" by accident without this adjustment.

  b) Selectivity filter
     Only consider brokers buying on <40% of trading days for that stock.
     Filters out market makers and always-on passive flow.

  c) Size significance
     Broker net value as % of daily turnover.
     Filters out noise-level activity (500 lots on a stock trading 500K).

  d) Concentration qualifier
     Segment by bandar_detector top5 concentration:
     - High concentration + net buy = few large clients (stronger signal)
     - Low concentration + net buy = many small clients (weaker signal)
     Measure excess return separately for each segment.
     A broker is a proxy for its client base — concentration tells you
     whether the flow is driven by informed large players or retail noise.

  e) Sample size >= 15 for per-ticker ranking

  Note: brokers aren't actors — they're channels. "YP is smart on BBNI"
  really means "flow through YP on BBNI, when concentrated, has historically
  preceded moves." Could be one institutional client who covers banking well.
  On INCO that same broker may be noise because different clients trade there.

  → Feeds into per-ticker smart broker ranking (replaces static list)
  → Broker agreement (3+ brokers same side) may indicate one large player
    splitting across channels, which is an even stronger signal

  Fallback hierarchy:
  - Per-ticker ranking (if enough samples, n >= 20)
  - Per-sector ranking (aggregate across sector)
  - Global ranking (aggregate across all stocks)

  Ranking updates as new data accumulates. Agent says "YP has +2.4%
  excess return on BBNI (n=45)" not just "YP is a smart broker."

Concentration
  Qualifier on smart_broker_net_buy signal. High concentration makes
  the signal stronger. Already in bandar_detector.

Order Flow Profile
  Source: broker_summary (freq, value)
  Avg value per transaction (value / freq) per broker.
  Qualifier: institutional-size flow vs retail noise.

Liquidity Percentile
  Percentile rank of 20-day avg daily value across all 300 stocks.
  Hard floor: 500M IDR (exclude dead/suspended stocks).
  Context flag for agent: "low liquidity, signals less reliable."
  Also normalizes broker interpretation: 500 lots on BBCA is noise,
  500 lots on a small cap is the whole market.

Cross-Stock Broker Flow
  Detect when a broker sells stock A and buys stock B on the same day.
  Context for agent: "ZP sold BBNI and bought BBCA same day — rotation, not exit."

Corporate Action Adjustment
  Flag dates with stock splits, rights issues, dividends that distort
  price/volume data. Data quality layer: exclude flagged dates from
  base rate calculations. Agent warns: "volume spike may be ex-dividend artifact."

Market-Wide Broker Sentiment
  Aggregate smart broker net buy/sell across all stocks.
  Context for agent: "smart money net buyer market-wide today" —
  helps distinguish stock-specific signal from macro call.
```

### 1F. Signal Summary

```
SIGNALS (get base rates, counted in scanner funnel):
  Bandarmology:  4 signals
  Technical:     8 signals
  Combinations:  5+ signals
  Total:         ~17 signal types

INFRASTRUCTURE (no base rates, not counted):
  Broker timing score    — ranking system
  Concentration          — qualifier on broker signals
  Order flow profile     — qualifier (institutional vs retail)
  Liquidity percentile   — context flag
  Cross-stock flow       — context for narration
  Corporate actions      — data quality filter
  Market-wide sentiment  — context for narration
```

---

## Part 2: Scanner Funnel

### Architecture Change

**Old:** 5 binary screener rules → pass/fail → flat list of matches.
**New:** signal-count funnel. Compute all signals for all 300 stocks,
surface the ones with the most reliable signals firing.

### Funnel Steps

```
Step 1: Compute all signals for all 300 stocks (batch)
        Indicators, whale scores, bandarmology metrics, temporal fields.
        Currently whale.py only runs on watchlist → expand to full pool.
        Broker data already fetched for all 300 (run_eod.py line 114).
        Extra compute time: ~2-3 min, acceptable for 16:30 batch job.

Step 2: Evaluate all ~17 signal types per stock
        Each signal is a STATE CHANGE — fires on the day the transition
        happens, not while the condition persists. A stock where RSI has
        been below 30 for a week doesn't fire RSI oversold every day.
        The trigger is the transition back above 30.
        Store as list of (signal_type, signal_value) per stock.

Step 3: Count reliable signals per stock
        Pre-backfill: count all signals equally. No base rate filter.
        3+ signals to appear in output. That's the only gate.
        Post-backfill: "reliable" = signal has >55% hit rate on this
        ticker (from base rates). Fall back to market-wide rate if
        per-ticker n < 15. If market-wide rate also < 55%, don't count.

Step 4: Liquidity filter
        Flag stocks below 25th percentile liquidity.
        Don't exclude — agent mentions "low liquidity, signals less reliable."
        Hard floor: exclude stocks below 500M IDR avg daily value.

Step 5: Rank by reliable signal count → top 3-5
        Aggressive filtering. Some days zero candidates. That's fine.
        More reliable signals firing = more interesting.
        Ties broken by avg base rate quality (post-backfill).
        The goal: a short list you can review in 5-15 minutes,
        not a spreadsheet of 20 stocks.

Step 6: Output to latest_eod.json
        Top 3-5 scanner candidates with:
        - All active signals + their base rates
        - Broker narrative
        - Liquidity percentile
        - Sector context
        - S/R zones (nearest 2-3 above and below)
```

### Watchlist vs Scanner

```
WATCHLIST (5-10 stocks):
  Full treatment every day — all signals, broker narrative,
  chart vision, change detection, thesis update.
  Stocks you're actively tracking or holding.

SCANNER (300 → top 3-5):
  Discovery mode — "here's what did something today outside your watchlist."
  If something appears repeatedly, agent suggests adding to watchlist.
  "MDKA has appeared in scanner 3 days running — consider watchlist."

  Scanner candidates that are already in watchlist are merged into
  the watchlist section, not duplicated.
```

### Scanner Output Format

```json
{
  "scanner": [
    {
      "symbol": "MDKA",
      "reliable_signal_count": 4,
      "signals": [
        {
          "type": "accumulation_streak",
          "value": 5,
          "base_rate": {"hit_rate_10d": 0.68, "avg_return_10d": 4.1, "n": 28, "scope": "MDKA"}
        },
        {
          "type": "volume_spike",
          "value": 2.3,
          "base_rate": {"hit_rate_10d": 0.57, "avg_return_10d": 1.8, "n": 42, "scope": "MDKA"}
        },
        {
          "type": "bb_squeeze_release",
          "value": 12,
          "base_rate": {"hit_rate_10d": 0.63, "avg_return_10d": 3.2, "n": 15, "scope": "market_wide"}
        },
        {
          "type": "foreign_flow_reversal",
          "value": true,
          "base_rate": {"hit_rate_10d": 0.61, "avg_return_10d": 2.5, "n": 31, "scope": "MDKA"}
        }
      ],
      "sr_zones": {
        "resistance": [
          {"range": [4400, 4450], "touches": 4, "last_touch": "2026-04-15"},
          {"range": [4800, 4800], "touches": 2, "last_touch": "2026-03-20"}
        ],
        "support": [
          {"range": [4050, 4100], "touches": 3, "last_touch": "2026-04-28"},
          {"range": [3800, 3800], "touches": 2, "last_touch": "2026-03-05"}
        ]
      },
      "liquidity_percentile": 72,
      "sector": "Basic Materials",
      "days_in_scanner": 3
    }
  ]
}
```

Agent narrates this into:

```
📡 Scanner — 30 Apr 2026
Regime: risk_off | IHSG 6,940 | Foreign -5.2T/5d

🔍 New opportunities:

$MDKA — 4 signals (3rd day in scanner)
• Accumulation streak 5d (68% at 10d on MDKA, n=28)
• Volume spike 2.3x (57% at 10d, n=42)
• BB squeeze release after 12d compression (63% at 10d, n=15 market-wide)
• Foreign flow reversed to positive
S/R: resistance 4,400-4,450 (4 touches), support 4,050-4,100 (3 touches)
Liquidity: 72nd pctl | Sector: Basic Materials

$ITMG — 3 signals
• Broker agreement — 3 smart brokers same side (64% at 10d, n=31)
• RSI exiting oversold at 32 (59% at 10d on ITMG, n=38)
• Price within 2% of support at 24,800 (3 touches)
Liquidity: 89th pctl | Sector: Energy
```

### What Happens to the Old Screener Rules?

The 5 screener rules (momentum_breakout, whale_accumulation, etc.) become
**named patterns** — predefined signal combinations that are tracked as
their own signal type in base rates. They're no longer the gateway.

```
Old flow:  300 stocks → 5 rules (gate) → hits → score them
New flow:  300 stocks → all signals (no gate) → count reliable ones → rank
```

The named patterns still exist for two purposes:
1. They're signal types in signal_history with their own base rates
2. Agent can reference them: "this matches the capitulation_reversal pattern,
   which has 61% hit rate at 10d on this name"

But a stock doesn't need to match a named pattern to appear in the scanner.
If it has 4 reliable individual signals firing, it shows up regardless.

---

## Part 3: Pipeline Additions (run_eod.py)

### 3A. Temporal Derived Fields

Computed before scanner runs. Stored in DB. Computed for ALL 300 stocks.

```
smart_broker_streak    — consecutive days any smart broker is net buyer
                         (uses per-ticker smart broker ranking when available)
bb_squeeze_days        — days BB width has been narrowing
foreign_flow_reversal  — True if 5d foreign net > 0 after 10d net < 0
accdist_slope          — slope of top5 accdist over 5/10/20 days
```

### 3B. Change Detection

Diff today vs yesterday for watchlist + top scanner hits:
- Volume spikes (today vs 20d avg)
- Whale score jumps (>0.2 change)
- New scanner hits (wasn't in top 5 yesterday)
- Foreign flow reversals (sign change on 5d net)
- Price crossing key levels (S/R, EMA20/50)
- Smart broker starts/stops accumulating
- Distribution warning triggers

Output: `"changes"` section in latest_eod.json.

### 3C. Broker Narrative

For watchlist + top scanner hits, plain-text broker activity summary:

```
"BBNI": "YP net buy 3 consecutive days (total +12.3B, timing accuracy
72% on BBNI). CC started buying today (+2.1B). ZP neutral. Foreign net:
+15B over 5d after 2 weeks of selling."
```

The story behind the composite score. This is what a discretionary trader
actually reads — not "whale score 0.78."

### 3D. Signal Logging

Every signal that fires today gets logged to signal_history with:
- date, symbol, signal_type, signal_value, regime
- Forward returns (5d/10d/20d) filled in automatically as days pass

This is the closed loop — system measures its own accuracy over time.

---

## Part 4: Agent Memory

Two tables plus a ticker history log. Lightweight, persistent across sessions.

### ticker_thesis

```sql
CREATE TABLE ticker_thesis (
    symbol      TEXT PRIMARY KEY,
    thesis      TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);
```

Agent overwrites after each analysis. Current read on the stock:
"BBNI: accumulation phase since Apr 25, tranche 1 at 3,840, watching
for volume confirm above 4,000. YP active 5 days, timing accuracy 72%."

### ticker_history

```sql
CREATE TABLE ticker_history (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    date        TEXT NOT NULL,
    symbol      TEXT NOT NULL,
    price       REAL NOT NULL,
    signals     TEXT NOT NULL,       -- JSON array of active signals
    sr_zones    TEXT,                -- JSON of S/R zones at the time
    ema_state   TEXT,                -- JSON: ema20, ema50, ema200 values
    source      TEXT NOT NULL,       -- 'scanner' or 'adhoc'
    note        TEXT                 -- optional context
);
CREATE INDEX idx_ticker_history_symbol ON ticker_history(symbol, date);
```

Append-only log. Every time a ticker surfaces in the scanner or gets
an ad-hoc request, the pipeline logs a snapshot. Enables:

- "You looked at GJTL 2 weeks ago at 4,200 with volume spike + RSI
   exiting oversold. Since then: up 7%, now at 4,500, approaching
   resistance at 4,550."
- "MDKA has appeared in scanner 5 times this month" — recurring activity
- Personal backtest: "every time you checked a stock with this setup,
  what actually happened next?"

The agent queries this automatically when analyzing a ticker — no need
to search chat history.

### session_summary

```sql
CREATE TABLE session_summary (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    summary     TEXT NOT NULL,
    created_at  TEXT NOT NULL
);
```

3-5 lines after each conversation. Last N summaries injected on startup.
Provides continuity without bloating context.

Trade journal = existing `trades` table. No separate action_log or lessons.

---

## Part 5: Context Injection (context.py)

~2-3K tokens injected into every agent turn. Order = priority:

```
1. Changes today (what happened — most important for discretionary trader)
2. Macro regime snapshot (regime, score, key indicators, market-wide broker sentiment)
3. Portfolio state (positions, P&L, stops, available tranches, portfolio heat)
4. Watchlist signals + broker narratives
5. Scanner candidates (new opportunities, with reliable signal count + base rates)
6. Active ticker theses (from DB)
7. Last session summary (from DB)
```

If over budget, trim scanner candidates and older session summaries first.

---

## Part 6: System Prompt (system_prompt.py)

- Identity: IDX research analyst for discretionary trader
- Philosophy: narrate don't score, base rates as context not rules,
  flag what changed, be direct about uncertainty and sample sizes
- Trading style: scaling-in, mid-caps under 10K, bandarmology edge,
  top-down macro → sector → stock
- Tool descriptions (6 tools)
- Dynamic context (from context.py)
- Rules:
  - Always mention sample size with base rates
  - Flag when n < 20 as low confidence
  - Never combine base rates into a single probability
  - Mention liquidity context for illiquid stocks
  - Flag corporate action dates that may distort signals
  - Distribution warnings are as important as entry signals

---

## Part 7: Tools (tools.py)

6 tools with Anthropic-compatible schemas:

### ticker_deep_dive(symbol)
Full profile:
- Latest price, indicators, S/R levels
- All active signals with individual base rates (per-ticker + market-wide)
- Smart broker ranking for this ticker (not just global list)
- Broker flow detail (who's buying/selling, streaks, timing accuracy)
- Liquidity percentile
- Active signal combinations with their base rates
- Sector context
- Active thesis if exists
- Distribution warnings if any

### chart(symbol, days=90)
Render candlestick chart → base64 image in tool_result.
Agent sees via vision, describes pattern, ties to flow data.
Strip image from history after analysis.

### research(query)
Exa API search for macro news, ticker-specific news, sector research.

### portfolio(action, ...)
- status → positions, P&L, stops, portfolio heat, exposure %
- buy → record buy (symbol, lots, price, stop, tranches)
  - Shows suggested lot size based on stop distance and risk budget
- sell → record sell
- set_stop → update stop loss

### query_db(sql)
Raw SQL escape hatch. Agent can explore any data freely.

### note(action, symbol, text)
- read → get thesis for symbol (or all)
- write → save/update thesis

---

## Part 8: Agent (agent.py)

Anthropic SDK conversation loop (Claude Sonnet).

### EOD brief flow (triggered after pipeline):
1. Read latest_eod.json
2. For each watchlist stock, send chart through vision
3. Generate narrative: changes → signals with base rates → chart reads
   → broker stories → recommendations
4. Flag distribution warnings on existing positions
5. Send to Telegram

### On-demand flow (user asks a question):
1. Context already injected
2. Agent uses tools as needed
3. Responds with narrative including base rates
4. Updates thesis if analysis was done

### Session management:
- In-memory dict (single user)
- Short sessions (10-15 turns), no sliding window
- Token budget check at 80K, start fresh if exceeded
- Auto-save session_summary on conversation end

---

## Part 9: Telegram Bot (bot.py)

python-telegram-bot library:
- /start — initialize session
- /eod — trigger EOD brief manually
- /portfolio — quick portfolio status
- Text messages → agent conversation
- Chart sending: bot.send_photo()
- Single user, no multi-tenant

---

## Part 10: Backfill & Base Rates

### signal_history table

```sql
CREATE TABLE signal_history (
    date         TEXT NOT NULL,
    symbol       TEXT NOT NULL,
    signal_type  TEXT NOT NULL,
    signal_value REAL,
    regime       TEXT,
    return_5d    REAL,
    return_10d   REAL,
    return_20d   REAL,
    win_5d       BOOLEAN,
    win_10d      BOOLEAN,
    win_20d      BOOLEAN,
    PRIMARY KEY (date, symbol, signal_type)
);
```

### Signal types to backfill (~17)

**Bandarmology (4):**
smart_broker_net_buy, accumulation_streak,
broker_agreement, buyer_seller_imbalance

**Technical (8):**
ema_reclaim_lose, ema200_touch, ema_cross, rsi_exit_oversold_overbought,
macd_histogram_flip, volume_spike, bb_squeeze_release, sr_proximity_break

**Combinations (5+):**
silent_accumulation, distribution_warning,
foreign_domestic_alignment, accumulation_volume_spike,
coiled_spring

### backfill_signals.py

One-time script:
1. For each trading day in 6 years
2. For each stock with data that day
3. Recompute indicators + bandarmology metrics
4. Evaluate every signal trigger (as state changes — need prior day state)
5. Look up actual forward returns from prices
6. Write to signal_history
7. Compute per-(broker, ticker) timing scores → smart broker ranking

Heavy compute but one-time. Estimate 15-30 min for full backfill.

### base_rates.py

Query module:

```python
def get_signal_base_rates(signal_type, symbol=None, regime=None,
                          min_samples=10):
    """Individual signal base rate."""
    # Returns: samples, avg_return_5/10/20d, win_rate_5/10/20d

def get_broker_timing(broker_code, symbol=None):
    """Per-broker timing accuracy on a ticker (or global)."""
    # Returns: hit_rate, avg_return, sample_size, rank

def get_combo_base_rates(signal_types: list, symbol=None):
    """Base rate for a specific signal combination."""
    # Looks up the combo as its own signal type

def get_active_signals(symbol, date=None):
    """All signals currently firing on a ticker with their base rates."""
    # Used by ticker_deep_dive tool
```

### Adding new signals later

1. Define trigger condition
2. Run backfill for that signal type only
3. Agent can now reference it
4. No changes to scanner, config, or scoring needed

---

## Part 11: Portfolio Risk Management

Enhance existing portfolio.py:

```
- Capital tracking (deposit/withdrawal)
- Risk per trade: fixed % of capital (default 2%)
- Position sizing: (entry - stop) × lots ≤ max risk
- Tranche sizing: total position / planned tranches
- Exposure %: deployed / total capital
- Portfolio heat: sum of open risk across all positions (cap 6-8%)
- Cash available: capital - deployed
```

When agent recommends an entry, it shows:
"Suggested: 5 lots at 3,920, stop 3,660. Risk: 130K (1.3% of capital).
Portfolio heat after: 4.2%. Tranche 2/4."

---

## Part 12: Polish

- Error handling (API failures, missing data, DB locks)
- Graceful degradation (if base rates empty, skip; if backfill incomplete, warn)
- WAL mode on SQLite (already enabled)
- Corporate action date flagging in base rate queries
- Liquidity percentile refresh on pool refresh
- Logging
- Chart sending via Telegram
- Session summary auto-save

---

## Build Order

```
Phase 1 — Signal Foundation (no agent needed)
  1. Fix broker_score scaling for mid-caps in whale.py
  2. Expand compute to all 300 stocks (whale scores, bandarmology metrics)
  3. Compute temporal derived fields (streaks, squeezes, reversals)
  4. Implement signal evaluation engine (all ~17 signal types, state changes per stock)

Phase 2 — Pipeline Enrichment
  5. Scanner funnel (count reliable signals → rank → top 3-5)
     Pre-backfill: count all signals equally, 3+ to appear
     Post-backfill: filter by >55% hit rate per signal per ticker
  6. Change detection in run_eod.py
  7. Broker narrative generation
  8. Signal logging (record daily firings for forward return tracking)

Phase 3 — Backfill & Base Rates
  9. backfill_signals.py — replay 6 years, compute all ~17 signal types
  10. Compute per-(broker, ticker) timing scores → smart broker ranking
  11. base_rates.py — query module for signal/broker/combo base rates
  12. Enable base rate filtering in scanner funnel (step 5 upgrade)
  13. Validate signal thresholds against base rates (sanity check)

Phase 4 — Agent
  14. memory.py — ticker_thesis + ticker_history + session_summary
  15. context.py — assemble injection context
  16. system_prompt.py — identity, philosophy, tools, dynamic context
  17. tools.py — 6 tools
  18. agent.py — conversation loop, EOD brief, chart vision
  19. bot.py — Telegram bot

Phase 5 — Portfolio & Polish
  20. Enhance portfolio.py with risk management (sizing, heat, exposure)
  21. Polish (error handling, graceful degradation, logging)
```

Phase 1-2 can run without base rates or agent — scanner works on raw signal
count. Phase 3 adds historical validation. Phase 4 wraps the LLM around it.
Phase 5 hardens everything.

Note: the scanner funnel is designed to work in two modes:
- **Pre-backfill:** all signals counted equally, no reliability filter.
  Still useful — more signals firing = more interesting.
- **Post-backfill:** only reliable signals counted (>55% hit rate).
  Scanner output becomes much higher quality.

---

## Open Decisions

- **Telegram library:** python-telegram-bot vs aiogram
- **Conversation persistence:** in-memory dict vs SQLite
- **broker_score scaling:** fixed divisor vs relative to avg daily value
- **Temporal signal storage:** new table vs extend indicators
- **Base rates min sample size:** 10? 15? 20?
- **Smart broker ranking update frequency:** daily vs weekly
- **Combo signal list:** start with 4, expand based on what patterns emerge
- **Liquidity tier multipliers:** calibrate from backtest or use fixed estimates
- **Scanner top N:** 3-5 candidates (aggressive filtering, some days zero)
- **Pre-backfill signal count threshold:** 3+ signals to appear

---

## Design Principles

- Full dataset for base rates, no train/test split (not enough history)
- Always show sample size — user judges confidence
- Regime shown as context label, not scoring modifier
- No composite scoring — system shows signal stats, agent narrates, user decides
- Per-ticker broker ranking where sample allows, fallback to sector then global
- Distribution warnings are as important as entry signals
- Measure signal combinations as their own type, not combined probabilities
- System measures itself via signal log forward returns (closed loop)
- Start simple, iterate based on results

---

## Unchanged Decisions

- Standalone agent, not integrated with Hermes
- Own Telegram bot + Anthropic SDK (Claude Sonnet)
- SQLite for everything
- latest_eod.json as fast-read cache
- Pipeline runs at 16:30 WIB weekdays
- Short sessions, no sliding window
- Chart → base64 in tool_result → strip from history after analysis

---

## Part 13: Daily Workflow

The system's purpose framed as a day in the life.

### Automated (zero effort)

EOD pipeline runs ~16:30 WIB after market close. Computes all signals,
runs the funnel, generates data. Report hits Telegram:

- Which stocks did something today (3-5 max from scanner)
- What happened — narrative per stock describing state changes
- Watchlist status — what changed or didn't on tracked stocks
- Broker flow summary — what smart money did today

Read it on commute or after dinner. 2 minutes.

### Evening routine (5-15 min, only when something shows up)

Open charts for anything interesting from the report. Check S/R, EMA
structure, overall context. Decide: add to watchlist, plan entry, or ignore.
The scanner did 2 hours of scanning work. You spend 5 minutes per stock
on the final discretionary call.

### Ad-hoc requests (anytime via Telegram)

**Stock-specific:**
- "What's happening with GJTL" — pull latest data, signals, narrate state
- "Show me INCO chart" — generate chart, describe factually
- "Check broker flow on TAPG last 5 days" — specific data pull
- "Any smart broker activity on ELSA today" — quick check

**Screening:**
- "Any stocks breaking out today" — run scanner on demand
- "What's showing volume spikes right now" — filter by specific signal
- "Stocks near EMA200" — custom filter query

**Research:**
- "What's the news on BBNI" — search and summarize
- "Compare GJTL vs TAPG broker flow this week" — side by side
- "How has INCO behaved around this S/R level historically" — backtest

**Portfolio:**
- "Update my watchlist, add MDKA remove ELSA"
- "Where are my stops" — current position tracking
- "What's my average entry on BBNI if I add at 3,700" — quick math

**Market context:**
- "How's foreign flow today" — macro check
- "USD/IDR trend this week" — quick macro read

The principle: anything you'd normally open Stockbit for and click around
for 10 minutes — just ask. The daily report is the baseline, ad-hoc is
for when you want to dig deeper.

### The split

Pipeline does the heavy work: watch 300 stocks, filter, compute signals.
Agent narrates: turns data into readable context with S/R levels, broker
flow, and signal descriptions. You judge, plan, and execute: open the chart,
apply discretionary read, decide if it's worth trading.

---

## Part 14: US Stock Support (TBD)

Planned but not yet designed. Same pipeline architecture, different data.

### Key differences from IDX
- No broker summary data → replaced by insider buying (SEC Form 4),
  short interest (FINRA), and unusual options activity
- Relative strength vs SPY as quality filter
- Sector ETF momentum for top-down context
- Rich earnings/catalyst calendar:
  - Pre-event: consensus estimates + recent news aggregation
  - Post-event: beat/miss, guidance direction, key takeaways
  - Proximity flag: "earnings in X days" attached to signals
- Platform: Peluang (CFDs, 650+ US stocks)
- Market hours: 20:30-03:00 WIB

### Same principles apply
- Deterministic pipeline, LLM narrates
- State-change signals, not static conditions
- Aggressive funnel filtering → short list
- Agent describes, trader interprets
- Ticker history logging for all analyzed stocks

### To be designed
- Data sources and APIs (free: SEC EDGAR, FINRA, Yahoo Finance)
- Options flow data availability (paid vs free)
- Signal stack finalization
- Earnings calendar integration
- Pipeline module structure (shared vs separate from IDX)
