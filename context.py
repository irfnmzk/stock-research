"""Context assembly — build dynamic context for agent system prompt.

Reads latest_eod.json + memory tables, produces a compact plain-text
block (~2-3K tokens) injected into every agent call.
"""

import json
from pathlib import Path
from db import get_db
from memory import get_all_theses, get_recent_summaries
from signal_engine import display_name

SCRIPT_DIR = Path(__file__).resolve().parent
EOD_PATH = SCRIPT_DIR / "data" / "latest_eod.json"


def _fmt_idr(val):
    """Format IDR value compactly."""
    if val is None:
        return "n/a"
    av = abs(val)
    sign = "+" if val >= 0 else "-"
    if av >= 1e12:
        return f"{sign}{av/1e12:.1f}T"
    if av >= 1e9:
        return f"{sign}{av/1e9:.1f}B"
    if av >= 1e6:
        return f"{sign}{av/1e6:.0f}M"
    return f"{val:,.0f}"


def _load_eod():
    """Load latest_eod.json. Returns dict or empty dict."""
    if not EOD_PATH.exists():
        return {}
    with open(EOD_PATH) as f:
        return json.load(f)


def _section_macro(data):
    macro = data.get("macro", {})
    if not macro:
        return ""
    regime = macro.get("regime", "unknown").upper()
    score = macro.get("score", 0)
    ff = _fmt_idr(macro.get("foreign_flow_5d"))
    usdidr = macro.get("usdidr", {})
    us10y = macro.get("us10y", {})
    return (
        f"Macro: {regime} (score {score}). "
        f"USD/IDR {usdidr.get('value', 'n/a')} ({usdidr.get('trend', '')}). "
        f"US10Y {us10y.get('value', 'n/a')}. "
        f"Foreign flow 5d: {ff}."
    )


def _section_changes(data):
    changes = data.get("changes", [])
    if not changes:
        return ""
    lines = ["Changes today:"]
    for ch in changes[:12]:
        lines.append(f"  {ch['symbol']}: {ch['description']}")
    return "\n".join(lines)


def _section_portfolio(data):
    portfolio = data.get("portfolio", [])
    warnings = data.get("stop_warnings", [])
    if not portfolio:
        return ""
    lines = ["Portfolio:"]
    for p in portfolio:
        line = (
            f"  {p['symbol']}: {p['total_lots']} lots @ {p['avg_cost']:,.0f}, "
            f"now {p['current_price']:,.0f} ({p['pnl_pct']:+.1f}%)"
        )
        if p.get("stop_loss"):
            line += f", stop {p['stop_loss']:,.0f} ({p['stop_distance_pct']:.1f}% away)"
        lines.append(line)
    for w in warnings:
        if w.get("breached"):
            lines.append(f"  !! {w['symbol']} STOP BREACHED at {w['current_price']:,.0f}")
        elif w.get("distance_pct", 100) < 3:
            lines.append(f"  ! {w['symbol']} near stop ({w['distance_pct']:.1f}% away)")
    return "\n".join(lines)


def _section_watchlist(data):
    watchlist = data.get("watchlist", {})
    if not watchlist:
        return ""
    lines = ["Watchlist:"]
    for sym, entry in watchlist.items():
        price = entry.get("price", 0)
        chg = entry.get("change_pct", 0)
        r5 = entry.get("return_5d")
        r10 = entry.get("return_10d")
        r20 = entry.get("return_20d")
        rsi = entry.get("rsi")

        ret_str = ""
        if r5 is not None:
            ret_str = f"5d:{r5:+.1f}% 10d:{r10:+.1f}% 20d:{r20:+.1f}%"

        line = f"  {sym}: {price:,.0f} ({chg:+.1f}%)"
        if rsi:
            line += f" RSI {rsi:.0f}"
        if ret_str:
            line += f" [{ret_str}]"
        lines.append(line)

        for s in entry.get("signals", []):
            sig_line = f"    signal: {display_name(s['signal_type'], s['direction'])}"
            if s.get("avg_return_10d"):
                sig_line += f" — avg 10d return: {s['avg_return_10d']:+.2f}% (n={s.get('sample_size', '?')})"
            if s.get("description"):
                sig_line += f" — {s['description']}"
            lines.append(sig_line)

    return "\n".join(lines)


def _section_scanner(data):
    scanner = data.get("scanner", [])
    if not scanner:
        return "Scanner: no candidates today."
    lines = ["Scanner picks:"]
    for c in scanner:
        sym = c["symbol"]
        cnt = c["signal_count"]
        sector = c.get("sector", "")
        r5 = c.get("return_5d")
        r10 = c.get("return_10d")
        r20 = c.get("return_20d")
        ret_str = ""
        if r5 is not None:
            ret_str = f" [5d:{r5:+.1f}% 10d:{r10:+.1f}% 20d:{r20:+.1f}%]"

        lines.append(f"  {sym} ({sector}): {cnt} signals{ret_str}")
        for s in c.get("signals", []):
            sig_line = f"    {display_name(s['type'], s['direction'])}"
            if s.get("avg_return_10d"):
                sig_line += f" — avg 10d: {s['avg_return_10d']:+.2f}%"
            if s.get("description"):
                sig_line += f" — {s['description']}"
            lines.append(sig_line)
        if c.get("broker_narrative"):
            narr = c["broker_narrative"]
            if len(narr) > 200:
                narr = narr[:200] + "..."
            lines.append(f"    brokers: {narr}")
    return "\n".join(lines)


def _section_theses(db):
    theses = get_all_theses(db)
    if not theses:
        return ""
    lines = ["Ticker theses:"]
    for t in theses:
        lines.append(f"  {t['symbol']}: {t['thesis']}")
    return "\n".join(lines)


def _section_summaries(db):
    summaries = get_recent_summaries(db, n=3)
    if not summaries:
        return ""
    lines = ["Recent sessions:"]
    for s in summaries:
        date = s["created_at"][:10]
        lines.append(f"  [{date}] {s['summary']}")
    return "\n".join(lines)


def build_context(cfg):
    """Assemble full context string for agent system prompt."""
    data = _load_eod()
    db = get_db(cfg)

    sections = [
        _section_macro(data),
        _section_changes(data),
        _section_portfolio(data),
        _section_watchlist(data),
        _section_scanner(data),
        _section_theses(db),
        _section_summaries(db),
    ]

    db.close()
    return "\n\n".join(s for s in sections if s)


if __name__ == "__main__":
    import yaml
    cfg = yaml.safe_load(open(SCRIPT_DIR / "config.yaml"))
    ctx = build_context(cfg)
    print(ctx)
    print(f"\n--- {len(ctx)} chars, ~{len(ctx)//4} tokens ---")
