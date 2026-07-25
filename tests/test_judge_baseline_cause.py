"""⚖️ CMX-177 — a red baseline must NAME ITS CAUSE, not just report an exit code.

``run_experiments`` correctly refuses to run a single mutation against a baseline that is
already red (see ``test_a_baseline_that_is_not_green_verifies_NOTHING`` in ``test_judge.py``)
— but until now it only ever said "the suite is NOT GREEN before any mutation (`…` exited 1:
N failed, M passed)". That names neither WHICH test failed nor WHY: is this branch's own
doing, already broken on ``base_branch`` (so rework here would fix nothing), or a problem
with the judge's own box? Those are three different actions, and an operator reading only an
exit code cannot tell them apart — observed live 2026-07-25 on cmx-174, where a human had to
manually re-run the suite in a scratch worktree to learn the baseline was stale, not broken.
CMX-176 fixed the staleness (the judge worktree is refreshed from ``origin/<base>`` before the
baseline ever runs). These tests pin the diagnosis that runs when the baseline is STILL red
after that refresh: ``judge._diagnose_red_baseline`` checks ``origin/<base>`` ALONE and names
one of "RED ON BASE TOO", "RED ONLY ON THIS BRANCH", or an honest "could not tell".
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from chela import judge

TEST_CMD = f'"{sys.executable}" -m pytest -q'

PASSING_TEST = "def test_ok():\n    assert True\n"
FAILING_TEST = "def test_known_broken():\n    assert False, 'this one is broken on purpose'\n"


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
    """The dispatcher's own clone of origin, on `dev`, seeded with a real pytest suite."""
    work = tmp_path / "repo"
    subprocess.run(["git", "clone", str(origin), str(work)], check=True, capture_output=True)
    for k, v in (("user.email", "t@example.com"), ("user.name", "T"), ("commit.gpgsign", "false")):
        _git("config", k, v, cwd=work)
    (work / "test_suite.py").write_text(PASSING_TEST)
    _git("add", "test_suite.py", cwd=work)
    _git("commit", "-m", "seed: a green suite", cwd=work)
    _git("push", "-u", "origin", "dev", cwd=work)
    return work


def _branch_from_head(repo: Path, name: str) -> str:
    _git("branch", name, cwd=repo)
    return _git("rev-parse", name, cwd=repo).stdout.strip()


def _commit_on(repo: Path, branch: str, contents: str, msg: str) -> None:
    _git("checkout", branch, cwd=repo)
    (repo / "test_suite.py").write_text(contents)
    _git("commit", "-am", msg, cwd=repo)


def _push(repo: Path, branch: str) -> None:
    _git("push", "origin", branch, cwd=repo)


def _detached_worktree(repo: Path, ref: str, path: Path) -> Path:
    _git("worktree", "add", "--detach", str(path), ref, cwd=repo)
    return path


def _exp() -> dict:
    """A harmless real mutation — never reached, since every scenario here stops at the
    red baseline before a single experiment runs. Its shape only has to satisfy
    `Experiment.parse`."""
    return {"guard": "irrelevant", "kind": "mutation", "file": "test_suite.py",
            "before": "assert True", "after": "assert True  # noop"}


# --- _diagnose_red_baseline: the pure git-and-suite mechanics ----------------------------


def test_red_on_base_too_is_named_when_base_shares_the_same_failure(tmp_path, repo, origin):
    """The failure predates the PR: origin/dev never had a passing suite either."""
    _commit_on(repo, "dev", FAILING_TEST, "dev itself is broken")
    _push(repo, "dev")
    _branch_from_head(repo, "pr-1")
    # An unrelated commit on top, so the PR branch's tip differs from origin/dev's — the
    # scenario this guards is "base is ALSO red", not "the PR branch IS base" (that is the
    # separate scenario below).
    _git("checkout", "pr-1", cwd=repo)
    (repo / "unrelated.txt").write_text("noise\n")
    _git("add", "unrelated.txt", cwd=repo)
    _git("commit", "-m", "an unrelated PR commit", cwd=repo)
    wt = _detached_worktree(repo, "pr-1", tmp_path / "wt")
    _git("fetch", "origin", "dev", cwd=wt)

    cause = judge._diagnose_red_baseline(wt, TEST_CMD, "dev", 60)

    assert "RED ON BASE TOO" in cause
    assert "predates the PR" in cause
    # ⛔ the worktree must come back to the PR's own tip — the next thing a caller does is
    # treat this worktree as the artifact under test, and a diagnostic that leaves it on a
    # different commit would corrupt everything after it.
    head = _git("rev-parse", "HEAD", cwd=wt).stdout.strip()
    assert head == _git("rev-parse", "pr-1", cwd=repo).stdout.strip()


