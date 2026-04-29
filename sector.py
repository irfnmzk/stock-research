"""Sector rotation tracking."""

import pandas as pd

from db import get_db

# Canonical mapping: Indonesian sector names (companies table) -> IDX index names (sector_rotation table)
SECTOR_MAP = {
    "Keuangan": "IDXFINANCE",
    "Barang Baku": "IDXBASIC",
    "Energi": "IDXENERGY",
    "Infrastruktur": "IDXINFRA",
    "Properti & Real Estat": "IDXPROPERT",
    "Barang Konsumen Primer": "IDXNONCYC",
    "Barang Konsumen Non-Primer": "IDXCYCLIC",
    "Kesehatan": "IDXHEALTH",
    "Teknologi": "IDXTECHNO",
    "Perindustrian": "IDXINDUST",
    "Transportasi & Logistik": "IDXTRANS",
}

# Reverse mapping for display
IDX_TO_SECTOR = {v: k for k, v in SECTOR_MAP.items()}


def get_stock_sector(db, symbol: str) -> dict:
    """Get sector info for a symbol. Returns {sector_name, idx_name} or empty dict."""
    row = db.execute(
        "SELECT sector_name FROM companies WHERE symbol = ?", (symbol,)
    ).fetchone()
    if not row or not row["sector_name"]:
        return {}
    sector_name = row["sector_name"]
    return {
        "sector_name": sector_name,
        "idx_name": SECTOR_MAP.get(sector_name, ""),
    }


def get_sector_leaders(cfg, db, top_n: int = 3) -> list:
    """Get top N sectors by 5d rotation rank with their constituent stocks.

    Returns list of dicts:
      [{sector: str, idx_name: str, rank_5d: int, pct_5d: float, pct_10d: float,
        momentum: float, stocks: [{symbol, change_5d, market_cap}]}]
    """
    # Get latest rotation data, ranked by 5d performance
    sectors = db.execute(
        """SELECT sector, rank_5d, pct_5d, pct_10d, pct_20d, momentum
           FROM sector_rotation
           WHERE date = (SELECT MAX(date) FROM sector_rotation)
           ORDER BY rank_5d ASC
           LIMIT ?""",
        (top_n,),
    ).fetchall()

    results = []
    for s in sectors:
        idx_name = s["sector"]
        sector_name = IDX_TO_SECTOR.get(idx_name, idx_name)

        # Find scan pool stocks in this sector
        stocks = db.execute(
            """SELECT c.symbol, c.market_cap,
                      p_new.close as price_now, p_old.close as price_5d_ago
               FROM companies c
               JOIN scan_pool sp ON c.symbol = sp.symbol
               LEFT JOIN prices p_new ON c.symbol = p_new.symbol
                   AND p_new.date = (SELECT MAX(date) FROM prices WHERE symbol = c.symbol)
               LEFT JOIN prices p_old ON c.symbol = p_old.symbol
                   AND p_old.date = (SELECT MAX(date) FROM prices WHERE symbol = c.symbol
                                     AND date < date(p_new.date, '-4 days'))
               WHERE c.sector_name = ?
               ORDER BY c.market_cap DESC
               LIMIT 10""",
            (sector_name,),
        ).fetchall()

        stock_list = []
        for st in stocks:
            change_5d = None
            if st["price_now"] and st["price_5d_ago"] and st["price_5d_ago"] > 0:
                change_5d = round((st["price_now"] / st["price_5d_ago"] - 1) * 100, 2)
            stock_list.append({
                "symbol": st["symbol"],
                "change_5d": change_5d,
                "market_cap": st["market_cap"],
            })

        results.append({
            "sector": sector_name,
            "idx_name": idx_name,
            "rank_5d": s["rank_5d"],
            "pct_5d": s["pct_5d"],
            "pct_10d": s["pct_10d"],
            "pct_20d": s["pct_20d"],
            "momentum": s["momentum"],
            "stocks": stock_list,
        })

    return results


def compute_rotation(cfg, db):
    """Compute sector rotation metrics from sector index prices."""
    windows = cfg["sectors"]["windows"]

    for sector in cfg["sectors"]["indices"]:
        rows = db.execute(
            "SELECT date, close FROM prices WHERE symbol = ? ORDER BY date",
            (sector,),
        ).fetchall()
        if len(rows) < max(windows):
            continue

        df = pd.DataFrame(rows, columns=["date", "close"])
        df["date"] = pd.to_datetime(df["date"])
        df.set_index("date", inplace=True)

        latest = df.iloc[-1]["close"]
        pcts = {}
        for w in windows:
            if len(df) >= w:
                past = df.iloc[-w]["close"]
                pcts[w] = round((latest - past) / past * 100, 2)
            else:
                pcts[w] = None

        db.execute(
            """INSERT OR REPLACE INTO sector_rotation
               (sector, date, pct_5d, pct_10d, pct_20d, rank_5d, rank_10d, rank_20d, momentum)
               VALUES (?, date('now'), ?, ?, ?, NULL, NULL, NULL, NULL)""",
            (sector, pcts.get(5), pcts.get(10), pcts.get(20)),
        )

    db.commit()

    # Compute ranks
    today_rows = db.execute(
        "SELECT sector, pct_5d, pct_10d, pct_20d FROM sector_rotation WHERE date = date('now')"
    ).fetchall()

    if today_rows:
        df = pd.DataFrame([dict(r) for r in today_rows])
        for w in windows:
            col = f"pct_{w}d"
            rank_col = f"rank_{w}d"
            df[rank_col] = df[col].rank(ascending=False, na_option="bottom").astype(int)

        for _, row in df.iterrows():
            momentum = None
            if row.get("rank_5d") and row.get("rank_20d"):
                momentum = round(row["rank_20d"] - row["rank_5d"], 2)
            db.execute(
                "UPDATE sector_rotation SET rank_5d=?, rank_10d=?, rank_20d=?, momentum=? "
                "WHERE sector=? AND date=date('now')",
                (row.get("rank_5d"), row.get("rank_10d"), row.get("rank_20d"), momentum, row["sector"]),
            )
        db.commit()

    print("Sector rotation computed.")


def compute_all(cfg):
    db = get_db(cfg)
    compute_rotation(cfg, db)
    db.close()
