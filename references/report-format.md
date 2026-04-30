# EOD Report Format

## Template

```
📊 EOD {date} ({day}) — {REGIME}

💼 Portfolio
$TICKER price (pnl%) | stop X ✅/⚠️
Brief status. Action or hold.

🌐 Macro
USD/IDR X (trend) | US10Y X | BI X
Foreign flow summary. Key divergences.

--- per watchlist stock (with MEDIA: chart) ---

MEDIA:/path/to/chart/TICKER.png
$TICKER price (change%) | RSI X
EMA20 X | EMA50 X
Whale X | Bandar +/-XB
Foreign 5d: +/-XB
S: X | R: X
Signal: summary with score
⚠️ warnings if any

--- end per stock ---

🔍 Screener
rule_name: $TICKER1, $TICKER2, $TICKER3
(only rules with hits worth checking)

🎯 Top Signals
$TICKER: score X — one-line reasoning
(top 5 non-watchlist signals)

📡 Sector Rotation
1. Sector +/-X% (momentum +/-X)
(top 5 sectors)

📝 Summary
2-3 lines: what happened today, overall read.
Deep dive suggestion: which stock(s) deserve closer look and why.
```

## Rules

- Use $TICKER prefix for all stock symbols (e.g. $BBNI, $INCO)
- Charts sent per watchlist stock via MEDIA: tag (arrives as split image + text due to gateway limitation, accepted)
- Tone: active recommendations with reasoning, not directives ("recommend X if Y" not "BUY X")
- Reference user's thesis and entry plans from analysis_log.md
- Keep it scannable on mobile — short lines, clear sections
- If pipeline_status is "partial" or "error", mention what broke at the end
- Skip screener rules with zero hits
- Summary section always at the bottom
- Portfolio and macro go first, then per-stock charts, then screener/signals/summary at bottom
