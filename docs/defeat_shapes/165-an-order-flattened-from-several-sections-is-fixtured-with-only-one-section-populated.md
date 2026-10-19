## 165. An order flattened from several sections is fixtured with only one section populated

**Assertion form:** a function flattens a structured model into ONE display order:
`sidebarWids(model)` walks `needsYou`, then every group's items, then `archived`, and for
each row emits its primary window and then its other windows. The guard is exact
(`deepEqual` over the whole pill strip, against a negative control in a different order),
but every fixture is three plain, idle, one-window sessions: nothing in Needs you, nothing
archived, no row with a second window. The "not in the order" tail is fixtured with ONE
unknown window.

**Mutation that defeats it:** drop any section or sub-part that no fixture fills:
`...(model.needsYou || [])` or `...(model.archived || [])` deleted, `[prim, ...it.windows]`
cut to `[prim]` (a run's judge window falls out) or to `[...it.windows]` (the judge leads
its own agent), or the unknown tail `.reverse()`d. Each stays green: an empty section
contributes nothing whether or not it is walked, a one-window row's `windows` is just
`[prim]`, and a one-item tail reads the same reversed.

**Why it looks covered:** the order assertion is exact and has a negative control, so it
reads as pinning the order itself. It pins the order of the ONE section the fixture
populates. A dropped section does not even fail loudly in production: its windows still
get pills, just in the "unknown" tail, so the bug only shows as a wrong position.

**Guard form that survives:** one test per section and per sub-part, each fixturing that
part non-empty AND placed where its absence changes the answer: a row blocked on you (it
must LEAD the strip), a dispatched run whose judge window is listed BEFORE its agent (the
agent's pill must lead), an archived run whose group was drawn first (it must move to the
bottom, before the unknowns), and two unknowns in a known order. Arrange the negative
control (here `#term-agent`'s option order) so a window that falls out of the order lands
somewhere no expected answer has it. The same goes for every filter the order is meant to
see THROUGH (View Status, Environment, Last activity): one fixture per filter that hides a
row from the drawn sidebar, asserting its pill keeps its group's position.

**Found:** CMX-165 rework round 1, PR #715. `chela/dashboard/static/js/sidebarmodel.js`
`sidebarWids()` / `orderByList()`, `nav.js` `sidebarOrder()`; fixed in
`tests/mobile_swipe_sidebar_order.test.mjs` (the pill strip it guarded was removed in CMX-202; the swipe keeps the order).
