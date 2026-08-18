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

**Round 3 — the guard that closed round 2 still checked wording and step-identity, not the
command's exact text or the state at the point of use:**

```diff
-         run: git checkout -B "${GITHUB_HEAD_REF:-$GITHUB_REF_NAME}"
+         run: echo git checkout -B "${GITHUB_HEAD_REF:-$GITHUB_REF_NAME}"
```

`test_head_is_renamed_to_the_pr_branch_before_pytest` asserted `"git checkout -B" in
rename["run"]` — a substring. Prefixing the command with `echo` leaves the needle
(`GITHUB_HEAD_REF`), the substring (`git checkout -B`), the unconditional `if`, and the
ordering all untouched, while the step only *prints* the command instead of running it. HEAD
stays detached and the CMX-301 guard keeps skipping at the gate.

```diff
-       - name: Install uv
+       - name: Pin the exact commit under test
+         run: git checkout --detach HEAD
+
+       - name: Install uv
```

Round 2's `test_exactly_one_checkout_step` generalized the "exactly one" doctrine, but only
to steps with `uses: actions/checkout@...`. A later plain `run:` step re-detaching HEAD is a
different mechanism: it has no `uses:` (checkout count stays 1), doesn't mention
`GITHUB_HEAD_REF` (rename count stays 1), and doesn't mention `uv run pytest` (ordering
unchanged) — so every assertion round 2 wrote stays green while HEAD is detached again by
the time the CMX-301 guard runs.

Both mutations, applied by the judge to a throwaway checkout of PR #379's round-2 head,
stayed green against `CHELA_REQUIRE_JS_TESTS=1 uv run pytest -q` (3221 passed, 0 failed).

**Why this slips through (round 3):** the first mutation is the same root shape as
`01-presence-substring-assertion-defeated-by-dead-coding.md` and
`32-a-substring-assertion-pins-text-unchanged-by-the-revert.md` — a substring match pins
that certain text occurs somewhere in the string, not that the string *is* that text, so
wrapping it in another command satisfies the assertion while changing what actually
executes. The second mutation is `25-a-shape-s-own-prescribed-fix-is-applied-fully-at.md`:
round 2's own prescribed fix — "pin exactly one of a kind, so a second copy added later
can't go unchecked" — was applied only at the one call site (`actions/checkout` steps) it
was written for, rather than generalized to the invariant it was actually protecting: the
ref must still name the PR branch at the moment Pytest runs, and *anything* that touches the
ref after the rename can violate that, not only a second `actions/checkout`.

**Guard form that survives (round 3):** pin the rename step's `run:` to the exact expected
command (`rename["run"].strip() == 'git checkout -B "${GITHUB_HEAD_REF:-$GITHUB_REF_NAME}"'`),
not a substring of it — the same treatment `fetch-depth` already gets. And assert the state
at the point of use rather than the step in isolation: no step positioned between the rename
and Pytest may run a ref-mutating git command (`git checkout`, `git switch`, `git reset`)
(`test_nothing_between_the_rename_and_pytest_touches_head`).

**Found:** PR #379 (CMX-305, rework round 3) — both mutations above, applied by the judge to
a throwaway checkout of the round-2 fix, stayed green (3221 passed, 0 failed). Closed by
tightening `test_head_is_renamed_to_the_pr_branch_before_pytest` to an exact-match assertion
and adding `test_nothing_between_the_rename_and_pytest_touches_head` to
`tests/test_ci_workflow.py`.

**Round 4 — the guard that closed round 3 named the state-at-point-of-use invariant it
wanted but implemented it as an enumerated denylist, and never checked the job's own `if:`:**

```diff
-       - name: Install uv
+       - name: Record the exact ref under test
+         run: git branch -m ci-head
+
+       - name: Install uv
```

```diff
-       - name: Install uv
+       - name: Drop the fetch remote (self-contained build)
+         run: git remote remove origin
+
+       - name: Install uv
```

