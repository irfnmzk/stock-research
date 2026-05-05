"""US signal evaluation engine — state-change signals, bullish only."""

import json
from dataclasses import dataclass

from db import get_us_db


@dataclass
class Signal:
    signal_type: str
    ticker: str
    date: str
    direction: str
    magnitude: float | None = None
    description: str = ""
    meta: dict | None = None


def compute_relative_strength(db):
    """Compute RS vs SPY and vs sector ETF for all equities."""
    tickers = db.execute(
        "SELECT ticker, sector_etf FROM assets WHERE active = 1 AND quote_type = 'EQUITY'"
    ).fetchall()

    spy_prices = _price_series(db, "SPY")
    if not spy_prices:
        print("  No SPY prices — skipping RS computation")
        return

    for row in tickers:
        ticker = row["ticker"]
        sector_etf = row["sector_etf"]

        stock_prices = _price_series(db, ticker)
        if len(stock_prices) < 21:
            continue

        sector_prices = _price_series(db, sector_etf) if sector_etf else {}

        dates = sorted(stock_prices.keys())
        for date in dates[-60:]:
            rs_spy_10 = _rs(stock_prices, spy_prices, date, 10)
            rs_spy_20 = _rs(stock_prices, spy_prices, date, 20)
            rs_sec_10 = _rs(stock_prices, sector_prices, date, 10) if sector_prices else None
            rs_sec_20 = _rs(stock_prices, sector_prices, date, 20) if sector_prices else None

            if rs_spy_10 is None:
                continue

            db.execute(
                """INSERT OR REPLACE INTO relative_strength
                   (ticker, date, rs_vs_spy_10d, rs_vs_spy_20d, rs_vs_sector_10d, rs_vs_sector_20d)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (ticker, date, rs_spy_10, rs_spy_20, rs_sec_10, rs_sec_20),
            )

    db.commit()


def compute_sector_rotation(db):
    """Compute sector ETF rotation rankings."""
    etfs = ["QQQ", "XLF", "XLE", "XLY", "XLP", "XLV", "XLU", "XLB", "SPY"]
    today = None
    etf_data = []

    for etf in etfs:
        prices = _price_series(db, etf)
        if not prices:
            continue
        dates = sorted(prices.keys())
        if not dates:
            continue
        if today is None:
            today = dates[-1]

        latest = prices.get(today)
        if latest is None:
            continue

        pct_5 = _pct_change(prices, dates, today, 5)
        pct_10 = _pct_change(prices, dates, today, 10)
        pct_20 = _pct_change(prices, dates, today, 20)

        momentum = ((pct_5 or 0) * 2 + (pct_10 or 0) * 1.5 + (pct_20 or 0)) / 4.5
        etf_data.append((etf, pct_5, pct_10, pct_20, momentum))

    if not etf_data or not today:
        return

    etf_data.sort(key=lambda x: x[4], reverse=True)
    for rank, (etf, p5, p10, p20, mom) in enumerate(etf_data, 1):
        db.execute(
            """INSERT OR REPLACE INTO sector_rotation
               (sector_etf, date, pct_5d, pct_10d, pct_20d, momentum, rank)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (etf, today, p5, p10, p20, round(mom, 4), rank),
        )
    db.commit()


def _price_series(db, ticker: str) -> dict[str, float]:
    rows = db.execute(
        "SELECT date, close FROM prices WHERE ticker = ? ORDER BY date",
        (ticker,),
    ).fetchall()
    return {r["date"]: r["close"] for r in rows}


def _rs(stock_prices: dict, bench_prices: dict, date: str, window: int) -> float | None:
    dates = sorted(stock_prices.keys())
    try:
        idx = dates.index(date)
    except ValueError:
        return None
    if idx < window:
        return None

    past_date = dates[idx - window]
    stock_now = stock_prices.get(date)
    stock_past = stock_prices.get(past_date)
    bench_now = bench_prices.get(date)
    bench_past = bench_prices.get(past_date)

    if not all([stock_now, stock_past, bench_now, bench_past]):
        return None
    if stock_past == 0 or bench_past == 0:
        return None

    stock_ret = (stock_now - stock_past) / stock_past * 100
    bench_ret = (bench_now - bench_past) / bench_past * 100
    return round(stock_ret - bench_ret, 4)


