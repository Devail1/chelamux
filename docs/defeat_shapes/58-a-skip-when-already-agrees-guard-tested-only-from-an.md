## 58. A "skip when the pin already AGREES" guard is tested only from an empty store, so a mutation that widens it to "skip when a pin merely EXISTS" survives

**Assertion form:** a best-effort promotion helper reads the current durable value before
writing, and skips the write when it already matches the new one — `if current_value ==
new_value: return` — documented as "skips the write when the pin already agrees," so a
caller that re-resolves the same identity on every tick doesn't turn into a steady stream of
file writes for a value that never changes. The test suite for this has an empty-store test
(first resolution: nothing pinned yet, the write happens) and a same-value test (the store
already holds exactly the value being written again, and the write is skipped). Both pass.

**Mutation that defeats it:** narrow the equality check to a presence check —
`if current_value == new_value: return` → `if current_value is not None: return`. Every
existing test still passes: the empty-store test still sees a write (nothing was there to be
"not None"), and the same-value test still sees a skip (the existing value is trivially "not
None" too, so the mutated condition returns exactly when the original one did — for that one
input). Nothing in the suite ever puts a *different*, already-pinned value in the store before
resolving, so no test can tell "skip because it agrees" apart from "skip because something,
anything, is already there."

**Why this slips through even with a same-value test in place:** a same-value regression test
proves the guard doesn't skip *less* than it should (doesn't always write). It does not prove
the guard doesn't skip *more* than it should (never overwrites a stale value), because
"agrees" and "exists" are the same predicate on every fixture that only ever seeds the store
with the value under test. The two conditions only diverge once a test seeds the store with a
*different* prior value and then resolves to a new one — exactly the case a "recycled address,
new identity" caller (a window id reused by a restarted server, a socket reused after
reconnect, any address space with reuse) depends on the write actually happening for.

**Guard form that survives:** add a fixture that pre-seeds the store with a value *different*
from the one about to be resolved, run the resolution, and assert the store now holds the
*new* value, not the stale one. This is a distinct case from both existing tests — not a
duplicate of the empty-store test (there IS a pin to begin with) and not a duplicate of the
same-value test (the pin disagrees) — so it is the only fixture where "agrees" and "exists"
produce different answers and a presence-only mutation goes red.

**Found:** `chela/sessions.py`'s `_promote` (CMX-296, PR #369, round 2) — `if
sessionids.session_id_for(wid) == session_id: return` mutated to `if
sessionids.session_id_for(wid) is not None: return` stayed green against `CHELA_REQUIRE_JS_TESTS=1
uv run pytest -q` (3141 passed) because every existing promotion test either started from an
empty `sessionids` store or re-pinned the identical session id. Closed by
`test_a_promotion_updates_a_pin_that_names_a_different_session`, which pins `@1` to a
different, stale session id before resolving it to a new one via the event log, and asserts
the durable pin is updated to the new id rather than left on the stale one — the "a window
restarted in place must not inherit a dead agent's session" case CMX-296's own module
docstring already refuses everywhere else in `chela/sessions.py`.
