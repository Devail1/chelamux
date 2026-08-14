## 36. A ternary's mutually-exclusive branches share a keyword, so a substring assertion can't tell which branch rendered

**Assertion form:** a user-facing string is built by a ternary/if-chain with several
mutually-exclusive branches, each describing a *different* cause for the same outcome. The
guard picks one branch, drives the code down it, and asserts the rendered string with a
single lowercase-substring check (`"login" in judge_detail.lower()`) chosen because that
word appears in the branch's own prose.

**Mutation that defeats it:** rewrite the branch's sentence to claim a DIFFERENT cause,
while leaving one incidental fragment untouched that happens to contain the same substring —
`"the judge's session login expired mid-run (\"Login expired · Please run /login\") — not a
verdict on the PR"` → `"the judge's window disappeared before it published a verdict
(/login)"`. The rewritten sentence now describes the *other* branch's failure mode entirely
(the window vanishing, not the login expiring) — a real misfiling a human reading the reason
would be misled by — but it still contains the literal substring `/login`, so
`"login" in judge_detail.lower()` still passes and the suite stays green.

**Why this is distinct from shape 23:** shape 23 is two *numeric* quantities computed from
the same inputs that happen to collapse onto the same digits when one formula is broken.
This shape is prose: two *mutually exclusive branches* of a dispatch, each meant to be
distinguishable from the others, that share an incidental keyword — the assertion is testing
"does this word appear anywhere in the string," not "did the RIGHT branch fire."

**Guard form that survives:** when a ternary's branches exist specifically to report
different causes for the same outcome, assert the FULL expected sentence for the branch
under test (`==`, not `in`) — or at minimum a phrase unique to that branch that the
sibling branches do not also contain. A substring shared across branches proves nothing
about which branch rendered.

**Found:** `chela/dispatcher.py`'s `_judge_watchdog` (CMX-282, PR #353, round 3) — both
`test_an_expired_login_is_reaped_immediately_and_does_not_spend_a_retry` and
`test_an_expired_login_reaps_even_when_the_judge_lock_says_alive` asserted only
`"login" in judge_detail.lower()`. The judge rewrote the `login_expired` branch's sentence to
read like the window-vanished branch while keeping a stray `/login` in it, and both tests
stayed green. Closed by asserting the full expected sentence with `==` in both tests.
