## 434. A challenge-response gate is driven only with the correct answer

**Assertion form:** a security gate unlocks a peer only after it answers a fresh challenge
(`hello.resume == self._resume_nonce` marks the stream fresh; input from a stale stream is
dropped). The guard replays an old frame (dropped ✓), then sends a hello carrying the RIGHT
answer and asserts input now flows (✓), then replays the old frame again (still dropped ✓).
Every step is true — and none of them ever offers the gate a WRONG answer.

**Mutation that defeats it:** drop the comparison clause — any hello now unlocks the stream
(`if obj.get("t") == "hello" and self._resume_nonce is not None:`). The replayed T_INPUT is
still dropped (it isn't a hello), the correct hello still unlocks, so the suite stays green.
But a plain hello the relay recorded BEFORE the restart — exactly the replay the gate exists
to stop — now unlocks it too.

**Why it is not just shape 55:** 55 is a compound gate whose bound clause is fixtured only
alongside an absent signal. Here the signal is present and the gate is exercised; what is
missing is the adversary's input. A challenge check has two outcomes and the happy-path
test drives only the one an honest peer produces.

**Guard form that survives:** feed the gate every answer an attacker can produce *without*
the secret — no answer, an empty one, a wrong one, and the previous incarnation's correct
answer (a fresh challenge per restart) — and assert each leaves input blocked; only then
send the real answer as the positive control, so the harness is proven able to unlock.

**Found:** CMX-434 rework round 1 (PR #585). Fixed by
`test_a_hello_that_does_not_answer_the_challenge_never_unlocks_input` (parametrized
no-resume / wrong-nonce / empty) and `test_a_fresh_challenge_per_restore_an_old_answer_is_refused`
in `tests/test_share_restore.py`.
