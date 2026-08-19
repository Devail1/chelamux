"""⚖️🕳️ CMX-271 — a deletion-heavy PR must not read CLEAN just because every experiment the
judge proposed happened to be KILLED.

Measured on cmx-268 (#338, 2026-08-13): a pure deletion (`terminals.js` -17, `style.css` -20,
two test files -172 net, +10 total) reached a CLEAN verdict — six proposed experiments, all
KILLED, every one of them corrupting a guard the deletion never touched (a survivor immediately
above or below the deleted lines, in the same file). The battery looked thorough and proved
nothing about the deletion itself. These tests pin `judge._deletion_heavy_diff`, `judge.
_diff_numstat`, and their wiring into `run_experiments`.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from chela import judge

TEST_CMD = f'"{sys.executable}" -m pytest -q'

FEATURE_PY = "def add(a, b):\n    return a + b\n"

# One real guard (`test_add`, covers `add`) plus ten padding tests that exist only to give a
# later deletion enough LINE COUNT to cross MIN_DELETION_HEAVY_LINES — the check is a shape
# measurement, not a semantic one, so padding is a faithful stand-in for "an existing guard".
BASE_TEST_FEATURE = "from feature import add\n\ndef test_add():\n    assert add(2, 3) == 5\n" + "".join(
    f"\n\ndef test_pad_{i:02d}():\n    assert True\n" for i in range(10)
)


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
    (work / "feature.py").write_text(FEATURE_PY)
    (work / "test_feature.py").write_text(BASE_TEST_FEATURE)
    _git("add", "feature.py", "test_feature.py", cwd=work)
    _git("commit", "-m", "seed: a green suite with padding tests to delete later", cwd=work)
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


def _delete_padding_tests(repo: Path) -> None:
    """Keep `test_add` (the only real guard); drop all ten padding tests — a deletion-heavy
    diff (0 lines added, ~30 removed) with no replacement guard, exactly cmx-268's shape."""
    (repo / "test_feature.py").write_text(
        "from feature import add\n\ndef test_add():\n    assert add(2, 3) == 5\n"
    )


# --- _diff_numstat: the pure git mechanics --------------------------------------------------


def test_diff_numstat_parses_added_and_deleted_per_file(tmp_path, repo, origin):
    _branch_from_head(repo, "pr-1")
    _git("checkout", "pr-1", cwd=repo)
    _delete_padding_tests(repo)
    _git("commit", "-am", "drop padding tests", cwd=repo)
    wt = _prep_worktree(repo, "pr-1", tmp_path)

    rows = judge._diff_numstat(wt, "dev")

    assert rows is not None
    by_path = {p: (a, d) for a, d, p in rows}
    assert "test_feature.py" in by_path
    added, deleted = by_path["test_feature.py"]
    assert added == 0
    assert deleted >= 20


def test_diff_numstat_is_none_without_a_base_branch(tmp_path, repo):
    wt = _detached_worktree(repo, "dev", tmp_path / "wt")
    assert judge._diff_numstat(wt, "") is None


def test_diff_numstat_is_none_when_the_base_ref_does_not_resolve(tmp_path, repo):
    wt = _detached_worktree(repo, "dev", tmp_path / "wt")
    assert judge._diff_numstat(wt, "does-not-exist") is None


# --- _deletion_heavy_diff: the shape measurement --------------------------------------------


def test_deletion_heavy_diff_is_true_for_a_pure_deletion(tmp_path, repo, origin):
    _branch_from_head(repo, "pr-1")
    _git("checkout", "pr-1", cwd=repo)
    _delete_padding_tests(repo)
    _git("commit", "-am", "drop padding tests, no replacement guard", cwd=repo)
    wt = _prep_worktree(repo, "pr-1", tmp_path)

    result = judge._deletion_heavy_diff(wt, "dev")

    assert result is not None
    heavy, added, deleted, files = result
    assert heavy is True
    assert added == 0
    assert deleted >= 20
    assert "test_feature.py" in files


def test_deletion_heavy_diff_is_false_when_replacement_lines_balance_the_deletion(tmp_path, repo, origin):
    """The same shape of deletion, but with enough ADDED lines (a real replacement guard) to
    clear the ratio — mirrors cmx-268's own rework, whose total PR diff moved from
    added=10/deleted=209 to added=108/deleted=211 by adding real guard tests back."""
    _branch_from_head(repo, "pr-1")
    _git("checkout", "pr-1", cwd=repo)
    replacement = "from feature import add\n\ndef test_add():\n    assert add(2, 3) == 5\n" + "".join(
        f"\n\ndef test_replacement_{i:02d}():\n    assert add(1, {i}) == 1 + {i}\n" for i in range(10)
    )
    (repo / "test_feature.py").write_text(replacement)
    _git("commit", "-am", "swap padding tests for real replacement guards", cwd=repo)
    wt = _prep_worktree(repo, "pr-1", tmp_path)

    result = judge._deletion_heavy_diff(wt, "dev")

    assert result is not None
    heavy, added, deleted, _files = result
    assert heavy is False
    assert added > 0


def test_deletion_heavy_diff_is_false_below_the_minimum_line_floor(tmp_path, repo, origin):
    """A tiny deletion (well under MIN_DELETION_HEAVY_LINES) must not trip the check —
    otherwise every one-line cleanup PR would get flagged."""
    _branch_from_head(repo, "pr-1")
    _git("checkout", "pr-1", cwd=repo)
    (repo / "test_feature.py").write_text(
        BASE_TEST_FEATURE.replace("\n\ndef test_pad_09():\n    assert True\n", "")
    )
    _git("commit", "-am", "drop one padding test", cwd=repo)
    wt = _prep_worktree(repo, "pr-1", tmp_path)

    result = judge._deletion_heavy_diff(wt, "dev")

    assert result is not None
    heavy, _added, deleted, _files = result
    assert deleted < judge.MIN_DELETION_HEAVY_LINES
    assert heavy is False


