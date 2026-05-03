#!/usr/bin/env python3
"""Standalone EOD pipeline script.

Runs as a system cron job (no LLM involvement).
Fetches data, computes indicators, screens, generates charts,
and writes results to data/latest_eod.json for the LLM report layer.

Always writes data/pipeline_status.json with run status.
Logs to data/pipeline.log.

Usage:
    python run_eod.py [--config config.yaml]
"""

import argparse
import json
import logging
import os
import sys
import tempfile
import time
import traceback
from datetime import datetime
from pathlib import Path

# Ensure project root is on sys.path so local imports work
SCRIPT_DIR = Path(__file__).resolve().parent
os.chdir(SCRIPT_DIR)
sys.path.insert(0, str(SCRIPT_DIR))

import yaml

from dotenv import load_dotenv
load_dotenv()

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
LOG_PATH = SCRIPT_DIR / "data" / "pipeline.log"
LOG_PATH.parent.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_PATH),
        logging.StreamHandler(),
    ],
)
log = logging.getLogger("eod-pipeline")

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
DATA_DIR = SCRIPT_DIR / "data"
EOD_PATH = DATA_DIR / "latest_eod.json"
STATUS_PATH = DATA_DIR / "pipeline_status.json"


def _json_default(obj):
    """Handle non-serializable types."""
    if hasattr(obj, "isoformat"):
        return obj.isoformat()
    if isinstance(obj, bytes):
        return obj.decode("utf-8", errors="replace")
    return str(obj)


def _atomic_write_json(path: Path, data: dict):
    """Write JSON to a temp file then atomic-rename into place."""
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(data, f, indent=2, default=_json_default, ensure_ascii=False)
        os.replace(tmp, path)
    except Exception:
        # Clean up temp file on failure
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _write_status(status: str, error: str | None = None, partial: bool = False,
                  duration_s: float | None = None):
    """Write pipeline_status.json."""
    payload = {
        "status": status,
        "timestamp": datetime.now().isoformat(),
    }
    if duration_s is not None:
        payload["duration_s"] = round(duration_s, 1)
    if error:
        payload["error"] = error
    if partial:
        payload["partial"] = True
    _atomic_write_json(STATUS_PATH, payload)


# ---------------------------------------------------------------------------
# Pipeline steps
# ---------------------------------------------------------------------------

def step_fetch(cfg, fetch_days=180):
    """Fetch prices, brokers, fundamentals, news for the full pool."""
    from fetcher import fetch_prices, fetch_broker_summary, fetch_fundamentals, _pool_symbols
    from news import fetch_news

    log.info("Fetching prices (%d days)...", fetch_days)
    fetch_prices(cfg, days=fetch_days)

    # Fetch broker data for full 300-stock pool (not just watchlist)
    # to accumulate data for smart money / broker scoring analysis
    pool = _pool_symbols(cfg)
    log.info("Fetching broker data (%d pool stocks)...", len(pool))
    fetch_broker_summary(cfg, symbols=pool if pool else None)

    log.info("Fetching fundamentals...")
    fetch_fundamentals(cfg)

    log.info("Fetching news...")
    fetch_news(cfg)


def _get_pool_symbols(cfg):
    """Get all symbols from scan_pool, falling back to watchlist."""
    from db import get_db
    db = get_db(cfg)
    rows = db.execute("SELECT symbol FROM scan_pool ORDER BY rank").fetchall()
    db.close()
    pool = [r["symbol"] for r in rows]
    if not pool:
        log.warning("scan_pool empty, falling back to watchlist")
        pool = [s.replace(".JK", "") for s in cfg["watchlist"]]
    return pool


def step_compute(cfg):
    """Compute indicators, whale scores, S/R, sector rotation, temporal fields for all pool stocks."""
    from indicators import compute_all as compute_indicators
    from whale import compute_all as compute_whales
    from support_resistance import detect_all
    from sector import compute_rotation
    from temporal import compute_all as compute_temporal
    from db import get_db

    pool = _get_pool_symbols(cfg)
    log.info("Computing for %d stocks...", len(pool))

    log.info("Computing indicators...")
    compute_indicators(cfg, symbols=pool)

    log.info("Computing whale scores...")
    compute_whales(cfg, symbols=pool)

    log.info("Detecting S/R levels...")
    detect_all(cfg, symbols=pool)

    log.info("Computing temporal fields...")
    compute_temporal(cfg, symbols=pool)

    log.info("Computing sector rotation...")
    db = get_db(cfg)
    compute_rotation(cfg, db)
    db.close()


