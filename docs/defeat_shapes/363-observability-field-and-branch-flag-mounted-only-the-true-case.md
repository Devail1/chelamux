## 363. Observability field and branch flag mounted only the TRUE case

**Assertion form:** a new failed-read flag (`GhIssuesSource.read_failed`, and the tick
summary's `tracker_read_failed` field it feeds) is set `True` on several distinct failure
branches, and exactly one test (`test_tick_does_not_reconcile_a_review_row_when_the_tracker_read_fails`)
asserted `summary["tracker_read_failed"] is True` on a tick where the read genuinely failed.
No test ever asserted the flag's value on a tick where the read genuinely *succeeded*, and no
test drove `GhIssuesSource.list_open_tasks()` through its non-zero-exit or config_error
branches at all — the only fixture exercising the flag went through `MarkdownSource`, a
different implementation of the same interface.

**Mutation that defeats it:** three independent one-line mutations, all invisible to the
existing suite:
1. `chela/dispatcher.py`'s tick summary: replace `"tracker_read_failed": tracker_read_failed`
   with the literal `"tracker_read_failed": True`. Every tick, real or mocked, now reports
   `True` regardless of whether the read failed — every existing assertion is `is True`, so
   all of them still pass.
2. `chela/sources/gh_issues.py`'s non-zero-`gh issue list`-exit branch: change
   `self.read_failed = True` to `self.read_failed = False` right after logging the failure.
   No test reads `.read_failed` after driving a non-zero exit code through the fake `gh`, so
   this is unobserved.
3. The same file's `config_error` branch (the "fail CLOSED and LOUD" branch whose own comment
   states the exact defect this flag exists to prevent): same substitution, same result —
   `test_unconfigured_refuses_and_SAYS_SO` only checked the returned task list and the log
   text, never the flag the branch is supposed to set alongside them.

**Guard form that survives:** mount the OFF state as well as the ON state, and mount both
branches of the implementation the flag was added to, not just the sibling implementation
that happened to have an existing fixture:
- Assert `tracker_read_failed is False` on a tick whose tracker read actually succeeded
  (`test_tick_removes_the_worktree_when_the_tracker_line_is_struck_by_hand`), so a hardcoded
  `True` constant goes red there even though it still passes the failed-read test.
- Drive `GhIssuesSource.list_open_tasks()` through a non-zero `gh` exit and assert
  `src.read_failed is True` directly (`test_a_nonzero_gh_exit_sets_read_failed`).
- Assert `src.read_failed is True` inside the existing config_error test
  (`test_unconfigured_refuses_and_SAYS_SO`), rather than only asserting the task list and log
  message that test already checked.

**Found:** CMX-363 rework round 1 (2026-09-13), PR #498 — judge's required-mutation-set
verdict, all three mutations SURVIVED the pre-fix suite (`CHELA_REQUIRE_JS_TESTS=1 uv run
pytest -q`, 3966 passed / 0 failed both before and with each mutation applied). See also
[[03|shape 3]] (positive-case-only mount, never mounts the OFF state — named directly by the
judge's own verdict on this PR) and [[66|shape 66]] (a two-axis predicate's negative control
covers only one side) — this is the same "only the TRUE branch is mounted" shape, recurring
across a dispatcher-observability field and a tracker-source's own instance attribute in the
same PR.
