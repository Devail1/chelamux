# TODO

The chela dispatcher claims each unchecked `- [ ]` item under an **Open** section, runs
it as an isolated git-worktree agent (adversarially reviewed by the judge), and strikes
it on merge. This file is the **live queue, not an archive** — completed-task history
lives in `git log`.

Each item is a four-field brief the judge can enforce mechanically:

- **OBJECTIVE** — what to build and why.
- **BOUNDARIES** — files/scope it may touch; what not to regress.
- **GUARDS** — tests that must go **RED** when their invariant is corrupted (a guard that
  survives its own corruption is decoration).
- **VERIFY** — how to confirm the result, including anything that needs a manual check.

## Open — CI drives the loop

- [ ] **📩 TELEGRAM BRIDGE — add `/compact` to the "/" menu as a passthrough command (Liav, 2026-07-21).** `/compact` is a **Claude Code** slash command (compacts the session's context), so the bridge should surface it in Telegram's "/" autocomplete and **forward** it to the bridged session — NOT intercept it. Mirrors the existing `/clear`.

  **OBJECTIVE.** In `chela/telegram/inbound.py`, add `("compact", "Compact the agent's context (forwarded to Claude Code)")` to **`PASSTHROUGH_COMMANDS`** (the list of Claude-Code commands surfaced in the "/" menu but forwarded via the catch-all send_tmux path — right next to the existing `("clear", …)`). Because it's a passthrough, do NOT register a `CommandHandler` for it and do NOT add it to `BRIDGE_COMMANDS` (that would make the bridge intercept it instead of forwarding to Claude Code). `MENU_COMMANDS` auto-includes it (it's `BRIDGE_COMMANDS + PASSTHROUGH_COMMANDS`), and the existing `resolve_command_for_window` already strips the `@botname` suffix Telegram appends in groups, so `/compact@chelamuxbot` forwards as `/compact`.

  **BOUNDARIES.** `chela/telegram/inbound.py` only (+ a test). Do NOT touch `BRIDGE_COMMANDS`, `resolve_command_for_window`, or register a handler for compact. PR → `dev`.

  **GUARDS (pytest; corrupt→RED).**
    - `("compact", …)` is present in `PASSTHROUGH_COMMANDS` (and therefore in `MENU_COMMANDS`, so it publishes to the "/" menu). Remove it → RED.
    - `compact` is NOT in `BRIDGE_COMMANDS` — it must be forwarded to Claude Code, not intercepted by the bridge. Move it into `BRIDGE_COMMANDS` → RED. (Guards the exact "intercept instead of forward" mistake.)
    - `resolve_command_for_window("/compact@somebot", "somebot")` returns `"/compact"` (the group `@botname` suffix is stripped, so Claude Code receives its own command, not a stray prompt). Break the stripping for compact → RED.

  **VERIFY.** In a Telegram forum topic bound to a session, `/compact` appears in the "/" autocomplete menu, and tapping/sending it forwards `/compact` to the pane so Claude Code compacts its context.

