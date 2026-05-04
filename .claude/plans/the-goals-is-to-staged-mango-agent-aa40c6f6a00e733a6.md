# Scheduled EOD Brief at 5pm GMT+7

## Summary

Add a daily 5pm WIB (GMT+7) scheduled job to the Telegram bot that runs the full EOD pipeline and then sends the brief. Use python-telegram-bot's built-in `JobQueue` (APScheduler-backed) — no new dependencies. Save the brief to conversation history so the agent can reference it in follow-up chats.

---

## Key Design Decisions

### 1. Scheduler should run the full pipeline, not just send the brief

**Rationale:** The current `trigger_eod_brief` only generates and sends the LLM brief. It assumes `run_eod.py` already ran via system cron. But if the bot is the scheduling authority, it should own the full pipeline too — otherwise you need two separate schedulers (system cron for pipeline + bot for brief), which is fragile and defeats the purpose.

The scheduled job should: run the pipeline (`run_eod.run()`), then generate and send the brief. If the pipeline fails, send a short error notification instead of a stale brief.

### 2. Save the brief to conversation history — yes

**Rationale:** Without this, the agent has no memory of what it reported. A user asking "what did you say about BBNI in today's brief?" gets nothing. The cost is one `assistant` turn per day in a dedicated session — trivial.

Implementation: create a new session, save the brief text as an assistant turn, then close the session with a summary. This keeps it separate from interactive chat sessions but discoverable via `get_recent_summaries()`.

### 3. Timezone: use `zoneinfo` with `"Asia/Jakarta"`

**Rationale:** `zoneinfo.ZoneInfo("Asia/Jakarta")` is stdlib (Python 3.9+), handles DST edge cases correctly (Indonesia doesn't observe DST, but the named zone is still more robust and readable than a raw `datetime.timezone(timedelta(hours=7))`). It also makes the config self-documenting.

---

## Implementation Plan

### Step 1: Add schedule config to `config.yaml`

Add a new `schedule` section under the existing config:

```yaml
# --- Schedule ---
schedule:
  eod_brief:
    enabled: true
    time: "17:00"          # 24h format
    timezone: "Asia/Jakarta"  # GMT+7 / WIB
    run_pipeline: true     # run full pipeline before sending brief
```

**File:** `/home/zuck/Work/personal/stock-research/config.yaml`

This keeps the schedule declarative and easy to toggle. `run_pipeline: true` controls whether the job runs `run_eod.run()` first or just sends the brief from existing data.

### Step 2: Add APScheduler to dependencies

python-telegram-bot's `JobQueue` requires `apscheduler` as an optional dependency. It may not be installed yet (it wasn't found in the venv).

**File:** `/home/zuck/Work/personal/stock-research/pyproject.toml`

Add to `dependencies`:
```
"APScheduler>=3.10,<4",
```

Then `uv sync` to install.

### Step 3: Create the scheduled job callback in `bot.py`

Add an async callback function that the `JobQueue` will invoke daily. This is the core of the feature.

**File:** `/home/zuck/Work/personal/stock-research/bot.py`

New function `_scheduled_eod_job(context: ContextTypes.DEFAULT_TYPE)`:

