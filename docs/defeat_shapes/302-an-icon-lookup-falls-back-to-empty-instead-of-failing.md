## 302. An icon-lookup helper falls back to empty markup instead of failing on an unknown key

**Assertion form:** a guard proves a specific icon renders by checking that the badge
contains *an* `<svg>` element — `assert.ok(badge.querySelector('svg'))`. The helper behind it,
`lucideIcon(name)`, looked the name up in a vendored map and rendered whatever it found:
`` `<svg ...>${_LUCIDE[name] || ''}</svg>` ``. A name present in the map renders its real path
data; a name absent from the map (typo'd, renamed, or dropped in a merge) still returns a
syntactically valid, non-null `<svg>` element — just an empty one, with zero child nodes.

**Mutation that defeats it:** rename or delete the map entry the badge's call site depends on
(e.g. `'crown': '<path .../>'` → dropped, while the call site still reads
`lucideIcon('crown', 12)`). `badge.querySelector('svg')` still returns the (now-empty) `<svg>`
node — the assertion was only ever checking "is there an SVG tag", never "did the SVG get any
content" — so the guard stays green while the badge silently renders a blank shape in
production. The same shape defeats any check phrased as *presence of the wrapper element*
rather than *presence of the thing the wrapper is supposed to contain*.

**Guard form that survives:** make the failure happen at the source instead of leaving it for
a caller to notice (or not) downstream — `lucideIcon` now throws
(`if (!(name in _LUCIDE)) throw new Error(...)`) the moment it's asked for a name the map
doesn't have, rather than degrading to `''`. This turns an unbounded set of possible call
sites that would each need their own "is the SVG actually non-empty" assertion into one
guarded chokepoint: any renamed or dropped map entry now breaks the render immediately and
loudly (a thrown exception during `renderSidebarAgents`, not a passing test and a blank icon
in the DOM), and a single direct test —
`assert.throws(() => lucideIcon('not-a-real-lucide-icon'), /unknown icon/)` — proves the
chokepoint itself works, independent of any one badge's markup. Existing per-badge
`querySelector('svg')` checks are still useful for confirming *an icon was requested at all*,
but they can no longer stand in for "and it actually resolved" — that half now lives in the
helper's own contract, checked once.

**Found:** `chela/dashboard/static/js/util.js::lucideIcon` / `tests/sidebar.test.mjs`
(CMX-302, PR #376, rework round 1) — noticed while re-checking the orchestrator-badge guard
(`badge.querySelector('svg')`) against what it would actually catch if the `crown` map entry
went missing: nothing, because an empty `<svg>` still satisfies that assertion.

**Found again, the sibling mutation the round-1 fix didn't reach:** the round-1 fix above
guards the ABSENT-key form of this shape (`name in _LUCIDE` is `false`) — it says nothing
about a key that is PRESENT but maps to an empty string, which is a second, independent way
to reach the exact same downstream symptom (an empty `<svg>`). `chela judge` round 2
(2026-08-17, same PR) mutated `'crown': '<path .../>'` → `'crown': ''` — the key is still
`in _LUCIDE`, so the round-1 throw never fires, `lucideIcon('crown', 12)` still returns a
syntactically valid empty `<svg>`, and `badge.querySelector('svg')` — the very check this
shape's own write-up called "still useful for confirming an icon was requested at all" —
still finds it. `CHELA_REQUIRE_JS_TESTS=1 uv run pytest -q` stayed green (3210 passed) with
this mutation applied, one round after round 1 had just finished writing a fix whose own
description named the general danger ("presence of the wrapper vs. presence of the thing
the wrapper is supposed to contain") without checking for every way the content can go
missing. This is the same meta-pattern [shape 52](52-an-index-guard-s-own-prescribed-fix-still-leaves-a.md)
found for a positional lookup: closing a guard against one degrade-to-a-constant shortcut
(missing key) does not, by itself, rule out a sibling shortcut (present-but-blank value)
that reaches the same observable side effect through a different mechanism. Closed by
hardening the per-badge assertion alongside the chokepoint: `tests/sidebar.test.mjs` now
also asserts `svg.querySelector('path')` on the rendered crown badge — content, not just
wrapper presence — on top of (not instead of) the existing `querySelector('svg')` check and
the round-1 `in _LUCIDE` throw.

**Found a third time, two more independent mechanisms `svg.querySelector('path')` still can't
see:** the round-2 fix still answers only "did the interpolation produce a non-empty child" —
neither "which child" nor "is the child's own paint attribute intact" follows from that.
`chela judge` round 3 (2026-08-17, same PR) found both:
1. **Swap the icon name at the call site**, not the map entry — `lucideIcon('crown', 12)` →
   `lucideIcon('minus', 12)` in `nav.js`. Every entry in `_LUCIDE` has at least one `<path>`,
   so `svg.querySelector('path')` is exactly as true for the wrong icon as the right one — a
   presence check can't tell a crown from a dash (the general form of this half is
   [shape 78](78-a-distinctness-only-assertion-stands-in-for-the-designed-literal.md): a
   distinctness/presence check standing in for the designed literal value).
2. **Kill the paint, not the geometry** — `lucideIcon`'s `stroke="currentColor"` (these are
   outline icons: `fill="none"`, so the stroke IS the visible ink) → `stroke="none"`. The
   `<path>` node and its exact `d` data stay in the DOM untouched, and no CSS rule changes, so
   neither a literal-path-data guard (closing mutation 1) nor a `getComputedStyle` check on
   the badge's *wrapper* (`display`, `width` —
   [shape 67](67-a-computed-style-visibility-guard-is-written-for-a.md)'s recipe, added to
   `tests/sidebar_role_badge_css.test.mjs` in round 2) sees it, because `stroke` is an SVG
   *presentation attribute* on the icon's own element — one layer beneath both the DOM-presence
   check and the wrapper's CSS cascade. The badge renders a correctly-shaped, correctly-sized,
   zero-ink outline: visually identical to the bare colour dot CMX-300 was written to forbid.

