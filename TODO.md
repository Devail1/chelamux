# TODO

## Open — chela telegram bridge commands

- [ ] **Bridge commands: `/screenshot` + `/esc` + the Telegram command menu (port ccbot's slash-commands).** chela's inbound currently forwards EVERY `/command` straight to Claude Code via `send_tmux`, so bridge-level commands don't work and there's no "/" autocomplete. Add:
  1. **Command interception** — in the inbound path (`chela/telegram/inbound.py`, the PTB `_on_message` / a command handler), intercept **bridge commands** (`/screenshot`, `/esc`) BEFORE routing to `send_tmux`, so they're NOT typed into the prompt. Everything else (incl. Claude-native `/clear`,`/model`) forwards as today. Still gate on the bound `chat_id` + resolve the topic→window via the `BindingRegistry` (`window_for_thread`); reply/act on THAT window and post results back to the SAME topic (`message_thread_id`).
  2. **`/esc`** — send a bare Escape key to the bound window (interrupt Claude). Add a `messenger.send_escape(window_id)` (tmux `send-keys Escape`) — do NOT reuse `send_tmux`'s text path. Confirm back to the topic ("⎋ sent").
  3. **`/screenshot`** — capture the bound window's pane WITH ANSI (`tmux capture-pane -p -e`), render the terminal text → **PNG** (port ccbot's `src/ccbot/screenshot.py::text_to_image` — Pillow + ANSI parsing + a monospace font; keep six-ddc MIT attribution), and **send it as a photo** to the topic via the PTB bot (`context.bot.send_photo(chat_id, photo=<png bytes>, message_thread_id=thread)` — the inbound handler already has the bot; NO urllib multipart needed). ⚠️ capture-pane is READ-ONLY (safe — NOT the tmux-teardown that once killed ccbot; do not add any teardown).
  4. **Command menu** — on startup (in `build_application` / `cmd_telegram`) call `bot.set_my_commands([BotCommand("screenshot","Terminal screenshot"), BotCommand("esc","Send Escape to interrupt Claude")])` so Telegram shows the "/" preview.
  5. **Dependency** — add **Pillow** to the `[telegram]` extra in `pyproject.toml`. Font: port ccbot's font handling; if it bundles a TTF, bundle an **open-licensed monospace** (OFL/Apache — check the license, note it) in the package, with a graceful fallback. Document the font choice.

  **Don't touch** routing/registry/lifecycle/outbound-relay contracts, `parser.py`, or `reconcile.py`. **Landmines:** `/screenshot` and `/esc` must NOT be forwarded to Claude (intercept first, match case-insensitively, allow `/screenshot@botname` suffix Telegram appends in groups); resolve the window from the *topic the command arrived on* (not a global default); NEVER surface the bot token. **Verify:** unit-test the command dispatch (a `/screenshot`/`/esc` message routes to the handler + resolves the right window, a normal message still forwards) + the `text_to_image` renderer produces non-empty PNG bytes for sample text — against stubs, NO live Telegram. Keep `uv run ruff check chela tests` green + `uv run pytest -q`. Report back. Reference: ccbot `src/ccbot/screenshot.py` (renderer to port, MIT), `src/ccbot/tmux_manager.py::capture_pane` (with_ansi), `src/ccbot/bot.py:1847-1859` (BotCommand list + `set_my_commands`) + its `/screenshot`/`/esc` command handlers, `chela/telegram/inbound.py`, `chela/messenger.py`, `chela/telegram/bindings.py`.

## Backlog (not yet dispatchable)

- **Settings view — editable toggles (follow-up)**: in-UI write-back + daemon restart.
- **Retire ccbot** ~07-19 after warm standby: `pm2 delete ccbot` + `pm2 save` + archive repo (ops).
- Interactive UI: AskUserQuestion / ExitPlanMode / Permission phone **approvals (buttons)** + message merging (the richer button-rendering version; `/screenshot`+`/esc` handled above).
- Privacy scrub (forum IDs, `CCBOT_PANE_FALLBACK`, abs paths → env) before public `main`.
- **Cost view** (cockpit): transcript tokens × price → $ per agent / run / fleet.
- **Unified graph viewer** (cockpit): memory `[[wikilinks]]` + fleet; renderer = **Sigma.js + Graphology (MIT, WebGL)**, clean-room (not a GitNexus fork — PolyForm-NC). Colorblind-safe.

## Done

- [x] telegram-setup skill (#14) · scaffold + extra (#15) · dashboard workflow discovery (#16) · monitor port (#17) · outbound relay + CLI (#18).
- [x] Fold steps 1-3 — paste-submit (#19), INBOUND (#21), monitor resolve-by-record (#22) [CMX-6/8/9].
- [x] Mobile Kanban — Swipe + Rows (#20, CMX-7).
- [x] Multi-topic Slice A registry (#23) + Slice B auto-create/lifecycle (#24) [CMX-10/11].
- [x] Topic naming by project cwd (#25, CMX-12).
- [x] `CHELA_SHOW_TOOL_CALLS` toggle — hide tool spam (#26, CMX-13).
- [x] Settings view — Connections & Status (#27, CMX-14) + Telegram bridge row (#28, CMX-15) + Work-dispatcher-badge fix (auto-discovery signal).
- [x] **CUTOVER LIVE 2026-07-12** — `chela-telegram` (pm2 id 12) on @chelamuxbot / test forum ↔ 4 agents (chelamux/nautilus/strategy-lab/orchestrator); tool-spam hidden.
