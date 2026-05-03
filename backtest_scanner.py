#!/usr/bin/env python3
"""Backtest scanner — replay scanner logic against historical signal_events.

Measures whether multi-signal stock-days outperform baseline.
Uses historical trailing ADV for liquidity filtering (no look-ahead bias).

Usage:
    python backtest_scanner.py [--config config.yaml] [--mode simple|base_rate]
                               [--top-n 5] [--min-signals 2] [--csv output.csv]
"""

import argparse
import math
import sys
import time
from pathlib import Path

import pandas as pd
import yaml

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

from db import get_db

LIQUIDITY_FLOOR = 500_000_000
BASE_RATE_THRESHOLD = 1.0


def _load_config(path):
    with open(path) as f:
        return yaml.safe_load(f)


def _get_signal_base_rates(db):
    """Load market-wide base rates. Returns {(signal_type, direction): avg_return_10d}."""
    rows = db.execute("""
        SELECT signal_type, direction, avg_return_10d
        FROM signal_base_rates
        WHERE (symbol IS NULL OR symbol = '') AND (broker_code IS NULL OR broker_code = '')
    """).fetchall()
    return {(r["signal_type"], r["direction"]): r["avg_return_10d"] for r in rows}


def _build_picks(db, mode, base_rates, min_signals):
    """Build DataFrame of all scanner-eligible stock-days.

    Returns DataFrame with: symbol, date, signal_count, signal_types, fwd_5d, fwd_10d, fwd_20d, adv
    """
    # Load all signal events grouped by (symbol, date)
    rows = db.execute("""
        SELECT symbol, date,
               GROUP_CONCAT(DISTINCT signal_type || ':' || trend) as sig_keys,
               COUNT(DISTINCT signal_type) as raw_count,
               MIN(fwd_5d) as fwd_5d,
               MIN(fwd_10d) as fwd_10d,
               MIN(fwd_20d) as fwd_20d
        FROM signal_events
        WHERE filled_through >= 10
        GROUP BY symbol, date
    """).fetchall()

    stock_days = []
    for r in rows:
        sig_keys = r["sig_keys"].split(",") if r["sig_keys"] else []

        if mode == "base_rate":
            count = 0
            for sk in sig_keys:
                parts = sk.split(":")
                if len(parts) == 2:
                    sig_type, direction = parts
                    rate = base_rates.get((sig_type, direction))
                    if rate is not None and rate >= BASE_RATE_THRESHOLD:
                        count += 1
        else:
            count = r["raw_count"]

        if count >= min_signals and r["fwd_10d"] is not None:
            stock_days.append({
                "symbol": r["symbol"],
                "date": r["date"],
                "signal_count": count,
                "signal_types": r["sig_keys"],
                "fwd_5d": r["fwd_5d"],
                "fwd_10d": r["fwd_10d"],
                "fwd_20d": r["fwd_20d"],
            })

    if not stock_days:
        return pd.DataFrame()

    picks = pd.DataFrame(stock_days)

    # Compute historical 20-day trailing ADV
    prices = pd.read_sql_query(
        "SELECT symbol, date, value FROM prices WHERE value IS NOT NULL ORDER BY symbol, date",
        db,
    )
    prices["adv20"] = prices.groupby("symbol")["value"].transform(
        lambda x: x.rolling(20, min_periods=5).mean()
    )
    adv_lookup = prices.set_index(["symbol", "date"])["adv20"]

    picks["adv"] = picks.apply(
        lambda row: adv_lookup.get((row["symbol"], row["date"]), 0), axis=1
    )
    picks = picks[picks["adv"] >= LIQUIDITY_FLOOR].reset_index(drop=True)

    return picks


def _compute_baseline(db):
    """Per-date average fwd returns across all signal stock-days (deduplicated)."""
    rows = db.execute("""
        SELECT date, AVG(fwd_5d) as avg_5d, AVG(fwd_10d) as avg_10d,
               AVG(fwd_20d) as avg_20d, COUNT(*) as n
        FROM (
            SELECT DISTINCT symbol, date, fwd_5d, fwd_10d, fwd_20d
            FROM signal_events WHERE filled_through >= 10
        )
        GROUP BY date
    """).fetchall()
    return {r["date"]: {"avg_5d": r["avg_5d"], "avg_10d": r["avg_10d"],
                        "avg_20d": r["avg_20d"], "n": r["n"]} for r in rows}


def _compute_metrics(picks_df, baseline, label):
    """Compute performance metrics for a set of picks."""
    if picks_df.empty:
        return None

    n = len(picks_df)
    active_days = picks_df["date"].nunique()
    total_days = len(baseline)

    avg_5d = picks_df["fwd_5d"].mean()
    avg_10d = picks_df["fwd_10d"].mean()
    avg_20d = picks_df["fwd_20d"].mean()
    hit_5d = (picks_df["fwd_5d"] > 0).mean() * 100
    hit_10d = (picks_df["fwd_10d"] > 0).mean() * 100
    hit_20d = (picks_df["fwd_20d"] > 0).mean() * 100

    # Per-day excess vs baseline
    daily = picks_df.groupby("date")["fwd_10d"].mean().reset_index()
    daily["baseline"] = daily["date"].map(lambda d: baseline.get(d, {}).get("avg_10d", 0))
    daily["excess"] = daily["fwd_10d"] - daily["baseline"]

    excess_mean = daily["excess"].mean()
    excess_std = daily["excess"].std()
    sharpe = (excess_mean / excess_std * math.sqrt(252)) if excess_std > 0 else 0

    return {
        "label": label,
        "picks": n,
        "days": active_days,
        "picks_per_day": n / active_days if active_days > 0 else 0,
        "avg_5d": avg_5d,
        "avg_10d": avg_10d,
        "avg_20d": avg_20d,
        "hit_5d": hit_5d,
        "hit_10d": hit_10d,
        "hit_20d": hit_20d,
        "excess_10d": excess_mean,
        "sharpe": sharpe,
    }


