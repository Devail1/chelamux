## 329. A new branch's `notify.enabled()` gate goes untested because every fixture exercising that branch hardcodes the notifier as enabled

**Assertion form:** a function has an existing branch that correctly checks
`notify.enabled()` before pushing, proven by a dedicated disabled-notifier test
(`test_notifier_never_pulls`, constructing `_StubNotify(enabled=False)`). A new branch is
added to the same function, copying the same `if notify.enabled(): notify.send(...)` shape.
Every test written for the new branch — including the one written specifically to name the
DEFEAT_SHAPES risk of an unasserted payload — constructs `_StubNotify(enabled=True)`, because
the tests were designed to prove the branch's *content* (log line, push payload,
edge-triggering) fires, not to independently re-prove the *gate* on a branch that only looks
like it inherited proof from its sibling.

**Mutation that defeats it:** replace `if notify.enabled():` with `if True:` immediately
before the new branch's `notify.send(...)` call. Every test for that branch still constructs
an enabled stub, so nothing ever observes `stub.sent` while disabled — full suite (3470
tests) stays green.

**Why coverage looked complete but wasn't:** the sibling branch's disabled-notify test
creates the appearance that "the notifier gate is tested" as a property of the module, when
it is actually tested only for the one branch that has a dedicated fixture. A second branch
reusing the identical one-line gate does not inherit that proof — each call site to
`notify.send` behind `notify.enabled()` needs its own disabled-stub test, because the
mutation that deletes the gate is scoped to that call site, not to the module.

**Guard form that survives:** construct `_StubNotify(enabled=False)`, drive the specific
branch under test, and assert `stub.sent == []` — mirroring the proof pattern already used
for the sibling branch, rather than assuming it transfers.

**Found:** CMX-328 rework round 2, PR #420. The judge's required-mutation-set verdict found
`chela/update.py`'s unknown-state branch's `if notify.enabled():` reducible to `if True:`,
with the full suite (3470 tests) staying green — every prior test of that branch, including
`test_notifier_warns_once_on_transition_to_unknown_state` (added in round 1 specifically to
close a payload-assertion gap), used an enabled stub. Closed by adding
`test_notifier_respects_disabled_notify_on_transition_to_unknown_state` (mirrors
`test_notifier_never_pulls` for the unknown-state branch, asserting `stub.sent == []` when
disabled); verified to fail when the mutation above is re-applied by hand.
