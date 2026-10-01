---
name: telegram-send
description: Send a message or file to Telegram from an agent — push a result, chart, log, or notification to a chat/topic via the Telegram Bot API. Use when an agent needs to notify the user or deliver an artifact out-of-band (e.g. "send me the chart", proactive status pings, or surfacing a decision to your phone).
---

# Send to Telegram

Push a message or a file to Telegram from any agent — a result, a chart, a log tail, or a
heads-up — so the human gets it on their phone without watching the terminal. Composes with
the `orchestrate` skill: the fleet can reach you proactively.

Dependency-free (Python stdlib only). Configure via environment — the same bot and chat as
the `chela telegram` bridge ([`telegram-setup`](../telegram-setup/SKILL.md)):

| Env var | |
|---------|--|
| `TELEGRAM_BOT_TOKEN` | Bot token from @BotFather (required) |
| `TELEGRAM_CHAT_ID` | Target chat/group id (required) |
| `TELEGRAM_TOPIC_ID` | Forum topic id / `message_thread_id` (optional; unset → the General thread) |

The token and chat id live in `~/.chela/secrets.env`. A service started through
`scripts/run-chela.sh` already has them; from a shell, `set -a; . ~/.chela/secrets.env; set +a`.

## Use

```bash
# a message (auto-split at Telegram's 4096-char limit)
python skills/telegram-send/send.py "backtest done — Sharpe 1.4, DD 8%"

# a file (chart, log, artifact) with a caption
python skills/telegram-send/send.py --file ./equity_curve.png --caption "equity curve"
```

`send_message(text)` and `send_file(path, caption)` are importable if you'd rather call them
from Python directly.

## When not to use it

- **Talking to an agent that has a bridge topic?** Its own output already reaches its topic
  through `chela telegram`. Use this for out-of-band pushes, not to duplicate the relay.
- **A decision for the human** — prefer `chela escalate "…" --recommend "…"`: it records the
  decision in the event log and pushes it over `CHELA_NOTIFY_URL`.
- To post into an agent's own topic, read its thread id from
  `$CHELA_DIR/telegram-bindings.json` and set `TELEGRAM_TOPIC_ID` for the call.

Keep the token out of source control — `secrets.env` (`chmod 600`) only.
