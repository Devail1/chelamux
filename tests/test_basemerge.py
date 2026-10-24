"""🔀 CMX-262 against a real bare origin + clone: clean + green ⇒ pushed; else nothing pushed.

CMX-264: the tick wiring runs with `basemerge.attempt` stubbed, through the same gh stub as
tests/test_dispatcher_ci.py."""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from chela import basemerge, dispatcher, generated, judge
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


def test_a_suite_that_never_ran_is_infra_and_pushes_nothing(fx, monkeypatch):
    repo, head = fx
    _dev(repo, {"other.py": "x = 2\n"})
    monkeypatch.setattr(judge, "run_suite", lambda *a, **k: judge.SuiteResult(
        False, -1, 0, 0, 0, "", "no shell"))
    res = basemerge.attempt(repo, "feat", "dev", PYTEST)
    assert (res["outcome"], res["files"]) == ("infra", ["tests/test_mod.py"])
    assert res["detail"] == "the targeted tests could not run: no shell"
    assert res["merge_sha"] and _origin_feat(repo) == head
    assert len(_g(repo, "worktree", "list").splitlines()) == 1


# --- 🏗️ CMX-323: a scratch worktree with no environment is provisioned; a no-start is infra --

@pytest.mark.skipif(shutil.which("uv") is None, reason="needs uv on PATH")
def test_a_runner_that_fails_to_spawn_is_infra_not_red(fx):
    """The live shape: uv's own "Failed to spawn" (exit 2, no test ran), unmocked."""
    repo, head = fx
    _dev(repo, {"other.py": "x = 2\n"})
    res = basemerge.attempt(repo, "feat", "dev", "uv run --no-project no-such-runner-cmx323")
    assert (res["outcome"], _origin_feat(repo)) == ("infra", head)
    assert "never started (exit 2, no test ran)" in res["detail"]
    assert "Failed to spawn" in res["detail"]


def test_nothing_collected_is_infra_not_red(fx):
    repo, head = fx
    _dev(repo, {"other.py": "x = 2\n"})
    res = basemerge.attempt(repo, "feat", "dev", f"{PYTEST} -k no_such_test_cmx323")
    assert (res["outcome"], _origin_feat(repo)) == ("infra", head)
    assert "never started (exit 5, no test ran)" in res["detail"]


def test_a_collection_error_the_merge_caused_is_still_red(fx):
    """Negative control for exit 2: an ImportError the base brought in IS the merge's red."""
    repo, head = fx
    _dev(repo, {"tests/test_mod.py": TEST.replace("import f", "import nope")})
    res = basemerge.attempt(repo, "feat", "dev", PYTEST)
    assert (res["outcome"], _origin_feat(repo)) == ("red", head)
    assert "1 error" in res["detail"]


def test_the_scratch_worktree_is_provisioned_before_the_tests_run(fx, monkeypatch):
    repo, _ = fx
    _dev(repo, {"other.py": "x = 2\n"})
    calls = []
    monkeypatch.setattr(judge, "provision_suite_env", lambda wt: calls.append(
        ("provision", Path(wt).name, Path(wt).parent.name[:16])) or "")
    monkeypatch.setattr(judge, "run_suite", lambda c, cwd, timeout: calls.append(
        ("suite", Path(cwd).name)) or judge.SuiteResult(True, 0, 1, 0, 0, "1 passed"))
    res = basemerge.attempt(repo, "feat", "dev", PYTEST)
    assert res["outcome"] == "merged"
    assert calls == [("provision", "wt", "chela-basemerge-"), ("suite", "wt")]


def test_a_scratch_worktree_that_cannot_be_provisioned_is_infra_and_runs_nothing(fx,
                                                                                 monkeypatch):
    repo, head = fx
    _dev(repo, {"other.py": "x = 2\n"})
    ran = []
    monkeypatch.setattr(judge, "provision_suite_env", lambda wt: "`uv sync` failed (exit 2)")
    monkeypatch.setattr(judge, "run_suite", lambda *a, **k: ran.append(a))
    res = basemerge.attempt(repo, "feat", "dev", PYTEST)
    assert (res["outcome"], ran, _origin_feat(repo)) == ("infra", [], head)
    assert res["detail"] == ("the scratch worktree could not be provisioned: "
                             "`uv sync` failed (exit 2)")
    assert len(_g(repo, "worktree", "list").splitlines()) == 1