def _print_results(all_metrics, baseline, mode, top_n):
    """Print formatted results table."""
    # Overall baseline stats
    all_10d = [v["avg_10d"] for v in baseline.values() if v["avg_10d"] is not None]
    baseline_avg = sum(all_10d) / len(all_10d) if all_10d else 0
    total_stock_days = sum(v["n"] for v in baseline.values())

    print(f"\nScanner Backtest Results")
    print(f"{'=' * 70}")
    dates = sorted(baseline.keys())
    print(f"Period: {dates[0]} to {dates[-1]} ({len(dates)} trading days)")
    print(f"Mode: {mode} | Liquidity floor: {LIQUIDITY_FLOOR/1e6:.0f}M IDR | Top-N: {top_n or 'no cap'}")
    print(f"Baseline (all signal stock-days): avg_fwd_10d = +{baseline_avg:.2f}%, n = {total_stock_days:,}")
    print()

    header = f"{'Threshold':<10} {'Picks':>6} {'Days':>5} {'P/Day':>5} {'Avg5d':>7} {'Avg10d':>7} {'Avg20d':>7} {'Hit10d':>6} {'Exc10d':>7} {'Sharpe':>6}"
    print(header)
    print("─" * len(header))

    for m in all_metrics:
        if m is None:
            continue
        print(
            f"{m['label']:<10} {m['picks']:>6,} {m['days']:>5} {m['picks_per_day']:>5.1f}"
            f" {m['avg_5d']:>+6.2f}% {m['avg_10d']:>+6.2f}% {m['avg_20d']:>+6.2f}%"
            f" {m['hit_10d']:>5.1f}% {m['excess_10d']:>+6.2f}% {m['sharpe']:>6.2f}"
        )

    print()


def main():
    parser = argparse.ArgumentParser(description="Backtest scanner against historical signal_events")
    parser.add_argument("--config", default="config.yaml")
    parser.add_argument("--mode", choices=["simple", "base_rate"], default="simple")
    parser.add_argument("--top-n", type=int, default=0, help="Cap picks per day (0=no cap)")
    parser.add_argument("--min-signals", type=int, default=2)
    parser.add_argument("--csv", type=str, help="Output CSV path")
    args = parser.parse_args()

    cfg = _load_config(SCRIPT_DIR / args.config)
    db = get_db(cfg)

    t0 = time.time()

    # Load base rates if needed
    base_rates = _get_signal_base_rates(db) if args.mode == "base_rate" else {}

    # Build all eligible picks
    print("Building scanner picks...")
    picks = _build_picks(db, args.mode, base_rates, args.min_signals)
    if picks.empty:
        print("No picks found.")
        db.close()
        return

    print(f"  {len(picks):,} stock-days pass liquidity + signal threshold")

    # Baseline
    baseline = _compute_baseline(db)

    # Test multiple thresholds
    thresholds = [2, 3, 4]
    all_metrics = []

    # Uncapped results
    for thresh in thresholds:
        subset = picks[picks["signal_count"] >= thresh]
        m = _compute_metrics(subset, baseline, f"{thresh}+")
        all_metrics.append(m)

    _print_results(all_metrics, baseline, args.mode, top_n=None)

    # Top-N capped results
    top_n = args.top_n if args.top_n > 0 else 5
    print(f"With top-{top_n} cap per day:")
    capped_metrics = []
    for thresh in thresholds:
        subset = picks[picks["signal_count"] >= thresh]
        capped = subset.sort_values(["date", "signal_count"], ascending=[True, False])
        capped = capped.groupby("date").head(top_n)
        m = _compute_metrics(capped, baseline, f"{thresh}+ (t{top_n})")
        capped_metrics.append(m)

    header = f"{'Threshold':<12} {'Picks':>6} {'Days':>5} {'P/Day':>5} {'Avg5d':>7} {'Avg10d':>7} {'Avg20d':>7} {'Hit10d':>6} {'Exc10d':>7} {'Sharpe':>6}"
    print(header)
    print("─" * len(header))
    for m in capped_metrics:
        if m is None:
            continue
        print(
            f"{m['label']:<12} {m['picks']:>6,} {m['days']:>5} {m['picks_per_day']:>5.1f}"
            f" {m['avg_5d']:>+6.2f}% {m['avg_10d']:>+6.2f}% {m['avg_20d']:>+6.2f}%"
            f" {m['hit_10d']:>5.1f}% {m['excess_10d']:>+6.2f}% {m['sharpe']:>6.2f}"
        )

    elapsed = time.time() - t0
    print(f"\nCompleted in {elapsed:.1f}s")

    # CSV output
    if args.csv:
        picks["baseline_10d"] = picks["date"].map(lambda d: baseline.get(d, {}).get("avg_10d", 0))
        picks["excess_10d"] = picks["fwd_10d"] - picks["baseline_10d"]
        picks.to_csv(args.csv, index=False)
        print(f"CSV written: {args.csv} ({len(picks):,} rows)")

    db.close()


if __name__ == "__main__":
    main()
