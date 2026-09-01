## 328. A boundary widened for a latched sentinel's sake, and the loop variable that carries the sentinel across calls, both go unexercised

**Assertion form:** a function introduces a negative sentinel (`UNKNOWN_BEHIND = -1`) to
distinguish "already notified about an unknowable state" from a real, never-negative count,
and threads it through a caller-held accumulator (`previously_behind`) that the daemon loop
re-passes on every tick. Landing the sentinel required widening an existing edge condition
from an exact match to an inequality — `previously_behind == 0` (the pre-sentinel invariant:
"only notify once, coming from a state that was never behind") became `previously_behind <=
0` (needed so a checkout latched at -1 can still notify once it later becomes genuinely
behind) — and the caller that stores the sentinel needed its assignment kept intact
(`update_behind_seen = update.check_and_notify(update_behind_seen)`) so the value actually
persists tick-to-tick instead of being recomputed from a stale seed. Every existing test of
the seam enters `previously_behind` already at its narrowed value (0), and every existing
test of the call-site drives only ONE tick — so neither the widened half of the boundary nor
the loop variable's round-trip is ever the thing under test, even though both are separately
mutable and separately load-bearing.

**Mutation that defeats it:** two independent one-line reverts, both green under the full
suite before this fix: (1) narrow `previously_behind <= 0` back to `== 0` — every "unknown,
then behind" sequence a human might reasonably picture (no upstream configured yet → gains
one → falls behind) then goes permanently silent, since `-1` never equals `0`; nothing tests
entering the real-update edge FROM the sentinel, only from the ordinary post-notify `0`. (2)
drop the call-site's assignment, calling the seam as a bare statement — the daemon now
re-derives `previously_behind` from whatever the loop's own initial value was on every tick
instead of what the seam last returned, so the "notify once" contract dies in production
while every unit test of the seam itself (which supplies its own `previously_behind` by hand,
never through the loop) stays green.

**Why one shape covers two mutations:** they are the same missing test from two altitudes.
The seam-level test suite validates `check_and_notify`'s *logic* by calling it directly with
hand-picked `previously_behind` values, which is correct for testing the logic but never
proves the logic's own contract (its second return value becomes the NEXT call's first
argument) actually happens across real ticks of the thing that calls it. A round-trip that
only the wiring can break is invisible to a suite that only ever unit-tests the seam and only
ever drives the loop for a single tick — you need a test that (a) enters the sentinel branch
first and later crosses into the widened boundary in the SAME sequence, and (b) drives the
production loop for at least two ticks and asserts what value tick 2 actually received.

**Guard form that survives:** for the boundary widening — construct the exact sequence the
widening exists for (unknown-state tick, THEN gain a real update) using the real seam, and
assert the real-update notification fires on the second tick, not just that the sentinel is
returned from the first. For the wiring — drive the production loop (not the seam directly)
for two ticks with the interval guard forced due on both (e.g. pin the interval constant to
`0` so the real wall-clock gap between ticks still satisfies it), stub the seam to return a
value distinguishable from the loop's initial seed, and assert tick 2 is called with tick 1's
*return* value, not the seed — a dropped assignment reverts every tick to the seed and this
goes red; a working `x = f(x)` passes.

**Found:** CMX-328 rework round 1, PR #420. The judge's required-mutation-set verdict found
`chela/update.py`'s `previously_behind <= 0` narrowable to `== 0` and `chela/main.py`'s
`update_behind_seen = update.check_and_notify(update_behind_seen)` reducible to a bare
`update.check_and_notify(update_behind_seen)` call — both with the full suite (3458 tests)
staying green. Closed by adding
`test_notifier_fires_when_a_checkout_that_was_unknown_later_falls_genuinely_behind` (drives
the real seam through an unknown-then-behind sequence and asserts both notifications fire) and
`test_the_daemon_loop_carries_check_and_notifys_return_value_into_the_next_tick` (drives
`main.cmd_run` for two real ticks with `UPDATE_CHECK_INTERVAL_SECONDS` pinned to 0 and asserts
tick 2 receives tick 1's stubbed return value rather than the loop's initial `0` seed); both
verified to fail when each mutation above is re-applied by hand.
