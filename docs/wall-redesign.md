# Wall redesign — design doc

Status: **shipped on `wall-redesign` (dogfooding), not yet promoted to dev/main** · 2026-07-24 · owner: orchestrator (worktree-built slices, render-verified). Slices 1–3 done (status-dense tile, auto-arrange toggle, Focus layout); slice 5 (drag-to-swap) skipped — already existed in Lock mode. Remaining optional: status-driven *sizing* (panes grow/shrink by state).

## Context — why

The **Wall** (`terminals` view) is chela's primary mission-control surface: the grid of live agent panes you watch and drive. As the fleet grows, four frictions bite, and today the Wall addresses only the first, thinly:

1. **Attention routing** — which pane needs *me* right now.
2. **Progress / context** — what each agent is doing and how far along, without opening it.
3. **Density & readability** — see many panes without losing the important one.
4. **Driving** — act / approve / jump fast.

We evaluated three directions (interactive study: *Triage rail* / *Status-dense grid* / *Focus+filmstrip*) and picked **B · status-dense grid** as the home layout, because it keeps the "watch many at once" density that *is* mission-control while making each pane self-describing. North star (owner): **daily-driver flow + adopter legibility over visual wow** — favor information density and self-explanation over flash.

This doc is design-first on purpose: the chela **judge cannot verify rendered CSS** (CMX-117), so every slice is settled visually up front and **render-verified manually** on an isolated dashboard instance before merge.

## Current state — ground truth (do not re-derive)

