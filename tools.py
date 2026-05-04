"""Agent tools — tool definitions and handlers for the Anthropic SDK."""

from db import get_db
from memory import set_thesis, get_thesis, get_all_theses, get_recent_summaries
from signal_engine import display_name


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
        "name": "news",
        "description": "Get recent news and catalysts for a stock. Returns headlines from the last 7 days.",
        "input_schema": {
            "type": "object",
            "properties": {
                "symbol": {"type": "string", "description": "Stock ticker symbol"},
                "days": {"type": "integer", "description": "Number of days to look back (default 7)", "default": 7},
            },
            "required": ["symbol"],
        },
    },
    {
        "name": "research",
        "description": "Search the web for recent news, catalysts, and analysis. Use for earnings, macro events, sector trends, or any topic not in the local database. Powered by Exa semantic search.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query_type": {
                    "type": "string",
                    "enum": ["ticker", "sector", "macro", "custom"],
                    "description": "Type of research: ticker (company news), sector (industry trends), macro (global/Indonesia macro), custom (free-form query)",
                },
                "symbol": {"type": "string", "description": "Stock ticker symbol (required for ticker type)"},
                "sector": {"type": "string", "description": "Sector name: banking, coal, nickel, telco, consumer, property, energy (for sector type)"},
                "query": {"type": "string", "description": "Free-form search query (for custom type)"},
                "days": {"type": "integer", "description": "Days to look back (default 7)", "default": 7},
            },
            "required": ["query_type"],
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
        "description": (
            "Run a read-only SQL query against the research database. Schema:\n"
            "prices(symbol, date, open, high, low, close, volume, value, foreign_buy, foreign_sell, market_cap)\n"
            "indicators(symbol, date, rsi, ema20, ema50, ema200, macd, macd_hist, bb_upper, bb_lower, bb_width, atr, volume_ratio, smart_broker_streak, bb_squeeze_days, accdist_slope_5d/10d/20d)\n"
            "broker_summary(symbol, date, broker_code, buy_lot, sell_lot, net_lot, net_value, avg_price)\n"
            "bandar_detector(symbol, date, top1_net, top3_net, top5_net, top10_net, top1_accdist, top3_accdist, total_buyers, total_sellers)\n"
            "signal_events(symbol, date, signal_type, broker_code, magnitude, close, volume_ratio, regime, trend, fwd_5d, fwd_10d, fwd_20d)\n"
            "signal_base_rates(signal_type, direction, symbol, broker_code, sample_size, hit_rate_5d/10d/20d, avg_return_5d/10d/20d)\n"
            "signals(symbol, date, signal_type, direction, score, description)\n"
            "companies(symbol, name, sector_name, subsector_name, market_cap, last_price, avg_volume)\n"
            "fundamentals(symbol, date, pe_ttm, pe_forward, pbv, ev_ebitda, dividend_yield, earnings_yield)\n"
            "news(symbol_queried, title, content, source, url, published_at, total_likes)\n"
            "insider(symbol, name, date, action_type, change_shares, price, badge)\n"
            "support_resistance(symbol, level, level_type, touch_count, strength_score)\n"
            "sector_rotation(sector, date, pct_5d, pct_10d, pct_20d, rank_5d, rank_10d, rank_20d, momentum)\n"
            "whale_scores(symbol, date, foreign_flow_score, broker_score, composite_score)\n"
            "relative_strength(symbol, date, vs_ihsg_5d/10d/20d, vs_sector_5d/10d/20d)\n"
            "positions(symbol, avg_cost, total_lots, stop_loss)\n"
            "trades(symbol, date, action, lots, price, fees, notes)\n"
            "scan_pool(symbol, market_cap, rank)\n\n"
            "Signal display names (always use these instead of raw signal_type in responses):\n"
            "broker_significance=Broker Accumulation/Distribution, buyer_seller_imbalance=Stealth Buying, "
            "ema_cross=Golden Cross, macd_histogram_flip=MACD Flip, volume_spike=Volume Breakout, "
            "bb_squeeze_release=BB Squeeze Release, sr_break=Falling Knife"
        ),
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
    {
        "name": "watchlist",
        "description": "Show the current watchlist.",
        "input_schema": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "watchlist_add",
        "description": "Add a stock to the watchlist. Use when the user asks to watch or track a stock.",
        "input_schema": {
            "type": "object",
            "properties": {
                "symbol": {"type": "string", "description": "Stock ticker symbol (e.g. ITMG, BBCA)"},
            },
            "required": ["symbol"],
        },
    },
    {
        "name": "watchlist_remove",
        "description": "Remove a stock from the watchlist.",
        "input_schema": {
            "type": "object",
            "properties": {
                "symbol": {"type": "string", "description": "Stock ticker symbol to remove"},
            },
            "required": ["symbol"],
        },
    },
    {
        "name": "refresh",
        "description": "Fetch latest data for a single ticker from Stockbit (prices, broker summary) and recompute indicators, signals, and support/resistance. Call this before ticker_deep_dive when the user asks about a stock mid-day and needs fresh data.",
        "input_schema": {
            "type": "object",
            "properties": {
                "symbol": {"type": "string", "description": "Stock ticker symbol (e.g. BBNI, ITMG)"},
            },
            "required": ["symbol"],
        },
    },
]


