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

**Found a fifth time, one axis outside the icon entirely and one CSS declaration outside the
width/padding pair:** every prior round pinned an attribute of the `<svg>` or `.ar-role`
*box* — none of them ever read the badge's own **text**, and none read the two CSS
properties (`min-width`/`max-width`) that override `width` outright rather than sitting
beside it. `chela judge` round 5 (2026-08-17, same PR) found three:
1. **Put the word back next to the icon** — `nav.js`'s call site changed from
   `title="Orchestrator session">${lucideIcon('crown', 12)}</span>` to
   `title="Orchestrator session">Orchestrator${lucideIcon('crown', 12)}</span>`. This is the
   *inverse* of every icon-content mutation above: instead of erasing the icon, it reinstates
   the exact pre-CMX-302 bug (a badge wide enough to truncate the session name beside it) by
   adding a sibling text node the icon assertions never look past. `svg.querySelector('path')`,
   the literal `d`-data deepEqual, `stroke`, `stroke-width`, `width`/`height`, `title` and
   `classList` are all read off the `<svg>` or the badge's *attributes* — none of them read
   the badge's `textContent`, which is the one property this mutation actually changes. The
   original text-pill assertion (`assert.equal(badge.textContent, 'Orchestrator')`) was
   *replaced*, not kept inverted, when CMX-302 first landed — nothing was left checking that
   the text stayed gone.
2. **Widen the `<svg>`'s `viewBox`, not its `width`/`height`** — `viewBox="0 0 24 24"` →
   `viewBox="0 0 2400 2400"` in `util.js`. The crown's path data is authored in a 24×24
   user-space box; widening the viewBox 100× maps that same, byte-identical `d` data into 1%
   of the rendered area — a sub-pixel speck, the same "renders nothing visible" result as
   round 3's `stroke="none"` and round 4's `stroke-width="0"`, reached through the one
   remaining geometry attribute (the coordinate system the path is drawn *into*) rather than
   the box attributes (`width`/`height`) round 4 already pinned.
3. **CSS `min-width: 180px`**, appended beside round 4's already-guarded `padding: 0` on the
   same `.ar-role.orchestrator` declaration line — `tests/sidebar_role_badge_css.test.mjs`
   read exactly `display`, `width`, `paddingLeft`, `paddingRight`; `min-width` is a third,
   completely unread mechanism for regrowing the same box, and — unlike `padding` under
   `box-sizing: border-box` — it beats a `width` declaration outright in real layout, so it
   reproduces the reported bug in its purest form while `getComputedStyle(badge).width` keeps
   reporting the untouched `18px`.

`CHELA_REQUIRE_JS_TESTS=1 uv run pytest -q` stayed green (3211 passed) with each mutation
applied in isolation. Closed by reading past the icon entirely on the first axis, and past
`width`/`padding` on the CSS axis: `tests/sidebar.test.mjs` now also asserts
`badge.textContent === ''` (no text node exists anywhere in the badge, not just inside the
`<svg>`) and `svg.getAttribute('viewBox') === '0 0 24 24'`;
`tests/sidebar_role_badge_css.test.mjs` now also asserts `getComputedStyle(badge).minWidth
=== '18px'` and `.maxWidth === '18px'`, backed by an explicit `min-width: 18px; max-width:
18px;` added to the `.ar-role.orchestrator` rule itself (previously only `width` constrained
the box, so an appended `min-width` elsewhere in the same rule had nothing to lose to).
Five rounds in, the shape has now been found on every layer the badge is built from — map
entry, path identity, paint colour, paint width, box size, box padding, coordinate system,
sibling text, and the two properties that override `width` outright — which is the argument,
made concretely rather than in the abstract, for the single-assertion "read the badge's whole
visual contract at once" refactor a prior round's non-blocking note already proposed: a
guard built one named attribute at a time will keep finding exactly one more named attribute,
because that is the shape of how it was built, not a property of this particular badge.

**Found a sixth time, the two remaining CASCADE "off switches" plus one property first
mis-classified as unguardable:** round 5's CSS guard read `display`, `width`, `paddingLeft`/
`paddingRight`, `minWidth`, `maxWidth` — five properties, still an enumerated list. `chela
judge` round 6 (2026-08-17, same PR) found three more, all on `.ar-role.orchestrator`:
1. **`visibility: hidden`** and **`opacity: 0`**, independently — `display`'s two direct
   cascade siblings on the "erase this element" spectrum ([shape 67](67-a-computed-style-visibility-guard-is-written-for-a.md)'s
   general form). Neither touches any of the five already-pinned properties, so the badge
   painted nothing while every prior assertion — icon content, paint, box size — stayed green.
2. **`margin: 0 80px`** — the one box property OUTSIDE the border box, unreachable by
   `box-sizing: border-box` and therefore invisible to `width`/`padding`/`min-width`/
   `max-width` alike. Under the row's real flex layout this regrows the badge to ~178px,
   reproducing the reported truncation bug in its purest form.

A human directive on this PR (2026-08-17, superseding round 6's per-property framing)
correctly closed `visibility`/`opacity` as ONE collapse-check group (reusing the trio
CMX-298 settled on for `.kanban-card-parked`) instead of a sixth/seventh named property —
but INITIALLY treated `margin` as belonging to the same bucket as `transform`/`clip-path`/
off-screen positioning: properties CMX-273's spike (`docs/SPIKE_WALL_FILLS_STAGE.md`)
established jsdom cannot observe because they need a real layout engine, and prescribed a
NOT GUARDED note plus a one-time browser capture instead of a guard. That classification
was wrong, and was caught by testing it rather than reasoning from the property's name:
`getComputedStyle(el).marginLeft`/`.marginRight` in jsdom resolves the literal declared
length exactly the same way `width`/`padding`/`min-width`/`max-width` already do — verified
directly (`node -e` against a real jsdom instance and this repo's real `style.css`) before
writing the NOT GUARDED note, not after. `margin` is CASCADE, not LAYOUT, and is guarded
directly like its siblings. The genuine LAYOUT boundary — verified the same way — is
RESOLVED GEOMETRY (`getBoundingClientRect`/`offsetWidth`, always zero in jsdom; percentage/
`vw`/flex-distributed widths, which never resolve against a real parent), not "any property
that sits outside the border box" or "any property that sounds like it needs layout."

`CHELA_REQUIRE_JS_TESTS=1 uv run pytest -q` stayed green (3211 passed) with each of the
three mutations applied in isolation. Closed in `tests/sidebar_role_badge_css.test.mjs`:
`display`/`visibility`/`opacity` as one assertion group (not re-narrowed to a fourth/fifth
name later), and `marginLeft`/`marginRight === '0px'` as a direct guard — applied to BOTH
`.ar-role.orchestrator` and the new `.ar-role.dispatched` bot-icon badge (CMX-302 item 2,
same round), whose shared box-model declarations were deduplicated into one CSS rule so the
two badges cannot drift apart. The meta-pattern this round adds to the five above: before
citing a "needs real layout" precedent as the reason NOT to guard something, run the
two-line jsdom check that precedent was originally built on — a property's category in the
CSS spec, or its name, is not evidence of what a specific harness can or cannot observe.
