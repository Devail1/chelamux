## 398b. A fallback branch is tested only where a downstream clamp saturates every candidate to the same value

**Assertion form:** a function chooses between candidates, then clamps the choice into a
range: `placePopover` (`nav.js`, CMX-398) opens a popover below its anchor, else above, else
"on the ROOMIER side", and then clamps `top` into `[8, vh − 8 − h]`. The suite has a test for
the "neither side fits" case: a 2000px menu at the foot gear, which asserts `top === 8`, a
max-height and a scrollbar. That reads as covering the roomier-side fallback.

**Why the obvious tests don't catch it:** the too-tall menu is first capped to the viewport's
room (`h = vh − 16`), which makes the clamp range a single point: `[8, vh − 8 − h] = [8, 8]`.
Every candidate `top`, whether roomier side, other side or garbage, clamps to 8. The
assertion can only see what comes out of the clamp, so it cannot tell which branch ran. This
is the clamp version of [[342]]: both quantities coincide, but here it is because the clamp
saturates, not because of how the fixture was built. It is easy to miss because the fixture
looks like the right one: it is the "doesn't fit either way" case.

**Mutation that defeats it:**
`(vh - r.bottom >= r.top)` → `(vh - r.bottom < r.top)` (put it on the SMALLER side). The
suite stayed green (4359 passed).

**Guard form that survives:** for every branch that feeds a clamp, pick a fixture where the
clamp range is wider than the gap between the candidates, so they land on different values
after clamping. Here the menu has to fit the viewport but fit neither above nor below the
anchor: vh=900, h=500 (no max-height), anchor at 400–438 ⇒ roomier = below ⇒ `top = 392`,
while the other side would give 8. Pin the mirror case as well (anchor at 470–508 ⇒ roomier =
above ⇒ 8, other side ⇒ 392) so that flipping the comparison either way goes RED. Assert
the precondition too (`maxHeight === ''`), so a later fixture edit can't quietly push the
menu back into the saturated range.

**Found:** CMX-398, PR #550, round 2. Closed by `tests/popover_placement.test.mjs`'s
"neither side has room: the menu goes on the ROOMIER side" test. The judge's mutation turns
it RED.
