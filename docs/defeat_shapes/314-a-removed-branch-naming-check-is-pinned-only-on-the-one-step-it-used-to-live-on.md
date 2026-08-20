## 314. A removed branch-naming check is pinned only on the one step it used to live on

**Assertion form:** CMX-305's ref-state-assertion step originally hard-failed CI unless
`git rev-parse --abbrev-ref HEAD` matched `cmx-[0-9]+` — but `tests/test_judge.py`'s own
CMX-301 guard treats every non-`cmx-N` branch (`dev`, `main`, `release/*`, a docs branch, ...)
as a legitimate, tested SKIP, not a fault
(`test_cmx_task_number_from_branch_parses_or_gives_a_loud_reason`). The naming half of the CI
check turned that designed skip into a hard-red build for every such PR, including the `dev`
-> `main` 0.8.0 release-promotion PR, live-failing in production. CMX-314 fixed it by dropping
the naming half from the ref-state step's `run:` and re-pinning that ONE step's exact text
(`tests/test_ci_workflow.py::test_ref_state_is_asserted_immediately_before_pytest`,
`expected_run`).

**Mutation that defeats it:** reintroduce the exact removed check in a DIFFERENT step of the
same job — either a brand-new one, or folded into an existing, unpinned step's `run:` so no
new step name appears in the diff at all:

```diff
       - name: Ruff
         run: uv run ruff check chela tests
+
+      - name: Assert the branch is a task branch
+        run: |
+          grep -qiE 'refs/heads/cmx-[0-9]+$' .git/HEAD
```

```diff
       - name: Ruff
-        run: uv run ruff check chela tests
+        run: |
+          uv run ruff check chela tests
+          grep -qiE 'refs/heads/cmx-[0-9]+$' .git/HEAD
```

Both reproduce the exact production regression CMX-314 exists to fix — every PR opened from a
legitimately-named non-`cmx-N` branch goes red again — while the ref-state step's pinned
`run:`, its `if:`-absence, its position immediately before Pytest, and every other assertion
in `tests/test_ci_workflow.py` stay byte-for-byte untouched. Applied by the judge to a
throwaway checkout of PR #392's head, `CHELA_REQUIRE_JS_TESTS=1 uv run pytest -q` stayed green
(3300 passed, 0 failed, 0 error(s)) through both.

**Why this slips through:** the fix for a bad invariant is naturally written as "un-pin what
the previous round pinned," and the test that proves the fix is naturally written as "assert
this one step's text no longer contains it" — both are true statements about the step the bug
used to live on. Neither is a statement about the rest of the job, so nothing stops the same
check from moving one step sideways. This is the same generalization gap
`09-a-behavior-changing-fix-shipped-with-no-guard-at-all.md` and the round-6 fix in
[`305-a-ci-wiring-fix-shipped-as-plain-yaml-with-no-guard-reading-the-file.md`](305-a-ci-wiring-fix-shipped-as-plain-yaml-with-no-guard-reading-the-file.md)
already named for `continue-on-error`: a check scoped to one named step is only as good as
the assumption that no other step in the job could carry the same behavior — an assumption
that a config file with a dozen `run:` blocks in one job never actually guarantees.

**Guard form that survives:** scan every step's `run:` text for the removed invariant, not
just the one step it used to live on — the same single-step -> whole-job generalization
`test_no_step_in_the_test_job_swallows_its_own_failure` already applies to
`continue-on-error`. `tests/test_ci_workflow.py::test_no_step_reasserts_a_cmx_branch_naming_
convention` regex-matches every step's `run:` for a `cmx-N`-shaped naming assertion
(`cmx` followed within a few characters by a digit-class token — `[0-9]`, `\d`, or `0-9`),
independent of which step name or position carries it, and fails loudly if any step does.

**Found:** PR #392 (CMX-314, rework round 1) — both mutations above, applied by the judge to
a throwaway checkout of the PR's head, stayed green against `CHELA_REQUIRE_JS_TESTS=1 uv run
pytest -q` (3300 passed, 0 failed, 0 error(s)). Closed by adding
`test_no_step_reasserts_a_cmx_branch_naming_convention` to `tests/test_ci_workflow.py`.
