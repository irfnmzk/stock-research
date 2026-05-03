"""Portfolio tracker - trades, positions, and P&L."""

import sqlite3
from datetime import date as dt_date
from db import get_db


def add_trade(conn: sqlite3.Connection, symbol: str, action: str, lots: int,
              price: float, fees: float = 0, date: str = None, notes: str = None,
              stop_loss: float = None, tranches_planned: int = None):
    """Record a trade and update the position."""
    symbol = symbol.upper()
    action = action.lower()
    if action not in ("buy", "sell"):
        raise ValueError("action must be 'buy' or 'sell'")
    if date is None:
        date = dt_date.today().isoformat()

    conn.execute(
        "INSERT INTO trades (symbol, date, action, lots, price, fees, notes) VALUES (?,?,?,?,?,?,?)",
        (symbol, date, action, lots, price, fees, notes)
    )

    # Recalculate position from all trades
    _recalc_position(conn, symbol, stop_loss=stop_loss, tranches_planned=tranches_planned)
    conn.commit()
    print(f"Recorded: {action.upper()} {lots} lots {symbol} @ {price:,.0f} on {date}")


def _recalc_position(conn: sqlite3.Connection, symbol: str,
                     stop_loss: float = None, tranches_planned: int = None):
    """Recalculate position from trade history."""
    rows = conn.execute(
        "SELECT action, lots, price, fees FROM trades WHERE symbol = ? ORDER BY date, id",
        (symbol,)
    ).fetchall()

    total_lots = 0
    total_cost = 0.0  # cost basis (excluding sold shares)
    buy_count = 0

    for row in rows:
        action, lots, price, fees = row["action"], row["lots"], row["price"], row["fees"]
        if action == "buy":
            total_lots += lots
            total_cost += (lots * 100 * price) + fees
            buy_count += 1
        elif action == "sell":
            if total_lots > 0:
                # Reduce cost proportionally (average cost method)
                avg = total_cost / (total_lots * 100) if total_lots > 0 else 0
                total_lots -= lots
                total_cost = avg * total_lots * 100
            else:
                total_lots -= lots
                total_cost = 0

    if total_lots <= 0:
        conn.execute("DELETE FROM positions WHERE symbol = ?", (symbol,))
        return

    avg_cost = total_cost / (total_lots * 100)

    # Preserve existing stop_loss/tranches if not provided
    existing = conn.execute("SELECT stop_loss, tranches_planned FROM positions WHERE symbol = ?",
                            (symbol,)).fetchone()
    if stop_loss is None and existing:
        stop_loss = existing["stop_loss"]
    if tranches_planned is None and existing:
        tranches_planned = existing["tranches_planned"]
    if tranches_planned is None:
        tranches_planned = 4

    conn.execute("""
        INSERT INTO positions (symbol, avg_cost, total_lots, stop_loss, tranches_planned, tranches_done)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(symbol) DO UPDATE SET
            avg_cost = excluded.avg_cost,
            total_lots = excluded.total_lots,
            stop_loss = COALESCE(excluded.stop_loss, positions.stop_loss),
            tranches_planned = COALESCE(excluded.tranches_planned, positions.tranches_planned),
            tranches_done = excluded.tranches_done
    """, (symbol, avg_cost, total_lots, stop_loss, tranches_planned, buy_count))


def set_stop_loss(conn: sqlite3.Connection, symbol: str, stop_loss: float):
    """Update stop loss for a position."""
    symbol = symbol.upper()
    result = conn.execute("UPDATE positions SET stop_loss = ? WHERE symbol = ?",
                          (stop_loss, symbol))
    if result.rowcount == 0:
        print(f"No open position for {symbol}")
    else:
        conn.commit()
        print(f"Stop loss for {symbol} set to {stop_loss:,.0f}")


