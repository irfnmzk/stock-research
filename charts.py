"""Chart rendering with mplfinance."""

from pathlib import Path

import mplfinance as mpf
import pandas as pd

from db import get_db


def render_chart(cfg, symbol, days=90):
    """Render a 3-panel candlestick chart for a symbol."""
    db = get_db(cfg)

    rows = db.execute(
        "SELECT date, open, high, low, close, volume FROM prices "
        "WHERE symbol = ? ORDER BY date",
        (symbol,),
    ).fetchall()
    if not rows:
        print(f"No price data for {symbol}")
        return None

    df = pd.DataFrame(rows, columns=["Date", "Open", "High", "Low", "Close", "Volume"])
    df["Date"] = pd.to_datetime(df["Date"])
    df.set_index("Date", inplace=True)
    df = df.tail(days)

    # Load indicators
    ind_rows = db.execute(
        "SELECT date, ema20, ema50, ema200, rsi FROM indicators "
        "WHERE symbol = ? ORDER BY date",
        (symbol,),
    ).fetchall()

    add_plots = []
    if ind_rows:
        idf = pd.DataFrame(ind_rows, columns=["Date", "ema20", "ema50", "ema200", "rsi"])
        idf["Date"] = pd.to_datetime(idf["Date"])
        idf.set_index("Date", inplace=True)
        idf = idf.reindex(df.index)

        colors = {"ema20": "#2196F3", "ema50": "#FF9800", "ema200": "#E91E63"}
        for col, color in colors.items():
            if idf[col].notna().any():
                add_plots.append(mpf.make_addplot(idf[col], color=color, width=1))

        if idf["rsi"].notna().any():
            add_plots.append(mpf.make_addplot(idf["rsi"], panel=2, color="#9C27B0", ylabel="RSI"))

    # Foreign flow as bar chart on panel 2
    ff_rows = db.execute(
        "SELECT date, (COALESCE(foreign_buy, 0) - COALESCE(foreign_sell, 0)) as foreign_net "
        "FROM prices WHERE symbol = ? ORDER BY date",
        (symbol,),
    ).fetchall()

    if ff_rows:
        fdf = pd.DataFrame(ff_rows, columns=["Date", "foreign_net"])
        fdf["Date"] = pd.to_datetime(fdf["Date"])
        fdf.set_index("Date", inplace=True)
        fdf = fdf.reindex(df.index).fillna(0)
        colors_ff = ["#4CAF50" if v >= 0 else "#F44336" for v in fdf["foreign_net"]]
        add_plots.append(mpf.make_addplot(
            fdf["foreign_net"], panel=2, type="bar", color=colors_ff, ylabel="Foreign Net", width=0.7,
        ))

    # Whale score as line on panel 3 (if data exists)
    ws_rows = db.execute(
        "SELECT date, composite_score FROM whale_scores WHERE symbol = ? ORDER BY date",
        (symbol,),
    ).fetchall()
    if ws_rows:
        wdf = pd.DataFrame(ws_rows, columns=["Date", "whale"])
        wdf["Date"] = pd.to_datetime(wdf["Date"])
        wdf.set_index("Date", inplace=True)
        wdf = wdf.reindex(df.index)
        if wdf["whale"].notna().any():
            add_plots.append(mpf.make_addplot(
                wdf["whale"], panel=3, color="#FF9800", ylabel="Whale", width=1.2,
            ))

    # S/R lines — only nearest 3 supports and 3 resistances within visible range
    price_min = df["Low"].min()
    price_max = df["High"].max()
    current = df["Close"].iloc[-1]

    sr_rows = db.execute(
        "SELECT level, level_type, touch_count FROM support_resistance "
        "WHERE symbol = ? AND level BETWEEN ? AND ? ORDER BY level",
        (symbol, price_min * 0.95, price_max * 1.05),
    ).fetchall()

    supports = sorted(
        [r for r in sr_rows if r["level_type"] == "support" and r["level"] < current],
        key=lambda r: r["level"], reverse=True,
    )[:3]
    resistances = sorted(
        [r for r in sr_rows if r["level_type"] == "resistance" and r["level"] > current],
        key=lambda r: r["level"],
    )[:3]

    for sr in supports + resistances:
        color = "#4CAF50" if sr["level_type"] == "support" else "#F44336"
        hline_series = pd.Series(sr["level"], index=df.index)
        add_plots.append(mpf.make_addplot(hline_series, color=color, linestyle="--", width=0.7))

    out_dir = Path(cfg["charts"]["output_dir"])
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{symbol}.png"

    fig_w, fig_h = cfg["charts"]["figsize"]
    kwargs = dict(
        type="candle",
        style=cfg["charts"]["style"],
        volume=True,
        title=f"{symbol} - {days}d",
        figsize=(fig_w, fig_h),
        savefig=str(out_path),
        tight_layout=True,
    )
    if add_plots:
        kwargs["addplot"] = add_plots

    mpf.plot(df, **kwargs)
    print(f"Chart saved to {out_path}")
    db.close()
    return str(out_path)