```python
async def _scheduled_eod_job(context: ContextTypes.DEFAULT_TYPE):
    """Scheduled daily EOD: run pipeline, generate brief, send to user, save to history."""
    cfg = _load_config()
    auth_id = cfg.get("telegram", {}).get("authorized_user_id", 0)
    if not auth_id:
        log.warning("Scheduled EOD: no authorized_user_id configured")
        return

    schedule_cfg = cfg.get("schedule", {}).get("eod_brief", {})
    run_pipeline = schedule_cfg.get("run_pipeline", True)

    # Step A: Optionally run the full pipeline
    if run_pipeline:
        try:
            log.info("Scheduled EOD: running pipeline...")
            loop = asyncio.get_event_loop()
            ok = await loop.run_in_executor(None, _run_pipeline_sync, cfg)
            if not ok:
                await context.bot.send_message(
                    chat_id=auth_id,
                    text="EOD pipeline completed with errors. Check pipeline.log. Sending brief from available data.",
                )
        except Exception as e:
            log.error("Scheduled EOD pipeline failed: %s", e)
            await context.bot.send_message(
                chat_id=auth_id,
                text=f"EOD pipeline failed: {e}\nNo brief today.",
            )
            return

    # Step B: Check data freshness
    status_path = SCRIPT_DIR / "data" / "pipeline_status.json"
    if status_path.exists():
        import json
        status = json.loads(status_path.read_text())
        if status.get("status") == "error" and not run_pipeline:
            await context.bot.send_message(
                chat_id=auth_id,
                text="Skipping EOD brief: pipeline last run failed and run_pipeline is disabled.",
            )
            return

    # Step C: Generate brief
    try:
        loop = asyncio.get_event_loop()
        brief = await loop.run_in_executor(None, generate_eod_brief, cfg)
    except Exception as e:
        log.error("Scheduled EOD brief generation failed: %s", e)
        await context.bot.send_message(chat_id=auth_id, text=f"Brief generation failed: {e}")
        return

    # Step D: Send via Telegram (reuse existing trigger_eod_brief logic)
    await trigger_eod_brief(cfg, context.application)

    # Step E: Save to conversation history
    try:
        _save_eod_to_history(cfg, brief)
    except Exception as e:
        log.error("Failed to save EOD brief to history: %s", e)
```

Helper for running the pipeline synchronously (called via `run_in_executor`):

```python
def _run_pipeline_sync(cfg):
    """Run the EOD pipeline. Returns True on success."""
    from run_eod import run
    return run(config_path="config.yaml")
```

### Step 4: Save EOD brief to conversation history

New function in `bot.py` (or could go in `agent.py`, but `bot.py` is the caller and keeps the dependency direction clean):

**File:** `/home/zuck/Work/personal/stock-research/bot.py`

```python
def _save_eod_to_history(cfg, brief_text):
    """Save the EOD brief as a conversation turn so the agent can recall it."""
    from memory import save_turn, start_session, save_session_summary
    db = get_db(cfg)
    session_id = start_session(db)
    save_turn(db, session_id, "user", "Generate today's end-of-day brief.")
    save_turn(db, session_id, "assistant", brief_text)
    save_session_summary(db, session_id, f"EOD brief delivered {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    db.close()
```

This creates a minimal session with one user turn (the implicit request) and one assistant turn (the brief). The session summary makes it show up in `get_recent_summaries()`, which feeds into `build_context()` via `_section_summaries()`. Future chat sessions will see "EOD brief delivered 2026-05-03 17:00" in their context.

Requires adding `from datetime import datetime` to `bot.py` imports (not currently imported there).

### Step 5: Register the scheduled job in `run_bot()`

**File:** `/home/zuck/Work/personal/stock-research/bot.py`

In the `run_bot()` function, after building the `app` and before `app.run_polling()`, register the daily job:

```python
def run_bot():
    cfg = _load_config()
    # ... existing token check and app build ...

    # Register scheduled EOD brief
    schedule_cfg = cfg.get("schedule", {}).get("eod_brief", {})
    if schedule_cfg.get("enabled", False):
        from zoneinfo import ZoneInfo
        from datetime import time as dt_time

        time_str = schedule_cfg.get("time", "17:00")
        tz_name = schedule_cfg.get("timezone", "Asia/Jakarta")
        hour, minute = map(int, time_str.split(":"))

        tz = ZoneInfo(tz_name)
        job_time = dt_time(hour=hour, minute=minute, tzinfo=tz)

        app.job_queue.run_daily(
            _scheduled_eod_job,
            time=job_time,
            name="eod_brief",
        )
        log.info("Scheduled EOD brief at %s %s", time_str, tz_name)

    # ... existing handler registration ...
    app.run_polling()
```

`JobQueue.run_daily()` accepts a `datetime.time` with `tzinfo` — APScheduler handles the conversion internally. The job fires once per day at the specified local time.

### Step 6: Update `trigger_eod_brief` to accept brief text (optional optimization)

