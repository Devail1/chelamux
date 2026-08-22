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

**Round 2 — same class of "re-runs on every connection", one turn earlier in the pipeline:**
round 1's fix stopped the backfill from laundering a bad *read*, but left the backfill's own
*trigger* unbounded. `ensure_schema()`'s migration loop applies every `(column, ddl)` pair on
every open and swallows `OperationalError` for a column that already exists; nothing recorded
which columns THIS call actually added versus which already existed, so the backfill had no
way to gate on "the tick that creates `adopted`" — it could only run unconditionally, forever.

**Mutation that defeats it:** once that trigger is tightened to a set of `added` columns
this call actually ALTERed in, the natural next mistake is gating on "some column was added
this tick" instead of "*this* column was added this tick":

```diff
-    if "adopted" in added:
-        conn.execute("UPDATE runs SET adopted=1 WHERE task_id LIKE 'adopt-%'")
+    if added:
+        conn.execute("UPDATE runs SET adopted=1 WHERE task_id LIKE 'adopt-%'")
```

(the query itself drops the `adopted=0 AND` clause that round 1 carried — once gated on
`"adopted" in added`, the branch only ever runs on the instant the column's own `ALTER TABLE
... ADD COLUMN` succeeds, at which point every existing row reads the column's fresh DEFAULT
of `0` anyway; nothing has had the chance to write `1` yet.)

On a mature DB — `adopted` added long ago, its one legitimate backfill tick long past — this
still passes every existing test, because every existing test either never adds an unrelated
column on a later open, or never plants a stray `adopted=0`/`adopt-`-prefixed row for the
backfill to wrongly "repair". The moment some future, wholly unrelated migration lands (a new
column with its own `ALTER TABLE`), `added` goes non-empty on that tick too — re-arming the
task_id-prefix backfill on a DB where it was supposed to have fired exactly once, and silently
overwriting any row that is merely `adopted=0` with an `adopt-` task_id, legacy or not.

**Guard form that survives:** a DB built from a genuine pre-`adopted` schema (via
`ALTER TABLE runs DROP COLUMN adopted`, not a hand-set `adopted=0` on an already-migrated
one) proves the backfill fires on `adopted`'s own creation tick; a second DB where `adopted`
already exists but a *different*, unrelated column gets dropped and re-added proves the
backfill does NOT fire again on that later, unrelated tick — a stray `adopted=0`/`adopt-*`
row planted between the two opens must still read `0` afterward:

```python
with sqlite3.connect(str(dispatcher.DB_PATH)) as raw:
    raw.execute("ALTER TABLE runs DROP COLUMN ci_infra_streak")  # unrelated later column
    raw.execute("INSERT INTO runs (task_id, ..., adopted) VALUES ('adopt-9999', ..., 0)")
with dispatcher._db() as conn:  # re-adds ci_infra_streak; `adopted` untouched
    row = conn.execute("SELECT adopted FROM runs WHERE task_id='adopt-9999'").fetchone()
assert row["adopted"] == 0
```

Same root cause as round 1, one layer earlier: a repair that is supposed to run exactly once
needs its OWN "have I already run" bookkeeping, keyed to the one tick that is actually true —
not a nearby proxy ("some `_db()` open", "some column got added") that is true far more often
than the tick it stands in for.

Found: PR #400 (CMX-321), round 2. The mutation above, applied by the judge to a throwaway
checkout of the PR's head, stayed green through the full suite; closed by tracking `added`
columns per `ensure_schema()` call and gating the backfill on `"adopted" in added` rather than
on `added` alone, confirmed to go red under the same mutation before the fix was pushed.
