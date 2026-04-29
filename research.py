"""Exa-powered research for stock analysis - global macro, sector news, ticker-specific."""

import json
import os
import sys
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Try stock-research .env first, fall back to polymarket .env
ENV_PATHS = [
    Path(__file__).parent / "data" / ".env",
    Path(__file__).parent.parent / "polymarket" / "data" / ".env",
]


def load_api_key() -> str:
    key = os.environ.get("EXA_API_KEY", "")
    if not key:
        for env_path in ENV_PATHS:
            if env_path.exists():
                for line in env_path.read_text().splitlines():
                    line = line.strip()
                    if line.startswith("EXA_API_KEY="):
                        key = line.split("=", 1)[1].strip().strip("\"'")
                        break
            if key:
                break
    if not key:
        print("Error: EXA_API_KEY not found.", file=sys.stderr)
        return ""
    return key


def exa_search(query: str, num_results: int = 10, days_back: int = 7) -> list[dict]:
    """Semantic search via Exa API."""
    api_key = load_api_key()
    if not api_key:
        return []

    start_date = (datetime.now(timezone.utc) - timedelta(days=days_back)).strftime("%Y-%m-%dT%H:%M:%S.000Z")

    payload = json.dumps({
        "query": query,
        "numResults": num_results,
        "type": "auto",
        "startPublishedDate": start_date,
        "contents": {
            "text": {"maxCharacters": 1500},
            "highlights": {"numSentences": 3},
        },
    }).encode()

    req = urllib.request.Request(
        "https://api.exa.ai/search",
        data=payload,
        headers={
            "x-api-key": api_key,
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            data = json.loads(resp.read())
    except Exception as e:
        print(f"Exa search error: {e}", file=sys.stderr)
        return []

    results = []
    for r in data.get("results", []):
        results.append({
            "title": r.get("title", ""),
            "url": r.get("url", ""),
            "published": r.get("publishedDate", "")[:10],
            "text": r.get("text", ""),
            "highlights": r.get("highlights", []),
        })
    return results


# --- Research functions for stock analysis ---

def research_global_macro(days_back: int = 3) -> list[dict]:
    """Research global macro events affecting emerging markets / Indonesia."""
    queries = [
        "US Federal Reserve interest rate policy emerging markets impact",
        "Indonesia economy IDX stock market foreign capital flow",
        "USD IDR exchange rate Bank Indonesia monetary policy",
        "global trade war tariff impact Southeast Asia",
    ]
    all_results = []
    for q in queries:
        results = exa_search(q, num_results=3, days_back=days_back)
        all_results.extend(results)
    return all_results


def _get_ticker_name(symbol: str, db=None) -> str:
    """Get full company name for a ticker. Uses DB lookup with hardcoded fallback."""
    # Try DB first for dynamic coverage
    if db:
        row = db.execute(
            "SELECT name FROM companies WHERE symbol = ? LIMIT 1",
            (symbol,),
        ).fetchone()
        if row and row["name"]:
            return row["name"]

    # Hardcoded fallback for common tickers
    ticker_names = {
        "BBNI": "Bank Negara Indonesia BNI",
        "BBRI": "Bank Rakyat Indonesia BRI",
        "BBCA": "Bank Central Asia BCA",
        "BMRI": "Bank Mandiri",
        "TLKM": "Telkom Indonesia",
        "ASII": "Astra International",
        "UNTR": "United Tractors",
        "ADRO": "Adaro Energy",
        "ITMG": "Indo Tambangraya Megah",
        "PTBA": "Bukit Asam coal",
        "INCO": "Vale Indonesia nickel",
        "GGRM": "Gudang Garam",
        "HMSP": "HM Sampoerna",
        "INDF": "Indofood",
        "ICBP": "Indofood CBP",
        "BSDE": "Bumi Serpong Damai",
        "PGAS": "Perusahaan Gas Negara PGN",
        "BNGA": "CIMB Niaga",
        "JSMR": "Jasa Marga toll road",
        "SIDO": "Sido Muncul",
        "EXCL": "XL Axiata",
        "ANTM": "Aneka Tambang",
        "ELSA": "Elnusa",
    }
    return ticker_names.get(symbol, symbol)


def research_ticker(symbol: str, days_back: int = 7, db=None) -> list[dict]:
    """Research news for a specific IDX ticker."""
    name = _get_ticker_name(symbol, db)
    query = f"{name} {symbol} Indonesia stock earnings dividend news"
    return exa_search(query, num_results=5, days_back=days_back)


def research_sector(sector: str, days_back: int = 7) -> list[dict]:
    """Research sector-level news."""
    sector_queries = {
        "banking": "Indonesia banking sector loan growth NIM credit quality 2026",
        "coal": "coal price Indonesia mining export production",
        "nickel": "nickel price Indonesia smelter EV battery",
        "telco": "Indonesia telecom 5G data revenue",
        "consumer": "Indonesia consumer spending FMCG retail",
        "property": "Indonesia property real estate mortgage",
        "energy": "Indonesia oil gas energy price",
    }
    query = sector_queries.get(sector, f"Indonesia {sector} sector stock market")
    return exa_search(query, num_results=5, days_back=days_back)


def summarize_research(results: list[dict]) -> str:
    """Create a brief summary from research results for LLM context."""
    if not results:
        return "No research results available."

    lines = []
    for r in results[:10]:
        title = r["title"]
        highlights = " | ".join(r.get("highlights", [])[:2])
        if highlights:
            lines.append(f"- {title}: {highlights}")
        else:
            text_snippet = r.get("text", "")[:200]
            lines.append(f"- {title}: {text_snippet}")
    return "\n".join(lines)


def research_portfolio(cfg, days_back: int = 3) -> dict:
    """Research all open portfolio positions. Returns {symbol: [results]}."""
    from db import get_db
    from portfolio import get_portfolio

    conn = get_db(cfg)
    positions = get_portfolio(conn)

    research = {}
    for pos in positions:
        symbol = pos["symbol"]
        results = research_ticker(symbol, days_back=days_back, db=conn)
        if results:
            research[symbol] = results

    conn.close()
    return research
