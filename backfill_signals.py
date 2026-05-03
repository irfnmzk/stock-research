#!/usr/bin/env python3
"""Backfill signal history — replay historical data through the signal engine.

One-time script that processes all trading days, computes temporal fields,
evaluates signals, logs them to signal_events, and fills forward returns.

Resumable: tracks progress via last processed date in signal_events.

Usage:
    python backfill_signals.py [--config config.yaml] [--start 2020-01-02] [--end 2026-04-30]
"""

import argparse
import sys
import time
from pathlib import Path

import yaml

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

from db import get_db
from temporal import (
    _smart_broker_streak,
    _bb_squeeze_days,
    _foreign_flow_reversal,
    _accdist_slopes,
)
from signal_engine import evaluate_signals
import json


def _get_trading_dates(db, start=None, end=None):
    """Get all unique trading dates from prices table."""
    query = "SELECT DISTINCT date FROM prices"
    params = []
    clauses = []
    if start:
        clauses.append("date >= ?")
        params.append(start)
    if end:
        clauses.append("date <= ?")
        params.append(end)
    if clauses:
        query += " WHERE " + " AND ".join(clauses)
    query += " ORDER BY date"
    return [r["date"] for r in db.execute(query, params).fetchall()]


def _get_symbols_for_date(db, date):
    """Get symbols that have both price and indicator data on a given date."""
    rows = db.execute(
        """SELECT p.symbol FROM prices p
           JOIN indicators i ON p.symbol = i.symbol AND p.date = i.date
           WHERE p.date = ?""",
        (date,),
    ).fetchall()
    return [r["symbol"] for r in rows]


def _compute_temporal_for_date(db, symbol, date):
    """Compute temporal fields for a specific symbol and date."""
    streak = _smart_broker_streak(db, symbol, date)
    squeeze = _bb_squeeze_days(db, symbol, date)
    reversal = _foreign_flow_reversal(db, symbol, date)
    slopes = _accdist_slopes(db, symbol, date)

    db.execute(
        """UPDATE indicators SET
               smart_broker_streak = ?,
               bb_squeeze_days = ?,
               foreign_flow_reversal = ?,
               accdist_slope_5d = ?,
               accdist_slope_10d = ?,
               accdist_slope_20d = ?
           WHERE symbol = ? AND date = ?""",
        (streak, squeeze, reversal,
         slopes["5d"], slopes["10d"], slopes["20d"],
         symbol, date),
    )


