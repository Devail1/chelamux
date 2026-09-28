## 379. A tri-state walk's "keep going" arm is never populated, so it collapses into "decide no"

**Assertion form:** a function walks a sequence newest-first and each entry answers one of
THREE things: *decides yes* (return True), *decides no* (return False), or *settles nothing*
(skip it and look at the next, older entry). CMX-379's `_outage_verdict` is the example: a
tool result carrying the classifier-outage signature decides yes, a tool result that
SUCCEEDED decides no, and any other error (a failed Read, `EISDIR`) settles nothing. The tests
cover the two deciding arms — an outage tail (yes), an outage followed by a success (no) — and
never put a settles-nothing entry in front of a deciding one.

**Mutation that defeats it:** fold the skip arm into one of the deciding arms —
`if not block.get("is_error"): return False` → `if True: return False`, so *every* non-outage
result, errors included, ends the walk as "no". Every fixture's most recent non-outage entry
is a success, so "only a success decides no" and "anything that isn't the outage decides no"
return the same answer on all of them. In production the difference is the whole feature: a
judge whose last tool call failed for an ordinary reason after the outage is still stuck on
the outage, and the mutated walk quietly stops reaping it.

This is the mirror image of [[12]]: there the risk is a walk that should STOP at an entry
falling through past it; here it is a walk that should FALL THROUGH an entry stopping at it.

**Guard form that survives:** for each "settles nothing" category, build a fixture where
that entry is the MOST RECENT one and a deciding entry sits behind it, and assert the answer
comes from the deciding entry (outage + later `EISDIR` ⇒ still an outage). Then check the
mutation that collapses the skip arm into either neighbour goes RED.
