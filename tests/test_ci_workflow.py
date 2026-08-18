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
