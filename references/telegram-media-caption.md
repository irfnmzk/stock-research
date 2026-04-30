# Telegram MEDIA: Caption Limitation

## Problem
The Hermes gateway does not support sending image + caption as a single Telegram message.

## How MEDIA: Works in Gateway

1. `BasePlatformAdapter.extract_media(content)` strips all `MEDIA:<path>` tags from text
2. Text portion is sent first as its own message
3. Media files are sent after, with NO caption parameter

Relevant code paths:
- `gateway/platforms/base.py` line ~2427: `extract_media()` splits text and media
- `gateway/platforms/base.py` line ~2550: `send_image_file()` called without caption
- `tools/send_message_tool.py` `_send_telegram()` line ~706: `bot.send_photo(chat_id, photo=f)` — no caption

## Current Approach (accepted 2026-04-30)
Send MEDIA:/path and caption text in the same message block. They arrive as two
adjacent Telegram messages (text then image). User accepted this as good enough.
No custom workaround needed.

## Technical Notes
- Telegram caption limit: 1024 characters
- Bot token lives in `~/.hermes/config.yaml` under `gateway.telegram.token`
- If upstream ever adds caption support to extract_media, this limitation goes away
