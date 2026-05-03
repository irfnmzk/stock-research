"""Telegram bot — single-user async bot for the IDX research agent.

Handles free-text messages, commands, and EOD brief delivery.
Uses python-telegram-bot v21+ async API.
"""

import asyncio
import logging
import os
import time
from pathlib import Path

import yaml
from dotenv import load_dotenv
load_dotenv()

from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from agent import generate_eod_brief, generate_eod_brief_with_charts, run_conversation, close_session
from db import get_db
from memory import set_thesis, get_thesis, get_all_theses, get_recent_summaries

SCRIPT_DIR = Path(__file__).resolve().parent
log = logging.getLogger("bot")

# Session state per user
_sessions = {}


def _load_config():
    with open(SCRIPT_DIR / "config.yaml") as f:
        return yaml.safe_load(f)


def _authorized(update: Update, cfg: dict) -> bool:
    """Check if the user is authorized."""
    auth_id = cfg.get("telegram", {}).get("authorized_user_id", 0)
    return update.effective_user.id == auth_id


def _get_session(user_id):
    """Get or create session state."""
    if user_id not in _sessions:
        _sessions[user_id] = {"session_id": None, "last_active": time.time()}
    return _sessions[user_id]


def _check_session_timeout(cfg, user_id):
    """Close session if timed out. Returns True if session was reset."""
    session = _get_session(user_id)
    timeout = cfg.get("agent", {}).get("session_timeout_min", 30) * 60
    if session["session_id"] and (time.time() - session["last_active"]) > timeout:
        try:
            close_session(cfg, session["session_id"])
        except Exception:
            pass
        session["session_id"] = None
        return True
    return False


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cfg = _load_config()
    if not _authorized(update, cfg):
        return
    await update.message.reply_text("IDX Research Agent ready. Send any message to chat, or use /brief for the EOD report.")


