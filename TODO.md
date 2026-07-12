# TODO

## Open — chela telegram bridge commands

- [x] **`/screenshot` control-key keyboard (port ccbot's "with control keys").** CMX-16 shipped `/screenshot` as a bare PNG, but ccbot's screenshot came WITH an inline keyboard of control keys so you can drive the terminal from your phone. Attach an `InlineKeyboardMarkup` to the `/screenshot` `reply_photo` + add a `CallbackQueryHandler` that sends the tapped key to the bound window:
  1. **Key map** (port ccbot's `_KEYS_SEND_MAP` + `_KEY_LABELS`, `src/ccbot/bot.py:361-386`): `up→Up, dn→Down, lt→Left, rt→Right, esc→Escape, ent→Enter, spc→Space, tab→Tab, cc→C-c` (all `send-keys`, no trailing Enter, not literal). Labels: `↑ ↓ ← →, ⎋ Esc, ⏎ Enter, ␣ Space, ⇥ Tab, ^C`.
  2. **Keyboard layout** (4 rows): `[␣ Space, ↑, ⇥ Tab]` / `[←, ↓, →]` / `[⎋ Esc, ^C, ⏎ Enter]` / `[🔄 Refresh]`. `callback_data = f"{KEYS_PREFIX}{key_id}:{window_id}"` and `f"{REFRESH_PREFIX}{window_id}"`, each **truncated to ≤64 bytes** (Telegram limit; `@N` window ids are short so fine).
  3. **`messenger.send_key(window_id, key)`** — generalize the existing `send_escape` into a helper that `tmux send-keys -t <session>:<wid> <key>` (named keys `Up`/`Escape`/`C-c`/…, no Enter). Keep `send_escape` as a thin alias if referenced elsewhere.
  4. **`CallbackQueryHandler`** (registered in `build_application`, matched to the keys/refresh `callback_data` prefix): parse `key_id` + `window_id`; **gate on the bound `chat_id`** (reuse the `resolve`/gate discipline — `callback_query.message.chat.id`) AND verify `window_id` is a currently-bound window in the `BindingRegistry` (don't send keys to an arbitrary id from crafted callback_data); then `messenger.send_key(...)` + `query.answer(label)` toast. For **Refresh**: re-`capture` the pane → re-render PNG → `query.edit_message_media(InputMediaPhoto(png), reply_markup=<same keyboard>)`.
  **Don't touch** the `/screenshot`/`/esc` capture+render path, routing, registry, or lifecycle — this ADDS the keyboard + callback only. **Landmines:** callback_data ≤64 bytes; the callback must chat-gate + registry-verify the window (a button is a user-supplied string); `edit_message_media` needs the fresh PNG wrapped in `InputMediaPhoto`; keys are sent with `enter=False`. **Verify:** unit-test the keyboard builder (correct rows/labels/callback_data) + the callback dispatch (a keys callback → `send_key` with the mapped tmux key on the right window; a wrong-chat callback → dropped; an unbound window_id → dropped) against stubs, NO live Telegram. Keep `uv run ruff check chela tests` green + `uv run pytest -q`. Report back. Reference: ccbot `src/ccbot/bot.py:360-410` (`_KEYS_SEND_MAP`, `_KEY_LABELS`, `_build_screenshot_keyboard`) + its keys `CallbackQueryHandler`, `chela/telegram/inbound.py` (`_on_screenshot`, `build_application`), `chela/messenger.py` (`send_escape` → `send_key`), `chela/telegram/bindings.py`.

## Backlog (not yet dispatchable)

- **Settings view — editable toggles (follow-up)**: in-UI write-back + daemon restart.
- **Retire ccbot** ~07-19 after warm standby: `pm2 delete ccbot` + `pm2 save` + archive repo (ops).
- Interactive UI: AskUserQuestion / ExitPlanMode / Permission phone **approvals (buttons)** + message merging.
- `/kill` (explicit agent-kill + close topic) — optional, higher-stakes; skipped `/unbind` `/history` `/usage` (don't fit auto-topics).
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
- [x] Bridge commands `/screenshot` (PNG) + `/esc` + `set_my_commands` "/" menu (#29, CMX-16).
- [x] **CUTOVER LIVE 2026-07-12** — `chela-telegram` (pm2 id 12) on @chelamuxbot / test forum ↔ 4 agents; tool-spam hidden.
