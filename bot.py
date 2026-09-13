"""
AS AuraX Clone Bot
-------------------
Relays every command the user sends to the real @ASxAura_bot, then re-sends
the response back through YOUR bot — with the "Commands available / Special
Thanks" block removed and the join-channel link/button pointed at your own
channel instead of theirs.

Required environment variables (same pattern as before):
  API_ID          - your Telegram API ID (my.telegram.org)
  API_HASH        - your Telegram API hash
  BOT_TOKEN       - your clone bot's token (from @BotFather)
  SESSION_STRING  - your userbot session string (generate_session.py)
  TARGET_BOT      - defaults to @ASxAura_bot, override if needed
  BOT_USERNAME    - your clone bot's own @username (without @) — used to
                    replace any self-referencing links the target bot sends
  CHANNEL_URL     - defaults to https://t.me/ModAppsKing
  PORT            - defaults to 10000 (Render sets this automatically)
"""

import os
import re
import time
import asyncio
import logging
import threading

from flask import Flask, jsonify
from telethon import TelegramClient, events, Button
from telethon.sessions import StringSession

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
API_ID = int(os.environ.get("API_ID", "0"))
API_HASH = os.environ.get("API_HASH", "")
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
SESSION_STRING = os.environ.get("SESSION_STRING", "")
TARGET_BOT = os.environ.get("TARGET_BOT", "@ASxAura_bot")
BOT_USERNAME = os.environ.get("BOT_USERNAME", "")  # set this once you know your clone's @username
CHANNEL_URL = os.environ.get("CHANNEL_URL", "https://t.me/ModAppsKing")
PORT = int(os.environ.get("PORT", 10000))

# ---------------------------------------------------------------------------
# Flask keep-alive (Render health check)
# ---------------------------------------------------------------------------
app = Flask(__name__)


@app.route("/")
def home():
    return jsonify({"status": "alive"})


def run_flask():
    app.run(host="0.0.0.0", port=PORT)


# ---------------------------------------------------------------------------
# Telethon clients
# ---------------------------------------------------------------------------
# Python 3.14 removed asyncio's old auto-create-a-loop-if-none-exists
# behavior, but Telethon (1.36) still expects one to exist the moment a
# TelegramClient is constructed at module level (before main()/asyncio.run
# ever runs). Without this, client construction itself crashes with
# "RuntimeError: no running event loop". Explicitly making one here is the
# standard fix for older Telethon versions on newer Python.
_loop = asyncio.new_event_loop()
asyncio.set_event_loop(_loop)

bot = TelegramClient("bot_session", API_ID, API_HASH)
user = TelegramClient(StringSession(SESSION_STRING), API_ID, API_HASH)
# Default "Markdown" mode has no underline syntax, so a message that
# combines bold + underline + a text link in one span (like AS AuraX's
# "Click Here to Get Auth Key" button text) doesn't round-trip cleanly —
# the link can silently vanish on resend. HTML mode represents all three
# unambiguously (<b>, <u>, <a href>), so switch both clients to it.
bot.parse_mode = "html"
user.parse_mode = "html"

captured_msg = None
response_event = asyncio.Event()
# Only one command can be "in flight" to TARGET_BOT at a time (single userbot
# session) — this lock keeps concurrent requests from getting their
# responses cross-matched. See King-Multiverse's bot.py for the full story.
request_lock = asyncio.Lock()


# ---------------------------------------------------------------------------
# Branding replacement
# ---------------------------------------------------------------------------
def replace_text_links(text):
    """Strip the source bot's commands/credits block and re-brand links."""
    if not text:
        return text

    # Remove the boxed "Commands available: ... Special Thanks ... Many More"
    # block entirely. It always runs from "Commands available:" up to (but
    # not including) the "⚠️ Important" warning that follows it.
    text = re.sub(r'Commands available:.*?(?=\n?\s*⚠️|\Z)', '', text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r'\n{3,}', '\n\n', text).strip()

    # Re-brand the join-channel mentions
    text = text.replace("https://t.me/AS_AuraX", CHANNEL_URL)
    text = text.replace("t.me/AS_AuraX", CHANNEL_URL.replace("https://", ""))
    text = text.replace("@AS_AuraX", CHANNEL_URL)

    # Re-brand any self-referencing link/username the target bot sends
    if BOT_USERNAME:
        text = text.replace(f"@{TARGET_BOT.lstrip('@')}", f"@{BOT_USERNAME}")
        text = text.replace(f"t.me/{TARGET_BOT.lstrip('@')}", f"t.me/{BOT_USERNAME}")

    return text


def replace_url(url):
    """Re-brand a raw URL (used for inline buttons)."""
    if not url:
        return url
    if "AS_AuraX" in url:
        return CHANNEL_URL
    if BOT_USERNAME and TARGET_BOT.lstrip("@") in url:
        return url.replace(TARGET_BOT.lstrip("@"), BOT_USERNAME)
    return url