def handle_tool(cfg, tool_name, tool_input):
    """Dispatch a tool call and return the result string."""
    handlers = {
        "ticker_deep_dive": _handle_deep_dive,
        "news": _handle_news,
        "research": _handle_research,
        "chart": _handle_chart,
        "portfolio": _handle_portfolio,
        "query_db": _handle_query_db,
        "note": _handle_note,
        "recall": _handle_recall,
        "refresh": _handle_refresh,
        "watchlist": _handle_watchlist,
        "watchlist_add": _handle_watchlist_add,
        "watchlist_remove": _handle_watchlist_remove,
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
            line = f"  {display_name(s['signal_type'], s['direction'])}"
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


def _handle_news(cfg, inp):
    from datetime import datetime, timedelta
    symbol = inp["symbol"].upper()
    days = inp.get("days", 7)
    cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")

    db = get_db(cfg)
    rows = db.execute(
        """SELECT title, source, published_at, url, total_likes
           FROM news
           WHERE symbol_queried = ? AND published_at >= ?
           ORDER BY published_at DESC LIMIT 15""",
        (symbol, cutoff),
    ).fetchall()
    db.close()

    if not rows:
        return f"No news for {symbol} in the last {days} days."

    lines = [f"News for {symbol} (last {days}d):"]
    for r in rows:
        date = r["published_at"][:10] if r["published_at"] else ""
        likes = f" ({r['total_likes']} likes)" if r["total_likes"] else ""
        lines.append(f"  [{date}] {r['title']}{likes}")
        if r["url"]:
            lines.append(f"    {r['url']}")

    return "\n".join(lines)


def _handle_research(cfg, inp):
    from research import research_ticker, research_sector, research_global_macro, exa_search, summarize_research
    from db import get_db as _get_db

    query_type = inp.get("query_type", "custom")
    days = inp.get("days", 7)

    if query_type == "ticker":
        symbol = inp.get("symbol", "").upper()
        if not symbol:
            return "Symbol required for ticker research."
        db = _get_db(cfg)
        results = research_ticker(symbol, days_back=days, db=db)
        db.close()
    elif query_type == "sector":
        sector = inp.get("sector", "")
        if not sector:
            return "Sector required. Options: banking, coal, nickel, telco, consumer, property, energy."
        results = research_sector(sector, days_back=days)
    elif query_type == "macro":
        results = research_global_macro(days_back=days)
    elif query_type == "custom":
        query = inp.get("query", "")
        if not query:
            return "Query required for custom research."
        results = exa_search(query, num_results=5, days_back=days)
    else:
        return f"Unknown query_type: {query_type}"

    if not results:
        return "No research results found."

    return summarize_research(results)


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


def _handle_refresh(cfg, inp):
    from datetime import datetime
    from fetcher import fetch_prices, fetch_broker_summary
    from indicators import compute_all as compute_indicators
    from support_resistance import detect_all as compute_sr
    from whale import compute_all as compute_whales
    from temporal import compute_all as compute_temporal
    from signal_engine import evaluate_all, log_signals
    from macro import get_macro_regime
    from db import get_db as _get_db

    symbol = inp["symbol"].upper()
    syms = [symbol]

    steps = []
    try:
        fetch_prices(cfg, symbols=syms, days=180)
        steps.append("prices")
        fetch_broker_summary(cfg, symbols=syms)
        steps.append("brokers")
    except Exception as e:
        return f"Refresh failed during fetch: {e}"

    try:
        compute_indicators(cfg, symbols=syms)
        steps.append("indicators")
        compute_sr(cfg, symbols=syms)
        steps.append("S/R")
        compute_whales(cfg, symbols=syms)
        steps.append("whale scores")
        compute_temporal(cfg, symbols=syms)
        steps.append("temporal")
    except Exception as e:
        return f"Refresh failed during compute ({', '.join(steps)} ok): {e}"

    try:
        regime_data = get_macro_regime(cfg)
        regime = regime_data.get("regime")
        signals = evaluate_all(cfg, symbols=syms)
        db = _get_db(cfg)
        log_signals(db, signals, regime=regime)
        db.commit()
        db.close()
        steps.append("signals")
    except Exception as e:
        return f"Refresh failed during signals ({', '.join(steps)} ok): {e}"

    now = datetime.now().strftime("%H:%M")
    sig_count = len(signals.get(symbol, []))
    return f"{symbol} refreshed at {now} ({', '.join(steps)}). {sig_count} active signal(s)."


def _handle_watchlist(cfg, _inp):
    from db import get_watchlist
    symbols = get_watchlist(cfg)
    if not symbols:
        return "Watchlist is empty."
    return "Watchlist: " + ", ".join(symbols)


def _handle_watchlist_add(cfg, inp):
    from datetime import datetime
    symbol = inp["symbol"].upper().replace(".JK", "")
    db = get_db(cfg)

    existing = db.execute("SELECT 1 FROM watchlist WHERE symbol = ?", (symbol,)).fetchone()
    if existing:
        db.close()
        return f"{symbol} is already on the watchlist."

    db.execute(
        "INSERT INTO watchlist (symbol, added_at) VALUES (?, ?)",
        (symbol, datetime.now().isoformat()),
    )
    db.commit()
    db.close()
    return f"{symbol} added to watchlist."


def _handle_watchlist_remove(cfg, inp):
    symbol = inp["symbol"].upper().replace(".JK", "")
    db = get_db(cfg)

    existing = db.execute("SELECT 1 FROM watchlist WHERE symbol = ?", (symbol,)).fetchone()
    if not existing:
        db.close()
        return f"{symbol} is not on the watchlist."

    db.execute("DELETE FROM watchlist WHERE symbol = ?", (symbol,))
    db.commit()
    db.close()
    return f"{symbol} removed from watchlist."
