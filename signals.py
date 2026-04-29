"""Signal scoring engine - unified scoring with macro/sector/flow integration."""

from db import get_db
from sector import get_stock_sector


def compute_signal_score(conn, symbol: str, cfg: dict, macro_regime: dict = None) -> dict:
    """
    Compute a weighted signal score for a stock.

    Uses configurable weights from cfg['signals']['weights'] and applies
    macro regime modifier. Returns structured dict for pipeline consumption.
    """
    weights = cfg.get("signals", {}).get("weights", {})
    w_whale = weights.get("whale_score", 1.0)
    w_foreign = weights.get("foreign_flow", 1.5)
    w_support = weights.get("near_support", 1.0)
    w_rsi = weights.get("rsi_oversold", 0.5)
    w_volume = weights.get("volume_spike", 0.5)
    w_sector = weights.get("sector_momentum", 0.5)

    score = 0.0
    signals = []
    warnings = []

    # --- 1. Whale / bandar accumulation ---
    bandar = conn.execute("""
        SELECT top5_net, top5_accdist, total_value
        FROM bandar_detector WHERE symbol = ?
        ORDER BY date DESC LIMIT 3
    """, (symbol,)).fetchall()

    if bandar:
        latest_net = bandar[0]["top5_net"] or 0
        if latest_net > 0:
            score += w_whale
            signals.append(f"Bandar net buy {latest_net/1e9:+.1f}B")
            # Multi-day accumulation = stronger
            if len(bandar) >= 2 and (bandar[1]["top5_net"] or 0) > 0:
                score += w_whale * 0.5
                signals.append("Multi-day accumulation (2+ days)")
        elif latest_net < 0:
            warnings.append(f"Bandar distributing {latest_net/1e9:.1f}B")

    # Whale composite score from whale_scores table
    whale_row = conn.execute(
        "SELECT composite_score FROM whale_scores WHERE symbol = ? ORDER BY date DESC LIMIT 1",
        (symbol,),
    ).fetchone()
    if whale_row and whale_row["composite_score"]:
        cs = whale_row["composite_score"]
        if cs > 0.7:
            score += w_whale * 0.5
            signals.append(f"Whale composite high ({cs:.2f})")
        elif cs < 0.3:
            warnings.append(f"Whale composite low ({cs:.2f})")

    # --- 2. Foreign flow (heaviest weight per user preference) ---
    foreign = conn.execute("""
        SELECT foreign_buy, foreign_sell FROM prices WHERE symbol = ?
        ORDER BY date DESC LIMIT 5
    """, (symbol,)).fetchall()

    if foreign:
        net_3d = sum((r["foreign_buy"] or 0) - (r["foreign_sell"] or 0) for r in foreign[:3])
        net_5d = sum((r["foreign_buy"] or 0) - (r["foreign_sell"] or 0) for r in foreign)
        if net_5d > 0:
            score += w_foreign
            signals.append(f"Foreign net buy 5d: {net_5d/1e9:+.1f}B")
        elif net_3d > 0 and net_5d <= 0:
            score += w_foreign * 0.3
            signals.append(f"Foreign turning positive (3d: {net_3d/1e9:+.1f}B)")
        elif net_5d < -5e9:
            warnings.append(f"Foreign net sell 5d: {net_5d/1e9:.1f}B")

        # Foreign flow spike detection (folded from alerts.py)
        if foreign:
            latest_net = (foreign[0]["foreign_buy"] or 0) - (foreign[0]["foreign_sell"] or 0)
            if abs(latest_net) >= 50e9:
                direction = "inflow" if latest_net > 0 else "outflow"
                signals.append(f"Foreign {direction} spike: {abs(latest_net)/1e9:.1f}B")
                if latest_net > 0:
                    score += w_foreign * 0.3

    # --- 3. RSI ---
    ind = conn.execute("""
        SELECT rsi, volume_ratio, macd_hist, ema20, ema50
        FROM indicators WHERE symbol = ?
        ORDER BY date DESC LIMIT 2
    """, (symbol,)).fetchall()

    if ind:
        rsi = ind[0]["rsi"]
        if rsi and rsi < 30:
            score += w_rsi * 2
            signals.append(f"RSI oversold ({rsi:.0f})")
        elif rsi and rsi < 40:
            score += w_rsi
            signals.append(f"RSI low ({rsi:.0f})")
        elif rsi and rsi > 70:
            warnings.append(f"RSI overbought ({rsi:.0f})")

        # Volume spike (folded from alerts.py)
        vol_ratio = ind[0]["volume_ratio"]
        if vol_ratio and vol_ratio > 2.5:
            score += w_volume * 2
            signals.append(f"Volume spike ({vol_ratio:.1f}x avg)")
        elif vol_ratio and vol_ratio > 2.0:
            score += w_volume
            signals.append(f"Volume elevated ({vol_ratio:.1f}x avg)")

        # MACD turning positive
        macd = ind[0]["macd_hist"]
        if macd and len(ind) >= 2 and ind[1]["macd_hist"]:
            if macd > ind[1]["macd_hist"] and ind[1]["macd_hist"] < 0:
                score += 0.3
                signals.append("MACD turning up")

    # --- 4. Price near support ---
    price_row = conn.execute(
        "SELECT close, date FROM prices WHERE symbol = ? ORDER BY date DESC LIMIT 1",
        (symbol,),
    ).fetchone()

    sr = conn.execute(
        "SELECT level, level_type FROM support_resistance WHERE symbol = ? ORDER BY last_touched DESC LIMIT 10",
        (symbol,),
    ).fetchall()

    if price_row and sr:
        price = price_row["close"]
        supports = [r["level"] for r in sr if r["level_type"] == "support" and r["level"] < price]
        if supports:
            nearest_support = max(supports)
            distance_pct = ((price / nearest_support) - 1) * 100
            if distance_pct < 2:
                score += w_support
                signals.append(f"Near support {nearest_support:,.0f} ({distance_pct:.1f}% above)")
            elif distance_pct < 5:
                score += w_support * 0.5
                signals.append(f"Support at {nearest_support:,.0f} ({distance_pct:.1f}% below)")

        # Breakout detection (folded from alerts.py)
        resistances = [r["level"] for r in sr if r["level_type"] == "resistance" and r["level"] <= price]
        if resistances and ind:
            vol_ratio = ind[0]["volume_ratio"] if ind else None
            if vol_ratio and vol_ratio > 1.5:
                broken = max(resistances)
                score += 0.5
                signals.append(f"Breakout above {broken:,.0f} on volume")

    # --- 5. EMA alignment ---
    if ind and price_row:
        price = price_row["close"]
        ema20 = ind[0]["ema20"]
        ema50 = ind[0]["ema50"]
        if ema20 and ema50:
            if price < ema20 and abs(price - ema20) / ema20 < 0.02:
                score += 0.3
                signals.append(f"Testing EMA20 ({ema20:,.0f})")
            if price < ema50 and abs(price - ema50) / ema50 < 0.02:
                score += 0.3
                signals.append(f"Testing EMA50 ({ema50:,.0f})")

    # --- 6. Sector momentum boost ---
    sector_info = get_stock_sector(conn, symbol)
    if sector_info.get("idx_name"):
        sr_row = conn.execute(
            "SELECT rank_5d, momentum FROM sector_rotation WHERE sector = ? ORDER BY date DESC LIMIT 1",
            (sector_info["idx_name"],),
        ).fetchone()
        if sr_row:
            rank = sr_row["rank_5d"]
            if rank and rank <= 3:
                score += w_sector
                signals.append(f"Sector top-3 ({sector_info['sector_name']}, rank {rank})")
            momentum = sr_row["momentum"]
            if momentum and momentum > 3:
                score += w_sector * 0.5
                signals.append(f"Sector improving (momentum +{momentum:.0f})")

    # --- 7. Macro regime modifier ---
    if macro_regime:
        regime = macro_regime.get("regime", "cautious")
        modifiers = cfg.get("signals", {}).get("macro_modifier", {})
        modifier = modifiers.get(regime, 0.0)
        if modifier != 0:
            score += modifier
            if modifier > 0:
                signals.append(f"Macro {regime} bonus (+{modifier})")
            else:
                warnings.append(f"Macro {regime} penalty ({modifier})")

    # Round to nearest 0.5
    score = round(max(0, score) * 2) / 2

    # Suggested action
    threshold = cfg.get("signals", {}).get("score_threshold", 3.0)
    if score >= threshold + 1:
        action = "STRONG BUY signal - consider entry"
    elif score >= threshold:
        action = "BUY signal - good entry if fits plan"
    elif score >= threshold - 1:
        action = "WATCH - building but not ready"
    elif score >= 1:
        action = "HOLD - weak signal, wait for confirmation"
    else:
        action = "NO SIGNAL - stay away or wait"

    return {
        "symbol": symbol,
        "score": score,
        "signals": signals,
        "warnings": warnings,
        "action": action,
        "sector": sector_info.get("sector_name", ""),
    }


