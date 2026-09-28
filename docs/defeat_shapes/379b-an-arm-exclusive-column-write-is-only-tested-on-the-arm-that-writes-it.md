## 379b. An arm-exclusive column write is only tested on the arm that writes it

**Assertion form:** one shared `UPDATE` serves several reap arms, and ONE of them is meant to
set an extra column — every other arm must write NULL. CMX-379's judge watchdog is the
example: only the classifier-outage arm stamps `judge_retry_after`, via
`… if classifier_outage and now is not None else None`. The tests pin the stamp on the outage
arm (present, and computed from the knob), and pin that a later judge-state write clears it —
but never reap through the timeout, login-expired or vanished-window arm and read the column
back. Those arms' own tests predate the column and never look at it.

**Mutation that defeats it:** drop the arm from the condition —
`if classifier_outage and now is not None` → `if now is not None`. Now every reap stamps a
backoff. The outage tests still pass (the outage arm still stamps), and the older arm tests
still pass (they never read the new column). In production a timeout or a vanished window is
shown on the inbox as an outage and the trigger backs it off for no reason.

The general form: a new column/field written by a SHARED statement, conditioned on one arm,
is a property of every arm that runs the statement — not just the one that sets it. The
tests written alongside a new arm cover the new arm; the existing arms' tests were written
before the field existed and cannot see it.

**Guard form that survives:** parametrize over every OTHER arm that runs the shared write,
drive each one to actually fire (assert the handed-over count and that arm's own reason /
flags, so a NULL is the arm's write and not an untouched row), and assert the field is NULL.
Then apply the "drop the arm from the condition" mutation and watch every parameter go RED.