def test_a_real_uv_project_with_no_venv_gets_one_and_runs_its_tests(fx, monkeypatch):
    """The scratch worktree has no .venv: provisioning makes one (a stubbed `uv sync` that
    links this test's own venv; the real one needs the network) and the tests RUN, green."""
    repo, _ = fx
    _commit(repo, {"pyproject.toml": "[project]\nname = 'x'\nversion = '0'\n"}, "uv project")
    _g(repo, "push", "-q", "origin", "dev")
    _dev(repo, {"other.py": "x = 2\n"})
    synced, run, which = [], subprocess.run, shutil.which

    def fake_uv(argv, *a, cwd=None, **k):
        if argv[:2] != ["uv", "sync"]:
            return run(argv, *a, cwd=cwd, **k)
        synced.append((argv, Path(cwd).name))
        (Path(cwd) / ".venv").symlink_to(sys.prefix)     # a venv with pytest in it
        return subprocess.CompletedProcess(argv, 0, "", "")
    monkeypatch.setattr(shutil, "which", lambda n, *a, **k: "/usr/bin/uv" if n == "uv"
                        else which(n, *a, **k))
    monkeypatch.setattr(subprocess, "run", fake_uv)
    res = basemerge.attempt(repo, "feat", "dev",
                            "sh -c './.venv/bin/python -m pytest -q -p no:cacheprovider "
                            "tests/test_mod.py'")
    assert synced == [(["uv", "sync", "--all-extras", "--quiet"], "wt")]
    assert (res["outcome"], res["files"]) == ("merged", ["tests/test_mod.py"]), res["detail"]


@pytest.mark.parametrize("code, passed, failed, errors, expected", [
    (2, 0, 0, 0, True), (3, 0, 0, 0, True), (4, 0, 0, 0, True), (5, 0, 0, 0, True),
    (0, 0, 0, 0, False), (1, 0, 0, 0, False), (6, 0, 0, 0, False), (-1, 0, 0, 0, False),
    (2, 0, 0, 1, False), (2, 1, 0, 0, False), (2, 0, 1, 0, False), (1, 0, 1, 0, False)])
def test_never_started_is_a_no_start_code_with_no_test_run_and_no_error(
        code, passed, failed, errors, expected):
    assert basemerge.never_started(
        judge.SuiteResult(True, code, passed, failed, errors, "")) is expected


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


def test_past_three_free_conflict_rounds_a_conflict_is_charged_and_escalates(tmp_path, tried):
    wf, fake = _setup(tmp_path, pr_mergeable="CONFLICTING", rework_count=7, conflict_reworks=3)
    assert dispatcher.effective_rework_cap(dispatcher.resolve_run("abc123")) == 7
    summary = _tick(wf, fake)
    run = dispatcher.resolve_run("abc123")
    assert (summary["escalated"], run["conflict_reworks"]) == (1, 3)
    assert run["status"] != "running"


def test_an_attempt_that_raises_oserror_writes_nothing(tmp_path, monkeypatch):
    def boom(*a):
        raise PermissionError("scratch dir")
    monkeypatch.setattr(basemerge, "attempt", boom)
    wf, fake = _setup(tmp_path, pr_mergeable="CONFLICTING")
    assert _tick(wf, fake)["base_conflict"] == 0
    assert dispatcher.resolve_run("abc123")["conflict_sha"] is None


def test_a_clean_merge_with_no_targeted_tests_says_so(tmp_path, tried):
    _, answer = tried
    answer["res"] = _result("merged", files=[])
    wf, fake = _setup(tmp_path, pr_mergeable="CONFLICTING")
    _tick(wf, fake)
    assert any("(no targeted tests ran; CI gates it)" in c for c in fake.comments)
    assert not any("0 targeted" in c for c in fake.comments)


# --- 🏗️ CMX-323: tests that could not start are infra: counted, reported, never sent back ---

@pytest.fixture
def notices(monkeypatch):
    from chela import inbox
    got = []
    monkeypatch.setattr(inbox, "enqueue", lambda kind, summary, payload: got.append(
        (kind, summary, payload)) or True)
    return got


def _infra(detail="error: Failed to spawn: `pytest`\n  Caused by: No such file (os error 2)"):
    return {**_result("infra", files=["tests/test_mod.py"]), "merge_sha": "m" * 40,
            "detail": detail}


