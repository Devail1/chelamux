## 88. An "always overwrite, never inherit" write is tested only from a row that already holds the reset value

**Assertion form:** a row is reused across rounds (a rework round reuses its run row), and a
per-round column must be WRITTEN on every completion — `UPDATE runs SET col=? …` with NULL
when this round supplied nothing — so a round never inherits the previous round's value. The
tests write a value and read it back, and check that a round with nothing supplied reads
NULL. Both pass.

**Mutation that defeats it:** turn the overwrite into a merge —
`SET col=?` → `SET col=COALESCE(?, col)`. A round with nothing supplied now keeps the
previous round's value instead of clearing it.

**Why it looks covered:** every "nothing supplied ⇒ NULL" test starts from a fresh row whose
column is ALREADY NULL — `COALESCE(NULL, NULL)` is NULL too, so the reset and the inherit
read the same. The sibling column the change was modelled on (`handoff`) had the right test —
it seeds `'round 1 note'` first — and the new column copied the code comment ("always
written, like the handoff") but not the test.

**Guard form that survives:** seed the column with a previous round's NON-reset value first,
then complete a round that supplies nothing, and assert the reset value. When a new column
mirrors an existing "never inherit" column, mirror its seeded test along with its comment.

**Found:** CMX-88 rework round 2, PR #661. `chela/dispatcher.py` `mark_awaiting_review()`'s
`runs.sweep_equivalents` write; fixed in `tests/test_task_finished_handoff.py`
(`test_a_rework_round_finished_without_equivalents_does_not_inherit_the_last_ones`). The
same round's second survivor — `_seg` slicing ast's UTF-8 BYTE offsets by characters, green
because every fixture was ASCII — is shape 29 (an ASCII payload is identity under bytes vs
characters); fixed with a non-ASCII fixture in `tests/test_judge_sweep.py`.