Currently `trigger_eod_brief` calls `generate_eod_brief` internally. The scheduled job also calls `generate_eod_brief` (to save the text to history). To avoid generating the brief twice, refactor `trigger_eod_brief` to optionally accept pre-generated text:

**File:** `/home/zuck/Work/personal/stock-research/bot.py`

Change signature from:
```python
async def trigger_eod_brief(cfg, app: Application):
```
to:
```python
async def trigger_eod_brief(cfg, app: Application, brief_text: str | None = None):
```

If `brief_text` is provided, skip the `generate_eod_brief(cfg)` call and use the provided text directly. This is backward-compatible — `run_eod.py --notify` still works without passing text.

Then in `_scheduled_eod_job`, call:
```python
await trigger_eod_brief(cfg, context.application, brief_text=brief)
```

### Step 7: Add `/eod` command for manual pipeline + brief trigger

Optional but useful. Lets the user manually trigger the full pipeline + brief from Telegram without waiting for the schedule.

**File:** `/home/zuck/Work/personal/stock-research/bot.py`

```python
async def cmd_eod(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Manually trigger EOD pipeline + brief."""
    cfg = _load_config()
    if not _authorized(update, cfg):
        return
    await update.message.reply_text("Running EOD pipeline...")
    # Reuse the scheduled job logic
    await _scheduled_eod_job(context)
```

Register in `run_bot()`:
```python
app.add_handler(CommandHandler("eod", cmd_eod))
```

And add to `_post_init` bot commands list:
```python
BotCommand("eod", "Run EOD pipeline + brief now"),
```

---

## File Change Summary

| File | Change |
|------|--------|
| `config.yaml` | Add `schedule.eod_brief` section |
| `pyproject.toml` | Add `APScheduler>=3.10,<4` dependency |
| `bot.py` | Add `_scheduled_eod_job`, `_save_eod_to_history`, `_run_pipeline_sync`; modify `run_bot()` to register daily job; modify `trigger_eod_brief` to accept optional `brief_text`; optionally add `/eod` command |

No changes needed to `agent.py`, `memory.py`, `run_eod.py`, `context.py`, or `db.py`. The existing infrastructure (session creation, turn saving, summary retrieval, context building) already supports this without modification.

---

## Edge Cases and Failure Modes

1. **Pipeline takes too long:** `run_eod.run()` typically takes a few minutes. It runs in a thread executor so it won't block the bot's event loop. The bot remains responsive to messages during the pipeline run.

2. **Bot restarts mid-day after 5pm:** `JobQueue.run_daily()` fires at the next occurrence of the specified time. If the bot starts at 6pm, the job fires the next day at 5pm. No catch-up mechanism needed — the user can use `/eod` or `/brief` manually.

3. **Pipeline partially fails:** `run_eod.run()` returns `False` on errors but still writes partial data. The brief will be generated from whatever data is available, and the user gets a warning message.

4. **No `latest_eod.json` exists:** `build_context()` in `context.py` already handles this gracefully — `_load_eod()` returns `{}` if the file doesn't exist. The brief will be sparse but won't crash.

5. **APScheduler not installed:** If the user hasn't run `uv sync` after adding the dependency, `app.job_queue` will be `None`. Add a guard: check `if app.job_queue is None` and log a warning instead of crashing.

6. **Duplicate briefs:** If the user runs `/brief` or `/eod` close to 5pm, they might get two briefs. This is acceptable — the scheduled one also saves to history, the manual one doesn't (unless triggered via `/eod`).

---

## Sequence Diagram

```
5:00 PM WIB
    |
    v
JobQueue fires _scheduled_eod_job()
    |
    +--> run_in_executor(run_eod.run())    # full pipeline: fetch, compute, signals, assemble, charts
    |       writes data/latest_eod.json
    |       writes data/pipeline_status.json
    |
    +--> run_in_executor(generate_eod_brief())  # Claude call using latest_eod.json context
    |       returns brief text
    |
    +--> trigger_eod_brief(cfg, app, brief_text)  # send to Telegram
    |       send_message() + send_media_group()
    |
    +--> _save_eod_to_history(cfg, brief)  # persist to SQLite
            start_session() -> save_turn(user) -> save_turn(assistant) -> save_session_summary()
```
