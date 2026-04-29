"""Screener rules engine."""

from db import get_db
from sector import SECTOR_MAP, get_stock_sector


def _latest_data(db, symbol):
    """Get latest indicator + price row for a symbol (includes open for reversal detection)."""
    row = db.execute(
        """SELECT p.close, p.open, p.volume, p.date as price_date, i.*
           FROM prices p
           JOIN indicators i ON p.symbol = i.symbol AND p.date = i.date
           WHERE p.symbol = ?
           ORDER BY p.date DESC LIMIT 1""",
        (symbol,),
    ).fetchone()
    return dict(row) if row else None


def _latest_fundamentals(db, symbol):
    row = db.execute(
        "SELECT * FROM fundamentals WHERE symbol = ? ORDER BY date DESC LIMIT 1",
        (symbol,),
    ).fetchone()
    return dict(row) if row else {}


def _latest_whale(db, symbol):
    row = db.execute(
        "SELECT * FROM whale_scores WHERE symbol = ? ORDER BY date DESC LIMIT 1",
        (symbol,),
    ).fetchone()
    return dict(row) if row else {}


def _latest_sr(db, symbol):
    rows = db.execute(
        "SELECT level, level_type FROM support_resistance WHERE symbol = ?",
        (symbol,),
    ).fetchall()
    price_row = db.execute(
        "SELECT close FROM prices WHERE symbol = ? ORDER BY date DESC LIMIT 1",
        (symbol,),
    ).fetchone()
    close = price_row["close"] if price_row else None

    supports = [r["level"] for r in rows if r["level_type"] == "support"]
    resistances = [r["level"] for r in rows if r["level_type"] == "resistance"]

    if close:
        supports_below = [s for s in supports if s <= close]
        resistances_above = [r for r in resistances if r >= close]
    else:
        supports_below = supports
        resistances_above = resistances

    return {
        "support": max(supports_below) if supports_below else None,
        "resistance": min(resistances_above) if resistances_above else None,
    }


def _latest_sector_data(db, symbol):
    """Get sector rotation data for the symbol's sector."""
    info = get_stock_sector(db, symbol)
    if not info or not info.get("idx_name"):
        return {}
    sr = db.execute(
        "SELECT * FROM sector_rotation WHERE sector = ? ORDER BY date DESC LIMIT 1",
        (info["idx_name"],),
    ).fetchone()
    if not sr:
        return {}
    return {
        "sector_name": info.get("sector_name", ""),
        "idx_name": info.get("idx_name", ""),
        "sector_rank_5d": sr["rank_5d"],
        "sector_rank_20d": sr["rank_20d"],
        "sector_pct_5d": sr["pct_5d"],
        "sector_pct_20d": sr["pct_20d"],
        "sector_momentum": sr["momentum"],
    }


def _latest_foreign_net(db, symbol):
    """Get recent net foreign flow for a symbol (5-day sum)."""
    rows = db.execute(
        """SELECT foreign_buy, foreign_sell FROM prices
           WHERE symbol = ? AND foreign_buy IS NOT NULL
           ORDER BY date DESC LIMIT 5""",
        (symbol,),
    ).fetchall()
    if not rows:
        return {}
    net = sum((r["foreign_buy"] or 0) - (r["foreign_sell"] or 0) for r in rows)
    return {"foreign_net_5d": net}


def _eval_condition(condition, data):
    """Evaluate a single condition string like 'volume_ratio > 2' or 'close >= support * 0.98'."""
    parts = condition.split()
    if len(parts) == 5 and parts[3] == "*":
        # Handle 'field op ref * mult' format (e.g. 'close >= support * 0.98')
        field, op, ref, _, mult = parts
        ref_val = data.get(ref)
        if ref_val is None:
            return False
        threshold = float(ref_val) * float(mult)
    elif len(parts) == 3:
        field, op, value = parts
        if "*" in value:
            ref, mult = value.split("*")
            ref_val = data.get(ref.strip())
            if ref_val is None:
                return False
            threshold = float(ref_val) * float(mult)
        else:
            try:
                threshold = float(value)
            except ValueError:
                threshold = data.get(value)
                if threshold is None:
                    return False
                threshold = float(threshold)
    else:
        return False

    actual = data.get(field)
    if actual is None:
        return False

    ops = {">": lambda a, b: a > b, "<": lambda a, b: a < b,
           ">=": lambda a, b: a >= b, "<=": lambda a, b: a <= b,
           "==": lambda a, b: a == b}
    return ops.get(op, lambda a, b: False)(float(actual), threshold)


def run_screener(cfg, rule=None, use_pool=False):
    """Run screener rules against watchlist or scan pool symbols.

    Returns dict of {rule_name: [{symbol, sector_name, idx_name, data}]}.
    Each hit includes sector tag and the data snapshot used for evaluation.
    """
    db = get_db(cfg)

    if use_pool:
        rows = db.execute("SELECT symbol FROM scan_pool ORDER BY rank").fetchall()
        symbols = [r["symbol"] for r in rows]
        if not symbols:
            print("Scan pool is empty. Run refresh-pool first.")
            db.close()
            return {}
        print(f"Screening {len(symbols)} pool stocks...")
    else:
        symbols = [s.replace(".JK", "") for s in cfg["watchlist"]]

    rules = cfg["screener"]["rules"]

    if rule:
        rules = {rule: rules[rule]}

    results = {}
    for rule_name, conditions in rules.items():
        hits = []
        for symbol in symbols:
            data = _latest_data(db, symbol)
            if not data:
                continue
            data.update(_latest_fundamentals(db, symbol))
            data.update(_latest_whale(db, symbol))
            data.update(_latest_sr(db, symbol))
            sector_data = _latest_sector_data(db, symbol)
            data.update(sector_data)
            data.update(_latest_foreign_net(db, symbol))

            if all(_eval_condition(c, data) for c in conditions):
                hits.append({
                    "symbol": symbol,
                    "sector_name": sector_data.get("sector_name", ""),
                    "idx_name": sector_data.get("idx_name", ""),
                    "rule": rule_name,
                    "data": data,
                })

        if hits:
            results[rule_name] = hits
            symbols_str = ", ".join(h["symbol"] for h in hits)
            print(f"[{rule_name}] {symbols_str}")

    if not results:
        print("No screener hits.")

    db.close()
    return results
