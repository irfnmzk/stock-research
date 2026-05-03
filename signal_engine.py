"""Signal evaluation engine — state-change signals for all 300 stocks.

A signal fires on the day a transition happens, not while a condition persists.
Each evaluator compares today vs yesterday to detect the change.
"""

from dataclasses import dataclass, asdict
import json
from db import get_db

BROKER_SIG_THRESHOLD = 0.03      # 3% net significance to fire signal
BROKER_INDIVIDUAL_THRESHOLD = 0.005  # 0.5% of turnover per broker

SIGNAL_DISPLAY_NAMES = {
    ("broker_significance", "bullish"): "Broker Accumulation",
    ("broker_significance", "bearish"): "Broker Distribution",
    ("buyer_seller_imbalance", "bullish"): "Absorption",
    ("ema_cross", "bullish"): "EMA Cross",
    ("macd_histogram_flip", "bullish"): "MACD Flip",
    ("volume_spike", "bullish"): "Volume Spike",
    ("bb_squeeze_release", "bullish"): "Squeeze Break",
    ("sr_break", "bearish"): "Support Break",
}


def display_name(signal_type, direction):
    return SIGNAL_DISPLAY_NAMES.get((signal_type, direction), signal_type)


@dataclass
class Signal:
    signal_type: str
    symbol: str
    date: str
    direction: str  # "bullish" or "bearish"
    value: float | None = None
    description: str = ""
    meta: dict | None = None

    def to_dict(self):
        d = asdict(self)
        if d["meta"] is None:
            d.pop("meta")
        return d


# ---------------------------------------------------------------------------
# Data loaders — fetch today + yesterday rows for a symbol
# ---------------------------------------------------------------------------

def _load_indicator_pair(db, symbol, date):
    """Load today and yesterday indicator rows."""
    rows = db.execute(
        """SELECT * FROM indicators
           WHERE symbol = ? AND date <= ?
           ORDER BY date DESC LIMIT 2""",
        (symbol, date),
    ).fetchall()
    if not rows or rows[0]["date"] != date:
        return None, None
    today = dict(rows[0])
    yesterday = dict(rows[1]) if len(rows) > 1 else None
    return today, yesterday


def _load_price_pair(db, symbol, date):
    """Load today and yesterday price rows."""
    rows = db.execute(
        """SELECT * FROM prices
           WHERE symbol = ? AND date <= ?
           ORDER BY date DESC LIMIT 2""",
        (symbol, date),
    ).fetchall()
    if not rows or rows[0]["date"] != date:
        return None, None
    today = dict(rows[0])
    yesterday = dict(rows[1]) if len(rows) > 1 else None
    return today, yesterday


def _load_sr_levels(db, symbol):
    """Load S/R levels for a symbol."""
    rows = db.execute(
        """SELECT level, level_type, touch_count
           FROM support_resistance WHERE symbol = ?""",
        (symbol,),
    ).fetchall()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Bandarmology signals (2)
# ---------------------------------------------------------------------------

def _eval_broker_significance(db, symbol, date, price_today):
    """Signal 1: Significant net flow imbalance across all brokers.

    Finds brokers with individually significant positions (|net| > 0.5% of turnover),
    then measures their aggregate net flow as % of turnover.
    Fires when aggregate net significance >= 3%.
    """
    signals = []
    if not price_today:
        return signals

    turnover_row = db.execute(
        """SELECT SUM(buy_value + sell_value) as turnover
           FROM broker_summary WHERE symbol = ? AND date = ?""",
        (symbol, date),
    ).fetchone()
    turnover = (turnover_row["turnover"] or 0) if turnover_row else 0
    if turnover <= 0:
        return signals

    rows = db.execute(
        """SELECT broker_code, net_value
           FROM broker_summary WHERE symbol = ? AND date = ?""",
        (symbol, date),
    ).fetchall()
    if not rows:
        return signals

    sig_brokers = []
    for r in rows:
        nv = r["net_value"] or 0
        if abs(nv) / turnover >= BROKER_INDIVIDUAL_THRESHOLD:
            sig_brokers.append({"code": r["broker_code"], "net_value": nv})

    if not sig_brokers:
        return signals

    sig_net = sum(b["net_value"] for b in sig_brokers)
    net_significance = abs(sig_net) / turnover

    if net_significance < BROKER_SIG_THRESHOLD:
        return signals

    direction = "bullish" if sig_net > 0 else "bearish"
    action = "net buy" if sig_net > 0 else "net sell"
    signals.append(Signal(
        signal_type="broker_significance",
        symbol=symbol,
        date=date,
        direction=direction,
        value=sig_net,
        description=f"{len(sig_brokers)} brokers {action} {abs(sig_net)/1e9:.1f}B ({net_significance:.1%} of turnover)",
        meta={
            "n_significant_brokers": len(sig_brokers),
            "sig_net": sig_net,
            "net_significance": round(net_significance, 4),
            "turnover": turnover,
        },
    ))

    return signals


