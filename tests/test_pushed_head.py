"""📌 CMX-394: the PR must be on the worktree's HEAD when a run is handed in.

CMX-386 and CMX-388 both made a `request-push` BEFORE their final commits and nothing pushed
after, so the PR (and the judge, CI and the conflict check reading it) sat on an older commit
while `task-finished` reported success. These run real git against a bare `origin`.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from chela import basemerge, dispatcher, main

from tests.test_dispatcher_rework import _row, _wf

REAL_PUSHED_HEAD = True
BRANCH = "liavacc/cmx-1-a-thing"


@pytest.fixture(autouse=True)
def _own_runs_db(tmp_path, monkeypatch):
    monkeypatch.setattr(dispatcher, "DB_PATH", tmp_path / "scheduler.db")


def _git(cwd: Path, *argv: str) -> str:
    out = subprocess.run(["git", *argv], cwd=str(cwd), capture_output=True, text=True,
                         check=True, env={"GIT_CONFIG_GLOBAL": "/dev/null",
                                          "GIT_CONFIG_NOSYSTEM": "1", "PATH": "/usr/bin:/bin",
                                          "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@x",
                                          "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@x"})
    return out.stdout.strip()


def _commit(wt: Path, name: str) -> str:
    (wt / name).write_text(name)
    _git(wt, "add", name)
    _git(wt, "commit", "-qm", name)
    return _git(wt, "rev-parse", "HEAD")


def _origin_has(origin: Path) -> str:
    return _git(origin, "rev-parse", f"refs/heads/{BRANCH}")


@pytest.fixture
def repo(tmp_path):
    origin = tmp_path / "origin.git"
    _git(tmp_path, "init", "-q", "--bare", str(origin))
    wt = tmp_path / "wt"
    _git(tmp_path, "init", "-q", "-b", BRANCH, str(wt))
    _git(wt, "remote", "add", "origin", str(origin))
    return origin, wt


def _run(wt: Path, pr_url: str | None = "https://github.com/o/r/pull/80"):
    with dispatcher._db() as conn:
        return _row(conn, task_id="t1", status="running", worktree_path=str(wt),
                    branch_name=BRANCH, pr_url=pr_url)


def _apply(wf, task_id="t1") -> dict:
    """What the daemon's tick does with a pending push marker (pr_url set ⇒ no `gh`)."""
    with dispatcher._db() as conn:
        row = conn.execute("SELECT * FROM runs WHERE task_id=?", (task_id,)).fetchone()
        out = dispatcher._apply_push_request(conn, wf, row)
    if out.get("ok"):
        dispatcher._push_request_path(row["worktree_path"]).unlink()
    return out


# --- acceptance ---------------------------------------------------------------------------


def test_request_push_at_a_then_commit_b_then_finish_lands_b_on_origin(tmp_path, repo):
    """The CMX-388 shape: request-push at A (honoured), commit B, task-finished ⇒ origin ends
    at B. Corrupt the gate (skip the HEAD-vs-origin check) ⇒ origin stays at A ⇒ RED."""
    origin, wt = repo
    wf = _wf(tmp_path)
    a = _commit(wt, "a")
    _run(wt)
    assert dispatcher.request_push("t1")["ok"]
    assert _apply(wf) == {"ok": True, "pr_url": "https://github.com/o/r/pull/80"}
    assert _origin_has(origin) == a
    b = _commit(wt, "b")

    out = dispatcher.ensure_pushed_head("t1")

    assert out == {"ok": True, "local": b, "origin": a, "requested": True}
    assert dispatcher._push_request_path(wt).exists()
    assert _apply(wf)["ok"]
    assert _origin_has(origin) == b


def test_a_second_request_push_after_a_new_commit_pushes_it(tmp_path, repo):
    origin, wt = repo
    wf = _wf(tmp_path)
    _commit(wt, "a")
    _run(wt)
    dispatcher.request_push("t1")
    assert _apply(wf)["ok"]
    b = _commit(wt, "b")
    dispatcher.request_push("t1")
    assert _apply(wf)["ok"]
    assert _origin_has(origin) == b


def test_with_no_pr_yet_an_unpushed_head_is_refused_with_the_exact_message(tmp_path, repo):
    """No PR ⇒ only `request-push` carries a title, so finishing refuses (and files nothing)."""
    origin, wt = repo
    a = _commit(wt, "a")
    _git(wt, "push", "-q", "origin", BRANCH)
    b = _commit(wt, "b")
    _run(wt, pr_url=None)

    out = dispatcher.ensure_pushed_head("t1")

    assert out == {"ok": False,
                   "error": f"HEAD {b[:12]} not pushed (origin has {a[:12]}): run request-push"}
    assert not dispatcher._push_request_path(wt).exists()
    assert _origin_has(origin) == a


