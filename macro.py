"""Macro indicators: USD/IDR, US 10Y yield, BI Rate, aggregate foreign flow."""

import csv
import io
import sqlite3
from datetime import date, timedelta

import httpx

from db import get_db


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

MACRO_SCHEMA = """
CREATE TABLE IF NOT EXISTS macro (
    date        TEXT NOT NULL,
    indicator   TEXT NOT NULL,
    value       REAL,
    PRIMARY KEY (date, indicator)
);
CREATE INDEX IF NOT EXISTS idx_macro_indicator ON macro(indicator, date);
"""


def init_macro_table(conn: sqlite3.Connection):
    conn.executescript(MACRO_SCHEMA)


# ---------------------------------------------------------------------------
# Fetchers
# ---------------------------------------------------------------------------

def fetch_usdidr(conn: sqlite3.Connection, days: int = 180):
    """Fetch historical USD/IDR from Frankfurter API."""
    end = date.today()
    start = end - timedelta(days=days)
    url = f"https://api.frankfurter.app/{start}..{end}?from=USD&to=IDR"
    r = httpx.get(url, timeout=20, follow_redirects=True)
    r.raise_for_status()
    data = r.json()
    rates = data.get("rates", {})
    rows = [(d, "USDIDR", rates[d]["IDR"]) for d in rates]
    conn.executemany(
        "INSERT OR REPLACE INTO macro (date, indicator, value) VALUES (?, ?, ?)",
        rows,
    )
    conn.commit()
    print(f"  USD/IDR: {len(rows)} data points stored ({start} to {end})")
    return rows


def fetch_us10y(conn: sqlite3.Connection, year: int = None):
    """Fetch US 10Y yield from Treasury.gov CSV."""
    if year is None:
        year = date.today().year
    url = (
        f"https://home.treasury.gov/resource-center/data-chart-center/"
        f"interest-rates/daily-treasury-rates.csv/{year}/all"
        f"?type=daily_treasury_yield_curve&field_tdr_date_value={year}"
        f"&page&_format=csv"
    )
    r = httpx.get(url, timeout=20, follow_redirects=True)
    r.raise_for_status()
    reader = csv.reader(io.StringIO(r.text))
    header = next(reader)
    # Find the 10 Yr column index
    col_idx = None
    for i, h in enumerate(header):
        if "10 yr" in h.lower() or "10 year" in h.lower():
            col_idx = i
            break
    if col_idx is None:
        # Typically column index 9 (1mo,2mo,3mo,4mo,6mo,1yr,2yr,3yr,5yr,7yr,10yr,20yr,30yr)
        # Header: Date,1 Mo,2 Mo,3 Mo,4 Mo,6 Mo,1 Yr,2 Yr,3 Yr,5 Yr,7 Yr,10 Yr,20 Yr,30 Yr
        col_idx = 11  # 0-indexed, 10 Yr is typically at index 11

    rows = []
    for line in reader:
        if not line or not line[0]:
            continue
        # Date format: MM/DD/YYYY
        parts = line[0].split("/")
        if len(parts) == 3:
            d = f"{parts[2]}-{parts[0].zfill(2)}-{parts[1].zfill(2)}"
        else:
            continue
        try:
            val = float(line[col_idx]) if line[col_idx] else None
        except (ValueError, IndexError):
            continue
        if val is not None:
            rows.append((d, "US10Y", val))

    conn.executemany(
        "INSERT OR REPLACE INTO macro (date, indicator, value) VALUES (?, ?, ?)",
        rows,
    )
    conn.commit()
    print(f"  US 10Y: {len(rows)} data points stored (year {year})")
    return rows


def set_bi_rate(conn: sqlite3.Connection, rate: float, effective_date: str):
    """Manually set BI Rate (only changes on RDG meetings)."""
    conn.execute(
        "INSERT OR REPLACE INTO macro (date, indicator, value) VALUES (?, ?, ?)",
        (effective_date, "BI_RATE", rate),
    )
    conn.commit()
    print(f"  BI Rate set: {rate}% effective {effective_date}")


# ---------------------------------------------------------------------------
# Aggregate foreign flow from prices table
# ---------------------------------------------------------------------------

