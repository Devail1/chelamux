## 39. A control's selection indicator is computed by a separate step from its functional effect, and only the effect is asserted

**Assertion form:** a state-changing setter (`setCostWindow(win)`) does two things on every
call — it produces a functional effect (re-fetch, re-render a table/list keyed on the new
state) and, as a separate step, marks which control in a group visually represents the
current state (`.active` class, `aria-pressed`). A test drives the setter and asserts the
functional effect in detail (the fetch URL, the re-rendered table's contents) but never reads
the selection indicator back off the control group at all.

**Mutation that defeats it:** dead-code only the indicator step, leaving the functional
effect untouched — `const on = b.dataset.win === _window` -> `const on = false &&
b.dataset.win === _window`. Every fetch/table assertion still passes exactly as before,
because the setter's other half (which segment is highlighted) was never the thing under
test. The user-visible result: the fetch goes to the right window and the table shows the
right data, but no segment is ever marked selected, and `aria-pressed` stays permanently
`true` on whichever segment happened to render that way first — which is what a screen
reader announces regardless of which window is actually showing.

**Why this is distinct from shape 5:** shape 5 (and its recurrences) is about a test that
never touches the RIGHT artifact — it reads a source constant, or calls a handler function
that bypasses the DOM binding a real click goes through. Here the test drives the real
setter through the real binding and gets a real, correct effect back; the gap is that the
setter has two outputs and the test only reads one of them. The fix is not "read the
rendered value instead of a stand-in" (shape 5's lesson) — it is "when a state-changing
function has more than one observable output, assert all of them, not just the one already
under scrutiny."

**Guard form that survives:** after driving the state change, read back every control in the
group's `.active`/`aria-pressed` (or equivalent selection-indicator attribute) — the newly
selected control should show it, and the previously selected one should have lost it. A
census of "what does this function's own body touch on the DOM" (same idea shape 7
prescribes for call sites) surfaces the second output; asserting only the first one it
happens to be adjacent to in the test file is what leaves the second unguarded.

**Found:** CMX-287 rework round 4 (2026-08-14), PR #358 — `tests/settings_cost.test.mjs`'s
Cost-window switcher test called `window.chela.setCostWindow('7d')` and asserted the
re-fetched URL and the re-rendered table's cost figures, but never read
`.cost-window-btn`'s `.active` class or `aria-pressed` attribute back. `cost.js`'s
`_applyWindowButtons()` — the only code that moves either off the template's hardcoded Live
default — was dead-coded (`const on = false && ...`) and the suite stayed green. Closed by
reading both attributes off the newly-selected and previously-selected segments after every
state change (in the direct-call test and again in the onclick-binding test added alongside
it for the sibling gap — see `docs/defeat_shapes/05-asserting-a-source-constant-instead-of-the-rendered-value.md`'s
fourth "Found" entry).
