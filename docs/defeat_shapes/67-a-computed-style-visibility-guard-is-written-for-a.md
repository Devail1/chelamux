## 67. A computed-style visibility guard is written for a child element and an enumerated property list — leaving the parent element and any un-enumerated property free to erase it

**Assertion form:** a guard closes shape [[54|54]] correctly — it mounts the real markup under
the real stylesheet in a `pretendToBeVisual` jsdom and reads `getComputedStyle` instead of
trusting `classList`/`textContent`. But it stops at the first element that satisfies the test's
own title: it reads `getComputedStyle` on the CHILD element the feature is about (a chip nested
inside a card), and it proves visibility by listing the handful of CSS properties the PR's diff
happened to touch (`display`, `visibility`, `overflow`, `text-overflow`, `white-space`) rather
than the general "is this rendered" question.

**Mutation that defeats it:** two independent mutations, both left after a guard that read only
the child chip's five named properties:
1. Collapse the PARENT element's own opacity — `.kanban-card-parked { opacity: 0.85 }` →
   `opacity: 0`. The child chip's own computed style is untouched (`opacity` does not inherit
   its *computed* value the way `color` does — a child's `opacity` computes independently of
   its ancestor's), so every assertion the guard makes on the chip stays green while the whole
   card, chip included, is invisible on screen.
2. Zero a property on the SAME rule the guard already reads, but one the guard's property list
   didn't enumerate — `.kanban-parked-reason { font-size: 10px }` → `font-size: 0`. `display`,
   `visibility`, `overflow`, `text-overflow`, and `white-space` all keep their exact asserted
   values; the glyph and text both render at zero size, i.e. not at all.

Both mutations landed live on CMX-298, PR #372, round 6 (2026-08-16) — `CHELA_REQUIRE_JS_TESTS=1
uv run pytest -q` stayed green (3171 passed) with either applied in isolation, one round after
round 5 closed shape 54 for the very same chip.

**Why proving the child is visible doesn't prove the card is visible:** CSS visibility
properties are evaluated per-element. A child that is `display: block; visibility: visible;
opacity: 1` on its own computed style can still be invisible on screen because an ANCESTOR is
`display: none`, `visibility: hidden`, or `opacity: 0` — none of which force a matching computed
value onto descendants (`opacity` composes visually via alpha-multiplication, it does not
propagate as an inherited computed property; `display: none` removes the box entirely, which
jsdom's `getComputedStyle` on the still-attached child does not reflect back). A guard scoped to
"prove the feature's own new element is visible" silently assumes every ancestor between it and
the document root is already covered elsewhere — which is exactly the assumption round 5 left
unstated for `.kanban-card-parked`, the rule the *same* PR added one level up from the chip it
was guarding.

**Why an enumerated property list doesn't prove the rule is intact:** `display`/`visibility` are
the two CSS-standard "off switches," but they are not the only way a declaration can render an
element imperceptible — `opacity: 0`, `font-size: 0`, `color: transparent` on text,
`clip-path`/`clip: rect(0,0,0,0)`, and `width: 0; overflow: hidden` all collapse visible content
to nothing without touching `display` or `visibility`. A guard that asserts five named
properties proves those five did not regress; it says nothing about the sixth property the next
one-line edit happens to zero out, because listing properties is closed under "what this PR's
diff touched today," not under "what could erase this element."

**Guard form that survives:** for any element whose reader-visible presence is the point of a
test (not merely its *content*, which `textContent` already covers), assert computed-style
visibility on EVERY element between it and the point where the fixture was mounted — the
feature's own new element AND its containing card/row/section, not just the one the PR's diff
happened to add a rule for — and prefer an "is this collapsed to invisible" check over an
enumerated property list: `getComputedStyle(el).opacity !== '0'` catches every future opacity
edit the same way `!== 'none'`/`!== 'hidden'` catches every future display/visibility edit,
without needing a new assertion line per newly-discovered collapsible property. Where a specific
property genuinely matters beyond "not collapsed" (e.g. `overflow`/`text-overflow`/`white-space`
gating a tooltip fallback), keep that assertion too — it is additive to the collapse check, not
a substitute for it.

**Found:** `tests/kanban_flatten.test.mjs` test 10/11 (CMX-298, PR #372, round 6). Closed by
reading `getComputedStyle` on `.kanban-card-parked` itself (`opacity`, `display`, `visibility`)
alongside the existing `.kanban-parked-reason` assertions, and adding a `font-size !== 0` check
on the reason chip instead of only the five properties round 5 already enumerated.

**See also:** [[54|shape 54]] — the same PR/round pair this shape recurred on; 54 closes
`classList` vs. cascaded-CSS blindness for a single element, this shape is what's left over
once that recipe is applied to only one element in a parent/child pair and only a fixed
property list.
