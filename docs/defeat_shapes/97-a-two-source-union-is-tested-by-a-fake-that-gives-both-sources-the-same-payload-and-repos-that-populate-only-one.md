## 97. A two-source union is tested by a fake that gives both sources the same payload, and by repos that populate only one

**Assertion form:** `unsupported_files()` unions two git reads — `git diff --name-only
--diff-filter=d <merge-base>` (committed + uncommitted edits, deletions filtered out) and
`git ls-files --others --exclude-standard` (untracked files) — and its docstring promises
"changed (or untracked, not deleted)". Two guards covered it: a fake `_git` that answered
EVERY call with the same `x.go\0` (to prove a failing read is an error, not "no files"), and
end-to-end runs in a throwaway repo whose foreign files were all COMMITTED on the branch.

**Mutation that defeats it:** drop either half of the contract — union only `names.stdout`
(untracked files vanish), or delete `--diff-filter=d` (a deleted file is reported UNKNOWN).
Both stayed green across the whole suite.

**Why it looks covered:** the fake gives both sources the same payload, so the union equals
either source alone — it cannot tell whether the second source was read. And the real-repo
fixtures only ever produced the state one source sees (a committed file): none had an
untracked file, so the `ls-files` half contributed nothing, and none deleted a foreign file, so
the filter had nothing to filter. A flag or source that only matters in a state no fixture
creates is not guarded, however many end-to-end tests run around it. (Shape 45b is the
"second source answers empty" variant. This is "both sources answer the same", plus a filter
no fixture triggers.)

**Guard form that survives:** build ONE real repo with every state the docstring names, each
on a distinct file: committed, uncommitted edit, untracked, committed delete, working-tree
delete, the old side of a rename, ignored, and a test file. Then assert the EXACT result set.
Each source and each filter now owns at least one file that only it can add or remove, so
dropping any one of them changes the set.

**Found:** CMX-97 rework round 2, PR #678. `chela/sweep.py` `unsupported_files()`; fixed in
`tests/test_judge_sweep_standalone.py`
(`test_unsupported_files_is_exactly_the_live_foreign_source_vs_the_merge_base`).
