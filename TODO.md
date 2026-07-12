# TODO

## Open — chela telegram formatting

- [x] **Render assistant markdown properly in Telegram (add `telegramify-markdown`).** `chela/telegram/format.py::to_markdown_v2` currently **blind-escapes** the body via `escape_markdown_v2` (every MarkdownV2 special char backslashed), so Claude's markdown renders LITERALLY on Telegram — ` ``` code blocks ``` ` show as escaped backticks, `*bold*` shows the asterisks, tables show raw pipes. Port ccbot's approach (it deliberately was NOT ported to stay dep-free — now we want the fidelity):
  1. Add **`telegramify-markdown`** to the `[telegram]` extra in `pyproject.toml` (what ccbot used; pulls `mistletoe`).
  2. In `to_markdown_v2`, render the **assistant/text body** through `telegramify-markdown` → valid MarkdownV2 (real code-block + bold + inline-code entities), replacing the blind `escape_markdown_v2` for that body. Optionally port ccbot's `convert_markdown_tables` (tables → card-style key/value, since Telegram has no tables) — `src/ccbot/markdown_v2.py`.
  3. **Keep the fallback:** import `telegramify-markdown` **lazily**; if it's absent (import fails) or raises on a message, fall back to the current `escape_markdown_v2` blind-escape so the core still works without the extra. The relay's existing MarkdownV2→plain-text-on-reject path stays as the outer safety net.
  4. The emoji **header** (`🔧`/`✅`/`🤖` + tool/role) must remain valid MarkdownV2 — escape it or keep it outside the converted body; don't double-escape. **Leave `to_code_block`** (used by `/screenshot`'s text fallback) untouched.
  **Don't touch** routing / registry / lifecycle / the relay's send+fallback contract. **Landmines:** don't double-escape the header; a telegramify failure on ONE message must degrade to escaped-plain for that message, never crash the relay; keep it lazy so pure format tests don't need the extra. **Verify:** unit-test that an assistant message with a fenced code block + `*bold*` renders MarkdownV2 with a real code entity (backticks NOT backslash-escaped) when telegramify is available, and that the fallback path (telegramify absent/raising) still produces safe escaped output. Keep `uv run ruff check chela tests` green + `uv run pytest -q`. Report back. Reference: ccbot `src/ccbot/markdown_v2.py` (telegramify usage + `convert_markdown_tables`), `chela/telegram/format.py`, `chela/telegram/relay.py` (send + plain fallback), `pyproject.toml` `[telegram]` extra.

## Backlog (not yet dispatchable)

- **Settings view — editable toggles (follow-up)**: in-UI write-back + daemon restart.
- **Retire ccbot** ~07-19 after warm standby: `pm2 delete ccbot` + `pm2 save` + archive repo (ops).
- Interactive UI: AskUserQuestion / ExitPlanMode / Permission phone **approvals (buttons)** + message merging.
- `/kill` (explicit agent-kill + close topic) — optional, higher-stakes; `/unbind` `/history` `/usage` skipped (don't fit auto-topics).
- Privacy scrub (forum IDs, `CCBOT_PANE_FALLBACK`, abs paths → env) before public `main`.
- **Cost view** (cockpit): transcript tokens × price → $ per agent / run / fleet.
- **Unified graph viewer** (cockpit): memory `[[wikilinks]]` + fleet; renderer = **Sigma.js + Graphology (MIT, WebGL)**, clean-room (not a GitNexus fork — PolyForm-NC). Colorblind-safe.

## Done

- [x] telegram-setup (#14) · scaffold+extra (#15) · dashboard workflow discovery (#16) · monitor port (#17) · outbound relay+CLI (#18).
- [x] Fold steps 1-3 — paste-submit (#19), INBOUND (#21), monitor resolve-by-record (#22) [CMX-6/8/9].
- [x] Mobile Kanban — Swipe + Rows (#20, CMX-7).
- [x] Multi-topic Slice A registry (#23) + Slice B auto-create/lifecycle (#24) [CMX-10/11].
- [x] Topic naming by project cwd (#25, CMX-12).
- [x] `CHELA_SHOW_TOOL_CALLS` toggle — hide tool spam (#26, CMX-13).
- [x] Settings view — Connections & Status (#27) + Telegram-bridge row (#28) + Work-dispatcher-badge fix [CMX-14/15].
- [x] Bridge commands `/screenshot` (PNG) + `/esc` + `/` menu (#29, CMX-16) + `/screenshot` control-key keyboard + Refresh (#30, CMX-17).
- [x] **CUTOVER LIVE 2026-07-12** — `chela-telegram` (pm2 id 12) on @chelamuxbot / test forum ↔ 4 agents; tool-spam hidden.
