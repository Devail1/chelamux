## 366. A guard asserts "it never shelled out" as a stand-in for "it never made the privileged write" — an in-process write needs no subprocess at all

**Assertion form:** a function's docstring/comment states the invariant in the STRONG, actually-
load-bearing form — "touches NOTHING privileged", "no other privileged write path" — because the
whole point of the change is removing a privilege (here: an agent's own process being able to
`UPDATE` the shared `runs` table). The test that is supposed to guard this picks the ONE mechanism
the old, privileged code used to reach that write (`subprocess.run`, for a `tmux`-mediated path
that also happened to precede a DB write in the old code) and asserts *that mechanism* was never
invoked: `run.assert_not_called()`. The docstring's claim and the test's assertion read as the same
sentence until you ask what a corruption would actually have to do to defeat it.

**Mutation that defeats it:** add a DB write that needs the mechanism the test watches not at all —
`conn.execute("UPDATE runs SET status='failed' WHERE task_id<>?", (task_id,))` inside the same
`with _db() as conn:` block the function already opens to do its (legitimate) read. No
`subprocess.run` fires — the mutated function still calls it zero times, exactly like the correct
version — so `run.assert_not_called()` stays green. The corrupted row (some OTHER run's row, not
even the one the call was for) is never read back by the test either, because the test's only
row-level assertion was `dispatcher.resolve_run(task_id)["status"] == "running"` — the row for the
task_id the call was FOR, which the mutation deliberately leaves untouched (`task_id<>?`) so that
check also stays green.

**Why "no subprocess.run" reads as proof of "no privileged write" and isn't:** in the code being
replaced, the two were coupled by history, not by necessity — the old privileged path happened to
route its DB write and its window-kill through the same privileged process, and the window-kill
half is genuinely `subprocess`-shaped (`tmux kill-window`), so a spy on `subprocess.run` was a
correct guard for THAT half. Carrying the same spy over to cover "and also no DB write" silently
assumes the only way to write the DB is by shelling out to something. It isn't — `sqlite3` is an
in-process library call. A guard that watches the door doesn't notice a window.

**Guard form that survives:** for a claim of the form "this call must not perform a privileged
write", assert the STATE the write would have changed, not a proxy for how a write might have been
delivered — snapshot the actual table (every row, not just the row the call under test is FOR) both
before and after the call, and assert they are identical. Include a SECOND, unrelated row in the
fixture: "this row is untouched" is not "no row is touched" until at least one other row is present
to prove the write couldn't have landed somewhere else instead. Keep the `subprocess.run` spy too —
it is still the right guard for the window-kill half of the same docstring's claim — but it is not a
substitute for the DB-state assertion the "no privileged write" half actually needs.

**Found:** `chela judge`, PR #511 (issue #502 rework round 1). `chela/dispatcher.py`'s
`request_task_finished` reads a run's row via `with _db() as conn: ... conn.execute("SELECT ...")`
with no `UPDATE` anywhere in the real function; its docstring states "touches NOTHING privileged".
`tests/test_dispatcher_task_finished_request.py::test_request_task_finished_writes_a_marker_and_
never_shells_out` asserted `run.assert_not_called()` (a `subprocess.run` spy) plus
`dispatcher.resolve_run("t1")["status"] == "running"` (this row only). The judge mutated the
function to `conn.execute("UPDATE runs SET status='failed' WHERE task_id<>?", (task_id,))` right
before the `SELECT` — `CHELA_REQUIRE_JS_TESTS=1 uv run pytest -q` stayed green (3996 passed) with
the mutation applied, because the mutation calls no subprocess and never touches `t1`'s own row.
Closed by replacing both assertions with a full-table snapshot (`SELECT * FROM runs ORDER BY
task_id`) taken before and after the call, over a fixture seeding a SECOND row (`t2`) alongside the
one being finished, asserting the two snapshots are equal
(`test_request_task_finished_writes_a_marker_and_touches_no_run_row`).
