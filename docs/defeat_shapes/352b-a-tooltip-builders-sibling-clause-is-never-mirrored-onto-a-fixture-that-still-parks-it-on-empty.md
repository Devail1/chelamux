## 352b. A tooltip-builder's sibling clause is never mirrored onto a fixture that still parks it on empty

**Assertion form:** one function builds a multi-line tooltip out of several independent
clauses over the same data object — `_taskProgressChip` in
`chela/dashboard/static/js/dispatcher.js` appends one line for `tasks.in_progress` (a single
optional field) and then a second, structurally parallel block for `tasks.blocked` (a loop
over a list, each iteration choosing between two ternary arms depending on whether
`blocked_by` is present). Round 1 of this PR's rework (CMX-352, PR #465) closed the guard gap
named in shape [[352|352]] by adding `tests/task_progress_chip.test.mjs`'s
`the chip's tooltip names the CURRENT in-progress task's subject` — a real DOM assertion that
reads the rendered `title` attribute back. But the single shared fixture (`TASKS`) that test
and every other test in the file reuses set `blocked: []` — the *empty* default — so the
`for (const b of (tasks.blocked || []))` loop, and both arms of its `by ? ... : ...` ternary,
render nothing in every test that exists. The in-progress line got a real positive control;
its sibling clause, one loop-body away in the same function, never did. A reviewer who sees
the neighbouring line covered reasonably assumes the tooltip's coverage is uniform — the two
clauses read as "the same kind of guard, twice," exactly the illusion shape [[311|311]]
describes for sibling *classes*, here reproduced one level down for sibling *clauses inside
one function*, stacked on shape [[02|2]] (the fixture never crosses the threshold — here,
never leaves the loop's empty-array default) because that is the specific mechanism by which
the second clause stayed dark.

**Mutation that defeats it:** replace the loop's source with an unconditionally empty one —
`for (const b of (tasks.blocked || []))` -> `for (const b of [])`. The loop body (both the
`by`-present and `by`-absent ternary arms, and the `lines.push` call itself) becomes dead
code. `node --check` still parses the file; `CHELA_REQUIRE_JS_TESTS=1 uv run pytest -q` still
reports 3797 passed, 0 failed, because no fixture in the suite ever supplies a non-empty
`tasks.blocked` for the tooltip test to read back — the shared `TASKS` fixture's `blocked: []`
never gives the loop anything to iterate, mutated or not.

**Guard form that survives:** when a render function assembles its output (here, a tooltip's
`title`) out of N independent clauses over sibling fields of one data object, a positive
control on clause 1 does not transfer to clause 2 — each clause needs its own fixture value
that actually reaches its body, the same way [[311|shape 311]] requires each sibling
*implementation* to carry its own copy of a control rather than inheriting a neighbour's.
For a clause that loops over a list, "reaches its body" means a fixture with at least one
element, not merely a present-but-empty list (the loop-specific form of [[02|shape 2]]'s
threshold-crossing rule) — and if the loop branches on a sub-field's presence (here,
`blocked_by` empty vs. non-empty), the fixture needs one element on each side of that branch
too, or the branch not taken is exactly as dark as the whole clause was before. Closed here
by giving the shared `TASKS` fixture two `blocked` entries — one with `blocked_by: ['t-1',
't-2']`, one with `blocked_by: []` — and adding
`the chip's tooltip also names each BLOCKED task, with its blocked-by ids when present`,
which asserts both rendered lines (`"ship the release notes" blocked by t-1, t-2` and
`"audit the migration" blocked` with no dangling `by`) against the real `title` attribute.

**Found:** CMX-352 rework round 2, PR #465. The judge's required-mutation-set verdict named
this exact mutation verbatim (`for (const b of (tasks.blocked || []))` ->
`for (const b of [])`), having applied it to a throwaway checkout of round 1's head and found
the suite still green.

**See also:** [[352|shape 352]] — the original guard-gap this feature shipped with, of which
this is the one sibling clause round 1's fix did not mirror. [[311|shape 311]] — the same
"positive control on one implementation doesn't transfer to its structural sibling" illusion,
one level up (across classes, not within one function's own clauses). [[02|shape 2]] — the
underlying mechanism (fixture parked on a default that never crosses into the guarded
region), here the loop's own empty-array default rather than a scalar threshold.
