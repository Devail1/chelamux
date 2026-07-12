# TODO

## Open — migration critical path (retire ccbot; ordered, concurrency 1)

- [x] **Fold: multi-topic routing SLICE B — auto-create a topic per agent + lifecycle (the "magic"; builds on Slice A's `BindingRegistry`).** Slice A gave us N↔N routing over a persisted registry; Slice B POPULATES that registry automatically. Add a window-watch reconcile loop to the `chela telegram` daemon:
  1. **Provision**: enumerate windows via `chela.discovery`; for each **AGENT (Claude) window** (use discovery's type classification — NOT shells/servers) that has no binding in the registry, call the Telegram **`createForumTopic(chat_id, name=<agent/window display name>)`** Bot API (direct urllib, mirror `relay.py`'s transport — do NOT pull new deps), take the returned `message_thread_id`, `registry.bind(window_id, thread_id)`, and `registry.save()`.
  2. **Window death** → `closeForumTopic(chat_id, message_thread_id)` (archive, don't delete) + `registry.unbind(window_id)` + save.
  3. **Telegram topic-closed event** (PTB `StatusUpdate.FORUM_TOPIC_CLOSED`) → `registry.unbind` ONLY. **Do NOT kill the agent** (Liav default — differs from ccbot deliberately).
  4. **Restart reconcile**: on startup, load the persisted registry, then reconcile against live windows — provision topics for new agent windows, unbind dead ones. Idempotent.
  5. **Wire into `cmd_telegram`**: run the reconcile loop on an interval alongside inbound (PTB) + outbound (monitor). Gate behind an `--auto-topics` flag (default the daemon to it when no `--wid`/`--bind` given; `--wid`/`--bind` stays manual back-compat).
  6. **Hardening**: suppress httpx INFO logging (the bot token leaks in the URL at INFO → would hit pm2 logs) — set the `httpx`/`telegram` loggers to WARNING in the daemon.

  **Defaults (locked by Liav):** agent-windows-only; topic-close→unbind-not-kill; window-death→close-topic; restart→reconcile. **Don't touch** Slice A's `BindingRegistry`/`RegistryRouter`/`RegistryRelay` routing contracts, `send_tmux`, or `parser.py`. **Landmines:** `createForumTopic` needs the bot to be a **forum admin with _manage topics_** perm (a HUMAN prereq — unit tests must NOT call live Telegram); reconcile must be idempotent (never double-create a topic for a window that already has a binding); a window rename should NOT orphan its topic (match by window_id, not name). **Verify:** unit-test the reconcile loop against a **stub Telegram API** (fake createForumTopic/closeForumTopic returning canned thread_ids) + a fake `discovery` window set — assert: new agent window → one createForumTopic + bind; non-agent window → skipped; dead window → closeForumTopic + unbind; restart with a persisted binding → NO duplicate create; topic-closed event → unbind only. NO live Telegram in tests. Keep `uv run ruff check chela tests` green + `uv run pytest -q`. Report back — do NOT start the next task. Reference: ccbot `src/ccbot/bot.py` (`topic_closed_handler`, `_create_and_bind_window`), `chela/telegram/bindings.py`, `chela/telegram/relay.py` (transport), `chela/telegram/inbound.py`, `chela/main.py::cmd_telegram`, `chela/discovery.py`.

## Backlog (not yet dispatchable)

- Cutover runbook + safety test: stop ccbot (PM2) → start `chela telegram --auto-topics` on the real token, same `ccbot` session; test-session isolation + cross-session-teardown assertion. ccbot warm-standby ~1 week → retire.
- Interactive UI: AskUserQuestion / ExitPlanMode / Permission phone approvals + `/esc` + `/screenshot` + message merging (ccbot parity — Decision 4 v1 scope).
- Privacy scrub (forum IDs, `CCBOT_PANE_FALLBACK`, absolute paths → env-driven) before any of this touches public `main`.
- **Cost view** (cockpit): aggregate transcript token usage × model price → $ per agent / per dispatch-run / fleet-total.
- **Unified graph viewer** (cockpit, "Both"): force-directed canvas = memory/knowledge graph (`[[wikilinks]]`) + live fleet (orchestrator→agents→tasks). Renderer = **Sigma.js + Graphology (both MIT, WebGL, bundleable — no CDN)**, NOT d3-force (WebGL scales past a few hundred nodes; d3 chokes). Clean-room, our own data — do NOT fork GitNexus (PolyForm-Noncommercial poisons chela's MIT). Colorblind-safe node states; post-fold unified source.

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
- [x] Fold multi-topic routing Slice A — N thread↔window bindings in one process (PR #23, CMX-10).
