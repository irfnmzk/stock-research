# EOD Report Format

## Template

```
📊 EOD {date} ({day}) — {REGIME}

💼 Portfolio
$TICKER price (pnl%) | stop X ✅/⚠️
Brief status. Action or hold.

🎯 Recommendations

$TICKER price ↑/↓/→ score X.X
Reasoning from analysis log + current data. Active recommendation (not directive).

(repeat for each watchlist stock with a signal or actionable setup)

📡 Signals
$TICKER — one-line signal summary
(all watchlist stocks, including "quiet, no signal")

🔍 Screener
rule_name: $TICKER1, $TICKER2, $TICKER3
(only rules with hits worth checking)

🌐 Macro
USD/IDR X (trend) | US10Y X | BI X
Foreign flow summary. Key divergences.

📝 Summary
2-3 lines: what happened today, overall read.
Deep dive suggestion: which stock(s) deserve closer look and why.
```

## Rules

- Use $TICKER prefix for all stock symbols (e.g. $BBNI, $INCO)
- No charts in daily summary — charts only on demand when user asks
- Tone: active recommendations with reasoning, not directives ("recommend X if Y" not "BUY X")
- Reference user's thesis and entry plans from analysis_log.md
- Keep it scannable on mobile — short lines, clear sections
- If pipeline_status is "partial" or "error", mention what broke at the end
- Skip screener rules with zero hits
- Summary section always at the bottom
