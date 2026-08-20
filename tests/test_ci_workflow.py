"""`.github/workflows/ci.yml` states two invariants CMX-305 added; nothing read the file.

The judge corrupted both on this PR's head — dropping `fetch-depth: 0` to `fetch-depth: 1`,
and replacing the HEAD-renaming step's `run:` with a no-op `git rev-parse --abbrev-ref HEAD`
— and the whole 3211-test suite stayed green through both. A pull_request-triggered CI run
checks out a detached merge commit by default; without both fixes,
`tests/test_judge.py::test_defeat_shapes_added_files_are_numbered_by_branch_task_id` SKIPS
at the gate instead of running (its own branch-name and origin/dev preconditions unmet) — a
hand-opened PR carrying a mis-numbered defeat-shape entry sails straight past it, silently.
That is precisely the gap CMX-305 exists to close, so the fix is asserted here against the
workflow file itself rather than left to a human rereading the YAML.

The two:

1. The checkout step fetches full history — `fetch-depth: 0` exactly, not merely present —
   so `origin/dev` is resolvable for the CMX-301 guard's diff.
2. Exactly one step renames the checked-out HEAD to the PR's own branch (derived from
   `GITHUB_HEAD_REF`), and it runs before the Pytest step — so `git rev-parse --abbrev-ref
   HEAD` resolves to `cmx-NNN` instead of `HEAD` by the time the guard runs.

Rework round 1 pinned the rename step by NAME and its position, and selected the checkout
step with `next(...)` — the FIRST match. The judge's round-1 verdict showed both of those are
gaps, not the invariant itself:

3. The rename step must actually RUN — dead-coding it with `if: false` leaves its name, its
   `run:`, and its position before Pytest untouched, so a presence-and-ordering check alone
   cannot see it. Round 1's own `_step_running` docstring already argues this: "exactly one,
   so a second copy added later can never be the one silently going unchecked" — the same
   doctrine applies to a step that's still present but switched off.
4. There must be EXACTLY one `actions/checkout` step. GitHub Actions hands the workspace to
   whichever checkout ran LAST, not the first one in the file — a `next(...)` selector that
   grabs the first match can be defeated by a second, shallow checkout added later (e.g.
   right before "Install uv"), which re-detaches HEAD while this test keeps inspecting the
   original, now-irrelevant, first step.

Round 2's own fixes had two more gaps of the same shape, both closed in round 3:

5. The rename step's `run:` must be pinned EXACTLY, not by substring. `"git checkout -B" in
   rename["run"]` is satisfied by `echo git checkout -B "..."` — the needle, the substring,
   the unconditional `if`, and the ordering are all untouched while the step only prints the
   command instead of running it.
6. The rename must still be in effect WHEN PYTEST RUNS, not merely have happened at some
   earlier step. Round 2's exactly-one doctrine was applied only to `actions/checkout`
   steps; a later plain `run: git checkout --detach HEAD` step has no `uses:` (checkout
   count stays 1), doesn't mention `GITHUB_HEAD_REF` (rename count stays 1), and doesn't
   mention `uv run pytest` (ordering unchanged) — so it re-detaches HEAD invisibly to every
   assertion that existed before round 3.

Round 3's own fix had two more gaps of the same shape, both closed in round 4:

7. "No ref-mutating command" was written as a three-item denylist (`git checkout`, `git
   switch`, `git reset`) instead of the state-at-point-of-use invariant its own docstring
   named. `git branch -m ci-head` renames the current branch without matching any of the
   three — HEAD stops naming `cmx-NNN` without ever being detached. `git remote remove
   origin` deletes the remote-tracking refs the CMX-301 guard's `origin/dev` diff needs,
   also without matching any of the three. Both are `git` invocations in the window between
   the rename and Pytest; neither is one of the three enumerated verbs. The fix generalizes
   from a denylist of verbs to a ban on `git` itself: nothing between the rename and Pytest
   may invoke `git` at all, so a future command needs no prediction of *which* verb it'll
   use.
8. The rename step's own `if:`-absence (`test_head_rename_step_is_unconditional`) was never
   checked at the level above it. `jobs.test.if: false` dead-codes every step in the file —
   the rename included — while the step itself keeps its exact `run:`, no `if:` key, and its
   position before Pytest; every existing assertion stays green. A skipped job doesn't fail
   the workflow run, so CI reports green with the CMX-301 guard never having executed at
   all.

Round 4's own fix had three more gaps of the same shape, all closed in round 5:

9. Every `if:`-absence check landed on the rename step and the job, never on the step whose
   EXECUTION is the entire point of this file: Pytest itself. `if: false` on the Pytest step
   leaves the needle (`uv run pytest`), the `run:`, and the ordering (`index(rename) <
   index(pytest)`) byte-identical — a skipped step doesn't fail a workflow run, so CI reports
   green having executed zero tests, the CMX-301 guard included.
10. `test_head_is_renamed_to_the_pr_branch_before_pytest` pins the rename command's TEXT
    exactly, but never its INPUT. A step-level `env: GITHUB_HEAD_REF: "${{ github.ref_name
    }}"` leaves the `run:` byte-identical (GITHUB_-prefixed env keys do take effect —
    `GITHUB_TOKEN` is the ubiquitous precedent), so the exact-match assertion, the ordering,
    the git-ban window, and every `if:`-absence check all stay green — while on a
    `pull_request` event `github.ref_name` is `379/merge`, not `cmx-305`, so the rename
    checks out the WRONG branch and the CMX-301 guard's own branch-number parse fails.
11. The git-ban window started at the rename step, but the fetch-depth invariant's exposure
    starts at the CHECKOUT — the segment between checkout and rename was unguarded. `git
    remote remove origin`, moved a few lines earlier (before the rename instead of after),
    deletes `refs/remotes/origin/*` outside the banned window entirely; nothing between
    checkout and rename was ever scanned.

No amount of enumerating one more property closes this class — verb, spelling, level,
window boundary, and now environment have each, in turn, been the property the previous
round's assertion didn't cover. Round 5 stops chasing properties of the YAML and instead
asserts the STATE the CMX-301 guard actually needs, AT THE POINT PYTEST RUNS: a new step,
"Assert the ref state the CMX-301 guard needs", runs `git rev-parse --abbrev-ref HEAD | grep
-qiE '^cmx-[0-9]+$'` and `git rev-parse --verify --quiet origin/dev` immediately before
Pytest — the same `cmx-N` pattern `tests/test_judge.py::_cmx_task_number_from_branch` itself
parses. Whatever mutation disturbs the ref (a rename, a dropped remote, a re-detach, a
second checkout, ...) now fails IN CI, at the point of use, rather than leaving the CMX-301
guard to skip quietly three steps later. This is asserted here as: the step exists exactly
once, is unconditional, sits immediately before Pytest, and its `run:` is pinned exactly —
plus the git-ban window is widened to start at the checkout (a superset of the
rename-to-Pytest window), exempting only the rename step and this new assertion step, both
of which are independently pinned exactly elsewhere in this file.

Round 7 (CMX-314): round 5's own `run:` was itself wrong, not defeated — the `^cmx-[0-9]+$`
half of the assertion enforced a branch-NAMING convention, but `_cmx_task_number_from_branch`
treats every non-`cmx-N` branch (`dev`, `main`, `release/*`, a docs branch, ...) as a
legitimate skip, by design and by its own test
(`test_cmx_task_number_from_branch_parses_or_gives_a_loud_reason`). Asserting the branch name
here turned that designed skip into a hard CI failure for every PR not opened from a `cmx-N`
branch — including the `dev` -> `main` release-promotion PR, which this exact step broke in
production. The fix drops the naming check and keeps only what CMX-305 actually needed: HEAD
attached to a real branch (not the detached default of a `pull_request` checkout) and
`origin/dev` resolvable.

Round 7's own fix had one more gap, closed here in round 8: it removed the `cmx-[0-9]+`
naming check from the ONE step round 5 pinned (`test_ref_state_is_asserted_immediately_
before_pytest`'s `expected_run`), but nothing stops the identical check from being
reintroduced in a DIFFERENT step — either a brand-new one, or folded into an existing,
unpinned step's `run:` (e.g. appended to Ruff's). Either place reproduces the exact
production regression this PR exists to fix (every non-`cmx-N` PR — `dev`, `main`,
`release/*` — goes red again) while the pinned ref-state step's `run:` stays byte-for-byte
untouched, so every assertion in this file that looks at that one step stays green.

15. No step anywhere in the job's `run:` text may assert a `cmx-N` branch-naming
    convention at all — not just the one step round 5 happened to pin. This is the same
    generalization round 6 already applied to `continue-on-error` (one named step's ban ->
    the whole job's ban): the invariant CMX-314 actually needs is "nothing in this job
    hard-fails a legitimately-named non-`cmx-N` branch," which a single pinned step's exact
    `run:` text can never guarantee once a second, unpinned step can carry the same check.

Round 9's own fix had one more gap, closed here in round 10: banning the literal substring
"cmx" is still a denylist over shell TEXT, and a config language doesn't need the literal
string to express the same gate. `grep -qE '^ref: refs/heads/[a-z]+-[0-9]+$' .git/HEAD`
folded into Ruff's `run:`, or a brand-new step keyed on `if:
${{ !startsWith(github.head_ref, 'cmx-') }}` with `run: exit 1`, both reproduce the exact
CMX-314 production regression — every PR opened from a legitimately-named non-`cmx-N` branch
goes red again — while containing no "cmx" substring at all (the first spells the prefix as
a character class; the second spells it in an `if:` expression the guard never reads, since
`test_no_step_reasserts_a_cmx_branch_naming_convention` only looked at `step["run"]`).

16. No amount of enumerating what a step's `run:` (or `if:`) TEXT may contain converges,
    because the space of ways to spell "reject this branch" is unbounded — a regex, a
    literal ban, a wider literal ban, each just moves the goalposts to a spelling the
    previous round didn't enumerate. The fix stops denylisting step CONTENT and starts
    allowlisting what the job's STEPS ARE: pin the whole ordered step list — every step's
    `name`, `uses`, and exact `run:` text, compared with `==` against a literal table — the
    same exact-value doctrine invariant 14 already applies to the checkout step's `with:`
    block and invariant 13 to Pytest's command, generalized to the entire job at once. Under
    this rule, folding a check into Ruff's `run:` changes Ruff's pinned entry (red), and a
    brand-new step — regardless of what its `if:` says, since the step is present in the
    parsed step list whether or not it ever executes — changes the list's length and order
    (red). No future spelling, key, or step position needs to be predicted in advance,
    because nothing may be added, removed, or edited without a visible diff here.

The env-override mutation (10) is the one gap the runtime state-assertion step does not
close on its own for THIS suite — it turns the env override into a red build in actual CI,
but a suite that only parses the YAML locally never executes the workflow, so a step that
merely runs unmodified `git checkout -B "${GITHUB_HEAD_REF:-$GITHUB_REF_NAME}"` still looks
identical whether or not a sibling `env:` key would have changed what it expands to.
Closing that locally needs a direct structural check instead: no `env:` block anywhere in
the file — workflow-level, job-level, or on any step — may define `GITHUB_HEAD_REF` or
`GITHUB_REF_NAME`, the two variables the rename command's own `${GITHUB_HEAD_REF:-
$GITHUB_REF_NAME}` reads. That is provably complete against THIS shape of attack (an `env:`
key in the YAML) — it is not a defense against a step writing to `$GITHUB_ENV` to redefine
either variable at runtime for a later step, which no denylist of YAML keys can see; the
runtime state-assertion step is what still catches that residual case, in actual CI, at the
point of use.

Round 5's own fix had three more gaps of the same shape, closed here in round 6:

12. `test_pytest_step_is_unconditional` banned `continue-on-error` on the Pytest step
    ALONE. `continue-on-error: true` on the ref-state-assertion step — round 5's own
    design, the one thing that turns every earlier round's mutation into a red build —
    reaches the same defanged end state by a different door: the step still runs, still
    fails on a bad ref, but the job still reports success, and every existing assertion
    (its exact `run:`, its `if:`-absence, its position, the git-ban exemption) stays green.
    Enumerating one more named step is the same shape that has already cost this PR five
    rounds and CMX-299 fourteen — the next step added would be unguarded again by
    construction. The fix generalizes: no step in the `test` job may carry
    `continue-on-error` at all, checked over the WHOLE step list with an explicit
    allow-list (empty today, one-line reason required per entry) for any step that
    legitimately needs it — and it fails loudly, not vacuously, if the job or its step list
    can't be found.
13. The Pytest step's `run:` was matched by substring only (`_step_running`) — the last
    substring-only anchor in the file, after round 3 pinned the rename step's `run:` and
    round 5 pinned the ref-state step's. `echo `-prefixing it leaves the needle
    (`uv run pytest`), the ordering, and every `if:`/`continue-on-error` check untouched
    while CI prints a command and executes zero tests, the CMX-301 guard included.
14. The checkout step's `with:` block was pinned on `fetch-depth` alone
    (`test_checkout_fetches_full_history`), leaving the rest of the mapping unconstrained.
    `ref: dev` on that same step clones `dev` instead of the PR's merge commit; the rename
    step then runs `git checkout -B cmx-305` on dev's tip, so both halves of the round-5
    runtime assertion (branch name, `origin/dev` resolvability) pass — it checks ref STATE,
    not which commit the ref points at — while CI silently tests `dev` and never the PR.
    Pin the whole `with:` mapping, not one key, so `ref:`, `repository:`,
    `sparse-checkout:`, or any future key are all closed by the same assertion.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

WORKFLOW = Path(__file__).resolve().parent.parent / ".github" / "workflows" / "ci.yml"


@pytest.fixture(scope="module")
def workflow() -> dict:
    return yaml.safe_load(WORKFLOW.read_text())


@pytest.fixture(scope="module")
def job(workflow) -> dict:
    return workflow["jobs"]["test"]


@pytest.fixture(scope="module")
def steps(job) -> list[dict]:
    return job["steps"]


def _step_running(steps: list[dict], needle: str) -> dict:
    """The one step whose `run:` block contains ``needle`` — asserting there is exactly
    one, so a second copy added later can never be the one silently going unchecked."""
    matches = [s for s in steps if needle in s.get("run", "")]
    assert len(matches) == 1, f"expected exactly one step running {needle!r}, found {len(matches)}"
    return matches[0]


def test_exactly_one_checkout_step(steps):
    """Round-1 rework's `next(...)` selector grabs the FIRST `actions/checkout` step, but
    GitHub Actions gives the workspace to whichever checkout ran LAST. A second, shallow
    checkout added later (e.g. right before "Install uv") would re-detach HEAD and go
    completely unseen by a first-match selector. Pin the count to exactly one so a later
    addition is a visible, asserted diff rather than a silent second copy.
    """
    checkouts = [s for s in steps if s.get("uses", "").startswith("actions/checkout@")]
    assert len(checkouts) == 1, (
        f"expected exactly one actions/checkout step, found {len(checkouts)} — a second "
        "checkout re-clones the workspace and can silently re-detach HEAD or shorten "
        "history, even though the first checkout still looks correct"
    )


def test_checkout_fetches_full_history(steps):
    """Invariant 1 — corrupt `fetch-depth: 0` to `fetch-depth: 1` (the judge's mutation) and
    this reddens.

    Pinned to the checkout step's `with.fetch-depth` EXACTLY equal to the integer `0`, not
    merely "a fetch-depth key exists" — `fetch-depth: 1` (the shallow default) also has the
    key present, so a presence-only check would stay green under exactly the mutation this
    guard exists to catch.
    """
    checkout = next(s for s in steps if s.get("uses", "").startswith("actions/checkout@"))
    assert checkout.get("with", {}).get("fetch-depth") == 0, (
        f"checkout step's fetch-depth is {checkout.get('with', {}).get('fetch-depth')!r}, "
        "expected exactly 0 (full history) — anything shallower leaves origin/dev unfetched "
        "and the CMX-301 defeat-shape numbering guard skips at the gate"
    )


def test_checkout_step_with_block_has_no_extra_keys(steps):
    """Round 6 — invariant 14: `test_checkout_fetches_full_history` pins `fetch-depth`
    alone, leaving the rest of the checkout step's `with:` mapping unconstrained. Adding
    `ref: dev` alongside the pinned `fetch-depth: 0` clones `dev` instead of the PR's own
    merge commit — the rename step then checks out a branch named `cmx-305` on `dev`'s tip,
    so the round-5 runtime ref-state assertion (which checks that HEAD names a `cmx-N`
    branch and that `origin/dev` resolves, not which commit either points at) passes while
    CI silently tests `dev` and never the PR.

    Pin the WHOLE `with:` mapping to exactly `{fetch-depth: 0}`, the action's default for
    everything else — closing `ref:`, `repository:`, `sparse-checkout:`, and any future key
    in one assertion, the same exact-value doctrine `fetch-depth` itself already follows.
    """
    checkout = next(s for s in steps if s.get("uses", "").startswith("actions/checkout@"))
    assert checkout.get("with") == {"fetch-depth": 0}, (
        f"checkout step's `with:` is {checkout.get('with')!r}, expected exactly "
        "{'fetch-depth': 0} — any other key (e.g. `ref:`) can point the checkout at a "
        "different commit than the PR's own while every other assertion in this file stays "
        "green"
    )


def test_head_is_renamed_to_the_pr_branch_before_pytest(steps):
    """Invariant 2 — corrupt the rename step's `run:` to a no-op read like `git rev-parse
    --abbrev-ref HEAD` (the judge's mutation) and this reddens, since that string no longer
    contains the needle below and `_step_running` finds zero matches instead of one.

    Round 3: a *substring* check (`"git checkout -B" in rename["run"]`) is defeated by
    prefixing the command with `echo` — the needle, the substring, the unconditional `if`,
    and the ordering are all untouched while the step only PRINTS the command instead of
    running it. Pin the `run:` to the exact expected command, not a substring of it, the
    same way `test_checkout_fetches_full_history` pins `fetch-depth` to the exact integer
    `0` rather than "the key is present."
    """
    rename = _step_running(steps, "GITHUB_HEAD_REF")
    expected_run = 'git checkout -B "${GITHUB_HEAD_REF:-$GITHUB_REF_NAME}"'
    assert rename["run"].strip() == expected_run, (
        f"the GITHUB_HEAD_REF step's run is {rename['run']!r}, expected exactly "
        f"{expected_run!r} — it must actually check out a branch named after "
        "GITHUB_HEAD_REF, not merely reference or print the variable, or branch-name-derived "
        "guards keep seeing a detached HEAD"
    )

    pytest_step = _step_running(steps, "uv run pytest")
    assert steps.index(rename) < steps.index(pytest_step), (
        "the HEAD-renaming step must run before Pytest, or the CMX-301 guard still sees "
        "the detached checkout when it runs"
    )


_REF_ENV_VARS = ("GITHUB_HEAD_REF", "GITHUB_REF_NAME")


def test_nothing_redefines_the_rename_steps_env_vars(workflow, job, steps):
    """Round 5: `test_head_is_renamed_to_the_pr_branch_before_pytest` pins the rename
    step's `run:` TEXT exactly (round 3) — but never what that text expands to. A
    step-level `env: {GITHUB_HEAD_REF: "${{ github.ref_name }}"}` on the rename step leaves
    the `run:` byte-identical (so the exact-match assertion is untouched), adds no `if:`
    key, no `uses:`, doesn't invoke `git` itself (so it isn't caught by the git-ban window,
    and wouldn't be inside it anyway — it sits ON the rename step, which that window
    already exempts), and doesn't change step order. On a `pull_request` event
    `github.ref_name` is `379/merge`, not the PR's own branch, so the rename would check
    out the wrong ref while every assertion above stays green.

    `GITHUB_`-prefixed env keys do take effect in a step's process — `env: GITHUB_TOKEN:
    ...` is the standard way to hand a token to a step — so this is a real expansion, not a
    hypothetical one. Ban either variable from being (re)defined by an `env:` block at
    ANY level — workflow, job, or step — since a workflow- or job-level override reaches
    the rename step exactly as a step-level one does, without adding an `env:` key to the
    step itself.
    """
    def _env_keys(node: dict) -> set[str]:
        env = node.get("env") if isinstance(node, dict) else None
        return set(env) if isinstance(env, dict) else set()

    offenders = _env_keys(workflow) & set(_REF_ENV_VARS)
    assert not offenders, (
        f"workflow-level `env:` redefines {sorted(offenders)} — this changes what the "
        "rename step's ${GITHUB_HEAD_REF:-$GITHUB_REF_NAME} expands to without touching "
        "its run: text at all"
    )

    offenders = _env_keys(job) & set(_REF_ENV_VARS)
    assert not offenders, (
        f"the `test` job's `env:` redefines {sorted(offenders)} — this changes what the "
        "rename step's ${GITHUB_HEAD_REF:-$GITHUB_REF_NAME} expands to without touching "
        "its run: text at all"
    )

    for step in steps:
        offenders = _env_keys(step) & set(_REF_ENV_VARS)
        assert not offenders, (
            f"step {step.get('name')!r} redefines {sorted(offenders)} in its own `env:` — "
            "this changes what the rename step's ${GITHUB_HEAD_REF:-$GITHUB_REF_NAME} "
            "expands to without touching its run: text at all"
        )


_GIT_INVOCATION = re.compile(r"(?<![\w.-])git(?![\w.-])")


def test_nothing_between_the_checkout_and_pytest_touches_git_except_the_pinned_steps(steps):
    """Round 3: round 2's `test_exactly_one_checkout_step` closed the "second `actions/
    checkout` step" shape, but only for steps that `uses: actions/checkout@...` — a plain
    `run:` step re-detaching HEAD between the rename and Pytest is invisible to it. Such a
    step has no `uses:` (checkout count stays 1), doesn't mention `GITHUB_HEAD_REF` (the
    rename count stays 1), and doesn't mention `uv run pytest` (the ordering assertion is
    unchanged) — every existing assertion in this file stays green while HEAD is detached
    again by the time the CMX-301 guard reads it.

    The invariant CMX-305 actually needs is "HEAD names the PR branch when Pytest starts",
    not merely "a step earlier in the file once renamed it" — so assert the state at the
    point of use.

    Round 4: round 3's own fix wrote that as a three-item denylist (`git checkout`, `git
    switch`, `git reset`). `git branch -m ci-head` renames the current branch without
    detaching HEAD — HEAD just stops naming `cmx-NNN` — and matches none of the three.
    `git remote remove origin` deletes the remote-tracking refs the CMX-301 guard's
    `origin/dev` diff needs, and also matches none of the three. Both are `git` invocations;
    neither is an enumerated verb. Ban `git` itself in this window, not a guessed list of
    its subcommands, so no future verb needs to be predicted in advance.

    Round 5: the window started at the rename step, but the fetch-depth invariant's own
    exposure starts at the CHECKOUT — `git remote remove origin`, moved a few lines earlier
    (before the rename instead of after), sat entirely outside the old window and went
    unscanned. Widen the window to `[checkout+1 : pytest)`, a strict superset of the old
    one. The rename step and the new ref-state-assertion step both legitimately invoke
    `git` inside that wider window — each is pinned to its exact `run:` text by its own
    test elsewhere in this file, so exempting the two of them here doesn't reopen any gap;
    every OTHER step in the window still may not touch `git` at all.
    """
    checkout = next(s for s in steps if s.get("uses", "").startswith("actions/checkout@"))
    rename = _step_running(steps, "GITHUB_HEAD_REF")
    ref_assert = _step_running(steps, "origin/dev")
    pytest_step = _step_running(steps, "uv run pytest")
    between = steps[steps.index(checkout) + 1 : steps.index(pytest_step)]
    pinned_elsewhere = {id(rename), id(ref_assert)}

    for step in between:
        if id(step) in pinned_elsewhere:
            continue
        run = step.get("run", "")
        assert not _GIT_INVOCATION.search(run), (
            f"step {step.get('name')!r} between the checkout and Pytest runs {run!r}, "
            "which invokes `git` — no unpinned step in that window may touch git at all, "
            "or it can silently rename, re-detach, or unfetch HEAD before the CMX-301 "
            "guard reads it, even though the rename step itself still looks correct"
        )


def test_head_rename_step_is_unconditional(steps):
    """Round-1 rework pinned the rename step's name, its `run:` contents, and its position —
    but never that it actually EXECUTES. Dead-coding it with `if: false` leaves the name,
    the `run:` string, and the ordering all untouched, so every assertion above still
    passes while the rename never runs and the CMX-301 guard keeps seeing a detached HEAD.

    The invariant is that the step is unconditional: no `if:` key at all. Any future
    condition on this step should be a deliberate, visible diff here, not a silent skip.
    """
    rename = _step_running(steps, "GITHUB_HEAD_REF")
    assert "if" not in rename, (
        f"the GITHUB_HEAD_REF rename step has an `if: {rename.get('if')!r}` condition — it "
        "must run unconditionally on every pull_request build, or it can be switched off "
        "while its name, run:, and position all still look correct"
    )


def test_job_is_unconditional(job):
    """Round 4: `test_head_rename_step_is_unconditional` pins the rename STEP's `if:` —
    but `jobs.test.if: false` dead-codes every step in the file, the rename included, one
    level above where that test looks. The step keeps no `if:` key, its exact `run:`, and
    its position before Pytest; every step-level assertion in this file stays green. A
    skipped job doesn't fail the workflow run, so CI reports green with the CMX-301 guard
    never having executed at all.

    The invariant is that the job itself is unconditional: no `if:` key at all, the same
    doctrine `test_head_rename_step_is_unconditional` already applies one level down.
    """
    assert "if" not in job, (
        f"the `test` job has an `if: {job.get('if')!r}` condition — it must run "
        "unconditionally on every pull_request build, or the entire job (rename step "
        "included) can be switched off while every step-level assertion still passes"
    )


def test_pytest_step_is_unconditional(steps):
    """Round 5: rounds 2 and 4 checked `if:`-absence on the rename step and on the job, but
    never on Pytest itself — the one step whose EXECUTION is the entire point of this file,
    and the one `_step_running(steps, "uv run pytest")` already uses as an ordering anchor.

    `if: false` on the Pytest step leaves the needle (`uv run pytest`), the `run:`, and the
    ordering (`index(rename) < index(pytest)`) byte-identical to every assertion above — a
    skipped step doesn't fail a workflow run, so CI would report green having executed zero
    tests, the CMX-301 guard included. `continue-on-error: true` reaches the same end state
    by a different door: the step runs, fails, and the job still reports success — so that
    is banned here too.
    """
    pytest_step = _step_running(steps, "uv run pytest")
    assert "if" not in pytest_step, (
        f"the Pytest step has an `if: {pytest_step.get('if')!r}` condition — it must run "
        "unconditionally, or CI can report green having executed zero tests"
    )
    assert "continue-on-error" not in pytest_step, (
        f"the Pytest step has `continue-on-error: {pytest_step.get('continue-on-error')!r}` "
        "— a step whose failure is swallowed reports the same green CI as one that never "
        "ran at all"
    )


def test_pytest_step_runs_the_exact_command(steps):
    """Round 6 — invariant 13: the Pytest step's `run:` was matched by substring only
    (`_step_running`, used as the ordering anchor for every test in this file) and pinned
    nowhere — the last substring-only anchor left, after round 3 pinned the rename step's
    `run:` exactly and round 5 did the same for the ref-state step's. Prefixing it with
    `echo` leaves the needle (`_step_running` still finds exactly one match), the ordering,
    and every `if:`/`continue-on-error` check untouched — CI prints a command and executes
    zero tests, the CMX-301 guard included, while every assertion in this file stays green.
    """
    pytest_step = _step_running(steps, "uv run pytest")
    assert pytest_step["run"].strip() == "uv run pytest -q", (
        f"the Pytest step's run is {pytest_step['run']!r}, expected exactly "
        "'uv run pytest -q' — it must actually run the suite, not merely reference or "
        "print the command"
    )


_CONTINUE_ON_ERROR_ALLOWLIST: dict[str, str] = {
    # step name -> one-line reason it's allowed to swallow its own failure.
    # Empty by design: every step in this job is meant to fail the build on failure.
}


def test_no_step_in_the_test_job_swallows_its_own_failure(job, steps):
    """Round 6 — invariant 12: `test_pytest_step_is_unconditional` banned
    `continue-on-error` on the Pytest step ALONE. `continue-on-error: true` on the
    ref-state-assertion step — round 5's own design, the one thing that turns every
    earlier round's mutation into a red build — reaches the identical defanged end state
    by a different door: the step still runs, still fails on a bad ref, but the job still
    reports success, while its exact `run:`, its `if:`-absence, its position immediately
    before Pytest, and its git-ban exemption all stay untouched and every existing
    assertion in this file stays green.

    Enumerating one more named step is the same shape that has already cost this PR five
    rounds and CMX-299 fourteen — the next step added is unguarded again by construction.
    Assert the invariant over the WHOLE job instead: no step may carry `continue-on-error`,
    with an explicit allow-list for any step that legitimately needs it (empty today — add
    an entry to `_CONTINUE_ON_ERROR_ALLOWLIST` with a one-line reason if one ever does). A
    rule over the whole job survives the next step being added; a rule over two named steps
    does not.

    If the job or its step list can't even be found, this must FAIL loudly rather than
    vacuously pass — an empty list would otherwise satisfy "no step violates X" for the
    wrong reason: nothing was actually checked.
    """
    assert job, "the `test` job was not found in the workflow — cannot check its steps"
    assert steps, "the `test` job has no steps — cannot check continue-on-error usage"

    for step in steps:
        name = step.get("name", "<unnamed step>")
        if name in _CONTINUE_ON_ERROR_ALLOWLIST:
            continue
        assert "continue-on-error" not in step, (
            f"step {name!r} has `continue-on-error: {step.get('continue-on-error')!r}` and "
            "is not in _CONTINUE_ON_ERROR_ALLOWLIST — a step whose failure is swallowed "
            "reports the same green CI as one that never ran at all; if this step "
            "legitimately needs it, add it to the allow-list with a one-line reason"
        )


# (name, uses, run) for every step in the `test` job's steps list, in order. `run` is
# `.strip()`ped to match how every other test in this file compares `run:` text. This is
# the literal table invariant 16 pins the whole job against — see
# test_the_step_list_is_pinned_exactly.
_EXPECTED_STEPS: list[tuple[str | None, str | None, str | None]] = [
    (None, "actions/checkout@v4", None),
    ("Name the checked-out ref", None,
     'git checkout -B "${GITHUB_HEAD_REF:-$GITHUB_REF_NAME}"'),
    ("Install uv", "astral-sh/setup-uv@v5", None),
    ("Set up Python ${{ matrix.python-version }}", None,
     "uv python install ${{ matrix.python-version }}"),
    ("Sync (with dev + dashboard extras)", None,
     "uv sync --extra dev --extra dashboard --python ${{ matrix.python-version }}"),
    (None, "actions/setup-node@v4", None),
    ("Install jsdom (DOM test suites)", None, "npm ci"),
    ("Ruff", None, "uv run ruff check chela tests"),
    ("Assert the ref state the CMX-301 guard needs", None,
     'ref="$(git rev-parse --abbrev-ref HEAD)"\n'
     '[ -n "$ref" ] && [ "$ref" != "HEAD" ]\n'
     "git rev-parse --verify --quiet origin/dev"),
    ("Pytest", None, "uv run pytest -q"),
]


def test_the_step_list_is_pinned_exactly(steps):
    """Round 10 — invariant 16: round 9's guard banned the literal substring "cmx" in every
    step's `run:` text, and its own catalog entry (`docs/defeat_shapes/314-*.md`) called
    that "a strict superset [of the old regex] ... because all of them still have to name
    the branch prefix they're checking for." They don't — the judge defeated it twice in
    the same round, both times without the string "cmx" appearing anywhere in the mutation:

        # folded into the existing, unpinned Ruff step's run: — no "cmx" substring, no new
        # step name in the diff, and the naming gate is spelled as a generic task-branch
        # shape instead:
        - name: Ruff
          run: |
            uv run ruff check chela tests
            grep -qE '^ref: refs/heads/[a-z]+-[0-9]+$' .git/HEAD

        # a brand-new step keyed on `if:`, which test_no_step_reasserts_a_cmx_branch_naming_
        # convention never read (it only looked at step["run"]) — and `run: exit 1` needs no
        # "cmx" substring at all, since the branch check lives entirely in the condition:
        - name: Assert the branch is a task branch
          if: ${{ !startsWith(github.head_ref, 'cmx-') }}
          run: exit 1

    Both reproduce the exact CMX-314 production regression (every PR opened from a
    legitimately-named non-`cmx-N` branch goes red again) while every existing assertion in
    this file — the pinned ref-state step's exact `run:`, its `if:`-absence, its position
    immediately before Pytest, the git-ban window, every `continue-on-error` check — stays
    byte-for-byte untouched. A denylist over step CONTENT has no bottom: a regex, a wider
    regex, a literal-substring ban, each just relocates the enumeration to a spelling or a
    YAML key the previous round didn't cover.

    The fix stops denylisting what a step's `run:` may CONTAIN and starts allowlisting what
    the job's STEPS ARE, generalizing the exact-value doctrine `test_checkout_step_with_
    block_has_no_extra_keys` (14) already applies to `with:` and `test_pytest_step_runs_
    the_exact_command` (13) already applies to Pytest's command, to the WHOLE step list at
    once: every step's `name`, `uses`, and `run:` are compared with `==` against a literal
    table, in order. Folding a check into Ruff's `run:` changes Ruff's pinned entry — red.
    A brand-new step changes the list's length and every subsequent step's position — red,
    regardless of what that step's `if:` says, since the parsed step list contains it
    whether or not it ever executes. No future spelling, YAML key, or step position needs
    to be predicted in advance, because nothing may be added, removed, reordered, or edited
    without a visible diff here. This subsumes `_CMX_LITERAL_ALLOWLIST` and round 9's guard
    entirely — any step whose `run:` mentions "cmx" is, by construction, a step whose entry
    in `_EXPECTED_STEPS` doesn't match, the same way any other unpinned edit wouldn't.
    """
    actual = [
        (
            step.get("name"),
            step.get("uses"),
            step["run"].strip() if step.get("run") is not None else None,
        )
        for step in steps
    ]
    assert actual == _EXPECTED_STEPS, (
        f"the `test` job's step list no longer matches the pinned list exactly.\n"
        f"actual:   {actual!r}\n"
        f"expected: {_EXPECTED_STEPS!r}\n"
        "— every step's name/uses/run is pinned, in order; adding, removing, reordering, "
        "or editing ANY step (even one whose behavior no other test in this file covers) "
        "changes this list and must be a deliberate, visible diff here, not a silent "
        "addition"
    )


def test_ref_state_is_asserted_immediately_before_pytest(steps):
    """Round 5: rounds 1-4 each closed a mutation that disturbed a different PROPERTY of
    this YAML — a verb, a spelling, a step vs its parent job, a window boundary — and each
    round the next mutation simply moved to a property the previous assertion didn't
    enumerate. Chasing properties has no bottom.

    Instead of predicting the next way to disturb the ref, assert the STATE the CMX-301
    guard actually needs, at the exact point it needs it: a step immediately before Pytest
    must confirm HEAD is attached to a real branch (not the detached-merge-commit default
    of a `pull_request` checkout) and that `origin/dev` is resolvable. Whatever mutation
    disturbs either — a dropped remote, an overridden `GITHUB_HEAD_REF`, a re-detach, a
    second checkout, a switched-off rename — now fails IN CI at the point of use, rather
    than leaving the CMX-301 guard to skip quietly three steps later.

    Round 7 (CMX-314): round 5's own assertion also demanded HEAD's name match `cmx-[0-9]+`
    exactly. `tests/test_judge.py::_cmx_task_number_from_branch` treats a non-`cmx-N` branch
    (`dev`, `main`, `release/*`, ...) as a LEGITIMATE, tested skip — not a fault —
    (`test_cmx_task_number_from_branch_parses_or_gives_a_loud_reason` asserts exactly that).
    Demanding the branch-name shape here turned that designed skip into a hard CI failure on
    every PR not opened from a `cmx-N` branch, including the `dev` -> `main` release
    promotion PR itself. What CMX-305 actually needed was a non-detached HEAD, not a naming
    convention, so only that is asserted now.
    """
    ref_assert = _step_running(steps, "origin/dev")
    pytest_step = _step_running(steps, "uv run pytest")

    expected_run = (
        'ref="$(git rev-parse --abbrev-ref HEAD)"\n'
        '[ -n "$ref" ] && [ "$ref" != "HEAD" ]\n'
        "git rev-parse --verify --quiet origin/dev"
    )
    assert ref_assert["run"].strip() == expected_run, (
        f"the ref-state-assertion step's run is {ref_assert['run']!r}, expected exactly "
        f"{expected_run!r} — it must actually assert both that HEAD is attached AND that "
        "origin/dev resolves, not merely reference either, and must not reintroduce a "
        "branch-naming requirement that fails legitimate non-cmx-N branches"
    )
    assert "if" not in ref_assert, (
        f"the ref-state-assertion step has an `if: {ref_assert.get('if')!r}` condition — "
        "it must run unconditionally, or it can be switched off while its run: and "
        "position still look correct"
    )
    assert steps.index(ref_assert) == steps.index(pytest_step) - 1, (
        "the ref-state-assertion step must sit IMMEDIATELY before Pytest — anything "
        "positioned between the two could disturb the ref again after it was checked"
    )
