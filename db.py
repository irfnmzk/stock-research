"""SQLite schema and helper functions."""

import sqlite3
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS prices (
    symbol              TEXT NOT NULL,
    date                TEXT NOT NULL,
    open                REAL,
    high                REAL,
    low                 REAL,
    close               REAL,
    volume              INTEGER,
    value               REAL,
    frequency           INTEGER,
    foreign_buy         REAL,
    foreign_sell        REAL,
    market_cap          REAL,
    shares_outstanding  INTEGER,
    freq_analyzer       TEXT,
    PRIMARY KEY (symbol, date)
);

CREATE TABLE IF NOT EXISTS broker_summary (
    symbol      TEXT NOT NULL,
    date        TEXT NOT NULL,
    broker_code TEXT NOT NULL,
    broker_type TEXT,
    buy_lot     INTEGER,
    buy_value   REAL,
    sell_lot    INTEGER,
    sell_value  REAL,
    net_lot     INTEGER,
    net_value   REAL,
    avg_price   REAL,
    freq        INTEGER,
    PRIMARY KEY (symbol, date, broker_code)
);

CREATE TABLE IF NOT EXISTS bandar_detector (
    symbol          TEXT NOT NULL,
    date            TEXT NOT NULL,
    top1_net        REAL,
    top3_net        REAL,
    top5_net        REAL,
    top10_net       REAL,
    top1_accdist    REAL,
    top3_accdist    REAL,
    top5_accdist    REAL,
    top10_accdist   REAL,
    total_buyers    INTEGER,
    total_sellers   INTEGER,
    total_value     REAL,
    PRIMARY KEY (symbol, date)
);

CREATE TABLE IF NOT EXISTS insider (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol          TEXT NOT NULL,
    name            TEXT NOT NULL,
    date            TEXT NOT NULL,
    action_type     TEXT,
    previous_shares INTEGER,
    current_shares  INTEGER,
    change_shares   INTEGER,
    price           REAL,
    nationality     TEXT,
    badge           TEXT,
    UNIQUE (symbol, name, date)
);

CREATE TABLE IF NOT EXISTS companies (
    symbol          TEXT PRIMARY KEY,
    name            TEXT,
    sector_id       INTEGER,
    sector_name     TEXT,
    subsector_id    INTEGER,
    subsector_name  TEXT,
    market_cap      REAL,
    last_price      REAL,
    avg_volume      INTEGER,
    tradeable       INTEGER DEFAULT 1
);

CREATE TABLE IF NOT EXISTS fundamentals (
    symbol          TEXT NOT NULL,
    date            TEXT NOT NULL,
    pe_ttm          REAL,
    pe_forward      REAL,
    pbv             REAL,
    ps_ttm          REAL,
    pcf_ttm         REAL,
    ev_ebitda       REAL,
    peg             REAL,
    earnings_yield  REAL,
    dividend_yield  REAL,
    PRIMARY KEY (symbol, date)
);

