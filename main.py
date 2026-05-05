"""IDX Research Assistant - CLI entry point."""

import argparse
from pathlib import Path

import yaml
from dotenv import load_dotenv

load_dotenv()


def load_config(path="config.yaml"):
    with open(path) as f:
        return yaml.safe_load(f)


def cmd_fetch(args, cfg):
    from fetcher import fetch_prices
    fetch_prices(cfg, symbols=args.symbols, days=args.days)


def cmd_fetch_brokers(args, cfg):
    from fetcher import fetch_broker_summary
    fetch_broker_summary(cfg, symbols=args.symbols)


def cmd_fetch_insider(args, cfg):
    from fetcher import fetch_insider
    fetch_insider(cfg, symbols=args.symbols)


def cmd_fetch_companies(args, cfg):
    from fetcher import fetch_companies
    fetch_companies(cfg)


def cmd_refresh_pool(args, cfg):
    from fetcher import refresh_pool
    refresh_pool(cfg)


def cmd_fetch_pool(args, cfg):
    from fetcher import fetch_pool
    fetch_pool(cfg, days=args.days)


def cmd_fetch_fundamentals(args, cfg):
    from fetcher import fetch_fundamentals
    fetch_fundamentals(cfg, symbols=args.symbols)


def cmd_fetch_news(args, cfg):
    from news import fetch_news
    fetch_news(cfg, symbols=args.symbols)


def cmd_indicators(args, cfg):
    from indicators import compute_all
    compute_all(cfg, symbols=args.symbols)


def cmd_sr(args, cfg):
    from support_resistance import detect_all
    detect_all(cfg, symbols=args.symbols)


def cmd_chart(args, cfg):
    from charts import render_chart
    render_chart(cfg, symbol=args.symbol, days=args.days)


def cmd_fetch_macro(args, cfg):
    from macro import fetch_macro
    fetch_macro(cfg, days=args.days)


def cmd_macro_signals(args, cfg):
    from macro import show_signals
    show_signals(cfg)


def cmd_set_bi_rate(args, cfg):
    from macro import set_bi_rate, init_macro_table
    from db import get_db
    from datetime import date as dt_date
    conn = get_db(cfg)
    init_macro_table(conn)
    effective = args.date or dt_date.today().isoformat()
    set_bi_rate(conn, rate=args.rate, effective_date=effective)
    conn.close()


def cmd_buy(args, cfg):
    from portfolio import cmd_buy as _cmd_buy
    _cmd_buy(args, cfg)


def cmd_sell(args, cfg):
    from portfolio import cmd_sell as _cmd_sell
    _cmd_sell(args, cfg)


def cmd_portfolio(args, cfg):
    from portfolio import cmd_portfolio as _cmd_portfolio
    _cmd_portfolio(args, cfg)


def cmd_trades(args, cfg):
    from portfolio import cmd_trades as _cmd_trades
    _cmd_trades(args, cfg)


def cmd_set_stop(args, cfg):
    from portfolio import cmd_set_stop as _cmd_set_stop
    _cmd_set_stop(args, cfg)


def cmd_backfill(args, cfg):
    """Backfill historical prices and/or broker summary data."""
    import logging
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[
            logging.FileHandler(Path(__file__).parent / "data" / "backfill.log"),
            logging.StreamHandler(),
        ],
    )
    from backfill import backfill_prices, backfill_brokers

    do_all = not args.prices and not args.brokers  # if neither flag, do both

    if args.prices or do_all:
        backfill_prices(cfg, start_date=args.start, end_date=args.end)

    if args.brokers or do_all:
        backfill_brokers(cfg, start_date=args.start, end_date=args.end, batch_pause=args.delay)


