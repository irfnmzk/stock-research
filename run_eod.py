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
    from fetcher import fetch_prices, fetch_broker_summary, fetch_fundamentals
    from news import fetch_news

    log.info("Fetching prices (%d days)...", fetch_days)
    fetch_prices(cfg, days=fetch_days)

    log.info("Fetching broker data...")
    fetch_broker_summary(cfg)

    log.info("Fetching fundamentals...")
    fetch_fundamentals(cfg)

    log.info("Fetching news...")
    fetch_news(cfg)


def step_compute(cfg):
    """Compute indicators, whale scores, S/R, sector rotation."""
    from indicators import compute_all as compute_indicators
    from whale import compute_all as compute_whales
    from support_resistance import detect_all
    from sector import compute_rotation
    from db import get_db

    log.info("Computing indicators...")
    compute_indicators(cfg)

    log.info("Computing whale scores...")
    compute_whales(cfg)

    log.info("Detecting S/R levels...")
    detect_all(cfg)

    log.info("Computing sector rotation...")
    db = get_db(cfg)
    compute_rotation(cfg, db)
    db.close()


def step_assemble(cfg) -> dict:
    """Assemble EOD report data (macro, screener, signals, portfolio)."""
    from reports import get_eod_report_data

    log.info("Assembling report data...")
    return get_eod_report_data(cfg)


def step_charts(cfg, data: dict, chart_days=90) -> list[str]:
    """Generate charts for watchlist + top screener hits."""
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

    # Top screener hits not already in watchlist (max 5)
    hit_symbols = set()
    for rule_name, hits in data.get("screener_hits", {}).items():
        for h in hits:
            hit_symbols.add(h["symbol"])
    extra = [s for s in hit_symbols if s not in set(symbols)][:5]
    for symbol in extra:
        try:
            path = render_chart(cfg, symbol=symbol, days=chart_days)
            if path:
                chart_paths.append(str(Path(path).resolve()))
                log.info("  Chart: %s (screener)", symbol)
        except Exception as e:
            log.warning("  Chart error for %s: %s", symbol, e)

    return chart_paths


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def run(config_path="config.yaml", fetch_days=180, chart_days=90):
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

    # Step 3: Assemble report data
    data = None
    try:
        data = step_assemble(cfg)
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

    return len(errors) == 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="EOD Pipeline (standalone)")
    parser.add_argument("--config", default="config.yaml", help="config file")
    parser.add_argument("--fetch-days", type=int, default=180, help="days of price history")
    parser.add_argument("--chart-days", type=int, default=90, help="chart lookback days")
    args = parser.parse_args()

    ok = run(config_path=args.config, fetch_days=args.fetch_days, chart_days=args.chart_days)
    sys.exit(0 if ok else 1)
