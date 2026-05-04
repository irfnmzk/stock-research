"""Data fetching orchestration using Stockbit API."""

from datetime import datetime, timedelta

from db import get_db
from stockbit import StockbitClient


def _symbols(cfg, override=None):
    """Return plain symbol list (no .JK suffix)."""
    if override:
        return [s.replace(".JK", "") for s in override]
    from db import get_watchlist
    return get_watchlist(cfg)


def _pool_symbols(cfg):
    """Return symbols from scan_pool table, or empty list if not populated."""
    db = get_db(cfg)
    rows = db.execute("SELECT symbol FROM scan_pool ORDER BY rank").fetchall()
    db.close()
    return [r["symbol"] for r in rows]


def _sector_indices(cfg):
    """Return sector index symbols from config."""
    return cfg.get("sectors", {}).get("indices", [])


def fetch_prices(cfg, symbols=None, days=180):
    """Fetch daily OHLCV + foreign flow from Stockbit and upsert into SQLite."""
    sb = StockbitClient()
    db = get_db(cfg)
    syms = _symbols(cfg, symbols)
    # Also fetch sector indices and IHSG
    syms += _sector_indices(cfg)
    if "IHSG" not in syms:
        syms.append("IHSG")

    end = datetime.now().strftime("%Y-%m-%d")
    start = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")

    for symbol in syms:
        print(f"Fetching prices: {symbol}...")
        try:
            resp = sb.daily_prices(symbol, start, end)
        except Exception as e:
            print(f"  Error: {e}")
            continue

        data = resp.get("data", resp)
        if not data:
            print(f"  No data for {symbol}")
            continue

        # Stockbit nests candles under data.chartbit
        candles = data.get("chartbit", data if isinstance(data, list) else [])
        if not candles:
            print(f"  No candles for {symbol}")
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
                c.get("open"),
                c.get("high"),
                c.get("low"),
                c.get("close"),
                c.get("volume"),
                c.get("value"),
                c.get("frequency"),
                c.get("foreignbuy"),
                c.get("foreignsell"),
                None,  # market_cap not in candle; derive from close * sharesoutstanding if needed
                c.get("shareoutstanding"),
                c.get("freq_analyzer"),
            ))

        db.executemany(
            """INSERT OR REPLACE INTO prices
               (symbol, date, open, high, low, close, volume, value, frequency,
                foreign_buy, foreign_sell, market_cap, shares_outstanding, freq_analyzer)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            rows,
        )
        db.commit()
        print(f"  Stored {len(rows)} rows for {symbol}")

    sb.close()
    db.close()


def fetch_broker_summary(cfg, symbols=None):
    """Fetch broker summary + bandar detector from Stockbit."""
    sb = StockbitClient()
    db = get_db(cfg)
    today = datetime.now().strftime("%Y-%m-%d")

    for symbol in _symbols(cfg, symbols):
        print(f"Fetching broker data: {symbol}...")
        try:
            resp = sb.market_detectors(symbol)
        except Exception as e:
            print(f"  Error: {e}")
            continue

        data = resp.get("data", resp)
        if not data:
            continue

        # Broker summary: merge brokers_buy + brokers_sell into unified rows
        bs = data.get("broker_summary", {})
        buys = {b["netbs_broker_code"]: b for b in bs.get("brokers_buy", [])}
        sells = {b["netbs_broker_code"]: b for b in bs.get("brokers_sell", [])}
        all_codes = set(buys) | set(sells)

        if all_codes:
            broker_rows = []
            for code in all_codes:
                buy = buys.get(code, {})
                sell = sells.get(code, {})
                # blot/bval = buy lot/value, slot/sval = sell lot/value (sell values are negative)
                buy_lot = int(float(buy.get("blot", 0)))
                buy_val = float(buy.get("bval", 0))
                sell_lot = abs(int(float(sell.get("slot", 0))))
                sell_val = abs(float(sell.get("sval", 0)))
                net_lot = buy_lot - sell_lot
                net_val = buy_val - sell_val
                btype = buy.get("type") or sell.get("type", "")
                avg_price = float(buy.get("netbs_buy_avg_price", 0) or sell.get("netbs_sell_avg_price", 0) or 0)
                freq = int(buy.get("freq", 0) or 0) + int(sell.get("freq", 0) or 0)

                broker_rows.append((
                    symbol, today, code, btype,
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
            print(f"  {len(broker_rows)} brokers for {symbol}")

        # Bandar detector: nested under top1/top3/top5/top10 with .amount and .accdist
        bd = data.get("bandar_detector", {})
        if bd:
            db.execute(
                """INSERT OR REPLACE INTO bandar_detector
                   (symbol, date, top1_net, top3_net, top5_net, top10_net,
                    top1_accdist, top3_accdist, top5_accdist, top10_accdist,
                    total_buyers, total_sellers, total_value)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    symbol, today,
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
            print(f"  Bandar detector stored for {symbol}")

        db.commit()

    sb.close()
    db.close()


def fetch_insider(cfg, symbols=None):
    """Fetch insider / major holder filings."""
    sb = StockbitClient()
    db = get_db(cfg)

    for symbol in _symbols(cfg, symbols):
        print(f"Fetching insider data: {symbol}...")
        try:
            resp = sb.insider_holders(symbol)
        except Exception as e:
            print(f"  Error: {e}")
            continue

        data = resp.get("data", resp)
        movements = data.get("movement", []) if isinstance(data, dict) else []
        count = 0
        for h in movements:
            # Parse date like "28 Jan 26" -> "2026-01-28"
            raw_date = h.get("date", "")
            try:
                dt = datetime.strptime(raw_date, "%d %b %y").strftime("%Y-%m-%d")
            except (ValueError, TypeError):
                dt = raw_date

            # Parse numeric values (they have commas)
            def _parse_int(val):
                if not val:
                    return None
                return int(str(val).replace(",", "").replace("+", ""))

            prev_shares = _parse_int(h.get("previous", {}).get("value"))
            curr_shares = _parse_int(h.get("current", {}).get("value"))
            change_val = h.get("changes", {}).get("formatted_value", "")
            change_shares = _parse_int(change_val) if change_val else None
            # Negative if sell/cross-out
            if h.get("action_type") in ("ACTION_TYPE_SELL",) and change_shares:
                change_shares = -abs(change_shares)

            price_str = h.get("price_formatted", "")
            price = _parse_int(price_str)

            badges = h.get("badges", [])
            badge = badges[0] if badges else None

            try:
                db.execute(
                    """INSERT OR IGNORE INTO insider
                       (symbol, name, date, action_type, previous_shares, current_shares,
                        change_shares, price, nationality, badge)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        symbol,
                        h.get("name", ""),
                        dt,
                        h.get("action_type", ""),
                        prev_shares,
                        curr_shares,
                        change_shares,
                        price,
                        h.get("nationality", ""),
                        badge,
                    ),
                )
                count += 1
            except Exception:
                pass
        db.commit()
        print(f"  {count} insider records for {symbol}")

    sb.close()
    db.close()


def fetch_companies(cfg):
    """Fetch full stock universe: sectors -> subsectors -> companies."""
    sb = StockbitClient()
    db = get_db(cfg)

    print("Fetching sectors...")
    try:
        resp = sb.sectors()
    except Exception as e:
        print(f"  Error: {e}")
        sb.close()
        db.close()
        return

    sectors_data = resp.get("data", resp)
    sectors_list = sectors_data if isinstance(sectors_data, list) else sectors_data.get("sectors", [])

    total = 0
    for sector in sectors_list:
        sid = sector.get("id", sector.get("sectorId"))
        sname = sector.get("name", sector.get("sectorName", ""))
        print(f"  Sector: {sname}")

        try:
            sub_resp = sb.subsectors(sid)
        except Exception as e:
            print(f"    Error fetching subsectors: {e}")
            continue

        sub_data = sub_resp.get("data", sub_resp)
        subs = sub_data if isinstance(sub_data, list) else sub_data.get("subsectors", [])

        for sub in subs:
            ssid = sub.get("id", sub.get("subsectorId"))
            ssname = sub.get("name", sub.get("subsectorName", ""))

            try:
                comp_resp = sb.companies(sid, ssid)
            except Exception as e:
                print(f"    Error fetching companies: {e}")
                continue

            comp_data = comp_resp.get("data", comp_resp)
            companies = comp_data if isinstance(comp_data, list) else comp_data.get("companies", [])

            for c in companies:
                sym = c.get("symbol", c.get("Symbol", ""))
                if not sym:
                    continue
                db.execute(
                    """INSERT OR REPLACE INTO companies
                       (symbol, name, sector_id, sector_name, subsector_id, subsector_name,
                        market_cap, last_price, avg_volume, tradeable)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        sym,
                        c.get("name", c.get("companyName", "")),
                        sid, sname, ssid, ssname,
                        c.get("market_cap", c.get("marketCap")),
                        c.get("last_price", c.get("lastPrice", c.get("close"))),
                        c.get("avg_volume", c.get("avgVolume")),
                        1,
                    ),
                )
                total += 1

        db.commit()

    print(f"Stored {total} companies.")
    sb.close()
    db.close()