def _pct_change(prices: dict, dates: list, today: str, window: int) -> float | None:
    try:
        idx = dates.index(today)
    except ValueError:
        return None
    if idx < window:
        return None
    past = prices.get(dates[idx - window])
    now = prices.get(today)
    if not past or not now or past == 0:
        return None
    return round((now - past) / past * 100, 4)


def _load_indicator_pair(db, ticker: str, date: str):
    rows = db.execute(
        "SELECT * FROM indicators WHERE ticker = ? AND date <= ? ORDER BY date DESC LIMIT 2",
        (ticker, date),
    ).fetchall()
    if not rows or rows[0]["date"] != date:
        return None, None
    today = dict(rows[0])
    yesterday = dict(rows[1]) if len(rows) > 1 else None
    return today, yesterday


def _load_rs_pair(db, ticker: str, date: str):
    rows = db.execute(
        "SELECT * FROM relative_strength WHERE ticker = ? AND date <= ? ORDER BY date DESC LIMIT 2",
        (ticker, date),
    ).fetchall()
    if not rows or rows[0]["date"] != date:
        return None, None
    today = dict(rows[0])
    yesterday = dict(rows[1]) if len(rows) > 1 else None
    return today, yesterday


def _eval_ema_cross(ind_today, ind_yesterday, ticker, date) -> list[Signal]:
    if not ind_yesterday:
        return []
    e10_t, e21_t = ind_today.get("ema10"), ind_today.get("ema21")
    e10_y, e21_y = ind_yesterday.get("ema10"), ind_yesterday.get("ema21")
    if None in (e10_t, e21_t, e10_y, e21_y):
        return []
    if e10_y <= e21_y and e10_t > e21_t:
        return [Signal(
            signal_type="ema_cross", ticker=ticker, date=date, direction="bullish",
            magnitude=round(e10_t - e21_t, 4),
            description="EMA10 crossed above EMA21",
        )]
    return []


def _eval_macd_flip(ind_today, ind_yesterday, ticker, date) -> list[Signal]:
    if not ind_yesterday:
        return []
    hist_t = ind_today.get("macd_hist")
    hist_y = ind_yesterday.get("macd_hist")
    if hist_t is None or hist_y is None:
        return []
    if hist_y < 0 and hist_t >= 0:
        return [Signal(
            signal_type="macd_flip", ticker=ticker, date=date, direction="bullish",
            magnitude=round(hist_t, 4),
            description="MACD histogram flipped positive",
        )]
    return []


def _eval_bb_squeeze(ind_today, ind_yesterday, ticker, date) -> list[Signal]:
    if not ind_yesterday:
        return []
    bw_t = ind_today.get("bb_width")
    bw_y = ind_yesterday.get("bb_width")
    if bw_t is None or bw_y is None:
        return []

    bb_upper = ind_today.get("bb_upper")
    bb_lower = ind_today.get("bb_lower")
    if not bb_upper or not bb_lower:
        return []

    if bw_t > bw_y and bw_y < 0.04:
        mid = (bb_upper + bb_lower) / 2
        ema10 = ind_today.get("ema10")
        if ema10 and ema10 > mid:
            return [Signal(
                signal_type="bb_squeeze", ticker=ticker, date=date, direction="bullish",
                magnitude=round(bw_t, 4),
                description=f"BB squeeze release (width {bw_y:.3f} -> {bw_t:.3f})",
            )]
    return []


def _eval_sr_break(db, ind_today, ind_yesterday, ticker, date) -> list[Signal]:
    if not ind_yesterday:
        return []
    close_t = ind_today.get("ema10")
    close_y = ind_yesterday.get("ema10")
    if not close_t or not close_y:
        return []

    levels = db.execute(
        "SELECT level, touch_count FROM support_resistance WHERE ticker = ? AND level_type = 'resistance'",
        (ticker,),
    ).fetchall()

    signals = []
    for lv in levels:
        level = lv["level"]
        touches = lv["touch_count"]
        if touches < 2:
            continue
        if close_y <= level and close_t > level:
            signals.append(Signal(
                signal_type="sr_break", ticker=ticker, date=date, direction="bullish",
                magnitude=level,
                description=f"Broke resistance {level:.2f} ({touches} touches)",
                meta={"level": level, "touch_count": touches},
            ))
    return signals