def step_signals(cfg):
    """Evaluate state-change signals for all pool stocks and log to DB."""
    from signal_engine import evaluate_all, log_signals
    from macro import get_macro_regime
    from db import get_db

    log.info("Evaluating signals...")
    results = evaluate_all(cfg)
    total = sum(len(sigs) for sigs in results.values())
    log.info("  %d signals fired across %d stocks", total, len(results))

    if results:
        regime_data = get_macro_regime(cfg)
        regime = regime_data.get("regime")
        db = get_db(cfg)
        log_signals(db, results, regime=regime)
        db.close()
        log.info("  Signals logged to signal_events")

    return results


def step_base_rates(cfg):
    """Fill forward returns for past signals and recompute base rates weekly."""
    from base_rates import fill_forward_returns, compute_signal_base_rates
    from datetime import date

    log.info("Filling forward returns...")
    filled = fill_forward_returns(cfg)
    log.info("  Filled %d forward returns", filled)

    # Recompute base rates on Fridays (or if tables are empty)
    from db import get_db
    db = get_db(cfg)
    count = db.execute("SELECT COUNT(*) FROM signal_base_rates").fetchone()[0]
    db.close()

    is_friday = date.today().weekday() == 4
    if is_friday or count == 0:
        log.info("Recomputing signal base rates...")
        compute_signal_base_rates(cfg)