def refresh_pool(cfg):
    """Fetch 1 day of prices for all Listing Board stocks, rank by market cap, store top N."""
    sb = StockbitClient()
    db = get_db(cfg)
    pool_cfg = cfg.get("pool", {})
    pool_size = pool_cfg.get("size", 300)
    min_vol = pool_cfg.get("min_avg_volume", 0)

    # Get all Listing Board symbols
    rows = db.execute("SELECT symbol FROM companies WHERE tradeable = 1").fetchall()
    all_symbols = [r["symbol"] for r in rows]
    if not all_symbols:
        print("No companies found. Run fetch-companies first.")
        sb.close()
        db.close()
        return

    print(f"Fetching 1-day prices for {len(all_symbols)} stocks to rank by market cap...")
    end = datetime.now().strftime("%Y-%m-%d")
    start = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")  # 7 days to ensure we get at least 1 trading day

    mcap_data = []
    for i, symbol in enumerate(all_symbols, 1):
        if i % 50 == 0:
            print(f"  Progress: {i}/{len(all_symbols)}")
        try:
            resp = sb.daily_prices(symbol, start, end)
        except Exception:
            continue

        data = resp.get("data", resp)
        if not data:
            continue
        candles = data.get("chartbit", [])
        if not candles:
            continue

        # Take the latest candle
        latest = candles[-1]
        close = latest.get("close")
        shares = latest.get("shareoutstanding")
        volume = latest.get("volume", 0)
        if close and shares and shares > 0:
            mcap = close * shares
            if min_vol and (volume or 0) < min_vol:
                continue
            mcap_data.append((symbol, mcap, volume))

    # Sort by market cap descending, take top N
    mcap_data.sort(key=lambda x: x[1], reverse=True)
    top = mcap_data[:pool_size]

    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    db.execute("DELETE FROM scan_pool")
    for rank, (symbol, mcap, _vol) in enumerate(top, 1):
        db.execute(
            "INSERT INTO scan_pool (symbol, market_cap, rank, updated_at) VALUES (?, ?, ?, ?)",
            (symbol, mcap, rank, now),
        )
    db.commit()

    print(f"Scan pool updated: {len(top)} stocks (from {len(mcap_data)} with valid market cap)")
    if top:
        print(f"  Top 5: {', '.join(t[0] for t in top[:5])}")
        print(f"  #300: {top[-1][0]} (mcap: {top[-1][1]/1e12:.2f}T)" if len(top) >= pool_size else "")

    sb.close()
    db.close()