def test_a_branch_origin_never_had_reads_as_nothing(tmp_path, repo):
    _origin, wt = repo
    b = _commit(wt, "a")
    _run(wt, pr_url=None)
    assert dispatcher.ensure_pushed_head("t1")["error"] == \
        f"HEAD {b[:12]} not pushed (origin has nothing): run request-push"


def test_a_head_origin_already_has_files_nothing(tmp_path, repo):
    _origin, wt = repo
    a = _commit(wt, "a")
    _git(wt, "push", "-q", "origin", BRANCH)
    _run(wt)
    assert dispatcher.ensure_pushed_head("t1") == {"ok": True, "local": a, "origin": a,
                                                   "requested": False}
    assert not dispatcher._push_request_path(wt).exists()


def test_a_pending_push_request_is_kept_as_it_is(tmp_path, repo):
    """A first dispatch's pending marker carries the PR title — never overwritten, and the
    remote is not even read (the daemon pushes and checks it before the hand-in applies)."""
    _origin, wt = repo
    a = _commit(wt, "a")
    _run(wt, pr_url=None)
    dispatcher.request_push("t1", pr_title="CMX-1: x", pr_body="body")
    before = dispatcher._push_request_path(wt).read_text()
    with patch.object(dispatcher, "_origin_branch_head",
                      side_effect=AssertionError("origin must not be read")):
        out = dispatcher.ensure_pushed_head("t1")
    assert out == {"ok": True, "local": a, "origin": None, "requested": True}
    assert dispatcher._push_request_path(wt).read_text() == before


def test_an_unreadable_origin_fails_closed(tmp_path, repo):
    _origin, wt = repo
    a = _commit(wt, "a")
    _git(wt, "remote", "set-url", "origin", str(tmp_path / "no-such-remote.git"))
    _run(wt)
    out = dispatcher.ensure_pushed_head("t1")
    assert out["ok"] is False
    assert out["error"].startswith(f"could not tell whether HEAD {a[:12]} is pushed: "
                                   f"could not read origin/{BRANCH}: ")
    assert not dispatcher._push_request_path(wt).exists()


def test_an_unreadable_head_fails_closed(tmp_path, repo):
    _origin, wt = repo                          # no commit: HEAD does not resolve
    _run(wt)
    assert dispatcher.ensure_pushed_head("t1") == {
        "ok": False, "error": f"could not read HEAD in {wt}"}


def test_a_run_without_a_branch_is_refused(tmp_path):
    with dispatcher._db() as conn:
        _row(conn, task_id="t1", status="running", worktree_path=str(tmp_path),
             branch_name="")
    assert dispatcher.ensure_pushed_head("t1")["ok"] is False


def test_a_push_request_that_cannot_be_filed_is_refused(tmp_path, repo):
    origin, wt = repo
    a = _commit(wt, "a")
    _git(wt, "push", "-q", "origin", BRANCH)
    b = _commit(wt, "b")
    _run(wt)
    with patch.object(dispatcher, "request_push",
                      return_value={"ok": False, "error": "status 'done'"}):
        out = dispatcher.ensure_pushed_head("t1")
    assert out == {"ok": False, "error": f"HEAD {b[:12]} not pushed (origin has {a[:12]}) — "
                                         "and its push could not be requested: status 'done'"}
    assert _origin_has(origin) == a


# --- the daemon checks origin after pushing ---------------------------------------------


def test_a_push_that_leaves_origin_behind_head_is_an_error_not_a_success(tmp_path, repo):
    """`git push` of the branch exits 0 while the worktree's HEAD is a detached commit the
    branch lacks: origin != HEAD must be said plainly, and the marker stays (retried)."""
    origin, wt = repo
    wf = _wf(tmp_path)
    a = _commit(wt, "a")
    _git(wt, "checkout", "-q", "--detach")
    b = _commit(wt, "b")
    _run(wt)
    dispatcher.request_push("t1")
    out = _apply(wf)
    assert out == {"ok": False, "error": f"git push exited 0 but origin/{BRANCH} is at "
                                         f"{a[:12]}, not the worktree's HEAD {b[:12]}"}
    assert _origin_has(origin) == a
    assert dispatcher._push_request_path(wt).exists()


