## 352d. A single loop fans a join out across several sibling output buckets, but every assertion reads only one of them

**Assertion form:** `/api/dispatcher`'s handler computes three sibling lists per workflow —
`active`, `awaiting`, `recent` — via one call to `_runs_for_workflow`, then joins per-run data
onto all of them in a single pass: `for r in (*active, *awaiting, *recent): r["tasks"] =
tasklists.progress_for_run(...)`. The frontend genuinely needs the join on every bucket —
`kanban.js`'s card renderer draws cards from `active_runs`, `awaiting_review_runs` AND
`recent_runs` off this same payload — but every test added for the join
(`tests/test_tasklists_dispatcher_api.py`) constructs a run that lands in `active` (via
`status="running"` or `status="claimed"`) and reads back only
`data["workflows"][0]["active_runs"][0]["tasks"]`. The join is a single piece of code and a
single loop, which makes "cover the loop" and "cover every bucket it writes into" look like
the same task — they are not, because the loop's *output* fans out across three independently
consumed lists while its *fixtures* only ever populate one of them.

**Mutation that defeats it:** scope the join to the one bucket every fixture happens to use:

```diff
-             r["tasks"] = tasklists.progress_for_run(r, session_entries, current_epoch)
+             r["tasks"] = tasklists.progress_for_run(r, session_entries, current_epoch) if r in active else None
```

Every existing assertion still passes — they all read `active_runs[0]["tasks"]`, and every
fixture run in the file is dispatched with a status that lands in `active`.
`CHELA_REQUIRE_JS_TESTS=1 uv run pytest -q` stayed green (3820 passed, 0 failed, 0 error(s))
with the mutation in place, even though a finished (`done`) or in-review
(`awaiting_review`) run's card would now silently lose its task-progress chip in production.

**Why this isn't shape [[7|shape 7]]:** shape 7 is about a *function* reached through
multiple *call sites*, where a fixture drives one caller and not the other. Here there is
exactly one call site — one loop, one line — and the gap is instead in which *outputs* of
that single call the fixtures observe afterward. The mutation doesn't need to know or care
about call sites; it only needs to know that "which bucket is `r` in" is a distinction the
test suite never makes.

**Guard form that survives:** when a single join/transform is applied identically across N
sibling output buckets built from the same source list, write one fixture *per bucket* — not
one fixture reused N times — that puts a run in that specific bucket (by giving it the
status/shape that bucket's own filter selects for) and reads the joined field back from that
bucket specifically. A test asserting only on the bucket a fixture happens to default into
cannot tell "joined everywhere" apart from "joined only where I looked."

**Found:** CMX-352 rework round 3 (2026-09-09), PR #465. The judge's own throwaway-checkout
mutation battery found the `if r in active else None` guard survived with 3820 tests green —
every one of `test_tasklists_dispatcher_api.py`'s runs used a status that resolves to
`active`. Closed by `test_a_recent_completed_run_also_carries_its_task_progress` (status=
`"done"`, reads `recent_runs`) and `test_an_awaiting_review_run_also_carries_its_task_progress`
(status=`"awaiting_review"`, reads `awaiting_review_runs`).

**See also:** [[7|shape 7]] — the call-site version of "the fixture only reaches the route it
happens to reach"; this shape is its single-call-site, multi-output-bucket sibling.
