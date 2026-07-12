# TODO

## Open — chela telegram polish

- [x] **Name auto-created Telegram topics by the agent's PROJECT (cwd basename), not the tmux window name.** `chela/telegram/reconcile.py` currently provisions topics with `create_topic(name=<tmux window display name>)` → generic, misleading "shell-1..4" (e.g. shell-1 is actually nautilus). Add a **pure, testable** helper — `topic_name_for(cwd, window_name)` — that returns the **basename of the agent's cwd** (resolved via `chela.discovery.get_window_cwd_by_id`; e.g. `/home/liavedunix/projects/chelamux` → `chelamux`), and **falls back to the tmux window display name** when the cwd is the user's home dir, `/`, or empty (so a `~`-rooted session doesn't become "liavedunix"). Use it for the `name=` passed to `create_topic` in the reconcile provision step. **Don't touch** routing / lifecycle / the `BindingRegistry` contract (this is names only). Unit-test the helper: a project path → basename; home/root/empty cwd → the fallback window name (inject cwd + name — NO live tmux/Telegram). Keep `uv run ruff check chela tests` green + `uv run pytest -q`. Report back. Reference: `chela/telegram/reconcile.py`, `chela/discovery.py::get_window_cwd_by_id`.

## Backlog (not yet dispatchable)

- **Dashboard consolidation** — "consolidate the ccbot engine into the dashboard" (Liav's acceptance criterion). SHAPE UNDECIDED: (a) dashboard SURFACES the bridge (Telegram panel reading `~/.chela/telegram-bindings.json`; bridge stays a separate pm2 proc — lighter, crash-isolated, recommended) vs (b) bridge runs INSIDE the dashboard process (one pm2 app; PTB-asyncio-in-Flask fiddlier). Its own design step.
- **Retire ccbot** ~07-19 after warm standby: `pm2 delete ccbot` + `pm2 save` + archive repo (ops, not a dispatch task).
- Interactive UI: AskUserQuestion / ExitPlanMode / Permission phone approvals + `/esc` + `/screenshot` + message merging (ccbot parity — Decision 4 v1 scope).
- Privacy scrub (forum IDs, `CCBOT_PANE_FALLBACK`, absolute paths → env-driven) before any of this touches public `main`.
- **Cost view** (cockpit): aggregate transcript token usage × model price → $ per agent / per dispatch-run / fleet-total.
- **Unified graph viewer** (cockpit): force-directed canvas = memory `[[wikilinks]]` graph + live fleet. Renderer = **Sigma.js + Graphology (MIT, WebGL, bundleable)**, clean-room (NOT a GitNexus fork — PolyForm-NC). Colorblind-safe; post-fold unified source.

## Done

- [x] `telegram-setup` skill (PR #14) · scaffold pkg + `[telegram]` extra (#15) · dashboard workflow discovery (#16) · monitor port (#17) · outbound relay + CLI (#18).
- [x] Fold step 1 — `chela drive` paste-submit fix (PR #19, CMX-6).
- [x] Mobile Kanban — Swipe + Rows (PR #20, CMX-7).
- [x] Fold step 2 — INBOUND routing, bidirectional (PR #21, CMX-8).
- [x] Fold step 3 — monitor resolve-by-newest-record (PR #22, CMX-9).
- [x] Multi-topic Slice A — N thread↔window registry (PR #23, CMX-10).
- [x] Multi-topic Slice B — auto-create per agent + lifecycle (PR #24, CMX-11).
- [x] **CUTOVER LIVE 2026-07-12** — ccbot stopped (warm standby), `chela-telegram` (pm2 id 12) bridges @chelamuxbot / reused test forum `-1003732570394` ↔ 4 agents (bidirectional confirmed).