def fetch_pool(cfg, days=180):
    """Fetch all data for scan pool symbols: prices, brokers, fundamentals, news, then compute."""
    pool = _pool_symbols(cfg)
    if not pool:
        print("Scan pool is empty. Run refresh-pool first.")
        return

    print(f"=== Fetching data for {len(pool)} pool stocks ===")

    print(f"\n--- Prices ({len(pool)} symbols, {days} days) ---")
    fetch_prices(cfg, symbols=pool, days=days)

    print(f"\n--- Broker data ({len(pool)} symbols) ---")
    fetch_broker_summary(cfg, symbols=pool)

    print(f"\n--- Fundamentals ({len(pool)} symbols) ---")
    fetch_fundamentals(cfg, symbols=pool)

    print(f"\n--- News ({len(pool)} symbols) ---")
    from news import fetch_news
    fetch_news(cfg, symbols=pool)

    print(f"\n--- Computing indicators ---")
    from indicators import compute_all as compute_indicators
    compute_indicators(cfg, symbols=pool)

    print(f"\n--- Computing S/R ---")
    from support_resistance import detect_all as compute_sr
    compute_sr(cfg, symbols=pool)

    print(f"\n--- Computing whale scores ---")
    from whale import compute_all as compute_whale
    compute_whale(cfg, symbols=pool)

    print(f"\n--- Computing sector rotation ---")
    from sector import compute_all as compute_sector
    compute_sector(cfg)

    print("\n=== Pool fetch + compute complete ===")