def step_assemble(cfg, signals_by_symbol=None) -> dict:
    """Assemble EOD report data using new signal-based pipeline.

    Combines: macro, scanner, changes, broker narratives, watchlist signals,
    portfolio, and sector leaders into a single dict.
    """
    from macro import get_macro_regime
    from scanner import scan
    from changes import detect_changes
    from broker_narrative import generate_all as generate_narratives
    from sector import get_sector_leaders
    from portfolio import get_portfolio, get_tranche_suggestions, get_stop_warnings
    from base_rates import get_signal_base_rate
    from db import get_db

    log.info("Assembling report data...")

    # 1. Macro regime
    macro = get_macro_regime(cfg)

    # 2. Scanner funnel
    scanner_candidates = scan(cfg, signals_by_symbol=signals_by_symbol, top_n=5, use_base_rates=True)
    log.info("  Scanner: %d candidates", len(scanner_candidates))

    # 3. Watchlist signals
    watchlist_symbols = [s.replace(".JK", "") for s in cfg.get("watchlist", [])]
    watchlist = {}
    db = get_db(cfg)

    for symbol in watchlist_symbols:
        sigs = signals_by_symbol.get(symbol, []) if signals_by_symbol else []
        price_row = db.execute(
            """SELECT p.close, p.open, p.high, p.low, p.volume, p.date,
                      p.foreign_buy, p.foreign_sell,
                      i.rsi, i.volume_ratio, i.ema20, i.ema50, i.ema200,
                      i.bb_width, i.macd_hist,
                      i.smart_broker_streak, i.bb_squeeze_days
               FROM prices p
               LEFT JOIN indicators i ON p.symbol = i.symbol AND p.date = i.date
               WHERE p.symbol = ?
               ORDER BY p.date DESC LIMIT 1""",
            (symbol,),
        ).fetchone()

        # Enrich signals with base rates
        enriched_signals = []
        for s in sigs:
            sd = s.to_dict()
            rate = get_signal_base_rate(cfg, s.signal_type, s.direction, symbol)
            sd["avg_return_10d"] = round(rate["avg_return_10d"], 2) if rate and rate["avg_return_10d"] else None
            sd["sample_size"] = rate["sample_size"] if rate else None
            sd["scope"] = rate["scope"] if rate else None
            enriched_signals.append(sd)

        entry = {"signals": enriched_signals}
        if price_row:
            entry.update({
                "price": price_row["close"],
                "date": price_row["date"],
                "change_pct": round((price_row["close"] - price_row["open"]) / price_row["open"] * 100, 2) if price_row["open"] else 0,
                "rsi": price_row["rsi"],
                "volume_ratio": price_row["volume_ratio"],
                "ema20": price_row["ema20"],
                "ema50": price_row["ema50"],
                "ema200": price_row["ema200"],
                "smart_broker_streak": price_row["smart_broker_streak"],
                "bb_squeeze_days": price_row["bb_squeeze_days"],
            })

            # Multi-day returns
            hist = db.execute(
                """SELECT close FROM prices WHERE symbol = ? AND date <= ?
                   ORDER BY date DESC LIMIT 21""",
                (symbol, price_row["date"]),
            ).fetchall()
            close = price_row["close"]
            entry["return_5d"] = round((close - hist[5]["close"]) / hist[5]["close"] * 100, 2) if len(hist) > 5 and hist[5]["close"] else None
            entry["return_10d"] = round((close - hist[10]["close"]) / hist[10]["close"] * 100, 2) if len(hist) > 10 and hist[10]["close"] else None
            entry["return_20d"] = round((close - hist[20]["close"]) / hist[20]["close"] * 100, 2) if len(hist) > 20 and hist[20]["close"] else None

        # S/R zones
        sr_rows = db.execute(
            "SELECT level, level_type, touch_count FROM support_resistance WHERE symbol = ?",
            (symbol,),
        ).fetchall()
        close = price_row["close"] if price_row else 0
        entry["sr_zones"] = {
            "resistance": [
                {"level": r["level"], "touches": r["touch_count"]}
                for r in sr_rows if r["level_type"] == "resistance" and r["level"] > close
            ][:3],
            "support": [
                {"level": r["level"], "touches": r["touch_count"]}
                for r in sr_rows if r["level_type"] == "support" and r["level"] < close
            ][-3:],
        }

        watchlist[symbol] = entry

    # 4. Broker narratives (watchlist + scanner symbols)
    scanner_symbols = [c["symbol"] for c in scanner_candidates if not c.get("in_watchlist")]
    all_narrative_symbols = watchlist_symbols + scanner_symbols
    narratives = generate_narratives(cfg, all_narrative_symbols)
    for symbol in watchlist_symbols:
        if symbol in narratives:
            watchlist[symbol]["broker_narrative"] = narratives[symbol]
    for candidate in scanner_candidates:
        if candidate["symbol"] in narratives:
            candidate["broker_narrative"] = narratives[candidate["symbol"]]

    # 4b. Multi-day returns for scanner candidates
    for candidate in scanner_candidates:
        sym = candidate["symbol"]
        hist = db.execute(
            """SELECT close FROM prices WHERE symbol = ?
               ORDER BY date DESC LIMIT 21""",
            (sym,),
        ).fetchall()
        if hist and hist[0]["close"]:
            c = hist[0]["close"]
            candidate["return_5d"] = round((c - hist[5]["close"]) / hist[5]["close"] * 100, 2) if len(hist) > 5 and hist[5]["close"] else None
            candidate["return_10d"] = round((c - hist[10]["close"]) / hist[10]["close"] * 100, 2) if len(hist) > 10 and hist[10]["close"] else None
            candidate["return_20d"] = round((c - hist[20]["close"]) / hist[20]["close"] * 100, 2) if len(hist) > 20 and hist[20]["close"] else None

    # 5. Change detection
    change_symbols = watchlist_symbols + scanner_symbols
    changes_list = detect_changes(cfg, symbols=change_symbols)
    log.info("  Changes: %d detected", len(changes_list))

    # 6. Portfolio
    portfolio = get_portfolio(db)
    stop_warnings = get_stop_warnings(cfg, threshold_pct=3.0)

    # 7. Sector leaders
    sector_leaders = get_sector_leaders(cfg, db, top_n=5)

    db.close()

    return {
        "macro": macro,
        "changes": changes_list,
        "watchlist": watchlist,
        "scanner": scanner_candidates,
        "portfolio": portfolio,
        "stop_warnings": stop_warnings,
        "sector_leaders": sector_leaders,
    }


