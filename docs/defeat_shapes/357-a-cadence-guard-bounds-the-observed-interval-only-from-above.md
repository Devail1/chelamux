## 357. A cadence guard bounds the observed interval only from above

**Assertion form:** a guard exists specifically so that raising a poll/retry interval to
something too coarse (`sleep 30/60`) goes red — `time.sleep` is intercepted, each requested
duration is captured into a list, and the test asserts `all(d <= CEILING for d in sleeps)`.
The docstring explains, correctly, why this observes the *actual* interval the code sleeps
for rather than pinning the module constant as a literal — so a `def f(interval=POLL_INTERVAL)`
default-argument freeze (which would leave `wait.POLL_INTERVAL` reading correctly but never
actually reach the sleep call) can't fool a literal-equality check. What the ceiling-only
bound misses is the opposite direction: nothing stops the interval from being *too small*.

**Mutation that defeats it:** replace the sleep with a busy-spin that ignores the constant
entirely — `time.sleep(POLL_INTERVAL)` → `time.sleep(0)`. Every recorded duration is now
`0`, which trivially satisfies `d <= CEILING`. The guard was written to catch "reads a value
that's too large," and a corruption that instead "never reads the value at all, sleeps near
zero" is a different failure of the same code path but slips through the same assertion
because the assertion only ever looks at one tail of the distribution.

**A second, related gap in a paired "accept" test:** the same PR carried a second test whose
whole point was to prove the interval is actually *live* — it monkeypatches the module
constant to an unusual value (`wait.POLL_INTERVAL = 1.7`, deliberately not the shipped
default) and asserts the wait still resolves correctly. But "still resolves correctly" is
true of a fully broken read too: `_wait_wid` reacts to the delivered event on its own
regardless of how long `_poll` slept between checks, so a `_poll` that froze the constant at
import time (never observing the monkeypatched 1.7) still produces the same `ok`/`state`/
`event` result. The test that was supposed to prove "the live value took effect" recorded no
evidence of what value was actually used.

**Guard form that survives:** bound the interval on both sides against the *live* module
constant, not a re-typed literal — `wait.POLL_INTERVAL <= d <= CEILING` for every recorded
duration — so a busy-spin (`d` near zero) fails the floor the same way a too-coarse interval
fails the ceiling. Separately, the paired "different interval, still correct" test needs to
capture `time.sleep`'s requested durations too (the same recorder the cadence test already
uses) and assert the monkeypatched value actually appears among them (`1.7 in sleeps`) —
proving the constant was *read*, not just that the surrounding behavior happened to still
work whether it was read or not.

**Found:** CMX-357 rework round 1 (2026-09-10), PR #481. The judge's required-mutation-set
applied both `time.sleep(POLL_INTERVAL) → time.sleep(0)` and the `_poll(..., POLL_INTERVAL:
float = POLL_INTERVAL)` default-argument freeze to `chela/wait.py`; the suite stayed green
under both. Closed in `tests/test_wait.py` by adding a floor assertion (`d >=
wait.POLL_INTERVAL`) to `test_wait_wid_done_polls_at_a_bounded_cadence`, and by recording
`sleeps` and asserting `1.7 in sleeps` in
`test_wait_wid_done_still_resolves_correctly_at_a_slower_poll_interval`.
