"""Pluang API client — auth, OHLC endpoint, rate limiting."""

import json
import time
import uuid
from datetime import datetime, date
from pathlib import Path

import httpx

BASE_URL = "https://api-pluang.pluang.com"
TOKEN_FILE = Path(__file__).parent / "data" / ".tokens.json"
REQUEST_DELAY = 0.1


class PluangClient:
    def __init__(self):
        self._token = self._load_token()
        self._device_id = f"web-{uuid.uuid4()}"
        self._last_request = 0.0
        self.client = httpx.Client(base_url=BASE_URL, timeout=30)

    def _load_token(self) -> str:
        if not TOKEN_FILE.exists():
            raise RuntimeError(
                f"Pluang token not found. Add 'pluang_token' to {TOKEN_FILE}"
            )
        data = json.loads(TOKEN_FILE.read_text())
        token = data.get("pluang_token")
        if not token:
            raise RuntimeError(
                f"'pluang_token' key missing in {TOKEN_FILE}"
            )
        return token

    def _headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self._token}",
            "x-platform": "desktop-web",
            "x-device-id": self._device_id,
            "x-language-code": "id",
            "Referer": "https://trade.pluang.com/",
            "Origin": "https://trade.pluang.com",
        }

    def _throttle(self):
        elapsed = time.time() - self._last_request
        if elapsed < REQUEST_DELAY:
            time.sleep(REQUEST_DELAY - elapsed)
        self._last_request = time.time()

    def _fetch_ohlc_chunk(self, pluang_id: int, start_iso: str, end_iso: str) -> list[dict]:
        """Fetch a single chunk (start must be Jan 1, max 366 days)."""
        self._throttle()
        resp = self.client.get(
            f"/api/v4/asset/global-stock/price/ohlcStatsByDateRangeWithAlias/{pluang_id}",
            params={"timeFrame": "DAILY", "startDate": start_iso, "endDate": end_iso},
            headers=self._headers(),
        )
        resp.raise_for_status()
        body = resp.json()

        candles = body.get("data", [])
        results = []
        for c in candles:
            st = c.get("st", "")
            d = st[:10] if st else ""
            results.append({
                "date": d,
                "open": c.get("o"),
                "high": c.get("h"),
                "low": c.get("l"),
                "close": c.get("c"),
            })
        return results

    def fetch_ohlc(self, pluang_id: int, start_date: str, end_date: str) -> list[dict]:
        """Fetch OHLC data for an asset, chunking by year boundaries.

        API constraints:
        - startDate must be Jan 1 of a year
        - max range is 366 days (within same year or into next Jan 1)

        Args:
            pluang_id: Pluang internal asset ID
            start_date: ISO date string (YYYY-MM-DD)
            end_date: ISO date string (YYYY-MM-DD)

        Returns:
            List of {date, open, high, low, close} dicts
        """
        start_d = date.fromisoformat(start_date)
        end_d = date.fromisoformat(end_date)

        # Build year chunks: each chunk is Jan 1 -> Dec 31 (or end_date if same year)
        results = []
        current_year = start_d.year
        end_year = end_d.year

        for year in range(current_year, end_year + 1):
            chunk_start = f"{year}-01-01T00:00:00.000Z"
            chunk_end = f"{year}-12-31T00:00:00.000Z" if year < end_year else f"{end_date}T00:00:00.000Z"

            try:
                chunk = self._fetch_ohlc_chunk(pluang_id, chunk_start, chunk_end)
            except httpx.HTTPStatusError:
                continue

            # Filter to requested date range
            for c in chunk:
                if c["date"] and start_date <= c["date"] <= end_date:
                    results.append(c)

        return results

    def close(self):
        self.client.close()
