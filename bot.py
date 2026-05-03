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

from telegram import BotCommand, InputMediaPhoto, Update
from telegram.constants import ChatAction, ParseMode
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

_sessions = {}


def _load_config():
    with open(SCRIPT_DIR / "config.yaml") as f:
        return yaml.safe_load(f)


def _authorized(update: Update, cfg: dict) -> bool:
    auth_id = cfg.get("telegram", {}).get("authorized_user_id", 0)
    return update.effective_user.id == auth_id


def _get_session(user_id):
    if user_id not in _sessions:
        _sessions[user_id] = {"session_id": None, "last_active": time.time()}
    return _sessions[user_id]


def _check_session_timeout(cfg, user_id):
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


async def _keep_typing(chat, stop_event: asyncio.Event):
    """Send typing action every 4s until stop_event is set."""
    while not stop_event.is_set():
        try:
            await chat.send_action(ChatAction.TYPING)
        except Exception:
            break
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=4)
            break
        except asyncio.TimeoutError:
            continue


async def _send_reply(message, text, charts=None):
    """Send text + optional charts. Single chart: text as caption. Multiple: text then album."""
    CAPTION_MAX = 1024

    valid_charts = [Path(p) for p in (charts or []) if Path(p).exists()]

    if len(valid_charts) == 1:
        photo = open(valid_charts[0], "rb")
        if len(text) <= CAPTION_MAX:
            try:
                await message.reply_photo(photo=photo, caption=text, parse_mode=ParseMode.HTML)
            except Exception:
                await message.reply_photo(photo=photo, caption=text)
        else:
            caption = text[:CAPTION_MAX].rsplit("\n", 1)[0]
            remainder = text[len(caption):].lstrip("\n")
            try:
                await message.reply_photo(photo=photo, caption=caption, parse_mode=ParseMode.HTML)
            except Exception:
                await message.reply_photo(photo=photo, caption=caption)
            for chunk in _split_message(remainder):
                try:
                    await message.reply_text(chunk, parse_mode=ParseMode.HTML)
                except Exception:
                    await message.reply_text(chunk)
        return

    for chunk in _split_message(text):
        try:
            await message.reply_text(chunk, parse_mode=ParseMode.HTML)
        except Exception:
            await message.reply_text(chunk)

    if not valid_charts:
        return

    photos = [InputMediaPhoto(media=open(p, "rb"), caption=p.stem.upper()) for p in valid_charts]
    if len(photos) == 1:
        await message.reply_photo(photo=photos[0].media, caption=photos[0].caption)
    else:
        for batch in [photos[i:i+10] for i in range(0, len(photos), 10)]:
            await message.reply_media_group(media=batch)


# --- Commands ---

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cfg = _load_config()
    if not _authorized(update, cfg):
        return
    await update.message.reply_text(
        "IDX Research Agent ready.\n\nSend any message to chat, or tap the menu for commands.",
        parse_mode=ParseMode.HTML,
    )


