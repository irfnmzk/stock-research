# Analysis Log Format

The analysis log at `data/analysis_log.md` is a rolling journal of daily analysis, user decisions, and evolving thesis. It bridges qualitative reasoning between sessions.

Numbers are recomputed fresh by the pipeline each run, but narrative context (why a stock was added/dropped, multi-day flow patterns, user's current thinking) would otherwise be lost.

## Format

One section per trading day, ~5-15 lines covering:

- Macro regime + key shifts
- Watchlist changes with rationale
- Position updates + user decisions
- Active thesis (sector narratives, entry targets)
- Triggers to watch next session
- User's own thoughts and preferences on current positions

## Maintenance

- **Morning**: append brief notes (overnight events, what to watch)
- **EOD**: append day's observations (what moved, why, what changed)
- **Weekly**: summarize the week, prune stale entries older than 2 weeks

## Example Entry

```markdown
## 2026-04-28

**Macro**: Cautious. USD/IDR 16,450 (stable). US 10Y 4.38% (down from 4.42%). BI rate 5.75%.

**Watchlist changes**: Dropped ADRO (no foreign inflow, restructuring noise). Added ELSA (oil services, foreign accumulation 3 days).

**Positions**: BBNI tranche 1/4 at 3,840. Stop 3,660. Holding.

**Thesis**: Banking sector under pressure from foreign outflow but BBNI holding relative strength. Watching for tranche 2 entry near 3,700 if macro turns risk-on.

**Watch tomorrow**: INCO earnings release, nickel price reaction.
```

## Notes

- The log is user-specific runtime data, not tracked in git
- Created automatically on first analysis run
- Agents should read this file at the start of every workflow for context continuity
