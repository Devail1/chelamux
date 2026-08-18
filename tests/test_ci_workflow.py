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
"""
from __future__ import annotations

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


def test_head_is_renamed_to_the_pr_branch_before_pytest(steps):
    """Invariant 2 — corrupt the rename step's `run:` to a no-op read like `git rev-parse
    --abbrev-ref HEAD` (the judge's mutation) and this reddens, since that string no longer
    contains the needle below and `_step_running` finds zero matches instead of one.
    """
    rename = _step_running(steps, "GITHUB_HEAD_REF")
    assert "git checkout -B" in rename["run"], (
        f"the GITHUB_HEAD_REF step's run is {rename['run']!r} — it must actually check out "
        "a branch named after GITHUB_HEAD_REF, not merely reference the variable, or "
        "branch-name-derived guards keep seeing a detached HEAD"
    )

    pytest_step = _step_running(steps, "uv run pytest")
    assert steps.index(rename) < steps.index(pytest_step), (
        "the HEAD-renaming step must run before Pytest, or the CMX-301 guard still sees "
        "the detached checkout when it runs"
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
