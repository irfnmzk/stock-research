"""Whale tracking - broker summary scoring, foreign flow, composite score."""

from db import get_db

BROKER_INDIVIDUAL_THRESHOLD = 0.005  # 0.5% of turnover


def compute_whale_score(cfg, db, symbol):
    """Compute composite whale score for a symbol.

    Foreign flow is derived from prices table (foreign_buy - foreign_sell).
    Broker accumulation from significant brokers in broker_summary table.
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

    # Broker accumulation score — significant brokers (|net| > 0.5% of turnover)
    broker_score = 0.0
    accum_days = wc.get("accumulation_days", 10)
    br_rows = db.execute(
        """SELECT bs.date, bs.broker_code, bs.net_value,
                  (SELECT SUM(b2.buy_value + b2.sell_value)
                   FROM broker_summary b2
                   WHERE b2.symbol = bs.symbol AND b2.date = bs.date) as day_turnover
           FROM broker_summary bs
           WHERE bs.symbol = ? ORDER BY bs.date DESC LIMIT ?""",
        (symbol, accum_days * 50),
    ).fetchall()

    sig_net_total = 0
    for r in br_rows:
        turnover = r["day_turnover"] or 0
        nv = r["net_value"] or 0
        if turnover > 0 and abs(nv) / turnover >= BROKER_INDIVIDUAL_THRESHOLD:
            sig_net_total += nv

    if sig_net_total != 0:
        broker_score = min(1.0, max(0.0, sig_net_total / 1e11))

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
    if symbols:
        syms = symbols
    else:
        from db import get_watchlist
        syms = get_watchlist(cfg)
    for s in syms:
        symbol = s.replace(".JK", "")
        score = compute_whale_score(cfg, db, symbol)
        print(f"  {symbol}: whale score {score:.2f}")
    db.close()