CREATE TABLE IF NOT EXISTS news (
    stream_id       TEXT PRIMARY KEY,
    symbol_queried  TEXT,
    title           TEXT NOT NULL,
    content         TEXT,
    source          TEXT,
    url             TEXT,
    published_at    TEXT,
    topics          TEXT,
    total_likes     INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS indicators (
    symbol       TEXT NOT NULL,
    date         TEXT NOT NULL,
    ema20        REAL,
    ema50        REAL,
    ema200       REAL,
    rsi          REAL,
    macd         REAL,
    macd_signal  REAL,
    macd_hist    REAL,
    bb_upper     REAL,
    bb_lower     REAL,
    bb_width     REAL,
    atr          REAL,
    volume_ratio REAL,
    smart_broker_streak   INTEGER,
    bb_squeeze_days       INTEGER,
    foreign_flow_reversal INTEGER,
    accdist_slope_5d      REAL,
    accdist_slope_10d     REAL,
    accdist_slope_20d     REAL,
    PRIMARY KEY (symbol, date)
);

CREATE TABLE IF NOT EXISTS support_resistance (
    symbol         TEXT NOT NULL,
    level          REAL NOT NULL,
    level_type     TEXT NOT NULL,
    touch_count    INTEGER DEFAULT 1,
    last_touched   TEXT,
    strength_score REAL,
    PRIMARY KEY (symbol, level, level_type)
);

CREATE TABLE IF NOT EXISTS whale_scores (
    symbol             TEXT NOT NULL,
    date               TEXT NOT NULL,
    foreign_flow_score REAL,
    broker_score       REAL,
    composite_score    REAL,
    PRIMARY KEY (symbol, date)
);

CREATE TABLE IF NOT EXISTS sector_rotation (
    sector   TEXT NOT NULL,
    date     TEXT NOT NULL,
    pct_5d   REAL,
    pct_10d  REAL,
    pct_20d  REAL,
    rank_5d  INTEGER,
    rank_10d INTEGER,
    rank_20d INTEGER,
    momentum REAL,
    PRIMARY KEY (sector, date)
);

CREATE TABLE IF NOT EXISTS signals (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol      TEXT NOT NULL,
    date        TEXT NOT NULL,
    signal_type TEXT NOT NULL,
    direction   TEXT NOT NULL,
    score       REAL,
    description TEXT
);

CREATE TABLE IF NOT EXISTS alerts_sent (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol     TEXT NOT NULL,
    alert_type TEXT NOT NULL,
    sent_at    TEXT NOT NULL,
    message    TEXT
);

CREATE TABLE IF NOT EXISTS capital (
    id              INTEGER PRIMARY KEY CHECK (id = 1),
    total           REAL NOT NULL,
    risk_per_trade  REAL DEFAULT 0.02,
    max_heat        REAL DEFAULT 0.08,
    updated_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS capital_log (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    date            TEXT NOT NULL,
    action          TEXT NOT NULL,  -- 'deposit', 'withdraw', 'adjust'
    amount          REAL NOT NULL,
    balance_after   REAL NOT NULL,
    notes           TEXT
);

CREATE TABLE IF NOT EXISTS scan_pool (
    symbol      TEXT PRIMARY KEY,
    market_cap  REAL,
    rank        INTEGER,
    updated_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS trades (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol          TEXT NOT NULL,
    date            TEXT NOT NULL,
    action          TEXT NOT NULL,  -- 'buy' or 'sell'
    lots            INTEGER NOT NULL,
    price           REAL NOT NULL,
    fees            REAL DEFAULT 0,
    notes           TEXT
);

CREATE TABLE IF NOT EXISTS positions (
    symbol          TEXT PRIMARY KEY,
    avg_cost        REAL NOT NULL,
    total_lots      INTEGER NOT NULL,
    stop_loss       REAL,
    tranches_planned INTEGER DEFAULT 4,
    tranches_done   INTEGER DEFAULT 0,
    notes           TEXT
);

CREATE TABLE IF NOT EXISTS relative_strength (
    symbol      TEXT NOT NULL,
    date        TEXT NOT NULL,
    vs_ihsg_5d  REAL,
    vs_ihsg_10d REAL,
    vs_ihsg_20d REAL,
    vs_sector_5d  REAL,
    vs_sector_10d REAL,
    vs_sector_20d REAL,
    PRIMARY KEY (symbol, date)
);

CREATE INDEX IF NOT EXISTS idx_relative_strength ON relative_strength(symbol, date);
CREATE INDEX IF NOT EXISTS idx_trades_symbol ON trades(symbol, date);
CREATE INDEX IF NOT EXISTS idx_prices_symbol ON prices(symbol, date);
CREATE INDEX IF NOT EXISTS idx_broker_summary_symbol ON broker_summary(symbol, date);
CREATE INDEX IF NOT EXISTS idx_bandar_symbol ON bandar_detector(symbol, date);
CREATE INDEX IF NOT EXISTS idx_insider_symbol ON insider(symbol, date);
CREATE INDEX IF NOT EXISTS idx_fundamentals_symbol ON fundamentals(symbol, date);
CREATE INDEX IF NOT EXISTS idx_news_published ON news(published_at);
CREATE INDEX IF NOT EXISTS idx_news_symbol ON news(symbol_queried);
CREATE INDEX IF NOT EXISTS idx_indicators_symbol ON indicators(symbol, date);
CREATE INDEX IF NOT EXISTS idx_signals_symbol ON signals(symbol, date);
CREATE INDEX IF NOT EXISTS idx_alerts_sent_symbol ON alerts_sent(symbol, sent_at);

CREATE TABLE IF NOT EXISTS signal_events (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol          TEXT NOT NULL,
    date            TEXT NOT NULL,
    signal_type     TEXT NOT NULL,
    broker_code     TEXT,
    magnitude       REAL,
    close           REAL,
    volume_ratio    REAL,
    regime          TEXT,
    trend           TEXT,
    fwd_5d          REAL,
    fwd_10d         REAL,
    fwd_20d         REAL,
    filled_through  INTEGER DEFAULT 0,
    meta            TEXT
);

CREATE INDEX IF NOT EXISTS idx_se_type_symbol ON signal_events(signal_type, symbol);
CREATE INDEX IF NOT EXISTS idx_se_broker ON signal_events(broker_code, symbol)
    WHERE broker_code IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_se_date ON signal_events(date);
CREATE INDEX IF NOT EXISTS idx_se_regime ON signal_events(regime, signal_type);
CREATE UNIQUE INDEX IF NOT EXISTS idx_se_dedup ON signal_events(symbol, date, signal_type, broker_code);

CREATE TABLE IF NOT EXISTS signal_base_rates (
    signal_type     TEXT NOT NULL,
    direction       TEXT NOT NULL DEFAULT '',
    symbol          TEXT,
    broker_code     TEXT,
    sample_size     INTEGER,
    hit_rate_5d     REAL,
    hit_rate_10d    REAL,
    hit_rate_20d    REAL,
    avg_return_5d   REAL,
    avg_return_10d  REAL,
    avg_return_20d  REAL,
    median_return_5d  REAL,
    median_return_10d REAL,
    median_return_20d REAL,
    last_computed   TEXT,
    PRIMARY KEY (signal_type, direction, symbol, broker_code)
);

CREATE TABLE IF NOT EXISTS ticker_thesis (
    symbol      TEXT PRIMARY KEY,
    thesis      TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS session_summary (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    summary     TEXT NOT NULL,
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS conversation_turns (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id  INTEGER NOT NULL,
    role        TEXT NOT NULL,
    content     TEXT NOT NULL,
    created_at  TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_conv_session ON conversation_turns(session_id);

CREATE TABLE IF NOT EXISTS watchlist (
    symbol      TEXT PRIMARY KEY,
    added_at    TEXT NOT NULL,
    notes       TEXT
);
"""


def _migrate(conn: sqlite3.Connection):
    """Add columns that may be missing from older databases."""
    new_cols = [
        ("indicators", "smart_broker_streak", "INTEGER"),
        ("indicators", "bb_squeeze_days", "INTEGER"),
        ("indicators", "foreign_flow_reversal", "INTEGER"),
        ("indicators", "accdist_slope_5d", "REAL"),
        ("indicators", "accdist_slope_10d", "REAL"),
        ("indicators", "accdist_slope_20d", "REAL"),
    ]
    existing = {}
    for table, col, col_type in new_cols:
        if table not in existing:
            rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
            existing[table] = {r[1] for r in rows}
        if col not in existing[table]:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {col_type}")

    # Add direction column to signal_base_rates if missing
    sbr_cols = {r[1] for r in conn.execute("PRAGMA table_info(signal_base_rates)").fetchall()}
    if "direction" not in sbr_cols:
        conn.execute("DROP TABLE IF EXISTS signal_base_rates")
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS signal_base_rates (
                signal_type     TEXT NOT NULL,
                direction       TEXT NOT NULL DEFAULT '',
                symbol          TEXT,
                broker_code     TEXT,
                sample_size     INTEGER,
                hit_rate_5d     REAL,
                hit_rate_10d    REAL,
                hit_rate_20d    REAL,
                avg_return_5d   REAL,
                avg_return_10d  REAL,
                avg_return_20d  REAL,
                median_return_5d  REAL,
                median_return_10d REAL,
                median_return_20d REAL,
                last_computed   TEXT,
                PRIMARY KEY (signal_type, direction, symbol, broker_code)
            );
        """)

    conn.commit()


def get_db(cfg) -> sqlite3.Connection:
    """Open (and initialize) the database."""
    db_path = Path(cfg["db"]["path"])
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path), timeout=30)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    _migrate(conn)
    return conn


US_SCHEMA = """
CREATE TABLE IF NOT EXISTS assets (
    pluang_id   INTEGER PRIMARY KEY,
    ticker      TEXT UNIQUE NOT NULL,
    name        TEXT,
    quote_type  TEXT,
    sector      TEXT,
    industry    TEXT,
    sector_etf  TEXT,
    market_cap  REAL,
    active      INTEGER DEFAULT 1
);

CREATE TABLE IF NOT EXISTS prices (
    ticker  TEXT NOT NULL,
    date    TEXT NOT NULL,
    open    REAL,
    high    REAL,
    low     REAL,
    close   REAL,
    PRIMARY KEY (ticker, date)
);

CREATE TABLE IF NOT EXISTS indicators (
    ticker      TEXT NOT NULL,
    date        TEXT NOT NULL,
    ema10       REAL,
    ema21       REAL,
    ema50       REAL,
    ema200      REAL,
    rsi         REAL,
    macd        REAL,
    macd_signal REAL,
    macd_hist   REAL,
    bb_upper    REAL,
    bb_lower    REAL,
    bb_width    REAL,
    atr         REAL,
    adr_pct     REAL,
    PRIMARY KEY (ticker, date)
);

CREATE TABLE IF NOT EXISTS support_resistance (
    ticker          TEXT NOT NULL,
    level           REAL NOT NULL,
    level_type      TEXT NOT NULL,
    touch_count     INTEGER DEFAULT 1,
    last_touched    TEXT,
    strength_score  REAL,
    PRIMARY KEY (ticker, level, level_type)
);

CREATE TABLE IF NOT EXISTS relative_strength (
    ticker              TEXT NOT NULL,
    date                TEXT NOT NULL,
    rs_vs_spy_10d       REAL,
    rs_vs_spy_20d       REAL,
    rs_vs_sector_10d    REAL,
    rs_vs_sector_20d    REAL,
    PRIMARY KEY (ticker, date)
);

CREATE TABLE IF NOT EXISTS sector_rotation (
    sector_etf  TEXT NOT NULL,
    date        TEXT NOT NULL,
    pct_5d      REAL,
    pct_10d     REAL,
    pct_20d     REAL,
    momentum    REAL,
    rank        INTEGER,
    PRIMARY KEY (sector_etf, date)
);

CREATE TABLE IF NOT EXISTS signal_events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker      TEXT NOT NULL,
    date        TEXT NOT NULL,
    signal_type TEXT NOT NULL,
    direction   TEXT NOT NULL,
    magnitude   REAL,
    close       REAL,
    meta        TEXT,
    fwd_5d      REAL,
    fwd_10d     REAL,
    fwd_20d     REAL
);

CREATE TABLE IF NOT EXISTS signal_base_rates (
    signal_type     TEXT PRIMARY KEY,
    sample_size     INTEGER,
    hit_rate_5d     REAL,
    hit_rate_10d    REAL,
    hit_rate_20d    REAL,
    avg_return_5d   REAL,
    avg_return_10d  REAL,
    avg_return_20d  REAL,
    last_computed   TEXT
);

CREATE INDEX IF NOT EXISTS idx_us_prices ON prices(ticker, date);
CREATE INDEX IF NOT EXISTS idx_us_indicators ON indicators(ticker, date);
CREATE INDEX IF NOT EXISTS idx_us_rs ON relative_strength(ticker, date);
CREATE INDEX IF NOT EXISTS idx_us_signals ON signal_events(ticker, date);
CREATE UNIQUE INDEX IF NOT EXISTS idx_us_signals_dedup ON signal_events(ticker, date, signal_type);
"""


def get_us_db() -> sqlite3.Connection:
    """Open (and initialize) the US stock database."""
    db_path = Path(__file__).parent / "data" / "us.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path), timeout=30)
    conn.row_factory = sqlite3.Row
    conn.executescript(US_SCHEMA)
    return conn


def get_watchlist(cfg) -> list[str]:
    """Get watchlist symbols from DB, seeding from config on first use."""
    db = get_db(cfg)
    rows = db.execute("SELECT symbol FROM watchlist ORDER BY added_at").fetchall()

    if not rows:
        from datetime import datetime
        now = datetime.now().isoformat()
        for s in cfg.get("watchlist", []):
            symbol = s.replace(".JK", "")
            db.execute(
                "INSERT OR IGNORE INTO watchlist (symbol, added_at) VALUES (?, ?)",
                (symbol, now),
            )
        db.commit()
        rows = db.execute("SELECT symbol FROM watchlist ORDER BY added_at").fetchall()

    db.close()
    return [r["symbol"] for r in rows]