- **Layout:** GridStack (vendored), `column:12, cellHeight:70, float:true`, drag via the `.gs-grip` header handle, resize handles, layout persisted per-window-id to `localStorage['pc_wall_layout']`. Presets `WALL_PRESETS` (`1col/2col/3col/4col/2×2/3×2`) in `localStorage['pc_wall_preset']`. Files: `chela/dashboard/static/js/terminals.js` (`buildWall` ~1942, `_wallTileHTML` ~1905, `paneHead` ~755, `_ctxBarHTML` ~1932, `WALL_PRESETS` ~2089).
- **Each pane** is a **live `ttyd` iframe** (`/term/<wid>/`) — real interactive terminal, keyed by tmux **window id `@N`** (renames never reload it). ⛔ **The iframe eats pointer events** — a drag layer must float *above* the wall and hit-test manually; this is why drag today only works via the header handle.
- **Live transport (keep as-is):** ttyd's own WebSocket (content) + a 4 s reactive poll (`startTermTimer`, `TERM_REFRESH_MS=4000` → dots/context/tile-set) + SSE `windows`/`term-ready` (spawn/kill/ready). No full reload path.
- **Data already on tap** — no new backend needed:
  - `/api/agents` (`app.py:179`): `name`, `window_id`, `ai_title`, `cwd`, `session_status` (busy/idle/waiting), **`needs_human`**, `dispatched`, `thinking`, `claude_running`, `liveness`/`health`, `pr` (`{n,url,…}`), **`recap`** + `recap_ts` (Claude's away-summary — **exists but never shown on the Wall today**), `has_schedules`, `schedule_next_run`, `shared`.
  - `/api/agents/context` (`app.py:1948`): `used_pct`, `used`/`total`, `model`, `cost_usd`, `branch`, `rate_limit_pct`/`weekly_rl_pct`, `estimated`.
  - **The attention signal already exists:** `needs_human` (`_needs_human` `app.py:146` — OR of `session_status=='waiting'` and, for dispatched panes, a tmux-capture permission-gate detector) → client `wantsHuman(a)` (`util.js:84`) already drives the yellow ring, auto pop-out, taskbar sort, and tab-title count. The redesign **elevates this existing signal into the organizing principle**; it does not invent it.
- **Driving:** click iframe to focus; type directly; on-screen keyboard bar → `POST /api/term/key` (`tmux send-keys`); no dedicated approve/deny endpoint (you answer a gate by sending keystrokes); `Alt+N` jump; overflow menu = Wire/Share/Orchestrator/Pin/Kill.

## The redesigned tile (component: the status-dense card)

A pane tile is the live iframe **plus a self-describing frame** drawing only on existing fields:

- **Header row:** state pill (`● working` / `◆ needs you` / `✓ done` / `○ idle` — **glyph + word always**, colour secondary, red-weak safe) · pane `name` · `ai_title` as dim subtitle.
- **Meta row:** `model` · `⎇ branch` · PR chip (`⚑ #14 open`, links the PR) · `cost_usd` · a **context bar** with `%` (fill = `used_pct`; warn > 60 → `--ok-orange`, danger > 80 → `--ok-vermillion`).
- **`recap` line:** Claude's "what happened while you were away," surfaced on the Wall for the first time (dimmed, one line, `recap_ts` in the tooltip).
- **Action affordances:** a pane where `wantsHuman(a)` is true gets an **amber border + an action bar** with the right verb (Approve on a permission gate, Answer on a question) that sends the keystroke via the existing `/api/term/key`; a `finished`/done pane gets a blue **Review** bar linking its PR.
- The **`ttyd` iframe is untouched** — same element, same socket; the frame wraps it.

Semantic palette = the existing Okabe-Ito tokens (`--ok-green` working, `--ok-orange`/`--work` amber for needs-you, `--ok-vermillion` error/danger, `--ok-sky` done). **Colour is never the sole signal** — glyph + word + number carry state.

## The grid / interaction layer

Three capabilities layered on GridStack (**enhance the incumbent unless a slice proves we must replace it**):

1. **Smart auto-layout (toggle).** Status drives size + position: `wantsHuman`/active panes claim prime real-estate and grow; `idle`/`done` shrink. A user toggle — manual layout stays available and persists. The *ordering* math (rank a pane by status → target cell/size) is a **pure, unit-testable function**; GridStack just applies the computed layout.
2. **True drag-to-swap.** Drop pane A onto pane B → they **exchange cells** (vs GridStack's free-float reflow). Implemented with a **transparent drag-overlay above the iframes** that hit-tests the pointer to a target `wid`; on drop, swap the two panes' `{x,y,w,h}` and re-apply. This is the slice that must beat the iframe-pointer-events constraint. The swap-cell computation (given two tiles' rects → the two new layouts) is a **pure, unit-testable function**; the overlay/hit-test is the render-verified part.
3. **Better presets + auto-arrange.** Curated layouts (even-grid / focus-one / priority-split) + a one-click **auto-arrange** that snaps a messy wall back to a sane, status-aware layout. Preset → layout mapping is **pure/testable**.

## Data flow & error handling

- **No new backend** for the tile or attention/sort/preset slices — all fields exist; the tile reads `/api/agents` + `/api/agents/context` exactly as the current dots/ctx-bar do (`_applyTermStatus`, `_applyTermContext`). Live via the existing 4 s poll + SSE; **do not touch the ttyd transport or add a reload path**.
- A **genuine per-agent progress/phase signal** does *not* exist (only `used_pct` + `session_status` + `recap` as proxies). v1 uses those proxies; a real phase/turn signal is a **later, optional** backend addition, out of scope here.
- **Degrade quietly:** a missing field (`recap`, `pr`, `cost_usd` null) hides that element, never blanks the tile; the iframe always renders regardless of frame data.

## Constraints & risks

- **iframe eats pointer events** → drag-to-swap needs the manual hit-test overlay (slice 5's central risk).
- **GridStack incumbent** → default to enhancing it; only slice 4/5 may surface a need to replace, which would be its own decision.
- **CSS-render-blind judge** → guards target pure logic only; pixels are verified by the orchestrator on an isolated dashboard.
- **`@N` window-id keying is load-bearing** — never key tiles/layout by display name (renames must not reload the iframe).
- **Don't regress** the 4 s poll, SSE reconcile, minimize-dock, share/wire/orchestrator affordances, or mobile single-mode (< 768 px).

## Slices (→ dispatch briefs), sequenced by value × (low) risk

Each slice is one `cmx-N` dispatch: **Fable designs the concrete mockup/spec → dispatch → orchestrator render-verifies on an isolated dashboard → merge to `dev` → promote**. Guards target pure logic; the orchestrator verifies rendered pixels + interaction manually.

| # | Slice | Scope | Pure-logic guards | Manual render-verify |
|---|-------|-------|-------------------|----------------------|
| **1** | **Tile redesign** | Status card: header pill, meta row, context bar, `recap` line, amber/needs + blue/done action bars. Presentational; wires to existing fields. | pill/level mapping (`status → glyph+word+class`), context `%`→warn/danger threshold, field-null → element hidden. | tile renders on desktop + 375 px; states show correctly; action bar sends the right key; no console errors; iframe unaffected. |
| **2** ✅ | **Auto-arrange mode toggle** (merges old 2+4) | A default-off *Auto-arrange* toolbar toggle; when ON the wall continuously ranks panes by attention and lays them out in rank order; when OFF the manual layout is restored. **Owner decision: manual layout is sacred — auto never overwrites `pc_wall_layout`; ranking only applies while the toggle is on.** | pure `rankOrder(agents, wantsByWid)` — needs-you → busy → idle → done, stable within a rank. | auto on → needs-you/active to front without reloading iframes; auto off → manual layout restored byte-for-byte; drag/resize disabled while on. **Done — `wall-redesign`.** |
| **3** ✅ | **Focus layout** (one large pane + strip) | Curated presets (even-grid/focus-one/priority-split) + one-click auto-arrange/reset. | preset → `{wid:{x,y,w,h}}` layout map; auto-arrange is deterministic given a fleet. | each preset applies; reset snaps sane; persists to localStorage. |
| ~~4~~ | **Smart auto-layout — folded into slice 2** | The status-driven *ranking* shipped in slice 2's auto mode. What remains for a later pass is status-driven *sizing* (needs-you/active panes grow, idle/done shrink) — a refinement of the same toggle, not a separate mode. | — | — |
| ~~5~~ | **Drag-to-swap — SKIPPED (already exists)** | Discovered during scoping: **Lock mode already implements drag-to-swap** (`_snapshotForSwap`/`_swapTargetWid`/`_doSwap`, centroid hit-test, live target highlight, swap-*and-resize*) — drag a pane's header onto another. The only net-new would be full-pane drag *over the iframe* via a transparent overlay; owner decided header-drag is sufficient and skipped the overlay (avoids the iframe-pointer-events risk). | — | — |

*(Later, separate workstreams — not this doc: A's collapsible "Needs you" rail; C's focus-mode toggle; cutting the Knowledge view; a Settings surface.)*

## Verification (whole workstream)

- Per-slice: pure-logic guards go **RED** under corruption (orchestrator reads the assertions, not the pass count); CI green on 3.11 + 3.12; **manual render pass** on an isolated dashboard instance with a screenshot before merge.
- Slices 4 & 5 get a **validation prototype/artifact first** (like the direction study), because auto-layout and drag-swap are interaction-novel and un-judgeable by CI.
- No regression to transport (ttyd/poll/SSE), minimize-dock, share/wire/orchestrator, or mobile single-mode.

## Open decisions (resolve as we reach them)

- **Enhance vs replace GridStack** for slices 4/5 — default enhance; revisit only if the API can't express status-driven layout cleanly.
- **Layout-state persistence** — keep `localStorage['pc_wall_layout']` per-`wid`; decide whether the auto-layout toggle state is per-device (localStorage) or synced.
- **A real progress/phase signal** — deferred; v1 uses `used_pct`+`session_status`+`recap` proxies.
