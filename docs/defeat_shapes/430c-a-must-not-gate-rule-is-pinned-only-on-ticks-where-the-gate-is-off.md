## 430c. A "this failure must NOT gate that transition" rule is pinned only on ticks where the failure never happened

**Assertion form:** a new failure mode (here: the tracker id refresh FAILED) is meant to block
ONE class of transition and leave every other one alone. The tests for the other transitions
(a dead window → `failed`, the agent's task-finished marker, merged-PR evidence) already
existed — and they all run on ticks where the new failure is **off**: the refresh succeeded,
or there was nothing to refresh because the row's task sat in the open listing. A new test or
two pins the "still acts" rule for whichever (status × evidence) pair the author thought of.

**Why it doesn't guard:** a mutation that gates a transition on the new failure flag
(`... and not summary["tracker_refresh_failed"]`) is a no-op on every tick where the flag is
False — which is every tick the pre-existing tests run. Only a test that runs that exact
transition WITH the flag True can tell the gated code from the ungated code. Pinning pairs
one at a time never converges: each judge round found one more unpinned pair (round 4:
merged PR on review rows only; round 5: the same branch on `running`/`failed`, the dead
window, the task-finished marker).

**Mutation that defeats it:** (CMX-430) `if row["status"] == "running" and row["window_name"]
and row["window_name"] not in live_windows:` → `... and not summary["tracker_refresh_failed"]:`.
A dead agent stays `running` for as long as the tracker is unreadable. Suite green — the
dead-window tests all refresh successfully.

**Guard form that survives:** pin the RULE, not the cases — ONE table-driven test over every
(status × non-tracker evidence) pair, each row running a real tick with the failure **forced
on** (and asserted on: `summary["tracker_refresh_failed"] is True`, not assumed), and each
row's status set taken from the code's OWN constants (`RECONCILE_MERGE_STATUSES_WITH_RUNNING`,
`ACTIVE_STATUSES`, …) so a status added to a constant joins the table on its own. Add a
coverage assertion that every status the pass reads appears in some row, and the converse
half — with no other evidence, the failure changes nothing — over the same status set.

**Related:** [[416|shape 416]] (a gate called from two branches proven only on one) — the same
"the test only visits one side of the condition" blind spot, here on a flag the code under
test must NOT read.
