# TODO

## Open — chela dashboard/telegram

- [ ] **Settings view — add the missing Telegram BRIDGE row.** CMX-14 shipped the "Connections & Status" surface (`/api/settings` in `chela/dashboard/app.py::_settings_status`) but it surfaces tmux/collab/notify/wall/dispatcher/scheduler/tool-call-relay and is **MISSING the actual Telegram bridge** — the core thing. Add a **"Telegram bridge"** item to the **Connections** section, matching the existing item shape (`{label, state, detail, on}`):
  - **Running?** detect the `chela telegram` daemon (e.g. `pgrep -f "chela telegram"` — pick a reliable signal, document it) → `on: true` "Connected" vs `on: false` "Off".
  - **Bindings** — read `~/.chela/telegram-bindings.json`: the forum `chat_id` + **N agents bound**; put the count in `state`/`detail` and, if easy, list each **agent(window-name) → topic** (resolve window→name via `chela.discovery`). Degrade gracefully (missing/empty file → "Off"/"0 agents", never error).
  - ⚠️ **NEVER surface the bot token** — `bindings.json` has none; do NOT read `~/.chela/telegram.env`'s token into the response. (Reuse CMX-14's masking discipline + its no-token-leak test pattern — add a bindings-based row without regressing it.)
  - Colorblind-safe rendering already lives in `nav.js::renderSettings` (`●`/`○` + label) — just emit the item; no CSS/JS changes needed unless the mapping list needs markup.
  **Don't touch** the other status rows, routing, or the masking helpers. Unit-test the bridge-status aggregation with a stubbed bindings file + a stubbed process check (running vs not; present vs missing file). Keep `uv run ruff check chela tests` green + `uv run pytest -q`. Report back. Reference: `chela/dashboard/app.py::_settings_status` (add an item to the Connections section), `~/.chela/telegram-bindings.json`, `chela/discovery.py`, `tests/test_settings_status.py`.

## Backlog (not yet dispatchable)

- **Settings view — editable toggles (follow-up)**: in-UI write-back (flip `SHOW_TOOL_CALLS`, edit telegram env) + daemon restart from the dashboard. Higher risk.
- **Retire ccbot** ~07-19 after warm standby: `pm2 delete ccbot` + `pm2 save` + archive repo (ops).
- Interactive UI: AskUserQuestion / ExitPlanMode / Permission phone **approvals (buttons)** + `/esc` + `/screenshot` + message merging.
- Privacy scrub (forum IDs, `CCBOT_PANE_FALLBACK`, abs paths → env) before public `main`.
- **Cost view** (cockpit): transcript tokens × price → $ per agent / run / fleet.
- **Unified graph viewer** (cockpit): memory `[[wikilinks]]` + fleet; renderer = **Sigma.js + Graphology (MIT, WebGL)**, clean-room (not a GitNexus fork — PolyForm-NC). Colorblind-safe.

## Done

- [x] telegram-setup skill (#14) · pkg scaffold + `[telegram]` extra (#15) · dashboard workflow discovery (#16) · monitor port (#17) · outbound relay + CLI (#18).
- [x] Fold steps 1-3 — paste-submit (#19), INBOUND (#21), monitor resolve-by-record (#22) [CMX-6/8/9].
- [x] Mobile Kanban — Swipe + Rows (#20, CMX-7).
- [x] Multi-topic Slice A registry (#23) + Slice B auto-create/lifecycle (#24) [CMX-10/11].
- [x] Topic naming by project cwd (#25, CMX-12).
- [x] `CHELA_SHOW_TOOL_CALLS` toggle — hide tool spam by default (#26, CMX-13).
- [x] Settings view — live "Connections & Status" read-only surface (#27, CMX-14) [missing telegram-bridge row → follow-up above].
- [x] **CUTOVER LIVE 2026-07-12** — ccbot warm standby; `chela-telegram` (pm2 id 12) on @chelamuxbot / test forum ↔ 4 agents.
