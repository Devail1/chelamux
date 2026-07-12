# TODO

## Open — chela telegram polish

- [x] **Add `CHELA_SHOW_TOOL_CALLS` toggle — hide tool-call notifications by DEFAULT (port ccbot's `show_tool_calls`).** The outbound relay currently sends EVERY `tool_use` + `tool_result` as its own message (`🔧 Bash` / `✅ Bash result`) — noisy on a phone. Add an env flag **`CHELA_SHOW_TOOL_CALLS` (default `false` = HIDE)**. When `false`, the relay SKIPS `tool_use`/`tool_result` events — **EXCEPT interactive-prompt tools** (port ccbot's `INTERACTIVE_TOOL_NAMES`: `AskUserQuestion`, `ExitPlanMode`, and any permission-prompt tool), which **MUST still relay** so the human sees prompts. `text`, `thinking`, and `user` messages are ALWAYS relayed. When `true`, relay everything (today's behavior). Read the flag in `cmd_telegram` / a config helper and pass it into the relay (`RegistryRelay` + the single-window `TelegramRelay`); do the skip in the relay's `on_message`, BEFORE formatting/sending. **Don't touch** routing / registry / inbound / `parser.py`. **Landmine:** don't hide `AskUserQuestion`/`ExitPlanMode` — verify by tool_name against the interactive set. Unit-test (stub sender, NO live Telegram): with the flag OFF, a `Bash` tool_use+tool_result is dropped but an `AskUserQuestion` tool_use is KEPT and a text message is KEPT; with the flag ON, all are kept. Keep `uv run ruff check chela tests` green + `uv run pytest -q`. Report back. Reference: ccbot `src/ccbot/config.py` (`show_tool_calls`) + `src/ccbot/bot.py:1797` (the skip) + its `INTERACTIVE_TOOL_NAMES`, `chela/telegram/relay.py`, `chela/telegram/format.py`, `chela/main.py::cmd_telegram`.

## Backlog (not yet dispatchable)

- **Dashboard consolidation** — "consolidate the ccbot engine into the dashboard". SHAPE UNDECIDED: (a) dashboard SURFACES the bridge (Telegram panel reading `~/.chela/telegram-bindings.json`; separate pm2 proc — recommended) vs (b) bridge INSIDE the dashboard process (one pm2 app; PTB-asyncio-in-Flask fiddlier). Own design step.
- **Retire ccbot** ~07-19 after warm standby: `pm2 delete ccbot` + `pm2 save` + archive repo (ops).
- Interactive UI: AskUserQuestion / ExitPlanMode / Permission phone **approvals (buttons)** + `/esc` + `/screenshot` + message merging (ccbot parity — Decision 4 v1 scope). [note: the toggle above keeps interactive prompts VISIBLE as text; this is the richer button-rendering version.]
- Privacy scrub (forum IDs, `CCBOT_PANE_FALLBACK`, absolute paths → env-driven) before any of this touches public `main`.
- **Cost view** (cockpit): transcript tokens × price → $ per agent / run / fleet.
- **Unified graph viewer** (cockpit): memory `[[wikilinks]]` + fleet; renderer = **Sigma.js + Graphology (MIT, WebGL)**, clean-room (not a GitNexus fork — PolyForm-NC). Colorblind-safe.

## Done

- [x] telegram-setup skill (#14) · pkg scaffold + `[telegram]` extra (#15) · dashboard workflow discovery (#16) · monitor port (#17) · outbound relay + CLI (#18).
- [x] Fold step 1 — `chela drive` paste-submit fix (PR #19, CMX-6).
- [x] Mobile Kanban — Swipe + Rows (PR #20, CMX-7).
- [x] Fold step 2 — INBOUND routing (PR #21, CMX-8).
- [x] Fold step 3 — monitor resolve-by-newest-record (PR #22, CMX-9).
- [x] Multi-topic Slice A — N thread↔window registry (PR #23, CMX-10).
- [x] Multi-topic Slice B — auto-create + lifecycle (PR #24, CMX-11).
- [x] Topic naming by project cwd (PR #25, CMX-12).
- [x] **CUTOVER LIVE 2026-07-12** — ccbot warm standby; `chela-telegram` (pm2 id 12) on @chelamuxbot / test forum `-1003732570394` ↔ 4 agents (chelamux/nautilus/strategy-lab/orchestrator). Topics named + test topic cleaned.
