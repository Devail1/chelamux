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

**Round 2 — the guard that closed round 1 had two more gaps of the exact same shape:**

`tests/test_ci_workflow.py` (round 1's fix) checks that a step named after the rename EXISTS,
has the right `run:` string, and sits before Pytest — and that the checkout step's
`fetch-depth` is `0`. Neither check constrains whether the rename step actually **runs**, nor
whether a **later** checkout step overrides the first one's effect:

```diff
-       - name: Name the checked-out ref
+       - name: Name the checked-out ref
+         if: false
```

Dead-coding the step this way leaves its name, `run:`, and position all untouched — every
assertion round 1 wrote still passes — while the rename never executes and CI is right back
to a detached HEAD.

```diff
-       - name: Install uv
+       - uses: actions/checkout@v4
+         with:
+           fetch-depth: 1
+
+       - name: Install uv
```

`test_checkout_fetches_full_history` selects the checkout step with `next(...)` — the FIRST
match — but GitHub Actions hands the workspace to whichever checkout ran **last**. A second,
shallow checkout added later re-clones at depth 1 and re-detaches HEAD, undoing both halves
of this shape's original fix at once, invisibly to a first-match selector.

Both mutations, applied by the judge to a throwaway checkout of PR #379's round-1 head,
stayed green against `CHELA_REQUIRE_JS_TESTS=1 uv run pytest -q` (3214 passed, 0 failed).

**Why this slips through (round 2):** round 1's guard read the file and pinned real values —
a step beating "no test reads the file at all" is genuine progress — but pinning *presence,
wording, and position* is still not the same as pinning *effect*. A step that exists,
reads correctly, and sits in the right order can still be switched off (`if: false`) or have
its outcome silently overridden by a step added later that the selector never looks past.
This is the same root shape as `01-presence-substring-assertion-defeated-by-dead-coding.md`
(a live step vs. a dead-coded one look identical to a presence/wording check) combined with
the `_step_running` doctrine the file's own docstring states but, this time, didn't apply to
the checkout step it's actually about: "exactly one … so a second copy added later can never
be the one silently going unchecked."

**Guard form that survives (round 2):** assert the rename step carries no `if:` key at all
(`test_head_rename_step_is_unconditional`) — an unconditional step is the invariant, so any
future condition on it becomes a deliberate, visible diff instead of a silent skip. And
assert there is **exactly one** `actions/checkout` step before pinning its `fetch-depth`
(`test_exactly_one_checkout_step`), in the same shape `_step_running` already uses for `run:`
steps, so a second checkout added later fails loudly instead of leaving the first,
now-irrelevant one still looking correct.

**Found:** PR #379 (CMX-305, rework round 2) — both mutations above, applied by the judge to
a throwaway checkout of the round-1 fix, stayed green (3214 passed, 0 failed). Closed by
adding `test_head_rename_step_is_unconditional` and `test_exactly_one_checkout_step` to
`tests/test_ci_workflow.py`.
