## 321. An idempotent backfill migration re-runs on every connection and launders a broken write back to correct

**Assertion form:** CMX-321 made `adopt_pr` write `adopted=1` as a recorded FACT on the row
it inserts (rather than a proxy inferred later from `worktree_path`), and paired it with a
one-time schema backfill for rows written by an OLDER chela, before the column existed:
`UPDATE runs SET adopted=1 WHERE adopted=0 AND task_id LIKE 'adopt-%'`, run inside
`ensure_schema()`. The test proved the write by calling `adopt_pr(...)` and then reading the
row back through the ordinary read path — `dispatcher.resolve_run("adopt-1")` — and asserting
`run["adopted"] == 1`.

**Mutation that defeats it:** flip the literal `adopt_pr` inserts, from `1` to `0`:

```diff
-               started_at, attempt, pr_url, pr_state, pr_head_sha, brief, adopted)
-               VALUES (?, ?, ?, 'awaiting_review', ?, ?, 1, ?, 'open', ?, ?, 1)""",
+               started_at, attempt, pr_url, pr_state, pr_head_sha, brief, adopted)
+               VALUES (?, ?, ?, 'awaiting_review', ?, ?, 1, ?, 'open', ?, ?, 0)""",
```

`ensure_schema()` — and with it the backfill `UPDATE` — runs unconditionally on *every*
`_db()` open, not once per process or once per database file: it has no "have I already
backfilled" bookkeeping, only the query's own `WHERE adopted=0 AND task_id LIKE 'adopt-%'`
filter. That filter cannot tell a row from before the column existed apart from a row
`adopt_pr` just inserted wrong — both are `adopted=0` with an `adopt-<n>` task_id. So the
very next `_db()` open after the mutated insert — the one inside `resolve_run`'s
`list_runs()` call, which the test used to read the row back — re-runs the backfill and
silently repairs the mutated `0` to `1` before the assertion ever saw it. The suite stayed
green (`CHELA_REQUIRE_JS_TESTS=1 uv run pytest -q`, 3326 passed, 0 failed, 0 error(s)) with
the insert permanently broken.

**Guard form that survives:** read the row back with a connection that does NOT go through
`ensure_schema()` — a raw `sqlite3.connect(str(dispatcher.DB_PATH))` — so the backfill never
gets a chance to run between the write under test and the assertion:

```python
raw = sqlite3.connect(str(dispatcher.DB_PATH))
raw.row_factory = sqlite3.Row
row = raw.execute("SELECT adopted FROM runs WHERE task_id='adopt-1'").fetchone()
raw.close()
assert row["adopted"] == 1
```

This is the same shape `05-asserting-a-source-constant-instead-of-the-rendered-value.md`
names for a different mechanism: the read path there was a stand-in (a source constant, a
handler called directly) instead of the rendered artifact; here the read path is a
self-healing one, so any assertion made through it necessarily observes "the value after
repair," never "the value the code under test actually wrote." A repair migration that is
*supposed* to be idempotent and safe to re-run is, by that same property, also unable to
distinguish "a row from before this migration" from "a row a bug just produced that happens
to look identical" — and every read through the normal path pays that migration's repair
before a test ever gets to look.

**Found:** PR #400 (CMX-321), round 1. The mutation above, applied by the judge to a
throwaway checkout of the PR's head, stayed green through
`tests/test_dispatcher_adopt.py::test_adopt_records_the_origin_as_a_FACT_on_the_row`; closed
by reading the row with a raw `sqlite3` connection instead of `resolve_run`, confirmed to go
red under the same mutation before the fix was pushed.
