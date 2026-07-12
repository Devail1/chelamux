# TODO

## Open — migration critical path (retire ccbot; ordered, concurrency 1)

- [x] **Fold cutover step 2 — INBOUND routing (Telegram → tmux), the piece that lets us ditch ccbot.** Add the inbound half of the bridge: a python-telegram-bot `Application` (the `[telegram]` extra already pins PTB) that listens on the bound forum topic(s) and routes each incoming message to the mapped tmux window via `chela/messenger.py::send_tmux` (reuse the reliable-submit path — do NOT reimplement tmux sending). Reuse `chela.discovery`/`agent_manager` for topic↔window resolution (Decision 1: chela is the registry, NOT ccbot's `session_map.json`). Wire it into the `chela telegram` subcommand so ONE process does outbound (existing relay) AND inbound for the bound window — the daemon should relay that window's output to the topic AND deliver topic messages back to the window. Config stays env-driven (`TELEGRAM_BOT_TOKEN`/`CHAT_ID`/`TOPIC_ID`), validated against the TEST bot `@chelamuxbot` / topic 4 — NEVER the live ccbot token. Keep six-ddc MIT attribution on any ported bot glue. **Don't touch** the outbound relay/callback contract or `parser.py`. Unit-test topic→window routing against a stub sender (NO live Telegram). Keep `uv run ruff check chela tests` green + `uv run pytest -q`. Report back — do NOT start the next task. Reference: ccbot `src/ccbot/bot.py` + `handlers/`, `chela/telegram/relay.py`, `chela/messenger.py`, `chela/discovery.py`.

- [ ] **Fold cutover step 3 — monitor transcript resolution goes STALE on session change** (correctness for both directions). `chela/transcripts.py::transcript_for_cwd` picks the newest-**mtime** `*.jsonl` in the cwd's project dir; immediately after a `/clear` (which starts a new session_id → new jsonl) the PRE-clear file is momentarily newest (its last entry is the `/clear` marker) while the fresh session hasn't written yet, so the relay binds the wrong transcript. Make resolution current-session-correct: prefer the transcript whose newest **record** is latest (read each candidate's last JSONL entry's `timestamp`, not the file mtime), or otherwise detect the newer session and re-bind. `chela/telegram/monitor.py` already re-resolves per poll and resets `_Tracked` when `path` changes — verify that rotation still works after the fix (don't regress it). **Don't touch** the outbound-relay callback contract or `parser.py`. Add a unit test simulating two jsonl files in one project dir (an old/pre-clear file with a bumped mtime but older last-record timestamp, and a newer session) and assert the newer session is chosen. Keep `uv run ruff check chela tests` green + `uv run pytest -q`. Report back — do NOT start the next task. Reference: ccbot's `src/ccbot/session_monitor.py` + its `SessionStart` hook, `chela/transcripts.py::transcript_for_cwd`, `chela/telegram/monitor.py`.

## Backlog (not yet dispatchable)

- Interactive UI: AskUserQuestion / ExitPlanMode / Permission phone approvals + `/esc` + `/screenshot` + message merging (ccbot parity — Decision 4 v1 scope).
- Cutover runbook + safety test: stop ccbot (PM2) → start `chela telegram` on the real token, same `ccbot` session; test-session isolation + cross-session-teardown assertion. ccbot warm-standby ~1 week → retire.
- Privacy scrub (forum IDs, `CCBOT_PANE_FALLBACK`, absolute paths → env-driven) before any of this touches public `main`.
- **Cost view** (cockpit): aggregate transcript token usage × model price → $ per agent / per dispatch-run / fleet-total. Better after the fold (one source costs every agent).
- **Unified graph viewer** (cockpit, "Both"): Obsidian-style force-directed canvas = memory/knowledge graph (`[[wikilinks]]`) + live fleet (orchestrator→agents→tasks). Self-contained (bundle d3, no CDN), colorblind-safe node states, draws from post-fold unified source.

## Done

- [x] `telegram-setup` skill (PR #14).
- [x] Scaffold `chela/telegram/` package + `[telegram]` extra (PR #15).
- [x] Dashboard auto-discovers dispatcher workflows (PR #16).
- [x] Port the incremental transcript monitor to `chela/telegram/` (PR #17).
- [x] OUTBOUND relay + minimal `chela telegram` CLI (PR #18).
- [x] Fold cutover step 1 — fix `chela drive` unsubmitted paste input (PR #19, CMX-6).
- [x] Mobile Kanban — Swipe carousel + Rows accordion, colorblind-safe, persisted (PR #20, CMX-7).
