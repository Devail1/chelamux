# TODO

## Open

- [ ] Add a `telegram-setup` skill (`skills/telegram-setup/SKILL.md`) that walks a user through wiring Telegram for chela: get a bot token from @BotFather, find the chat id (via getUpdates) and the forum topic id, and set `TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID` / `TELEGRAM_TOPIC_ID`. Mirror the style of `skills/chela-setup` and `skills/telegram-send`; keep it generic and public-safe (no real tokens/ids); add a row for it to `skills/README.md`.