`test_nothing_between_the_rename_and_pytest_touches_head` (round 3's fix) rejected three
literal ref-mutating verbs — `git checkout`, `git switch`, `git reset` — in the window
between the rename and Pytest. `git branch -m ci-head` renames the current branch without
detaching HEAD; `git rev-parse --abbrev-ref HEAD` afterward returns `ci-head`, not a `cmx-NNN`
name, so `test_defeat_shapes_added_files_are_numbered_by_branch_task_id` skips at its gate
again — and the command matches none of the three denylisted strings. `git remote remove
origin` deletes `refs/remotes/origin/*`, so the CMX-301 guard's `origin/dev` diff fails to
resolve — the exact half of the original defect this PR exists to fix — and it, too, matches
none of the three.

```diff
   test:
+    if: false
     runs-on: ubuntu-latest
```

`test_head_rename_step_is_unconditional` (round 2's fix) pins the rename STEP's `if:` —
but the job carries the identical switch one level up. `jobs.test.if: false` means no step
in the file runs at all, the rename included, while the step itself keeps its exact `run:`,
no `if:` key, and its position before Pytest — every step-level assertion in the file stays
green. A skipped job doesn't fail the workflow run, so CI reports success with the CMX-301
guard never having executed at all.

All three mutations, applied by the judge to a throwaway checkout of PR #379's round-3 head,
stayed green against `CHELA_REQUIRE_JS_TESTS=1 uv run pytest -q` (3222 passed, 0 failed).

**Why this slips through (round 4):** the first two are
`25-a-shape-s-own-prescribed-fix-is-applied-fully-at.md` again, on a new axis — round 3's own
docstring stated the invariant as "HEAD names the PR branch when Pytest starts," then
implemented it as a denylist of the three verbs the round-3 mutation happened to use, rather
than a ban on the underlying tool. A denylist of verbs is only as complete as its
enumeration; naming the invariant in prose does not make the assertion enforce that invariant
in general. The third is `01-presence-substring-assertion-defeated-by-dead-coding.md` hoisted
one level: a presence-and-wording check on a step is blind to a dead-coding switch placed on
its *parent* rather than on itself, the same gap round 2 closed at the step level without
generalizing to every level a step lives inside.

**Guard form that survives (round 4):** ban the tool, not the verb — no step positioned
between the rename and Pytest may invoke `git` at all (`re.compile(r"(?<![\w.-])git(?![\w.-
])")` against each `run:` in that window), so a future command needs no prediction of which
subcommand it'll use. And check `if:`-absence at every level a dead-coding switch could sit,
not only the step: `test_job_is_unconditional` asserts `"if" not in job`, the same doctrine
`test_head_rename_step_is_unconditional` already applies one level down.

**Found:** PR #379 (CMX-305, rework round 4) — all three mutations above, applied by the
judge to a throwaway checkout of the round-3 fix, stayed green (3222 passed, 0 failed).
Closed by broadening `test_nothing_between_the_rename_and_pytest_touches_head` from a
three-verb denylist to a blanket `git`-invocation ban, and adding `test_job_is_unconditional`
to `tests/test_ci_workflow.py`.

**Round 5 — the guard that closed round 4 generalized "no `if:`" and "no `git`" as far as
rounds 1-4's own mutations reached, but not one step further, and pinned a command's text
without ever pinning what that text expands to:**

```diff
-       - name: Pytest
+       - name: Pytest
+         if: false
```

Rounds 2 and 4 added `if:`-absence checks for the rename step and for the job — but never
for Pytest itself, the one step whose EXECUTION is the entire point of this file, and the
one step `_step_running(steps, "uv run pytest")` already anchors on. `if: false` here
leaves the needle, the `run:`, and the ordering all untouched; a skipped step doesn't fail
a workflow run, so CI reports green having executed zero tests, the CMX-301 guard included.

```diff
+         env:
+           GITHUB_HEAD_REF: "${{ github.ref_name }}"
          run: git checkout -B "${GITHUB_HEAD_REF:-$GITHUB_REF_NAME}"
```

Round 3's exact-match assertion pins the rename command's TEXT, not its INPUT. A step-level
`env:` overriding `GITHUB_HEAD_REF` leaves that text byte-identical — so the exact-match
assertion, the ordering, the git-ban window, and every `if:`-absence check all stay green —
while on a `pull_request` event `github.ref_name` is `379/merge`, not the PR's own branch,
so the rename checks out the WRONG ref and the CMX-301 guard's own branch-number parse
fails downstream.

```diff
-       - name: Name the checked-out ref
+       - name: Prune the fetch remote (keep the build self-contained)
+         run: git remote remove origin
+
+       - name: Name the checked-out ref
```

Round 4's git-ban window starts at the rename step, but the fetch-depth invariant's own
exposure starts at the CHECKOUT — the segment between checkout and rename was never
scanned. The identical `git remote remove origin` that round 4's ban already catches
*after* the rename, moved a few lines *earlier*, sits entirely outside the banned window
and goes unnoticed.

All three mutations, applied by the judge to a throwaway checkout of PR #379's round-4
head, stayed green against `CHELA_REQUIRE_JS_TESTS=1 uv run pytest -q` (3223 passed, 0
failed).

**Why this slips through (round 5):** the first and third are
`25-a-shape-s-own-prescribed-fix-is-applied-fully-at.md` for a fourth time running — each
round's own doctrine ("no `if:` at any level a switch could sit", "the window an invariant
needs, not the window a previous mutation happened to use") was applied to every site the
*previous* mutation touched, but not generalized to the *next* site the same doctrine
already implied. The second is `05-asserting-a-source-constant-instead-of-the-rendered-
value.md`: pinning a command's source text is not the same as pinning what it resolves to
at runtime, and `env:`, `working-directory:`, `shell:` and the runner's own defaults all
sit outside any string comparison written against the `run:` field.

**Guard form that survives (round 5):** `test_pytest_step_is_unconditional` extends the
`if:`-absence doctrine to the Pytest step itself (plus `continue-on-error`, which reaches
the same silently-swallowed-failure end state through a different key).
`test_nothing_between_the_checkout_and_pytest_touches_git_except_the_pinned_steps` widens
the git-ban window to `[checkout+1 : pytest)` — a strict superset of the old
rename-to-Pytest window — exempting only the rename step and a new ref-state-assertion
step, both pinned exactly elsewhere. For the env-override mutation, two guards close it
from different angles: `test_nothing_redefines_the_rename_steps_env_vars` bans an `env:`
key defining `GITHUB_HEAD_REF` or `GITHUB_REF_NAME` at the workflow, job, OR step level
(closing this shape completely at the YAML-structure level, since a workflow- or job-level
override reaches the rename step exactly as a step-level one would); and a new CI step,
"Assert the ref state the CMX-301 guard needs", runs `git rev-parse --abbrev-ref HEAD |
grep -qiE '^cmx-[0-9]+$'` and `git rev-parse --verify --quiet origin/dev` immediately
before Pytest — asserting the STATE the CMX-301 guard actually needs at the point it needs
it, rather than one more property of the YAML that predicts how a future mutation might
disturb that state. That step is what stops this shape's four-round drift (verb → spelling
→ level → window boundary → environment): whatever the next mutation's spelling, a bad ref
at the point of use is a red CI build, not a silent skip three steps later — though it can
only observe that at actual CI runtime, not in this local suite, which is why the
YAML-structural `env:` ban above still carries the local, self-check-verifiable half of the
fix. (Even the state-assertion step is not unconditionally complete: a step that writes to
`$GITHUB_ENV` to redefine either variable for a later step, rather than setting `env:`
directly, matches no YAML-level denylist — the runtime assertion is the backstop for
exactly that residual case, and no known guard closes it at the YAML level.)

**Found:** PR #379 (CMX-305, rework round 5) — all three mutations above, applied by the
judge to a throwaway checkout of the round-4 fix, stayed green (3223 passed, 0 failed).
Closed by adding `test_pytest_step_is_unconditional`,
`test_nothing_redefines_the_rename_steps_env_vars`, and a "ref state" CI step verified by
`test_ref_state_is_asserted_immediately_before_pytest`, and by widening
`test_nothing_between_the_rename_and_pytest_touches_head` (renamed
`test_nothing_between_the_checkout_and_pytest_touches_git_except_the_pinned_steps`) to the
checkout-to-Pytest window, in `tests/test_ci_workflow.py`.

**Found (round 4, unchanged text below):** PR #379 (CMX-305, rework round 4) — all three
mutations above, applied by the judge to a throwaway checkout of the round-3 fix, stayed
green (3222 passed, 0 failed). Closed by broadening
`test_nothing_between_the_rename_and_pytest_touches_head` from a three-verb denylist to a
blanket `git`-invocation ban, and adding `test_job_is_unconditional`
to `tests/test_ci_workflow.py`.
