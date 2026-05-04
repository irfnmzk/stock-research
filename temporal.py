"""Temporal derived fields — streaks, squeezes, reversals.

Computes fields that require looking at prior-day state.
Results are written back to the indicators table (new columns).
"""

import numpy as np
from db import get_db


BROKER_INDIVIDUAL_THRESHOLD = 0.005  # 0.5% of turnover per broker


def _smart_broker_streak(db, symbol, date):
    """Consecutive days significant brokers are net buyers (negative = selling streak).

    Uses all brokers with individually significant positions (|net| > 0.5% of turnover)
    rather than a static broker list.
    """
    rows = db.execute(
        """SELECT bs.date,
                  SUM(bs.buy_value + bs.sell_value) as turnover,
                  SUM(CASE WHEN ABS(bs.net_value) * 1.0 /
                      NULLIF((SELECT SUM(b2.buy_value + b2.sell_value)
                              FROM broker_summary b2
                              WHERE b2.symbol = bs.symbol AND b2.date = bs.date), 0)
                      >= ? THEN bs.net_value ELSE 0 END) as sig_net
           FROM broker_summary bs
           WHERE bs.symbol = ? AND bs.date <= ?
           GROUP BY bs.date
           ORDER BY bs.date DESC
           LIMIT 30""",
        (BROKER_INDIVIDUAL_THRESHOLD, symbol, date),
    ).fetchall()

    if not rows:
        return 0

    streak = 0
    first_sign = None
    for row in rows:
        net = row["sig_net"] or 0
        if net == 0:
            break
        sign = 1 if net > 0 else -1
        if first_sign is None:
            first_sign = sign
        if sign != first_sign:
            break
        streak += 1

    return streak * (first_sign or 1)


def _bb_squeeze_days(db, symbol, date):
    """Consecutive days BB width has been narrowing."""
    rows = db.execute(
        """SELECT date, bb_width FROM indicators
           WHERE symbol = ? AND date <= ? AND bb_width IS NOT NULL
           ORDER BY date DESC LIMIT 30""",
        (symbol, date),
    ).fetchall()

    if len(rows) < 2:
        return 0

    days = 0
    for i in range(len(rows) - 1):
        if rows[i]["bb_width"] < rows[i + 1]["bb_width"]:
            days += 1
        else:
            break

    return days


def _foreign_flow_reversal(db, symbol, date):
    """True if 5d foreign net > 0 after 10d net < 0."""
    rows = db.execute(
        """SELECT date,
                  COALESCE(foreign_buy, 0) - COALESCE(foreign_sell, 0) as net
           FROM prices
           WHERE symbol = ? AND date <= ? AND foreign_buy IS NOT NULL
           ORDER BY date DESC LIMIT 15""",
        (symbol, date),
    ).fetchall()

    if len(rows) < 10:
        return 0

    net_5d = sum(r["net"] for r in rows[:5])
    net_10d = sum(r["net"] for r in rows[5:15])

    return 1 if (net_5d > 0 and net_10d < 0) else 0


def _accdist_slopes(db, symbol, date):
    """Slope of top5 accdist over 5/10/20 days (linear regression)."""
    rows = db.execute(
        """SELECT date, top5_accdist FROM bandar_detector
           WHERE symbol = ? AND date <= ? AND top5_accdist IS NOT NULL
           ORDER BY date DESC LIMIT 20""",
        (symbol, date),
    ).fetchall()

    results = {"5d": None, "10d": None, "20d": None}

    for window, key in [(5, "5d"), (10, "10d"), (20, "20d")]:
        if len(rows) < window:
            continue
        vals = []
        for r in rows[:window]:
            try:
                vals.append(float(r["top5_accdist"]))
            except (ValueError, TypeError):
                break
        if len(vals) < window:
            continue
        vals = vals[::-1]
        x = np.arange(len(vals), dtype=float)
        y = np.array(vals, dtype=float)
        if np.any(np.isnan(y)):
            continue
        slope = np.polyfit(x, y, 1)[0]
        results[key] = round(float(slope), 4)

    return results


def compute_temporal(cfg, db, symbol):
    """Compute temporal fields for the latest date of a symbol."""
    row = db.execute(
        "SELECT date FROM indicators WHERE symbol = ? ORDER BY date DESC LIMIT 1",
        (symbol,),
    ).fetchone()
    if not row:
        return

    date = row["date"]

    streak = _smart_broker_streak(db, symbol, date)
    squeeze = _bb_squeeze_days(db, symbol, date)
    reversal = _foreign_flow_reversal(db, symbol, date)
    slopes = _accdist_slopes(db, symbol, date)

    db.execute(
        """UPDATE indicators SET
               smart_broker_streak = ?,
               bb_squeeze_days = ?,
               foreign_flow_reversal = ?,
               accdist_slope_5d = ?,
               accdist_slope_10d = ?,
               accdist_slope_20d = ?
           WHERE symbol = ? AND date = ?""",
        (streak, squeeze, reversal,
         slopes["5d"], slopes["10d"], slopes["20d"],
         symbol, date),
    )
    db.commit()


def compute_all(cfg, symbols=None):
    """Compute temporal fields for all symbols."""
    db = get_db(cfg)
    if symbols is None:
        from db import get_watchlist
        symbols = get_watchlist(cfg)
    for symbol in symbols:
        compute_temporal(cfg, db, symbol)
    db.close()
    print(f"  Temporal fields computed for {len(symbols)} stocks")