def get_portfolio(conn: sqlite3.Connection) -> list[dict]:
    """Get all open positions with current price and P&L."""
    positions = conn.execute("""
        SELECT p.symbol, p.avg_cost, p.total_lots, p.stop_loss,
               p.tranches_planned, p.tranches_done, p.notes
        FROM positions p
        WHERE p.total_lots > 0
        ORDER BY p.symbol
    """).fetchall()

    result = []
    for pos in positions:
        symbol = pos["symbol"]
        # Get latest price
        latest = conn.execute(
            "SELECT close, date FROM prices WHERE symbol = ? ORDER BY date DESC LIMIT 1",
            (symbol,)
        ).fetchone()

        current_price = latest["close"] if latest else None
        price_date = latest["date"] if latest else None

        avg_cost = pos["avg_cost"]
        total_lots = pos["total_lots"]
        shares = total_lots * 100
        market_value = current_price * shares if current_price else None
        cost_basis = avg_cost * shares
        unrealized_pnl = (market_value - cost_basis) if market_value else None
        pnl_pct = ((current_price / avg_cost) - 1) * 100 if current_price and avg_cost > 0 else None

        # Distance to stop loss
        stop_distance = None
        if pos["stop_loss"] and current_price:
            stop_distance = ((current_price / pos["stop_loss"]) - 1) * 100

        result.append({
            "symbol": symbol,
            "avg_cost": avg_cost,
            "total_lots": total_lots,
            "shares": shares,
            "current_price": current_price,
            "price_date": price_date,
            "cost_basis": cost_basis,
            "market_value": market_value,
            "unrealized_pnl": unrealized_pnl,
            "pnl_pct": pnl_pct,
            "stop_loss": pos["stop_loss"],
            "stop_distance_pct": stop_distance,
            "tranches_planned": pos["tranches_planned"],
            "tranches_done": pos["tranches_done"],
            "notes": pos["notes"],
        })

    return result


def get_trade_history(conn: sqlite3.Connection, symbol: str = None) -> list[dict]:
    """Get trade history, optionally filtered by symbol."""
    if symbol:
        rows = conn.execute(
            "SELECT * FROM trades WHERE symbol = ? ORDER BY date DESC, id DESC",
            (symbol.upper(),)
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM trades ORDER BY date DESC, id DESC"
        ).fetchall()
    return [dict(r) for r in rows]


def show_portfolio(cfg):
    """Print portfolio summary to stdout."""
    conn = get_db(cfg)
    positions = get_portfolio(conn)
    conn.close()

    if not positions:
        print("No open positions.")
        return

    total_cost = 0
    total_market = 0

    print(f"{'Symbol':<8} {'Lots':>5} {'Avg Cost':>10} {'Current':>10} {'P&L %':>8} {'P&L IDR':>14} {'Stop':>8} {'Tranche':>8}")
    print("-" * 82)

    for p in positions:
        pnl_str = f"{p['pnl_pct']:+.1f}%" if p['pnl_pct'] is not None else "N/A"
        pnl_idr = f"{p['unrealized_pnl']:+,.0f}" if p['unrealized_pnl'] is not None else "N/A"
        current = f"{p['current_price']:,.0f}" if p['current_price'] else "N/A"
        stop = f"{p['stop_loss']:,.0f}" if p['stop_loss'] else "-"
        tranche = f"{p['tranches_done']}/{p['tranches_planned']}"

        print(f"{p['symbol']:<8} {p['total_lots']:>5} {p['avg_cost']:>10,.0f} {current:>10} {pnl_str:>8} {pnl_idr:>14} {stop:>8} {tranche:>8}")

        total_cost += p['cost_basis']
        if p['market_value']:
            total_market += p['market_value']

    print("-" * 82)
    total_pnl = total_market - total_cost
    total_pct = ((total_market / total_cost) - 1) * 100 if total_cost > 0 else 0
    print(f"{'TOTAL':<8} {'':>5} {'':>10} {'':>10} {total_pct:>+7.1f}% {total_pnl:>+14,.0f}")