async def cmd_brief(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Generate and send EOD brief."""
    cfg = _load_config()
    if not _authorized(update, cfg):
        return

    await update.message.reply_text("Generating EOD brief...")

    try:
        brief = generate_eod_brief(cfg)

        # Split long messages (Telegram limit is 4096 chars)
        for chunk in _split_message(brief):
            await update.message.reply_text(chunk)

        # Send charts
        chart_dir = SCRIPT_DIR / cfg.get("charts", {}).get("output_dir", "data/charts")
        watchlist = [s.replace(".JK", "") for s in cfg.get("watchlist", [])]
        for symbol in watchlist:
            chart_path = chart_dir / f"{symbol}.png"
            if chart_path.exists():
                await update.message.reply_photo(photo=open(chart_path, "rb"), caption=symbol)

    except Exception as e:
        log.error("Brief generation failed: %s", e)
        await update.message.reply_text(f"Error generating brief: {e}")


async def cmd_portfolio(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Quick portfolio summary without LLM."""
    cfg = _load_config()
    if not _authorized(update, cfg):
        return

    from portfolio import get_portfolio, get_stop_warnings
    db = get_db(cfg)
    positions = get_portfolio(db)
    db.close()
    warnings = get_stop_warnings(cfg, threshold_pct=5.0)

    if not positions:
        await update.message.reply_text("No open positions.")
        return

    lines = []
    for p in positions:
        line = (
            f"{p['symbol']}: {p['total_lots']} lots @ {p['avg_cost']:,.0f} → "
            f"{p['current_price']:,.0f} ({p['pnl_pct']:+.1f}%)"
        )
        if p.get("stop_loss"):
            line += f"\n  stop {p['stop_loss']:,.0f} ({p['stop_distance_pct']:.1f}% away)"
        lines.append(line)

    for w in warnings:
        if w.get("breached"):
            lines.append(f"\n⚠️ {w['symbol']} STOP BREACHED")
        elif w.get("distance_pct", 100) < 3:
            lines.append(f"\n⚠️ {w['symbol']} near stop ({w['distance_pct']:.1f}%)")

    await update.message.reply_text("\n".join(lines))


async def cmd_note(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Save a ticker thesis. Usage: /note SYMBOL your thesis text"""
    cfg = _load_config()
    if not _authorized(update, cfg):
        return

    args = update.message.text.split(maxsplit=2)
    if len(args) < 3:
        await update.message.reply_text("Usage: /note SYMBOL your thesis text")
        return

    symbol = args[1].upper()
    text = args[2]

    db = get_db(cfg)
    set_thesis(db, symbol, text)
    db.close()

    await update.message.reply_text(f"Thesis saved for {symbol}.")


async def cmd_recall(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Recall theses. Usage: /recall [SYMBOL]"""
    cfg = _load_config()
    if not _authorized(update, cfg):
        return

    args = update.message.text.split()
    db = get_db(cfg)

    if len(args) > 1:
        symbol = args[1].upper()
        thesis = get_thesis(db, symbol)
        if thesis:
            await update.message.reply_text(f"{symbol}: {thesis}")
        else:
            await update.message.reply_text(f"No thesis for {symbol}.")
    else:
        theses = get_all_theses(db)
        if theses:
            lines = [f"{t['symbol']}: {t['thesis']}" for t in theses]
            await update.message.reply_text("\n".join(lines))
        else:
            await update.message.reply_text("No theses saved.")

    db.close()


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle free-text messages — route to agent conversation."""
    cfg = _load_config()
    if not _authorized(update, cfg):
        return

    user_id = update.effective_user.id
    _check_session_timeout(cfg, user_id)
    session = _get_session(user_id)
    session["last_active"] = time.time()

    user_text = update.message.text
    if not user_text:
        return

    try:
        text, chart_paths, session_id = run_conversation(
            cfg, user_text, session_id=session["session_id"]
        )
        session["session_id"] = session_id

        for chunk in _split_message(text):
            await update.message.reply_text(chunk)

        for path in chart_paths:
            if Path(path).exists():
                await update.message.reply_photo(photo=open(path, "rb"))

    except Exception as e:
        log.error("Conversation error: %s", e)
        await update.message.reply_text(f"Error: {e}")


async def trigger_eod_brief(cfg, app: Application):
    """Send EOD brief to authorized user. Called after pipeline completes."""
    auth_id = cfg.get("telegram", {}).get("authorized_user_id", 0)
    if not auth_id:
        return

    try:
        brief = generate_eod_brief(cfg)

        for chunk in _split_message(brief):
            await app.bot.send_message(chat_id=auth_id, text=chunk)

        chart_dir = SCRIPT_DIR / cfg.get("charts", {}).get("output_dir", "data/charts")
        watchlist = [s.replace(".JK", "") for s in cfg.get("watchlist", [])]
        for symbol in watchlist:
            chart_path = chart_dir / f"{symbol}.png"
            if chart_path.exists():
                await app.bot.send_photo(chat_id=auth_id, photo=open(chart_path, "rb"), caption=symbol)

    except Exception as e:
        log.error("EOD brief delivery failed: %s", e)


def _split_message(text, max_len=4000):
    """Split text into chunks that fit Telegram's message limit."""
    if len(text) <= max_len:
        return [text]
    chunks = []
    while text:
        if len(text) <= max_len:
            chunks.append(text)
            break
        split_at = text.rfind("\n", 0, max_len)
        if split_at == -1:
            split_at = max_len
        chunks.append(text[:split_at])
        text = text[split_at:].lstrip("\n")
    return chunks


def run_bot():
    """Start the Telegram bot."""
    cfg = _load_config()
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token:
        print("Error: TELEGRAM_BOT_TOKEN environment variable not set")
        return

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")

    app = Application.builder().token(token).build()

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("brief", cmd_brief))
    app.add_handler(CommandHandler("portfolio", cmd_portfolio))
    app.add_handler(CommandHandler("note", cmd_note))
    app.add_handler(CommandHandler("recall", cmd_recall))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    log.info("Bot starting...")
    app.run_polling()


if __name__ == "__main__":
    run_bot()
