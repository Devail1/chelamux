## 368d. A function's only untested error return is the one branch every fixture in the file avoids by a shared hardcoded field

**Assertion form:** `_apply_push_request` has five distinct failure returns: a missing
marker, a missing `branch_name`, a failed `git push`, a failed `gh pr create`, and a failed
`gh pr view` recovery. Four of the five have a dedicated test. The fifth —
`if not branch: return {"ok": False, "error": "run has no branch_name on record"}` — does
not, for a reason that isn't visible from reading any single test: every test function in
`tests/test_dispatcher_push_request.py` that builds a row for `_apply_push_request` passes
`branch_name="cmx-1"` (or another non-empty literal) to the shared `_row(...)` helper. The
helper's own default is also non-empty. No individual test "skips" the branch-name check on
purpose; the whole file simply never happens to construct the one input that would reach it.

**Mutation that defeats it:**

```diff
      branch = row["branch_name"]
-     if not branch:
+     if False and not branch:
          return {"ok": False, "error": "run has no branch_name on record"}
```

`CHELA_REQUIRE_JS_TESTS=1 uv run pytest -q` stayed green (4046 passed) — every test in the
file supplies a `branch_name`, so `not branch` was already `False` on every call the suite
makes; dead-coding the check with `False and` changes no test's control flow. With the guard
gone, a row with no `branch_name` on record falls through to `_git(Path(worktree_path),
"push", "-u", "origin", branch, ...)` with `branch` empty/`None`. If `branch` is `None`,
`subprocess.run`'s argv contains a `None` element and raises `TypeError`; `_git` only catches
`subprocess.TimeoutExpired` and `FileNotFoundError`, so the exception escapes
`_apply_push_request` entirely and propagates into `tick()`'s row loop — taking down the
whole tick for every run on that workflow, not just the one row with the missing branch name.

**Why this is easy to miss:** the other four failure returns in the same function each have
an *externally visible trigger* a test naturally reaches for — a missing file, a fake
subprocess returning nonzero. The branch-name check's trigger is a *row field a shared test
helper defaults away*, so writing a test for it means noticing the helper's default and
deliberately overriding it — there's no natural prompt to do that while writing tests for the
other four branches, since none of them depend on `branch_name` being empty.

**Guard form that survives:** when a function has N sibling early-return error branches,
check each one is reached by at least one test that supplies the SPECIFIC field value that
trips it — don't infer coverage from "the function has tests" or "the other branches are
tested". Here: a test that overrides the shared row helper's `branch_name` to `""`, asserts
the exact refusal return, and — since the failure mode if the guard is dead-coded is an
uncaught exception rather than a different return value — also proves `subprocess.run` is
never called at all (a fake that raises `AssertionError` on any call), so the guard is
verified to run before any shell-out, not just to change the return value after one.

**Found:** `chela/dispatcher.py`'s `_apply_push_request` (CMX-368 round 5, PR #512) — judge
mutation `if not branch:` → `if False and not branch:` in a throwaway checkout,
`CHELA_REQUIRE_JS_TESTS=1 uv run pytest -q` green (4046 passed, 0 failed) under the
corruption. Closed by
`test_apply_push_request_refuses_when_the_row_has_no_branch_name` in
`tests/test_dispatcher_push_request.py`.
