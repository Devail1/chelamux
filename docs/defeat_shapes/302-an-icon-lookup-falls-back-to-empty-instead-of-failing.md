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
