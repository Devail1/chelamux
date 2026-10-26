## 334c. A failure cleanup is fixtured with a failure that stops before anything needs cleaning

**Assertion form:** `op()` fails ⇒ `undo()` (here: `git merge` fails ⇒ `git merge --abort`).
The test makes `op()` fail, asserts the refusal, and asserts the state is as before (HEAD
unchanged). It reads as "a failed op is cleaned up".

**Why that's a gap:** the fixture's failure is one `op()` detects *before it starts* — a dirty
worktree makes git refuse the merge without writing `MERGE_HEAD` or touching a file. The state
is unchanged because nothing ever changed, so `undo()` is a no-op on that path and the test
holds with the undo replaced by anything (`git status`). The failure the undo exists for — the
op that starts, half-applies and stops (a conflict the pre-check missed) — is never fed.

The same PR had two siblings of it: a fail-closed `except` whose only fixture made an
*earlier* step fail (a missing remote breaks the fetch first, so the new `rev-parse` step's
own `except` was never reached), and a "HEAD already has the base" early return no fixture
reached because every move test leaves HEAD behind.

**Mutation that defeats it:** replace `undo()` with a no-op call; turn the new `except`'s
refusal into a pass; drop the second conjunct of the early return.

**Guard form that survives:** feed the failure *mid-way*: stub the pre-check to miss the
conflict, so the real merge starts and stops, then assert the in-progress marker is gone
(`MERGE_HEAD` absent), the worktree is clean, and no file the half-merge wrote is left. For
each new fail-closed step, make *that* step fail (stub it to raise) while the steps before it
succeed. For each conjunct of an early return, one fixture where only that conjunct is true.
Related: [[86|shape 86]] (undo on a branch never entered), [[337|shape 337]].

**Found:** CMX-334 rework round 1 (2026-10-26), PR #883 — `handinmove.reconcile`'s
`merge --abort`, `check_base_sync`'s `base_sha` refusal, `basesync.moved`'s `not behind`.
