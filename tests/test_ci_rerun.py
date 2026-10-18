"""🔁 CMX-255: a red CI re-runs its failed jobs ONCE before it may spend a rework round.

10-18: a PR went red on one job from a flaky test it never touched; the dispatcher sent it
straight back for rework, the agent had nothing to fix, the run went needs_human, and a
human re-ran CI (green) by hand. One rework round and an hour, burned on a flake.

These pin the fix, with `gh` stubbed at the argv boundary (tests/test_dispatcher_ci.py):

* red → re-run → green ⇒ no rework, the flaky test recorded on the run, one notice;
* red → re-run → red ⇒ rework as before (a real failure);
* at most ONE re-run per head sha — a new push resets it;
* a re-run GitHub will not start falls through to the rework, never a silent park.
"""
from __future__ import annotations

import json
import subprocess

import pytest

from chela import dispatcher, inbox
from tests.test_dispatcher_ci import _check_run, _FakeGh, _row, _tick, _wf

_LOG = "E   assert 1 == 2\nFAILED tests/test_wall.py::test_grid - assert 1 == 2\n"
_NOTE = "CI re-run once: CI / test (3.12) failed on tests/test_wall.py::test_grid"


@pytest.fixture(autouse=True)
def _own_runs_db(tmp_path, monkeypatch):
    monkeypatch.setattr(dispatcher, "DB_PATH", tmp_path / "scheduler.db")


@pytest.fixture
def notices(monkeypatch):
    said: list[tuple[str, str, str]] = []
    monkeypatch.setattr(inbox, "ci_flake",
                        lambda run, *, sha, note: said.append((run["task_id"], sha, note)))
    return said


def _red(**kw):
    return _FakeGh(rollup=[_check_run("test (3.12)", conclusion="FAILURE")], log=_LOG,
                   rerun_rc=0, **kw)


def _setup(tmp_path):
    wf = _wf(tmp_path)
    with dispatcher._db() as conn:
        _row(conn, workflow_path=str(wf.path))
    return wf


def _run():
    return dispatcher.resolve_run("abc123")


def test_red_reruns_the_failed_jobs_once_and_spends_nothing(tmp_path):
    wf = _setup(tmp_path)
    fake = _red()

    summary = _tick(wf, fake)

    assert summary["ci_rerun"] == 1
    assert summary["ci_failed"] == 0
    assert fake.reruns == [["gh", "run", "rerun", "42", "--failed"]]
    k = fake.kwargs[fake.calls.index(fake.reruns[0])]
    assert k["cwd"] == str(wf.path.parent)
    assert k["timeout"] == 30
    assert k["capture_output"] is True
    run = _run()
    assert run["status"] == "awaiting_review"
    assert (run["rework_count"] or 0) == 0
    assert dispatcher.reviews_of(run) == []
    assert run["ci_rerun_sha"] == "deadbee1"
    assert run["ci_rerun_state"] == "pending"
    assert run["ci_rerun_note"] == _NOTE
    assert run["ci_failed_sha"] is None        # the re-run's own red must still be able to fire
    assert not fake.comments


def test_red_then_rerun_green_is_a_flake_no_rework_and_one_notice(tmp_path, notices):
    wf = _setup(tmp_path)
    fake = _red()
    _tick(wf, fake)
    fake.rollup = [_check_run("test (3.12)", status="IN_PROGRESS", conclusion="")]
    assert _tick(wf, fake)["ci_flake"] == 0
    fake.rollup = [_check_run("test (3.12)")]

    assert _tick(wf, fake)["ci_flake"] == 1
    assert _tick(wf, fake)["ci_flake"] == 0     # green again: said once

    run = _run()
    assert run["status"] == "awaiting_review"
    assert (run["rework_count"] or 0) == 0
    assert run["ci_rerun_state"] == "green"
    [flake] = json.loads(run["ci_flakes"])
    assert flake["sha"] == "deadbee1"
    assert flake["test"] == "tests/test_wall.py::test_grid"
    assert flake["note"] == _NOTE
    assert notices == [("abc123", "deadbee1", _NOTE)]
    assert len(fake.reruns) == 1