def copy_buttons(msg):
    """Rebuild TARGET bot's buttons, re-branding the join-channel one."""
    if not msg or not msg.buttons:
        return None
    new_rows = []
    for row in msg.buttons:
        new_row = []
        for btn in row:
            btn_text = btn.text
            url = getattr(btn, "url", None)
            if url:
                new_row.append(Button.url(btn_text, replace_url(url)))
            else:
                # Non-URL buttons (if any ever appear) are dropped rather
                # than relayed, since this bot doesn't simulate button
                # clicks on TARGET_BOT the way the video bot does — AS
                # AuraX is command-driven, not button-driven.
                continue
        if new_row:
            new_rows.append(new_row)
    return new_rows or None


# ---------------------------------------------------------------------------
# Talk to TARGET_BOT via the userbot session
# ---------------------------------------------------------------------------
async def send_to_target(text, timeout=30.0):
    """Send `text` to TARGET_BOT as the userbot, wait for its reply."""
    global captured_msg, response_event

    @user.on(events.NewMessage(chats=TARGET_BOT))
    @user.on(events.MessageEdited(chats=TARGET_BOT))
    async def _capture(event):
        global captured_msg, response_event
        captured_msg = event.message
        response_event.set()

    response_event.clear()
    captured_msg = None
    await user.send_message(TARGET_BOT, text)
    logger.info(f"Sent to TARGET bot: {text[:50]}")

    try:
        await asyncio.wait_for(response_event.wait(), timeout=timeout)
        return captured_msg
    except asyncio.TimeoutError:
        logger.error(f"Timeout ({timeout}s) waiting for TARGET bot response!")
        return None
    finally:
        user.remove_event_handler(_capture)


# ---------------------------------------------------------------------------
# Forward TARGET bot's response to the user
# ---------------------------------------------------------------------------
async def forward_response(event, target_msg, status_msg=None):
    async def _clear_status():
        if status_msg:
            try:
                await status_msg.delete()
            except Exception:
                pass

    if not target_msg:
        await _clear_status()
        await event.reply("❌ Target bot not responding. Try again.")
        return

    buttons = copy_buttons(target_msg)
    text = replace_text_links(target_msg.text or "")

    if target_msg.media:
        # Simple media relay — AS AuraX doesn't appear to send large video
        # files (unlike the King-Multiverse bot), so no need for the
        # multi-strategy/log-chat relay machinery there. Try a direct
        # send first, fall back to download+upload if that fails.
        await _clear_status()
        try:
            await bot.send_file(event.chat_id, file=target_msg.media, caption=text or None, buttons=buttons)
            return
        except Exception as e:
            logger.error(f"Direct media send failed: {e}")
        try:
            media_path = await target_msg.download_media()
            if media_path:
                await event.reply(text or " ", file=media_path, buttons=buttons)
                try:
                    os.remove(media_path)
                except Exception:
                    pass
                return
        except Exception as e:
            logger.error(f"Download & upload failed: {e}")
        await event.reply("❌ Could not deliver that. Try again.", buttons=buttons)
        return

    await _clear_status()
    if text:
        await event.reply(text, buttons=buttons, link_preview=False)
    else:
        await event.reply("✅ Done!", buttons=buttons)


# ---------------------------------------------------------------------------
# Handlers — every command the user sends is relayed as-is
# ---------------------------------------------------------------------------
@bot.on(events.NewMessage(func=lambda e: e.raw_text and e.raw_text.startswith("/")))
async def command_handler(event):
    text = event.raw_text.strip()
    status = await event.reply("⏳ Processing...")

    try:
        async with request_lock:
            target_response = await send_to_target(text, timeout=30.0)
            await forward_response(event, target_response, status)
    except Exception as e:
        logger.error(f"Error in command handler: {e}")
        try:
            await status.delete()
        except Exception:
            pass
        await event.reply("❌ Something went wrong. Try again.")


@bot.on(events.NewMessage(func=lambda e: e.raw_text and not e.raw_text.startswith("/")))
async def text_handler(event):
    """Non-command text (e.g. a raw link for /bypass-style flows) — relay as-is."""
    text = event.raw_text.strip()
    status = await event.reply("⏳ Processing...")

    try:
        async with request_lock:
            target_response = await send_to_target(text, timeout=30.0)
            await forward_response(event, target_response, status)
    except Exception as e:
        logger.error(f"Error in text handler: {e}")
        try:
            await status.delete()
        except Exception:
            pass
        await event.reply("❌ Something went wrong. Try again.")


# ---------------------------------------------------------------------------
# Startup
# ---------------------------------------------------------------------------
async def main():
    threading.Thread(target=run_flask, daemon=True).start()
    logger.info(f"Flask keep-alive running on port {PORT}")

    await user.start()
    me = await user.get_me()
    logger.info(f"✅ User session started: {me.first_name} (@{me.username})")

    target_entity = await user.get_entity(TARGET_BOT)
    logger.info(f"🎯 Target bot resolved: {TARGET_BOT}")

    await bot.start(bot_token=BOT_TOKEN)
    me_bot = await bot.get_me()
    logger.info(f"✅ Bot started: @{me_bot.username}")
    logger.info(f"   Proxy target: {TARGET_BOT}")
    logger.info(f"   Channel: {CHANNEL_URL}")

    await bot.run_until_disconnected()


if __name__ == "__main__":
    asyncio.run(main())
