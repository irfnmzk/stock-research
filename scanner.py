"""Scanner funnel — rank stocks by signal count.

Pre-backfill:  count all signals equally, 3+ to appear.
Post-backfill: only count signals with >55% hit rate (per-ticker or market-wide).
"""

from db import get_db
from signal_engine import evaluate_all

# Minimum avg daily value (IDR) to include in scanner output
LIQUIDITY_FLOOR = 500_000_000


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


def scan(cfg, signals_by_symbol=None, top_n=5, use_base_rates=False):
    """Run the scanner funnel.

    Args:
        cfg: config dict
        signals_by_symbol: pre-computed signals from evaluate_all().
            If None, evaluates signals fresh.
        top_n: max candidates to return
        use_base_rates: if True, only count signals with >55% hit rate

    Returns list of scanner candidates sorted by signal count (descending).
    """
    db = get_db(cfg)

    if signals_by_symbol is None:
        signals_by_symbol = evaluate_all(cfg)

    liquidity = _liquidity_percentiles(db)
    watchlist = {s.replace(".JK", "") for s in cfg.get("watchlist", [])}

    candidates = []
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

        if signal_count < 2:
            continue

        # Sector info
        sector_row = db.execute(
            "SELECT sector_name FROM companies WHERE symbol = ?",
            (symbol,),
        ).fetchone()
        sector = sector_row["sector_name"] if sector_row else ""

        # Days in scanner (how many recent days this stock had 3+ signals)
        recent_hits = db.execute(
            """SELECT COUNT(DISTINCT date) as days FROM signal_events
               WHERE symbol = ? AND date >= date(?, '-7 days')""",
            (symbol, sigs[0].date),
        ).fetchone()
        days_in_scanner = recent_hits["days"] if recent_hits else 1

        candidates.append({
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
        })

    candidates.sort(key=lambda x: x["signal_count"], reverse=True)

    db.close()
    return candidates[:top_n]
