## 359. A row lifted into one partition is never proven REMOVED from its sibling

**Assertion form:** a renderer partitions one input list into several output buckets that are
supposed to be mutually exclusive — "each item shows in exactly one place." Bucket A (a
special cluster, e.g. "Finished") is built with a positive filter straight off the full input:
`rows.filter(a => special(a))`. Bucket B (the ordinary grouping) is built with a *negative*
filter meant to exclude what A already claimed: `rows.filter(a => !special(a))`. The test for
this invariant stages one row that belongs in A and one that doesn't, then asserts on A alone
— A contains the special row, A's count is right, A doesn't contain the ordinary row. It never
looks at B at all, on the theory that "A is right" already proves the partition.

**Mutation that defeats it:** widen bucket B's filter by dropping its exclusion term —
`rows.filter(a => !wantsHuman(a) && !isDone(a))` → `rows.filter(a => !wantsHuman(a))`. Bucket
A is built independently, straight from `rows`, so it is completely untouched by this change
and every assertion made against it keeps passing exactly as before. But B now ALSO contains
the row A already claimed, so that row renders a second time — once inside the special
cluster, once again in its ordinary project group below. `CHELA_REQUIRE_JS_TESTS=1 uv run
pytest -q` stayed green (3937 passed, 0 failed, 0 error(s)) with the corruption in place,
because nothing counted how many times the row appeared across *both* buckets — only how many
times it appeared inside the one the test happened to be looking at.

**Why this is distinct from [[352d|shape 352d]]:** 352d is one transform applied identically
across N sibling *output* buckets, where the fixture only reads one bucket back. Here there is
no shared transform to under-test — A and B are built by two *independently evaluated* filter
expressions over the same source list, and the invariant under test ("exactly one place") is a
claim about their combined membership, not about either one's own correctness. A can be
proven perfectly correct in isolation, on its own fixture, while B silently stops excluding
what A claims — the two facts don't disagree with each other from either bucket's own local
point of view; they only disagree when both are read together.

**Guard form that survives:** for any "lives in exactly one bucket" partition built from two
(or more) independently filtered expressions over the same source list, assert the *total*
count of the special row across every bucket the DOM actually renders — not just its presence
inside the one bucket the test is nominally about. Concretely:
`document.querySelectorAll('#sidebar-agents .agent-row[data-agent="done-agent"]').length ===
1`, read across the whole rendered sidebar, not scoped to `.side-finished` alone. A fixture
that only ever queries inside the special cluster cannot see a duplicate sitting in the
ordinary group underneath it.

**Found:** `chela/dashboard/static/js/nav.js`'s `renderSidebarAgents` (CMX-359, PR #485,
judge round 1) — `const rest = rows.filter(a => !wantsHuman(a) && !isDone(a));` (nav.js:385)
partitions the "Finished" cluster's members out of the ordinary per-project grouping below
it. `tests/sidebar.test.mjs`'s
`'CMX-359: \`done\` agents float into their own "Finished" cluster, decoupled from project
groups'` asserted `cluster.querySelectorAll('.agent-row').length === 1` (scoped inside
`.side-finished`) and the done agent's presence inside that cluster, but never counted the
done agent's rows across the whole sidebar. Closed by adding
`document.querySelectorAll('#sidebar-agents .agent-row[data-agent="done-agent"]').length ===
1` to the same test.

**See also:** [[352d|shape 352d]] — the shared-transform, multi-bucket-read version of "look
at more than the one bucket you started with"; this is the two-independent-filters,
combined-membership version of the same lesson.
