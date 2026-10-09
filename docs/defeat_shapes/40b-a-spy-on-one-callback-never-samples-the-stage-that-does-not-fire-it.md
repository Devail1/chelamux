## 40b. A spy on one callback never samples the stage that does not fire it

**Assertion form:** a test proves "the live status is right" by wrapping ONE of the hooks a
long-running pipeline calls (here `progress(done, total)`, which `_apply_experiments` fires as
each experiment starts) and snapshotting the written status from inside that wrapper. The
assertions over those snapshots are exact and look complete. But a LATER stage of the same
pipeline — the consistency re-run, which re-applies experiments already adjudicated — goes
through a code path that never calls that hook (it passes no `progress`), so every write it
makes is invisible to the test. The fixture also held every experiment on one value (all
visible, all KILLED), so the one branch per variant (`held_out`, SURVIVED, INVALID) was never
reached either.

**Mutation that defeats it:** drop the stage condition that protects the tally from the later
stage (`elif event == "outcome" and status.get("phase") != "consistency":` →
`elif event == "outcome":`) — the re-run now double-counts, but only in writes the spy never
sees. Or disable the held-out branch of the label (`if False and raw_exp.get("held_out") is
True:`) — no fixture experiment is held out, so the label the test reads is the same.

**Guard form that survives:** spy on the WRITE itself (`_write_run_status`), not on one of the
callers that happens to precede some writes, so every stage's output is captured; assert the
stage you claim to be safe against actually ran (`phase == "consistency"` appears, and the
result reports `sampled == 2`), and that its writes leave the tally exactly where the first
pass left it. Give the fixture one item of EACH variant the code branches on (KILLED,
SURVIVED, INVALID, held-out) and assert the distinct progression of the tally, plus that the
held-out item's guard text appears in NO captured write.

**Found:** CMX-40 rework round 2 (2026-10-09), judge verdict on PR #622.
`tests/test_judge_battery_progress.py`'s
`test_judge_run_writes_the_tally_the_label_and_the_window` snapshotted inside a `progress`
wrapper over two visible, KILLED experiments; both mutations above stayed green on the full
suite (6281 passed). Closed by rewriting it to spy on `_write_run_status` over a four-experiment
battery (one of each verdict plus a held-out one) with the default consistency re-run on.