def _eval_rs_breakout_spy(rs_today, rs_yesterday, ticker, date) -> list[Signal]:
    if not rs_yesterday:
        return []
    rs_t = rs_today.get("rs_vs_spy_10d")
    rs_y = rs_yesterday.get("rs_vs_spy_10d")
    if rs_t is None or rs_y is None:
        return []
    if rs_y <= 0 and rs_t > 0:
        return [Signal(
            signal_type="rs_breakout_spy", ticker=ticker, date=date, direction="bullish",
            magnitude=round(rs_t, 4),
            description=f"RS vs SPY turned positive ({rs_t:+.1f}%)",
        )]
    return []


def _eval_rs_breakout_sector(rs_today, rs_yesterday, ticker, date) -> list[Signal]:
    if not rs_yesterday:
        return []
    rs_t = rs_today.get("rs_vs_sector_10d")
    rs_y = rs_yesterday.get("rs_vs_sector_10d")
    if rs_t is None or rs_y is None:
        return []
    if rs_y <= 0 and rs_t > 0:
        return [Signal(
            signal_type="rs_breakout_sector", ticker=ticker, date=date, direction="bullish",
            magnitude=round(rs_t, 4),
            description=f"RS vs sector turned positive ({rs_t:+.1f}%)",
        )]
    return []


def _eval_sector_momentum(db, ticker, date) -> list[Signal]:
    row = db.execute(
        "SELECT sector_etf FROM assets WHERE ticker = ?", (ticker,)
    ).fetchone()
    if not row or not row["sector_etf"]:
        return []

    sector_etf = row["sector_etf"]
    rot = db.execute(
        "SELECT rank FROM sector_rotation WHERE sector_etf = ? AND date = ?",
        (sector_etf, date),
    ).fetchone()
    if not rot:
        return []

    prev_rot = db.execute(
        "SELECT rank FROM sector_rotation WHERE sector_etf = ? AND date < ? ORDER BY date DESC LIMIT 1",
        (sector_etf, date),
    ).fetchone()

    if rot["rank"] <= 3 and (not prev_rot or prev_rot["rank"] > 3):
        return [Signal(
            signal_type="sector_momentum", ticker=ticker, date=date, direction="bullish",
            magnitude=float(rot["rank"]),
            description=f"Sector {sector_etf} entered top 3 (rank {rot['rank']})",
        )]
    return []


def evaluate_signals(db, ticker: str, date: str) -> list[Signal]:
    """Evaluate all signal types for a US ticker on a given date."""
    ind_today, ind_yesterday = _load_indicator_pair(db, ticker, date)
    rs_today, rs_yesterday = _load_rs_pair(db, ticker, date)

    if not ind_today:
        return []

    signals = []

    signals.extend(_eval_ema_cross(ind_today, ind_yesterday, ticker, date))
    signals.extend(_eval_macd_flip(ind_today, ind_yesterday, ticker, date))
    signals.extend(_eval_bb_squeeze(ind_today, ind_yesterday, ticker, date))
    signals.extend(_eval_sr_break(db, ind_today, ind_yesterday, ticker, date))

    if rs_today:
        signals.extend(_eval_rs_breakout_spy(rs_today, rs_yesterday, ticker, date))
        signals.extend(_eval_rs_breakout_sector(rs_today, rs_yesterday, ticker, date))
    signals.extend(_eval_sector_momentum(db, ticker, date))

    return signals


def evaluate_all(date: str | None = None, tickers: list[str] | None = None) -> dict[str, list[Signal]]:
    """Evaluate signals for all active US equities."""
    db = get_us_db()

    if date is None:
        row = db.execute("SELECT MAX(date) as d FROM prices").fetchone()
        date = row["d"] if row else None
        if not date:
            db.close()
            return {}

    if tickers is None:
        rows = db.execute(
            "SELECT ticker FROM assets WHERE active = 1 AND quote_type = 'EQUITY'"
        ).fetchall()
        tickers = [r["ticker"] for r in rows]

    results = {}
    for ticker in tickers:
        sigs = evaluate_signals(db, ticker, date)
        if sigs:
            results[ticker] = sigs

    db.close()
    return results


def log_signals(db, signals_by_ticker: dict[str, list[Signal]]):
    """Write fired signals to signal_events table."""
    for ticker, sigs in signals_by_ticker.items():
        for s in sigs:
            meta_json = json.dumps(s.meta) if s.meta else None
            db.execute(
                """INSERT OR IGNORE INTO signal_events
                   (ticker, date, signal_type, direction, magnitude, close, meta)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (s.ticker, s.date, s.signal_type, s.direction, s.magnitude, None, meta_json),
            )
    db.commit()
