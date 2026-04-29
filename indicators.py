"""Technical indicators computation."""

import pandas as pd
import pandas_ta as ta

from db import get_db


def _resolve_symbols(cfg, symbols=None):
    syms = symbols or cfg["watchlist"]
    return [s.replace(".JK", "") for s in syms]


def _load_prices(db, symbol):
    """Load daily prices as a DataFrame."""
    rows = db.execute(
        "SELECT date, open, high, low, close, volume FROM prices "
        "WHERE symbol = ? ORDER BY date",
        (symbol,),
    ).fetchall()
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows, columns=["date", "open", "high", "low", "close", "volume"])
    df["date"] = pd.to_datetime(df["date"])
    df.set_index("date", inplace=True)
    return df


def compute(cfg, db, symbol):
    """Compute all indicators for a symbol and store in DB."""
    df = _load_prices(db, symbol)
    if df.empty:
        print(f"  No price data for {symbol}")
        return

    ic = cfg["indicators"]

    # EMAs
    for p in ic["ema_periods"]:
        df[f"ema{p}"] = ta.ema(df["close"], length=p)

    # RSI
    df["rsi"] = ta.rsi(df["close"], length=ic["rsi_period"])

    # MACD
    macd = ta.macd(df["close"], fast=ic["macd_fast"], slow=ic["macd_slow"], signal=ic["macd_signal"])
    if macd is not None:
        df["macd"] = macd.iloc[:, 0]
        df["macd_signal"] = macd.iloc[:, 2]
        df["macd_hist"] = macd.iloc[:, 1]

    # Bollinger Bands
    bb = ta.bbands(df["close"], length=ic["bb_period"], std=ic["bb_std"])
    if bb is not None:
        df["bb_upper"] = bb.iloc[:, 2]
        df["bb_lower"] = bb.iloc[:, 0]
        df["bb_width"] = (df["bb_upper"] - df["bb_lower"]) / df["close"]

    # ATR
    df["atr"] = ta.atr(df["high"], df["low"], df["close"], length=ic["atr_period"])

    # Volume ratio
    df["volume_ratio"] = df["volume"] / df["volume"].rolling(ic["volume_avg_period"]).mean()

    # Store
    rows = []
    for date, row in df.iterrows():
        rows.append((
            symbol,
            date.strftime("%Y-%m-%d"),
            _g(row, "ema20"), _g(row, "ema50"), _g(row, "ema200"),
            _g(row, "rsi"),
            _g(row, "macd"), _g(row, "macd_signal"), _g(row, "macd_hist"),
            _g(row, "bb_upper"), _g(row, "bb_lower"), _g(row, "bb_width"),
            _g(row, "atr"),
            _g(row, "volume_ratio"),
        ))

    db.executemany(
        """INSERT OR REPLACE INTO indicators
           (symbol, date, ema20, ema50, ema200, rsi, macd, macd_signal, macd_hist,
            bb_upper, bb_lower, bb_width, atr, volume_ratio)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        rows,
    )
    db.commit()
    print(f"  Stored {len(rows)} indicator rows for {symbol}")


def _g(row, col):
    """Get value or None if NaN."""
    v = row.get(col)
    if v is None or pd.isna(v):
        return None
    return round(float(v), 4)


def compute_all(cfg, symbols=None):
    """Compute indicators for all watchlist symbols."""
    db = get_db(cfg)
    for symbol in _resolve_symbols(cfg, symbols):
        print(f"Computing indicators for {symbol}...")
        compute(cfg, db, symbol)
    db.close()
