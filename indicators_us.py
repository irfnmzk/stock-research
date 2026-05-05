"""US stock technical indicators computation."""

import pandas as pd
import pandas_ta as ta

from db import get_us_db


def _load_prices(db, ticker: str) -> pd.DataFrame:
    rows = db.execute(
        "SELECT date, open, high, low, close FROM prices WHERE ticker = ? ORDER BY date",
        (ticker,),
    ).fetchall()
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows, columns=["date", "open", "high", "low", "close"])
    df["date"] = pd.to_datetime(df["date"])
    df.set_index("date", inplace=True)
    return df


def _g(row, col):
    v = row.get(col)
    if v is None or pd.isna(v):
        return None
    return round(float(v), 4)


def compute(db, ticker: str):
    """Compute all indicators for a US ticker and store in DB."""
    df = _load_prices(db, ticker)
    if len(df) < 50:
        return

    df["ema10"] = ta.ema(df["close"], length=10)
    df["ema21"] = ta.ema(df["close"], length=21)
    df["ema50"] = ta.ema(df["close"], length=50)
    df["ema200"] = ta.ema(df["close"], length=200)

    df["rsi"] = ta.rsi(df["close"], length=14)

    macd = ta.macd(df["close"], fast=12, slow=26, signal=9)
    if macd is not None:
        df["macd"] = macd.iloc[:, 0]
        df["macd_signal"] = macd.iloc[:, 2]
        df["macd_hist"] = macd.iloc[:, 1]

    bb = ta.bbands(df["close"], length=20, std=2)
    if bb is not None:
        df["bb_upper"] = bb.iloc[:, 2]
        df["bb_lower"] = bb.iloc[:, 0]
        df["bb_width"] = (df["bb_upper"] - df["bb_lower"]) / df["close"]

    df["atr"] = ta.atr(df["high"], df["low"], df["close"], length=14)
    df["adr_pct"] = df["atr"] / df["close"] * 100

    rows = []
    for date, row in df.iterrows():
        rows.append((
            ticker,
            date.strftime("%Y-%m-%d"),
            _g(row, "ema10"), _g(row, "ema21"), _g(row, "ema50"), _g(row, "ema200"),
            _g(row, "rsi"),
            _g(row, "macd"), _g(row, "macd_signal"), _g(row, "macd_hist"),
            _g(row, "bb_upper"), _g(row, "bb_lower"), _g(row, "bb_width"),
            _g(row, "atr"), _g(row, "adr_pct"),
        ))

    db.executemany(
        """INSERT OR REPLACE INTO indicators
           (ticker, date, ema10, ema21, ema50, ema200, rsi,
            macd, macd_signal, macd_hist, bb_upper, bb_lower, bb_width, atr, adr_pct)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        rows,
    )
    db.commit()


def compute_all(tickers: list[str] | None = None):
    """Compute indicators for all active US equities."""
    db = get_us_db()
    if tickers is None:
        rows = db.execute(
            "SELECT ticker FROM assets WHERE active = 1 AND quote_type = 'EQUITY'"
        ).fetchall()
        tickers = [r["ticker"] for r in rows]

    print(f"Computing US indicators for {len(tickers)} tickers...")
    for i, ticker in enumerate(tickers, 1):
        compute(db, ticker)
        if i % 100 == 0:
            print(f"  Progress: {i}/{len(tickers)}")

    print("US indicators complete.")
    db.close()
