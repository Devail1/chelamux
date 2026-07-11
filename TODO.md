# TODO

## Open

- [ ] Fold: port the incremental transcript monitor into `chela/telegram/` (the outbound-relay foundation; NO Telegram wiring yet). Create `chela/telegram/monitor.py` (+ a parser helper) adapted from ccbot's `~/projects/ccbot/src/ccbot/session_monitor.py` and `transcript_parser.py` (six-ddc, MIT — keep attribution headers). Behaviour: given a set of tmux window ids (`@N`), poll each window's Claude Code JSONL transcript, incrementally read only NEW lines by byte offset, pair `tool_use`↔`tool_result`, and emit each new user/assistant/tool message via a callback. **LOCKED ARCHITECTURAL CONSTRAINT:** resolve window→transcript via chela's existing layer (`chela.discovery` + `chela.transcripts.transcript_for_cwd` / the window registry) — do NOT use ccbot's `session_map.json` `SessionStart` hook. This slice does NOT send to Telegram and does NOT import `python-telegram-bot` — it just turns JSONL into parsed message events via a callback. Unit-test against a sample JSONL fixture (incremental offset advance + tool pairing). Keep `uv run ruff check chela tests` green. Reference: `chela/{discovery,transcripts}.py`.

## Done

- [x] Add a `telegram-setup` skill (PR #14).
- [x] Scaffold the `chela/telegram/` package + `[telegram]` extra (PR #15).
- [x] Dashboard auto-discovers dispatcher workflows (PR #16).
