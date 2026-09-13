# AS AuraX Clone Bot

Relays every command to the real `@ASxAura_bot`, strips the
"Commands available / Special Thanks" block from its replies, and points
the join-channel link/button at your own channel instead of theirs.

## Setup

1. **Generate your session string** (one-time, run locally):
   ```
   pip install telethon
   python generate_session.py
   ```
   Enter your API_ID, API_HASH, phone number, and the OTP Telegram sends
   you. Copy the printed string — that's your `SESSION_STRING`.

2. **Create your bot** via [@BotFather](https://t.me/BotFather) if you
   haven't already, and grab its token.

3. **Deploy to Render:**
   - Push this folder to a GitHub repo (or use Render's "Deploy from Git")
   - Render will read `render.yaml` automatically
   - Set these environment variables in the Render dashboard:
     - `API_ID` — from [my.telegram.org](https://my.telegram.org)
     - `API_HASH` — from my.telegram.org
     - `BOT_TOKEN` — from @BotFather
     - `SESSION_STRING` — from step 1
     - `BOT_USERNAME` — your clone bot's own `@username`, without the `@`

4. Deploy. Check the Render logs for:
   ```
   ✅ User session started: ...
   🎯 Target bot resolved: @ASxAura_bot
   ✅ Bot started: @YourBotUsername
   ```

## Notes

- `TARGET_BOT` and `CHANNEL_URL` already have sensible defaults in
  `render.yaml` — only change them if either changes.
- This bot is command-driven (relays `/start`, `/AS_AuraX`, `/sk`,
  `/bypass <link>`, `/help`, etc. as typed) — there's no button-click
  simulation like the video-quality bot needed, since AS AuraX doesn't
  use inline buttons for its core flow.
