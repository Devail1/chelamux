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

- [ ] **🐛📱 MOBILE PANE CHROME — restore the pane title bar (shorter) + fix the terminal bottom-row cutoff (Liav, 2026-07-21).** Two fixes in the `@media (max-width: 768px)` block of `style.css`. They're bundled because they touch the same block and the cutoff is the *real* reason the pane bottom (input box + the TUI's own "auto mode on" line) isn't visible on mobile — fixing it gives the mode natively, no indicator needed.

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
