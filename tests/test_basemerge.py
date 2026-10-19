"""🔀 CMX-262 against a real bare origin + clone: clean + green ⇒ pushed; else nothing pushed.

CMX-264: the tick wiring runs with `basemerge.attempt` stubbed, through the same gh stub as
tests/test_dispatcher_ci.py."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from chela import basemerge, dispatcher, judge
from tests.test_dispatcher_ci import _FakeGh, _row, _tick, _wf

PYTEST = f"{sys.executable} -m pytest -q -p no:cacheprovider"
MOD = "def f():\n    return 1\n\n\ndef g():\n    return 2\n"
TEST = "from mod import f\n\n\ndef test_f():\n    assert f() == 1\n"


def _g(cwd, *argv) -> str:
    return subprocess.run(["git", *argv], cwd=cwd, check=True, capture_output=True,
                          text=True).stdout.strip()


def _commit(cwd, files: dict[str, str], msg: str) -> str:
    for rel, text in files.items():
        (Path(cwd) / rel).parent.mkdir(parents=True, exist_ok=True)
        (Path(cwd) / rel).write_text(text)
    _g(cwd, "add", *files)
    return _g(cwd, "commit", "-qm", msg) or _g(cwd, "rev-parse", "HEAD")


@pytest.fixture
def fx(tmp_path):
    """origin (bare) with `dev`, and a PR branch `feat` that touched `mod.py` + its test."""
    origin, repo = tmp_path / "origin.git", tmp_path / "repo"
    _g(tmp_path, "init", "-q", "--bare", "-b", "dev", str(origin))
    _g(tmp_path, "clone", "-q", "-c", "user.name=t", "-c", "user.email=t@example.com",
       str(origin), str(repo))
    _commit(repo, {"mod.py": MOD, "tests/test_mod.py": TEST, "other.py": "x = 1\n"}, "base")
    _g(repo, "push", "-q", "origin", "dev")
    _g(repo, "checkout", "-qb", "feat")
    head = _commit(repo, {"mod.py": MOD.replace("return 2", "return 3")}, "pr")
    _g(repo, "push", "-q", "origin", "feat")
    _g(repo, "checkout", "-q", "dev")
    return repo, head


def _dev(repo, files) -> str:
    _g(repo, "checkout", "-q", "dev")
    sha = _commit(repo, files, "dev moved")
    return _g(repo, "push", "-q", "origin", "dev") or sha


def _origin_feat(repo) -> str:
    return _g(repo, "ls-remote", "origin", "refs/heads/feat").split()[0]


def test_a_clean_base_merge_runs_the_targeted_tests_and_pushes(fx, monkeypatch):
    repo, head = fx
    base = _dev(repo, {"other.py": "x = 2\n"})
    ran = []
    real = judge.run_suite
    monkeypatch.setattr(judge, "run_suite", lambda cmd, cwd, timeout: (
        ran.append((cmd, Path(cwd).name, Path(cwd).parent.name[:16], timeout))
        or real(cmd, cwd, timeout)))

    res = basemerge.attempt(repo, "feat", "dev", PYTEST)

    assert res["outcome"] == "merged"
    assert (res["base_sha"], res["head_sha"]) == (base, head)
    assert ran == [(f"{PYTEST} tests/test_mod.py", "wt", "chela-basemerge-", 900)]
    assert (res["files"], _origin_feat(repo)) == (["tests/test_mod.py"], res["merge_sha"])
    assert _g(repo, "rev-parse", f"{res['merge_sha']}^@").split() == [head, base]
    assert len(_g(repo, "worktree", "list").splitlines()) == 1                      # the scratch worktree is gone


def test_a_real_conflict_lists_the_files_and_pushes_nothing(fx):
    repo, head = fx
    _dev(repo, {"mod.py": MOD.replace("return 2", "return 4")})
    agent = repo.parent / "agent-wt"                       # the run's own worktree, mid-edit
    _g(repo, "worktree", "add", "-q", str(agent), "feat")
    (agent / "mod.py").write_text("half-typed")

    res = basemerge.attempt(repo, "feat", "dev", PYTEST)

    assert (res["outcome"], res["files"], res["merge_sha"]) == ("conflict", ["mod.py"], None)
    assert _origin_feat(repo) == head == _g(agent, "rev-parse", "HEAD")
    assert (agent / "mod.py").read_text() == "half-typed"   # never touched
    assert len(_g(repo, "worktree", "list").splitlines()) == 2


def test_a_clean_merge_whose_targeted_tests_go_red_is_not_pushed(fx):
    repo, head = fx
    _dev(repo, {"tests/test_mod.py": TEST.replace("== 1", "== 9")})

    res = basemerge.attempt(repo, "feat", "dev", PYTEST)

    assert (res["outcome"], res["files"], _origin_feat(repo)) == ("red", ["tests/test_mod.py"], head)
    assert "1 failed" in res["detail"]


def test_a_branch_that_already_has_the_base_is_current_and_pushes_nothing(fx):
    repo, head = fx
    res = basemerge.attempt(repo, "feat", "dev", PYTEST)
    assert (res["outcome"], _origin_feat(repo)) == ("current", head)


_BOOM = judge.SuiteResult(True, 1, 0, 1, 0, "boom")
_HUNG = judge.SuiteResult(True, 0, 0, 0, 0, "", timed_out=True)


@pytest.mark.parametrize("cmd, suite, ran_as, outcome", [
    ("", _BOOM, [], "merged"), ("make test", _BOOM, ["make test"], "red"),
    (PYTEST, _HUNG, [f"{PYTEST} tests/test_mod.py"], "red")])
def test_no_test_cmd_pushes_an_unextendable_one_runs_as_is_a_hung_one_is_red(
        fx, monkeypatch, cmd, suite, ran_as, outcome):
    repo, head = fx
    _dev(repo, {"other.py": "x = 2\n"})
    ran = []
    monkeypatch.setattr(judge, "run_suite", lambda c, cwd, timeout: ran.append(c) or suite)
    res = basemerge.attempt(repo, "feat", "dev", cmd)
    assert (ran, res["outcome"]) == (ran_as, outcome)
    assert _origin_feat(repo) == (res["merge_sha"] if outcome == "merged" else head)


def test_a_suite_that_never_ran_raises_and_pushes_nothing(fx, monkeypatch):
    repo, head = fx
    _dev(repo, {"other.py": "x = 2\n"})
    monkeypatch.setattr(judge, "run_suite", lambda *a, **k: judge.SuiteResult(
        False, -1, 0, 0, 0, "", "no shell"))
    with pytest.raises(RuntimeError, match="could not run: no shell"):
        basemerge.attempt(repo, "feat", "dev", PYTEST)
    assert _origin_feat(repo) == head


def test_an_unfetchable_branch_raises_and_leaves_no_worktree(fx):
    repo, _ = fx
    with pytest.raises(RuntimeError, match="fetch"):
        basemerge.attempt(repo, "gone", "dev", PYTEST)
    assert len(_g(repo, "worktree", "list").splitlines()) == 1


def test_a_failed_merge_naming_no_file_raises_and_cleans_up(fx, monkeypatch):
    repo, _ = fx
    _dev(repo, {"other.py": "x = 2\n"})
    real = basemerge._git
    monkeypatch.setattr(basemerge, "_git", lambda cwd, *a, **k: (
        (_ for _ in ()).throw(RuntimeError("merge refused")) if a[0] == "merge"
        else real(cwd, *a, **k)))
    with pytest.raises(RuntimeError, match="naming no file"):
        basemerge.attempt(repo, "feat", "dev", PYTEST)
    assert len(_g(repo, "worktree", "list").splitlines()) == 1


def test_git_that_cannot_start_is_a_runtime_error(monkeypatch, tmp_path):
    monkeypatch.setattr(basemerge.subprocess, "run", lambda *a, **k: open(tmp_path / "no/git"))
    with pytest.raises(RuntimeError, match="could not run"):
        basemerge._git(tmp_path, "status")


def test_targeted_tests_are_the_prs_tests_and_the_importers_of_its_sources(tmp_path):
    for rel, text in {"tests/test_a.py": "import pkg.mod\n", "tests/test_b.py": "import os\n",
                      "tests/test_c.py": "from pkg import mod\n", "pkg/mod.py": "",
                      "tests/sub/test_d.py": "from pkg.mod import f\n", "tests/helpers.py": "",
                      "tests/test_e.py": "import helpers\n"}.items():
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text(text)
    assert basemerge.targeted_tests(tmp_path, ["pkg/mod.py", "tests/test_b.py",
                                               "tests/test_gone.py", "README.md"]) == [
        "tests/sub/test_d.py", "tests/test_a.py", "tests/test_b.py", "tests/test_c.py"]
    assert basemerge.targeted_tests(tmp_path, ["tests/helpers.py", "tests/conftest.py"]) == [
        "tests/test_e.py"]
    assert basemerge.targeted_tests(tmp_path, ["README.md"]) == []


def test_the_rework_bodies_name_the_base_sha_and_the_files():
    res = {"base_sha": "abcdef1234567890", "files": ["a.py", "b`\x1b[2Jc.py"], "detail": "x ``` y"}
    body = basemerge.rework_body({**res, "outcome": "conflict"}, "dev")
    assert "Conflict with base after `abcdef123456` merged: merge origin/dev and resolve" in body
    assert "- `a.py`\n- `bc.py`\n" in body and "x ``` y" not in body and "not charged" in body
    red = basemerge.rework_body({**res, "outcome": "red"}, "dev")
    assert "targeted tests go red" in red and "\n````\nx ``` y\n````\n" in red
    assert "- (none)" in basemerge.rework_body({**res, "files": [], "outcome": "red"}, "dev")


# --- the tick wiring --------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _own_runs_db(tmp_path, monkeypatch):
    monkeypatch.setattr(dispatcher, "DB_PATH", tmp_path / "scheduler.db")


def _result(outcome, files=("mod.py",)):
    return {"outcome": outcome, "base_sha": "b" * 40, "head_sha": "deadbee1",
            "merge_sha": "m" * 40 if outcome == "merged" else None, "files": list(files),
            "detail": ""}


@pytest.fixture
def tried(monkeypatch):
    calls, answer = [], {"res": _result("conflict")}
    monkeypatch.setattr(basemerge, "attempt", lambda *a: calls.append(a) or answer["res"])
    return calls, answer


class _Gh(_FakeGh):
    """`gh pr view --json state,mergeable` answers ``self.mergeable`` (the base stub: MERGEABLE)."""

    def run(self, cmd, *a, **k):
        r = super().run(cmd, *a, **k)
        if isinstance(cmd, list) and cmd[:3] == ["gh", "pr", "view"] and "state,mergeable" in cmd:
            r.stdout = json.dumps({"state": "OPEN", "mergeable": self.mergeable})
        return r


def _setup(tmp_path, **row):
    wf = _wf(tmp_path, judge={"test_cmd": "pytest -q", "enabled": False})
    with dispatcher._db() as conn:
        _row(conn, workflow_path=str(wf.path), **row)
    return wf, _Gh(mergeable="CONFLICTING")


def test_conflicting_on_two_ticks_tries_once_per_head_and_reworks_free(tmp_path, tried):
    calls, _ = tried
    wf, fake = _setup(tmp_path, rework_count=4)        # the normal-risk cap is spent

    assert _tick(wf, fake)["base_conflict"] == 0       # one read may be transient
    assert calls == []
    summary = _tick(wf, fake)

    assert calls == [(str(tmp_path), "test-1", "dev", "pytest -q")]
    assert (summary["base_conflict"], summary["escalated"], summary["reworked"]) == (1, 0, 1)
    run = dispatcher.resolve_run("abc123")
    assert (run["status"], run["rework_count"], run["conflict_reworks"]) == ("running", 5, 1)
    assert run["conflict_sha"] == "deadbee1"
    assert "Conflict with base after `bbbbbbbbbbbb` merged" in dispatcher.latest_verdict(run)
    assert dispatcher.effective_rework_cap(run) == 5


def test_a_conflict_already_handled_on_this_head_is_not_retried(tmp_path, tried):
    calls, _ = tried
    wf, fake = _setup(tmp_path, pr_mergeable="CONFLICTING", conflict_sha="deadbee1")
    _tick(wf, fake)
    assert calls == []


def test_a_mergeable_read_resets_the_two_tick_wait(tmp_path, tried):
    calls, _ = tried
    wf, fake = _setup(tmp_path, pr_mergeable="CONFLICTING")
    fake.mergeable = "MERGEABLE"
    _tick(wf, fake)
    fake.mergeable = "CONFLICTING"
    _tick(wf, fake)
    assert calls == []


def test_a_clean_merge_is_recorded_on_the_pr_and_not_sent_back(tmp_path, tried):
    calls, answer = tried
    answer["res"] = _result("merged", files=["tests/test_mod.py"])
    wf, fake = _setup(tmp_path, pr_mergeable="CONFLICTING")

    summary = _tick(wf, fake)

    assert (summary["base_merged"], summary["base_conflict"], len(calls)) == (1, 0, 1)
    run = dispatcher.resolve_run("abc123")
    assert (run["status"], run["rework_count"], run["conflict_reworks"]) == (
        "awaiting_review", 0, 0)
    assert run["conflict_sha"] == "deadbee1"
    assert any("Base merged automatically: origin/dev `bbbbbbbbbbbb` → merge commit "
               "`mmmmmmmmmmmm` (1 targeted test" in c for c in fake.comments)


def test_a_red_merge_reworks_free(tmp_path, tried):
    _, answer = tried
    answer["res"] = _result("red", files=["tests/test_mod.py"])
    wf, fake = _setup(tmp_path, pr_mergeable="CONFLICTING")
    assert _tick(wf, fake)["base_conflict"] == 1
    run = dispatcher.resolve_run("abc123")
    assert run["conflict_reworks"] == 1 and "targeted tests go red" in \
        dispatcher.latest_verdict(run)


def test_current_burns_the_sha_without_a_comment_or_rework(tmp_path, tried):
    _, answer = tried
    answer["res"] = _result("current")
    wf, fake = _setup(tmp_path, pr_mergeable="CONFLICTING")
    summary = _tick(wf, fake)
    run = dispatcher.resolve_run("abc123")
    assert (summary["base_merged"], summary["base_conflict"]) == (0, 0)
    assert (run["conflict_sha"], run["status"]) == ("deadbee1", "awaiting_review")
    assert not any("Base merged" in c for c in fake.comments)


def test_an_attempt_that_cannot_measure_writes_nothing_and_retries(tmp_path, monkeypatch):
    calls = []

    def boom(*a):
        calls.append(a)
        raise RuntimeError("fetch failed")
    monkeypatch.setattr(basemerge, "attempt", boom)
    wf, fake = _setup(tmp_path, pr_mergeable="CONFLICTING")
    _tick(wf, fake)
    _tick(wf, fake)
    run = dispatcher.resolve_run("abc123")
    assert len(calls) == 2 and run["conflict_sha"] is None and run["status"] == "awaiting_review"


@pytest.mark.parametrize("refusal", [{"ok": False, "error": "moved"}, "db"])
def test_an_unwritten_verdict_undoes_the_burn_and_the_free_round(tmp_path, tried,
                                                                monkeypatch, refusal):
    def refuse(*a, **k):
        if refusal == "db":
            raise dispatcher.sqlite3.OperationalError("locked")
        return refusal
    monkeypatch.setattr(dispatcher, "request_changes", refuse)
    wf, fake = _setup(tmp_path, pr_mergeable="CONFLICTING", conflict_reworks=2,
                      conflict_sha="old")
    assert _tick(wf, fake)["base_conflict"] == 0
    run = dispatcher.resolve_run("abc123")
    assert (run["conflict_sha"], run["conflict_reworks"]) == ("old", 2)


def test_the_inbox_cap_counts_the_free_conflict_rounds():
    from chela import inbox
    assert inbox._effective_rework_cap({"rework_count": 1, "conflict_reworks": 2}) == \
        inbox._effective_rework_cap({"rework_count": 1}) + 2


@pytest.mark.parametrize("row", [{"status": "running", "pr_head_sha": "deadbee1"},
                                 {"branch_name": ""}])
def test_only_an_awaiting_review_run_with_a_branch_is_tried(tmp_path, tried, row):
    calls, _ = tried
    wf, fake = _setup(tmp_path, pr_mergeable="CONFLICTING", **row)
    _tick(wf, fake)
    assert calls == []


def test_a_conflict_with_no_head_sha_known_is_not_tried(tmp_path, tried):
    calls, _ = tried
    wf, fake = _setup(tmp_path, pr_mergeable="CONFLICTING")
    fake.sha = ""
    _tick(wf, fake)
    assert calls == []


def test_the_cap_reads_a_row_that_has_no_conflict_column_as_zero_free_rounds():
    import sqlite3
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT 'normal' AS risk, 1 AS rework_count, 0 AS retry_count").fetchone()
    base = {"risk": "normal", "rework_count": 1, "retry_count": 0}
    assert dispatcher.effective_rework_cap(row) == dispatcher.effective_rework_cap(base) == \
        dispatcher.effective_rework_cap({**base, "conflict_reworks": 2}) - 2