- [x] **📱✕ MOBILE TITLE BAR — restore the ✕ close (kill) button (Liav, 2026-07-21).** CMX-130 restored the pane title bar on phones but hid ALL window controls with `.gs-keys { display: none }` in the `@media (max-width: 768px)` block — which also dropped the **✕ close/kill** button. Bring just the ✕ back.

  **OBJECTIVE.** In the `@media (max-width: 768px)` block of `style.css`: instead of `.gs-keys { display: none }`, **show `.gs-keys`** (its default `display: flex`) and hide only the wall-only **maximize** button (`.gs-max-btn { display: none }`) — maximize doesn't apply to the forced single pane. The **✕ kill button `.gs-kill-btn` stays visible** → the mobile title bar becomes `● · ⋮ · name · … · ✕`. (The minimize button `.gs-min-btn` is only rendered for *draggable wall tiles* — `terminals.js` ~764 gates it on `draggable` — so it's already absent on the mobile single pane; no rule needed for it.) The ✕ still only renders for **non-managed** sessions (existing behavior, `terminals.js` ~759 — managed personas keep no ✕), and its tap→confirm→kill flow (`termKillClick`/`termKillConfirm`) works as-is. If the ✕ looks oversized in the compact 27px mobile bar (`.gs-kill-btn` is `font-size: 12px; padding: 4px 7px`), tighten it slightly on mobile so it fits — small, judgment call.

  **BOUNDARIES.** `style.css` `@media (max-width: 768px)` block only. Do NOT change the **desktop** `.gs-keys`/`.gs-win-ctl` (min/max/kill stay on desktop), the kill/min/max JS (`terminals.js`), the CMX-130 title-bar height (`.gs-head` padding/font) or the pane-height cutoff fix (`.term-single .term-pane` calc), or the bottom bar. PR → `dev`.

  **GUARDS (`wallnav.test.mjs`; corrupt→RED — SOURCE-STRUCTURE parse of the `max-width: 768px` media block).**
    - Within that media block, `.gs-keys` is **NOT** `display: none` (the control container is shown). Re-add `.gs-keys { display: none }` → RED.
    - Within that block, `.gs-max-btn` **IS** `display: none` (maximize hidden on the single pane). Remove it → RED.
    - Within that block, **no rule hides `.gs-kill-btn`** (the ✕ must stay). Add `.gs-kill-btn { display: none }` → RED.

  **VERIFY (live, mobile viewport).** The pane title bar shows the **✕ close button** at the right — with **no** maximize/minimize — and tapping it opens the kill-confirm. (Doubles as a live check that the submit-hardening holds: this agent should self-submit with no manual nudge.)

