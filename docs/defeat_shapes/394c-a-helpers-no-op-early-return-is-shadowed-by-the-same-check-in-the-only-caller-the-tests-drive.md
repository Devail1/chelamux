## 394c. A helper's no-op early return is shadowed by the same check in the only caller the tests drive

**Assertion form:** a helper builds a notice and returns `None` when there is nothing to
announce (`_moved_event(old, new, ...)`: `if not old or old == new: return None`). One caller
(`_taken_over`, behind `register`/`watch`) repeats `if not old or old == new: return None`
before it ever calls the helper, and that caller is where every "the same window is not a move"
test goes. The tests are honest and would fail if the caller's check broke. They never reach the
helper's own check, because the caller already returned.

**Mutation that defeats it:** drop the `old == new` half from the helper. The callers that do
NOT pre-check (`readdress` for `chela restore --apply`, and the self-heal in `tick`) now announce
"the orchestrator pin moved @8 to @8" whenever a tmux restart reissues the same `@N` to the same
session. The suite stayed green: 4378 passed, 0 failed.

**Guard form that survives:** find the check's duplicates (`git grep` the condition). For every
caller that relies on the helper's copy rather than its own, drive the equal-input case through
that caller: here, `readdress("@8", "1-1", "@8")` under a fresh epoch. Assert that the operation
itself happened (the epoch was re-stamped, so the test is not green only because nothing ran),
and that neither the queue nor the event log holds a move. The control is the existing
restore-to-a-different-window test, which is announced.

**Related:** shape 07 (two callers, one guarded) is the general form. What sets this one apart is
that the guarded caller holds a *copy* of the check under test, so a reviewer who greps the
condition finds it covered and stops looking.

**Found:** CMX-394 (PR #546, judge round 4). The fix is
`test_a_restore_that_re_stamps_the_SAME_window_is_not_announced` in `tests/test_orchestrator_pin.py`.