def compute_aggregate_foreign_flow(conn: sqlite3.Connection, days: int = 60):
    """Compute daily aggregate net foreign flow across all stocks in DB."""
    cutoff = (date.today() - timedelta(days=days)).isoformat()
    cur = conn.execute("""
        SELECT date, 
               SUM(foreign_buy) as total_fbuy,
               SUM(foreign_sell) as total_fsell,
               SUM(foreign_buy) - SUM(foreign_sell) as net_foreign
        FROM prices
        WHERE date >= ? AND foreign_buy IS NOT NULL
        GROUP BY date
        ORDER BY date
    """, (cutoff,))
    rows = cur.fetchall()
    # Store as macro indicator
    macro_rows = [(r[0], "NET_FOREIGN_FLOW", r[3]) for r in rows]
    conn.executemany(
        "INSERT OR REPLACE INTO macro (date, indicator, value) VALUES (?, ?, ?)",
        macro_rows,
    )
    conn.commit()
    print(f"  Aggregate foreign flow: {len(macro_rows)} days computed")
    return rows


# ---------------------------------------------------------------------------
# Volume spike detection
# ---------------------------------------------------------------------------

def detect_volume_spikes(conn: sqlite3.Connection, threshold: float = 2.0, days: int = 60):
    """Detect days where aggregate market volume > threshold * 20-day MA."""
    cutoff = (date.today() - timedelta(days=days + 30)).isoformat()
    cur = conn.execute("""
        SELECT date, SUM(volume) as total_vol
        FROM prices
        WHERE date >= ? AND volume IS NOT NULL
        GROUP BY date
        ORDER BY date
    """, (cutoff,))
    daily_vols = cur.fetchall()

    spikes = []
    for i in range(20, len(daily_vols)):
        ma20 = sum(daily_vols[j][1] for j in range(i - 20, i)) / 20
        curr_vol = daily_vols[i][1]
        ratio = curr_vol / ma20 if ma20 > 0 else 0
        if ratio >= threshold:
            spikes.append((daily_vols[i][0], curr_vol, ratio))

    return spikes


# ---------------------------------------------------------------------------
# Capitulation candle detection
# ---------------------------------------------------------------------------

def detect_capitulation(conn: sqlite3.Connection, symbol: str = None, days: int = 60):
    """
    Detect capitulation candles: high volume + reversal (close > open after gap down).
    If symbol is None, checks aggregate IHSG-proxy behavior.
    """
    cutoff = (date.today() - timedelta(days=days + 30)).isoformat()

    if symbol:
        cur = conn.execute("""
            SELECT date, open, high, low, close, volume
            FROM prices WHERE symbol = ? AND date >= ?
            ORDER BY date
        """, (symbol, cutoff))
    else:
        # Aggregate: use total market volume and average price change
        cur = conn.execute("""
            SELECT date, 
                   AVG(open) as avg_open, AVG(high) as avg_high,
                   AVG(low) as avg_low, AVG(close) as avg_close,
                   SUM(volume) as total_vol
            FROM prices WHERE date >= ? AND volume IS NOT NULL
            GROUP BY date ORDER BY date
        """, (cutoff,))

    rows = cur.fetchall()
    signals = []

    for i in range(20, len(rows)):
        d, o, h, l, c, v = rows[i]
        # Volume MA20
        ma20_vol = sum(rows[j][5] for j in range(i - 20, i)) / 20
        vol_ratio = v / ma20_vol if ma20_vol > 0 else 0

        # Reversal: close > open (green candle) AND low significantly below open (wick)
        prev_close = rows[i - 1][4]
        gap_down = (o < prev_close)
        green_close = (c > o)
        long_lower_wick = (o - l) > (c - o) if c > o else False
        high_volume = vol_ratio >= 2.0

        if high_volume and green_close and (gap_down or long_lower_wick):
            signals.append({
                "date": d,
                "volume_ratio": round(vol_ratio, 2),
                "change_pct": round((c - o) / o * 100, 2),
                "pattern": "capitulation_reversal"
            })

    return signals


# ---------------------------------------------------------------------------
# Dashboard / Signal Summary
# ---------------------------------------------------------------------------

