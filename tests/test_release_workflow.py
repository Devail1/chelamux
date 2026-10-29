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

import re
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
    assert triggers["push"]["tags"] == ["v[0-9]+.[0-9]+.[0-9]+"]
    assert "workflow_dispatch" in triggers


# --- CMX-366: the moving major tag must not start a release ------------------------------
#
# A release force-moves `v0` after pushing `vX.Y.Z`. Under the old `v*` filter that push
# ran this workflow again, which asked `chela.release_notes` for a `## [0]` section and
# failed red on every release from 0.18.0 to 0.19.0. The filter is evaluated by GitHub,
# so it is evaluated here the way GitHub documents it ("Filter pattern cheat sheet").


def _github_filter_regex(pattern: str) -> re.Pattern[str]:
    """GitHub Actions' ref filter syntax, as a full-match regex: `*` is any run of
    characters except `/`, `**` any run at all, `?` and `+` make the PRECEDING character
    optional / repeatable, `[...]` is a character class, and everything else (`.`
    included) is literal."""
    out: list[str] = []
    i = 0
    while i < len(pattern):
        c = pattern[i]
        if pattern.startswith("**", i):
            out.append(".*")
            i += 2
            continue
        if c == "*":
            out.append("[^/]*")
        elif c in "?+":
            out.append(c)
        elif c == "[":
            end = pattern.index("]", i)
            out.append(pattern[i:end + 1])
            i = end
        else:
            out.append(re.escape(c))
        i += 1
    return re.compile("".join(out))


def _starts_the_workflow(patterns: list[str], tag: str) -> bool:
    return any(_github_filter_regex(p).fullmatch(tag) for p in patterns)


@pytest.mark.parametrize(("pattern", "ref", "matches"), [
    # Literal fixtures from GitHub's cheat sheet, both ways, so the matcher is not
    # merely agreeing with itself.
    ("v1*", "v1.10.1", True),
    ("v1*", "v2", False),
    ("v[12].[0-9]+.[0-9]+", "v1.10.1", True),
    ("v[12].[0-9]+.[0-9]+", "v3.0.0", False),
    ("v[12].[0-9]+.[0-9]+", "v1.10", False),
    ("feature/*", "feature/a/b", False),
    ("feature/**", "feature/a/b", True),
    ("*.jsx?", "page.js", True),
    ("*.jsx?", "page.jsx", True),
    ("*.jsx?", "page.jsxx", False),
    ("v1.0", "v1x0", False),  # `.` is literal
])
def test_the_filter_matcher_follows_githubs_syntax(pattern, ref, matches):
    assert bool(_github_filter_regex(pattern).fullmatch(ref)) is matches


@pytest.mark.parametrize("tag", ["v0", "v1", "v12"])
def test_a_moving_major_tag_push_does_not_start_a_release(workflow, tag):
    triggers = workflow[True] if True in workflow else workflow["on"]
    assert not _starts_the_workflow(triggers["push"]["tags"], tag), (
        f"pushing the major tag {tag} starts the Release workflow, which then fails "
        "looking for a CHANGELOG section that cannot exist"
    )


@pytest.mark.parametrize("tag", ["v0.19.1", "v0.18.0", "v1.0.0", "v10.20.300"])
def test_a_full_version_tag_push_starts_a_release(workflow, tag):
    triggers = workflow[True] if True in workflow else workflow["on"]
    assert _starts_the_workflow(triggers["push"]["tags"], tag)


@pytest.mark.parametrize("tag", ["v0.19", "v0x19x1", "0.19.1", "v0.19.1-rc1"])
def test_a_tag_that_is_not_a_full_version_does_not_start_a_release(workflow, tag):
    triggers = workflow[True] if True in workflow else workflow["on"]
    assert not _starts_the_workflow(triggers["push"]["tags"], tag)


def test_the_old_v_star_filter_would_have_started_on_the_major_tag():
    """Negative control: the matcher sees the bug the old filter had."""
    assert _starts_the_workflow(["v*"], "v0")


# --- CMX-363: the `pypi` job -------------------------------------------------------------
#
# A PyPI version can be uploaded once, ever, and this job only runs when a release is
# already happening. So its whole mapping is pinned (an unpinned key is a place a mutation
# hides), and the one step with logic in it, the tag/version check, is executed for real.