def test_red_then_rerun_red_is_a_real_failure_and_reworks(tmp_path, notices):
    wf = _setup(tmp_path)
    fake = _red()
    _tick(wf, fake)

    summary = _tick(wf, fake)                  # the re-run settled red on the same sha

    assert summary["ci_failed"] == 1
    assert summary["ci_rerun"] == 0
    assert len(fake.reruns) == 1               # ⛔ no second re-run on the same sha
    run = _run()
    assert run["status"] == "running"
    assert run["rework_count"] == 1
    assert run["ci_rerun_state"] == "red"
    assert run["ci_failed_sha"] == "deadbee1"
    verdict = dispatcher.latest_verdict(run)
    assert _NOTE in verdict and "failed AGAIN on the re-run" in verdict
    assert notices == []


def test_a_new_push_resets_the_rerun(tmp_path):
    wf = _setup(tmp_path)
    fake = _red(sha="firstsha")
    _tick(wf, fake)
    _tick(wf, fake)                            # real failure → rework
    with dispatcher._db() as conn:
        conn.execute("UPDATE runs SET status='awaiting_review' WHERE task_id='abc123'")
        conn.commit()
    fake.sha = "secondsh"

    summary = _tick(wf, fake)

    assert summary["ci_rerun"] == 1 and summary["ci_failed"] == 0
    assert len(fake.reruns) == 2
    assert _run()["ci_rerun_sha"] == "secondsh"
    assert _run()["ci_rerun_state"] == "pending"


def test_green_on_a_DIFFERENT_sha_is_not_a_flake(tmp_path, notices):
    wf = _setup(tmp_path)
    fake = _red()
    _tick(wf, fake)
    fake.sha = "newpush1"
    fake.rollup = [_check_run("test (3.12)")]

    assert _tick(wf, fake)["ci_flake"] == 0
    assert _run()["ci_rerun_state"] == "pending"
    assert _run()["ci_flakes"] is None
    assert notices == []


def test_a_plain_green_with_no_rerun_records_nothing(tmp_path, notices):
    wf = _setup(tmp_path)
    fake = _red()
    fake.rollup = [_check_run("test (3.12)")]
    assert _tick(wf, fake)["ci_flake"] == 0
    assert fake.reruns == [] and notices == []


def test_a_refused_rerun_falls_through_to_the_rework(tmp_path):
    wf = _setup(tmp_path)
    fake = _red()
    fake.rerun_rc = 1

    summary = _tick(wf, fake)

    assert summary["ci_failed"] == 1 and summary["ci_rerun"] == 0
    assert len(fake.reruns) == 1
    run = _run()
    assert run["rework_count"] == 1
    assert run["ci_rerun_sha"] is None
    assert "re-run" not in dispatcher.latest_verdict(run)


@pytest.mark.parametrize("exc", [FileNotFoundError("no gh"),
                                 subprocess.TimeoutExpired(["gh"], 30)])
def test_a_rerun_that_cannot_run_falls_through_to_the_rework(tmp_path, exc):
    wf = _setup(tmp_path)
    fake = _red()
    real = fake.run

    def run(cmd, *a, **k):
        if isinstance(cmd, list) and cmd[:3] == ["gh", "run", "rerun"]:
            raise exc
        return real(cmd, *a, **k)
    fake.run = run

    assert _tick(wf, fake)["ci_failed"] == 1
    assert _run()["rework_count"] == 1
    assert _run()["ci_rerun_sha"] is None


def test_no_run_id_means_no_rerun_and_the_rework(tmp_path):
    wf = _setup(tmp_path)
    fake = _FakeGh(rollup=[{"context": "ci/legacy", "state": "FAILURE"}], log=_LOG, rerun_rc=0)

    assert _tick(wf, fake)["ci_failed"] == 1
    assert fake.reruns == []