async def cmd_brief(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cfg = _load_config()
    if not _authorized(update, cfg):
        return

    stop = asyncio.Event()
    typing_task = asyncio.create_task(_keep_typing(update.effective_chat, stop))

    try:
        brief = await asyncio.get_event_loop().run_in_executor(
            None, generate_eod_brief, cfg
        )
        stop.set()
        await typing_task

        chart_dir = SCRIPT_DIR / cfg.get("charts", {}).get("output_dir", "data/charts")
        watchlist = [s.replace(".JK", "") for s in cfg.get("watchlist", [])]
        chart_paths = [
            str(chart_dir / f"{sym}.png")
            for sym in watchlist
            if (chart_dir / f"{sym}.png").exists()
        ]

        await _send_reply(update.message, brief, charts=chart_paths)

    except Exception as e:
        stop.set()
        await typing_task
        log.error("Brief generation failed: %s", e)
        await update.message.reply_text(f"Error: {e}")


async def cmd_portfolio(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cfg = _load_config()
    if not _authorized(update, cfg):
        return

    await update.effective_chat.send_action(ChatAction.TYPING)

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
            f"<b>{p['symbol']}</b>  {p['total_lots']} lots @ <code>{p['avg_cost']:,.0f}</code> → "
            f"<code>{p['current_price']:,.0f}</code>  <code>{p['pnl_pct']:+.1f}%</code>"
        )
        if p.get("stop_loss"):
            line += f"\nstop <code>{p['stop_loss']:,.0f}</code> ({p['stop_distance_pct']:.1f}% away)"
        lines.append(line)

    for w in warnings:
        if w.get("breached"):
            lines.append(f"\n⚠️ <b>{w['symbol']} STOP BREACHED</b>")
        elif w.get("distance_pct", 100) < 3:
            lines.append(f"\n⚠️ <b>{w['symbol']}</b> near stop ({w['distance_pct']:.1f}%)")

    await update.message.reply_text("\n\n".join(lines), parse_mode=ParseMode.HTML)


async def cmd_note(update: Update, context: ContextTypes.DEFAULT_TYPE):
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
    cfg = _load_config()
    if not _authorized(update, cfg):
        return

    args = update.message.text.split()
    db = get_db(cfg)

    if len(args) > 1:
        symbol = args[1].upper()
        thesis = get_thesis(db, symbol)
        if thesis:
            await update.message.reply_text(f"<b>{symbol}</b>: {thesis}", parse_mode=ParseMode.HTML)
        else:
            await update.message.reply_text(f"No thesis for {symbol}.")
    else:
        theses = get_all_theses(db)
        if theses:
            lines = [f"<b>{t['symbol']}</b>: {t['thesis']}" for t in theses]
            await update.message.reply_text("\n\n".join(lines), parse_mode=ParseMode.HTML)
        else:
            await update.message.reply_text("No theses saved.")

    db.close()


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
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

    stop = asyncio.Event()
    typing_task = asyncio.create_task(_keep_typing(update.effective_chat, stop))

    try:
        text, chart_paths, session_id = await asyncio.get_event_loop().run_in_executor(
            None, lambda: run_conversation(cfg, user_text, session_id=session["session_id"])
        )
        stop.set()
        await typing_task

        session["session_id"] = session_id
        await _send_reply(update.message, text, charts=chart_paths)

    except Exception as e:
        stop.set()
        await typing_task
        log.error("Conversation error: %s", e)
        await update.message.reply_text(f"Error: {e}")


async def trigger_eod_brief(cfg, app: Application):
    auth_id = cfg.get("telegram", {}).get("authorized_user_id", 0)
    if not auth_id:
        return

    try:
        brief = generate_eod_brief(cfg)

        for chunk in _split_message(brief):
            try:
                await app.bot.send_message(chat_id=auth_id, text=chunk, parse_mode=ParseMode.HTML)
            except Exception:
                await app.bot.send_message(chat_id=auth_id, text=chunk)

        chart_dir = SCRIPT_DIR / cfg.get("charts", {}).get("output_dir", "data/charts")
        watchlist = [s.replace(".JK", "") for s in cfg.get("watchlist", [])]
        photos = []
        for sym in watchlist:
            chart_path = chart_dir / f"{sym}.png"
            if chart_path.exists():
                photos.append(InputMediaPhoto(media=open(chart_path, "rb"), caption=sym))

        if len(photos) == 1:
            await app.bot.send_photo(chat_id=auth_id, photo=photos[0].media, caption=photos[0].caption)
        elif photos:
            for batch in [photos[i:i+10] for i in range(0, len(photos), 10)]:
                await app.bot.send_media_group(chat_id=auth_id, media=batch)

    except Exception as e:
        log.error("EOD brief delivery failed: %s", e)


def _split_message(text, max_len=4000):
    if len(text) <= max_len:
        return [text]
    chunks = []
    while text:
        if len(text) <= max_len:
            chunks.append(text)
            break
        split_at = text.rfind("\n\n", 0, max_len)
        if split_at == -1:
            split_at = text.rfind("\n", 0, max_len)
        if split_at == -1:
            split_at = max_len
        chunks.append(text[:split_at])
        text = text[split_at:].lstrip("\n")
    return chunks


async def _post_init(app: Application):
    await app.bot.set_my_commands([
        BotCommand("brief", "EOD brief report"),
        BotCommand("portfolio", "Portfolio positions & PnL"),
        BotCommand("note", "Save thesis — /note SYMBOL text"),
        BotCommand("recall", "Recall theses — /recall [SYMBOL]"),
    ])


def run_bot():
    cfg = _load_config()
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token:
        print("Error: TELEGRAM_BOT_TOKEN environment variable not set")
        return

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")

    app = Application.builder().token(token).post_init(_post_init).build()

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
