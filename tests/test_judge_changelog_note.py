"""⚖️📝 CMX-309 — a PR that changes non-prose files without touching CHANGELOG.md gets a
NOTE, mechanically, instead of nothing at all.

CONTRIBUTING.md says "any user-facing change adds a CHANGELOG entry" — that was pure prose,
and it failed TWICE: cutting 0.7.0 from `dev` would have shipped notes missing half of it (4
of the last 8 merges carried no entry, backfilled by hand in #382). These tests pin
``judge._changelog_missing_note`` and its wiring into ``run_experiments``.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from chela import judge

TEST_CMD = f'"{sys.executable}" -m pytest -q'

PASSING_TEST = "def test_ok():\n    assert True\n"


def _git(*args: str, cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(cwd), *args], check=True, capture_output=True, text=True,
    )


@pytest.fixture
def origin(tmp_path) -> Path:
    o = tmp_path / "origin.git"
    subprocess.run(["git", "init", "--bare", "-b", "dev", str(o)], check=True, capture_output=True)
    return o


@pytest.fixture
def repo(tmp_path, origin) -> Path:
    work = tmp_path / "repo"
    subprocess.run(["git", "clone", str(origin), str(work)], check=True, capture_output=True)
    for k, v in (("user.email", "t@example.com"), ("user.name", "T"), ("commit.gpgsign", "false")):
        _git("config", k, v, cwd=work)
    (work / "test_suite.py").write_text(PASSING_TEST)
    (work / "CHANGELOG.md").write_text("## [Unreleased]\n")
    (work / "README.md").write_text("# hello\n")
    _git("add", "test_suite.py", "CHANGELOG.md", "README.md", cwd=work)
    _git("commit", "-m", "seed: a green suite", cwd=work)
    _git("push", "-u", "origin", "dev", cwd=work)
    return work


def _branch_from_head(repo: Path, name: str) -> str:
    _git("branch", name, cwd=repo)
    return _git("rev-parse", name, cwd=repo).stdout.strip()


def _detached_worktree(repo: Path, ref: str, path: Path) -> Path:
    _git("worktree", "add", "--detach", str(path), ref, cwd=repo)
    return path


def _prep_worktree(repo: Path, branch: str, tmp_path: Path) -> Path:
    wt = _detached_worktree(repo, branch, tmp_path / "wt")
    _git("fetch", "origin", "dev", cwd=wt)
    return wt


# --- _changelog_missing_note: the pure git mechanics ----------------------------------------


def test_changelog_missing_note_fires_when_code_changes_without_a_changelog_entry(
    tmp_path, repo, origin,
):
    _branch_from_head(repo, "pr-1")
    _git("checkout", "pr-1", cwd=repo)
    (repo / "feature.py").write_text("def add(a, b):\n    return a + b\n")
    _git("add", "feature.py", cwd=repo)
    _git("commit", "-m", "add a feature, no changelog entry", cwd=repo)
    wt = _prep_worktree(repo, "pr-1", tmp_path)

    note = judge._changelog_missing_note(wt, "dev")

    assert note is not None
    assert "CHANGELOG.md" in note["title"]


def test_changelog_missing_note_still_fires_when_a_different_md_file_is_also_touched(
    tmp_path, repo, origin,
):
    """DEFEAT_SHAPES #309: the exemption must key on CHANGELOG.md by *name*, not by
    extension. Broadening ``Path(f).name == "CHANGELOG.md"`` to ``Path(f).suffix == ".md"``
    would let ANY touched markdown file (README.md, a docs page, ...) silently satisfy the
    exemption even though CHANGELOG.md itself was never touched.
    """
    _branch_from_head(repo, "pr-1")
    _git("checkout", "pr-1", cwd=repo)
    (repo / "feature.py").write_text("def add(a, b):\n    return a + b\n")
    (repo / "README.md").write_text("# hello\n\nmore words about the feature.\n")
    _git("add", "feature.py", "README.md", cwd=repo)
    _git("commit", "-m", "add a feature and touch README, still no changelog entry", cwd=repo)
    wt = _prep_worktree(repo, "pr-1", tmp_path)

    note = judge._changelog_missing_note(wt, "dev")

    assert note is not None
    assert "CHANGELOG.md" in note["title"]


def test_changelog_missing_note_body_carries_the_actionable_instruction(tmp_path, repo, origin):
    """DEFEAT_SHAPES #309: dead-coding the body (``"" and (...)``) leaves the note firing
    (its title survives) while the entire actionable payload silently collapses to "" —
    assert the body's real content, not just that a note exists.
    """
    _branch_from_head(repo, "pr-1")
    _git("checkout", "pr-1", cwd=repo)
    (repo / "feature.py").write_text("def add(a, b):\n    return a + b\n")
    _git("add", "feature.py", cwd=repo)
    _git("commit", "-m", "add a feature, no changelog entry", cwd=repo)
    wt = _prep_worktree(repo, "pr-1", tmp_path)

    note = judge._changelog_missing_note(wt, "dev")

    assert note is not None
    assert "## [Unreleased]" in note["body"]
    assert "CONTRIBUTING.md" in note["body"]


def test_changelog_missing_note_body_states_the_mechanical_fact_it_found(
    tmp_path, repo, origin,
):
    """DEFEAT_SHAPES #309 round 2: blanking just the FIRST half of the body (the sentence
    stating what was actually observed — non-prose files changed, CHANGELOG.md did not)
    leaves ``"## [Unreleased]"`` and ``"CONTRIBUTING.md"`` intact in what remains, so a test
    that only checks for those two substrings does not notice the mechanical-fact sentence
    is gone. Pin that sentence too.
    """
    _branch_from_head(repo, "pr-1")
    _git("checkout", "pr-1", cwd=repo)
    (repo / "feature.py").write_text("def add(a, b):\n    return a + b\n")
    _git("add", "feature.py", cwd=repo)
    _git("commit", "-m", "add a feature, no changelog entry", cwd=repo)
    wt = _prep_worktree(repo, "pr-1", tmp_path)

    note = judge._changelog_missing_note(wt, "dev")

    assert note is not None
    assert "changes non-prose files but never touches CHANGELOG.md" in note["body"]


def test_changelog_missing_note_is_none_when_the_changelog_was_touched(tmp_path, repo, origin):
    _branch_from_head(repo, "pr-1")
    _git("checkout", "pr-1", cwd=repo)
    (repo / "feature.py").write_text("def add(a, b):\n    return a + b\n")
    (repo / "CHANGELOG.md").write_text(
        "## [Unreleased]\n\n### Added\n\n- A feature. (#1)\n"
    )
    _git("add", "feature.py", "CHANGELOG.md", cwd=repo)
    _git("commit", "-m", "add a feature, with a changelog entry", cwd=repo)
    wt = _prep_worktree(repo, "pr-1", tmp_path)

    assert judge._changelog_missing_note(wt, "dev") is None


def test_changelog_missing_note_is_none_for_a_prose_only_diff(tmp_path, repo, origin):
    _branch_from_head(repo, "pr-1")
    _git("checkout", "pr-1", cwd=repo)
    (repo / "README.md").write_text("# hello\n\nmore words.\n")
    _git("commit", "-am", "docs only, no user-facing change to log", cwd=repo)
    wt = _prep_worktree(repo, "pr-1", tmp_path)

    assert judge._changelog_missing_note(wt, "dev") is None


def test_changelog_missing_note_is_none_without_a_base_branch(tmp_path, repo):
    wt = _detached_worktree(repo, "dev", tmp_path / "wt")
    assert judge._changelog_missing_note(wt, "") is None


def test_changelog_missing_note_is_none_when_the_base_ref_does_not_resolve(tmp_path, repo):
    wt = _detached_worktree(repo, "dev", tmp_path / "wt")
    assert judge._changelog_missing_note(wt, "does-not-exist") is None


def test_changelog_missing_note_is_none_on_an_empty_diff(tmp_path, repo):
    wt = _detached_worktree(repo, "dev", tmp_path / "wt")
    assert judge._changelog_missing_note(wt, "dev") is None


# --- wired into run_experiments's notes, on every report state -----------------------------


def test_run_experiments_carries_the_note_even_on_a_cannot_verify_report(
    tmp_path, repo, origin,
):
    """A missing CHANGELOG entry is independent of whether the judge could run its mutation
    battery — the note must survive a report that never gets past 'no experiments proposed'.
    """
    _branch_from_head(repo, "pr-1")
    _git("checkout", "pr-1", cwd=repo)
    (repo / "feature.py").write_text("def add(a, b):\n    return a + b\n")
    _git("add", "feature.py", cwd=repo)
    _git("commit", "-m", "add a feature, no changelog entry, no experiments proposed", cwd=repo)
    wt = _prep_worktree(repo, "pr-1", tmp_path)

    report = judge.run_experiments(
        wt, TEST_CMD, {"experiments": []}, timeout=60, base_branch="dev",
    )

    assert report.state == judge.J_CANNOT_VERIFY
    titles = [n.get("title") for n in report.notes]
    assert "No CHANGELOG.md entry" in titles


def test_run_experiments_carries_the_note_on_a_clean_report_with_experiments(
    tmp_path, repo, origin,
):
    """DEFEAT_SHAPES #309 round 2: gating the append on ``not items`` (so it only fires on
    the no-experiments / cannot-verify path) would let it slip past every other test in this
    file, since they only exercise ``{"experiments": []}``. Here ``items`` is non-empty and
    the report reaches a real (non-cannot-verify) verdict — the note must still be present.
    """
    _branch_from_head(repo, "pr-1")
    _git("checkout", "pr-1", cwd=repo)
    (repo / "feature.py").write_text("def add(a, b):\n    return a + b\n")
    _git("add", "feature.py", cwd=repo)
    _git("commit", "-m", "add a feature, no changelog entry, with a real experiment", cwd=repo)
    wt = _prep_worktree(repo, "pr-1", tmp_path)

    report = judge.run_experiments(
        wt, TEST_CMD,
        {"experiments": [{
            "guard": "irrelevant", "kind": "mutation", "file": "test_suite.py",
            "before": "assert True", "after": "assert False",
        }]},
        timeout=60, base_branch="dev",
    )

    assert report.state == judge.J_CLEAN
    assert report.outcomes and report.outcomes[0].verdict == judge.KILLED
    titles = [n.get("title") for n in report.notes]
    assert "No CHANGELOG.md entry" in titles


def test_run_experiments_carries_the_note_on_a_dirty_worktree_cannot_verify_report(
    tmp_path, repo, origin,
):
    """DEFEAT_SHAPES #309 round 3: the note is appended BEFORE the ``_git_dirty`` early
    return specifically so it survives that path too — gating the append on
    ``not _git_dirty(worktree)`` would let it slip past every other test in this file, since
    none of them ever dirty the worktree. Here the worktree has an uncommitted edit to a
    TRACKED file, so ``run_experiments`` bails out via the dirty-worktree branch of
    ``cannot_verify`` (not the ``not items`` branch) — the note must still be present.
    """
    _branch_from_head(repo, "pr-1")
    _git("checkout", "pr-1", cwd=repo)
    (repo / "feature.py").write_text("def add(a, b):\n    return a + b\n")
    _git("add", "feature.py", cwd=repo)
    _git("commit", "-m", "add a feature, no changelog entry", cwd=repo)
    wt = _prep_worktree(repo, "pr-1", tmp_path)
    (wt / "test_suite.py").write_text(PASSING_TEST + "# uncommitted edit\n")

    report = judge.run_experiments(
        wt, TEST_CMD, {"experiments": []}, timeout=60, base_branch="dev",
    )

    assert report.state == judge.J_CANNOT_VERIFY
    assert "not clean" in report.cannot_verify
    titles = [n.get("title") for n in report.notes]
    assert "No CHANGELOG.md entry" in titles


def test_run_experiments_carries_the_note_on_a_red_baseline_cannot_verify_report(
    tmp_path, repo, origin,
):
    """DEFEAT_SHAPES #309 round 4: the note is appended BEFORE every early return in
    ``run_experiments``, including the ``not baseline.green`` gate — the LOAD-BEARING one,
    and the most commonly reached in production. Every fixture above it in this file seeds a
    GREEN ``test_suite.py``, so a mutation that resets ``report.notes`` right before this
    ``cannot_verify`` is set would slip past all of them. Here `test_suite.py` itself fails
    on the PR branch, so `run_experiments` bails out via the red-baseline branch — the note
    must still be present.
    """
    _branch_from_head(repo, "pr-1")
    _git("checkout", "pr-1", cwd=repo)
    (repo / "feature.py").write_text("def add(a, b):\n    return a + b\n")
    (repo / "test_suite.py").write_text("def test_ok():\n    assert False\n")
    _git("add", "feature.py", "test_suite.py", cwd=repo)
    _git("commit", "-m", "add a feature, no changelog entry, red baseline", cwd=repo)
    wt = _prep_worktree(repo, "pr-1", tmp_path)

    report = judge.run_experiments(
        wt, TEST_CMD,
        {"experiments": [{
            "guard": "irrelevant", "kind": "mutation", "file": "test_suite.py",
            "before": "assert False", "after": "assert True",
        }]},
        timeout=60, base_branch="dev",
    )

    assert report.state == judge.J_CANNOT_VERIFY
    assert "NOT GREEN" in report.cannot_verify
    titles = [n.get("title") for n in report.notes]
    assert "No CHANGELOG.md entry" in titles


def test_run_experiments_carries_the_note_on_an_unprovisionable_worktree_cannot_verify_report(
    tmp_path, repo, origin, monkeypatch,
):
    """DEFEAT_SHAPES #309 round 4: same shape, the gate one step above the baseline. No
    fixture in this file can cheaply make ``provision_suite_env`` fail for real, so it's
    forced via monkeypatch — the note must still be present on the report it returns.
    """
    _branch_from_head(repo, "pr-1")
    _git("checkout", "pr-1", cwd=repo)
    (repo / "feature.py").write_text("def add(a, b):\n    return a + b\n")
    _git("add", "feature.py", cwd=repo)
    _git("commit", "-m", "add a feature, no changelog entry", cwd=repo)
    wt = _prep_worktree(repo, "pr-1", tmp_path)
    monkeypatch.setattr(judge, "provision_suite_env", lambda worktree, timeout=600.0: "boom")

    report = judge.run_experiments(
        wt, TEST_CMD,
        {"experiments": [{
            "guard": "irrelevant", "kind": "mutation", "file": "test_suite.py",
            "before": "assert True", "after": "assert False",
        }]},
        timeout=60, base_branch="dev",
    )

    assert report.state == judge.J_CANNOT_VERIFY
    assert "PROVISIONED" in report.cannot_verify
    titles = [n.get("title") for n in report.notes]
    assert "No CHANGELOG.md entry" in titles


def test_run_experiments_carries_the_note_on_a_contamination_cannot_verify_report(
    tmp_path, repo, origin, monkeypatch,
):
    """DEFEAT_SHAPES #309 round 4: the last of the three uncovered early returns below
    ``_git_dirty`` — a mutation that could not be restored. Rare and expensive to trigger
    for real (the only experiment-running fixture in this file restores cleanly), so it's
    forced via monkeypatch — the note must still be present on the report it returns.
    """
    _branch_from_head(repo, "pr-1")
    _git("checkout", "pr-1", cwd=repo)
    (repo / "feature.py").write_text("def add(a, b):\n    return a + b\n")
    _git("add", "feature.py", cwd=repo)
    _git("commit", "-m", "add a feature, no changelog entry", cwd=repo)
    wt = _prep_worktree(repo, "pr-1", tmp_path)
    monkeypatch.setattr(
        judge, "_apply_experiments",
        lambda worktree, test_cmd, items, baseline, timeout: ([], "could not restore a mutation"),
    )

    report = judge.run_experiments(
        wt, TEST_CMD,
        {"experiments": [{
            "guard": "irrelevant", "kind": "mutation", "file": "test_suite.py",
            "before": "assert True", "after": "assert False",
        }]},
        timeout=60, base_branch="dev",
    )

    assert report.state == judge.J_CANNOT_VERIFY
    assert report.cannot_verify == "could not restore a mutation"
    titles = [n.get("title") for n in report.notes]
    assert "No CHANGELOG.md entry" in titles


def test_run_experiments_carries_no_note_when_the_changelog_was_touched(tmp_path, repo, origin):
    _branch_from_head(repo, "pr-1")
    _git("checkout", "pr-1", cwd=repo)
    (repo / "feature.py").write_text("def add(a, b):\n    return a + b\n")
    (repo / "CHANGELOG.md").write_text(
        "## [Unreleased]\n\n### Added\n\n- A feature. (#1)\n"
    )
    _git("add", "feature.py", "CHANGELOG.md", cwd=repo)
    _git("commit", "-m", "add a feature, with a changelog entry", cwd=repo)
    wt = _prep_worktree(repo, "pr-1", tmp_path)

    report = judge.run_experiments(
        wt, TEST_CMD, {"experiments": []}, timeout=60, base_branch="dev",
    )

    titles = [n.get("title") for n in report.notes]
    assert "No CHANGELOG.md entry" not in titles


# --- round 5: the append must coexist with agent notes, and both must survive rendering -----

def test_run_experiments_keeps_agent_notes_alongside_the_changelog_note(
    tmp_path, repo, origin,
):
    """DEFEAT_SHAPES #309 round 5: the changelog note is APPENDED to whatever notes the judge
    agent already wrote — it must never REPLACE them. No other fixture in this file ever
    passes agent notes alongside a firing changelog note, so a mutation that assigns
    ``report.notes = [changelog_note]`` instead of appending would silently eat every agent
    note on exactly the PRs this feature fires on, and every test above would stay green.
    """
    _branch_from_head(repo, "pr-1")
    _git("checkout", "pr-1", cwd=repo)
    (repo / "feature.py").write_text("def add(a, b):\n    return a + b\n")
    _git("add", "feature.py", cwd=repo)
    _git("commit", "-m", "add a feature, no changelog entry, with agent notes", cwd=repo)
    wt = _prep_worktree(repo, "pr-1", tmp_path)

    report = judge.run_experiments(
        wt, TEST_CMD,
        {"experiments": [], "notes": [{"title": "naming", "body": "call it `cue`"}]},
        timeout=60, base_branch="dev",
    )

    titles = [n.get("title") for n in report.notes]
    assert "naming" in titles                       # the agent's own note survived
    assert "No CHANGELOG.md entry" in titles         # ...alongside the mechanical one


def test_comment_body_renders_the_changelog_note_title_and_body(tmp_path, repo, origin):
    """DEFEAT_SHAPES #309 round 5: the note's TITLE — the literal string 'No CHANGELOG.md
    entry' that CONTRIBUTING.md and the CHANGELOG entry both promise — must survive
    rendering, not just live in the in-memory note dict. Every title assertion above this
    one reads ``report.notes[i]['title']`` directly; none reads a rendered comment, so a
    mutation that dead-codes the rendered title to a fallback would slip past all of them.
    """
    _branch_from_head(repo, "pr-1")
    _git("checkout", "pr-1", cwd=repo)
    (repo / "feature.py").write_text("def add(a, b):\n    return a + b\n")
    _git("add", "feature.py", cwd=repo)
    _git("commit", "-m", "add a feature, no changelog entry, nothing to mutate", cwd=repo)
    wt = _prep_worktree(repo, "pr-1", tmp_path)

    report = judge.run_experiments(
        wt, TEST_CMD, {"experiments": []}, timeout=60, base_branch="dev",
    )

    assert report.state == judge.J_CANNOT_VERIFY
    body = judge.comment_body(report, "https://github.com/o/r/pull/9", TEST_CMD)
    assert "No CHANGELOG.md entry" in body
    assert "add an entry under `## [Unreleased]`" in body


def test_block_body_renders_the_changelog_note_on_a_survived_verdict(tmp_path, repo, origin):
    """DEFEAT_SHAPES #309 round 5: the note must reach the verdict COMMENT on every report
    state, including BLOCKED — the comment a SURVIVED verdict writes, and the one a rework
    agent actually reads. ``comment_body``'s notes section is exercised above; ``block_body``
    was never exercised with a note at all, so a mutation that drops its notes section (or
    renders an empty list instead of ``report.notes``) would slip past this whole file.
    """
    _branch_from_head(repo, "pr-1")
    _git("checkout", "pr-1", cwd=repo)
    (repo / "feature.py").write_text("def add(a, b):\n    return a + b\n")
    _git("add", "feature.py", cwd=repo)
    _git("commit", "-m", "add a feature with an unguarded mutation, no changelog entry", cwd=repo)
    wt = _prep_worktree(repo, "pr-1", tmp_path)

    report = judge.run_experiments(
        wt, TEST_CMD,
        {"experiments": [{
            "guard": "add really adds", "kind": "mutation", "file": "feature.py",
            "before": "return a + b", "after": "return a - b",
        }]},
        timeout=60, base_branch="dev",
    )

    assert report.state == judge.J_BLOCKED
    assert report.blocking

    body = judge.block_body(report, "https://github.com/o/r/pull/9", TEST_CMD)
    assert "No CHANGELOG.md entry" in body
    assert "add an entry under `## [Unreleased]`" in body


def test_comment_body_renders_agent_notes_alongside_the_changelog_note(tmp_path, repo, origin):
    """DEFEAT_SHAPES #309 round 7: 'notes survive coexistence in report.notes' (round 5's
    ``test_run_experiments_keeps_agent_notes_alongside_the_changelog_note``) and 'the
    rendered title reads the real value' (round 5's two rendering fixtures above) were each
    proven with a single-witness fixture — the coexistence fixture never renders, and the
    rendering fixtures each build a notes list with exactly ONE entry. Their CONJUNCTION — a
    rendered comment built from a notes list holding two or more entries — was never tested,
    so `_notes_section` iterating `notes[:1]` instead of `notes` stayed green: the changelog
    note is APPENDED (always last), so slicing to the first entry silently drops it from
    every comment on every PR that also carries an agent note — the normal case.
    """
    _branch_from_head(repo, "pr-1")
    _git("checkout", "pr-1", cwd=repo)
    (repo / "feature.py").write_text("def add(a, b):\n    return a + b\n")
    _git("add", "feature.py", cwd=repo)
    _git("commit", "-m", "add a feature, no changelog entry, with agent notes", cwd=repo)
    wt = _prep_worktree(repo, "pr-1", tmp_path)

    report = judge.run_experiments(
        wt, TEST_CMD,
        {"experiments": [], "notes": [{"title": "naming", "body": "call it `cue`"}]},
        timeout=60, base_branch="dev",
    )

    assert report.state == judge.J_CANNOT_VERIFY
    body = judge.comment_body(report, "https://github.com/o/r/pull/9", TEST_CMD)
    assert "naming" in body
    assert "call it `cue`" in body
    assert "No CHANGELOG.md entry" in body
    assert "add an entry under `## [Unreleased]`" in body


def test_block_body_renders_agent_notes_alongside_the_changelog_note(tmp_path, repo, origin):
    """Same conjunction gap as above, through the OTHER renderer: `block_body` is the comment
    a SURVIVED verdict writes, and the one a rework agent actually reads first. Round 5's
    block_body fixture never passed agent notes either, so `notes[:1]` reaches the more
    commonly-read renderer just as invisibly.
    """
    _branch_from_head(repo, "pr-1")
    _git("checkout", "pr-1", cwd=repo)
    (repo / "feature.py").write_text("def add(a, b):\n    return a + b\n")
    _git("add", "feature.py", cwd=repo)
    _git("commit", "-m", "add a feature with an unguarded mutation, no changelog entry", cwd=repo)
    wt = _prep_worktree(repo, "pr-1", tmp_path)

    report = judge.run_experiments(
        wt, TEST_CMD,
        {
            "experiments": [{
                "guard": "add really adds", "kind": "mutation", "file": "feature.py",
                "before": "return a + b", "after": "return a - b",
            }],
            "notes": [{"title": "naming", "body": "call it `cue`"}],
        },
        timeout=60, base_branch="dev",
    )

    assert report.state == judge.J_BLOCKED
    assert report.blocking

    body = judge.block_body(report, "https://github.com/o/r/pull/9", TEST_CMD)
    assert "naming" in body
    assert "call it `cue`" in body
    assert "No CHANGELOG.md entry" in body
    assert "add an entry under `## [Unreleased]`" in body
