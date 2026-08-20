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

**Round 1's own fix had a second gap, closed in round 2:** scanning every step's `run:` for
the removed invariant still has to recognize the invariant when it sees it, and round 1's
first cut (`_CMX_NAMING_ASSERTION = re.compile(r"cmx.{0,6}(\[0-9\]|\\d|0-9)", re.IGNORECASE)`)
did that by enumerating three digit-class token spellings — `[0-9]`, `\d`, `0-9` — the exact
"guessed list of subcommands" shape this same file's own `test_nothing_between_the_checkout_
and_pytest_touches_git_except_the_pinned_steps` (round 4) already ruled out for `git` verbs:
"Ban `git` itself in this window, not a guessed list of its subcommands, so no future verb
needs to be predicted in advance." Round 1 reached for the guessed-list form anyway, one
level down. The judge defeated it twice in the same round, with the POSIX class
`[[:digit:]]` and with a bare `case */cmx-*)` shell glob carrying no digit-class token at
all:

```diff
       - name: Ruff
         run: uv run ruff check chela tests
+        run: |
+          uv run ruff check chela tests
+          grep -qE '^ref: refs/heads/cmx-[[:digit:]]+$' .git/HEAD
```

```diff
       - name: Ruff
         run: uv run ruff check chela tests
+
+      - name: Assert the branch is a task branch
+        run: |
+          case "$(cat .git/HEAD)" in */cmx-*) ;; *) exit 1 ;; esac
```

Both dodge every digit-class token the regex enumerated while reproducing the identical
CMX-314 regression; `[1-9]`, `[[:alnum:]]`, or no digit-class token whatsoever were all just
as available to the round after this one. A denylist of spellings — even a whole-job one —
never converges; it just moves the enumeration from "which step" to "which spelling."

**Round 2's own fix had two more gaps, both closed in round 3 — and this time neither
mutation contains the string "cmx" at all:**

```diff
       - name: Ruff
-        run: uv run ruff check chela tests
+        run: |
+          uv run ruff check chela tests
+          grep -qE '^ref: refs/heads/[a-z]+-[0-9]+$' .git/HEAD
```

```diff
       - name: Ruff
         run: uv run ruff check chela tests
