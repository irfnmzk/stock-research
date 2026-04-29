# Configuration Reference

All settings live in `config.yaml` at the skill root.

## watchlist

Symbols for daily monitoring, chart generation, and reports.

```yaml
watchlist:
  - BBNI
  - INCO
  - ITMG
```

Note: watchlist is only for LLM analysis/charts. All data fetching uses the scan pool (top 300 by market cap).

## pool

```yaml
pool:
  size: 300              # top N stocks by market cap for scan pool
  min_avg_volume: 0      # minimum avg daily volume filter (0 = no filter)
```

Refreshed weekly via `refresh-pool`. The pool is the universe for `fetch-pool` and `screen --pool`.

## indicators

Technical indicator parameters.

```yaml
indicators:
  ema_periods: [20, 50, 200]   # exponential moving average periods
  rsi_period: 14                # RSI lookback
  macd_fast: 12                 # MACD fast EMA
  macd_slow: 26                 # MACD slow EMA
  macd_signal: 9                # MACD signal line EMA
  bb_period: 20                 # Bollinger Band period
  bb_std: 2                     # Bollinger Band standard deviations
  atr_period: 14                # Average True Range period
  volume_avg_period: 20         # volume ratio denominator (20-day avg)
```

## support_resistance

```yaml
support_resistance:
  window: 5              # scipy argrelextrema window for peak/trough detection
  cluster_pct: 0.02      # merge levels within 2% of each other
  min_touches: 2         # minimum touch count to qualify as a level
```

## breakout

```yaml
breakout:
  volume_ratio_min: 1.5  # minimum volume ratio to confirm breakout
  confirm_days: 2        # consecutive closes beyond level required
```

## whale

Bandarmology / institutional tracking settings.

```yaml
whale:
  smart_brokers: [YP, CC, ZP, AK, RX, KS, MS]  # broker codes to track
  accumulation_days: 5       # rolling window for accumulation detection
  foreign_flow_windows: [5, 10, 20]  # days for foreign flow analysis
  alert_threshold: 0.7      # whale composite score threshold for alerts
```

Smart broker codes are specific to IDX. These are known institutional/whale brokers identified through bandarmology analysis.

## sectors

```yaml
sectors:
  indices:               # IDX sector indices to track for rotation
    - IDXFINANCE
    - IDXBASIC
    - IDXENERGY
    - IDXINFRA
    - IDXPROPERT
    - IDXTECHNO
    - IDXHEALTH
    - IDXINDUST
    - IDXTRANS
  windows: [5, 10, 20]  # return periods for momentum ranking
```

## screener

Composable filter rules. Each rule is a list of conditions evaluated against the latest data snapshot for each stock.

```yaml
screener:
  rules:
    momentum_breakout:
      - volume_ratio > 2
      - close > ema20
      - rsi < 70
    whale_accumulation:
      - composite_score > 0.6
      - close >= support * 0.98
    sector_inflow:
      - sector_rank_5d < sector_rank_20d
      - foreign_net_5d > 0
    value_play:
      - pe_ttm < 15
      - pbv < 1.5
      - dividend_yield > 3
    capitulation_reversal:
      - volume_ratio > 2.5
      - close > open
      - close <= support * 1.02
      - rsi < 35
```

### Condition syntax

Each condition is a string: `field op value`

- **Fields**: any column from prices, indicators, whale_scores, fundamentals, support_resistance, or sector_rotation
- **Operators**: `>`, `<`, `>=`, `<=`, `==`
- **Values**: numeric literal, or a reference field (e.g. `ema20`), or `field * multiplier` (e.g. `support * 0.98`)

Available fields for conditions:
- From prices: `close`, `open`, `volume`, `foreign_buy`, `foreign_sell`
- From indicators: `ema20`, `ema50`, `ema200`, `rsi`, `macd_hist`, `bb_upper`, `bb_lower`, `bb_width`, `atr`, `volume_ratio`
- From whale_scores: `composite_score`, `foreign_flow_score`, `broker_score`
- From fundamentals: `pe_ttm`, `pe_forward`, `pbv`, `ps_ttm`, `dividend_yield`, `ev_ebitda`, `peg`, `earnings_yield`
- From support_resistance: `support`, `resistance`
- From sector_rotation: `sector_rank_5d`, `sector_rank_20d`, `sector_pct_5d`, `sector_pct_20d`, `sector_momentum`
- Computed: `foreign_net_5d` (5-day sum of net foreign flow)

A stock matches a rule only if ALL conditions are true (AND logic). To add new rules, just add entries to the YAML.

## signals

Signal scoring weights and thresholds. See [scoring.md](scoring.md) for the full scoring algorithm.

```yaml
signals:
  weights:
    whale_score: 1.0       # bandar/whale accumulation
    foreign_flow: 1.5      # net foreign buy/sell (heaviest weight)
    near_support: 1.0      # price near support level
    rsi_oversold: 0.5      # RSI below thresholds
    volume_spike: 0.5      # unusual volume
    sector_momentum: 0.5   # sector rotation rank
  macro_modifier:
    risk_on: 0.5           # bonus when macro is favorable
    cautious: 0.0          # no modifier
    risk_off: -1.0         # penalty when macro is unfavorable
  score_threshold: 3.0     # minimum score for BUY signal
```

## relative_strength

```yaml
relative_strength:
  benchmark: IHSG          # benchmark index
  windows: [5, 10, 20]    # comparison periods
```

Note: relative strength computation is defined but not yet populated (0 rows in DB).

## charts

```yaml
charts:
  output_dir: data/charts
  style: charles           # mplfinance chart style
  figsize: [14, 10]        # figure size in inches
```

## db

```yaml
db:
  path: data/stock-research.db
```

## stockbit

```yaml
stockbit:
  base_url: https://exodus.stockbit.com
```

Auth token is read from `data/.env` (STOCKBIT_JWT), not from config.yaml.
