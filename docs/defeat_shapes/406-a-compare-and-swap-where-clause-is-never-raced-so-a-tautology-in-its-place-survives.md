## 406. A compare-and-swap `WHERE … AND status=?` is never raced, so a tautology in its place survives

**Assertion form:** `close_run` reads a run row, runs a few checks on the status it read, then
writes `UPDATE runs SET status='closed' … WHERE task_id=? AND status=?`, passing the status it
read. The docstring says a tick that moved the row in the meantime wins and nothing is
changed. The suite covers every status the function accepts or refuses. Each of those tests
seeds a row, calls the function and reads the row back. Nothing else writes to the row
between the read and the UPDATE in any of them.

**Why that's a gap:** with no concurrent writer, `status=?` always matches, because the row
still holds the status that was just read. The CAS clause can only change the result when
the row moves between the SELECT and the UPDATE, and no test ever creates that window. The
`if cur.rowcount == 0:` arm that reports "run moved to …" is dead code as far as the suite
can tell.

**Mutation that defeats it:** `WHERE task_id=? AND status=?` →
`WHERE task_id=? AND ? IS NOT NULL`. The parameter count and order stay the same, so every
call still binds, and the UPDATE lands whatever status the row now holds. The whole suite
stays green.

**Guard form that survives:** reproduce the race deterministically. Patch a step the function
takes after its read and before its write (here, `_close_liveness`) so the patch first writes
a different status to the row, as a tick would, and then delegates to the real function.
Then assert that the call is refused, that the row keeps the tick's status, and that no
reason, history entry or event was written. Pair it with a negative control: the same
patched path without the intervening write must still close. Otherwise a refusal could just
mean the patch broke the function.

**General rule:** any "only if still X" write (CAS on a status, an `updated_at`, a version
column) needs one test in which something else changes X between the read and the write.
Sequential fixtures satisfy the guard under both implementations.

**Found:** CMX-406 rework round 1 (2026-09-30), PR #556. The same round also caught
`pr_is_open`'s "NULL `pr_state` counts as open" docstring claim without a NULL fixture (shape
[[373|shape 373]]), and the runs-table call site of `runCardNote` left unasserted while its
kanban twin was guarded (shape [[79|shape 79]]).
