"""Report generators for nanobot LLM layer.

Both get_morning_brief_data and get_eod_report_data return structured dicts
that the LLM layer (nanobot) formats into narrative reports.
"""

import json
from db import get_db


def get_morning_brief_data(cfg):
    """Gather data for morning brief pipeline.

    Pipeline: macro regime -> sector shifts -> watchlist key levels -> portfolio nudges.
    Returns dict for LLM to format into commute-friendly brief.
    """
    from macro import get_macro_regime
    from sector import get_sector_leaders
    from portfolio import get_portfolio, get_stop_warnings

    db = get_db(cfg)
    symbols = [s.replace(".JK", "") for s in cfg["watchlist"]]

    # 1. Macro regime
    macro = get_macro_regime(cfg)

    # 2. Sector rotation leaders
    sector_leaders = get_sector_leaders(cfg, db, top_n=3)

    # 3. Watchlist snapshot (key levels, overnight changes)
    watchlist_data = {}
    for symbol in symbols:
        row = db.execute(
            """SELECT p.close, p.volume, p.foreign_buy, p.foreign_sell, p.date,
                      i.rsi, i.volume_ratio, i.ema20, i.ema50,
                      w.composite_score as whale_score
               FROM prices p
               LEFT JOIN indicators i ON p.symbol = i.symbol AND p.date = i.date
               LEFT JOIN whale_scores w ON p.symbol = w.symbol AND p.date = w.date
               WHERE p.symbol = ?
               ORDER BY p.date DESC LIMIT 1""",
            (symbol,),
        ).fetchone()
        if row:
            d = dict(row)
            # Add S/R levels
            sr = db.execute(
                "SELECT level, level_type FROM support_resistance WHERE symbol = ?",
                (symbol,),
            ).fetchall()
            supports = [r["level"] for r in sr if r["level_type"] == "support" and r["level"] < (d.get("close") or 0)]
            resistances = [r["level"] for r in sr if r["level_type"] == "resistance" and r["level"] > (d.get("close") or 0)]
            d["nearest_support"] = max(supports) if supports else None
            d["nearest_resistance"] = min(resistances) if resistances else None
            watchlist_data[symbol] = d

    # 4. Portfolio nudges (stop warnings)
    stop_warnings = get_stop_warnings(cfg, threshold_pct=5.0)

    # 5. Recent news
    news = db.execute(
        "SELECT title, source, symbol_queried, published_at FROM news ORDER BY published_at DESC LIMIT 10"
    ).fetchall()

    db.close()

    return {
        "macro": macro,
        "sector_leaders": sector_leaders,
        "watchlist": watchlist_data,
        "stop_warnings": stop_warnings,
        "news": [dict(n) for n in news],
    }


def get_eod_report_data(cfg):
    """Gather data for full EOD report pipeline.

    Pipeline:
      1. Macro regime
      2. Sector rotation with leaders
      3. Screener hits (grouped by sector)
      4. Signal scores (watchlist + screener hits, with macro + sector)
      5. Portfolio (P&L, tranche suggestions, stop warnings)
      6. News

    Returns structured dict for LLM to format into narrative EOD report.
    """
    from macro import get_macro_regime
    from sector import get_sector_leaders
    from screener import run_screener
    from signals import score_watchlist, score_symbols
    from portfolio import get_portfolio, get_tranche_suggestions, get_stop_warnings

    db = get_db(cfg)
    symbols = [s.replace(".JK", "") for s in cfg["watchlist"]]

    # 1. Macro regime
    macro = get_macro_regime(cfg)

    # 2. Sector rotation leaders
    sector_leaders = get_sector_leaders(cfg, db, top_n=5)

    # 3. Screener hits (full pool scan)
    screener_hits = run_screener(cfg, use_pool=True)

    # Collect unique screener hit symbols
    hit_symbols = set()
    for rule_name, hits in screener_hits.items():
        for h in hits:
            hit_symbols.add(h["symbol"])

    # 4. Signal scores for watchlist + screener hits
    watchlist_scores = score_watchlist(cfg, macro_regime=macro)

    extra_symbols = [s for s in hit_symbols if s not in set(symbols)]
    screener_scores = score_symbols(cfg, extra_symbols, macro_regime=macro) if extra_symbols else []

    all_scores = watchlist_scores + screener_scores

    # 5. Watchlist deep dive data
    watchlist_data = {}
    for symbol in symbols:
        row = db.execute(
            """SELECT p.*, i.rsi, i.volume_ratio, i.macd_hist, i.bb_width,
                      i.ema20, i.ema50,
                      w.composite_score as whale_score,
                      bd.top5_net as bandar_top5_net
               FROM prices p
               LEFT JOIN indicators i ON p.symbol = i.symbol AND p.date = i.date
               LEFT JOIN whale_scores w ON p.symbol = w.symbol AND p.date = w.date
               LEFT JOIN bandar_detector bd ON p.symbol = bd.symbol AND p.date = bd.date
               WHERE p.symbol = ?
               ORDER BY p.date DESC LIMIT 1""",
            (symbol,),
        ).fetchone()
        if row:
            d = dict(row)
            # S/R levels
            sr = db.execute(
                "SELECT level, level_type FROM support_resistance WHERE symbol = ?",
                (symbol,),
            ).fetchall()
            supports = [r["level"] for r in sr if r["level_type"] == "support" and r["level"] < (d.get("close") or 0)]
            resistances = [r["level"] for r in sr if r["level_type"] == "resistance" and r["level"] > (d.get("close") or 0)]
            d["nearest_support"] = max(supports) if supports else None
            d["nearest_resistance"] = min(resistances) if resistances else None
            watchlist_data[symbol] = d

    # 6. Portfolio
    portfolio = get_portfolio(db)
    tranche_suggestions = get_tranche_suggestions(cfg, all_scores, macro_regime=macro)
    stop_warnings = get_stop_warnings(cfg, threshold_pct=3.0)

    # 7. News
    news = db.execute(
        "SELECT title, source, symbol_queried, published_at FROM news ORDER BY published_at DESC LIMIT 20"
    ).fetchall()

    db.close()

    # Group screener hits by sector for report
    hits_by_sector = {}
    for rule_name, hits in screener_hits.items():
        for h in hits:
            sector = h.get("sector_name") or "Unknown"
            if sector not in hits_by_sector:
                hits_by_sector[sector] = []
            hits_by_sector[sector].append({
                "symbol": h["symbol"],
                "rule": rule_name,
            })

    return {
        "macro": macro,
        "sector_leaders": sector_leaders,
        "screener_hits": screener_hits,
        "screener_by_sector": hits_by_sector,
        "signal_scores": all_scores,
        "watchlist": watchlist_data,
        "portfolio": portfolio,
        "tranche_suggestions": tranche_suggestions,
        "stop_warnings": stop_warnings,
        "news": [dict(n) for n in news],
    }


def print_pipeline_json(data: dict):
    """Print pipeline data as JSON for LLM consumption."""

    def _default(obj):
        """Handle non-serializable types."""
        if hasattr(obj, "isoformat"):
            return obj.isoformat()
        if isinstance(obj, bytes):
            return obj.decode("utf-8", errors="replace")
        return str(obj)

    print(json.dumps(data, indent=2, default=_default, ensure_ascii=False))
