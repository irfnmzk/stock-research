"""Scanner funnel — rank stocks by signal count.

Pre-backfill:  count all signals equally, 3+ to appear.
Post-backfill: only count signals with >55% hit rate (per-ticker or market-wide).
"""

from db import get_db
from signal_engine import evaluate_all

# Minimum avg daily value (IDR) to include in scanner output
LIQUIDITY_FLOOR = 500_000_000

NOTABLE_SIGNAL_TYPES = {"volume_spike", "broker_significance", "sr_break"}


def _liquidity_percentiles(db):
    """Compute liquidity percentile for each stock in scan_pool."""
    rows = db.execute(
        """SELECT sp.symbol,
                  (SELECT AVG(p.value) FROM (
                       SELECT value FROM prices
                       WHERE symbol = sp.symbol AND value IS NOT NULL
                       ORDER BY date DESC LIMIT 20
                   ) p) as avg_daily_value
           FROM scan_pool sp
           ORDER BY sp.rank"""
    ).fetchall()

    values = []
    for r in rows:
        values.append({"symbol": r["symbol"], "adv": r["avg_daily_value"] or 0})

    values.sort(key=lambda x: x["adv"])
    n = len(values)
    percentiles = {}
    for i, v in enumerate(values):
        percentiles[v["symbol"]] = round((i / max(n - 1, 1)) * 100) if n > 1 else 50

    return percentiles


def _get_base_rates(db, signal_type, direction, symbol, min_samples=15):
    """Look up base rate for a signal type + direction. Per-ticker first, then market-wide.

    Returns (avg_return_10d, scope) or (None, None) if no data.
    """
    row = db.execute(
        """SELECT avg_return_10d, sample_size FROM signal_base_rates
           WHERE signal_type = ? AND direction = ? AND symbol = ? AND sample_size >= ?""",
        (signal_type, direction, symbol, min_samples),
    ).fetchone()
    if row and row["avg_return_10d"] is not None:
        return row["avg_return_10d"], "per_ticker"

    row = db.execute(
        """SELECT avg_return_10d, sample_size FROM signal_base_rates
           WHERE signal_type = ? AND direction = ? AND (symbol IS NULL OR symbol = '')
             AND sample_size >= ?""",
        (signal_type, direction, min_samples),
    ).fetchone()
    if row and row["avg_return_10d"] is not None:
        return row["avg_return_10d"], "market_wide"

    return None, None


def scan(cfg, signals_by_symbol=None, top_n=5, notable_n=5, use_base_rates=False):
    """Run the scanner funnel.

    Args:
        cfg: config dict
        signals_by_symbol: pre-computed signals from evaluate_all().
            If None, evaluates signals fresh.
        top_n: max "pick" candidates to return
        notable_n: max "notable" candidates (single strong signal)
        use_base_rates: if True, only count signals with >55% hit rate

    Returns list of scanner candidates sorted by tier (pick first, then notable).
    """
    db = get_db(cfg)

    if signals_by_symbol is None:
        signals_by_symbol = evaluate_all(cfg)

    liquidity = _liquidity_percentiles(db)
    from db import get_watchlist
    watchlist = set(get_watchlist(cfg))

    picks = []
    notables = []
    for symbol, sigs in signals_by_symbol.items():
        adv_row = db.execute(
            """SELECT AVG(value) as adv FROM (
                   SELECT value FROM prices
                   WHERE symbol = ? AND value IS NOT NULL
                   ORDER BY date DESC LIMIT 20
               )""",
            (symbol,),
        ).fetchone()
        adv = (adv_row["adv"] or 0) if adv_row else 0
        if adv < LIQUIDITY_FLOOR:
            continue

        if use_base_rates:
            reliable = []
            seen_types = set()
            for s in sigs:
                avg_ret, scope = _get_base_rates(db, s.signal_type, s.direction, symbol)
                if avg_ret is not None and avg_ret >= 1.0:
                    reliable.append({"signal": s, "avg_return": avg_ret, "scope": scope})
                    seen_types.add(s.signal_type)
            signal_count = len(seen_types)
            counted_signals = reliable
        else:
            unique_types = {s.signal_type for s in sigs}
            signal_count = len(unique_types)
            counted_signals = [{"signal": s, "avg_return": None, "scope": None} for s in sigs]

        # Sector info
        sector_row = db.execute(
            "SELECT sector_name FROM companies WHERE symbol = ?",
            (symbol,),
        ).fetchone()
        sector = sector_row["sector_name"] if sector_row else ""

        # Momentum data
        ind_row = db.execute(
            """SELECT i.ema200, i.rsi, p.close
               FROM indicators i
               JOIN prices p ON i.symbol = p.symbol AND i.date = p.date
               WHERE i.symbol = ?
               ORDER BY i.date DESC LIMIT 1""",
            (symbol,),
        ).fetchone()
        ema200 = ind_row["ema200"] if ind_row else None
        rsi = ind_row["rsi"] if ind_row else None
        close = ind_row["close"] if ind_row else None

        # Days in scanner
        recent_hits = db.execute(
            """SELECT COUNT(DISTINCT date) as days FROM signal_events
               WHERE symbol = ? AND date >= date(?, '-7 days')""",
            (symbol, sigs[0].date),
        ).fetchone()
        days_in_scanner = recent_hits["days"] if recent_hits else 1

        candidate = {
            "symbol": symbol,
            "signal_count": signal_count,
            "signals": [
                {
                    "type": cs["signal"]["signal_type"] if isinstance(cs["signal"], dict) else cs["signal"].signal_type,
                    "direction": cs["signal"]["direction"] if isinstance(cs["signal"], dict) else cs["signal"].direction,
                    "description": cs["signal"]["description"] if isinstance(cs["signal"], dict) else cs["signal"].description,
                    "value": cs["signal"]["value"] if isinstance(cs["signal"], dict) else cs["signal"].value,
                    "avg_return_10d": round(cs["avg_return"], 2) if cs["avg_return"] else None,
                    "scope": cs["scope"],
                }
                for cs in counted_signals
            ],
            "liquidity_percentile": liquidity.get(symbol, 0),
            "low_liquidity": liquidity.get(symbol, 0) < 25,
            "sector": sector,
            "days_in_scanner": days_in_scanner,
            "in_watchlist": symbol in watchlist,
            "ema200": ema200,
            "rsi": rsi,
            "close": close,
        }

        if signal_count >= 2:
            candidate["tier"] = "pick"
            picks.append(candidate)
        elif signal_count == 1:
            sig_type = counted_signals[0]["signal"].signal_type if not isinstance(counted_signals[0]["signal"], dict) else counted_signals[0]["signal"]["signal_type"]
            if sig_type in NOTABLE_SIGNAL_TYPES:
                candidate["tier"] = "notable"
                notables.append(candidate)

    picks.sort(key=lambda x: x["signal_count"], reverse=True)
    notables.sort(key=lambda x: x["liquidity_percentile"], reverse=True)

    db.close()
    return picks[:top_n] + notables[:notable_n]
