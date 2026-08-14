## 38. A fixture's DOM is hand-typed to resemble the real template instead of sliced from it, so the template drifting away from the fixture is invisible

**Assertion form:** a JS test that needs the real page's markup (a tab rail an app's
`selectSettingsTab()` renders into, a panel a render function toggles `.active` on) writes
its own `const BODY = \`<nav id="...">...\`\`` literal that *looks like* the template at the
time the test was written — sometimes explicitly commented as "the real modal markup" —
rather than reading `templates/index.html` and slicing the relevant block out of it. The
suite passes because the hand-typed copy and the real template happen to agree today.

**Mutation that defeats it:** change an id, class, or nesting in the REAL template that the
JS being tested actually depends on (`id="settings-tabs"` -> `id="settings-tabs-reverted"`
in `templates/index.html`) without touching the test file at all. The test's own hand-typed
`BODY` string still has the original, correct id, so `document.getElementById('settings-tabs')`
still finds an element and every assertion still passes — the suite is now proving the JS
works against a fixture, not against the page a user is actually served.

**Why this is distinct from shape 7:** shape 7 ("two callers, one guarded") is about a
function with N call sites and a fixture that only ever drives one of them. Here there is
one call site and one fixture, but the fixture is a *duplicate transcription* of the real
source of truth rather than a read of it — the gap is between the fixture and the template
it claims to represent, not between covered and uncovered branches of the code under test.

**Guard form that survives:** `readFileSync` the real template, locate the relevant block
with `indexOf()`/`slice()` against two stable markers (throwing if either marker isn't
found, so the test fails loudly instead of silently reverting to an empty string the moment
the template is restructured), and embed that slice verbatim into the JSDOM body — the same
idiom `tests/dashboard_default_view.test.mjs` already established for `#panel-work`. Never
hand-type markup that a template file already owns.

**Found:** CMX-287 rework round 2 (2026-08-14), PR #358 — `tests/settings_cost.test.mjs`
and `tests/settings_modal_precedence.test.mjs` (the latter added in round 1, closing shape
32 above, and its own comment claimed to drive "the real modal markup") both hand-typed a
`BODY` literal instead of slicing `templates/index.html`. The judge's `id="settings-tabs"`
-> `id="settings-tabs-reverted"` mutation to the real template left the full suite green.
Closed by slicing `templates/index.html` between the `#drawer-scrim` div and the `"+ new"
popover` comment in both files, verified by hand to turn red under the same mutation.
