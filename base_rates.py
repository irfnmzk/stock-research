"""Base rates — compute and query signal performance statistics.

Computes per-signal-type hit rates and avg returns (per-ticker and market-wide).
Query functions return base rates for use in scanner, agent, and tools.
"""

from db import get_db

MIN_SAMPLES_TICKER = 15
MIN_SAMPLES_GLOBAL = 10


# ---------------------------------------------------------------------------
# Compute functions (run after backfill or periodically)
# ---------------------------------------------------------------------------

def compute_signal_base_rates(cfg):
    """Compute signal base rates from signal_events and write to signal_base_rates.

    Computes both per-ticker and market-wide rates for each signal type + direction.
    For bullish signals: hit = fwd_Xd > 0
    For bearish signals: hit = fwd_Xd < 0
    """
    db = get_db(cfg)

    db.execute("DELETE FROM signal_base_rates")

    # Per-ticker rates — bullish
    db.execute("""
        INSERT INTO signal_base_rates
            (signal_type, direction, symbol, broker_code, sample_size,
             hit_rate_5d, hit_rate_10d, hit_rate_20d,
             avg_return_5d, avg_return_10d, avg_return_20d,
             median_return_5d, median_return_10d, median_return_20d,
             last_computed)
        SELECT
            signal_type, 'bullish' as direction, symbol, '' as broker_code,
            COUNT(*) as sample_size,
            AVG(CASE WHEN fwd_5d > 0 THEN 1.0 ELSE 0.0 END),
            AVG(CASE WHEN fwd_10d > 0 THEN 1.0 ELSE 0.0 END),
            AVG(CASE WHEN fwd_20d > 0 THEN 1.0 ELSE 0.0 END),
            AVG(fwd_5d), AVG(fwd_10d), AVG(fwd_20d),
            0, 0, 0,
            datetime('now')
        FROM signal_events
        WHERE filled_through >= 20 AND trend = 'bullish'
        GROUP BY signal_type, symbol
        HAVING COUNT(*) >= ?
    """, (MIN_SAMPLES_GLOBAL,))

    # Per-ticker rates — bearish (hit = price went DOWN)
    db.execute("""
        INSERT INTO signal_base_rates
            (signal_type, direction, symbol, broker_code, sample_size,
             hit_rate_5d, hit_rate_10d, hit_rate_20d,
             avg_return_5d, avg_return_10d, avg_return_20d,
             median_return_5d, median_return_10d, median_return_20d,
             last_computed)
        SELECT
            signal_type, 'bearish' as direction, symbol, '' as broker_code,
            COUNT(*) as sample_size,
            AVG(CASE WHEN fwd_5d < 0 THEN 1.0 ELSE 0.0 END),
            AVG(CASE WHEN fwd_10d < 0 THEN 1.0 ELSE 0.0 END),
            AVG(CASE WHEN fwd_20d < 0 THEN 1.0 ELSE 0.0 END),
            AVG(fwd_5d), AVG(fwd_10d), AVG(fwd_20d),
            0, 0, 0,
            datetime('now')
        FROM signal_events
        WHERE filled_through >= 20 AND trend = 'bearish'
        GROUP BY signal_type, symbol
        HAVING COUNT(*) >= ?
    """, (MIN_SAMPLES_GLOBAL,))

    # Market-wide rates — bullish
    db.execute("""
        INSERT INTO signal_base_rates
            (signal_type, direction, symbol, broker_code, sample_size,
             hit_rate_5d, hit_rate_10d, hit_rate_20d,
             avg_return_5d, avg_return_10d, avg_return_20d,
             median_return_5d, median_return_10d, median_return_20d,
             last_computed)
        SELECT
            signal_type, 'bullish' as direction, '' as symbol, '' as broker_code,
            COUNT(*) as sample_size,
            AVG(CASE WHEN fwd_5d > 0 THEN 1.0 ELSE 0.0 END),
            AVG(CASE WHEN fwd_10d > 0 THEN 1.0 ELSE 0.0 END),
            AVG(CASE WHEN fwd_20d > 0 THEN 1.0 ELSE 0.0 END),
            AVG(fwd_5d), AVG(fwd_10d), AVG(fwd_20d),
            0, 0, 0,
            datetime('now')
        FROM signal_events
        WHERE filled_through >= 20 AND trend = 'bullish'
        GROUP BY signal_type
        HAVING COUNT(*) >= ?
    """, (MIN_SAMPLES_GLOBAL,))

    # Market-wide rates — bearish (hit = price went DOWN)
    db.execute("""
        INSERT INTO signal_base_rates
            (signal_type, direction, symbol, broker_code, sample_size,
             hit_rate_5d, hit_rate_10d, hit_rate_20d,
             avg_return_5d, avg_return_10d, avg_return_20d,
             median_return_5d, median_return_10d, median_return_20d,
             last_computed)
        SELECT
            signal_type, 'bearish' as direction, '' as symbol, '' as broker_code,
            COUNT(*) as sample_size,
            AVG(CASE WHEN fwd_5d < 0 THEN 1.0 ELSE 0.0 END),
            AVG(CASE WHEN fwd_10d < 0 THEN 1.0 ELSE 0.0 END),
            AVG(CASE WHEN fwd_20d < 0 THEN 1.0 ELSE 0.0 END),
            AVG(fwd_5d), AVG(fwd_10d), AVG(fwd_20d),
            0, 0, 0,
            datetime('now')
        FROM signal_events
        WHERE filled_through >= 20 AND trend = 'bearish'
        GROUP BY signal_type
        HAVING COUNT(*) >= ?
    """, (MIN_SAMPLES_GLOBAL,))

    db.commit()

    count = db.execute("SELECT COUNT(*) FROM signal_base_rates").fetchone()[0]
    print(f"  Computed {count} signal base rate entries")

    db.close()
    return count


