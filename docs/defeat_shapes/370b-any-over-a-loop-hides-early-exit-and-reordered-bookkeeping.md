## 370b. `any(...)` over a loop's side effects only proves one iteration ran — and
bookkeeping recorded before its operation is confirmed lies when that operation races

**Assertion form:** `test_a_duplicate_column_race_is_swallowed_not_escalated` modelled
`ensure_schema`'s benign duplicate-column race by faking an empty `PRAGMA table_info`
result, so every migration column looks missing and every one of their `ALTER TABLE`s
races and gets swallowed. The guard asserted `any("ALTER TABLE" in s for s in
racing.statements)` — one ALTER anywhere in the recorded statements — and never looked at
`added` (the set gating the CMX-321 backfill) at all.

**Mutation that defeats it:** two independent mutations to `chela/dispatcher.py`'s
`ensure_schema` loop each survived a full green suite:

- the swallow's `continue` (skip only the raced column, keep migrating the rest) changed to
  `break` (abandon every column after the first race) — this is bug #515's silently-skipped-
  migration shape recurring one column later, this time under a race instead of a missing
  pre-check. `any("ALTER TABLE" in s ...)` is satisfied by the SINGLE `ALTER TABLE runs ADD
  COLUMN pr_url` issued before the loop breaks, so a suite that stops migrating after the
  first column of ~30 looks identical to one that migrates all of them.
- `added.add(_column)` moved to BEFORE `conn.execute(ddl)` instead of after — recording
  "this call added the column" unconditionally, rather than only once the ALTER actually
  succeeded. Under the race, every `execute(ddl)` raises `duplicate column name` and gets
  swallowed, but the reordered `add()` already ran first, so `added` ends up containing every
  raced column anyway — including `adopted`, which wrongly re-fires the CMX-321 backfill
  (`UPDATE runs SET adopted=1 WHERE task_id LIKE 'adopt-%'`) on a connection that did not add
  that column at all. Nothing in the test read `added` or its downstream UPDATE, so the
  reorder was invisible.

**Guard form that survives:** for a loop that must keep going past a per-item recoverable
failure, assert on an item from the FAR end of the sequence, not just "an effect happened at
least once" — here, that the LAST column's `ALTER TABLE` (`blocked_race_ack_sha`) appears in
the recorded statements, which is only possible if every earlier race was skipped rather than
aborting the loop. For bookkeeping gated on an operation's success, assert the DOWNSTREAM
consequence of that bookkeeping is absent when the operation is known to have raced — here,
that no `UPDATE runs SET adopted=1` statement appears when every column (including `adopted`)
was raced, not genuinely added, by this call.

**Found:** PR #519 (CMX-370), round 2 — the judge, applying both mutations above to a
throwaway checkout independently, got `4067 passed, 0 failed, 0 error(s)` both times, because
the race test's two assertions (`any(ALTER present)`, `no raise`) were satisfied by the first
column alone and never inspected `added` or its backfill.