_TWO_RUNS = [_check_run("a", conclusion="FAILURE", run_id="1"),
             _check_run("b", conclusion="FAILURE", run_id="2")]


def test_every_failed_run_is_rerun(tmp_path):
    wf = _setup(tmp_path)
    fake = _FakeGh(rollup=_TWO_RUNS, log=_LOG, rerun_rc=0)
    assert _tick(wf, fake)["ci_rerun"] == 1
    assert fake.reruns == [["gh", "run", "rerun", "1", "--failed"],
                           ["gh", "run", "rerun", "2", "--failed"]]


def test_one_refused_rerun_of_several_means_rework(tmp_path):
    wf = _setup(tmp_path)
    fake = _FakeGh(rollup=_TWO_RUNS, log=_LOG)
    real = fake.run

    def run(cmd, *a, **k):
        r = real(cmd, *a, **k)
        if isinstance(cmd, list) and cmd[:3] == ["gh", "run", "rerun"]:
            r.returncode = 0 if cmd[3] == "1" else 1
        return r
    fake.run = run
    assert _tick(wf, fake)["ci_failed"] == 1
    assert _run()["ci_rerun_sha"] is None


def test_an_infra_only_red_is_not_rerun(tmp_path):
    wf = _setup(tmp_path)
    fake = _FakeGh(rollup=[_check_run("test", conclusion="STARTUP_FAILURE")], rerun_rc=0)
    assert _tick(wf, fake)["ci_infra_failed"] == 1
    assert fake.reruns == []


def test_a_corrupt_flake_record_is_restarted_not_fatal(tmp_path, notices):
    wf = _setup(tmp_path)
    fake = _red()
    _tick(wf, fake)
    with dispatcher._db() as conn:
        conn.execute("UPDATE runs SET ci_flakes='not json' WHERE task_id='abc123'")
        conn.commit()
    fake.rollup = [_check_run("test (3.12)")]
    assert _tick(wf, fake)["ci_flake"] == 1
    assert [f["sha"] for f in json.loads(_run()["ci_flakes"])] == ["deadbee1"]


@pytest.mark.parametrize("tail, test_id", [
    ("FAILED tests/x.py::test_y - boom\n", "tests/x.py::test_y"),
    ("ERROR tests/y.py\n", "tests/y.py"),
    ("xFAILED tests/z.py\n", "xFAILED tests/z.py"),       # anchored: not a summary line
    ("FAILEDtests/z.py\n", "FAILEDtests/z.py"),           # the space is required
    ("", "?"),
])
def test_ci_failed_test(tail, test_id):
    assert dispatcher._ci_failed_test(tail) == test_id


def test_the_flake_notice_says_flake_and_not_sent_back(tmp_path, monkeypatch):
    monkeypatch.setenv("CHELA_INBOX_FILE", str(tmp_path / "inbox.json"))
    monkeypatch.setenv("CHELA_EVENTS_FILE", str(tmp_path / "events.jsonl"))
    monkeypatch.setattr(inbox, "INBOX_ENABLED", True)
    run = {"task_id": "abc123", "status": "awaiting_review", "title": "t",
           "pr_url": "https://github.com/o/r/pull/80"}
    assert inbox.ci_flake(run, sha="deadbee1", note=_NOTE) is True
    [event] = [e for e in inbox.load()["queue"] if e["kind"] == "run_ci_flake"]
    assert "CI flake, re-run green on deadbee" in event["summary"]
    assert "NOT sent back" in event["summary"]
    assert event["payload"]["note"] == _NOTE
    assert inbox.ci_flake(run, sha="", note=_NOTE) is False
    monkeypatch.setattr(inbox, "INBOX_ENABLED", False)
    assert inbox.ci_flake(run, sha="deadbee1", note=_NOTE) is False