def macro_signals_summary(conn: sqlite3.Connection):
    """Generate a summary of all macro signals."""
    today = date.today().isoformat()
    summary = {}

    # Latest USD/IDR
    cur = conn.execute(
        "SELECT date, value FROM macro WHERE indicator='USDIDR' ORDER BY date DESC LIMIT 20"
    )
    usdidr_rows = cur.fetchall()
    if usdidr_rows:
        latest = usdidr_rows[0]
        summary["usdidr"] = {
            "latest": latest[1],
            "date": latest[0],
            "5d_ago": usdidr_rows[4][1] if len(usdidr_rows) > 4 else None,
            "20d_ago": usdidr_rows[19][1] if len(usdidr_rows) > 19 else None,
        }
        if summary["usdidr"]["5d_ago"]:
            summary["usdidr"]["5d_change"] = round(
                (latest[1] - summary["usdidr"]["5d_ago"]) / summary["usdidr"]["5d_ago"] * 100, 3
            )
        if summary["usdidr"]["20d_ago"]:
            summary["usdidr"]["20d_change"] = round(
                (latest[1] - summary["usdidr"]["20d_ago"]) / summary["usdidr"]["20d_ago"] * 100, 3
            )

    # Latest US 10Y
    cur = conn.execute(
        "SELECT date, value FROM macro WHERE indicator='US10Y' ORDER BY date DESC LIMIT 20"
    )
    us10y_rows = cur.fetchall()
    if us10y_rows:
        latest = us10y_rows[0]
        summary["us10y"] = {
            "latest": latest[1],
            "date": latest[0],
            "5d_ago": us10y_rows[4][1] if len(us10y_rows) > 4 else None,
            "20d_ago": us10y_rows[19][1] if len(us10y_rows) > 19 else None,
        }
        if summary["us10y"]["5d_ago"]:
            summary["us10y"]["5d_change"] = round(latest[1] - summary["us10y"]["5d_ago"], 3)
        if summary["us10y"]["20d_ago"]:
            summary["us10y"]["20d_change"] = round(latest[1] - summary["us10y"]["20d_ago"], 3)

    # Latest BI Rate
    cur = conn.execute(
        "SELECT date, value FROM macro WHERE indicator='BI_RATE' ORDER BY date DESC LIMIT 1"
    )
    bi_row = cur.fetchone()
    if bi_row:
        summary["bi_rate"] = {"rate": bi_row[1], "effective_date": bi_row[0]}

    # Aggregate foreign flow (last 20 days)
    cur = conn.execute(
        "SELECT date, value FROM macro WHERE indicator='NET_FOREIGN_FLOW' ORDER BY date DESC LIMIT 20"
    )
    flow_rows = cur.fetchall()
    if flow_rows:
        recent_5d = [r[1] for r in flow_rows[:5]]
        recent_20d = [r[1] for r in flow_rows]
        summary["foreign_flow"] = {
            "latest": flow_rows[0][1],
            "latest_date": flow_rows[0][0],
            "avg_5d": round(sum(recent_5d) / len(recent_5d)),
            "avg_20d": round(sum(recent_20d) / len(recent_20d)),
            "sum_5d": round(sum(recent_5d)),
            "sum_20d": round(sum(recent_20d)),
            "consecutive_negative": 0,
        }
        # Count consecutive negative days
        for r in flow_rows:
            if r[1] < 0:
                summary["foreign_flow"]["consecutive_negative"] += 1
            else:
                break

    # Volume spikes
    spikes = detect_volume_spikes(conn, threshold=2.0, days=30)
    summary["volume_spikes_30d"] = spikes

    # Capitulation signals
    caps = detect_capitulation(conn, symbol=None, days=30)
    summary["capitulation_signals_30d"] = caps

    return summary