# The wheel runs as an adopter runs it, and imports the dashboard (whose Flask import
# `--help` never reaches).
_SMOKE = (
    "uvx --from dist/chelamux-*.whl chelamux --help\n"
    "uvx --from dist/chelamux-*.whl python -c 'import chela.dashboard.app'\n"
)

_PYPI_VERSION_CHECK = """version="$(python3 -c 'import tomllib; print(tomllib.load(open("pyproject.toml", "rb"))["project"]["version"])')"
if [ "v$version" != "$GITHUB_REF_NAME" ]; then
  echo "tag $GITHUB_REF_NAME does not match pyproject.toml version $version" >&2
  exit 1
fi
"""

_EXPECTED_PYPI_JOB: dict = {
    "needs": "release",
    "runs-on": "ubuntu-latest",
    "if": "github.repository == 'Devail1/chelamux' && github.event_name == 'push'",
    "environment": {"name": "pypi", "url": "https://pypi.org/project/chelamux/"},
    "permissions": {"contents": "read", "id-token": "write"},
    "steps": [
        {"uses": "actions/checkout@v4"},
        {"name": "Install uv", "uses": "astral-sh/setup-uv@v5"},
        {"name": "Check the tag matches pyproject's version", "run": _PYPI_VERSION_CHECK},
        {"name": "Build sdist + wheel", "run": "uv build"},
        {"name": "Smoke-test the built wheel", "run": _SMOKE},
        {"name": "Publish to PyPI", "uses": "pypa/gh-action-pypi-publish@release/v1"},
    ],
}


@pytest.fixture(scope="module")
def pypi_job(workflow) -> dict:
    return workflow["jobs"]["pypi"]


def test_the_workflow_has_exactly_the_release_and_pypi_jobs(workflow):
    assert set(workflow["jobs"]) == {"release", "pypi"}


def test_the_pypi_job_mapping_is_pinned_exactly(pypi_job):
    assert pypi_job == _EXPECTED_PYPI_JOB


def test_pypi_publishes_with_trusted_publishing_not_a_token(pypi_job):
    """No `password:`/token input anywhere: the OIDC token (`id-token: write`) is the only
    credential, scoped to the `pypi` environment PyPI's trusted publisher names."""
    assert pypi_job["permissions"]["id-token"] == "write"
    assert pypi_job["environment"]["name"] == "pypi"
    publish = pypi_job["steps"][-1]
    assert publish["uses"] == "pypa/gh-action-pypi-publish@release/v1"
    assert "with" not in publish and "env" not in publish


def test_pypi_publishes_only_on_a_tag_push_after_the_release(pypi_job):
    """A dispatch run builds the default branch, not the tag; and a release whose notes
    failed to extract must not reach PyPI either."""
    assert pypi_job["needs"] == "release"
    assert "github.event_name == 'push'" in pypi_job["if"]
    assert "github.repository == 'Devail1/chelamux'" in pypi_job["if"]


def test_pypi_smoke_tests_the_wheel_before_publishing_it(pypi_job):
    names = [s.get("name") for s in pypi_job["steps"]]
    assert names.index("Check the tag matches pyproject's version") < names.index("Build sdist + wheel")
    assert names.index("Build sdist + wheel") < names.index("Smoke-test the built wheel")
    assert names.index("Smoke-test the built wheel") < names.index("Publish to PyPI")


def _run_version_check(pypi_job, tmp_path: Path, version: str, ref_name: str):
    import subprocess

    (tmp_path / "pyproject.toml").write_text(f'[project]\nname = "chelamux"\nversion = "{version}"\n')
    step = next(s for s in pypi_job["steps"] if s.get("name") == "Check the tag matches pyproject's version")
    # GitHub runs `run:` blocks with `bash -e {0}`.
    return subprocess.run(["bash", "-e", "-c", step["run"]], cwd=tmp_path, capture_output=True,
                          text=True, timeout=30, env={"PATH": "/usr/bin:/bin", "GITHUB_REF_NAME": ref_name})


def test_the_version_check_passes_a_matching_tag(pypi_job, tmp_path):
    result = _run_version_check(pypi_job, tmp_path, "0.19.0", "v0.19.0")
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("ref_name", ["v0.19.1", "0.19.0", "v0.19.0rc1", "v0.19"])
def test_the_version_check_refuses_a_mismatched_tag(pypi_job, tmp_path, ref_name):
    result = _run_version_check(pypi_job, tmp_path, "0.19.0", ref_name)
    assert result.returncode == 1
    assert f"tag {ref_name} does not match pyproject.toml version 0.19.0" in result.stderr
