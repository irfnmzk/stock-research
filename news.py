"""News fetching from Stockbit stream API."""

from db import get_db
from stockbit import StockbitClient


def fetch_news(cfg, symbols=None):
    """Fetch news stream for watchlist symbols and store in DB."""
    sb = StockbitClient()
    db = get_db(cfg)

    syms = symbols or [s.replace(".JK", "") for s in cfg["watchlist"]]
    total = 0

    for symbol in syms:
        print(f"Fetching news: {symbol}...")
        try:
            resp = sb.news_stream(symbol, limit=20)
        except Exception as e:
            print(f"  Error: {e}")
            continue

        data = resp.get("data", resp)
        items = data.get("stream", data if isinstance(data, list) else [])

        count = 0
        for item in items:
            sid = str(item.get("stream_id", ""))
            if not sid:
                continue

            title = item.get("title", "")
            if not title:
                title = str(item.get("content", ""))[:200]
            if not title:
                continue

            published = item.get("created_at", "")

            try:
                db.execute(
                    """INSERT OR IGNORE INTO news
                       (stream_id, symbol_queried, title, content, source, url,
                        published_at, topics, total_likes)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        sid, symbol, title,
                        item.get("content", item.get("content_original", "")),
                        item.get("user", {}).get("username", ""),
                        item.get("title_url", ""),
                        published, None,
                        item.get("total_likes", 0),
                    ),
                )
                count += 1
            except Exception:
                pass

        db.commit()
        total += count
        print(f"  {count} news items for {symbol}")

    print(f"Total: {total} news items stored.")
    sb.close()
    db.close()