def _log_signal(db, signal, regime=None):
    """Write a single signal to signal_events."""
    broker_code = signal.meta.get("broker_code") if signal.meta else None
    meta_json = json.dumps(signal.meta) if signal.meta else None

    price_row = db.execute(
        "SELECT close FROM prices WHERE symbol = ? AND date = ?",
        (signal.symbol, signal.date),
    ).fetchone()
    close = price_row["close"] if price_row else None

    ind_row = db.execute(
        "SELECT volume_ratio FROM indicators WHERE symbol = ? AND date = ?",
        (signal.symbol, signal.date),
    ).fetchone()
    vol_ratio = ind_row["volume_ratio"] if ind_row else None

    db.execute(
        """INSERT OR IGNORE INTO signal_events
           (symbol, date, signal_type, broker_code, magnitude,
            close, volume_ratio, regime, trend, meta)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (signal.symbol, signal.date, signal.signal_type, broker_code,
         signal.value, close, vol_ratio, regime, signal.direction, meta_json),
    )


def _fill_forward_returns(db, batch_size=5000):
    """Fill forward returns for signal_events that have NULL fwd columns."""
    rows = db.execute(
        """SELECT se.id, se.symbol, se.date, se.close
           FROM signal_events se
           WHERE se.fwd_5d IS NULL AND se.close IS NOT NULL
           ORDER BY se.date"""
    ).fetchall()

    if not rows:
        print("  No forward returns to fill")
        return 0

    filled = 0
    for i, row in enumerate(rows):
        symbol = row["symbol"]
        sig_date = row["date"]
        sig_close = row["close"]
        if not sig_close or sig_close == 0:
            continue

        future = db.execute(
            """SELECT date, close FROM prices
               WHERE symbol = ? AND date > ?
               ORDER BY date LIMIT 20""",
            (symbol, sig_date),
        ).fetchall()

        fwd_5d = fwd_10d = fwd_20d = None
        for j, f in enumerate(future):
            ret = (f["close"] - sig_close) / sig_close * 100
            if j == 4:
                fwd_5d = round(ret, 2)
            if j == 9:
                fwd_10d = round(ret, 2)
            if j == 19:
                fwd_20d = round(ret, 2)

        filled_through = 0
        if fwd_20d is not None:
            filled_through = 20
        elif fwd_10d is not None:
            filled_through = 10
        elif fwd_5d is not None:
            filled_through = 5

        if filled_through > 0:
            db.execute(
                """UPDATE signal_events SET
                       fwd_5d = ?, fwd_10d = ?, fwd_20d = ?,
                       filled_through = ?
                   WHERE id = ?""",
                (fwd_5d, fwd_10d, fwd_20d, filled_through, row["id"]),
            )
            filled += 1

        if (i + 1) % batch_size == 0:
            db.commit()
            print(f"  Forward returns: {i+1}/{len(rows)} processed, {filled} filled")

    db.commit()
    return filled


def _get_resume_date(db):
    """Get the last date that was fully processed."""
    row = db.execute(
        "SELECT MAX(date) as d FROM signal_events"
    ).fetchone()
    return row["d"] if row and row["d"] else None


def backfill(cfg, start=None, end=None, skip_temporal=False):
    """Run the full backfill."""
    db = get_db(cfg)
    t0 = time.time()

    # Determine date range
    all_dates = _get_trading_dates(db, start, end)
    if not all_dates:
        print("No trading dates found")
        db.close()
        return

    # Resume from last processed date
    if not start:
        resume = _get_resume_date(db)
        if resume:
            before = len(all_dates)
            all_dates = [d for d in all_dates if d > resume]
            if len(all_dates) < before:
                print(f"Resuming from {resume} ({before - len(all_dates)} dates already done)")

    print(f"Processing {len(all_dates)} trading dates ({all_dates[0]} to {all_dates[-1]})")
    print(f"Skip temporal: {skip_temporal}")
    print()

    total_signals = 0
    dates_done = 0

    for date in all_dates:
        symbols = _get_symbols_for_date(db, date)
        if not symbols:
            continue

        day_signals = 0

        # Phase 1: Compute temporal fields for this date
        if not skip_temporal:
            for symbol in symbols:
                _compute_temporal_for_date(db, symbol, date)

        # Phase 2: Evaluate signals
        for symbol in symbols:
            signals = evaluate_signals(db, symbol, date)
            for sig in signals:
                _log_signal(db, sig)
                day_signals += 1

        db.commit()
        dates_done += 1
        total_signals += day_signals

        if dates_done % 20 == 0:
            elapsed = time.time() - t0
            rate = dates_done / elapsed * 60
            remaining = (len(all_dates) - dates_done) / rate if rate > 0 else 0
            print(f"  [{dates_done}/{len(all_dates)}] {date}: {day_signals} signals "
                  f"({rate:.0f} dates/min, ~{remaining:.0f} min remaining)")

    elapsed = time.time() - t0
    print(f"\nSignal backfill complete: {total_signals} signals across {dates_done} dates ({elapsed:.0f}s)")

    # Phase 3: Fill forward returns
    print("\nFilling forward returns...")
    filled = _fill_forward_returns(db)
    print(f"  Filled {filled} forward returns")

    total_elapsed = time.time() - t0
    print(f"\nTotal backfill time: {total_elapsed:.0f}s ({total_elapsed/60:.1f} min)")

    db.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Backfill signal history")
    parser.add_argument("--config", default="config.yaml")
    parser.add_argument("--start", help="Start date (YYYY-MM-DD)")
    parser.add_argument("--end", help="End date (YYYY-MM-DD)")
    parser.add_argument("--skip-temporal", action="store_true",
                        help="Skip temporal field computation (if already done)")
    args = parser.parse_args()

    with open(SCRIPT_DIR / args.config) as f:
        cfg = yaml.safe_load(f)

    backfill(cfg, start=args.start, end=args.end, skip_temporal=args.skip_temporal)