def test_red_only_on_this_branch_is_named_when_base_is_green(tmp_path, repo, origin):
    """origin/dev is fine; this branch's own commit is what broke the suite."""
    _branch_from_head(repo, "pr-1")
    _commit_on(repo, "pr-1", FAILING_TEST, "the PR breaks its own suite")
    wt = _detached_worktree(repo, "pr-1", tmp_path / "wt")
    _git("fetch", "origin", "dev", cwd=wt)

    cause = judge._diagnose_red_baseline(wt, TEST_CMD, "dev", 60)

    assert "RED ONLY ON THIS BRANCH" in cause
    assert "own commits" in cause
    head = _git("rev-parse", "HEAD", cwd=wt).stdout.strip()
    assert head == _git("rev-parse", "pr-1", cwd=repo).stdout.strip()


def test_no_base_branch_known_says_so_rather_than_guessing(tmp_path, repo):
    _branch_from_head(repo, "pr-1")
    wt = _detached_worktree(repo, "pr-1", tmp_path / "wt")

    cause = judge._diagnose_red_baseline(wt, TEST_CMD, "", 60)

    assert "no `workspace.base_branch`" in cause


def test_an_unresolvable_base_ref_says_so_rather_than_blocking(tmp_path, repo):
    """No fetch ran (or the base branch is unknown to this worktree) — degrade to an honest
    unknown, never a crash and never a guess dressed up as a fact."""
    _branch_from_head(repo, "pr-1")
    wt = _detached_worktree(repo, "pr-1", tmp_path / "wt")
    # deliberately do NOT fetch origin/dev into this worktree

    cause = judge._diagnose_red_baseline(wt, TEST_CMD, "a-branch-that-was-never-fetched", 60)

    assert "does not resolve" in cause


def test_the_worktree_tip_already_being_base_is_named_not_mistaken_for_a_PR_regression(
    tmp_path, repo, origin,
):
    """The PR branch IS origin/dev's tip (nothing dispatched on top of it yet) — there is no
    PR content to blame, so the cause must say the failure is base's, not "this branch"."""
    _commit_on(repo, "dev", FAILING_TEST, "dev itself is broken")
    _push(repo, "dev")
    wt = _detached_worktree(repo, "dev", tmp_path / "wt")
    _git("fetch", "origin", "dev", cwd=wt)

    cause = judge._diagnose_red_baseline(wt, TEST_CMD, "dev", 60)

    assert "already equals" in cause
    assert "red on base_branch itself" in cause


# --- wired into run_experiments: the cause lands in `cannot_verify` ----------------------


def test_run_experiments_names_the_failing_test_and_the_cause(tmp_path, repo, origin):
    _commit_on(repo, "dev", FAILING_TEST, "dev itself is broken")
    _push(repo, "dev")
    _branch_from_head(repo, "pr-1")
    _git("checkout", "pr-1", cwd=repo)
    (repo / "unrelated.txt").write_text("noise\n")
    _git("add", "unrelated.txt", cwd=repo)
    _git("commit", "-m", "an unrelated PR commit", cwd=repo)
    wt = _detached_worktree(repo, "pr-1", tmp_path / "wt")
    _git("fetch", "origin", "dev", cwd=wt)

    report = judge.run_experiments(
        wt, TEST_CMD, {"experiments": [_exp()]}, timeout=60, base_branch="dev",
    )

    assert report.state == judge.J_CANNOT_VERIFY
    assert "test_known_broken" in report.cannot_verify        # WHICH test, not just a count
    assert "RED ON BASE TOO" in report.cannot_verify           # WHY, not just "exited 1"
    assert report.outcomes == []                               # still never ran a mutation


def test_run_experiments_without_a_base_branch_still_reports_cleanly(tmp_path, repo):
    """Callers that never pass `base_branch` (every existing caller before this PR) must keep
    getting a `cannot_verify` that still parses as a sentence — no crash, no blank cause."""
    _branch_from_head(repo, "pr-1")
    _commit_on(repo, "pr-1", FAILING_TEST, "breaks its own suite")
    wt = _detached_worktree(repo, "pr-1", tmp_path / "wt")

    report = judge.run_experiments(wt, TEST_CMD, {"experiments": [_exp()]}, timeout=60)

    assert report.state == judge.J_CANNOT_VERIFY
    assert "NOT GREEN" in report.cannot_verify
    assert "no `workspace.base_branch`" in report.cannot_verify


