---
name: telegram-setup
description: Wire Telegram for chela — create a bot with @BotFather, make a private forum group with Topics, find the chat id, put TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID in chela's secrets file, and run the `chela telegram` bridge (one topic per agent window, two-way). Use when the user wants to connect Telegram, drive agents from their phone, or configure the telegram-send skill.
---

# Wire Telegram for chela

`chela telegram` is chela's Telegram bridge: every agent window gets its own **forum
topic**, the agent's output is relayed into it, and whatever you type in the topic goes to
that agent's prompt. This skill walks through the one-time setup. The same two secrets also
feed the [`telegram-send`](../telegram-send/SKILL.md) skill.

| Env var | |
|---------|--|
| `TELEGRAM_BOT_TOKEN` | Bot token from @BotFather (required) |
| `TELEGRAM_CHAT_ID` | The forum group's chat id (required) |
| `TELEGRAM_TOPIC_ID` | Only for `chela telegram --wid` (single-window mode) and `telegram-send` |

Walk the user through the steps below in order. **Never** paste a real token or id into
committed code, docs, or examples — everything here uses placeholders.

## 0. Install the extra

```bash
uv sync --all-extras          # or name every extra you want: --extra dashboard --extra telegram
```

`uv sync --extra telegram` on its own **drops** the dashboard extra — `uv sync` replaces the
environment rather than adding to it.

## 1. Create a bot and get the token

In Telegram, open a chat with [@BotFather](https://t.me/BotFather):

1. Send `/newbot`.
2. Give it a display name and a username ending in `bot` (e.g. `my_chela_bot`).
3. BotFather replies with a token that looks like `123456789:AAExampleTokenReplaceMe`.

That token is `TELEGRAM_BOT_TOKEN`. Treat it like a password — anyone with it controls
the bot. If it leaks, `/revoke` in @BotFather to rotate it.

## 2. Make a private forum group

1. Create a new group **with only you in it**, and turn on **Topics** in the group
   settings (that makes it a forum supergroup).
2. Add the bot and make it an **admin** with **Manage Topics** — the bridge creates a topic
   per agent window and closes it when the window dies.

⚠️ **The chat is the security boundary.** The bridge accepts messages from the bound chat
id, and anything typed in a topic reaches that agent's prompt. Anyone you add to the group
can drive your agents.

## 3. Find the chat id

Post any message in the group, then read it back (swap in your token):

```bash
curl -s "https://api.telegram.org/bot<TOKEN>/getUpdates" | python3 -m json.tool
```

Read `result[].message.chat.id` — a negative number like `-1001234567890`. That is
`TELEGRAM_CHAT_ID`. If `result` is empty, the bot hasn't seen a message yet: post one and
retry. (`getUpdates` returns nothing while a webhook is set, or while a running
`chela telegram` is already consuming updates — stop it first.)

For `telegram-send` into a specific topic, post in that topic and read
`result[].message.message_thread_id` — that is `TELEGRAM_TOPIC_ID`.

## 4. Store the secrets

Secrets go in `~/.chela/secrets.env` (`$CHELA_DIR/secrets.env`), never in `chela.env` and
never in the repo:

```bash
umask 077 && printf 'TELEGRAM_BOT_TOKEN=%s\nTELEGRAM_CHAT_ID=%s\n' \
  '123456789:AAExampleTokenReplaceMe' '-1001234567890' > ~/.chela/secrets.env
```

`scripts/run-chela.sh` sources it for every service a process manager starts. For a
one-off run from a shell, load it first: `set -a; . ~/.chela/secrets.env; set +a`.

## 5. Run the bridge

```bash
chela telegram
```

Or as a service: `examples/ecosystem.config.js` already defines `chela-telegram`.

- **Auto-topics** is the default: a reconcile loop gives each live agent window a topic,
  archives a dead window's topic, and unbinds a window (without killing it) when you close
  its topic. A **dispatched** agent gets a topic only once it blocks on a human; set
  `CHELA_TELEGRAM_BIND_DISPATCHED=true` to bind every one.
- Bindings persist in `$CHELA_DIR/telegram-bindings.json` (override with
  `CHELA_TELEGRAM_BINDINGS`).
- Manual mode: `chela telegram --bind @3:42` (window `@3` ↔ topic `42`, repeatable), or
  `--wid @3` with `TELEGRAM_TOPIC_ID` set. `--no-inbound` relays output only.
- In a topic: `/new` starts a Claude session (browse to a folder), `/screenshot` snapshots
  the terminal, `/esc` interrupts. `/clear` and `/compact` are forwarded to Claude Code, as
  is any other text.

## 6. Verify

```bash
chela doctor                # among the rest: which windows the bridge has bound to topics
python skills/telegram-send/send.py "chela is wired to Telegram ✅"   # needs TELEGRAM_TOPIC_ID or lands in General
```

Then open a topic the bridge created and type into it — the agent should receive it. If a
send fails:

- **401 Unauthorized** — bad `TELEGRAM_BOT_TOKEN`.
- **400 chat not found** — wrong `TELEGRAM_CHAT_ID`, or the bot isn't in the group.
- **400 not enough rights to create a topic** — the bot isn't an admin with Manage Topics.
- **400 message thread not found** — wrong or stale `TELEGRAM_TOPIC_ID`.

## Notes

- **Phone pings when an agent blocks are a separate channel.** The needs-input notifier
  reads `CHELA_NOTIFY_URL` (ntfy, a Telegram `sendMessage` URL, or a webhook), not these
  variables. A Telegram URL embeds the bot token, so it belongs in `secrets.env` too.