def test_a_push_whose_result_cannot_be_read_back_is_an_error(tmp_path, repo):
    _origin, wt = repo
    wf = _wf(tmp_path)
    _commit(wt, "a")
    _run(wt)
    dispatcher.request_push("t1")
    with patch.object(dispatcher, "_origin_branch_head",
                      side_effect=RuntimeError("ls-remote said no")):
        out = _apply(wf)
    assert out == {"ok": False,
                   "error": "pushed, but could not confirm origin has HEAD: ls-remote said no"}


def test_the_read_back_asks_origin_live_with_the_network_timeout(repo):
    _origin, wt = repo
    calls = []

    def spy(cmd, **kw):
        calls.append((cmd, kw))
        return subprocess.CompletedProcess(cmd, 0, f"{'c' * 40}\trefs/heads/{BRANCH}\n"
                                                   f"{'d' * 40}\trefs/heads/{BRANCH}-x\n", "")
    with patch.object(dispatcher.subprocess, "run", side_effect=spy):
        assert dispatcher._origin_branch_head(wt, BRANCH) == "c" * 40
    [(cmd, kw)] = calls
    assert cmd == ["git", "-C", str(wt), "ls-remote", "origin", f"refs/heads/{BRANCH}"]
    assert kw["timeout"] == 60


def test_the_read_back_ignores_a_line_for_another_ref(repo):
    _origin, wt = repo

    def spy(cmd, **kw):
        return subprocess.CompletedProcess(cmd, 0, f"{'d' * 40}\trefs/heads/{BRANCH}-x\n", "")
    with patch.object(dispatcher.subprocess, "run", side_effect=spy):
        assert dispatcher._origin_branch_head(wt, BRANCH) == ""


# --- the CLI ------------------------------------------------------------------------------


def _cli(argv, capsys):
    with patch.object(sys, "argv", ["chela", *argv]):
        try:
            main.main()
        except SystemExit as e:
            return e.code, capsys.readouterr().out
    return 0, capsys.readouterr().out


def test_task_finished_refuses_and_files_no_completion_when_head_is_not_pushed(
        tmp_path, repo, capsys, monkeypatch):
    origin, wt = repo
    a = _commit(wt, "a")
    _git(wt, "push", "-q", "origin", BRANCH)
    b = _commit(wt, "b")
    _run(wt, pr_url=None)
    monkeypatch.setattr(dispatcher, "hand_in_tier", lambda *a: "high")
    monkeypatch.setattr(dispatcher, "hand_in_checks", lambda *a, **k: ())
    finished = []
    monkeypatch.setattr(dispatcher, "request_task_finished",
                        lambda *a, **k: finished.append(a) or {"ok": True, "task_id": "t1"})

    code, out = _cli(["task-finished", "t1", "--no-new-guards"], capsys)

    assert code == 1
    assert f"task-finished: 📌 HEAD {b[:12]} not pushed (origin has {a[:12]}): " \
           "run request-push" in out
    assert finished == []


def test_task_finished_says_it_requested_the_push_then_hands_in(tmp_path, repo, capsys,
                                                                 monkeypatch):
    _origin, wt = repo
    a = _commit(wt, "a")
    _git(wt, "push", "-q", "origin", BRANCH)
    b = _commit(wt, "b")
    _run(wt)
    monkeypatch.setattr(dispatcher, "hand_in_tier", lambda *a: "high")
    monkeypatch.setattr(dispatcher, "hand_in_checks", lambda *a, **k: ())

    code, out = _cli(["task-finished", "t1", "--no-new-guards"], capsys)

    assert code == 0, out
    assert f"📌 HEAD {b[:12]} not pushed (origin has {a[:12]}) — push requested; chela " \
           "pushes it before handing the run in." in out
    assert json.loads(dispatcher._push_request_path(wt).read_text())["pr_title"] is None
    assert dispatcher._task_finished_request_path(wt).exists()


# --- the conflict check names a local HEAD the pushed head lacks ------------------------


def test_local_vs_pushed_note(repo):
    _origin, wt = repo
    assert dispatcher._local_vs_pushed_note(str(wt), "a" * 40) == ""     # HEAD unreadable
    a = _commit(wt, "a")
    assert dispatcher._local_vs_pushed_note(str(wt), a) == ""
    assert dispatcher._local_vs_pushed_note(None, a) == ""
    assert dispatcher._local_vs_pushed_note(str(wt), "") == ""
    assert dispatcher._local_vs_pushed_note(str(wt), "e" * 40) == (
        f"⚠️ The worktree's local HEAD `{a[:12]}` is NOT the pushed PR head `{'e' * 12}` that "
        "this check graded — local commits may already resolve it. Push them with "
        "`chela request-push`, then `chela task-finished`.")


