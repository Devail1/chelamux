## 64. A comment's conjunctive list of prohibited affordances is guarded for its first clause only — a round that closes clause 1 reads as "the invariant is now guarded," leaving clause 2 (built from a variable already sitting in scope) completely unguarded

**Assertion form:** a production comment states a negative invariant as an *AND* of two or
more clauses — "never rendered as one: no Promote button ... **and** no delete button." A
round of guard-writing closes the FIRST clause with a real assertion
(`parkedCard.querySelector('.kanban-promote-btn')` is `null`) and the reviewer — human or
judge — reads the comment as now satisfied, because a comment that used to have zero
assertions now has one. The second clause names an *independently reachable* value: `delBtn`
is computed unconditionally at the top of `_kCard` for every card regardless of status, so
interpolating it into the parked branch's template (the same way the backlog and run-backed
branches already do) is a one-token change with no failing test anywhere — nothing in
`tests/` ever mentioned `.kanban-delete-btn` for a parked card. The same shape recurs twice
more in the same PR round on values whose "it's already computed / already flowing" status
made them feel implicitly covered: `raw: t.raw` is stated to survive a round-trip and gets
pinned at the Python parse hop and the `/api/dispatcher` serialization hop
([[63|shape 63]] instance 3) but not the THIRD hop — `_kanbanFlatten`'s own client-side copy
onto the card object, the one hop between the payload and what the task-detail modal actually
reads; and `escHtml()` is reached for on the card TITLE's `knInline` path (guarded by
`tests/kanban_flatten.test.mjs` test 4) but the new `card.reason` interpolation — a second,
independently-added call site of the exact same "repo-authored text landing in innerHTML"
contract — has no escaping assertion at all.

**Mutation that defeats it:** (1) interpolate `${delBtn}` into the parked-card template
(2) revert `raw: t.raw` to `raw: null` in `_kanbanFlatten`'s parked bucket (3) drop
`escHtml(...)` from the reason interpolation, leaving `${card.reason}` bare. All three parse,
and `CHELA_REQUIRE_JS_TESTS=1 uv run pytest -q` stays green (3169 passed) applied in
isolation — `chela judge` found all three live on CMX-298 rework round 3, PR #372, the round
immediately after round 2 closed the *sibling* clause/hop/call-site of each of the same three
constructs.

**Why "the comment now has an assertion" reads as "the comment is satisfied":** a comment
naming two prohibitions in one sentence describes one invariant in prose but is, in code,
two entirely independent facts — one about a template NOT interpolating a variable, one about
a different template NOT interpolating a different (or the same, at a different hop) variable.
Closing the first with a real test makes the diff *look* like "the parked-card guards" as a
category are now handled — the same misreading [[63|shape 63]] names for branches and hops
applies equally to the clauses of a single conjunctive sentence, and to independently-added
call sites of a shared escaping helper ([[60|shape 60]]'s pattern, recurring here on a
brand-new call site rather than a pre-existing sibling one). A value that is *already
computed* or *already flowing through* an adjacent hop is, if anything, MORE likely to be
assumed covered than one that visibly needs new plumbing — there is no visible diff noise at
the second clause to draw a reviewer's eye.

**Guard form that survives:** when a comment states a prohibition (or a contract like
"escaped before rendering") as a list — of affordances, of hops, of call sites — enumerate
every item in that list explicitly and write one assertion per item, even when the code for
item 2 is "just" reusing a variable or a helper that item 1 already proved works elsewhere.
Reusing a value is not the same as guarding its use at THIS interpolation site; a mutation
edits one template literal, not the value's computation.

**Found:** CMX-298 rework round 3 (2026-08-16), PR #372 — closed by asserting
`.kanban-delete-btn` is absent from a parked card (`tests/kanban_flatten.test.mjs`, extending
the existing Promote-button test), asserting a parked card's `raw` reaches the task-detail
modal's `.task-modal-brief` pane through a full render+click (`tests/kanban_task_modal_wiring
.test.mjs`, a new parked-card click test mirroring the existing backlog one), and asserting a
parked card's `reason` renders no live `<b>`/`<img>` element and its `innerHTML` carries
`&lt;`/`&gt;` entities (`tests/kanban_flatten.test.mjs`, a new test).
