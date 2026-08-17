## 77. A new click chain mirrors an already-guarded one but doesn't reuse the guard, so none of its hops has any test at all

**Assertion form:** none. A feature adds a multi-hop UI chain — a chip's `onclick` names a
handler, a module registers that handler as a side effect of an `import`, the handler shows a
modal by DOM id — and ships pure-function unit tests for its *rendering helpers* (a status
label, a CSS class, a line-classifier) while nothing drives the chain a mouse click actually
takes. This is not "one hop guarded, others missed" (that's [[7|shape 7]]/[[43|shape 43]]) —
it is the zero-guard case: every hop is simultaneously unprotected, because the suite never
simulates the click at all.

**Mutation that defeats it:** independently corrupt any one of the chain's links and the
suite stays green:
- drop the chip's markup from the function that emits the bottom-bar HTML — the chip that
  makes the whole feature reachable is simply gone;
- comment out the side-effect `import './handler.js'` in the module that renders the
  chip — the chip still renders, its `onclick` still names the right function, but
  `window.chela.<fn>` was never registered, so a real click throws;
- rename the target modal's `id` in the template — `showModal('the-id')` now finds nothing,
  no error, no `.active` class, the click chain completes and nothing appears on screen.

Three structurally different failure modes (an emission omission, a registration omission, a
DOM-target mismatch), and a suite with *zero* click-chain coverage cannot distinguish "works"
from any of them — there is no test whose red/green depends on any of the three being intact.

**Why this keeps recurring despite precedent existing:** the exact chain shape (card/chip
`onclick="chela.X(...)"` → a module's side-effect registration → `showModal(id)` on a
sliced-from-the-real-template overlay) was already closed once, for the kanban card → task
modal chain (`tests/kanban_task_modal_wiring.test.mjs`, CMX-290) — including the
`bootDashboardDom`/`clickOnclick`/`sliceTemplate` helpers built specifically to make writing
the *next* one cheap. A new feature that reproduces the identical chain shape on a new
surface (a wall tile's "Files" chip → the per-session diff modal) shipped with none of it
reused: the helpers existed, sitting in `tests/js_helpers/dashboard_dom.mjs`, unimported.
Coverage of a click chain is not implied by unit tests of what the chain eventually renders,
no matter how thorough those are — `diffpanel_model.test.mjs`'s three pure-helper suites
(status label, CSS class, patch-line class) said nothing about whether a click ever reaches
`_render` at all.

**Guard form that survives:** for any new `onclick="chela.X(...)"` → registration →
`showModal(id)` chain, write one test that: boots the real module graph in jsdom (or reuses
`bootDashboardDom`), renders the real emitting function (not a hand-typed fixture), reads the
real element's `onclick` attribute and executes it against the real `window.chela` (
`clickOnclick`/an equivalent that supplies `event` if the handler references it), and asserts
the real target modal — sliced from the real template via `sliceTemplate`, not hand-typed —
becomes `.active`. One such test kills all three failure modes above in one assertion chain,
because each mutation breaks a different link the SAME test walks through in order.

**Found:** CMX-299 rework round 1 (2026-08-16), PR #373. The judge corrupted the chip
emission (`terminals.js`'s `_ctxBarHTML` dropping `${filesChip}`), the registering import
(`terminals.js`'s `import './diffpanel.js';` commented out), and the modal target
(`templates/index.html`'s `#modal-diff` renamed) — three separate mutations, three separate
files — and `CHELA_REQUIRE_JS_TESTS=1 uv run pytest -q` (3174 tests) stayed green under each,
because nothing in the PR ever clicked the chip. Closed by
`tests/diff_modal_wiring.test.mjs`, built directly on the CMX-290 helper
`sliceTemplate` (from `tests/js_helpers/dashboard_dom.mjs`), plus a local `clickFilesChip`
extending `clickOnclick`'s pattern to supply the `event` object this chip's handler needs for
`event.stopPropagation()`, driving the real `_ctxBarHTML`-rendered tile end to end.
