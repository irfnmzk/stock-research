"""Change detection — diff today vs yesterday.

Compares signal state, price levels, and broker activity between
today and yesterday for watchlist + scanner hits.
"""

from db import get_db
from signal_engine import display_name


def detect_changes(cfg, date=None, symbols=None):
    """Detect what changed today vs yesterday for a set of symbols.

    Returns list of change dicts, each with: symbol, type, description, meta.
    """
    db = get_db(cfg)

    if date is None:
        row = db.execute("SELECT MAX(date) as d FROM prices").fetchone()
        date = row["d"] if row else None
        if not date:
            db.close()
            return []

    prev_row = db.execute(
        "SELECT MAX(date) as d FROM prices WHERE date < ?", (date,)
    ).fetchone()
    prev_date = prev_row["d"] if prev_row else None
    if not prev_date:
        db.close()
        return []

    if symbols is None:
        symbols = [s.replace(".JK", "") for s in cfg.get("watchlist", [])]

    changes = []

    for symbol in symbols:
        changes.extend(_price_changes(db, symbol, date, prev_date))
        changes.extend(_signal_changes(db, symbol, date, prev_date))
        changes.extend(_foreign_flow_changes(db, symbol, date))
        changes.extend(_broker_streak_changes(db, symbol, date, prev_date))

    db.close()
    return changes


def _price_changes(db, symbol, date, prev_date):
    """Detect price crossing EMA20/50 and S/R levels."""
    changes = []

    today = db.execute(
        """SELECT p.close, i.ema20, i.ema50, i.volume_ratio
           FROM prices p
           JOIN indicators i ON p.symbol = i.symbol AND p.date = i.date
           WHERE p.symbol = ? AND p.date = ?""",
        (symbol, date),
    ).fetchone()

    yesterday = db.execute(
        """SELECT p.close, i.ema20, i.ema50
           FROM prices p
           JOIN indicators i ON p.symbol = i.symbol AND p.date = i.date
           WHERE p.symbol = ? AND p.date = ?""",
        (symbol, prev_date),
    ).fetchone()

    if not today or not yesterday:
        return changes

    close_t, close_y = today["close"], yesterday["close"]
    pct_change = (close_t - close_y) / close_y * 100 if close_y else 0

    if abs(pct_change) >= 3:
        direction = "up" if pct_change > 0 else "down"
        changes.append({
            "symbol": symbol,
            "type": "big_move",
            "description": f"Price {direction} {abs(pct_change):.1f}% to {close_t:,.0f}",
            "meta": {"pct_change": round(pct_change, 2)},
        })

    return changes


def _signal_changes(db, symbol, date, prev_date):
    """Detect new signals that weren't firing yesterday."""
    changes = []

    today_sigs = db.execute(
        "SELECT signal_type, trend FROM signal_events WHERE symbol = ? AND date = ?",
        (symbol, date),
    ).fetchall()

    yesterday_types = {
        r["signal_type"]
        for r in db.execute(
            "SELECT signal_type FROM signal_events WHERE symbol = ? AND date = ?",
            (symbol, prev_date),
        ).fetchall()
    }

    new_sigs = [s for s in today_sigs if s["signal_type"] not in yesterday_types]
    for s in new_sigs:
        changes.append({
            "symbol": symbol,
            "type": "new_signal",
            "description": f"New signal: {display_name(s['signal_type'], s['trend'])}",
            "meta": {"signal_type": s["signal_type"], "direction": s["trend"]},
        })

    return changes


def _foreign_flow_changes(db, symbol, date):
    """Detect foreign flow reversal (sign change on 5d net)."""
    changes = []

    rows = db.execute(
        """SELECT date,
                  COALESCE(foreign_buy, 0) - COALESCE(foreign_sell, 0) as net
           FROM prices
           WHERE symbol = ? AND date <= ? AND foreign_buy IS NOT NULL
           ORDER BY date DESC LIMIT 10""",
        (symbol, date),
    ).fetchall()

    if len(rows) < 10:
        return changes

    net_5d_current = sum(r["net"] for r in rows[:5])
    net_5d_prior = sum(r["net"] for r in rows[5:10])

    if net_5d_prior < 0 and net_5d_current > 0:
        changes.append({
            "symbol": symbol,
            "type": "foreign_flow_reversal",
            "description": f"Foreign flow turned positive (5d net: +{net_5d_current/1e9:.1f}B after {net_5d_prior/1e9:.1f}B)",
            "meta": {"net_5d": net_5d_current, "prev_5d": net_5d_prior},
        })
    elif net_5d_prior > 0 and net_5d_current < 0:
        changes.append({
            "symbol": symbol,
            "type": "foreign_flow_reversal",
            "description": f"Foreign flow turned negative (5d net: {net_5d_current/1e9:.1f}B after +{net_5d_prior/1e9:.1f}B)",
            "meta": {"net_5d": net_5d_current, "prev_5d": net_5d_prior},
        })

    return changes


def _broker_streak_changes(db, symbol, date, prev_date):
    """Detect significant broker streak starting or ending."""
    changes = []

    today = db.execute(
        "SELECT smart_broker_streak FROM indicators WHERE symbol = ? AND date = ?",
        (symbol, date),
    ).fetchone()

    yesterday = db.execute(
        "SELECT smart_broker_streak FROM indicators WHERE symbol = ? AND date = ?",
        (symbol, prev_date),
    ).fetchone()

    if not today or not yesterday:
        return changes

    streak_t = today["smart_broker_streak"] or 0
    streak_y = yesterday["smart_broker_streak"] or 0

    if streak_y == 0 and streak_t > 0:
        changes.append({
            "symbol": symbol,
            "type": "broker_streak_start",
            "description": "Significant broker accumulation started",
            "meta": {"streak": streak_t},
        })
    elif streak_y == 0 and streak_t < 0:
        changes.append({
            "symbol": symbol,
            "type": "broker_streak_start",
            "description": "Significant broker distribution started",
            "meta": {"streak": streak_t},
        })
    elif streak_y > 0 and streak_t <= 0:
        changes.append({
            "symbol": symbol,
            "type": "broker_streak_end",
            "description": f"Significant broker accumulation ended after {streak_y}d",
            "meta": {"prev_streak": streak_y},
        })
    elif streak_y < 0 and streak_t >= 0:
        changes.append({
            "symbol": symbol,
            "type": "broker_streak_end",
            "description": f"Significant broker distribution ended after {abs(streak_y)}d",
            "meta": {"prev_streak": streak_y},
        })

    return changes