- [x] **🐛⌨️ HARDEN THE SEED SUBMIT — re-send Enter (not re-paste) so any startup redraw can't strand the prompt (Liav, 2026-07-21; residual after CMX-131).** CMX-131's MCP isolation removed the "MCP servers need authentication" notice, but dispatched windows STILL hang: a SECOND late startup notice (`gh auth login for PR status`) — and generically ANY startup redraw — lands after `send_tmux` (`messenger.py`) pastes the prompt and **eats its separately-sent Enter**, stranding the seed on the `❯` line unsubmitted. Confirmed live: judge launched with `--strict-mcp-config` (no MCP notice) yet still sat idle with the prompt typed.

  **ROOT CAUSE.** `_send_seed` (`dispatcher.py` ~1031) mis-recovers: on "agent still idle after the seed" it re-sends the **whole prompt** via `send_tmux` again — but the paste almost always DID land, so re-pasting **doubles the prompt** in the input box; and when `_seed_landed` reads the session status as `None` (unreadable — which is exactly what a mid-redraw window returns) it **fails open** ("assuming the seed landed"), so the Enter is never re-sent. The real failure is "paste landed, Enter eaten," but the code treats it as "paste dropped."

  **OBJECTIVE.** Make the submit notice-agnostic:
    1. When the agent hasn't gone busy after the initial `send_tmux`, **re-send JUST Enter** — a bare `tmux send-keys <target> Enter` (add a small `send_enter(window_id)` helper, e.g. in `messenger.py`), NOT another full paste. The paste is already in the box; a redraw only ate the Enter. Confirm busy; repeat up to `SEED_MAX_SENDS` with the settle gap. **Only if** every Enter-only re-send fails, fall back to ONE full `send_tmux` re-paste (covers the rarer genuinely-dropped-paste case).
    2. **Drop the fail-open on unreadable status:** `_seed_landed` returning `None` must NOT count as landed — keep retrying the Enter within the send budget (the observed hangs read `None` mid-redraw).

  **BOUNDARIES.** `dispatcher.py` (`_send_seed` + the recovery path) and `messenger.py` (a bare-Enter helper). Do NOT change `send_tmux`'s load-buffer/paste-buffer mechanism, the launch command / MCP isolation (CMX-131), `_wait_for_ready`, or `refuses_paste`. Keep the `SEED_*` constants (tune only if clearly needed). PR → `dev`.

  **GUARDS (pytest; corrupt→RED; mock the tmux sends + `_agent_status`).**
    - "Paste landed, agent still idle" (Enter eaten): the recovery's next send is a **bare Enter**, not a re-paste — assert the 2nd send carries NO prompt text (a re-paste would show the prompt again). Corrupt (make recovery re-paste) → RED.
    - `_seed_landed` → `None` (unreadable): `_send_seed` must **retry**, not return success. Corrupt (restore the fail-open) → RED.
    - Agent goes busy after an Enter re-send → `_send_seed` returns True (it converges, doesn't loop forever or fall through).

  **VERIFY.** A freshly dispatched agent AND judge submit their seed on their own — **no manual nudge, no watchdog trigger** — even with the `gh auth login for PR status` notice present. This is the real end of the hang saga; the external watchdog becomes a safety net, not a necessity.

- [x] **🐛🔌 DISPATCH STARTUP RACE — isolate MCP for dispatched agents + judge (fixes the "prompt pasted but not submitted" hang) (Liav, 2026-07-21).** Every dispatched window (agent AND judge, plus reworks) launches idle with its seed prompt typed but **unsubmitted**, needing a manual Enter — breaking unattended dispatch.

  **ROOT CAUSE.** `resolve_agent_cmd` (dispatcher.py ~308–346) builds the default `claude --permission-mode <mode> --model <model>` (via `AGENT_BASE_CMD`) with **no MCP isolation**, so dispatched agents + the judge inherit the orchestrator's interactive MCP servers (chrome-devtools, Gmail/Calendar/Drive). On startup those can't auth → Claude Code paints a "⚠ N MCP servers need authentication" notice whose **redraw lands after the pane first looked ready and eats the seed's submit Enter** — the paste stays in the input box, unsent. The `_send_seed`/`_seed_landed` recovery then reads the agent's status as `None` (unreadable) *during that redraw* and **fails open** ("assuming the seed landed"), so it's never re-sent.

  **OBJECTIVE.** Launch dispatched agents + the judge **MCP-isolated** so no auth-needing servers connect and the notice never appears. Add `--strict-mcp-config` (Claude only uses `--mcp-config`-provided servers, ignoring `~/.claude.json`) plus an **empty** `--mcp-config` to the default launch command — cleanest on **`AGENT_BASE_CMD`** (or the f-string at ~346) so BOTH the `role="coding"` and `role="judge"` paths inherit it through the single builder. Verify the exact incantation against `claude --help` (whether `--strict-mcp-config` needs an accompanying `--mcp-config`; if so, pass an empty config — a JSON string `'{"mcpServers":{}}'` or a tiny empty file). Do **NOT** put this in `WORKFLOW.md`'s `agent.cmd` — that pins the command and disables the dashboard Settings permission-mode control. Confirm `_spawn_judge` uses the resolved command (it goes through `resolve_agent_cmd(role="judge")`); if it builds its own anywhere, isolate that too.

  **BOUNDARIES.** `dispatcher.py` (`AGENT_BASE_CMD` / `resolve_agent_cmd` / the launch builders) + a guard test. Do NOT change the permission-mode/model resolution, the `agent.cmd`-override path's semantics, the Settings-control reachability, or the seed/nudge timing (`_send_seed`, `SEED_*`). PR → `dev`.

  **GUARDS (pytest; corrupt→RED).**
    - `resolve_agent_cmd(wf, role="coding")` AND `resolve_agent_cmd(wf, role="judge")` each return a command containing `--strict-mcp-config`. Remove the flag → RED.
    - The command carries an **empty** MCP config (no session servers) — assert the `--mcp-config` value contains no server names / is the empty form. Corrupt (point it at a populated config) → RED.
    - The existing `resolve_agent_cmd` tests (`--permission-mode` / `--model` correctness) stay green — the isolation must not clobber them.

  **VERIFY.** A newly dispatched agent's window shows **no** "MCP servers need authentication" notice and its seed prompt **submits on the first try** (no manual Enter); the judge window likewise starts clean. (Ironically the fix-agent itself will hit the current bug on launch — expect one manual nudge — then after merge+`pm2 restart chela-daemon`, dispatch heals itself.)

- [x] **🐛📱 MOBILE PANE CHROME — restore the pane title bar (shorter) + fix the terminal bottom-row cutoff (Liav, 2026-07-21).** Two fixes in the `@media (max-width: 768px)` block of `style.css`. They're bundled because they touch the same block and the cutoff is the *real* reason the pane bottom (input box + the TUI's own "auto mode on" line) isn't visible on mobile — fixing it gives the mode natively, no indicator needed.

  **OBJECTIVE.**
  1. **Restore the pane title bar on mobile.** It's currently `.gs-head { display: none }` (deliberate declutter). Show it instead at a **reduced height vs desktop** — desktop `.gs-head` is `padding: 4px 9px; font-size: 12px`; tighten to roughly `padding: 2px 8px` + `font-size: 11px` (aim ~70–75% of desktop height). Carry only the **mobile-relevant controls: the status dot, the pane name, and the ⋮ menu.** The wall-only controls (drag grip, min/max, resize handles, kill/rename buttons) stay hidden on mobile — they don't apply to the forced single pane; pane actions live in the ⋮ menu. If un-hiding `.gs-head` would surface any of those wall-only buttons, keep *those specific buttons* `display:none` on mobile so only dot · name · ⋮ show.
  2. **Fix the terminal bottom-row cutoff.** The fixed bottom keybar's real height is `~47px + env(safe-area-inset-bottom)` (6px top pad + 34px `.kb2-key` min-height + `calc(6px + env(safe-area-inset-bottom))` bottom pad + 1px border-top), but the reservation is a **bare `#term-stage { margin-bottom: 46px }`** — no inset. So on a phone with a home-indicator safe area, the keybar **overlaps the terminal's last rows** (input box + status line hidden). Change it to `#term-stage { margin-bottom: calc(47px + env(safe-area-inset-bottom)); }` so the reservation matches the keybar's true footprint. NOTE: restoring the title bar (part 1) also eats vertical space at the *top* of the pane — that's expected/fine on mobile.

  **BOUNDARIES.** `style.css` `@media (max-width: 768px)` block only (+ minimal mobile `.gs-head` height rules). Do NOT change the **desktop** `.gs-head`, the keybar's own styling, the `_kbPin` JS, or the bottom bar (`.term-ctx-bar` — CMX-127/129). Don't regress the mobile keybar or the agent-switcher pills. PR → `dev`.

  **GUARDS (`wallnav.test.mjs`; corrupt→RED — SOURCE-STRUCTURE parse of the media block; rendered layout stays MANUAL live-verify per [[reference_chela_judge_css_render_ceiling]]).**
    - Within the `@media (max-width: 768px)` block, `.gs-head` is **NOT** `display: none` (the title bar is restored). Isolate that media block and assert no `.gs-head { display: none }` in it; re-add it → RED.
    - The mobile `#term-stage` bottom reservation **references `env(safe-area-inset-bottom)`** (not a bare px). Assert its `margin-bottom` contains `safe-area-inset-bottom`; revert to a bare `46px` → RED.

  **VERIFY (live, narrow / mobile viewport).** (1) The pane shows a compact title bar — dot · name · ⋮ — visibly shorter than desktop, with no wall-only controls. (2) The terminal's **bottom rows are fully visible above the keybar** — the input box and the TUI status line ("auto mode on") are not cut off, including on a viewport emulating a home-indicator safe area.

## Backlog

Rough ideas, not yet dispatchable — each becomes a full four-field brief when picked up.

- **Planner / decomposer persona** — split an epic-sized item into N small guarded
  sub-tasks (each with corrupt-each-→-RED guards) for worker agents to pick up. The
  load-bearing risk is brief quality, so it needs guard-discipline in its prompt plus a
  critic/human checkpoint on the generated children before dispatch — not fire-and-forget.
- **Gate-unify** — the review-gate path and the merge path are separate authorities;
  reconcile them to one.
- **Auto-orchestrator teardown on lease expiry** — kill its window when the attended
  lease lapses, so the merge action-gate isn't the sole post-expiry stop.
- **Settings view — editable toggles** — in-UI write-back + daemon restart.
- **Cost view** — transcript tokens × price → cost per agent / run / fleet.
- **Fleet loose ends** — Wall terminal addressing, explicit agent-kill (`/kill` + close
  topic).