def cmd_agent_chat(args, cfg):
    """Interactive agent chat in terminal."""
    from agent import run_conversation, close_session
    session_id = None
    print("IDX Research Agent (type 'quit' to exit)")
    print("-" * 40)
    while True:
        try:
            msg = input("\nyou: ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if not msg or msg.lower() in ("quit", "exit", "q"):
            break
        text, charts, session_id = run_conversation(cfg, msg, session_id=session_id)
        print(f"\nagent: {text}")
        if charts:
            print(f"  [charts: {', '.join(charts)}]")
    if session_id:
        print("\nClosing session...")
        close_session(cfg, session_id)
    print("Done.")


def cmd_send_brief(args, cfg):
    """Generate and print EOD brief (or send via Telegram with --notify)."""
    from agent import generate_eod_brief
    import json
    brief = generate_eod_brief(cfg)
    print(json.dumps(brief, indent=2, ensure_ascii=False))

    if args.notify:
        import asyncio
        import os
        from bot import trigger_eod_brief
        from telegram.ext import Application

        token = os.environ.get("TELEGRAM_BOT_TOKEN")
        if not token:
            print("\nError: TELEGRAM_BOT_TOKEN not set")
            return
        app = Application.builder().token(token).build()
        asyncio.run(app.initialize())
        asyncio.run(trigger_eod_brief(cfg, app))
        asyncio.run(app.shutdown())
        print("\nBrief sent via Telegram.")


def cmd_bot(args, cfg):
    """Start the Telegram bot."""
    from bot import run_bot
    run_bot()


def cmd_us_seed(args, cfg):
    """Seed US assets from Pluang reference JSON."""
    from fetcher_us import seed_assets
    seed_assets()


def cmd_us_seed_sectors(args, cfg):
    """Seed sector/industry from yfinance."""
    from fetcher_us import seed_sectors
    seed_sectors()


def cmd_us_fetch(args, cfg):
    """Fetch US prices from Pluang."""
    from fetcher_us import fetch_prices
    fetch_prices(days=args.days)


def cmd_us_pipeline(args, cfg):
    """Run full US daily pipeline."""
    from fetcher_us import run_pipeline
    run_pipeline(days=args.days)


def cmd_us_scan(args, cfg):
    """Run US scanner."""
    from scanner_us import scan, format_scan_output
    from db import get_us_db
    candidates = scan(top_n=args.top)
    db = get_us_db()
    br_rows = db.execute("SELECT * FROM signal_base_rates").fetchall()
    base_rates = {r["signal_type"]: dict(r) for r in br_rows}
    db.close()
    print(format_scan_output(candidates, base_rates=base_rates))


def cmd_fetch_all(args, cfg):
    """Run full daily fetch pipeline: prices, brokers, fundamentals, news."""
    from fetcher import fetch_prices, fetch_broker_summary, fetch_fundamentals
    from news import fetch_news

    print("=== Fetching prices ===")
    fetch_prices(cfg, days=args.days)
    print("\n=== Fetching broker data ===")
    fetch_broker_summary(cfg)
    print("\n=== Fetching fundamentals ===")
    fetch_fundamentals(cfg)
    print("\n=== Fetching news ===")
    fetch_news(cfg)
    print("\n=== Done ===")


def cli():
    parser = argparse.ArgumentParser(description="IDX Research Assistant")
    parser.add_argument("--config", default="config.yaml", help="config file path")
    sub = parser.add_subparsers(dest="command", required=True)

    # fetch (prices)
    p = sub.add_parser("fetch", help="fetch daily price data from Stockbit")
    p.add_argument("--symbols", nargs="*", help="override watchlist")
    p.add_argument("--days", type=int, default=180, help="days of history (default: 180)")

    # fetch-brokers
    p = sub.add_parser("fetch-brokers", help="fetch broker summary + bandar detector")
    p.add_argument("--symbols", nargs="*", help="override watchlist")

    # fetch-insider
    p = sub.add_parser("fetch-insider", help="fetch insider / major holder filings")
    p.add_argument("--symbols", nargs="*", help="override watchlist")

    # fetch-companies
    sub.add_parser("fetch-companies", help="fetch full stock universe (sectors/subsectors)")

    # refresh-pool
    sub.add_parser("refresh-pool", help="rank all stocks by market cap and build top-N scan pool")

    # fetch-pool
    p = sub.add_parser("fetch-pool", help="fetch all data for scan pool symbols")
    p.add_argument("--days", type=int, default=180, help="days of price history")

    # fetch-fundamentals
    p = sub.add_parser("fetch-fundamentals", help="fetch key stats / ratios")
    p.add_argument("--symbols", nargs="*", help="override watchlist")

    # fetch-news
    p = sub.add_parser("fetch-news", help="fetch news from Stockbit stream")
    p.add_argument("--symbols", nargs="*", help="override watchlist")

    # fetch-all
    p = sub.add_parser("fetch-all", help="run full daily fetch pipeline")
    p.add_argument("--days", type=int, default=180, help="days of price history")

    # indicators
    p = sub.add_parser("indicators", help="compute technical indicators")
    p.add_argument("--symbols", nargs="*", help="override watchlist")

    # sr
    p = sub.add_parser("sr", help="detect support/resistance levels")
    p.add_argument("--symbols", nargs="*", help="override watchlist")

    # chart
    p = sub.add_parser("chart", help="render chart for a symbol")
    p.add_argument("symbol", help="ticker symbol (e.g. BBRI)")
    p.add_argument("--days", type=int, default=90, help="trading days to show")

    # fetch-macro
    p = sub.add_parser("fetch-macro", help="fetch macro indicators (USD/IDR, US 10Y)")
    p.add_argument("--days", type=int, default=180, help="days of USD/IDR history")

    # macro-signals
    sub.add_parser("macro-signals", help="show macro signals dashboard")

    # set-bi-rate
    p = sub.add_parser("set-bi-rate", help="manually set BI Rate")
    p.add_argument("rate", type=float, help="BI Rate in percent (e.g. 5.75)")
    p.add_argument("--date", default=None, help="effective date (default: today)")

    # --- Portfolio commands ---
    # buy
    p = sub.add_parser("buy", help="record a buy trade")
    p.add_argument("symbol", help="ticker (e.g. BBNI)")
    p.add_argument("lots", type=int, help="number of lots")
    p.add_argument("price", type=float, help="price per share")
    p.add_argument("--fees", type=float, default=0, help="transaction fees")
    p.add_argument("--date", default=None, help="trade date (default: today)")
    p.add_argument("--notes", default=None, help="optional notes")
    p.add_argument("--stop", type=float, default=None, help="stop loss price")
    p.add_argument("--tranches", type=int, default=None, help="total tranches planned")

    # sell
    p = sub.add_parser("sell", help="record a sell trade")
    p.add_argument("symbol", help="ticker (e.g. BBNI)")
    p.add_argument("lots", type=int, help="number of lots")
    p.add_argument("price", type=float, help="price per share")
    p.add_argument("--fees", type=float, default=0, help="transaction fees")
    p.add_argument("--date", default=None, help="trade date (default: today)")
    p.add_argument("--notes", default=None, help="optional notes")

    # portfolio
    sub.add_parser("portfolio", help="show current portfolio positions and P&L")

    # trades
    p = sub.add_parser("trades", help="show trade history")
    p.add_argument("--symbol", default=None, help="filter by symbol")

    # set-stop
    p = sub.add_parser("set-stop", help="set stop loss for a position")
    p.add_argument("symbol", help="ticker")
    p.add_argument("price", type=float, help="stop loss price")

    # backfill
    p = sub.add_parser("backfill", help="backfill historical prices and/or broker data")
    p.add_argument("--start", default="2020-01-01", help="start date (default: 2020-01-01)")
    p.add_argument("--end", default=None, help="end date (default: today)")
    p.add_argument("--prices", action="store_true", help="backfill prices only")
    p.add_argument("--brokers", action="store_true", help="backfill broker summary only")
    p.add_argument("--delay", type=float, default=0.5, help="pause between dates in seconds (default: 0.5)")

    # --- Agent commands ---
    # agent-chat
    sub.add_parser("agent-chat", help="interactive agent chat in terminal")

    # send-brief
    p = sub.add_parser("send-brief", help="generate EOD brief (print to stdout, optionally send via Telegram)")
    p.add_argument("--notify", action="store_true", help="also send via Telegram")

    # bot
    sub.add_parser("bot", help="start the Telegram bot")

    # --- US commands ---
    sub.add_parser("us-seed", help="seed US assets from Pluang reference JSON")
    sub.add_parser("us-seed-sectors", help="seed US sector/industry from yfinance")

    p = sub.add_parser("us-fetch", help="fetch US prices from Pluang")
    p.add_argument("--days", type=int, default=365, help="days of history (default: 365)")

    p = sub.add_parser("us-pipeline", help="run full US daily pipeline")
    p.add_argument("--days", type=int, default=365, help="days of history (default: 365)")

    p = sub.add_parser("us-scan", help="run US stock scanner")
    p.add_argument("--top", type=int, default=15, help="max results (default: 15)")

    args = parser.parse_args()
    cfg = load_config(args.config)

    commands = {
        "fetch": cmd_fetch,
        "fetch-brokers": cmd_fetch_brokers,
        "fetch-insider": cmd_fetch_insider,
        "fetch-companies": cmd_fetch_companies,
        "refresh-pool": cmd_refresh_pool,
        "fetch-pool": cmd_fetch_pool,
        "fetch-fundamentals": cmd_fetch_fundamentals,
        "fetch-news": cmd_fetch_news,
        "fetch-all": cmd_fetch_all,
        "indicators": cmd_indicators,
        "sr": cmd_sr,
        "chart": cmd_chart,
        "fetch-macro": cmd_fetch_macro,
        "macro-signals": cmd_macro_signals,
        "set-bi-rate": cmd_set_bi_rate,
        "buy": cmd_buy,
        "sell": cmd_sell,
        "portfolio": cmd_portfolio,
        "trades": cmd_trades,
        "set-stop": cmd_set_stop,
        "backfill": cmd_backfill,
        "agent-chat": cmd_agent_chat,
        "send-brief": cmd_send_brief,
        "bot": cmd_bot,
        "us-seed": cmd_us_seed,
        "us-seed-sectors": cmd_us_seed_sectors,
        "us-fetch": cmd_us_fetch,
        "us-pipeline": cmd_us_pipeline,
        "us-scan": cmd_us_scan,
    }
    commands[args.command](args, cfg)


if __name__ == "__main__":
    cli()
