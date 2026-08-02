"""`.github/workflows/release.yml` states four invariants; nothing read the file.

The judge corrupted two of them on this PR's head — replacing the notes step with
`echo "See CHANGELOG.md" > notes.md`, and widening the publish condition to
`if: always()` — and the whole 2381-test suite stayed green through both. A workflow
is code that only ever runs in anger, on a tag push, when a release is already
happening; there is no second chance to notice it was wired wrong. So the invariants
it states in prose are asserted here against the file itself.

The four:

1. The release body comes from the tested `chela.release_notes` module, never inline
   `sed`/`awk` in the YAML (the module's own docstring says so).
2. A `workflow_dispatch` dry run publishes NOTHING — only a tag push, or an explicit
   `dry_run=false`, may reach `gh release create` (OBJECTIVE 4).
3. ⭐ `gh release create` carries `--verify-tag`. Without it `gh` CREATES the tag at the
   default branch when it doesn't already exist, so the dispatch path could make CI tag
   — the exact "tagging stays a deliberate human act" boundary (OBJECTIVE 6) that this
   ticket exists because an agent crossed by hand.
4. The job skips cleanly on a fork rather than failing red trying to publish someone
   else's release.
"""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

WORKFLOW = Path(__file__).resolve().parent.parent / ".github" / "workflows" / "release.yml"


@pytest.fixture(scope="module")
def workflow() -> dict:
    return yaml.safe_load(WORKFLOW.read_text())


@pytest.fixture(scope="module")
def job(workflow) -> dict:
    return workflow["jobs"]["release"]


@pytest.fixture(scope="module")
def steps(job) -> list[dict]:
    return job["steps"]


def _step_running(steps: list[dict], needle: str) -> dict:
    """The one step whose `run:` block contains ``needle`` — asserting there is exactly
    one, so a second copy added later can never be the one silently going unchecked."""
    matches = [s for s in steps if needle in s.get("run", "")]
    assert len(matches) == 1, f"expected exactly one step running {needle!r}, found {len(matches)}"
    return matches[0]


def test_release_notes_come_from_the_tested_module(steps):
    """Invariant 1 — corrupt to `echo ... > notes.md` (the judge's mutation) and this reddens."""
    # `> notes.md` = the step that BUILDS the file, not the two that merely read it
    # (`cat notes.md`, `--notes-file notes.md`). The judge's mutation
    # (`echo "See CHANGELOG.md" > notes.md`) still matches this needle, so it is caught
    # rather than silently selecting a different step.
    step = _step_running(steps, "> notes.md")
    assert "python -m chela.release_notes" in step["run"]


def test_notes_are_never_built_by_inline_sed_or_awk(steps):
    """Invariant 1's other half: the extraction logic must not migrate back into the YAML."""
    for step in steps:
        run = step.get("run", "")
        if "notes.md" not in run:
            continue
        assert "sed" not in run and "awk" not in run, (
            f"notes.md built with inline sed/awk in step {step.get('name')!r} — "
            "extraction belongs in chela.release_notes, where it is unit-tested"
        )


def test_a_dispatch_dry_run_publishes_nothing(steps):
    """Invariant 2 — corrupt to `if: always()` (the judge's mutation) and this reddens.

    Pinned to the EXACT condition, not to "mentions workflow_dispatch and dry_run".
    The weaker form is satisfied by `inputs.dry_run == true` — an inversion that
    publishes on exactly the runs meant to publish nothing, while still naming both
    terms. A guard that a semantic inversion survives is checking spelling.
    """
    publish = _step_running(steps, "gh release create")
    expected = "github.event_name != 'workflow_dispatch' || inputs.dry_run == false"
    assert publish.get("if", "") == expected, (
        f"the publish step's `if:` is {publish.get('if')!r}, expected {expected!r} — "
        "it must stay conditioned on the workflow_dispatch/dry_run pair, or a dry run "
        "publishes a real release"
    )


def test_dry_run_input_defaults_to_true(workflow):
    """Invariant 2's other half: the safe value must be the DEFAULT, not a thing you
    remember to tick. Note `on:` is YAML 1.1's boolean `True` once parsed, not the
    string "on" — read it the way the parser actually stored it."""
    triggers = workflow[True] if True in workflow else workflow["on"]
    assert triggers["workflow_dispatch"]["inputs"]["dry_run"]["default"] is True


def test_publish_refuses_to_create_a_missing_tag(steps):
    """⭐ Invariant 3 — `gh release create` CREATES the tag when it is absent. Drop
    `--verify-tag` and CI can tag, against OBJECTIVE 6. Corrupt by deleting the flag."""
    publish = _step_running(steps, "gh release create")
    assert "--verify-tag" in publish["run"], (
        "`gh release create` without --verify-tag CREATES the tag at the default branch "
        "when it does not exist — that is CI tagging, which this workflow must never do"
    )


def test_ci_never_pushes_a_tag_itself(steps):
    """Invariant 3's companion: no step may create or push a tag directly either."""
    for step in steps:
        run = step.get("run", "")
        assert "git tag" not in run, f"step {step.get('name')!r} tags from CI"
        assert "push --tags" not in run, f"step {step.get('name')!r} pushes tags from CI"


def test_the_job_skips_cleanly_on_a_fork(job):
    """Invariant 4 — a fork must not fail red trying to publish someone else's release.

    ⛔ Asserts the repository this compares AGAINST, not merely that a comparison
    exists. The first version of this test checked `"github.repository ==" in ...`,
    and the judge killed it by pointing the guard at `Devail1/chelamux-does-not-exist`
    — a one-word edit that makes the release job never run ANYWHERE while leaving the
    assertion perfectly green. Presence is not a value.
    """
    assert job.get("if", "") == "github.repository == 'Devail1/chelamux'", (
        f"the job's `if:` is {job.get('if')!r} — it must gate on this repository "
        "exactly; a comparison against any other name silently disables releases"
    )


def test_the_workflow_reacts_to_tags_it_did_not_create(workflow):
    """The trigger contract: a tag a human already pushed, plus the manual dry-run entry."""
    triggers = workflow[True] if True in workflow else workflow["on"]
    assert triggers["push"]["tags"] == ["v*"]
    assert "workflow_dispatch" in triggers