def score_watchlist(cfg, macro_regime: dict = None) -> list[dict]:
    """Score all watchlist stocks with macro context."""
    conn = get_db(cfg)
    symbols = [s.replace(".JK", "") for s in cfg["watchlist"]]

    results = []
    for symbol in symbols:
        result = compute_signal_score(conn, symbol, cfg, macro_regime)
        results.append(result)

    conn.close()
    results.sort(key=lambda x: x["score"], reverse=True)
    return results


def score_symbols(cfg, symbols: list[str], macro_regime: dict = None) -> list[dict]:
    """Score an arbitrary list of symbols (for screener hits, pool top, etc.)."""
    conn = get_db(cfg)
    results = []
    for symbol in symbols:
        result = compute_signal_score(conn, symbol, cfg, macro_regime)
        results.append(result)
    conn.close()
    results.sort(key=lambda x: x["score"], reverse=True)
    return results


def score_pool_top(cfg, top_n: int = 30, macro_regime: dict = None) -> list[dict]:
    """Score top N stocks from scan pool for entry candidates."""
    conn = get_db(cfg)
    rows = conn.execute(
        "SELECT symbol FROM scan_pool ORDER BY rank LIMIT ?", (top_n,)
    ).fetchall()

    threshold = cfg.get("signals", {}).get("score_threshold", 3.0) - 1
    results = []
    for row in rows:
        symbol = row["symbol"]
        result = compute_signal_score(conn, symbol, cfg, macro_regime)
        if result["score"] >= threshold:
            results.append(result)

    conn.close()
    results.sort(key=lambda x: x["score"], reverse=True)
    return results


def format_signal_report(scores: list[dict]) -> str:
    """Format signal scores for display."""
    lines = []
    for s in scores:
        filled = int(s["score"])
        empty = max(0, 5 - filled)
        stars = "★" * filled + "☆" * empty
        sector_tag = f" [{s['sector']}]" if s.get("sector") else ""
        lines.append(f"{s['symbol']}{sector_tag}: {stars} ({s['score']})")
        for sig in s["signals"]:
            lines.append(f"  ✓ {sig}")
        for warn in s["warnings"]:
            lines.append(f"  ✗ {warn}")
        lines.append(f"  → {s['action']}")
        lines.append("")
    return "\n".join(lines)
