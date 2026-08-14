## 48. A rendered property is split across a real-artifact half and a synthetic-fixture half, and nothing ever composes the two real halves together

**Assertion form:** a single visual property actually depends on TWO real files agreeing — real
markup carrying the right class/attribute, and the real stylesheet resolving that class to the
right computed value. Two guards each close one half, and each is individually well-formed: one
reads the class **attribute text** off the real template (`REAL_HTML.match(...)`, per
[[42|shape 42]]'s fix) to prove the markup names the right class; the other mounts a **hand-built**
fixture carrying that same class name under the real stylesheet and reads the **computed style**
via `getComputedStyle` (per [[5|shape 5]]'s fix) to prove the stylesheet rule renders the right
value. Neither guard is wrong on its own — but nothing ever mounts the REAL markup under the
REAL stylesheet and reads the REAL computed value in one place.

**Mutation that defeats it:** add an inline `style="..."` attribute to the real element in the
template that beats the cascade (e.g. `style="display:flex"` on a div meant to render
`display:none` at rest via its class). The attribute-text guard still passes — the class list is
untouched, still contains the right class, still lacks the wrong one. The CSS-cascade guard still
passes — it never mounted this element at all, only a hand-built stand-in with the same class
name and no inline style. The property the two guards were written to jointly protect (this
specific element renders this specific computed value) is now false in production, and both
guards are green.

**Why this is distinct from [[38|shape 38]]/[[42|shape 42]] and [[5|shape 5]] individually:**
each of those shapes is "a single guard reads a fixture/source-constant instead of the real
artifact/rendered-value" — the fix for either one, alone, is to switch that one guard onto the
real thing it was missing. This shape is what remains **after both fixes have already landed
separately**: each guard now correctly reads its own real half, but the two halves were fixed by
different people in different rounds, aimed at different properties (attribute text vs. rendered
style), and neither fix noticed the other guard existed. The join between them — real markup fed
into the real stylesheet — was never anyone's assignment.

**Guard form that survives:** when a property depends on real markup AND a real stylesheet
together, write (or add) at least one guard that slices the actual element out of the real
template file (`readFileSync` + a stable marker/regex, not a hand-typed literal — [[38|shape
38]]) and mounts that exact slice inside a jsdom document that also loads the real stylesheet
text, then reads the property via `getComputedStyle` on that mounted real element — not on a
synthetic stand-in with a matching class name. This is strictly more than the union of the two
half-guards: an inline style, a `!important` override, or any other real-file-only defect that
beats the cascade only shows up on the actual element, never on a hand-built proxy for it.

**Found:** CMX-288 rework round 5 (2026-08-14), PR #359. Round 1 (shape 42) added a
`REAL_HTML`-based test that `#decisions-menu`'s class attribute lacks the `open` token at rest.
Round 4 (shape 5's recipe) added `tests/decisions_modal_css.test.mjs`, which proved
`.palette-overlay`'s `display` and `.modal-sheet`'s `width` render correctly — but against a
hand-built `<div class="palette-overlay"><div class="modal-sheet">` of its own construction, never
against the real `#decisions-menu` sliced from `index.html`. The judge added
`style="display:flex"` to the real div in `index.html`: the round-1 class-attribute guard stayed
green (no class changed), the round-4 CSS-cascade guard stayed green (it never mounted this
element), and all 3132 tests passed while the modal shipped permanently visible over the
dashboard on page load — the exact occlusion bug this PR exists to fix. The same gap covered a
second, sibling property in the same finding round: `.modal-sheet-body`'s `overflow-y` had no
guard reading either half at all. Closed by slicing the real `#decisions-menu` block out of
`index.html` and mounting it under the real `style.css`, asserting `display`/`width`/`overflow-y`
on the mounted real element rather than a hand-built stand-in.
