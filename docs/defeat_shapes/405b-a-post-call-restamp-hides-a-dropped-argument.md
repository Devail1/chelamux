## 405b. A post-call re-stamp hides a dropped argument, so the rendered value can't expose it

**Assertion form:** a caller computes a limit (`exp_cap = judge_max_experiments(risk)`) and
passes it into a worker (`run_experiments(..., max_experiments=exp_cap)`). After the call
returns, the caller writes the same value onto the result again
(`report.risk, report.cap = risk, exp_cap`) so that every path, including the error paths
that never call the worker, renders a consistent header. The guard asserts the rendered
header says "risk: low — 4 experiments". It is green, and the header is right.

**Mutation that defeats it:** drop the keyword from the worker call on one path
(`base_branch=base_branch, risk=risk,` with `max_experiments=exp_cap` removed). The worker
falls back to its default (the old global 12) and runs all six proposals. The header still
says 4, because the re-stamp after the call overwrote the worker's own `cap` with the right
number. The rendered value reports the intended limit, not the one that was enforced.
(CMX-405, PR #557, round 2. The reprovisioned-worktree branch of `judge_run` is the path the
suite didn't drive, which also makes it [[7|shape 7]].)

**Guard form that survives:** assert the effect of the limit, not its label. Here that is
`len(result["outcomes"]) == 4` for six proposals at `low`, driven through **each** branch
that calls the worker (the existing-worktree and the rebuilt-worktree paths). The same
applies to any text that echoes the limit next to the effect. The "N further experiment(s)
were not run (the cap is X)" line must be pinned to the level's own number and asserted not
to be the old global constant. Otherwise a render that reads the constant still passes on
the one level whose number happens to show up elsewhere in the text.

**Why this is distinct from [[7|shape 7]]:** shape 7 is an unexercised call site. Here the
fixture's assertion *would* reach the result of the unexercised path if one were added, but
it would still pass: the value it reads is written *after* the call, from the caller's own
variable, so it cannot tell whether the call honoured it. Adding the missing path's fixture
is not enough; the assertion has to move from the label to the count.
