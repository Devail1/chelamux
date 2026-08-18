## 54. A `classList.contains` assertion proves the class was ADDED, not that the CSS rule gating on it still makes anything visible

**Assertion form:** a guard proves a "show this UI" action fired by asserting the toggled
class landed — `modal.classList.contains('active') === true` — after driving the real click
that should have added it. The comment above the assertion frames it as proving the element
"becomes VISIBLE... the way a user watching the screen would see it," but the code only reads
the class list.

**Mutation that defeats it:** invert the CSS rule that gives the class its meaning —
`.modal-overlay.active { display: flex; }` → `.modal-overlay.active { display: none; }`. The
JS that adds `.active` is completely unchanged and still runs; `classList.contains('active')`
still returns `true`. The element the class was added to now never renders on screen — a user
watching the real page would see nothing happen — while every assertion checking the class
itself stays green. `chela judge` found this live on CMX-290 round 3:
`CHELA_REQUIRE_JS_TESTS=1 uv run pytest -q` stayed green (3133 passed) with the mutation
applied, and no `.css` parse check exists to catch it structurally either.

**Why this slips through even in a suite that already runs real markup:** the guard in
question mounts the REAL `#modal-task` fragment (via `sliceTemplate()`) specifically so a
rename of the element or the loss of its gating class shows up here rather than in a stale
hand-typed fixture. That closes the *markup*-drift half of the gap. It does not close the
*CSS*-drift half: jsdom's default `document.body.innerHTML = fixture` mount never loads
`style.css`, so `getComputedStyle` on that fragment returns the browser's built-in defaults,
not this repo's cascade — `classList.contains` is the only signal available from that mount,
and it is silent to a CSS-only regression by construction, no matter how "real" the HTML is.

**Guard form that survives:** for any element whose visibility is gated by a CSS class rather
than an inline style or `hidden` attribute, assert the CASCADED value, not just the class
name. Mount the real markup fragment alongside the real stylesheet in a fresh jsdom instance
(`new JSDOM(`<style>${styleCss}</style>${fragment}`, { pretendToBeVisual: true })`— the same
recipe `tests/wire_live_css.test.mjs` (CMX-120) already uses for a different selector) and
read `getComputedStyle(el).display` (or whichever property the rule sets) before and after
toggling the class. The `classList.contains` assertion is still worth keeping alongside it —
it pins that the JS ran — but it is not sufficient on its own for any element whose only
observable effect is "a CSS rule now matches it."

**Found:** `tests/kanban_task_modal_wiring.test.mjs`'s wiring guard (CMX-290, round 3) asserted
`modal.classList.contains('active')` for `#modal-task` and called that "visibly" in its own
title and comments, with no assertion anywhere in the suite reading `.modal-overlay.active`'s
cascaded `display`. Closed by mounting the sliced `#modal-task` fragment with the real
`style.css` in a dedicated jsdom instance and asserting `getComputedStyle(modal).display`
flips from `none` to `flex` across the class toggle.

**Seen again:** CMX-298, PR #372, round 5 (2026-08-16) — same shape, a different chip.
`tests/kanban_flatten.test.mjs`'s parked-card tests (5, 6, 8, 9) all read the `🔒`
`.kanban-parked-reason` chip via `textContent`/`querySelector`/`getAttribute`; none loaded
`style.css`, so a mutation adding `display: none` to that rule left every one of them green.
Closed the same way — a fresh `pretendToBeVisual` jsdom mounting the parked card's real
`outerHTML` under the real `style.css`, reading `getComputedStyle` for `display`, `visibility`,
and the `overflow`/`text-overflow`/`white-space` triple that same rule also carries.

**Seen again, no toggle involved at all:** CMX-302, PR #376, round 2 (2026-08-17) — a
still-narrower variant: `.ar-role.orchestrator` is not gated behind a class TOGGLE the way
`.modal-overlay.active` or the parked-card chip are — it is a plain, always-applied rule on a
class the badge either carries or doesn't, so there was no "before/after a click" pair for a
prior author to even think to diff. `tests/sidebar.test.mjs` (the badge's only guard) proved
the `<span class="ar-role orchestrator">` node exists, carries the right class, and holds a
crown `<svg>` — every DOM-only signal available from `bootDashboardDom`, which (like the
`document.body.innerHTML = fixture` mount this shape's original write-up already names) never
loads `style.css` at all. The judge applied two independent mutations in the same round, each
invisible to every one of those DOM assertions: `display: flex` → `display: none` (the badge
renders in the tree but never paints — the exact class of regression this shape exists for)
and, a second property entirely, `width: 18px` → `width: 180px` (the badge stays visible but
regrows into the wide text-pill CMX-302 was filed to eliminate — a geometry regression this
shape's `display`/`visibility`-focused write-up doesn't literally name, but the fix is
identical: read the cascaded value). `CHELA_REQUIRE_JS_TESTS=1 uv run pytest -q` stayed green
(3210 passed) with either mutation applied in isolation. Closed by adding
`tests/sidebar_role_badge_css.test.mjs`: it reuses the real `bootDashboardDom` + real
`orchestratorSubscribe()` boot (so the badge under test is the one nav.js actually renders,
not a hand-typed fixture that could drift from `_agentRowHtml`'s real class names) and injects
the real `style.css` into that same `jsdom` document afterward, then asserts
`getComputedStyle(badge).display === 'flex'` and `getComputedStyle(badge).width === '18px'`.
