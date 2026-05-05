"""US stock support/resistance detection from OHLC."""

import numpy as np
import pandas as pd
from scipy.signal import argrelextrema

from db import get_us_db


def _load_prices(db, ticker: str) -> pd.DataFrame:
    rows = db.execute(
        "SELECT date, high, low, close FROM prices WHERE ticker = ? ORDER BY date",
        (ticker,),
    ).fetchall()
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows, columns=["date", "high", "low", "close"])
    df["date"] = pd.to_datetime(df["date"])
    df.set_index("date", inplace=True)
    return df


def _cluster_levels(levels: list[float], pct: float) -> list[tuple[float, int]]:
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


def detect(db, ticker: str, window: int = 5, cluster_pct: float = 0.02, min_touches: int = 2):
    """Detect S/R levels for a US ticker and store in DB."""
    df = _load_prices(db, ticker)
    if len(df) < 20:
        return

    high_idx = argrelextrema(df["high"].values, np.greater_equal, order=window)[0]
    low_idx = argrelextrema(df["low"].values, np.less_equal, order=window)[0]

    resistance_levels = df["high"].iloc[high_idx].tolist()
    support_levels = df["low"].iloc[low_idx].tolist()

    resistances = _cluster_levels(resistance_levels, cluster_pct)
    supports = _cluster_levels(support_levels, cluster_pct)

    db.execute("DELETE FROM support_resistance WHERE ticker = ?", (ticker,))

    rows = []
    last_date = df.index[-1].strftime("%Y-%m-%d")
    for level, count in resistances:
        if count >= min_touches:
            rows.append((ticker, level, "resistance", count, last_date, float(count)))
    for level, count in supports:
        if count >= min_touches:
            rows.append((ticker, level, "support", count, last_date, float(count)))

    if rows:
        db.executemany(
            """INSERT OR REPLACE INTO support_resistance
               (ticker, level, level_type, touch_count, last_touched, strength_score)
               VALUES (?, ?, ?, ?, ?, ?)""",
            rows,
        )
    db.commit()


def detect_all(tickers: list[str] | None = None):
    """Detect S/R for all active US equities."""
    db = get_us_db()
    if tickers is None:
        rows = db.execute(
            "SELECT ticker FROM assets WHERE active = 1 AND quote_type = 'EQUITY'"
        ).fetchall()
        tickers = [r["ticker"] for r in rows]

    print(f"Detecting US S/R for {len(tickers)} tickers...")
    for i, ticker in enumerate(tickers, 1):
        detect(db, ticker)
        if i % 100 == 0:
            print(f"  Progress: {i}/{len(tickers)}")

    print("US S/R detection complete.")
    db.close()
