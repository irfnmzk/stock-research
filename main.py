"""IDX Research Assistant - CLI entry point."""

import argparse
import sys
from pathlib import Path

import yaml


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


def cmd_screen(args, cfg):
    from screener import run_screener
    run_screener(cfg, rule=args.rule, use_pool=args.pool)


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


def cmd_pipeline_morning(args, cfg):
    """Run full morning brief pipeline."""
    from reports import get_morning_brief_data, print_pipeline_json
    import json

    print("=== Morning Brief Pipeline ===")
    data = get_morning_brief_data(cfg)
    print_pipeline_json(data)


def cmd_report_morning(args, cfg):
    """One-shot morning report: fetch-all + pipeline-morning."""
    from fetcher import fetch_prices, fetch_broker_summary, fetch_fundamentals
    from news import fetch_news
    from reports import get_morning_brief_data, print_pipeline_json

    print("=== Fetch Phase ===")
    fetch_prices(cfg, days=args.days)
    fetch_broker_summary(cfg)
    fetch_fundamentals(cfg)
    fetch_news(cfg)

    print("\n=== Morning Brief Pipeline ===")
    data = get_morning_brief_data(cfg)
    print_pipeline_json(data)


def cmd_pipeline_eod(args, cfg):
    """Run full EOD report pipeline.

    Steps:
      1. Compute indicators (all watchlist)
      2. Compute whale scores
      3. Detect S/R levels
      4. Compute macro regime
      5. Compute sector rotation
      6. Run screener (pool)
      7. Compute signal scores (with macro + sector)
      8. Generate charts (watchlist + top hits)
      9. Assemble EOD report data
      10. Print JSON for LLM layer
    """
    _run_eod_pipeline(args, cfg)


def cmd_report_eod(args, cfg):
    """One-shot EOD report: fetch-all + pipeline-eod in a single command."""
    from fetcher import fetch_prices, fetch_broker_summary, fetch_fundamentals
    from news import fetch_news

    print("=== Fetch Phase ===")
    fetch_prices(cfg, days=args.fetch_days)
    fetch_broker_summary(cfg)
    fetch_fundamentals(cfg)
    fetch_news(cfg)

    print("\n=== EOD Pipeline ===")
    _run_eod_pipeline(args, cfg)


def _run_eod_pipeline(args, cfg):
    """Shared EOD pipeline logic used by both pipeline-eod and report-eod."""
    from indicators import compute_all as compute_indicators
    from whale import compute_all as compute_whales
    from support_resistance import detect_all
    from sector import compute_rotation
    from reports import get_eod_report_data, print_pipeline_json
    from charts import render_chart
    from db import get_db

    # Steps 1-3: compute derived data
    print("\n--- Computing indicators ---")
    compute_indicators(cfg)

    print("\n--- Computing whale scores ---")
    compute_whales(cfg)

    print("\n--- Detecting S/R levels ---")
    detect_all(cfg)

    print("\n--- Computing sector rotation ---")
    db = get_db(cfg)
    compute_rotation(cfg, db)
    db.close()

    # Steps 4-8: assemble report (macro, screener, signals all inside)
    print("\n--- Assembling report data ---")
    data = get_eod_report_data(cfg)

    # Generate charts for watchlist
    print("\n--- Generating charts ---")
    chart_paths = []
    symbols = [s.replace(".JK", "") for s in cfg["watchlist"]]
    for symbol in symbols:
        try:
            path = render_chart(cfg, symbol=symbol, days=args.days)
            if path:
                chart_paths.append(path)
        except Exception as e:
            print(f"  Chart error for {symbol}: {e}")

    # Also chart top screener hits not in watchlist
    hit_symbols = set()
    for rule_name, hits in data.get("screener_hits", {}).items():
        for h in hits:
            hit_symbols.add(h["symbol"])
    extra = [s for s in hit_symbols if s not in set(symbols)][:5]
    for symbol in extra:
        try:
            path = render_chart(cfg, symbol=symbol, days=args.days)
            if path:
                chart_paths.append(path)
        except Exception as e:
            print(f"  Chart error for {symbol}: {e}")

    data["chart_paths"] = chart_paths

    print("\n--- Pipeline output ---")
    print_pipeline_json(data)


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

    # pipeline-morning
    sub.add_parser("pipeline-morning", help="run morning brief pipeline (macro + watchlist + portfolio)")

    # report-morning (one-shot: fetch + morning pipeline)
    p = sub.add_parser("report-morning", help="one-shot morning report: fetch + pipeline")
    p.add_argument("--days", type=int, default=180, help="days of price history to fetch")

    # pipeline-eod
    p = sub.add_parser("pipeline-eod", help="run full EOD report pipeline")
    p.add_argument("--days", type=int, default=90, help="chart days to show")

    # report-eod (one-shot: fetch + eod pipeline)
    p = sub.add_parser("report-eod", help="one-shot EOD report: fetch + pipeline")
    p.add_argument("--days", type=int, default=90, help="chart days to show")
    p.add_argument("--fetch-days", type=int, default=180, help="days of price history to fetch")

    # indicators
    p = sub.add_parser("indicators", help="compute technical indicators")
    p.add_argument("--symbols", nargs="*", help="override watchlist")

    # sr
    p = sub.add_parser("sr", help="detect support/resistance levels")
    p.add_argument("--symbols", nargs="*", help="override watchlist")

    # screen
    p = sub.add_parser("screen", help="run screener")
    p.add_argument("--rule", help="run specific rule only")
    p.add_argument("--pool", action="store_true", help="screen against scan pool instead of watchlist")

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
        "pipeline-morning": cmd_pipeline_morning,
        "report-morning": cmd_report_morning,
        "pipeline-eod": cmd_pipeline_eod,
        "report-eod": cmd_report_eod,
        "indicators": cmd_indicators,
        "sr": cmd_sr,
        "screen": cmd_screen,
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
    }
    commands[args.command](args, cfg)


if __name__ == "__main__":
    cli()
