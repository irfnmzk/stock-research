"""Agent tools — tool definitions and handlers for the Anthropic SDK."""

import json
from db import get_db
from memory import set_thesis, get_thesis, get_all_theses, get_recent_summaries


TOOL_DEFINITIONS = [
    {
        "name": "ticker_deep_dive",
        "description": "Get detailed analysis for a stock: all active signals with base rates, support/resistance levels, broker narrative, and recent returns.",
        "input_schema": {
            "type": "object",
            "properties": {
                "symbol": {"type": "string", "description": "Stock ticker symbol (e.g. BBNI, ITMG)"},
            },
            "required": ["symbol"],
        },
    },
    {
        "name": "chart",
        "description": "Generate a price chart for a stock. Returns the chart image.",
        "input_schema": {
            "type": "object",
            "properties": {
                "symbol": {"type": "string", "description": "Stock ticker symbol"},
                "days": {"type": "integer", "description": "Number of days to show (default 90)", "default": 90},
            },
            "required": ["symbol"],
        },
    },
    {
        "name": "portfolio",
        "description": "Get current portfolio positions, PnL, and stop loss warnings.",
        "input_schema": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "query_db",
        "description": "Run a read-only SQL query against the research database. Tables: prices, indicators, signal_events, signal_base_rates, broker_summary, companies, scan_pool, support_resistance, sector_rotation.",
        "input_schema": {
            "type": "object",
            "properties": {
                "sql": {"type": "string", "description": "SELECT query to execute"},
            },
            "required": ["sql"],
        },
    },
    {
        "name": "note",
        "description": "Save or update a trading thesis for a stock. One thesis per symbol, overwrites previous.",
        "input_schema": {
            "type": "object",
            "properties": {
                "symbol": {"type": "string", "description": "Stock ticker symbol"},
                "text": {"type": "string", "description": "The thesis text"},
            },
            "required": ["symbol", "text"],
        },
    },
    {
        "name": "recall",
        "description": "Retrieve saved ticker theses and recent session summaries for context.",
        "input_schema": {
            "type": "object",
            "properties": {
                "symbol": {"type": "string", "description": "Optional: get thesis for a specific symbol. Omit to get all theses."},
            },
        },
    },
]


def handle_tool(cfg, tool_name, tool_input):
    """Dispatch a tool call and return the result string."""
    handlers = {
        "ticker_deep_dive": _handle_deep_dive,
        "chart": _handle_chart,
        "portfolio": _handle_portfolio,
        "query_db": _handle_query_db,
        "note": _handle_note,
        "recall": _handle_recall,
    }
    handler = handlers.get(tool_name)
    if not handler:
        return f"Unknown tool: {tool_name}"
    return handler(cfg, tool_input)


def _handle_deep_dive(cfg, inp):
    from base_rates import get_active_signals_with_rates
    from broker_narrative import generate_narrative

    symbol = inp["symbol"].upper()
    db = get_db(cfg)

    # Signals with base rates
    signals = get_active_signals_with_rates(cfg, symbol)

    # Price + returns
    hist = db.execute(
        "SELECT close FROM prices WHERE symbol = ? ORDER BY date DESC LIMIT 21",
        (symbol,),
    ).fetchall()

    price_info = ""
    if hist:
        close = hist[0]["close"]
        parts = [f"Price: {close:,.0f}"]
        if len(hist) > 5:
            parts.append(f"5d: {(close - hist[5]['close']) / hist[5]['close'] * 100:+.1f}%")
        if len(hist) > 10:
            parts.append(f"10d: {(close - hist[10]['close']) / hist[10]['close'] * 100:+.1f}%")
        if len(hist) > 20:
            parts.append(f"20d: {(close - hist[20]['close']) / hist[20]['close'] * 100:+.1f}%")
        price_info = ", ".join(parts)

    # S/R levels
    sr_rows = db.execute(
        "SELECT level, level_type, touch_count FROM support_resistance WHERE symbol = ? ORDER BY level",
        (symbol,),
    ).fetchall()
    close = hist[0]["close"] if hist else 0
    supports = [f"{r['level']:,.0f} ({r['touch_count']}t)" for r in sr_rows if r["level_type"] == "support" and r["level"] < close]
    resistances = [f"{r['level']:,.0f} ({r['touch_count']}t)" for r in sr_rows if r["level_type"] == "resistance" and r["level"] > close]

    # Indicators
    ind = db.execute(
        "SELECT rsi, volume_ratio, ema20, ema50, ema200 FROM indicators WHERE symbol = ? ORDER BY date DESC LIMIT 1",
        (symbol,),
    ).fetchone()

    # Broker narrative
    narrative = generate_narrative(cfg, symbol)

    # Thesis
    thesis = get_thesis(db, symbol)

    db.close()

    # Format output
    lines = [f"=== {symbol} Deep Dive ===", price_info]

    if ind:
        lines.append(f"RSI: {ind['rsi']:.0f}, Volume ratio: {ind['volume_ratio']:.1f}x, EMA20: {ind['ema20']:,.0f}, EMA50: {ind['ema50']:,.0f}, EMA200: {ind['ema200']:,.0f}")

    if signals:
        lines.append(f"\nActive signals ({len(signals)}):")
        for s in signals:
            line = f"  {s['signal_type']} ({s['direction']})"
            if s.get("base_rate"):
                br = s["base_rate"]
                line += f" — avg 10d: {br['avg_return_10d']:+.2f}%, n={br['sample_size']} ({br['scope']})"
            lines.append(line)
    else:
        lines.append("\nNo active signals today.")

    if supports:
        lines.append(f"\nSupport: {', '.join(supports[-3:])}")
    if resistances:
        lines.append(f"Resistance: {', '.join(resistances[:3])}")

    if narrative and narrative != "No notable broker activity":
        lines.append(f"\nBroker activity: {narrative}")

    if thesis:
        lines.append(f"\nYour thesis: {thesis}")

    return "\n".join(lines)


