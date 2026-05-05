"""US stock scanner — RS-first funnel with signal scoring."""

from db import get_us_db
from signal_engine_us import evaluate_signals


def scan(top_n: int = 15) -> list[dict]:
    """Run the US scanner funnel.

    Returns list of candidates sorted by tier (picks first) then score.
    """
    db = get_us_db()

    row = db.execute("SELECT MAX(date) as d FROM prices").fetchone()
    date = row["d"] if row else None
    if not date:
        db.close()
        return []

    tickers = db.execute(
        """SELECT a.ticker, a.sector, a.sector_etf
           FROM assets a
           WHERE a.active = 1 AND a.quote_type = 'EQUITY'
             AND (a.market_cap IS NULL OR a.market_cap >= 1000000000)
             AND (SELECT COUNT(*) FROM prices WHERE ticker = a.ticker) >= 200"""
    ).fetchall()

    candidates = []
    for row in tickers:
        ticker = row["ticker"]

        rs = db.execute(
            "SELECT rs_vs_spy_10d, rs_vs_sector_10d FROM relative_strength WHERE ticker = ? AND date = ?",
            (ticker, date),
        ).fetchone()
        if not rs or rs["rs_vs_spy_10d"] is None or rs["rs_vs_spy_10d"] <= 0:
            continue

        rs_spy = rs["rs_vs_spy_10d"]
        rs_sector = rs["rs_vs_sector_10d"] or 0

        recent_signals = db.execute(
            """SELECT COUNT(*) as cnt FROM signal_events
               WHERE ticker = ? AND date >= date(?, '-7 days')""",
            (ticker, date),
        ).fetchone()
        if not recent_signals or recent_signals["cnt"] == 0:
            continue

        signals = evaluate_signals(db, ticker, date)

        all_recent = db.execute(
            """SELECT DISTINCT signal_type FROM signal_events
               WHERE ticker = ? AND date >= date(?, '-7 days')""",
            (ticker, date),
        ).fetchall()
        total_signal_types = len(all_recent)

        score = (total_signal_types * 1.0) + (rs_spy * 1.5) + (rs_sector * 1.0)

        ind = db.execute(
            "SELECT ema10, ema21, ema50, ema200, rsi, adr_pct FROM indicators WHERE ticker = ? AND date = ?",
            (ticker, date),
        ).fetchone()

        price = db.execute(
            "SELECT close FROM prices WHERE ticker = ? ORDER BY date DESC LIMIT 1",
            (ticker,),
        ).fetchone()

        if total_signal_types >= 2:
            tier = "pick"
        else:
            tier = "notable"

        candidates.append({
            "ticker": ticker,
            "tier": tier,
            "score": round(score, 2),
            "signal_count": total_signal_types,
            "signals_today": [
                {"type": s.signal_type, "description": s.description}
                for s in signals
            ],
            "rs_vs_spy_10d": round(rs_spy, 2),
            "rs_vs_sector_10d": round(rs_sector, 2),
            "sector": row["sector"],
            "close": price["close"] if price else None,
            "rsi": round(ind["rsi"], 1) if ind and ind["rsi"] else None,
            "adr_pct": round(ind["adr_pct"], 1) if ind and ind["adr_pct"] else None,
            "ema_alignment": _ema_alignment(ind) if ind else "",
        })

    candidates.sort(key=lambda x: (0 if x["tier"] == "pick" else 1, -x["score"]))
    db.close()
    return candidates[:top_n]


def _ema_alignment(ind: dict) -> str:
    e10 = ind["ema10"] if ind["ema10"] else None
    e21 = ind["ema21"] if ind["ema21"] else None
    e50 = ind["ema50"] if ind["ema50"] else None
    e200 = ind["ema200"] if ind["ema200"] else None
    if not all([e10, e21, e50, e200]):
        return ""
    if e10 > e21 > e50 > e200:
        return "perfect uptrend"
    if e10 > e21 > e50:
        return "uptrend (below 200)"
    if e10 > e21:
        return "short-term up"
    return "mixed"


def format_scan_output(candidates: list[dict], base_rates: dict | None = None) -> str:
    """Format scanner output for agent/telegram."""
    if not candidates:
        return "No US scanner candidates today."

    picks = [c for c in candidates if c["tier"] == "pick"]
    notables = [c for c in candidates if c["tier"] == "notable"]

    def _sig_with_br(s):
        desc = s["description"]
        if base_rates and s["type"] in base_rates:
            br = base_rates[s["type"]]
            desc += f" (avg {br['avg_return_10d']:+.2f}% 10d, n={br['sample_size']:,})"
        return desc

    lines = []
    if picks:
        lines.append("US Picks:")
        for c in picks:
            sig_desc = ", ".join(_sig_with_br(s) for s in c["signals_today"][:3])
            lines.append(
                f"  {c['ticker']} — {c['ema_alignment']}, RS +{c['rs_vs_spy_10d']}% vs SPY. "
                f"{sig_desc}. ADR {c['adr_pct']}%."
            )

    if notables:
        lines.append("\nUS Notable:")
        for c in notables:
            sig_desc = ", ".join(_sig_with_br(s) for s in c["signals_today"][:2])
            lines.append(
                f"  {c['ticker']} — RS +{c['rs_vs_spy_10d']}% vs SPY. {sig_desc}."
            )

    return "\n".join(lines)
