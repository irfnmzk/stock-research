"""Broker narrative — plain-text broker activity summary per stock.

Generates human-readable broker flow stories for watchlist and scanner hits.
Uses significant brokers (|net| > 0.5% of turnover) rather than a static list.
"""

from db import get_db

BROKER_INDIVIDUAL_THRESHOLD = 0.005  # 0.5% of turnover


def _broker_activity(db, symbol, date, lookback=5):
    """Get significant broker activity over the last N days."""
    rows = db.execute(
        """SELECT bs.date, bs.broker_code, bs.net_value, bs.net_lot,
                  (SELECT SUM(b2.buy_value + b2.sell_value)
                   FROM broker_summary b2
                   WHERE b2.symbol = bs.symbol AND b2.date = bs.date) as day_turnover
           FROM broker_summary bs
           WHERE bs.symbol = ? AND bs.date <= ?
           ORDER BY bs.date DESC
           LIMIT 500""",
        (symbol, date),
    ).fetchall()

    by_broker = {}
    for r in rows:
        turnover = r["day_turnover"] or 0
        if turnover <= 0:
            continue
        nv = r["net_value"] or 0
        if abs(nv) / turnover < BROKER_INDIVIDUAL_THRESHOLD:
            continue
        code = r["broker_code"]
        if code not in by_broker:
            by_broker[code] = []
        if len(by_broker[code]) < lookback:
            by_broker[code].append({
                "date": r["date"],
                "net_value": nv,
                "net_lot": r["net_lot"] or 0,
                "significance": abs(nv) / turnover,
            })

    return by_broker


def _foreign_flow_summary(db, symbol, date):
    """Summarize recent foreign flow."""
    rows = db.execute(
        """SELECT COALESCE(foreign_buy, 0) - COALESCE(foreign_sell, 0) as net
           FROM prices
           WHERE symbol = ? AND date <= ? AND foreign_buy IS NOT NULL
           ORDER BY date DESC LIMIT 10""",
        (symbol, date),
    ).fetchall()

    if not rows:
        return ""

    net_1d = rows[0]["net"] if rows else 0
    net_5d = sum(r["net"] for r in rows[:5])
    net_10d = sum(r["net"] for r in rows[:10])

    parts = []
    parts.append(f"Foreign net: {_fmt_value(net_1d)} today")
    parts.append(f"{_fmt_value(net_5d)}/5d")
    if len(rows) >= 10:
        parts.append(f"{_fmt_value(net_10d)}/10d")

    if net_5d > 0 and net_10d < 0:
        parts.append("(reversing after selling)")
    elif net_5d < 0 and net_10d > 0:
        parts.append("(turning negative)")

    return ". ".join(parts) if len(parts) <= 2 else ", ".join(parts)


def _fmt_value(val):
    """Format IDR value in billions."""
    if abs(val) >= 1e9:
        return f"{val/1e9:+.1f}B"
    if abs(val) >= 1e6:
        return f"{val/1e6:+.0f}M"
    return f"{val:+,.0f}"


def _describe_broker(db, broker_code, symbol, days):
    """Build a sentence about one broker's recent activity."""
    consecutive = 0
    total_value = 0
    direction = None

    for d in days:
        net = d["net_value"]
        if net == 0:
            break
        current_dir = "buy" if net > 0 else "sell"
        if direction is None:
            direction = current_dir
        if current_dir != direction:
            break
        consecutive += 1
        total_value += net

    if consecutive == 0 or direction is None:
        return None

    parts = [f"{broker_code} net {direction}"]
    if consecutive > 1:
        parts[0] += f" {consecutive} consecutive days"
    parts.append(f"(total {_fmt_value(total_value)})")

    return " ".join(parts)


def generate_narrative(cfg, symbol, date=None):
    """Generate broker narrative for a single stock.

    Returns a plain-text string describing significant broker activity.
    """
    db = get_db(cfg)

    if date is None:
        row = db.execute("SELECT MAX(date) as d FROM prices WHERE symbol = ?", (symbol,)).fetchone()
        date = row["d"] if row else None
        if not date:
            db.close()
            return ""

    activity = _broker_activity(db, symbol, date)
    parts = []

    for broker_code, days in sorted(activity.items(), key=lambda x: abs(x[1][0]["net_value"]), reverse=True):
        if not days:
            continue
        today_net = days[0]["net_value"] if days and days[0]["date"] == date else 0
        if today_net == 0:
            continue
        desc = _describe_broker(db, broker_code, symbol, days)
        if desc:
            parts.append(desc)

    foreign = _foreign_flow_summary(db, symbol, date)
    if foreign:
        parts.append(foreign)

    db.close()
    return ". ".join(parts) if parts else "No notable broker activity"


def generate_all(cfg, symbols, date=None):
    """Generate broker narratives for multiple symbols.

    Returns dict of {symbol: narrative_string}.
    """
    return {symbol: generate_narrative(cfg, symbol, date) for symbol in symbols}
