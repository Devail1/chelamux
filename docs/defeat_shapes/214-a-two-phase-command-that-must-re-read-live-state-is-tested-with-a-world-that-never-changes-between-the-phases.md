## 214. A two-phase command that must re-read live state is tested with a world that never changes between the phases

**Assertion form:** a command runs in two phases separated by real time (pause → resume,
claim → finish, freeze → thaw). Phase 1 writes a snapshot of something live (a window id, a
pid, a path); phase 2 must ignore that snapshot and re-read the live source, because the
world moved in between (the window was respawned, the id recycled). The test runs phase 1,
then phase 2, against the same fixture, so the snapshot and the live value are equal and
phase 2 reads correctly no matter which one it uses.

**Mutation that defeats it:** in phase 2, read the snapshot instead of the live source.
CMX-214 (`chela/fleet.py`, `resume_all`): `wid = inbox.run_wid(runs[task])` became
`wid = agent.get("wid")`, the window recorded at pause time. Every resume test paused and
resumed against runs whose `window_id` never changed, so "resume" still reached the same
window and the suite stayed green.

**Guard form that survives:** between the two phases, change the live source so the
snapshot is wrong. Then assert that phase 2 acted on the live value and NOT on the
snapshot. For a source that can disappear, add a case where the live value is gone (the run
row has no window) and assert phase 2 does nothing. CMX-214 now has
`test_resume_goes_to_the_window_on_the_run_row_now` (`tests/test_fleet_pause.py`): the run
row moves T1 to `@9` and gives T2 no window, then the test asserts "resume" reaches `@9` only.

**See also:** [[342|shape 342]], the general family (two quantities that coincide in every
fixture); here the pair is "the same quantity at two moments". [[363d|shape 363d]] is the
same PR's other survivor: the re-pause skip set `(INTERRUPTED, FINISHING)` was only ever
fixtured on `INTERRUPTED`.
