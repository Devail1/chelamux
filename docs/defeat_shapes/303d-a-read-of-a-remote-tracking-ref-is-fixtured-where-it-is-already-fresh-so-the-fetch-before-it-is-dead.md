## 303d. A read of a remote-tracking ref is fixtured where it is already fresh, so the fetch before it is dead

**Assertion form:** the code fetches, then reads `origin/<base>`
(`git fetch --quiet origin dev`, then `git rev-parse origin/dev`). The fixture builds the repo
by pushing every commit from the SAME clone that is then swept, and asserts the run read the
tip it pushed.

**Why that's a gap:** a `git push` from a clone updates that clone's own `origin/dev` as a side
effect. So in every fixture the remote-tracking ref is already exactly the remote's tip before
the fetch runs, and a fetch that fetches nothing (a `--dry-run`, a wrong refspec, no fetch at
all) reads the same, correct tip.

**Mutation that defeats it:** `"fetch", "--quiet", "origin", ref.split("/", 1)[1]` →
`"fetch", "--quiet", "origin", "--dry-run"`. Suite green, while in production the merges land
on GitHub, not from this clone, so the stale `origin/dev` silently sweeps yesterday's tip and
the new merges are never covered.

**Guard form that survives:** advance the remote from a SECOND clone after the swept clone's
last fetch, assert the swept clone's `origin/<base>` is stale before the run, and assert the run
read the new tip and swept what it brought in. Pin the fetch's whole argv as well (remote and
branch), on a non-default base branch so the branch name is not also the default's.
Related: [[02|shape 02]], [[303b|shape 303b]].

**Found:** CMX-299 rework round 3 (2026-10-21), PR #819 (closed; re-scoped, and its scheduler
slice CMX-303 carries the guard). Closed in `tests/test_sweep_nightly.py` by
`test_the_sweep_fetches_the_base_so_a_tip_that_advanced_is_read` and
`test_every_git_call_runs_with_full_argv_cwd_and_timeout`.
