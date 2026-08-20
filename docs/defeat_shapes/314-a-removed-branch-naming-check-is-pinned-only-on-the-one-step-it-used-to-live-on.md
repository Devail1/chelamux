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

**Guard form that survives:** don't enumerate how the naming check might be SPELLED — ban
the literal substring it can't be written without. No step's `run:` in this job has any
legitimate reason to mention the string "cmx" at all: the genuine ref-state check
CMX-305/CMX-314 need is expressed without it (`git rev-parse --abbrev-ref HEAD` compared
against `HEAD`, not against a naming convention). `tests/test_ci_workflow.py::test_no_step_
reasserts_a_cmx_branch_naming_convention` now asserts `"cmx" not in step["run"].lower()` for
every step, with an explicit `_CMX_LITERAL_ALLOWLIST` (empty today, one-line reason per
entry — the same shape `_CONTINUE_ON_ERROR_ALLOWLIST` already uses) for the day a step gets
a legitimate reason to say it. This is a strict superset of the old regex: it closes both
round-1 mutations above and every other digit-class spelling in one line, because all of
them still have to name the branch prefix they're checking for. The residual it does NOT
close — a naming check written without the literal string, e.g. `grep -qE '^ref: refs/
heads/[a-z]+-[0-9]+$' .git/HEAD` — is a text-rule-over-a-config-language gap that no `run:`
substring ban can close; the durable fix is asserting the ref-state step's BEHAVIOUR (run
its `run:` block, read out of the YAML, against throwaway repos on `cmx-999`/`dev`/`main`/
`release/*`/a detached HEAD and check the exit codes) rather than its text at all — see
`05-asserting-a-source-constant-instead-of-the-rendered-value.md`. Left as a non-blocking
follow-up rather than required here, since no mutation exercising that residual has been
found yet.

**Found:** PR #392 (CMX-314). Round 1: the two step-relocation mutations above the fold,
applied by the judge to a throwaway checkout of the PR's head, stayed green against
`CHELA_REQUIRE_JS_TESTS=1 uv run pytest -q` (3300 passed, 0 failed, 0 error(s)); closed by
adding `test_no_step_reasserts_a_cmx_branch_naming_convention`. Round 2: the two
digit-spelling mutations above, applied the same way, also stayed green (3301 passed, 0
failed, 0 error(s)); closed by replacing the digit-class denylist with the literal-substring
ban described above.