def test_an_infra_base_merge_is_counted_reported_and_never_sent_back(tmp_path, tried, notices):
    calls, answer = tried
    answer["res"] = _infra()
    wf, fake = _setup(tmp_path, pr_mergeable="CONFLICTING", infra_restarts=2)

    summary = _tick(wf, fake)

    assert (summary["base_infra"], summary["base_conflict"], summary["reworked"],
            summary["escalated"]) == (1, 0, 0, 0)
    run = dispatcher.resolve_run("abc123")
    assert (run["status"], run["rework_count"], run["conflict_reworks"], run["infra_restarts"]) \
        == ("awaiting_review", 0, 0, 3)
    assert run["conflict_sha"] == "deadbee1"           # one infra round per head
    assert dispatcher.latest_verdict(run) in (None, "")
    assert not fake.comments
    cap = dispatcher.infra_restart_cap(run)
    [(kind, summary_line, payload)] = notices
    assert kind == "run_base_merge_infra"
    assert summary_line == (f"🏗️ test-1: origin/dev merges cleanly but its targeted tests could "
                            f"not start (infra 3/{cap}) — NOT sent back for rework, not retried on this head — "
                            f"Caused by: "
                            f"No such file (os error 2)")
    assert payload == {"task_id": "abc123", "branch_name": "test-1",
                       "pr_url": "https://github.com/o/r/pull/80", "base_sha": "b" * 40,
                       "head_sha": "deadbee1", "detail": _infra()["detail"],
                       "infra_restarts": 3, "infra_restart_cap": cap}
    _tick(wf, fake)                                     # the same head: not tried again
    assert len(calls) == 1 and dispatcher.resolve_run("abc123")["infra_restarts"] == 3


def test_a_run_that_left_awaiting_review_during_the_attempt_is_not_charged(tmp_path, notices,
                                                                          monkeypatch):
    def merged_meanwhile(*a):
        with dispatcher._db() as c:
            c.execute("UPDATE runs SET status='done' WHERE task_id='abc123'")
            c.commit()
        return _infra()
    monkeypatch.setattr(basemerge, "attempt", merged_meanwhile)
    wf, fake = _setup(tmp_path, pr_mergeable="CONFLICTING", infra_restarts=2)
    summary = _tick(wf, fake)
    run = dispatcher.resolve_run("abc123")
    assert (summary["base_infra"], summary["escalated"], notices) == (0, 0, [])
    assert (run["status"], run["infra_restarts"], run["conflict_sha"]) == ("done", 2, None)


def test_an_infra_base_merge_at_the_infra_cap_escalates(tmp_path, tried, notices, monkeypatch):
    _, answer = tried
    answer["res"] = _infra()
    monkeypatch.setattr(dispatcher, "max_infra_restarts", lambda: 3)
    wf, fake = _setup(tmp_path, pr_mergeable="CONFLICTING", infra_restarts=2)

    summary = _tick(wf, fake)

    run = dispatcher.resolve_run("abc123")
    assert (summary["base_infra"], summary["escalated"], summary["base_conflict"]) == (1, 1, 0)
    assert (run["status"], run["infra_restarts"], run["rework_count"]) == ("needs_human", 3, 0)
    assert "infra restart cap reached (3/3): origin/dev merges cleanly" in run["last_error"]
    assert "Caused by: No such file (os error 2)" in run["last_error"]


def test_one_infra_round_below_the_cap_does_not_escalate(tmp_path, tried, notices, monkeypatch):
    _, answer = tried
    answer["res"] = _infra()
    monkeypatch.setattr(dispatcher, "max_infra_restarts", lambda: 3)
    wf, fake = _setup(tmp_path, pr_mergeable="CONFLICTING", infra_restarts=1)
    assert _tick(wf, fake)["escalated"] == 0
    assert dispatcher.resolve_run("abc123")["status"] == "awaiting_review"


def test_an_inbox_that_raises_still_counts_the_infra_round(tmp_path, tried, monkeypatch):
    from chela import inbox
    _, answer = tried
    answer["res"] = _infra(detail="")
    monkeypatch.setattr(inbox, "enqueue", lambda *a: (_ for _ in ()).throw(RuntimeError("x")))
    wf, fake = _setup(tmp_path, pr_mergeable="CONFLICTING")
    assert _tick(wf, fake)["base_infra"] == 1
    run = dispatcher.resolve_run("abc123")
    assert (run["infra_restarts"], run["status"]) == (1, "awaiting_review")


