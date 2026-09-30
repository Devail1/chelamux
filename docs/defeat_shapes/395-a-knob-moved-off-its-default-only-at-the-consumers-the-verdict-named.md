## 395. A knob is moved off its default only at the consumers the last verdict named, and its other consumers in the same function stay parked on the default

**Assertion form:** a per-workflow knob (`judge.consistency_sample`, `judge.held_out_fraction`)
feeds several consumers inside one function: two `run_experiments(...)` calls on sibling
branches (worktree present / worktree just rebuilt), a prompt renderer, and a private-record
writer. A verdict names a surviving mutation at *some* of those consumers — the knob replaced
by its module constant — and the rework applies shape 2's prescribed fix exactly there: mount
a non-default value (`consistency_sample: 1`, `held_out_fraction: 0.55`) and read it back
through the named consumer. Each new test is honest and goes red for the mutation it answers.

**Mutation that defeats it:** replace the knob with its constant (or `0`) at a consumer the
verdict did *not* name — the rebuilt-worktree branch's `run_experiments` call, or the
`record_private(..., judge_cfg.held_out_fraction)` argument. The non-default fixture never
reaches the rebuilt branch (its worktree always exists), and nothing reads the private
record's `quota` back, so the suite stays green. A flag passed alongside (`stale=stale_head`)
has the same gap for the same reason: the sink it feeds is new and nothing reads it back
(shape 342b).

**Why this is distinct from shapes 2, 7 and 25:** shape 2 is the default-parked fixture
itself; shape 7 is the second caller never driven; shape 25 is a fix applied fully at one
site and *partially* at its sibling. Here the fix is applied *fully*, but its scope was set
by the verdict's list instead of by the knob's use list. That is the overfitting the judge's
held-out set exists to catch: fixing the listed cases, not the guard.

**Guard form that survives:** before closing a "knob replaced by its constant" finding,
`git grep` the knob's attribute (`judge_cfg.consistency_sample`) and drive every hit with a
non-default value. Parametrise over the branches that reach each one, and assert the branch
really ran (for example, a spy on `_reprovision_worktree` was or was not called). For a flag
passed into a new sink, read the sink back on both values of the flag, and include a control
for the value the mutation would hardcode.

**Found:** CMX-395 rework round 2 (2026-09-29), PR #547. Round 1 pinned both knobs at
non-default values through `judge_suite_config`, the rendered prompt and one end-to-end
`judge run`. The judge then hardcoded `consistency_sample=0` on the reprovisioned branch,
`HELD_OUT_FRACTION` into `record_private`, and `stale=False`, and all three survived 4372
tests. They were closed by a `[worktree-present, reprovisioned]` parametrised spy test, a
quota read-back at `held_out_fraction: 0.55` over 3 experiments (quota 2 vs. the default's 1),
and a stale/fresh pair on the private record's `stale` field.
