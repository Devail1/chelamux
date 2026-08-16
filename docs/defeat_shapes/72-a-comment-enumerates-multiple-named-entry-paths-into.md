## 72. A comment enumerates N named entry paths into one shared action; the test drives the action directly and none of them

**Assertion form:** a shared action (`closeDiffModal()`) is reachable from several
independently-registered entry points — a close button's `onclick`, a `keydown` listener's
`if (e.key === 'Escape') closeDiffModal();`, a backdrop-click listener's
`if (e.target.id === 'overlay-id') closeDiffModal();` — and a comment at the call site (or in
the template) documents all of them by name ("close button, Escape and a backdrop click all
route through `closeDiffModal()`"). The test that exists calls the shared action directly
(`window.chela.closeDiffModal()`), which proves the action itself works — and because the
comment enumerates the other routes, reading the test next to the comment gives the
impression they're covered too.

**Mutation that defeats it:** dead-code any one (or all) of the entry-point conditions
independently — `if (false && e.key === 'Escape') closeDiffModal();`,
`if (false && e.target.id === 'modal-diff') closeDiffModal();` — while leaving the shared
action itself, and the direct-call test, untouched. The direct call never goes through the
listener at all, so it cannot observe that the listener's own condition was gutted; the suite
stays green while every keyboard/backdrop route into the feature goes dead.

**Why this is distinct from [[47|shape 47]] and [[70|shape 70]]:** shape 47 is a single
early-return filter whose one accepted input value is the only one the suite ever
constructs — the fix is varying the input along that one axis. Shape 62 is zero coverage of a
single *sequential* chain (chip click → modal open). This shape is a *fan-in*: several
structurally different entry points (a button's onclick, a keydown listener, a click listener)
converge on one shared destination, and there is no code path shared between "call the
exported function" and "dispatch the DOM event a real user action would actually produce" — so
exercising the destination directly proves nothing about any of the routes into it, no matter
how many exist or how thoroughly the destination itself is tested. A guard closing shape 70 for
the OPENING half of a modal chain says nothing about the CLOSING half having the same fan-in gap.

**Guard form that survives:** when a comment (or the code itself) names N distinct entry
routes into one shared action, dispatch each route's OWN real event or attribute
independently — a real `document.dispatchEvent(new KeyboardEvent('keydown', {key:
'Escape'}))`, a real `overlay.dispatchEvent(new MouseEvent('click'))` where the overlay
itself is the target (not a descendant — that's the exact condition a backdrop check tests),
`clickOnclick()` on the close button — re-opening the target between each, and assert the
shared effect (the modal closes) after each route specifically. A single call to the shared
function proves the function; it proves nothing about whether any particular named route
still reaches it.

**Found:** CMX-299 rework round 3 (2026-08-16), PR #373.
`chela/dashboard/static/js/diffpanel.js`'s `#modal-diff` template comment claims three
dismissal routes — close button, Escape, backdrop click — all routing through
`closeDiffModal()`. `tests/diff_modal_wiring.test.mjs` (rounds 1–2) proved `closeDiffModal()`
itself closes the modal by calling `window.chela.closeDiffModal()` directly at the end of its
one test, never dispatching a keydown or a backdrop click. The judge independently dead-coded
`_diffModalKey`'s `e.key === 'Escape'` check and `_diffModalBackdrop`'s
`e.target.id === 'modal-diff'` check (`if (false && ...)`) and
`CHELA_REQUIRE_JS_TESTS=1 uv run pytest -q` (3181 tests) stayed green under each — a real
Escape keypress or backdrop click would silently do nothing. Closed by two new tests
dispatching a real `KeyboardEvent('keydown', {key: 'Escape'})` on `document` and a real
`MouseEvent('click')` on the `#modal-diff` node itself, each re-opening the modal first and
asserting `.active` is removed by that route alone.
