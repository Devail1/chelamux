## 296. A fail-safe guard sits above a catch-all that lands on the same answer

**Assertion form:** a function fails safe twice: first an early return for a missing input
(`if row is None or not row["worktree_path"] or not row["workflow_path"]: return CORE`), then a
`try/except Exception` around the real work that also returns `CORE`. The test drives each
missing input and asserts the return value is `CORE`.

**Mutation that defeats it:** disable the guard (`if False and (...)`). The suite stays green. A
`None` row or path now raises a `TypeError` inside the `try` (`load_workflow(None)`,
`Path(None)`), the catch-all turns it into `CORE`, and the test only reads the return value, so
it cannot tell which branch answered. The agent then declared the survivor "equivalent". It is
not equivalent. Without the guard, the function logs a missing run as a failure, loads the
workflow and shells out to git for a run that has nothing on record. The safe answer also
depends on the `except` staying broad: narrowing it to `(OSError, ValueError)` makes an unknown
run crash `task-finished` instead of returning CORE.

**Guard form that survives:** assert which path answered as well as the answer. Patch the work
inside the `try` (`load_workflow`, `judge._changed_files`) to return a value that would give the
LENIENT answer (`["chela/app.py"]` ⇒ standard), then assert that neither one was called and that
no "could not read" warning was logged. Write one case per condition in the guard: no row, no
worktree, and an empty workflow path. If the column is `NOT NULL`, write `''` to it, because a
`None` the schema refuses never reaches the code. Test the catch-all separately, with a value
the guard passes.

**Found:** CMX-296 round 1 (2026-10-21), PR #809, `chela/dispatcher.py` (`hand_in_tier`). Closed by
`tests/test_standard_no_judge.py::test_hand_in_tier_with_nothing_on_record_never_reads_the_tier`.

**Related:** [432c](432c-a-primary-check-and-its-fallback-are-fed-by-the-same-fixture-so-deleting-the-primary-changes-nothing.md):
there one fixture fed the primary and the fallback the same answer. Here no fixture is needed,
because both branches already return the same constant.
