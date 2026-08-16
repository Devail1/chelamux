## 63. Fixing one branch/hop of an invariant leaves its siblings — claimed by the same comment — unguarded

**Assertion form:** an invariant is stated once, in a comment or docstring, as holding across
*several* equivalent branches or hops (a conditional's two arms; a value's parse layer and its
API-serialization layer). A guard gets written for exactly one of them — often because that
one was the instance a previous review round already found and fixed — and the sibling(s) are
left resting on the same fixture or an assertion of an adjacent property, never the invariant
itself. Three independent instances landed in the same PR, all missed by a round-1 fix that
closed the *other* half of each pair:

1. **Sibling ternary arm.** `kanban.js`'s `_kCard` renders a parked card's reason chip as
   `card.reason ? '...🔒 ${reason}...' : '...🔒 parked...'` — the 🔒 cue is stated to appear on
   *both* arms. Round 1 (`docs/defeat_shapes/62`) pinned the reason-LESS fallback arm's own
   textContent. The reason-PRESENT arm — the one a real parked card with a `<!-- blocked: ...
   -->` reason actually takes — still only had its reason *text* asserted (`tests/kanban_
   flatten.test.mjs` test 5), never the 🔒 glyph on that branch. Dropping `'🔒 '` from that arm
   alone left every existing assertion green.
2. **A header-comment claim with zero assertion anywhere.** `_kanbanFlatten`'s own comment
   states `workflow_path is injected onto open_tasks + backlog_items + parked_tasks` — but no
   test read `card.workflow_path` (or the rendered `.kanban-wf-chip`) off a parked card at all.
   The claim was never guarded on ANY branch, not even the one instance a fixture already
   mounted.
3. **Parse hop pinned, API hop not.** `tests/test_markdown_parked.py::test_line_number_and_
   raw_are_preserved` pins `line_number`/`raw` at the parser (`MarkdownSource.parked_tasks_
   from_text`). `app.py`'s `/api/dispatcher` re-serializes those same fields into
   `entry["parked_tasks"]` for the task-detail modal — a second hop the value has to survive —
   but `tests/test_taskmodal_data.py`, the module that exists to pin that exact payload, only
   asserted `title` + `reason`.

**Mutation that defeats it:** (1) drop `'🔒 '` from the reason-present template literal only;
(2) set `workflow_path: null` instead of `wf.path` when building a parked bucket entry; (3)
hardcode `"line_number": 0` in app.py's parked-task serialization. All three still parse, all
three leave `CHELA_REQUIRE_JS_TESTS=1 uv run pytest -q` green (3169 passed) applied in
isolation — `chela judge` found all three live on CMX-298 rework round 2, PR #372, the round
immediately after round 1 closed the *other* branch/hop of the same three constructs.

**Why "we already fixed this invariant" isn't the same as "we fixed it everywhere it's
claimed":** a reviewer (human or judge) who watches round 1 close the reason-less fallback,
the bucket order, sees "the parked-card guards" as done — the comment that named the
invariant reads as satisfied because *a* branch of it now has a real assertion. But a
conditional's arms, and a value's hops through a pipeline, are independent code paths; a test
that reaches one proves nothing about the sibling unless it separately reads the sibling's own
output. The fix that closes instance N of a pattern is a strong signal to go hunting for
instance N+1..k of the *same* pattern in the *same* PR, not a signal the pattern is closed.

**Guard form that survives:** when a comment or docstring states an invariant holds across
enumerable branches/hops (a ternary's two arms, a value's parse-then-serialize pipeline,
"injected onto A + B + C"), write one assertion PER named branch/hop, reading back that
branch's actual rendered output or that hop's actual payload field — not an assertion that
happens to pass for every branch because it checks something branch-invariant instead.

**Found:** CMX-298 rework round 2 (2026-08-16), PR #372 — closed by extending
`tests/kanban_flatten.test.mjs` test 5 to assert the 🔒 cue and the `.kanban-wf-chip` text on
the reason-present parked-card branch, and extending
`tests/test_taskmodal_data.py::test_parked_tasks_surface_a_blocked_todo_bullet` to assert
`line_number`/`raw` on the `/api/dispatcher` payload, not just at the parser.