def test_a_red_merge_after_a_clean_merge_is_still_sent_back_not_infra(tmp_path, tried, notices):
    _, answer = tried
    answer["res"] = _result("red", files=["tests/test_mod.py"])
    wf, fake = _setup(tmp_path, pr_mergeable="CONFLICTING")
    summary = _tick(wf, fake)
    run = dispatcher.resolve_run("abc123")
    assert (summary["base_conflict"], summary["base_infra"], notices) == (1, 0, [])
    assert (run["status"], run["infra_restarts"] or 0) == ("running", 0)


# --- 🏭 CMX-300: a conflict only in the generated bundle is rebuilt, not sent back -------

APP = "chela/dashboard/static/app"


def _bundle(name: str) -> dict[str, str]:
    return {f"{APP}/manifest.json": f'{{"src/main.tsx": {{"file": "assets/{name}"}}}}\n',
            f"{APP}/assets/{name}": f"// {name}\n"}


def _rebundle(repo, name: str, files: dict[str, str], msg: str) -> str:
    """Commit ``files`` with the bundle renamed to ``name``, the way a port PR's rebuild does."""
    old = _g(repo, "ls-files", f"{APP}/assets").splitlines()
    _g(repo, "rm", "-q", *old)
    return _commit(repo, {**files, **_bundle(name)}, msg)


@pytest.fixture
def bx(tmp_path):
    """origin `dev` with a bundle; PR `feat` changed `src/a.ts` and rebuilt; then `dev` changed
    `src/b.ts` and rebuilt too ⇒ they conflict on the bundle ONLY."""
    origin, repo = tmp_path / "origin.git", tmp_path / "repo"
    _g(tmp_path, "init", "-q", "--bare", "-b", "dev", str(origin))
    _g(tmp_path, "clone", "-q", "-c", "user.name=t", "-c", "user.email=t@example.com",
       str(origin), str(repo))
    _commit(repo, {"src/a.ts": "a\n", "src/b.ts": "b\n", "mod.py": MOD, **_bundle("main-0.js")},
            "base")
    _g(repo, "push", "-q", "origin", "dev")
    _g(repo, "checkout", "-qb", "feat")
    head = _rebundle(repo, "main-PR.js", {"src/a.ts": "a2\n"}, "pr")
    _g(repo, "push", "-q", "origin", "feat")
    _g(repo, "checkout", "-q", "dev")
    base = _rebundle(repo, "main-DEV.js", {"src/b.ts": "b2\n"}, "dev moved")
    _g(repo, "push", "-q", "origin", "dev")
    return repo, head, base


@pytest.fixture
def steps(monkeypatch):
    """The rebuild steps, faked: each call recorded; the build step writes ``main-NEW.js``."""
    calls, answer = [], {"fail": None, "write": _bundle("main-NEW.js")}

    def step(wt, argv, timeout):
        calls.append((Path(wt).name, argv, timeout))
        if answer["fail"] == argv:
            return 1, "vite: boom"
        if argv[-1] == "build":
            for rel, text in answer["write"].items():
                (Path(wt) / rel).parent.mkdir(parents=True, exist_ok=True)
                (Path(wt) / rel).write_text(text)
        return 0, ""
    monkeypatch.setattr(basemerge, "_step", step)
    return calls, answer


def test_a_conflict_only_in_the_bundle_is_rebuilt_checked_and_pushed(bx, steps):
    repo, head, base = bx
    calls, _ = steps

    res = basemerge.attempt(repo, "feat", "dev", "")

    assert res["outcome"] == "rebuilt" and res["files"] == []
    assert [c[1] for c in calls] == [("pnpm", "install", "--frozen-lockfile"),
                                     ("pnpm", "-C", "chela/dashboard/frontend", "build"),
                                     (sys.executable, "chela/dashboard/frontend_build.py")]
    assert {c[0] for c in calls} == {"wt"}
    merge = res["merge_sha"]
    assert _origin_feat(repo) == merge
    assert _g(repo, "rev-parse", f"{merge}^@").split() == [head, base]
    assert _g(repo, "log", "-1", "--format=%s", merge) == "Merge origin/dev; rebuild generated bundle"
    assert _g(repo, "ls-tree", "-r", "--name-only", merge, APP).splitlines() == [
        f"{APP}/assets/main-NEW.js", f"{APP}/manifest.json"]
    assert _g(repo, "show", f"{merge}:src/a.ts") == "a2"            # the PR's source, kept
    assert _g(repo, "show", f"{merge}:src/b.ts") == "b2"            # and the base's
    assert len(_g(repo, "worktree", "list").splitlines()) == 1


