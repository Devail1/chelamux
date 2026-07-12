# TODO

## Open — chela dashboard/telegram

- [x] **Settings view → live "Connections & Status" (READ-ONLY this slice).** Upgrade the dashboard Settings drawer (`chela/dashboard/static/js/nav.js::renderSettings`) from static docs into a LIVE status surface. Add a backend `GET /api/settings` (in `chela/dashboard/app.py`) that aggregates READ-ONLY status; the drawer renders it as sections with **colorblind-safe status badges** (Liav is red-weak — use `●`/`○` shape + a text label like "Connected"/"Off", NEVER color alone):
  - **Telegram** — is the bridge running (detect via `pgrep -f "chela telegram"`, or a written heartbeat/status file — pick one, document it) + read `~/.chela/telegram-bindings.json` for `chat_id`/forum + **N agents bound** (list agent→topic). Show config: `CHELA_SHOW_TOOL_CALLS`, poll interval. ⚠️ **NEVER surface the bot token** — show a bot label or just "configured", masked.
  - **Notifications** — read `chela/config.py` `CHELA_NOTIFY_URL`/`CHELA_NOTIFY_KIND`: enabled? kind (ntfy/telegram/webhook) + target **with any embedded token stripped** (Telegram notify URLs contain the token).
  - **Collab** — relay URL + active shares count + default display name (from `chela/collab.py` / the collab config).
  - Keep the existing prefs sections (theme / fonts / projects folder) below, unchanged.
  READ-ONLY — no in-UI editing/write-back this slice (that's a follow-up). **Don't touch** the existing editable prefs, routing, or unrelated views. **Landmines:** secret-masking is load-bearing (tokens live in `telegram.env` + notify URLs — strip/omit them from `/api/settings`); the "is-running" signal must be reliable (a stale bindings file ≠ running — cross-check with the process). Lightweight-test the `/api/settings` aggregation + masking with stubbed config/files. Keep `uv run ruff check chela tests` green + `uv run pytest -q`. Report back. Reference: `chela/dashboard/app.py`, `chela/dashboard/static/js/nav.js::renderSettings` (existing section markup), `chela/config.py`, `chela/notify.py`, `chela/collab.py`, `~/.chela/telegram-bindings.json`.

## Backlog (not yet dispatchable)

- **Settings view — editable toggles (follow-up)**: in-UI write-back for the read-only view above (flip `SHOW_TOOL_CALLS`, edit telegram env) + daemon restart from the dashboard. Higher risk (write + restart from web app).
- **Dashboard consolidation (deeper)** — read-only settings view = the "surface" flavor; the "bridge INSIDE the dashboard process" (one pm2 app) alternative stays undecided.
- **Retire ccbot** ~07-19 after warm standby: `pm2 delete ccbot` + `pm2 save` + archive repo (ops).
- Interactive UI: AskUserQuestion / ExitPlanMode / Permission phone **approvals (buttons)** + `/esc` + `/screenshot` + message merging.
- Privacy scrub (forum IDs, `CCBOT_PANE_FALLBACK`, abs paths → env) before public `main`.
- **Cost view** (cockpit): transcript tokens × price → $ per agent / run / fleet.
- **Unified graph viewer** (cockpit): memory `[[wikilinks]]` + fleet; renderer = **Sigma.js + Graphology (MIT, WebGL)**, clean-room (not a GitNexus fork — PolyForm-NC). Colorblind-safe.

## Done

- [x] telegram-setup skill (#14) · pkg scaffold + `[telegram]` extra (#15) · dashboard workflow discovery (#16) · monitor port (#17) · outbound relay + CLI (#18).
- [x] Fold steps 1-3 — paste-submit fix (#19), INBOUND (#21), monitor resolve-by-record (#22) [CMX-6/8/9].
- [x] Mobile Kanban — Swipe + Rows (#20, CMX-7).
- [x] Multi-topic Slice A registry (#23) + Slice B auto-create/lifecycle (#24) [CMX-10/11].
- [x] Topic naming by project cwd (#25, CMX-12).
- [x] `CHELA_SHOW_TOOL_CALLS` toggle — hide tool spam by default, keep interactive prompts (#26, CMX-13).
- [x] **CUTOVER LIVE 2026-07-12** — ccbot warm standby; `chela-telegram` (pm2 id 12) on @chelamuxbot / test forum ↔ 4 agents (chelamux/nautilus/strategy-lab/orchestrator).
