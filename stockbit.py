"""Stockbit API client with automatic token rotation."""

import json
import os
import time
from pathlib import Path

import httpx

BASE_URL = "https://exodus.stockbit.com"
TOKEN_FILE = Path(__file__).parent / "data" / ".tokens.json"
ENV_FILE = Path(__file__).parent / "data" / ".env"


def _load_env():
    """Load .env file if it exists."""
    if ENV_FILE.exists():
        for line in ENV_FILE.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, val = line.split("=", 1)
                os.environ.setdefault(key.strip(), val.strip())


class TokenManager:
    """Handles token management. Supports plain access token or refresh flow."""

    def __init__(self):
        self.access_token = None
        self.refresh_token = None
        self.expires_at = 0
        self._static = False  # True when using a plain token (no refresh)
        _load_env()
        self._load()

    def _load(self):
        # STOCKBIT_TOKEN = plain access token, no refresh needed
        env_token = os.environ.get("STOCKBIT_TOKEN")
        if env_token:
            self.access_token = env_token
            self.expires_at = time.time() + 86400 * 365  # treat as never-expiring
            self._static = True
            return

        # Refresh token flow
        env_rt = os.environ.get("STOCKBIT_REFRESH_TOKEN")
        if TOKEN_FILE.exists():
            data = json.loads(TOKEN_FILE.read_text())
            self.access_token = data.get("access_token")
            self.refresh_token = data.get("refresh_token")
            self.expires_at = data.get("expires_at", 0)
        if env_rt:
            self.refresh_token = env_rt

    def _save(self):
        if self._static:
            return
        TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
        TOKEN_FILE.write_text(json.dumps({
            "access_token": self.access_token,
            "refresh_token": self.refresh_token,
            "expires_at": self.expires_at,
        }))

    def get_access_token(self) -> str:
        if self.access_token and time.time() < self.expires_at - 60:
            return self.access_token
        if self._static:
            raise RuntimeError("STOCKBIT_TOKEN expired or invalid")
        return self._refresh()

    def _refresh(self) -> str:
        if not self.refresh_token:
            raise RuntimeError(
                "No token. Set STOCKBIT_TOKEN (access token) or STOCKBIT_REFRESH_TOKEN env var"
            )
        resp = httpx.post(
            f"{BASE_URL}/login/refresh",
            headers={"Authorization": f"Bearer {self.refresh_token}"},
            timeout=15,
        )
        resp.raise_for_status()
        body = resp.json()
        data = body.get("data", body)
        # Response structure: data.access.token, data.refresh.token
        access_data = data.get("access", data)
        refresh_data = data.get("refresh", {})
        self.access_token = access_data.get("token") or access_data.get("access_token")
        if refresh_data.get("token"):
            self.refresh_token = refresh_data["token"]
        elif data.get("refresh_token"):
            self.refresh_token = data["refresh_token"]
        # Parse expiry from expired_at or fallback to 24h
        expired_at = access_data.get("expired_at")
        if expired_at:
            from datetime import datetime, timezone
            exp_dt = datetime.fromisoformat(expired_at.replace("Z", "+00:00"))
            self.expires_at = exp_dt.timestamp()
        else:
            self.expires_at = time.time() + data.get("expires_in", 86400)
        self._save()
        return self.access_token


class StockbitClient:
    """Thin wrapper around Stockbit exodus API."""

    def __init__(self):
        self.tm = TokenManager()
        self.client = httpx.Client(base_url=BASE_URL, timeout=30)

    def _headers(self):
        return {"Authorization": f"Bearer {self.tm.get_access_token()}"}

    def _get(self, path: str, params: dict = None) -> dict:
        resp = self.client.get(path, headers=self._headers(), params=params)
        resp.raise_for_status()
        return resp.json()

    def close(self):
        self.client.close()

    # --- Endpoints ---

    def daily_prices(self, symbol: str, start: str, end: str) -> dict:
        """Fetch daily OHLCV + foreign flow.
        Note: Stockbit swaps to/from -- to=start, from=end.
        start/end are 'YYYY-MM-DD' strings.
        """
        return self._get(f"/chartbit/{symbol}/price/daily", params={
            "to": start,
            "from": end,
        })

    def intraday_prices(self, symbol: str, ts_from: int, ts_to: int) -> dict:
        """Fetch intraday per-minute OHLCV. ts_from/ts_to are unix timestamps."""
        return self._get(f"/chartbit/{symbol}/price/intraday", params={
            "to": ts_from,
            "from": ts_to,
        })

    def market_detectors(self, symbol: str) -> dict:
        """Broker summary + bandar detector for a symbol (latest day)."""
        return self._get(f"/marketdetectors/{symbol}")

    def market_detectors_date(self, symbol: str, date: str) -> dict:
        """Broker summary + bandar detector for a symbol on a specific date."""
        return self._get(f"/marketdetectors/{symbol}", params={"from": date, "to": date})

    def running_trade(self, symbol: str) -> dict:
        """Running trade / order flow chart data."""
        return self._get(f"/order-trade/running-trade/chart/{symbol}")

    def broker_activity_chart(self, broker: str, **params) -> dict:
        """Broker reverse lookup (chart view)."""
        return self._get("/order-trade/broker/activity-chart", params={"broker": broker, **params})

    def broker_activity(self, broker: str, **params) -> dict:
        """Broker reverse lookup (table view)."""
        return self._get("/order-trade/broker/activity", params={"broker": broker, **params})

    def insider_holders(self, symbol: str) -> dict:
        """Insider / major holder filings."""
        return self._get(f"/insider/company/majorholder", params={"symbol": symbol})

    def sectors(self) -> dict:
        """List all sectors."""
        return self._get("/emitten/sectors")

    def subsectors(self, sector_id: int) -> dict:
        """List subsectors for a sector."""
        return self._get(f"/emitten/sectors/{sector_id}/subsectors")

    def companies(self, sector_id: int, subsector_id: int) -> dict:
        """List companies in a subsector with price/mcap."""
        return self._get(f"/emitten/v3/sector/{sector_id}/subsector/{subsector_id}/company")

    def fundamentals(self, symbol: str) -> dict:
        """Key stats / ratios for a symbol."""
        return self._get(f"/keystats/ratio/v1/{symbol}")

    def news_stream(self, symbol: str, limit: int = 20) -> dict:
        """News stream for a symbol."""
        return self._get(f"/stream/v3/symbol/{symbol}", params={
            "category": "STREAM_CATEGORY_NEWS",
            "limit": limit,
        })
