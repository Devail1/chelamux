## 305. A CI wiring fix shipped as plain YAML with no guard reading the file

**Assertion form:** none. CMX-305 fixed `.github/workflows/ci.yml` — `fetch-depth: 0` plus a
step renaming the detached `pull_request` HEAD to the PR's own branch — and shipped
`--no-new-guards`, on the reasoning that "no production guard/test code was changed, only the
CI workflow config." Nothing in the suite parses the workflow file at all.

**Mutation that defeats it:** revert either half independently — drop `fetch-depth` back to
`1`, or replace the HEAD-renaming step's `run:` with a no-op like `git rev-parse --abbrev-ref
HEAD`:

```diff
-       - uses: actions/checkout@v4
-         with:
-           fetch-depth: 0
+       - uses: actions/checkout@v4
+         with:
+           fetch-depth: 1
```

```diff
-         run: git checkout -B "${GITHUB_HEAD_REF:-$GITHUB_REF_NAME}"
+         run: git rev-parse --abbrev-ref HEAD
```

Either one alone puts `tests/test_judge.py::test_defeat_shapes_added_files_are_numbered_by_
branch_task_id` back to silently SKIPPING at the CI gate (no resolvable `origin/dev`, or no
derivable CMX branch number) — the precise defect CMX-305 exists to fix — and
`CHELA_REQUIRE_JS_TESTS=1 uv run pytest -q` stayed green (3211 passed) through both, because
no test in the suite ever reads `.github/workflows/ci.yml`.

**Why this slips through:** "it's only CI config, not production code" reads as a legitimate
reason to skip a guard, but this repo already measured that reasoning as false once before —
`tests/test_release_workflow.py` exists precisely because `.github/workflows/release.yml` had
the same status and the same blind spot, and a judge round corrupted two of its stated
invariants without the suite noticing. A workflow file is code that runs unattended at a gate
nothing else exercises; "config, not logic" is not a property that exempts a file from being
guarded, it's the reason the *previous* config file needed one.

**Guard form that survives:** `yaml.safe_load` the workflow file and assert its invariants
directly against the parsed structure, in the style of `tests/test_release_workflow.py`'s
`_step_running` helper — pin the checkout step's `with.fetch-depth` to the exact integer `0`
(not merely "a fetch-depth key exists," which a shallower value also satisfies), and assert
there is exactly one step whose `run:` references the branch-naming variable and that it is
positioned before the Pytest step. See `tests/test_ci_workflow.py`.

**Found:** PR #379 (CMX-305, rework round 1) — both mutations above, applied by the judge to
a throwaway checkout of the PR's head, stayed green against `CHELA_REQUIRE_JS_TESTS=1 uv run
pytest -q` (3211 passed, 0 failed). Closed by adding `tests/test_ci_workflow.py`, asserting
both invariants directly against the parsed workflow file.
