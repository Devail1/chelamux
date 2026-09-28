## 377c. An `if (el)`-guarded DOM update silently no-ops because the test fixture never mounts the element at all

**Assertion form:** `toggleSidebar()` (nav.js) updates TWO controls' `aria-expanded` on every
open/close: the desktop rail's `#btn-menu` (asserted by
`tests/sidebar.test.mjs`'s "on a desktop the control collapses the rail" test) and the phone
drawer's floating `#btn-menu-mobile` fab (`fab.setAttribute('aria-expanded', open ? 'true' :
'false')`, defensively `if (fab) ...` since the fab is phone-only markup). The suite's own
phone-drawer round-trip test ("opening the phone drawer sets body.sidebar-open; closing it
clears it") drives `toggleSidebar()` for real on a real `document.body`, but that file's
hand-typed `BODY` fixture (top of the file) never included `#btn-menu-mobile` at all —
`document.getElementById('btn-menu-mobile')` returns `null` every time the real code runs
inside this suite, so the `if (fab)` guard is always false and the `setAttribute` line never
executes, in test or in production-equivalent code path.

**Mutation that defeats it:** `fab.setAttribute('aria-expanded', open ? 'true' : 'false')` →
`fab.setAttribute('aria-expanded', 'false')` (a constant, never re-derived from `open`).
`CHELA_REQUIRE_JS_TESTS=1 uv run pytest -q` stayed green (4109 passed) with the mutation in
place — not because any assertion tolerated the wrong value, but because no assertion in the
whole suite ever reached a live `#btn-menu-mobile` node to read `aria-expanded` off in the
first place. A screen reader announcing the drawer as permanently "collapsed" regardless of its
actual state would ship unnoticed.

**Why this is a distinct shape from a plain missing assertion:** the code being silent is not
the same failure as the code being *absent from the fixture*. A test that calls
`toggleSidebar()` and asserts nothing about the fab would at least be an honest gap — visibly
"we don't check this." Here the surrounding test LOOKS like it proves `toggleSidebar()`'s
full phone-drawer effect (it drives the real function, checks `body.sidebar-open` both ways,
reads like end-to-end coverage of "opening the drawer"), while one whole branch of that same
function's real behavior — gated behind an `if (el)` null-check the fixture never satisfies —
runs zero times and is asserted on zero times. The `if (fab)` guard exists so the SAME function
works safely on a desktop fixture that legitimately has no fab; that legitimate defensive
pattern is indistinguishable, from inside the test, from "the fixture forgot to mount the
element this code path needs."

**Guard form that survives:** when a fixture is hand-typed (not `sliceTemplate()`'d off the
real template), audit it against every element ID the function-under-test's own `if
(document.getElementById(...))` guards reach for — a defensive null-check is exactly the
signal that this element is optional in SOME contexts and therefore easy to forget in a test
fixture that only has "some" of them. Add the element to the fixture and assert its state
through the SAME round-trip the test already drives (extending the existing open/close test,
not a parallel one, keeps the two related facts — `body.sidebar-open` and the fab's own
`aria-expanded` — from drifting apart again the way they just did).

**Found:** CMX-377 round 3 (PR #529), judge mutation 6, 2026-09-28. Closed by adding
`#btn-menu-mobile` to `tests/sidebar.test.mjs`'s `BODY` fixture and extending "opening the
phone drawer sets body.sidebar-open; closing it clears it" to also assert the fab's
`aria-expanded` flips `false` -> `true` -> `false` across the same round trip.
