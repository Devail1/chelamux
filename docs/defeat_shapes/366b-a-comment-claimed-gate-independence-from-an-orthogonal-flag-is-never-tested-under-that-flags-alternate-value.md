## 366b. A comment claims a gate is independent of an orthogonal flag, but no test exercises the gate with that flag at its alternate value

**Assertion form:** a condition that gates a strong completion signal (a marker the agent itself
wrote, proving its own work is done) is documented, in a comment directly above it, as
deliberately NOT depending on a separate flag that gates a weaker, unrelated signal elsewhere in
the same function ("checked ahead of the tracker-based reconciliation below: the agent's own
marker is direct completion evidence and must not wait on `open_ids`/`tracker_read_failed` — those
gate 'removed from the tracker', a weaker signal"). Every existing test that exercises the strong
gate leaves the orthogonal flag at its default (`False`), because nothing about the feature being
tested requires setting it any other way.

**Mutation that defeats it:** AND the orthogonal flag into the strong gate's condition anyway —
`row["status"] in ACTIVE_STATUSES` → `not tracker_read_failed and row["status"] in
ACTIVE_STATUSES`. Every existing test for the strong gate still passes, because every one of them
runs with `tracker_read_failed` at its default `False` value — the new clause is true in every
fixture that exists, so it changes nothing observable to the suite. Only a tick where the tracker
read has ALSO failed would ever notice the marker-based completion silently stopped applying, and
no test constructs that combination.

**Why the comment doesn't catch it:** a comment stating "must not wait on X" documents *intent*,
not a *test*. It reads as settled the moment the surrounding code visibly doesn't mention `X` in
its condition — but nothing stops a future edit (or, here, a deliberate corruption) from adding `X`
back in without touching the comment at all; the comment and the code silently diverge and the
suite has no way to notice, because no fixture was ever built specifically to make that divergence
observable.

**Guard form that survives:** whenever a comment states a gate is independent of a second,
orthogonal condition that also happens to be in scope in the same function, write a test that sets
the orthogonal condition to the value that would matter if it silently leaked into the gate — here,
a tick with BOTH a pending completion marker AND `tracker_read_failed = True` — and assert the
guarded outcome (the marker still applies) still holds. A comment claiming independence between two
conditions is exactly the shape that needs a fixture combining them at their "supposedly doesn't
matter" values, the same way [[66|shape 66]] needs a negative control on each axis of a two-axis
predicate — the difference here is the second axis is claimed to be a NON-axis at all, which is
precisely what makes it easy to leave unconstructed.

**Found:** `chela judge`, PR #511 (issue #502 rework round 1). `chela/dispatcher.py`'s `tick()`
gates the task-finished-marker branch on `row["status"] in ACTIVE_STATUSES and row["worktree_path"]
and _task_finished_request_path(...).exists()`, with a comment stating this must not also wait on
`tracker_read_failed`. No test in `tests/test_dispatcher_task_finished_request.py` ever set
`source.read_failed = True` while a marker was pending. The judge mutated the condition to prepend
`not tracker_read_failed and` — `CHELA_REQUIRE_JS_TESTS=1 uv run pytest -q` stayed green (3996
passed) with the mutation applied. Closed by
`test_tick_applies_a_pending_completion_request_even_when_the_tracker_read_failed`, which sets
`source.read_failed = True` on the fixture's tracker source and asserts the marker still applies
(`summary["task_finished_applied"] == 1`) despite `summary["tracker_read_failed"] is True`.
