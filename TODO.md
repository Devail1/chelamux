# TODO

## Open — migration critical path (retire ccbot; ordered, concurrency 1)

- [x] **Fold: multi-topic routing SLICE A — generalize the bridge from one window to a registry of N thread↔window bindings (routing only; NO Telegram topic-creation — that's Slice B).** Today `chela telegram --wid @N` binds ONE window to ONE topic (`chela/telegram/inbound.py::TopicRouter` + the outbound relay's fixed `TELEGRAM_TOPIC_ID`). Generalize to N agents ↔ N topics in ONE process:
  1. **`BindingRegistry`** (new, e.g. `chela/telegram/bindings.py`): holds the supergroup `chat_id` + a bidirectional map `thread_id ↔ window_id`; methods `bind(window_id, thread_id)`, `unbind(window_id)`, `window_for_thread(thread_id)`, `thread_for_window(window_id)`, and JSON load/save to a config path (default `~/.chela/telegram-bindings.json`, env-overridable). Persisted so it survives restart. Pure/testable, no Telegram calls.
  2. **Inbound (generalize `TopicRouter`)**: the PTB text handler looks up `registry.window_for_thread(thread_id)`; deliver via `messenger.send_tmux` (the reliable-submit path — do NOT reimplement). Still gate on the bound `chat_id` (security boundary from CMX-8). Unbound topic → drop + debug-log.
  3. **Outbound (generalize the relay)**: `chela/telegram/relay.py`'s sender currently posts to a single fixed topic; make it accept a **per-message `message_thread_id`** (int on the wire). The monitor already emits `(window_id, msg)` — look up `registry.thread_for_window(window_id)` and post to THAT topic. Keep the MarkdownV2→plain-text fallback. A window with no binding → skip (don't post).
  4. **`chela telegram` daemon**: consume the registry — the outbound monitor polls ALL bound `window_id`s (not one `--wid`); inbound routes by thread via the registry. Keep single-window back-compat: `--wid @N` + `TELEGRAM_TOPIC_ID` seeds a one-entry registry. Bindings are otherwise loaded from the persisted file (Slice B will POPULATE it via `createForumTopic`; Slice A just consumes/persists — seed manually or via a `--bind @N:<thread_id>` flag for testing).

  **Defaults (locked by Liav):** only agent/Claude windows get bound (Slice B enforces at create-time; Slice A routes whatever's in the registry). **Don't touch** `send_tmux`, `parser.py`, or the monitor's incremental byte-offset read (reuse it across multiple windows). **Landmines:** `message_thread_id` must be an int off the wire but registry keys may be str — normalize (str compare like CMX-8's `TopicRouter`); a forum's General topic has no thread_id → treat as unbound. **Verify:** unit-test `BindingRegistry` (bind/unbind/lookup/persist round-trip) + inbound routing (bound→window, unbound→drop, wrong-chat→drop) + outbound target selection (window→correct thread_id, unbound window→skip) against a stub sender — NO live Telegram. Live multi-topic validation happens after Slice B (auto-create) lands. Keep `uv run ruff check chela tests` green + `uv run pytest -q`. Report back — do NOT start the next task (Slice B). Reference: `chela/telegram/inbound.py`, `chela/telegram/relay.py`, `chela/telegram/monitor.py`, `chela/main.py::cmd_telegram`, `chela/discovery.py`.

## Backlog (not yet dispatchable)

- **Fold multi-topic SLICE B — auto-create + lifecycle** (after Slice A): watch the session's windows via `discovery`; for each AGENT (Claude) window with no binding → `createForumTopic(chat_id, name=<agent>)` → bind + persist. Window dies → close/archive topic + unbind. Telegram topic-closed event → unbind ONLY (do NOT kill the agent). Restart → rebuild from disk, reconcile against live windows. Bot needs forum-admin *manage topics* perm (fold into `telegram-setup` skill). Suppress httpx INFO logging (token leaks into the URL at INFO → pm2 logs).
- Cutover runbook + safety test: stop ccbot (PM2) → start `chela telegram` on the real token, same `ccbot` session; test-session isolation + cross-session-teardown assertion. ccbot warm-standby ~1 week → retire.
- Interactive UI: AskUserQuestion / ExitPlanMode / Permission phone approvals + `/esc` + `/screenshot` + message merging (ccbot parity — Decision 4 v1 scope).
- Privacy scrub (forum IDs, `CCBOT_PANE_FALLBACK`, absolute paths → env-driven) before any of this touches public `main`.
- **Cost view** (cockpit): aggregate transcript token usage × model price → $ per agent / per dispatch-run / fleet-total.
- **Unified graph viewer** (cockpit, "Both"): Obsidian-style force-directed canvas = memory/knowledge graph (`[[wikilinks]]`) + live fleet (orchestrator→agents→tasks). Self-contained (bundle d3, no CDN), colorblind-safe, post-fold unified source.

## Done

- [x] `telegram-setup` skill (PR #14).
- [x] Scaffold `chela/telegram/` package + `[telegram]` extra (PR #15).
- [x] Dashboard auto-discovers dispatcher workflows (PR #16).
- [x] Port the incremental transcript monitor to `chela/telegram/` (PR #17).
- [x] OUTBOUND relay + minimal `chela telegram` CLI (PR #18).
- [x] Fold cutover step 1 — fix `chela drive` unsubmitted paste input (PR #19, CMX-6).
- [x] Mobile Kanban — Swipe carousel + Rows accordion (PR #20, CMX-7).
- [x] Fold cutover step 2 — INBOUND routing, bidirectional proven live (PR #21, CMX-8).
- [x] Fold cutover step 3 — monitor transcript resolution by newest record (PR #22, CMX-9).
