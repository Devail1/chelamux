## 35. A bound clause's short-circuit tested with the gated call parked at the value that hides it

**Assertion form:** a compound boolean has a bound clause meant to short-circuit a downstream
call entirely — not just add another condition, but make the call irrelevant once the clause
is true (`not timed_out and not login_expired and judge.judge_lock_live(...)`: once
`login_expired` is true, `judge_lock_live` is never supposed to be consulted at all). The test
that drives the bound clause's branch never controls what the downstream call would return —
it's left at whatever its real/default behavior resolves to.

**Mutation that defeats it:** delete the bound clause from the AND-chain (`not login_expired
and` dropped), so the downstream call now actually runs in a case it should have been skipped
entirely. If the fixture's downstream call resolves the same way whether or not it's reached
(here: no lock file on disk, so `judge_lock_live` returns `False` either way — reached or not,
the branch taken is identical), the mutation is invisible. The branch the deleted clause was
supposed to prevent from ever running was never distinguished from the branch that runs it and
coincidentally gets the same answer.

**Guard form that survives:** mount the downstream call to return the OPPOSITE of what the
"safe" default would produce — here, a LIVE lock (`judge_lock_live` → `True`) at the same time
as the bound clause's own condition is true — and assert the bound clause still wins (reaps
immediately) even though the downstream call, if consulted, would have said "hold." Only that
combination proves the clause actually short-circuits rather than merely being ANDed in
alongside a call that happened to agree with it.

**Found:** `chela/dispatcher.py`'s `_judge_watchdog` (CMX-282, PR #353, round 1) —
`test_an_expired_login_is_reaped_immediately_and_does_not_spend_a_retry` never mocked
`judge.judge_lock_live`, so it fell back to its real behavior (no lock file on disk → `False`)
— the same answer the watchdog reaches whether or not `login_expired` short-circuits the
cross-check. The judge deleted `not login_expired and` from the AND-chain and the suite stayed
green. Fixed by `test_an_expired_login_reaps_even_when_the_judge_lock_says_alive`, which
patches `judge.judge_lock_live` to return `True` and asserts the watchdog still reaps —
proving `login_expired` bypasses the cross-check rather than merely coexisting with a lock
that happened to already say "not alive."
