## 430d. A candidate filter is pinned only where over-including is harmless

**Assertion form:** a filter picks which items go on to an expensive or failure-prone step.
Here, only runs ABSENT from the open listing get re-read by id. The test for "nothing to do"
seeds a row that IS in the listing, runs a tick against the real adapter, and asserts that
no failure was reported or logged.

**Why it doesn't guard:** drop the filter so every row is a candidate, and the step still
runs against the same healthy adapter. It reads the extra row, reports it open, and nothing
observable changes. The fixture makes over-including free, so the test cannot tell the
filtered code from the unfiltered code. The filter only matters when the step it skips
would FAIL or ACT, and the fixture never sets that up.

**Mutation that defeats it:** (CMX-430) `[r["task_id"] for r in rows if r["task_id"] not in
open_ids]` → `... if True or r["task_id"] not in open_ids]`. Every tick then asks the
tracker for every live run. When the refresh is down, every quiet tick logs a FAILURE and
reports `tracker_refresh_failed`. Suite green: the only "no candidates" test used a
refresh that succeeds.

**Guard form that survives:** make the skipped step costly in the fixture. Use a listing
that still carries the task, and an id refresh that would FAIL and records what it was
asked. Then assert it was never asked (`asked == []`), and that the tick reports and logs no
failure. A filter is proven only by a fixture in which an item it lets through would change
the outcome.
