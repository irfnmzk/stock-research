"""Whale tracking - broker summary scoring, foreign flow, composite score."""

from db import get_db


def compute_whale_score(cfg, db, symbol):
    """Compute composite whale score for a symbol.

    Foreign flow is derived from prices table (foreign_buy - foreign_sell).
    Broker accumulation from broker_summary table.
    """
    wc = cfg["whale"]
    max_window = max(wc["foreign_flow_windows"])

    # Foreign flow score from prices table
    ff_rows = db.execute(
        "SELECT date, (COALESCE(foreign_buy, 0) - COALESCE(foreign_sell, 0)) as foreign_net "
        "FROM prices WHERE symbol = ? ORDER BY date DESC LIMIT ?",
        (symbol, max_window),
    ).fetchall()

    ff_score = 0.0
    if ff_rows:
        nets = [r["foreign_net"] for r in ff_rows]
        short = nets[:wc["foreign_flow_windows"][0]]
        if short:
            ff_score = sum(1 for n in short if n > 0) / len(short)

    # Broker accumulation score (smart money)
    broker_score = 0.0
    smart = wc["smart_brokers"]
    placeholders = ",".join("?" * len(smart))
    br_rows = db.execute(
        f"SELECT date, broker_code, net_value FROM broker_summary "
        f"WHERE symbol = ? AND broker_code IN ({placeholders}) ORDER BY date DESC LIMIT ?",
        (symbol, *smart, wc["accumulation_days"] * len(smart)),
    ).fetchall()

    if br_rows:
        total_net = sum(r["net_value"] for r in br_rows if r["net_value"])
        broker_score = min(1.0, max(0.0, total_net / 1e11))

    # Composite (no txn_size_score - needs intraday)
    composite = 0.6 * ff_score + 0.4 * broker_score

    db.execute(
        """INSERT OR REPLACE INTO whale_scores
           (symbol, date, foreign_flow_score, broker_score, composite_score)
           VALUES (?, date('now'), ?, ?, ?)""",
        (symbol, round(ff_score, 4), round(broker_score, 4), round(composite, 4)),
    )
    db.commit()
    return composite


def compute_all(cfg, symbols=None):
    """Compute whale scores for all watchlist symbols."""
    db = get_db(cfg)
    syms = symbols or cfg["watchlist"]
    for s in syms:
        symbol = s.replace(".JK", "")
        score = compute_whale_score(cfg, db, symbol)
        print(f"  {symbol}: whale score {score:.2f}")
    db.close()