# ---------------------------------------------------------------------------
# Query functions
# ---------------------------------------------------------------------------

def get_signal_base_rate(cfg, signal_type, direction="bullish", symbol=None, min_samples=10):
    """Get base rate for a signal type + direction.

    Tries per-ticker first, falls back to market-wide.
    Returns dict with hit rates, avg returns, sample size, scope.
    """
    db = get_db(cfg)

    if symbol:
        row = db.execute(
            """SELECT * FROM signal_base_rates
               WHERE signal_type = ? AND direction = ? AND symbol = ? AND sample_size >= ?""",
            (signal_type, direction, symbol, min_samples),
        ).fetchone()
        if row:
            db.close()
            return _format_base_rate(row, "per_ticker")

    row = db.execute(
        """SELECT * FROM signal_base_rates
           WHERE signal_type = ? AND direction = ? AND (symbol IS NULL OR symbol = '')
             AND sample_size >= ?""",
        (signal_type, direction, min_samples),
    ).fetchone()
    db.close()

    if row:
        return _format_base_rate(row, "market_wide")
    return None


def get_active_signals_with_rates(cfg, symbol, date=None):
    """Get all signals firing on a ticker with their base rates.

    Used by ticker_deep_dive tool.
    """
    db = get_db(cfg)

    if date is None:
        row = db.execute(
            "SELECT MAX(date) as d FROM signal_events WHERE symbol = ?",
            (symbol,),
        ).fetchone()
        date = row["d"] if row else None
        if not date:
            db.close()
            return []

    rows = db.execute(
        """SELECT signal_type, trend, magnitude, meta
           FROM signal_events
           WHERE symbol = ? AND date = ?""",
        (symbol, date),
    ).fetchall()

    results = []
    for r in rows:
        base_rate = get_signal_base_rate(cfg, r["signal_type"], r["trend"], symbol)
        results.append({
            "signal_type": r["signal_type"],
            "direction": r["trend"],
            "magnitude": r["magnitude"],
            "base_rate": base_rate,
        })

    db.close()
    return results


def fill_forward_returns(cfg):
    """Daily job: fill forward returns for recent signal_events."""
    db = get_db(cfg)

    rows = db.execute(
        """SELECT se.rowid, se.symbol, se.date, se.close, se.filled_through
           FROM signal_events se
           WHERE se.close IS NOT NULL
             AND se.filled_through < 20
           ORDER BY se.date"""
    ).fetchall()

    if not rows:
        db.close()
        return 0

    filled = 0
    for row in rows:
        symbol = row["symbol"]
        sig_date = row["date"]
        sig_close = row["close"]
        already = row["filled_through"] or 0

        future = db.execute(
            """SELECT date, close FROM prices
               WHERE symbol = ? AND date > ?
               ORDER BY date LIMIT 20""",
            (symbol, sig_date),
        ).fetchall()

        fwd_5d = fwd_10d = fwd_20d = None
        for j, f in enumerate(future):
            ret = (f["close"] - sig_close) / sig_close * 100
            if j == 4:
                fwd_5d = round(ret, 2)
            if j == 9:
                fwd_10d = round(ret, 2)
            if j == 19:
                fwd_20d = round(ret, 2)

        new_filled = 0
        if fwd_20d is not None:
            new_filled = 20
        elif fwd_10d is not None:
            new_filled = 10
        elif fwd_5d is not None:
            new_filled = 5

        if new_filled > already:
            db.execute(
                """UPDATE signal_events SET
                       fwd_5d = COALESCE(?, fwd_5d),
                       fwd_10d = COALESCE(?, fwd_10d),
                       fwd_20d = COALESCE(?, fwd_20d),
                       filled_through = ?
                   WHERE rowid = ?""",
                (fwd_5d, fwd_10d, fwd_20d, new_filled, row["rowid"]),
            )
            filled += 1

    db.commit()
    db.close()
    return filled


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _format_base_rate(row, scope):
    return {
        "signal_type": row["signal_type"],
        "symbol": row["symbol"] or None,
        "sample_size": row["sample_size"],
        "hit_rate_5d": row["hit_rate_5d"],
        "hit_rate_10d": row["hit_rate_10d"],
        "hit_rate_20d": row["hit_rate_20d"],
        "avg_return_5d": row["avg_return_5d"],
        "avg_return_10d": row["avg_return_10d"],
        "avg_return_20d": row["avg_return_20d"],
        "scope": scope,
    }
