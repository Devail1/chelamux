# TODO

## Open

- [x] Fold: OUTBOUND relay + a minimal `chela telegram` CLI (first runnable bridge slice; outbound only). Add `chela/telegram/relay.py` that consumes `chela/telegram/monitor.py`'s `TranscriptMonitor` message callback and posts each new message to a Telegram topic via the **direct Bot API** (reuse the approach in `skills/telegram-send/send.py` — `sendMessage` + 4096-char split; do NOT pull in `python-telegram-bot` for outbound — PTB arrives with the inbound task). Port MarkdownV2 formatting from ccbot's `~/projects/ccbot/src/ccbot/markdown_v2.py` (six-ddc, MIT — keep attribution) with a plain-text fallback on format failure. Add a `chela telegram` subcommand to `chela/main.py` that: reads `TELEGRAM_BOT_TOKEN`/`TELEGRAM_CHAT_ID`/`TELEGRAM_TOPIC_ID` from env, binds ONE window (`--wid @N`) to the topic, runs the monitor, and relays that window's new output to the topic. Inbound (Telegram→tmux) is the NEXT task — outbound only here. Unit-test the relay/formatting against a stub sender (NO live Telegram calls in tests). Keep `uv run ruff check chela tests` green. Reference: `chela/telegram/monitor.py`, `skills/telegram-send/send.py`.

## Done

- [x] Add a `telegram-setup` skill (PR #14).
- [x] Scaffold the `chela/telegram/` package + `[telegram]` extra (PR #15).
- [x] Dashboard auto-discovers dispatcher workflows (PR #16).
- [x] Port the incremental transcript monitor to `chela/telegram/` (PR #17).
