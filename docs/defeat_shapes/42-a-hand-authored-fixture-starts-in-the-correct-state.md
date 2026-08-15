## 42. A hand-authored fixture starts in the correct state by construction, so the suite never checks that the REAL artifact does too

**Assertion form:** state-transition tests build their own synthetic fixture (a template
literal standing in for a chunk of real markup) and assert its transitions — open then
closed, on then off. The fixture is written correctly at rest (no `open` class, `hidden`
present, whatever the invariant is), so a "starts in the right state" assertion against the
fixture always passes — by construction, not by anything the suite checked. Separately, the
suite reads the REAL source file for one narrow purpose (e.g. confirming a handler attribute
string matches), but never re-checks the same rest-state property against that real markup.

**Mutation that defeats it:** ship the real template with the invariant already violated at
rest (e.g. the modal's root `class` carries the "open" token in the actual `index.html`,
rather than in the test's hand-written stand-in). Every transition test still passes, because
they all drive the *fixture*, and the fixture was never wrong. The one test that does read the
real file checks a different attribute entirely, so it has nothing to say about this one.

**Guard form that survives:** for any rest-state property a fixture asserts by construction,
add a **second** assertion that reads the same property off the REAL source file, not the
fixture. Regex/parse the real markup directly (`REAL_HTML.match(...)`) and assert the same
initial-state invariant against it — a synthetic fixture and a real artifact are not the same
claim, and passing on one says nothing about the other.

**Found:** CMX-288 round 1 (2026-08-14), PR #359. 40 tests covered `openDecisionsMenu`/
`hideDecisionsMenu`/Esc/backdrop/click-through against a hand-built `BODY` fixture whose
`#decisions-menu` div was written without the `open` class — so `assert.ok(!isOpen(), 'sanity:
the modal must start closed')` passed trivially, by fixture construction, every run. The one
test reading the real `chela/dashboard/templates/index.html` (`'the REAL index.html wires the
modal backdrop to hideDecisionsMenu'`) checked the `onclick` attribute string, not the `class`
attribute. Mutating the real template's `id="decisions-menu"` div from
`class="palette-overlay"` to `class="palette-overlay open"` — the modal open on page load,
occluding the Wall, the exact screenshot that opened the ticket — left all 40 tests green.
Closed by a new test that regexes `#decisions-menu`'s `class` attribute directly out of
`REAL_HTML` and asserts it does not include the `open` token.
