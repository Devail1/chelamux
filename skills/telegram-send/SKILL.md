---
name: telegram-send
description: Send a message or file to Telegram from an agent — push a result, chart, log, or notification to a chat/topic via the Telegram Bot API. Use when an agent needs to notify the user or deliver an artifact out-of-band (e.g. "send me the chart", proactive status pings, or surfacing a decision to your phone).
---

# Send to Telegram

Push a message or a file to Telegram from any agent — a result, a chart, a log tail, or a
heads-up — so the human gets it on their phone without watching the terminal. Composes with
the `orchestrate` skill: the fleet can reach you proactively.

Dependency-free (Python stdlib only). Configure via environment:

| Env var | |
|---------|--|
| `TELEGRAM_BOT_TOKEN` | Bot token from @BotFather (required) |
| `TELEGRAM_CHAT_ID` | Target chat/channel id (required) |
| `TELEGRAM_TOPIC_ID` | Forum topic id / `message_thread_id` (optional) |

## Use

```bash
# a message (auto-split at Telegram's 4096-char limit)
python skills/telegram-send/send.py "backtest done — Sharpe 1.4, DD 8%"

# a file (chart, log, artifact) with a caption
python skills/telegram-send/send.py --file ./equity_curve.png --caption "equity curve"
```

`send_message(text)` and `send_file(path, caption)` are importable if you'd rather call them
from Python directly.

## Getting the config

- **Bot token** — talk to [@BotFather](https://t.me/BotFather): `/newbot`, then copy the token.
- **Chat id** — message your bot, then open
  `https://api.telegram.org/bot<TOKEN>/getUpdates` and read `result[].message.chat.id`
  (group/channel ids are negative).
- **Topic id** (forum groups only) — the `message_thread_id` of the topic you want to post into.

Keep the token out of source control — set it in your shell env or a local `.env` that's gitignored.