+
+      - name: Assert the branch is a task branch
+        if: ${{ !startsWith(github.head_ref, 'cmx-') }}
+        run: exit 1
```

The first spells the branch-prefix check as a generic task-branch shape (`[a-z]+-[0-9]+`)
folded into the existing, unpinned Ruff step — no "cmx" substring, no new step name in the
diff. The second moves the check entirely into a new step's `if:` expression, which
`test_no_step_reasserts_a_cmx_branch_naming_convention` never read (it only looked at
`step["run"]`); `run: exit 1` needs no "cmx" substring because the branch match already
happened in the condition. Both reproduce the identical CMX-314 production regression while
every existing assertion — the pinned ref-state step's exact `run:`, its `if:`-absence, its
position immediately before Pytest, the git-ban window, every `continue-on-error` check —
stays byte-for-byte untouched. The round-2 catalog text above called the literal-"cmx" ban "a
strict superset of the old regex ... because all of them still have to name the branch
prefix they're checking for" — that was wrong; a config language has more surface than a
`run:` string, and the branch prefix can be named as a character class or hidden in a key
the guard doesn't read at all.

**Guard form that survives:** (round 3 — the round-2 form below no longer does, see round 4
below for what round 3's own fix still missed) stop
denylisting what a step's `run:` (or any other
key) may CONTAIN, and allowlist what the job's STEPS ARE. `tests/test_ci_workflow.py::
test_the_step_list_is_pinned_exactly` now pins the WHOLE ordered step list — every step's
`name`, `uses`, and exact `run:` text, compared with `==` against a literal table
(`_EXPECTED_STEPS`) — generalizing the exact-value doctrine invariant 14 already applies to
the checkout step's `with:` block and invariant 13 to Pytest's command, one level up, to the
entire job at once. Folding a check into Ruff's `run:` changes Ruff's pinned entry (red). A
brand-new step changes the list's length and every later step's position (red) — regardless
of what its `if:` says, since the parsed step list contains the step whether or not it ever
executes. No future spelling, YAML key, or step position needs to be predicted in advance,
because nothing may be added, removed, reordered, or edited without a visible diff here. This
subsumes the literal-"cmx" ban (and its `_CMX_LITERAL_ALLOWLIST`) entirely: any step whose
`run:` mentions "cmx" is, by construction, a step whose entry in `_EXPECTED_STEPS` no longer
matches, the same as any other unpinned edit wouldn't.

The one thing a full step-list pin still doesn't reach — the same residual round 2's entry
already named — is a mutation to the PINNED ref-state step's own `run:` text that keeps its
character count and shape but changes what it does when the shell actually runs it (e.g. an
off-by-one in the comparison operator). That is a source-constant-vs-rendered-value gap
(`05-asserting-a-source-constant-instead-of-the-rendered-value.md`), not a step-identity gap,
and remains a non-blocking follow-up: run the ref-state step's `run:` block, read out of the
YAML, against throwaway repos on `cmx-999`/`dev`/`main`/`release/*`/a detached HEAD and check
the exit codes, rather than comparing its text at all.

**Round 3's own fix had two more gaps, both closed in round 4 — this time not in WHAT the
guard pinned, but in the SCOPE of what "the job" it pinned was taken to mean:**

```diff
           env:
             CHELA_REQUIRE_JS_TESTS: "1"
           run: uv run pytest -q
+
+  branch-name:
+    runs-on: ubuntu-latest
+    steps:
+      - name: Assert the branch is a task branch
+        run: |
+          echo "${{ github.head_ref }}" | grep -qiE '^cmx-[0-9]+$'
```

```diff
       - uses: actions/checkout@v4
+        if: ${{ startsWith(github.head_ref, 'cmx-') }}
         with:
           fetch-depth: 0
```

Every fixture in `tests/test_ci_workflow.py` resolves through `workflow["jobs"]["test"]` —
`_EXPECTED_STEPS` pins that ONE job's step list, and nothing in the repo enumerated the
workflow's JOBS themselves. The first mutation adds a SECOND job carrying the identical
`cmx-N` naming gate CMX-314 exists to remove; it reproduces the exact production regression
(every PR from `dev`/`main`/`release/*` goes red again) while `jobs.test.steps` stays
byte-for-byte identical, invisible to a pin that only ever looks inside `jobs["test"]`.

The second mutation stayed inside the pinned job and touched no step's `name`, `uses`, or
`run:` at all — round 3's pin projected each step down to exactly those three keys before
comparing, so an `if:` key added to the checkout step was invisible to it by construction.
Gating the checkout step on the branch name reproduces the CMX-314 regression by a different
mechanism: on a non-`cmx-N` branch the checkout is skipped, the very next step
(`git checkout -B "..."`) then runs in an empty workspace with no `.git` directory, and the
build goes red on a `fatal: not a git repository` instead of a grep. The pinned step table
still matched (name/uses/run all unchanged), `with:` was still exactly `{fetch-depth: 0}`,
the checkout step carries no `run:` for the git-ban window to scan, and it sits outside that
window entirely (which starts one step later) — every existing assertion in the file stayed
green.

Both mutations applied by the judge to a throwaway checkout of PR #392's head kept
`CHELA_REQUIRE_JS_TESTS=1 uv run pytest -q` green (3301 passed, 0 failed, 0 error(s)).

**Why round 3's fix still had this gap:** "pin the whole job's step list" is a true
allowlist of what's INSIDE the `test` job, but it says nothing about what's OUTSIDE it — a
workflow with more than one job routes each through GitHub Actions independently, and a
second job's failure reddens the run exactly as the first job's would. Likewise "pin every
step's `name`/`uses`/`run`" is a true allowlist of three specific keys, but a YAML mapping
has more keys than that — `if:`, `env:`, `continue-on-error:`, `shell:`,
`working-directory:` — and a projection that only reads three of them can't see a fourth
one appear. Both gaps are the same shape this file's very first "Why this slips through"
section already named for a check scoped to one named STEP: an allowlist is only as
complete as the boundary drawn around what it encloses, and both round 3 pins drew that
boundary one level too narrow — the job set, and the step's own key set.

**Guard form that survives (round 4):** widen both boundaries by the same move that closed
round 3 — stop projecting down to a chosen subset and pin the WHOLE THING, one level out
each time. `tests/test_ci_workflow.py::test_the_workflow_has_exactly_one_job` asserts
`set(workflow["jobs"]) == {"test"}`, so a second job is a visible diff in the job SET, not
just inside one job's contents. `test_the_step_list_is_pinned_exactly` now compares each
step's COMPLETE parsed mapping with `==` against a literal table of full dicts, not a
`(name, uses, run)` projection of it — so `if:`, `env:`, `continue-on-error:`, or any other
key, present or absent, on any step is closed by the same assertion instead of needing its
own named test. Under this pair, the branch-name job changes the job set (red), and the
`if:` on checkout changes that step's mapping (red) — no future job name, step key, or
spelling needs to be predicted in advance, because nothing may be added anywhere in the
workflow this file reads without a visible diff here.

The `jobs.test.defaults.run.shell` override that could gate every `run:` step on the branch
name via a custom shell template, without touching any step's own keys, remains an
unconfirmed, non-blocking residual — recorded in the PR thread, not filed as an experiment,
because how the runner tokenizes a quoted multi-word custom shell string couldn't be
verified from a read-only review. A future full-job-mapping pin (matching `job == {...}`
the same way steps are now pinned, rather than the `runs-on`/`strategy`/`steps` keys pinned
individually today) would close it for free, the same way the full-step-mapping pin above
closed `if:`/`env:`/`continue-on-error:` on a step without enumerating them one by one.

**Found:** PR #392 (CMX-314). Round 1: the two step-relocation mutations above the fold,
applied by the judge to a throwaway checkout of the PR's head, stayed green against
`CHELA_REQUIRE_JS_TESTS=1 uv run pytest -q` (3300 passed, 0 failed, 0 error(s)); closed by
adding `test_no_step_reasserts_a_cmx_branch_naming_convention`. Round 2: the two
digit-spelling mutations above, applied the same way, also stayed green (3301 passed, 0
failed, 0 error(s)); closed by replacing the digit-class denylist with the literal-substring
ban described above. Round 3: the two no-"cmx"-substring mutations above, applied the same
way, stayed green (3301 passed, 0 failed, 0 error(s)) through a guard that its own round-2
writeup mistakenly called complete; closed by replacing the literal-substring ban with the
full step-list pin (`test_the_step_list_is_pinned_exactly`) described above. Round 4: the
second-job and step-`if:` mutations above, applied the same way, stayed green (3301 passed,
0 failed, 0 error(s)) through a guard that its own round-3 writeup didn't yet cover either
boundary; closed by adding `test_the_workflow_has_exactly_one_job` and widening
`test_the_step_list_is_pinned_exactly` from a `(name, uses, run)` projection to a full-
mapping `==` comparison, both described above.
