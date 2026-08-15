## 55. A compound entry gate's bound clause is never driven with a state that would make the ungated tier actually resolve differently

**Assertion form:** a resolution tier opens with a compound `and` guard
(`if pane is not None and pane.started is not None:`) — the first clause is only there to
make the tier's inputs safe to read, but the second clause is the actual invariant: a floor
that bounds how stale a durable signal is allowed to be, per the module's own stated rule
("Every signal that cannot be bounded is refused rather than believed"). Every test that
drives the second clause to its refusing value (`pane.started = None`) does so on a fixture
where the tier's own signal is also absent (no pin set) — so the gate refusing and the tier
simply having nothing to find produce byte-identical output. The clause meant to bound the
signal is never exercised *with a signal present to bound*.

**Mutation that defeats it:** drop the second clause (`and pane.started is not None`
removed). Every existing test that sets `pane.started = None` also leaves the tier's own
signal unset, so the mutant enters the block, finds nothing, and refuses anyway — same
output as the original. Nothing in the suite ever gave the ungated tier something to
actually try using.

**Guard form that survives:** construct the exact state the clause exists to catch — the
unbounded input (`pane.started = None`) — *together with* a resolvable signal downstream (a
pin naming a transcript that really exists on disk). Only that combination can distinguish
"the gate refused" from "there was nothing to find regardless": with the signal present, a
missing bound clause lets the tier actually reach for it (and here, since the freshness
check itself needs the very floor the clause was supposed to guarantee is non-`None`, doing
so raises rather than silently producing a wrong answer — either way, the suite goes red
only when both halves of the state are armed at once).

**Why this is distinct from [[47|shape 47]]:** shape 47 is a single-condition early-return
filter whose one branch is untested because every fixture's input already matches the
accepted value. This shape is a two-clause *entry* gate where clause 1 is always true in
every fixture that reaches it at all (a pane always exists) and clause 2 is the one that
varies — but "clause 2 varies" was never paired with the one fixture detail (a resolvable
pin) needed to make its absence observable. The gap isn't that an input value was never
tried; it's that the *combination* of that value with an armed downstream signal was never
tried.

**Found:** CMX-295 rework round 1, PR #368. `chela/sessions.py`'s session-id-pin tier
(`resolve_window`) opens with `if pane is not None and pane.started is not None:`.
`test_a_window_whose_process_cannot_be_read_does_not_inherit_a_SESSION_either` already drove
`pane.started = None` for tier 1's own bound, but the suite's autouse `no_pin` fixture keeps
`chela.sessionids.session_id_for` returning `None` throughout, so no test ever combined an
unbound pane with a pin that would actually resolve. The judge dropped
`and pane.started is not None` in a throwaway checkout and 3137 tests stayed green. Closed
by `test_the_pin_is_refused_when_the_panes_process_start_time_is_UNKNOWN`, which pins a real,
on-disk transcript to a window whose pane has `started=None` and asserts the pin is still
refused — a mutant that drops the clause instead reaches the freshness comparison with no
floor to compare against and raises `TypeError` before it can even produce a wrong answer.