def _eval_buyer_seller_imbalance(db, symbol, date, price_today, price_yesterday):
    """Signal 2: Few buyers absorbing many sellers + price up (absorption pattern)."""
    signals = []
    if not price_today or not price_yesterday:
        return signals

    bd = db.execute(
        "SELECT total_buyers, total_sellers, top5_net FROM bandar_detector WHERE symbol = ? AND date = ?",
        (symbol, date),
    ).fetchone()
    if not bd or not bd["total_buyers"] or not bd["total_sellers"]:
        return signals

    buyers = bd["total_buyers"]
    sellers = bd["total_sellers"]
    if buyers == 0 or sellers == 0:
        return signals

    ratio = buyers / sellers
    price_change = (price_today["close"] - price_yesterday["close"]) / price_yesterday["close"]

    if ratio < 0.33 and price_change > 0:
        signals.append(Signal(
            signal_type="buyer_seller_imbalance",
            symbol=symbol,
            date=date,
            direction="bullish",
            value=round(ratio, 2),
            description=f"Few buyers ({buyers}) absorbing many sellers ({sellers}), price up {price_change:.1%}",
            meta={"buyers": buyers, "sellers": sellers, "ratio": round(ratio, 2),
                  "top5_net": bd["top5_net"], "price_change_pct": round(price_change * 100, 2)},
        ))

    return signals


# ---------------------------------------------------------------------------
# Technical signals (4)
# ---------------------------------------------------------------------------

def _eval_ema_cross(ind_today, ind_yesterday, symbol, date):
    """EMA20 crosses above EMA50 (golden cross only)."""
    signals = []
    if not ind_yesterday:
        return signals

    ema20_t = ind_today.get("ema20")
    ema50_t = ind_today.get("ema50")
    ema20_y = ind_yesterday.get("ema20")
    ema50_y = ind_yesterday.get("ema50")

    if None in (ema20_t, ema50_t, ema20_y, ema50_y):
        return signals

    if ema20_y <= ema50_y and ema20_t > ema50_t:
        signals.append(Signal(
            signal_type="ema_cross",
            symbol=symbol, date=date, direction="bullish",
            value=round(ema20_t - ema50_t, 2),
            description=f"Golden cross — EMA20 ({ema20_t:,.0f}) crossed above EMA50 ({ema50_t:,.0f})",
        ))

    return signals


def _eval_macd_histogram_flip(ind_today, ind_yesterday, symbol, date):
    """MACD histogram flips positive (bullish only)."""
    signals = []
    if not ind_yesterday:
        return signals

    hist_t = ind_today.get("macd_hist")
    hist_y = ind_yesterday.get("macd_hist")
    if hist_t is None or hist_y is None:
        return signals

    if hist_y < 0 and hist_t >= 0:
        signals.append(Signal(
            signal_type="macd_histogram_flip",
            symbol=symbol, date=date, direction="bullish",
            value=round(hist_t, 4),
            description=f"MACD histogram flipped positive ({hist_t:.2f})",
        ))

    return signals


def _eval_volume_spike(ind_today, price_today, symbol, date):
    """Volume ratio > 2.0 on a green candle (bullish only)."""
    signals = []
    vr = ind_today.get("volume_ratio")
    if vr is None or vr < 2.0:
        return signals

    close = price_today["close"]
    open_ = price_today["open"]
    if close < open_:
        return signals

    signals.append(Signal(
        signal_type="volume_spike",
        symbol=symbol, date=date, direction="bullish",
        value=round(vr, 2),
        description=f"Volume spike {vr:.1f}x average (green candle)",
    ))

    return signals


def _eval_bb_squeeze_release(ind_today, ind_yesterday, price_today, symbol, date):
    """BB squeeze release — width was contracting 5+ days, now expanding upward (bullish only)."""
    signals = []
    if not ind_yesterday:
        return signals

    bw_t = ind_today.get("bb_width")
    bw_y = ind_yesterday.get("bb_width")

    if bw_t is None or bw_y is None:
        return signals

    prev_squeeze = (ind_yesterday.get("bb_squeeze_days") or 0)
    if prev_squeeze >= 5 and bw_t > bw_y:
        bb_mid = (ind_today.get("bb_upper", 0) + ind_today.get("bb_lower", 0)) / 2 if ind_today.get("bb_upper") else None
        close = price_today["close"]
        if bb_mid and close > bb_mid:
            signals.append(Signal(
                signal_type="bb_squeeze_release",
                symbol=symbol, date=date, direction="bullish",
                value=float(prev_squeeze),
                description=f"BB squeeze release after {prev_squeeze}d compression",
            ))

    return signals