def print_dashboard(summary: dict):
    """Pretty-print the macro signals dashboard."""
    print("=" * 60)
    print("  MACRO SIGNALS DASHBOARD")
    print("=" * 60)

    # USD/IDR
    if "usdidr" in summary:
        s = summary["usdidr"]
        trend = "↑" if s.get("5d_change", 0) > 0 else "↓" if s.get("5d_change", 0) < 0 else "→"
        print(f"\n  USD/IDR: {s['latest']:,.0f} ({s['date']}) {trend}")
        if s.get("5d_change") is not None:
            print(f"    5d change: {s['5d_change']:+.3f}%")
        if s.get("20d_change") is not None:
            print(f"    20d change: {s['20d_change']:+.3f}%")
        # Signal interpretation
        if s.get("5d_change", 0) > 0.5:
            print("    ⚠️  IDR weakening rapidly - foreign selling pressure likely continues")
        elif s.get("5d_change", 0) < -0.3:
            print("    ✅ IDR strengthening - foreign selling pressure may ease")
        else:
            print("    ➡️  IDR stable - neutral signal")

    # US 10Y
    if "us10y" in summary:
        s = summary["us10y"]
        trend = "↑" if s.get("5d_change", 0) > 0 else "↓" if s.get("5d_change", 0) < 0 else "→"
        print(f"\n  US 10Y Yield: {s['latest']:.2f}% ({s['date']}) {trend}")
        if s.get("5d_change") is not None:
            print(f"    5d change: {s['5d_change']:+.3f}%")
        if s.get("20d_change") is not None:
            print(f"    20d change: {s['20d_change']:+.3f}%")
        if s.get("5d_change", 0) > 0.1:
            print("    ⚠️  Yields rising - EM less attractive, capital outflow risk")
        elif s.get("5d_change", 0) < -0.1:
            print("    ✅ Yields falling - EM more attractive, potential inflow catalyst")
        else:
            print("    ➡️  Yields stable - neutral")

    # BI Rate
    if "bi_rate" in summary:
        s = summary["bi_rate"]
        print(f"\n  BI Rate: {s['rate']:.2f}% (effective {s['effective_date']})")

    # Foreign Flow
    if "foreign_flow" in summary:
        s = summary["foreign_flow"]
        print(f"\n  Aggregate Foreign Flow:")
        print(f"    Latest: {s['latest']:,.0f} ({s['latest_date']})")
        print(f"    5d avg: {s['avg_5d']:,.0f} | 5d sum: {s['sum_5d']:,.0f}")
        print(f"    20d avg: {s['avg_20d']:,.0f} | 20d sum: {s['sum_20d']:,.0f}")
        print(f"    Consecutive negative days: {s['consecutive_negative']}")
        if s["avg_5d"] > 0:
            print("    ✅ Foreign flow turning positive - potential reversal signal")
        elif abs(s["avg_5d"]) < abs(s["avg_20d"]) * 0.5:
            print("    🟡 Outflow slowing (5d avg < 50% of 20d avg) - exhaustion signal")
        else:
            print("    ⚠️  Heavy outflow continues - stay cautious")

    # Volume spikes
    spikes = summary.get("volume_spikes_30d", [])
    if spikes:
        print(f"\n  Volume Spikes (>2x MA20) in last 30d: {len(spikes)}")
        for s in spikes[-3:]:
            print(f"    {s[0]}: {s[2]:.1f}x normal volume")
    else:
        print(f"\n  Volume Spikes (>2x MA20) in last 30d: None")

    # Capitulation
    caps = summary.get("capitulation_signals_30d", [])
    if caps:
        print(f"\n  Capitulation Signals (last 30d): {len(caps)}")
        for c in caps[-3:]:
            print(f"    {c['date']}: vol {c['volume_ratio']}x, +{c['change_pct']}% reversal")
        print("    🟢 Capitulation detected - potential bottom forming")
    else:
        print(f"\n  Capitulation Signals (last 30d): None detected")

    # Overall assessment
    print("\n" + "-" * 60)
    print("  OVERALL ASSESSMENT:")
    score = 0
    reasons = []
    if "usdidr" in summary:
        if summary["usdidr"].get("5d_change", 0) < -0.3:
            score += 1
            reasons.append("IDR strengthening")
        elif summary["usdidr"].get("5d_change", 0) > 0.5:
            score -= 1
            reasons.append("IDR weakening")
    if "us10y" in summary:
        if summary["us10y"].get("5d_change", 0) < -0.1:
            score += 1
            reasons.append("US yields falling")
        elif summary["us10y"].get("5d_change", 0) > 0.1:
            score -= 1
            reasons.append("US yields rising")
    if "foreign_flow" in summary:
        if summary["foreign_flow"]["avg_5d"] > 0:
            score += 2
            reasons.append("foreign flow positive")
        elif abs(summary["foreign_flow"]["avg_5d"]) < abs(summary["foreign_flow"]["avg_20d"]) * 0.5:
            score += 1
            reasons.append("outflow slowing")
        else:
            score -= 1
            reasons.append("heavy outflow")
    if caps:
        score += 1
        reasons.append("capitulation detected")

    if score >= 3:
        verdict = "🟢 STRONG BUY SIGNAL - Multiple indicators aligning for reversal"
    elif score >= 1:
        verdict = "🟡 EARLY SIGNAL - Some conditions improving, consider first tranche"
    elif score >= 0:
        verdict = "⚪ NEUTRAL - Wait for clearer signals"
    else:
        verdict = "🔴 CAUTION - Conditions still deteriorating, stay patient"

    print(f"  Score: {score}/5 | {verdict}")
    if reasons:
        print(f"  Factors: {', '.join(reasons)}")
    print("=" * 60)


