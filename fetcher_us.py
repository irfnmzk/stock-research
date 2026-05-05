"""US stock fetch orchestration — Pluang prices, yfinance sector seed."""

import json
import time
from datetime import datetime, timedelta
from pathlib import Path

from db import get_us_db
from pluang import PluangClient

REFERENCE_FILE = Path(__file__).parent / "references" / "pluiang-stock.json"

SECTOR_ETF_MAP = {
    "Technology": "QQQ",
    "Financial Services": "XLF",
    "Energy": "XLE",
    "Consumer Cyclical": "XLY",
    "Consumer Defensive": "XLP",
    "Healthcare": "XLV",
    "Utilities": "XLU",
    "Basic Materials": "XLB",
    "Communication Services": "QQQ",
    "Industrials": "SPY",
    "Real Estate": "SPY",
}


def seed_assets():
    """Parse pluiang-stock.json and populate assets table."""
    db = get_us_db()
    data = json.loads(REFERENCE_FILE.read_text())
    stocks = data["props"]["pageProps"]["tradingInfo"]["data"]["palnStockListData"]

    count = 0
    for s in stocks:
        db.execute(
            """INSERT OR REPLACE INTO assets (pluang_id, ticker, name, active)
               VALUES (?, ?, ?, ?)""",
            (s["id"], s["cc"], s["na"], 1 if s.get("ioa") else 0),
        )
        count += 1
    db.commit()
    print(f"Seeded {count} assets ({sum(1 for s in stocks if s.get('ioa'))} active)")
    db.close()


def seed_sectors(batch_size: int = 50):
    """Fetch sector/industry/quoteType from yfinance for all equities."""
    import yfinance as yf

    db = get_us_db()
    rows = db.execute(
        "SELECT ticker FROM assets WHERE active = 1 AND sector IS NULL"
    ).fetchall()
    tickers = [r["ticker"] for r in rows]

    if not tickers:
        print("All active assets already have sector data.")
        db.close()
        return

    print(f"Fetching sector data for {len(tickers)} tickers...")
    for i in range(0, len(tickers), batch_size):
        batch = tickers[i:i + batch_size]
        print(f"  Batch {i // batch_size + 1}: {batch[0]}..{batch[-1]}")

        for ticker in batch:
            try:
                info = yf.Ticker(ticker).info
                sector = info.get("sector")
                industry = info.get("industry")
                quote_type = info.get("quoteType", "EQUITY")
                sector_etf = SECTOR_ETF_MAP.get(sector, "SPY") if sector else None
                market_cap = info.get("marketCap")

                db.execute(
                    """UPDATE assets
                       SET sector = ?, industry = ?, quote_type = ?, sector_etf = ?, market_cap = ?
                       WHERE ticker = ?""",
                    (sector, industry, quote_type, sector_etf, market_cap, ticker),
                )
            except Exception as e:
                print(f"    {ticker}: {e}")
            time.sleep(0.2)

        db.commit()

    print("Sector seed complete.")
    db.close()


def fetch_prices(tickers: list[str] | None = None, days: int = 365):
    """Fetch OHLC for all active assets from Pluang."""
    db = get_us_db()
    client = PluangClient()

    if tickers:
        rows = db.execute(
            f"SELECT pluang_id, ticker FROM assets WHERE ticker IN ({','.join('?' * len(tickers))})",
            tickers,
        ).fetchall()
    else:
        rows = db.execute(
            "SELECT pluang_id, ticker FROM assets WHERE active = 1"
        ).fetchall()

    end_date = datetime.now().strftime("%Y-%m-%d")
    start_date = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")

    print(f"Fetching prices for {len(rows)} assets ({start_date} to {end_date})...")
    for i, row in enumerate(rows, 1):
        if i % 50 == 0:
            print(f"  Progress: {i}/{len(rows)}")
        try:
            candles = client.fetch_ohlc(row["pluang_id"], start_date, end_date)
        except Exception as e:
            print(f"  {row['ticker']}: {e}")
            continue

        if not candles:
            continue

        price_rows = [
            (row["ticker"], c["date"], c["open"], c["high"], c["low"], c["close"])
            for c in candles if c["date"]
        ]
        db.executemany(
            """INSERT OR REPLACE INTO prices (ticker, date, open, high, low, close)
               VALUES (?, ?, ?, ?, ?, ?)""",
            price_rows,
        )

        if i % 50 == 0:
            db.commit()

    db.commit()
    client.close()
    print("Price fetch complete.")
    db.close()


def fetch_prices_single(ticker: str, days: int = 365):
    """Fetch OHLC for a single ticker."""
    fetch_prices(tickers=[ticker], days=days)


def run_pipeline(days: int = 365):
    """Run the full US daily pipeline."""
    from indicators_us import compute_all as compute_indicators
    from support_resistance_us import detect_all as compute_sr
    from signal_engine_us import (
        compute_relative_strength, compute_sector_rotation,
        evaluate_all, log_signals, get_us_db,
    )

    print("=== US Pipeline ===")

    print("\n--- Fetching prices ---")
    fetch_prices(days=days)

    print("\n--- Computing indicators ---")
    compute_indicators()

    print("\n--- Computing S/R ---")
    compute_sr()

    print("\n--- Computing relative strength ---")
    db = get_us_db()
    compute_relative_strength(db)
    print("  RS computed")

    print("\n--- Computing sector rotation ---")
    compute_sector_rotation(db)
    print("  Sector rotation computed")

    print("\n--- Evaluating signals ---")
    signals = evaluate_all()
    log_signals(db, signals)
    print(f"  {sum(len(v) for v in signals.values())} signals fired across {len(signals)} tickers")

    db.close()
    print("\n=== US Pipeline complete ===")