@pytest.mark.parametrize("drifted", [True, False])
def test_a_conflict_rework_on_a_stale_pushed_head_says_so(tmp_path, repo, monkeypatch,
                                                          drifted):
    from chela import inbox
    _origin, wt = repo
    local = _commit(wt, "a")
    pushed = "e" * 40 if drifted else local
    wf = _wf(tmp_path)
    with dispatcher._db() as conn:
        row = _row(conn, task_id="t1", status="awaiting_review", worktree_path=str(wt),
                   branch_name=BRANCH)
    monkeypatch.setattr(basemerge, "attempt", lambda *a: {
        "outcome": basemerge.CONFLICT, "files": ["x.py"], "base_sha": "b" * 40, "detail": ""})
    bodies, notices = [], []
    monkeypatch.setattr(dispatcher, "request_changes",
                        lambda tid, body, **k: bodies.append(body) or {"ok": True})
    monkeypatch.setattr(inbox, "enqueue", lambda *a: notices.append(a) or True)

    with dispatcher._db() as conn:
        assert dispatcher._resolve_base_conflict(conn, wf, row, pushed) == "rework"

    note = dispatcher._local_vs_pushed_note(str(wt), pushed)
    [body] = bodies
    if not drifted:
        assert note == "" and "NOT the pushed PR head" not in body and notices == []
        return
    assert body.endswith(f"\n{note}\n")
    [(kind, summary, payload)] = notices
    assert kind == "run_head_unpushed"
    assert summary == (f"📌 {BRANCH}: sent back for a base conflict on the PUSHED head — "
                       f"{note}")
    assert payload == {"task_id": "t1", "branch_name": BRANCH,
                       "pr_url": "https://github.com/o/r/pull/80", "pushed_sha": pushed}


def test_origin_with_commits_head_lacks_is_refused_not_pushed(tmp_path, repo):
    """CMX-262's automatic base merge pushes to the PR branch: a plain push from the stale
    local HEAD would only be rejected later. Refuse now, with the pull to run."""
    origin, wt = repo
    a = _commit(wt, "a")
    _git(wt, "push", "-q", "origin", BRANCH)
    other = tmp_path / "other"
    _git(tmp_path, "clone", "-q", "-b", BRANCH, str(origin), str(other))
    o = _commit(other, "o")
    _git(other, "push", "-q", "origin", BRANCH)
    _git(wt, "fetch", "-q", "origin")
    b = _commit(wt, "b")
    assert a != o
    _run(wt)

    out = dispatcher.ensure_pushed_head("t1")

    assert out == {"ok": False, "error": f"HEAD {b[:12]} not pushed (origin has {o[:12]}), and "
                                         f"origin has commits HEAD lacks: `git pull --no-rebase "
                                         f"origin {BRANCH}`, then task-finished"}
    assert not dispatcher._push_request_path(wt).exists()


def test_a_pending_request_is_named_by_the_cli(tmp_path, repo, capsys, monkeypatch):
    _origin, wt = repo
    a = _commit(wt, "a")
    _run(wt, pr_url=None)
    dispatcher.request_push("t1", pr_title="CMX-1: x")
    monkeypatch.setattr(dispatcher, "hand_in_tier", lambda *a: "high")
    monkeypatch.setattr(dispatcher, "hand_in_checks", lambda *a, **k: ())

    code, out = _cli(["task-finished", "t1", "--no-new-guards"], capsys)

    assert code == 0, out
    assert f"📌 a push request is pending — chela pushes HEAD {a[:12]} and checks origin has " \
           "it before handing the run in." in out


def test_no_unpushed_notice_when_the_rework_was_not_written(tmp_path, repo, monkeypatch):
    from chela import inbox
    _origin, wt = repo
    _commit(wt, "a")
    wf = _wf(tmp_path)
    with dispatcher._db() as conn:
        row = _row(conn, task_id="t1", status="awaiting_review", worktree_path=str(wt),
                   branch_name=BRANCH)
    monkeypatch.setattr(basemerge, "attempt", lambda *a: {
        "outcome": basemerge.CONFLICT, "files": ["x.py"], "base_sha": "b" * 40, "detail": ""})
    monkeypatch.setattr(dispatcher, "request_changes", lambda *a, **k: {"ok": False, "error": "x"})
    notices = []
    monkeypatch.setattr(inbox, "enqueue", lambda *a: notices.append(a) or True)
    with dispatcher._db() as conn:
        assert dispatcher._resolve_base_conflict(conn, wf, row, "e" * 40) == "none"
    assert notices == []