`CHELA_REQUIRE_JS_TESTS=1 uv run pytest -q` stayed green (3211 passed) with either mutation
applied in isolation. Closed in `tests/sidebar.test.mjs` by asserting, independent of
`_LUCIDE` (hardcoded, so a corrupted map entry can't satisfy its own test): the rendered
`<path>` elements' exact `d` attributes equal the crown icon's own literal path data
(`assert.deepEqual([...svg.querySelectorAll('path')].map(p => p.getAttribute('d')), [...])`),
and the `<svg>`'s own `stroke` attribute equals the paint-producing value
(`assert.equal(svg.getAttribute('stroke'), 'currentColor')`) — both alongside, not instead of,
the existing `querySelector('path')` and CSS-cascade checks. Icon identity and icon paint are
two axes a single presence boolean cannot separate; each needed its own literal-value
assertion.

**Found a fourth time, one hop further out on each of the same two axes, plus a sibling on the
CSS side:** round 3's fix pinned `stroke="currentColor"` (the paint's *colour*) and the crown's
own literal `d` data (its *identity*), and round 2's CSS test pinned the wrapper `<span>`'s
cascaded `width`. Each pin left an adjacent attribute, on the same element or the same CSS
rule, completely unread. `chela judge` round 4 (2026-08-17, same PR) found three:
1. **`stroke-width="0"`** — the sibling paint attribute beside `stroke`. The colour assertion
   from round 3 only checks *which* colour the stroke paints with, never *how wide* the line
   is; zero width leaves `stroke="currentColor"`, every `<path>` node and its `d` data
   completely untouched while drawing a zero-ink outline — same visible result as round 3's
   `stroke="none"`, one attribute over.
2. **`lucideIcon('crown', 0)`** — the size argument at the call site (`nav.js`), which no
   assertion anywhere reads and no CSS rule backstops: the wrapper `<span>`'s 18px (round 2's
   `sidebar_role_badge_css.test.mjs` guard) sizes the `.ar-role.orchestrator` box, not the
   `<svg>` inside it — there is no `.ar-role svg { width: ... }` rule. A 0×0 `<svg>` keeps its
   real path data, stroke paint, title and class, so every prior assertion on this icon stays
   green while it has no area left to draw into.
3. **CSS `padding: 0 80px`** — the sibling declaration beside round 2's `width: 18px` inside the
   same `.ar-role.orchestrator` rule. `sidebar_role_badge_css.test.mjs` reads only the cascaded
   `width`; `box-sizing: border-box` clamps *content* to that declared width, not padding, so
   nonzero padding regrows the exact same box the ticket was written to shrink — a badge wide
   enough to truncate the session name beside it — while `getComputedStyle(badge).width` keeps
   reporting `18px`.

`CHELA_REQUIRE_JS_TESTS=1 uv run pytest -q` stayed green (3211 passed) with each mutation
applied in isolation. Closed by reading the two sibling attributes/declarations directly,
alongside (not instead of) every existing check: `tests/sidebar.test.mjs` now also asserts
`svg.getAttribute('stroke-width') === '2'` and `svg.getAttribute('width') === svg.getAttribute('height') === '12'`
(the exact size the call site passes); `tests/sidebar_role_badge_css.test.mjs` now also asserts
`getComputedStyle(badge).paddingLeft === '0px'` and `paddingRight === '0px'`. The recurring
meta-pattern across all four rounds: a guard that pins one attribute/declaration of a
multi-attribute mechanism (map key, paint colour, cascaded width) leaves every sibling
attribute of that *same* mechanism (paint width, size argument, padding) completely unread —
closing a shape means asking not just "what else could remove this content" but "what other
attribute on this exact element or rule controls the same visible outcome."