# --- rework round: ANSI colour must not defeat the name extraction -----------------------
#
# Filed live against this branch: the judge daemon's own environment carries `FORCE_COLOR=3`
# (inherited by whatever it spawns), and pytest's short summary line under colour is
# `\x1b[31mFAILED\x1b[0m path::test - why` — the escape sits exactly where
# `_RE_PYTEST_FAILED_NAME` anchors (`^FAILED`), so `findall` returned [] and the report fell
# straight back to the bare-count message this feature exists to replace. CI never caught it
# because CI does not force colour; the box the judge actually runs on does.


def test_failing_test_names_survives_a_real_coloured_pytest_summary():
    """A genuine coloured pytest tail (escapes embedded, not a sanitised string standing in
    for one) must still yield the node id. This is the guarantee: remove `_strip_ansi` from
    `_failing_test_names` and this goes red on its own, with no subprocess involved."""
    tail = (
        "\x1b[31m\x1b[1m=================================== FAILURES "
        "===================================\x1b[0m\n"
        "\x1b[31m\x1b[1m_______________________________ test_known_broken "
        "________________________________\x1b[0m\n"
        "\n"
        "    def test_known_broken():\n"
        ">       assert False, 'this one is broken on purpose'\n"
        "\x1b[1m\x1b[31mE       AssertionError: this one is broken on purpose\x1b[0m\n"
        "\n"
        "test_suite.py:1: AssertionError\n"
        "\x1b[36m\x1b[1m=============================== short test summary info "
        "================================\x1b[0m\n"
        "\x1b[31mFAILED\x1b[0m test_suite.py::test_known_broken - AssertionError: this one "
        "is broken on purpose\n"
        "\x1b[31m1 failed\x1b[0m in 0.04s\n"
    )

    assert judge._failing_test_names(tail) == ["test_suite.py::test_known_broken"]


def test_run_experiments_names_the_failing_test_even_when_the_suite_is_forced_to_colour(
    tmp_path, repo, origin,
):
    """End-to-end version of the guard above: force the CHILD suite itself to colour with
    `--color=yes`, which pytest honours over the judge's own `NO_COLOR` hygiene env — so this
    proves the strip in `_failing_test_names`, not the env fix in `run_suite`, is what saves
    the name. Remove the strip and this goes red too."""
    _commit_on(repo, "dev", FAILING_TEST, "dev itself is broken")
    _push(repo, "dev")
    _branch_from_head(repo, "pr-1")
    _git("checkout", "pr-1", cwd=repo)
    (repo / "unrelated.txt").write_text("noise\n")
    _git("add", "unrelated.txt", cwd=repo)
    _git("commit", "-m", "an unrelated PR commit", cwd=repo)
    wt = _detached_worktree(repo, "pr-1", tmp_path / "wt")
    _git("fetch", "origin", "dev", cwd=wt)

    coloured_cmd = f'"{sys.executable}" -m pytest -q --color=yes'
    report = judge.run_experiments(
        wt, coloured_cmd, {"experiments": [_exp()]}, timeout=60, base_branch="dev",
    )

    assert report.state == judge.J_CANNOT_VERIFY
    assert "test_known_broken" in report.cannot_verify
    assert "RED ON BASE TOO" in report.cannot_verify


# --- rework round: a GREEN baseline must never pay for the base_branch diagnostic --------


def test_diagnose_red_baseline_never_runs_against_a_green_baseline(tmp_path, repo, monkeypatch):
    """The cost guard the brief made mandatory: `_diagnose_red_baseline` re-runs the WHOLE
    suite against `origin/<base_branch>` — worth paying for on a RED baseline (it is the only
    way to tell "this branch broke it" from "base was already broken"), but a GREEN baseline
    has nothing to diagnose. Today this holds only because the call sits inside
    `if not baseline.green:` in `run_experiments` — a refactor that hoists it out would
    silently double the wall-clock of every judge run on the common path, and nothing else
    here would fail. This test is what makes that refactor fail."""
    _branch_from_head(repo, "pr-1")
    wt = _detached_worktree(repo, "pr-1", tmp_path / "wt")

    def _must_not_run(*args, **kwargs):
        raise AssertionError("_diagnose_red_baseline ran against a GREEN baseline")

    monkeypatch.setattr(judge, "_diagnose_red_baseline", _must_not_run)

    report = judge.run_experiments(
        wt, TEST_CMD, {"experiments": [_exp()]}, timeout=60, base_branch="dev",
    )

    assert report.baseline is not None
    assert report.baseline.green