def _handle_chart(cfg, inp):
    from charts import render_chart
    symbol = inp["symbol"].upper()
    days = inp.get("days", 90)
    path = render_chart(cfg, symbol=symbol, days=days)
    if path:
        return f"__CHART__:{path}"
    return f"No chart data available for {symbol}."


def _handle_portfolio(cfg, _inp):
    from portfolio import get_portfolio, get_stop_warnings
    db = get_db(cfg)
    positions = get_portfolio(db)
    db.close()
    warnings = get_stop_warnings(cfg, threshold_pct=5.0)

    if not positions:
        return "No open positions."

    lines = ["Current positions:"]
    for p in positions:
        line = (
            f"  {p['symbol']}: {p['total_lots']} lots @ {p['avg_cost']:,.0f}, "
            f"now {p['current_price']:,.0f} ({p['pnl_pct']:+.1f}%), "
            f"value {p['market_value']:,.0f}"
        )
        if p.get("stop_loss"):
            line += f", stop {p['stop_loss']:,.0f} ({p['stop_distance_pct']:.1f}% away)"
        lines.append(line)

    for w in warnings:
        if w.get("breached"):
            lines.append(f"\n!! STOP BREACHED: {w['symbol']} at {w['current_price']:,.0f} (stop was {w['stop_loss']:,.0f})")
        elif w.get("distance_pct", 100) < 3:
            lines.append(f"\n! {w['symbol']} approaching stop ({w['distance_pct']:.1f}% away)")

    return "\n".join(lines)


def _handle_query_db(cfg, inp):
    sql = inp["sql"].strip()

    forbidden = ["INSERT", "UPDATE", "DELETE", "DROP", "ALTER", "CREATE", "ATTACH", "DETACH"]
    first_word = sql.split()[0].upper() if sql else ""
    if first_word in forbidden:
        return f"Write operations not allowed. Only SELECT queries are permitted."

    db = get_db(cfg)
    try:
        rows = db.execute(sql).fetchall()
        if not rows:
            return "Query returned no results."
        cols = rows[0].keys()
        lines = [" | ".join(cols)]
        lines.append("-" * len(lines[0]))
        for r in rows[:50]:
            lines.append(" | ".join(str(r[c]) for c in cols))
        if len(rows) > 50:
            lines.append(f"... ({len(rows)} total rows, showing first 50)")
        return "\n".join(lines)
    except Exception as e:
        return f"Query error: {e}"
    finally:
        db.close()


def _handle_note(cfg, inp):
    symbol = inp["symbol"].upper()
    text = inp["text"]
    db = get_db(cfg)
    set_thesis(db, symbol, text)
    db.close()
    return f"Thesis saved for {symbol}."


def _handle_recall(cfg, inp):
    db = get_db(cfg)
    symbol = inp.get("symbol", "").upper() if inp.get("symbol") else None

    lines = []
    if symbol:
        thesis = get_thesis(db, symbol)
        if thesis:
            lines.append(f"{symbol} thesis: {thesis}")
        else:
            lines.append(f"No thesis saved for {symbol}.")
    else:
        theses = get_all_theses(db)
        if theses:
            lines.append("All theses:")
            for t in theses:
                lines.append(f"  {t['symbol']}: {t['thesis']}")
        else:
            lines.append("No theses saved.")

    summaries = get_recent_summaries(db, n=5)
    if summaries:
        lines.append("\nRecent sessions:")
        for s in summaries:
            date = s["created_at"][:10]
            lines.append(f"  [{date}] {s['summary']}")

    db.close()
    return "\n".join(lines)
