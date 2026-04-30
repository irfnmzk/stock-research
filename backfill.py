"""Backfill historical data for prices and broker summary.

Usage via CLI:
    main.py backfill --from 2020-01-01 [--prices] [--brokers] [--batch-size 10] [--delay 0.5]

Prices: fast, one API call per stock covers the full date range.
Brokers: slow, one API call per stock per trading day. Resumable.

Progress is logged to data/backfill_progress.json so the job can be
stopped and restarted without re-fetching completed dates.
"""

import json
import logging
import time
from datetime import datetime, timedelta
from pathlib import Path

from db import get_db
from stockbit import StockbitClient

log = logging.getLogger("backfill")

SCRIPT_DIR = Path(__file__).resolve().parent
PROGRESS_FILE = SCRIPT_DIR / "data" / "backfill_progress.json"


def _pool_symbols(cfg):
    db = get_db(cfg)
    rows = db.execute("SELECT symbol FROM scan_pool ORDER BY rank").fetchall()
    db.close()
    return [r["symbol"] for r in rows]


def _trading_days(start_date: str, end_date: str) -> list[str]:
    """Generate weekday dates between start and end (inclusive).
    Actual holidays will return empty data from the API and get skipped."""
    days = []
    d = datetime.strptime(start_date, "%Y-%m-%d")
    end = datetime.strptime(end_date, "%Y-%m-%d")
    while d <= end:
        if d.weekday() < 5:  # Mon-Fri
            days.append(d.strftime("%Y-%m-%d"))
        d += timedelta(days=1)
    return days


def _load_progress() -> dict:
    if PROGRESS_FILE.exists():
        return json.loads(PROGRESS_FILE.read_text())
    return {"completed_dates": [], "failed_dates": [], "last_updated": None}


def _save_progress(progress: dict):
    progress["last_updated"] = datetime.now().isoformat()
    PROGRESS_FILE.parent.mkdir(parents=True, exist_ok=True)
    PROGRESS_FILE.write_text(json.dumps(progress, indent=2))


