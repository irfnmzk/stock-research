# Stockbit API Quirks

Known gotchas when working with the Stockbit API (exodus.stockbit.com).

## Authentication

- Auth flow: refresh_token -> `POST /login/refresh` -> access_token (also rotates refresh_token)
- Refresh token must be manually extracted from browser localStorage (login cannot be automated)
- Token manager in `stockbit.py` handles rotation and persistence to `data/.tokens.json`

## Endpoint Quirks

### Daily price (chartbit)
- `to` param = start date, `from` param = end date (swapped from what you'd expect)
- Daily data lives under `data.chartbit`, not `data.candles`
- Field names are lowercase no-underscore (e.g. `foreignbuy`, not `foreign_buy`)

### Intraday price
- Uses unix timestamps for `to`/`from` params (not date strings)

### Board/market type enums
- Inconsistent naming across endpoints:
  - `MARKET_BOARD_REGULER` (marketdetectors)
  - `BOARD_TYPE_REGULAR` (companies)
  - `MARKET_TYPE_REGULER` (other endpoints)

### Broker summary
- `type` field values differ by endpoint:
  - `"Asing"` / `"Lokal"` on marketdetectors
  - `"BROKER_TYPE_LOCAL"` / `"BROKER_TYPE_FOREIGN"` on broker activity
- Broker summary has separate `brokers_buy` / `brokers_sell` arrays with different field names

### Sectors
- Sectors API returns string IDs (not ints)
- Companies table uses Indonesian sector names while sector rotation uses IDX index names (IDXFINANCE, IDXBASIC, etc.)
- Explicit mapping dict in `screener.py` bridges the two naming schemes

### Fundamentals
- Returns a flat list, not structured objects

## Rate Limiting

- Not a concern per user testing. Lots of wiggle room.