def test_a_rebuilt_merge_runs_the_targeted_tests_and_a_red_one_is_not_pushed(bx, steps,
                                                                            monkeypatch):
    repo, head, _ = bx
    ran = []
    monkeypatch.setattr(basemerge, "targeted_tests", lambda wt, touched: ["tests/test_mod.py"])
    monkeypatch.setattr(judge, "run_suite", lambda c, cwd, timeout: ran.append(c) or _BOOM)
    res = basemerge.attempt(repo, "feat", "dev", PYTEST)
    assert (res["outcome"], ran, _origin_feat(repo)) == (
        "red", [f"{PYTEST} tests/test_mod.py"], head)


def test_a_source_conflict_beside_the_bundle_one_is_sent_back_and_never_rebuilt(bx, steps):
    repo, head, _ = bx
    calls, _ = steps
    _g(repo, "checkout", "-q", "feat")
    head = _commit(repo, {"mod.py": MOD.replace("return 2", "return 3")}, "pr 2")
    _g(repo, "push", "-q", "origin", "feat")
    _dev(repo, {"mod.py": MOD.replace("return 2", "return 4")})

    res = basemerge.attempt(repo, "feat", "dev", "")

    assert res["outcome"] == "conflict" and "mod.py" in res["files"]
    assert any(f.startswith(APP) for f in res["files"])
    assert (calls, _origin_feat(repo)) == ([], head)


@pytest.mark.parametrize("failing", [s[0] for s in basemerge.REBUILD_STEPS],
                         ids=["install", "build", "stale-check"])
def test_a_failed_rebuild_step_is_sent_back_with_its_error_and_nothing_pushed(bx, steps, failing):
    repo, head, _ = bx
    calls, answer = steps
    answer["fail"] = failing

    res = basemerge.attempt(repo, "feat", "dev", "")

    assert res["outcome"] == "build_failed" and res["merge_sha"] is None
    assert res["detail"] == f"`{' '.join(failing)}` exited 1:\nvite: boom"
    assert f"{APP}/manifest.json" in res["files"]
    assert calls[-1][1] == failing
    assert _origin_feat(repo) == head
    assert len(_g(repo, "worktree", "list").splitlines()) == 1


@pytest.mark.parametrize("write, why", [
    ({**_bundle("main-NEW.js"), "src/a.ts": "rewritten\n"}, "changed outside the bundle:\nsrc/a.ts"),
    ({**_bundle("main-NEW.js"), "stray.txt": "x\n"}, "changed outside the bundle:\nstray.txt"),
    ({}, f"wrote no {APP}/manifest.json"),
    ({f"{APP}/assets/main-NEW.js": "x\n"}, f"wrote no {APP}/manifest.json"),
])
def test_a_rebuild_that_strays_or_writes_nothing_is_a_build_failure(bx, steps, write, why):
    repo, head, _ = bx
    _, answer = steps
    answer["write"] = write
    res = basemerge.attempt(repo, "feat", "dev", "")
    assert res["outcome"] == "build_failed" and res["detail"].endswith(why)
    assert _origin_feat(repo) == head


def test_the_rebuild_steps_are_install_build_and_the_stale_asset_check():
    assert basemerge.BUNDLE_DIR == "chela/dashboard/static/app"
    assert generated.is_rebuilt(f"{basemerge.BUNDLE_DIR}/manifest.json")   # one scope, both
    assert basemerge.REBUILD_STEPS == (
        (("pnpm", "install", "--frozen-lockfile"), 600),
        (("pnpm", "-C", "chela/dashboard/frontend", "build"), 300),
        ((sys.executable, "chela/dashboard/frontend_build.py"), 600))