def show_trades(cfg, symbol=None):
    """Print trade history."""
    conn = get_db(cfg)
    trades = get_trade_history(conn, symbol)
    conn.close()

    if not trades:
        print("No trades recorded.")
        return

    print(f"{'Date':<12} {'Symbol':<8} {'Action':<6} {'Lots':>5} {'Price':>10} {'Fees':>10} {'Notes'}")
    print("-" * 70)
    for t in trades:
        notes = t['notes'] or ""
        print(f"{t['date']:<12} {t['symbol']:<8} {t['action']:<6} {t['lots']:>5} {t['price']:>10,.0f} {t['fees']:>10,.0f} {notes}")


# --- CLI entry points ---

def cmd_portfolio(args, cfg):
    show_portfolio(cfg)


def cmd_trades(args, cfg):
    show_trades(cfg, symbol=args.symbol if hasattr(args, 'symbol') else None)


def cmd_buy(args, cfg):
    conn = get_db(cfg)
    add_trade(conn, symbol=args.symbol, action="buy", lots=args.lots,
              price=args.price, fees=args.fees or 0, date=args.date,
              notes=args.notes, stop_loss=args.stop,
              tranches_planned=args.tranches)
    conn.close()


def cmd_sell(args, cfg):
    conn = get_db(cfg)
    add_trade(conn, symbol=args.symbol, action="sell", lots=args.lots,
              price=args.price, fees=args.fees or 0, date=args.date,
              notes=args.notes)
    conn.close()


def cmd_set_stop(args, cfg):
    conn = get_db(cfg)
    set_stop_loss(conn, args.symbol, args.price)
    conn.close()


def get_tranche_suggestions(cfg, signal_scores: list[dict], macro_regime: dict = None) -> list[dict]:
    """Check open positions for tranche-up opportunities.

    Returns list of dicts for positions where:
    - tranches_done < tranches_planned
    - signal score above threshold
    - macro regime is not risk_off
    """
    if macro_regime and macro_regime.get("regime") == "risk_off":
        return []

    conn = get_db(cfg)
    positions = get_portfolio(conn)
    conn.close()

    threshold = cfg.get("signals", {}).get("score_threshold", 3.0)
    score_map = {s["symbol"]: s for s in signal_scores}

    suggestions = []
    for pos in positions:
        symbol = pos["symbol"]
        if pos["tranches_done"] >= pos["tranches_planned"]:
            continue

        sig = score_map.get(symbol)
        if not sig or sig["score"] < threshold:
            continue

        suggestions.append({
            "symbol": symbol,
            "current_tranche": pos["tranches_done"],
            "tranches_planned": pos["tranches_planned"],
            "avg_cost": pos["avg_cost"],
            "current_price": pos["current_price"],
            "pnl_pct": pos["pnl_pct"],
            "signal_score": sig["score"],
            "signals": sig["signals"],
            "reason": f"Score {sig['score']}, tranche {pos['tranches_done']}/{pos['tranches_planned']}",
        })

    return suggestions


def get_stop_warnings(cfg, threshold_pct: float = 3.0) -> list[dict]:
    """Flag positions where price is within threshold% of stop loss.

    Returns list of dicts with position info and distance to stop.
    """
    conn = get_db(cfg)
    positions = get_portfolio(conn)
    conn.close()

    warnings = []
    for pos in positions:
        if not pos["stop_loss"] or not pos["current_price"]:
            continue
        distance = pos["stop_distance_pct"]
        if distance is not None and distance <= threshold_pct:
            warnings.append({
                "symbol": pos["symbol"],
                "current_price": pos["current_price"],
                "stop_loss": pos["stop_loss"],
                "distance_pct": round(distance, 2),
                "avg_cost": pos["avg_cost"],
                "pnl_pct": pos["pnl_pct"],
                "total_lots": pos["total_lots"],
                "breached": distance <= 0,
            })

    return warnings