def test_deletion_heavy_diff_excludes_prose_paths(tmp_path, repo, origin):
    """A big deletion in a CHANGELOG (prose) must not read as a deletion-heavy CODE diff —
    mirrors `_docs_only_diff` excluding the same paths for the same reason."""
    _branch_from_head(repo, "pr-1")
    _git("checkout", "pr-1", cwd=repo)
    (repo / "CHANGELOG.md").write_text("\n".join(f"- old entry {i}" for i in range(30)) + "\n")
    _git("add", "CHANGELOG.md", cwd=repo)
    _git("commit", "-m", "trim a long changelog", cwd=repo)
    (repo / "CHANGELOG.md").write_text("- kept entry\n")
    _git("commit", "-am", "trim a long changelog for real", cwd=repo)
    wt = _prep_worktree(repo, "pr-1", tmp_path)

    assert judge._deletion_heavy_diff(wt, "dev") is None


def test_deletion_heavy_diff_is_none_without_a_base_branch(tmp_path, repo):
    wt = _detached_worktree(repo, "dev", tmp_path / "wt")
    assert judge._deletion_heavy_diff(wt, "") is None


# --- wired into run_experiments: the CLEAN-but-unproven report -----------------------------


def test_deletion_heavy_pr_with_only_killed_experiments_is_cannot_verify_not_clean(tmp_path, repo, origin):
    """DEFEAT_SHAPES #309 round 7: the CMX-271 deletion-heavy downgrade is a SIXTH
    cannot_verify-setting site in `run_experiments`, on top of the five early `return`s
    rounds 2-4 enumerated — and unlike them it does not `return`, it assigns
    `report.cannot_verify` and falls through. The changelog note fires on this diff (only
    `test_feature.py` changes, never CHANGELOG.md) and lands in `report.notes` well before
    this branch runs; nothing below asserted it was still there, so blanking `report.notes`
    right before this verdict was invisible to the suite.
    """
    _branch_from_head(repo, "pr-1")
    _git("checkout", "pr-1", cwd=repo)
    _delete_padding_tests(repo)
    _git("commit", "-am", "drop padding tests, no replacement guard (cmx-268 shape)", cwd=repo)
    wt = _prep_worktree(repo, "pr-1", tmp_path)

    raw = {
        "experiments": [
            {"guard": "add still adds", "kind": "mutation", "file": "feature.py",
             "before": "return a + b", "after": "return a - b"},
        ],
    }
    report = judge.run_experiments(wt, TEST_CMD, raw, timeout=60, base_branch="dev")

    assert all(o.verdict == judge.KILLED for o in report.outcomes)
    assert report.state == judge.J_CANNOT_VERIFY
    assert "DELETION-HEAVY" in report.cannot_verify
    assert "cmx-268" in report.cannot_verify
    assert report.blocking == []
    assert any(n.get("title") == "No CHANGELOG.md entry" for n in report.notes)


def test_deletion_heavy_pr_with_a_survived_experiment_still_blocks(tmp_path, repo, origin):
    """A real SURVIVED finding must never be masked by the deletion-heavy downgrade — the
    `not any(o.blocking ...)` guard is what this pins."""
    _branch_from_head(repo, "pr-1")
    _git("checkout", "pr-1", cwd=repo)
    _delete_padding_tests(repo)
    (repo / "feature.py").write_text(FEATURE_PY + "\n\ndef mul(a, b):\n    return a * b\n")
    _git("commit", "-am", "drop padding tests AND add an untested function", cwd=repo)
    wt = _prep_worktree(repo, "pr-1", tmp_path)

    raw = {
        "experiments": [
            {"guard": "mul still multiplies", "kind": "mutation", "file": "feature.py",
             "before": "return a * b", "after": "return a"},
        ],
    }
    report = judge.run_experiments(wt, TEST_CMD, raw, timeout=60, base_branch="dev")

    assert any(o.verdict == judge.SURVIVED for o in report.outcomes)
    assert report.state == judge.J_BLOCKED
    assert report.cannot_verify == ""


def test_a_normal_non_deletion_pr_stays_clean(tmp_path, repo, origin):
    """Regression check: a PR that adds a real guard (not deletion-heavy) must not be swept
    into cannot_verify by this new check."""
    _branch_from_head(repo, "pr-1")
    _git("checkout", "pr-1", cwd=repo)
    (repo / "feature.py").write_text(FEATURE_PY + "\n\ndef mul(a, b):\n    return a * b\n")
    (repo / "test_feature.py").write_text(
        BASE_TEST_FEATURE.replace(
            "from feature import add",
            "from feature import add, mul",
        ) + "\n\ndef test_mul():\n    assert mul(2, 3) == 6\n"
    )
    _git("commit", "-am", "add mul plus its guard", cwd=repo)
    wt = _prep_worktree(repo, "pr-1", tmp_path)

    raw = {
        "experiments": [
            {"guard": "mul still multiplies", "kind": "mutation", "file": "feature.py",
             "before": "return a * b", "after": "return a"},
        ],
    }
    report = judge.run_experiments(wt, TEST_CMD, raw, timeout=60, base_branch="dev")

    assert report.state == judge.J_CLEAN
    assert report.cannot_verify == ""
