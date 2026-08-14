## 43. Fixing one wiring point against the real artifact leaves its structurally-identical siblings uncovered

**Assertion form:** the same gap as [[32|shape 32]] — a hand-authored fixture hardcodes its own
copy of an attribute the real template also carries (an `onclick`, a `role`), so every test
that drives the fixture proves nothing about the real file. Shape 32's own fix closed exactly
ONE such attribute (the modal backdrop's `onclick`, regexed out of `REAL_HTML`) and stopped
there — but the same real template carries at least three MORE structurally-identical wiring
points (the open button's `onclick`, the close button's `onclick`, the modal's `role="dialog"`),
each hardcoded into the SAME fixture the same way, each just as unchecked against the real file
as the backdrop was before shape 32's fix.

**Mutation that defeats it:** strip any of the sibling attributes — `onclick=
"chela.openDecisionsMenu()"` off the real open button, `onclick="chela.hideDecisionsMenu()"`
off the real close button, `role="dialog"` off the real modal sheet — while leaving the fixture
(and therefore all 40+ fixture-driven tests) untouched. The one test shape 32 added checks a
DIFFERENT attribute on a DIFFERENT element, so it has nothing to say about any of these; the
suite stays green while the button a human actually clicks to reach the feature does nothing.

**Why this is distinct from shape 32:** shape 32 names the *mechanism* (a fixture can be
correct-by-construction while the real file drifts). This shape is what happens the round
after that mechanism is diagnosed and fixed at only one of several sites it applies to — the
same "count the call sites" discipline shape 7 prescribes for JS function callers applies
here to markup attributes inside one template: every attribute the hand-authored fixture
duplicates from the real file is a separate site that needs its own `REAL_HTML` assertion,
not just the one the previous round happened to be looking at.

**Guard form that survives:** when a hand-authored DOM fixture duplicates attributes from a
real template (as opposed to attributes the fixture only needs for its own internal wiring),
enumerate every one of them — `grep` the real file for the element in question — and add one
`REAL_HTML.match(...)`/`assert.match(REAL_HTML, ...)` assertion per attribute, not just the
one attribute the current finding named. A guard added for one wiring point says nothing
about its siblings in the same fixture.

**Found:** CMX-288 rework round 2 (2026-08-14), PR #359. Round 1 (shape 32) added exactly one
`REAL_HTML` assertion, for the backdrop's `onclick`. The judge's round-2 mutations stripped
`onclick="chela.openDecisionsMenu()"` from the real `#btn-decisions`, `onclick=
"chela.hideDecisionsMenu()"` from the real close button, and `role="dialog"` from the real
`.modal-sheet` — three siblings of the same shape, none previously checked — and all 45 tests
(the 40 from round 1 plus round 1's own new backdrop/rest-state guards) stayed green under
each. Closed by three more `REAL_HTML` regex assertions, one per attribute, plus a fourth,
unrelated guard in the same round: `_markSeen()` inside `openDecisionsMenu` (the "opening
marks events seen" claim in that function's own doc comment) had never been driven by any
test at all — dead-coding it to `if (false) _markSeen();` also survived, closed by a new test
that primes an unseen event and asserts the unread badge actually clears after
`openDecisionsMenu()` runs.

**Round 3 recurrence (2026-08-14), same PR:** two more siblings of the same shape, still on
`#decisions-menu`. The `open`-at-rest test (round 1) only checked the token `"open"` was
absent from the class list — it never checked `"palette-overlay"` was *present* — so renaming
the shell class itself to `"decisions-modal"` (the PR's stated fix, "reuses the shared
`.palette-overlay` shell", silently un-done) stayed green: the `open` class then toggles no
CSS rule and the sheet renders inline/always-visible, the exact bug this PR fixed. And
`#btn-decisions`'s `onclick` attribute was pinned in round 2, but its sibling
`aria-haspopup="dialog"` attribute (changed deliberately, alongside `role="dialog"` on the
sheet, from the old `aria-haspopup="true"`) was not — reverting it to `"true"` stayed green
too. Closed by one more `REAL_HTML` assertion each: the class-list check extended to require
`"palette-overlay"` present (not just `"open"` absent), and a new regex pinning
`aria-haspopup="dialog"` on `#btn-decisions`.
