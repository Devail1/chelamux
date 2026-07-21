---
name: telegram-setup
description: Wire Telegram for chela — get a bot token from @BotFather, find the chat id (via getUpdates) and the forum topic id, and set TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID / TELEGRAM_TOPIC_ID. Use when the user wants to connect Telegram, enable phone notifications, or configure the telegram-send skill.
---

# Wire Telegram for chela

Set up a Telegram bot so agents can reach you on your phone — status pings, results,
charts, or a surfaced decision. This is the one-time config the [`telegram-send`](../telegram-send/SKILL.md)
skill (and chela's needs-input notifications) read from three environment variables:

| Env var | |
|---------|--|
| `TELEGRAM_BOT_TOKEN` | Bot token from @BotFather (required) |
| `TELEGRAM_CHAT_ID` | Target chat/group id (required) |
| `TELEGRAM_TOPIC_ID` | Forum topic id / `message_thread_id` (optional) |

Walk the user through the steps below in order. **Never** paste a real token or id into
committed code, docs, or examples — everything here uses placeholders, and the final
values go in the user's shell env or a gitignored `.env`.

## 1. Create a bot and get the token

In Telegram, open a chat with [@BotFather](https://t.me/BotFather):

1. Send `/newbot`.
2. Give it a display name and a username ending in `bot` (e.g. `my_chela_bot`).
3. BotFather replies with a token that looks like `123456789:AAExampleTokenReplaceMe`.

That token is `TELEGRAM_BOT_TOKEN`. Treat it like a password — anyone with it controls
the bot. If it leaks, `/revoke` in @BotFather to rotate it.

## 2. Find the chat id

The bot can only message a chat it has "seen." **Send at least one message** to/from the
target chat first, then read it back from the API:

- **Direct message** — open a DM with your bot and send it any message (e.g. `hi`).
- **Group** — add the bot to the group, then send a message that mentions it (or, if it's
  not an admin, run `/setprivacy` → *Disable* in @BotFather first so it can see plain
  messages).

Then fetch recent updates (swap in your token):

```bash
curl -s "https://api.telegram.org/bot<TOKEN>/getUpdates" | python3 -m json.tool
```

Read `result[].message.chat.id` from the JSON. That value is `TELEGRAM_CHAT_ID`.

- **Private chats** are positive (e.g. `12345678`).
- **Groups / supergroups** are negative (e.g. `-1001234567890`).

If `result` is empty, the bot hasn't seen a message yet — send one and retry. (Note:
`getUpdates` and webhooks are mutually exclusive; if a webhook is set, `getUpdates` returns
nothing until you `deleteWebhook`.)

## 3. Find the forum topic id (optional)

Only needed if you want messages to land in a specific **topic** of a forum-enabled
supergroup (Telegram groups with "Topics" turned on). Otherwise skip this — leave
`TELEGRAM_TOPIC_ID` unset and messages go to the group's General thread.

To get it: post a message **in the target topic**, then run the same `getUpdates` call from
step 2 and read `result[].message.message_thread_id`. That value is `TELEGRAM_TOPIC_ID`.

(Alternatively, open the topic in Telegram Web/Desktop and copy the trailing number from the
URL — but `getUpdates` is the reliable, cross-client way.)

## 4. Set the environment variables

Put the three values in your shell env or a gitignored `.env` — **never** commit them:

```bash
export TELEGRAM_BOT_TOKEN="123456789:AAExampleTokenReplaceMe"
export TELEGRAM_CHAT_ID="-1001234567890"
export TELEGRAM_TOPIC_ID="42"        # omit if not using forum topics
```

Add them to `~/.bashrc` (or `~/.zshrc`) to persist across shells, or to a local `.env`
you load — just make sure `.env` is in `.gitignore`.

## 5. Verify

Confirm the wiring end-to-end with the `telegram-send` skill:

```bash
python skills/telegram-send/send.py "chela is wired to Telegram ✅"
```

You should get the message on your phone. If it fails:

- **401 Unauthorized** — bad `TELEGRAM_BOT_TOKEN`.
- **400 chat not found** — wrong `TELEGRAM_CHAT_ID`, or the bot has never seen that chat
  (redo step 2).
- **400 message thread not found** — wrong or stale `TELEGRAM_TOPIC_ID`; unset it to fall
  back to the General thread.

## Notes

- Keep the token and ids out of source control — env or gitignored `.env` only.
- Once set, both the `telegram-send` skill and chela's needs-input notifications reuse the
  same three variables — no per-tool config.