def backfill_prices(cfg, start_date: str, end_date: str = None, symbols: list[str] = None):
    """Backfill price/candle data. One call per stock covers the full range."""
    sb = StockbitClient()
    db = get_db(cfg)
    syms = symbols or _pool_symbols(cfg)
    end = end_date or datetime.now().strftime("%Y-%m-%d")

    log.info("Backfilling prices for %d stocks from %s to %s", len(syms), start_date, end)

    success = 0
    errors = 0
    for i, symbol in enumerate(syms, 1):
        try:
            resp = sb.daily_prices(symbol, start_date, end)
        except Exception as e:
            log.warning("[%d/%d] %s price error: %s", i, len(syms), symbol, e)
            errors += 1
            continue

        data = resp.get("data", resp)
        candles = data.get("chartbit", []) if data else []
        if not candles:
            log.debug("[%d/%d] %s: no candle data", i, len(syms), symbol)
            continue

        rows = []
        for c in candles:
            dt = c.get("date", "")
            if isinstance(dt, (int, float)):
                dt = datetime.fromtimestamp(dt).strftime("%Y-%m-%d")
            elif "T" in str(dt):
                dt = str(dt)[:10]

            rows.append((
                symbol, dt,
                c.get("open"), c.get("high"), c.get("low"), c.get("close"),
                c.get("volume"), c.get("value"), c.get("frequency"),
                c.get("foreignbuy"), c.get("foreignsell"),
                None, c.get("shareoutstanding"), c.get("freq_analyzer"),
            ))

        db.executemany(
            """INSERT OR REPLACE INTO prices
               (symbol, date, open, high, low, close, volume, value, frequency,
                foreign_buy, foreign_sell, market_cap, shares_outstanding, freq_analyzer)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            rows,
        )
        db.commit()
        success += 1

        if i % 50 == 0:
            log.info("  Progress: %d/%d stocks (%d ok, %d errors)", i, len(syms), success, errors)

    log.info("Price backfill complete: %d ok, %d errors", success, errors)
    sb.close()
    db.close()


def backfill_brokers(cfg, start_date: str, end_date: str = None, symbols: list[str] = None,
                     batch_pause: float = 0.5, max_retries: int = 3):
    """Backfill broker summary data day by day. Resumable via progress file.

    For each trading day, fetches broker data for all pool stocks.
    Skips dates already completed. Saves progress after each date.
    """
    sb = StockbitClient()
    db = get_db(cfg)
    syms = symbols or _pool_symbols(cfg)
    end = end_date or datetime.now().strftime("%Y-%m-%d")

    all_days = _trading_days(start_date, end)
    progress = _load_progress()
    completed = set(progress["completed_dates"])
    remaining = [d for d in all_days if d not in completed]

    log.info(
        "Broker backfill: %d stocks, %d total days, %d already done, %d remaining",
        len(syms), len(all_days), len(completed), len(remaining),
    )

    for di, date in enumerate(remaining, 1):
        day_start = time.time()
        stored = 0
        empty = 0
        errors = 0

        for symbol in syms:
            retries = 0
            while retries <= max_retries:
                try:
                    resp = sb.market_detectors_date(symbol, date)
                    break
                except Exception as e:
                    err_str = str(e)
                    if "429" in err_str or "rate" in err_str.lower():
                        retries += 1
                        wait = 2 ** retries
                        log.warning("Rate limited on %s %s, waiting %ds (retry %d/%d)",
                                    symbol, date, wait, retries, max_retries)
                        time.sleep(wait)
                    else:
                        log.debug("%s %s error: %s", symbol, date, e)
                        errors += 1
                        resp = None
                        break
            else:
                log.warning("Max retries exceeded for %s %s", symbol, date)
                errors += 1
                continue

            if resp is None:
                continue

            data = resp.get("data", resp)
            if not data:
                empty += 1
                continue

            # Parse broker summary
            bs = data.get("broker_summary", {})
            buys = {b["netbs_broker_code"]: b for b in bs.get("brokers_buy", [])}
            sells = {b["netbs_broker_code"]: b for b in bs.get("brokers_sell", [])}
            all_codes = set(buys) | set(sells)

            if not all_codes:
                empty += 1
                continue

            broker_rows = []
            for code in all_codes:
                buy = buys.get(code, {})
                sell = sells.get(code, {})
                buy_lot = int(float(buy.get("blot", 0)))
                buy_val = float(buy.get("bval", 0))
                sell_lot = abs(int(float(sell.get("slot", 0))))
                sell_val = abs(float(sell.get("sval", 0)))
                net_lot = buy_lot - sell_lot
                net_val = buy_val - sell_val
                btype = buy.get("type") or sell.get("type", "")
                avg_price = float(buy.get("netbs_buy_avg_price", 0) or
                                  sell.get("netbs_sell_avg_price", 0) or 0)
                freq = int(buy.get("freq", 0) or 0) + int(sell.get("freq", 0) or 0)

                broker_rows.append((
                    symbol, date, code, btype,
                    buy_lot, buy_val, sell_lot, sell_val,
                    net_lot, net_val, avg_price, freq,
                ))

            db.executemany(
                """INSERT OR REPLACE INTO broker_summary
                   (symbol, date, broker_code, broker_type, buy_lot, buy_value,
                    sell_lot, sell_value, net_lot, net_value, avg_price, freq)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                broker_rows,
            )
            stored += 1

            # Also store bandar detector
            bd = data.get("bandar_detector", {})
            if bd:
                db.execute(
                    """INSERT OR REPLACE INTO bandar_detector
                       (symbol, date, top1_net, top3_net, top5_net, top10_net,
                        top1_accdist, top3_accdist, top5_accdist, top10_accdist,
                        total_buyers, total_sellers, total_value)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        symbol, date,
                        bd.get("top1", {}).get("amount"),
                        bd.get("top3", {}).get("amount"),
                        bd.get("top5", {}).get("amount"),
                        bd.get("top10", {}).get("amount"),
                        bd.get("top1", {}).get("accdist"),
                        bd.get("top3", {}).get("accdist"),
                        bd.get("top5", {}).get("accdist"),
                        bd.get("top10", {}).get("accdist"),
                        bd.get("total_buyer"),
                        bd.get("total_seller"),
                        bd.get("value"),
                    ),
                )

        db.commit()

        # Mark date as completed
        progress["completed_dates"].append(date)
        if errors > len(syms) * 0.5:
            progress["failed_dates"].append(date)
        _save_progress(progress)

        elapsed = time.time() - day_start
        log.info(
            "[%d/%d] %s: %d stored, %d empty, %d errors (%.1fs)",
            di, len(remaining), date, stored, empty, errors, elapsed,
        )

        # Pause between dates
        if batch_pause > 0 and di < len(remaining):
            time.sleep(batch_pause)

    log.info("Broker backfill complete. Total dates: %d", len(progress["completed_dates"]))
    sb.close()
    db.close()