def test_a_step_runs_its_argv_in_the_worktree_without_a_leaked_node_ipc_fd(monkeypatch,
                                                                          tmp_path):
    seen = []
    monkeypatch.setenv("NODE_CHANNEL_FD", "3")
    monkeypatch.setenv("KEEP_ME", "1")
    monkeypatch.setattr(basemerge.subprocess, "run", lambda argv, **k: seen.append((argv, k))
                        or subprocess.CompletedProcess(argv, 2, "out\n", "err\n"))
    assert basemerge._step(tmp_path, ("pnpm", "build"), 42) == (2, "out\nerr\n")
    (argv, kw), = seen
    assert argv == ["pnpm", "build"]
    assert (kw["cwd"], kw["timeout"], kw["capture_output"], kw["text"]) == (
        str(tmp_path), 42, True, True)
    assert "NODE_CHANNEL_FD" not in kw["env"] and kw["env"]["KEEP_ME"] == "1"


def test_a_step_that_times_out_is_a_failed_step(monkeypatch, tmp_path):
    exc = subprocess.TimeoutExpired("pnpm", 1)

    def boom(*a, **k):
        raise exc
    monkeypatch.setattr(basemerge.subprocess, "run", boom)
    assert basemerge._step(tmp_path, ("pnpm",), 1) == (-1, str(exc))


def test_a_step_that_cannot_start_is_unmeasured_not_the_agents_build(monkeypatch, tmp_path):
    def boom(*a, **k):
        raise FileNotFoundError("no pnpm")
    monkeypatch.setattr(basemerge.subprocess, "run", boom)
    with pytest.raises(RuntimeError, match="`pnpm install` could not start: no pnpm"):
        basemerge._step(tmp_path, ("pnpm", "install"), 1)


def test_a_rebuild_step_that_cannot_start_raises_and_pushes_nothing(bx, monkeypatch):
    repo, head, _ = bx
    real = subprocess.run
    monkeypatch.setattr(basemerge.subprocess, "run", lambda argv, **k: (
        (_ for _ in ()).throw(FileNotFoundError("no pnpm")) if argv[0] == "pnpm"
        else real(argv, **k)))
    with pytest.raises(RuntimeError, match="could not start"):
        basemerge.attempt(repo, "feat", "dev", "")
    assert _origin_feat(repo) == head
    assert len(_g(repo, "worktree", "list").splitlines()) == 1


def test_the_build_failed_body_names_the_files_and_fences_the_build_error():
    res = {"base_sha": "abcdef1234567890", "files": [f"{APP}/manifest.json"],
           "detail": "x ``` y", "outcome": "build_failed"}
    body = basemerge.rework_body(res, "dev")
    assert ("Conflict with base after `abcdef123456` merged, only in the generated bundle, but "
            "rebuilding it on the merge failed: merge origin/dev, rebuild and fix the build"
            ) in body
    assert f"- `{APP}/manifest.json`\n" in body and "\n````\nx ``` y\n````\n" in body
    assert "not charged" in body and "targeted tests" not in body


def test_a_rebuilt_merge_is_recorded_on_the_pr_as_rebuilt_and_not_sent_back(tmp_path, tried):
    _, answer = tried
    answer["res"] = {**_result("merged", files=[]), "outcome": "rebuilt"}
    wf, fake = _setup(tmp_path, pr_mergeable="CONFLICTING")

    summary = _tick(wf, fake)

    assert (summary["base_merged"], summary["base_conflict"]) == (1, 0)
    run = dispatcher.resolve_run("abc123")
    assert (run["status"], run["rework_count"], run["conflict_sha"]) == (
        "awaiting_review", 0, "deadbee1")
    assert any("Base merged automatically (it conflicted only in the generated bundle, so the "
               "bundle was rebuilt from the merged source and passed the stale-asset check): "
               "origin/dev `bbbbbbbbbbbb` → merge commit `mmmmmmmmmmmm`" in c
               for c in fake.comments)


def test_a_failed_rebuild_reworks_free_with_the_build_error(tmp_path, tried):
    _, answer = tried
    answer["res"] = {**_result("build_failed"), "detail": "vite: boom"}
    wf, fake = _setup(tmp_path, pr_mergeable="CONFLICTING")
    assert _tick(wf, fake)["base_conflict"] == 1
    run = dispatcher.resolve_run("abc123")
    verdict = dispatcher.latest_verdict(run)
    assert run["conflict_reworks"] == 1 and "rebuilding it on the merge failed" in verdict
    assert "vite: boom" in verdict and not any("Base merged" in c for c in fake.comments)