# ---------------------------------------------------------------------------
# CLI entry points
# ---------------------------------------------------------------------------

def fetch_macro(cfg, days: int = 180):
    """Fetch all macro data."""
    conn = get_db(cfg)
    init_macro_table(conn)
    print("Fetching macro data...")
    fetch_usdidr(conn, days=days)
    fetch_us10y(conn)
    # Also fetch previous year if we're early in the year
    if date.today().month <= 3:
        fetch_us10y(conn, year=date.today().year - 1)
    compute_aggregate_foreign_flow(conn, days=60)
    conn.close()


def get_macro_regime(cfg) -> dict:
    """Compute macro regime for pipeline integration.

    Returns structured dict:
      regime: risk_on | cautious | risk_off
      score: float (-2 to +2)
      usdidr: {value, trend, change_5d}
      us10y: {value, trend}
      bi_rate: float
      foreign_flow_5d: float
      summary: str (one-line human readable)
    """
    conn = get_db(cfg)
    init_macro_table(conn)
    raw = macro_signals_summary(conn)
    conn.close()

    score = 0.0
    reasons = []

    # USD/IDR component
    usdidr_info = {"value": None, "trend": "unknown", "change_5d": None}
    if "usdidr" in raw:
        s = raw["usdidr"]
        usdidr_info["value"] = s.get("latest")
        usdidr_info["change_5d"] = s.get("5d_change", 0)
        change = s.get("5d_change", 0) or 0
        if change < -0.3:
            score += 0.5
            usdidr_info["trend"] = "strengthening"
            reasons.append("IDR strengthening")
        elif change > 0.5:
            score -= 0.5
            usdidr_info["trend"] = "weakening"
            reasons.append("IDR weakening")
        else:
            usdidr_info["trend"] = "stable"

    # US 10Y component
    us10y_info = {"value": None, "trend": "unknown"}
    if "us10y" in raw:
        s = raw["us10y"]
        us10y_info["value"] = s.get("latest")
        change = s.get("5d_change", 0) or 0
        if change < -0.1:
            score += 0.5
            us10y_info["trend"] = "falling"
            reasons.append("US yields falling")
        elif change > 0.1:
            score -= 0.5
            us10y_info["trend"] = "rising"
            reasons.append("US yields rising")
        else:
            us10y_info["trend"] = "stable"

    # BI Rate
    bi_rate = raw.get("bi_rate", {}).get("rate")

    # Foreign flow component (heaviest weight)
    foreign_flow_5d = 0.0
    if "foreign_flow" in raw:
        s = raw["foreign_flow"]
        foreign_flow_5d = s.get("avg_5d", 0) or 0
        if foreign_flow_5d > 0:
            score += 1.0
            reasons.append("foreign flow positive")
        elif s.get("avg_20d", 0) and abs(foreign_flow_5d) < abs(s["avg_20d"]) * 0.5:
            score += 0.5
            reasons.append("outflow slowing")
        else:
            score -= 0.5
            reasons.append("heavy outflow")

    # Determine regime
    if score >= 1.0:
        regime = "risk_on"
    elif score <= -0.5:
        regime = "risk_off"
    else:
        regime = "cautious"

    summary_line = f"{regime.upper()} (score {score:+.1f}): {', '.join(reasons) if reasons else 'no signals'}"

    return {
        "regime": regime,
        "score": score,
        "usdidr": usdidr_info,
        "us10y": us10y_info,
        "bi_rate": bi_rate,
        "foreign_flow_5d": foreign_flow_5d,
        "summary": summary_line,
    }


def show_signals(cfg):
    """Show macro signals dashboard."""
    conn = get_db(cfg)
    init_macro_table(conn)
    summary = macro_signals_summary(conn)
    print_dashboard(summary)
    conn.close()
    return summary