def fetch_fundamentals(cfg, symbols=None):
    """Fetch key stats / ratios for symbols."""
    sb = StockbitClient()
    db = get_db(cfg)
    today = datetime.now().strftime("%Y-%m-%d")

    # Map Stockbit fitem names to our DB columns
    FIELD_MAP = {
        "Current PE Ratio (TTM)": "pe_ttm",
        "Forward PE Ratio": "pe_forward",
        "Current Price to Book Value": "pbv",
        "Current Price to Sales (TTM)": "ps_ttm",
        "Current Price To Cashflow (TTM)": "pcf_ttm",
        "EV to EBITDA (TTM)": "ev_ebitda",
        "PEG Ratio": "peg",
        "Earnings Yield (TTM)": "earnings_yield",
        "Dividend Yield": "dividend_yield",
    }

    for symbol in _symbols(cfg, symbols):
        print(f"Fetching fundamentals: {symbol}...")
        try:
            resp = sb.fundamentals(symbol)
        except Exception as e:
            print(f"  Error: {e}")
            continue

        data = resp.get("data", resp)
        if not data:
            continue

        # Parse flat fitem list
        values = {}
        groups = data.get("closure_fin_items_results", [])
        for group in groups:
            for fi in group.get("fin_name_results", []):
                fitem = fi.get("fitem", {})
                name = fitem.get("name", "")
                raw = fitem.get("value", "")
                col = FIELD_MAP.get(name)
                if col:
                    # Clean value: remove %, commas, parentheses
                    cleaned = raw.replace("%", "").replace(",", "").replace("(", "-").replace(")", "").strip()
                    try:
                        values[col] = float(cleaned) if cleaned and cleaned != "-" else None
                    except ValueError:
                        values[col] = None

        db.execute(
            """INSERT OR REPLACE INTO fundamentals
               (symbol, date, pe_ttm, pe_forward, pbv, ps_ttm, pcf_ttm,
                ev_ebitda, peg, earnings_yield, dividend_yield)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                symbol, today,
                values.get("pe_ttm"),
                values.get("pe_forward"),
                values.get("pbv"),
                values.get("ps_ttm"),
                values.get("pcf_ttm"),
                values.get("ev_ebitda"),
                values.get("peg"),
                values.get("earnings_yield"),
                values.get("dividend_yield"),
            ),
        )
        db.commit()
        print(f"  Fundamentals stored for {symbol}")

    sb.close()
    db.close()
