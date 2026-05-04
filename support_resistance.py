"""Support/resistance detection and breakout logic."""

import numpy as np
import pandas as pd
from scipy.signal import argrelextrema

from db import get_db


def _load_prices(db, symbol):
    rows = db.execute(
        "SELECT date, high, low, close, volume FROM prices "
        "WHERE symbol = ? ORDER BY date",
        (symbol,),
    ).fetchall()
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows, columns=["date", "high", "low", "close", "volume"])
    df["date"] = pd.to_datetime(df["date"])
    df.set_index("date", inplace=True)
    return df


def _cluster_levels(levels, pct):
    """Merge nearby levels within pct tolerance. Returns (level, count) pairs."""
    if not levels:
        return []
    levels = sorted(levels)
    clusters = []
    current = [levels[0]]
    for lv in levels[1:]:
        if (lv - current[0]) / current[0] <= pct:
            current.append(lv)
        else:
            clusters.append((round(np.mean(current), 2), len(current)))
            current = [lv]
    clusters.append((round(np.mean(current), 2), len(current)))
    return clusters


def detect(cfg, db, symbol):
    """Detect S/R levels for a symbol and store in DB."""
    df = _load_prices(db, symbol)
    if len(df) < 20:
        print(f"  Not enough data for {symbol}")
        return

    sr_cfg = cfg["support_resistance"]
    window = sr_cfg["window"]
    cluster_pct = sr_cfg["cluster_pct"]
    min_touches = sr_cfg["min_touches"]

    high_idx = argrelextrema(df["high"].values, np.greater_equal, order=window)[0]
    low_idx = argrelextrema(df["low"].values, np.less_equal, order=window)[0]

    resistance_levels = df["high"].iloc[high_idx].tolist()
    support_levels = df["low"].iloc[low_idx].tolist()

    resistances = _cluster_levels(resistance_levels, cluster_pct)
    supports = _cluster_levels(support_levels, cluster_pct)

    db.execute("DELETE FROM support_resistance WHERE symbol = ?", (symbol,))

    rows = []
    for level, count in resistances:
        if count >= min_touches:
            rows.append((symbol, level, "resistance", count, df.index[-1].strftime("%Y-%m-%d"), count * 1.0))
    for level, count in supports:
        if count >= min_touches:
            rows.append((symbol, level, "support", count, df.index[-1].strftime("%Y-%m-%d"), count * 1.0))

    if rows:
        db.executemany(
            """INSERT OR REPLACE INTO support_resistance
               (symbol, level, level_type, touch_count, last_touched, strength_score)
               VALUES (?, ?, ?, ?, ?, ?)""",
            rows,
        )
    db.commit()
    print(f"  Found {len(rows)} S/R levels for {symbol}")


def detect_all(cfg, symbols=None):
    """Detect S/R for all watchlist symbols."""
    db = get_db(cfg)
    if symbols:
        syms = symbols
    else:
        from db import get_watchlist
        syms = get_watchlist(cfg)
    for s in syms:
        symbol = s.replace(".JK", "")
        print(f"Detecting S/R for {symbol}...")
        detect(cfg, db, symbol)
    db.close()