def _eval_sr_break(db, price_today, price_yesterday, ind_today, symbol, date):
    """Support break with volume — exit signal (bearish only)."""
    signals = []
    if not price_yesterday:
        return signals

    close_t = price_today["close"]
    close_y = price_yesterday["close"]
    vr = ind_today.get("volume_ratio") or 0

    levels = _load_sr_levels(db, symbol)
    if not levels:
        return signals

    for lv in levels:
        level = lv["level"]
        ltype = lv["level_type"]
        touches = lv["touch_count"]
        if touches < 2:
            continue

        if ltype == "support" and close_y >= level and close_t < level and vr >= 2.0:
            signals.append(Signal(
                signal_type="sr_break",
                symbol=symbol, date=date, direction="bearish",
                value=level,
                description=f"Broke support {level:,.0f} ({touches} touches) on {vr:.1f}x volume",
                meta={"touch_count": touches, "volume_ratio": round(vr, 2)},
            ))

    return signals


# ---------------------------------------------------------------------------
# Main evaluation
# ---------------------------------------------------------------------------

def evaluate_signals(db, symbol, date):
    """Evaluate all signal types for a symbol on a given date.

    Returns list of Signal objects that fired.
    """
    ind_today, ind_yesterday = _load_indicator_pair(db, symbol, date)
    price_today, price_yesterday = _load_price_pair(db, symbol, date)

    if not ind_today or not price_today:
        return []

    signals = []

    # Bandarmology (2)
    signals.extend(_eval_broker_significance(db, symbol, date, price_today))
    signals.extend(_eval_buyer_seller_imbalance(db, symbol, date, price_today, price_yesterday))

    # Technical (4 entry + 1 exit)
    signals.extend(_eval_ema_cross(ind_today, ind_yesterday, symbol, date))
    signals.extend(_eval_macd_histogram_flip(ind_today, ind_yesterday, symbol, date))
    signals.extend(_eval_volume_spike(ind_today, price_today, symbol, date))
    signals.extend(_eval_bb_squeeze_release(ind_today, ind_yesterday, price_today, symbol, date))
    signals.extend(_eval_sr_break(db, price_today, price_yesterday, ind_today, symbol, date))

    return signals


def evaluate_all(cfg, date=None, symbols=None):
    """Evaluate signals for all pool stocks on a given date.

    Returns dict of {symbol: [Signal, ...]}.
    """
    db = get_db(cfg)

    if date is None:
        row = db.execute("SELECT MAX(date) as d FROM prices").fetchone()
        date = row["d"] if row else None
        if not date:
            db.close()
            return {}

    if symbols is None:
        rows = db.execute("SELECT symbol FROM scan_pool ORDER BY rank").fetchall()
        symbols = [r["symbol"] for r in rows]
        if not symbols:
            symbols = [s.replace(".JK", "") for s in cfg["watchlist"]]

    results = {}
    for symbol in symbols:
        sigs = evaluate_signals(db, symbol, date)
        if sigs:
            results[symbol] = sigs

    db.close()
    return results


def log_signals(db, signals_by_symbol, regime=None):
    """Write fired signals to signal_events table.

    Forward return columns are left NULL — filled later by the forward return job.
    """
    for symbol, sigs in signals_by_symbol.items():
        for s in sigs:
            broker_code = s.meta.get("broker_code") if s.meta else None
            magnitude = s.value
            meta_json = json.dumps(s.meta) if s.meta else None

            price_row = db.execute(
                "SELECT close, volume FROM prices WHERE symbol = ? AND date = ?",
                (s.symbol, s.date),
            ).fetchone()
            close = price_row["close"] if price_row else None

            ind_row = db.execute(
                "SELECT volume_ratio FROM indicators WHERE symbol = ? AND date = ?",
                (s.symbol, s.date),
            ).fetchone()
            vol_ratio = ind_row["volume_ratio"] if ind_row else None

            db.execute(
                """INSERT OR IGNORE INTO signal_events
                   (symbol, date, signal_type, broker_code, magnitude,
                    close, volume_ratio, regime, trend, meta)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (s.symbol, s.date, s.signal_type, broker_code, magnitude,
                 close, vol_ratio, regime, s.direction, meta_json),
            )

    db.commit()
