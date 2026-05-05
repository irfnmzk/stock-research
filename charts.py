"""Chart rendering with mplfinance."""

from pathlib import Path

import mplfinance as mpf
import pandas as pd

from db import get_db, get_us_db


def render_chart(cfg, symbol, days=90):
    """Render a candlestick chart: price + EMAs + S/R, volume, RSI, MACD."""
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
        "SELECT date, ema20, ema50, ema200, rsi, macd, macd_signal, macd_hist FROM indicators "
        "WHERE symbol = ? ORDER BY date",
        (symbol,),
    ).fetchall()

    add_plots = []
    if ind_rows:
        idf = pd.DataFrame(ind_rows, columns=["Date", "ema20", "ema50", "ema200", "rsi", "macd", "macd_signal", "macd_hist"])
        idf["Date"] = pd.to_datetime(idf["Date"])
        idf.set_index("Date", inplace=True)
        idf = idf.reindex(df.index)

        colors = {"ema20": "#2196F3", "ema50": "#FF9800", "ema200": "#E91E63"}
        for col, color in colors.items():
            if idf[col].notna().any():
                add_plots.append(mpf.make_addplot(idf[col], color=color, width=1))

        if idf["rsi"].notna().any():
            add_plots.append(mpf.make_addplot(idf["rsi"], panel=2, color="#9C27B0", ylabel="RSI"))

        if idf["macd"].notna().any():
            add_plots.append(mpf.make_addplot(idf["macd"], panel=3, color="#2196F3", ylabel="MACD", width=0.8))
            add_plots.append(mpf.make_addplot(idf["macd_signal"], panel=3, color="#FF9800", width=0.8))
            macd_colors = ["#4CAF50" if v >= 0 else "#F44336" for v in idf["macd_hist"].fillna(0)]
            add_plots.append(mpf.make_addplot(idf["macd_hist"], panel=3, type="bar", color=macd_colors, width=0.7))

    out_dir = Path(cfg["charts"]["output_dir"])
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{symbol}.png"

    fig_w, fig_h = cfg["charts"]["figsize"]
    kwargs = dict(
        type="candle",
        style=cfg["charts"]["style"],
        volume=True,
        figsize=(fig_w, fig_h),
        tight_layout=True,
        returnfig=True,
        panel_ratios=(4, 1.5, 1.5, 1.5),
    )
    if add_plots:
        kwargs["addplot"] = add_plots

    fig, axes = mpf.plot(df, **kwargs)

    # Style panel labels: clear, outside the plot area
    panel_labels = {0: symbol, 1: "Vol", 2: "RSI", 3: "MACD"}
    for ax in axes:
        ax.yaxis.label.set_fontsize(9)
        ax.yaxis.label.set_fontweight("bold")
    for panel_idx, label in panel_labels.items():
        ax = axes[panel_idx * 2] if panel_idx * 2 < len(axes) else None
        if ax:
            ax.set_ylabel(label, fontsize=10, fontweight="bold", rotation=0, labelpad=35, va="center")

    fig.savefig(str(out_path), dpi=150, bbox_inches="tight")
    import matplotlib.pyplot as plt
    plt.close(fig)

    print(f"Chart saved to {out_path}")
    db.close()
    return str(out_path)


def render_chart_us(cfg, ticker, days=90):
    """Render a candlestick chart for a US stock: price + EMAs + S/R, RSI, MACD."""
    db = get_us_db()

    rows = db.execute(
        "SELECT date, open, high, low, close FROM prices "
        "WHERE ticker = ? ORDER BY date",
        (ticker,),
    ).fetchall()
    if not rows:
        print(f"No price data for {ticker}")
        db.close()
        return None

    df = pd.DataFrame(rows, columns=["Date", "Open", "High", "Low", "Close"])
    df["Date"] = pd.to_datetime(df["Date"])
    df.set_index("Date", inplace=True)
    df = df.tail(days)

    ind_rows = db.execute(
        "SELECT date, ema10, ema21, ema50, ema200, rsi, macd, macd_signal, macd_hist "
        "FROM indicators WHERE ticker = ? ORDER BY date",
        (ticker,),
    ).fetchall()

    add_plots = []
    has_rsi = False
    has_macd = False
    next_panel = 1

    if ind_rows:
        idf = pd.DataFrame(ind_rows, columns=["Date", "ema10", "ema21", "ema50", "ema200", "rsi", "macd", "macd_signal", "macd_hist"])
        idf["Date"] = pd.to_datetime(idf["Date"])
        idf.set_index("Date", inplace=True)
        idf = idf.reindex(df.index)

        colors = {"ema10": "#00BCD4", "ema21": "#2196F3", "ema50": "#FF9800", "ema200": "#E91E63"}
        for col, color in colors.items():
            if idf[col].notna().any():
                add_plots.append(mpf.make_addplot(idf[col], color=color, width=1))

        if idf["rsi"].notna().any():
            has_rsi = True
            rsi_panel = next_panel
            next_panel += 1
            add_plots.append(mpf.make_addplot(idf["rsi"], panel=rsi_panel, color="#9C27B0", ylabel="RSI"))

        if idf["macd"].notna().any():
            has_macd = True
            macd_panel = next_panel
            next_panel += 1
            add_plots.append(mpf.make_addplot(idf["macd"], panel=macd_panel, color="#2196F3", ylabel="MACD", width=0.8))
            add_plots.append(mpf.make_addplot(idf["macd_signal"], panel=macd_panel, color="#FF9800", width=0.8))
            macd_colors = ["#4CAF50" if v >= 0 else "#F44336" for v in idf["macd_hist"].fillna(0)]
            add_plots.append(mpf.make_addplot(idf["macd_hist"], panel=macd_panel, type="bar", color=macd_colors, width=0.7))

    out_dir = Path(cfg["charts"]["output_dir"])
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{ticker}.png"

    fig_w, fig_h = cfg["charts"]["figsize"]
    panel_ratios = [4] + [1.5] * (next_panel - 1)
    kwargs = dict(
        type="candle",
        style=cfg["charts"]["style"],
        volume=False,
        figsize=(fig_w, fig_h),
        tight_layout=True,
        returnfig=True,
    )
    if len(panel_ratios) > 1:
        kwargs["panel_ratios"] = tuple(panel_ratios)
    if add_plots:
        kwargs["addplot"] = add_plots

    fig, axes = mpf.plot(df, **kwargs)

    panel_labels = {0: ticker}
    if has_rsi:
        panel_labels[rsi_panel] = "RSI"
    if has_macd:
        panel_labels[macd_panel] = "MACD"
    for ax in axes:
        ax.yaxis.label.set_fontsize(9)
        ax.yaxis.label.set_fontweight("bold")
    for panel_idx, label in panel_labels.items():
        ax = axes[panel_idx * 2] if panel_idx * 2 < len(axes) else None
        if ax:
            ax.set_ylabel(label, fontsize=10, fontweight="bold", rotation=0, labelpad=35, va="center")

    fig.savefig(str(out_path), dpi=150, bbox_inches="tight")
    import matplotlib.pyplot as plt
    plt.close(fig)

    print(f"Chart saved to {out_path}")
    db.close()
    return str(out_path)