def step_charts(cfg, data: dict, chart_days=90) -> list[str]:
    """Generate charts for watchlist + top scanner hits."""
    from charts import render_chart

    chart_paths = []
    symbols = [s.replace(".JK", "") for s in cfg["watchlist"]]

    # Watchlist charts
    for symbol in symbols:
        try:
            path = render_chart(cfg, symbol=symbol, days=chart_days)
            if path:
                chart_paths.append(str(Path(path).resolve()))
                log.info("  Chart: %s", symbol)
        except Exception as e:
            log.warning("  Chart error for %s: %s", symbol, e)

    # Top scanner hits not already in watchlist (max 5)
    scanner_symbols = [
        c["symbol"] for c in data.get("scanner", [])
        if not c.get("in_watchlist")
    ][:5]
    for symbol in scanner_symbols:
        try:
            path = render_chart(cfg, symbol=symbol, days=chart_days)
            if path:
                chart_paths.append(str(Path(path).resolve()))
                log.info("  Chart: %s (scanner)", symbol)
        except Exception as e:
            log.warning("  Chart error for %s: %s", symbol, e)

    return chart_paths


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def run(config_path="config.yaml", fetch_days=180, chart_days=90, notify=False):
    """Run the full EOD pipeline."""
    t0 = time.time()
    errors = []

    log.info("=" * 60)
    log.info("EOD Pipeline started")

    # Load config
    with open(SCRIPT_DIR / config_path) as f:
        cfg = yaml.safe_load(f)

    # Step 1: Fetch
    try:
        step_fetch(cfg, fetch_days=fetch_days)
    except Exception as e:
        msg = f"Fetch failed: {e}"
        log.error(msg)
        log.error(traceback.format_exc())
        errors.append(msg)

    # Step 2: Compute (can run on stale data if fetch partially failed)
    try:
        step_compute(cfg)
    except Exception as e:
        msg = f"Compute failed: {e}"
        log.error(msg)
        log.error(traceback.format_exc())
        errors.append(msg)

    # Step 2b: Evaluate signals
    signals_by_symbol = None
    try:
        signals_by_symbol = step_signals(cfg)
    except Exception as e:
        msg = f"Signals failed: {e}"
        log.error(msg)
        log.error(traceback.format_exc())
        errors.append(msg)

    # Step 2c: Fill forward returns and recompute base rates
    try:
        step_base_rates(cfg)
    except Exception as e:
        msg = f"Base rates failed: {e}"
        log.error(msg)
        log.error(traceback.format_exc())
        errors.append(msg)

    # Step 3: Assemble report data
    data = None
    try:
        data = step_assemble(cfg, signals_by_symbol=signals_by_symbol)
    except Exception as e:
        msg = f"Assemble failed: {e}"
        log.error(msg)
        log.error(traceback.format_exc())
        errors.append(msg)

    # Step 4: Charts
    if data:
        try:
            log.info("Generating charts...")
            chart_paths = step_charts(cfg, data, chart_days=chart_days)
            data["chart_paths"] = chart_paths
        except Exception as e:
            msg = f"Charts failed: {e}"
            log.error(msg)
            errors.append(msg)

    duration = time.time() - t0

    # Write output
    if data:
        data["generated_at"] = datetime.now().isoformat()
        _atomic_write_json(EOD_PATH, data)
        log.info("Wrote %s", EOD_PATH)

    # Write status
    if errors and data:
        _write_status("partial", error="; ".join(errors), partial=True, duration_s=duration)
        log.warning("Pipeline completed with errors (%.1fs): %s", duration, "; ".join(errors))
    elif errors:
        _write_status("error", error="; ".join(errors), duration_s=duration)
        log.error("Pipeline failed (%.1fs): %s", duration, "; ".join(errors))
    else:
        _write_status("ok", duration_s=duration)
        log.info("Pipeline completed successfully (%.1fs)", duration)

    # Send Telegram notification if requested and pipeline produced data
    if notify and data:
        try:
            import asyncio
            from bot import trigger_eod_brief
            from telegram.ext import Application

            token = os.environ.get("TELEGRAM_BOT_TOKEN")
            if token:
                log.info("Sending EOD brief via Telegram...")
                app = Application.builder().token(token).build()
                asyncio.run(app.initialize())
                asyncio.run(trigger_eod_brief(cfg, app))
                asyncio.run(app.shutdown())
                log.info("EOD brief sent.")
            else:
                log.warning("--notify requested but TELEGRAM_BOT_TOKEN not set")
        except Exception as e:
            log.error("Telegram notification failed: %s", e)

    return len(errors) == 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="EOD Pipeline (standalone)")
    parser.add_argument("--config", default="config.yaml", help="config file")
    parser.add_argument("--fetch-days", type=int, default=180, help="days of price history")
    parser.add_argument("--chart-days", type=int, default=90, help="chart lookback days")
    parser.add_argument("--notify", action="store_true", help="send EOD brief via Telegram after pipeline completes")
    args = parser.parse_args()

    ok = run(config_path=args.config, fetch_days=args.fetch_days, chart_days=args.chart_days, notify=args.notify)
    sys.exit(0 if ok else 1)
