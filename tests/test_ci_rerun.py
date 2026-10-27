"""🔁 CMX-255: a red CI re-runs its failed jobs ONCE before it may spend a rework round.

A flaky test the PR never touched sent it back for rework; the agent had nothing to fix and
a human re-ran CI (green) by hand. Pinned here, with `gh` stubbed at the argv boundary:
red → re-run → green is a recorded flake and no rework; red → re-run → red is a rework;
one re-run per head sha; a re-run GitHub will not start falls through to the rework.
"""
from __future__ import annotations

import json
import subprocess

import pytest

from chela import dispatcher, inbox
from tests.test_dispatcher_ci import _check_run, _FakeGh, _row, _tick, _wf

_LOG = "E   assert 1 == 2\nFAILED tests/test_wall.py::test_grid - assert 1 == 2\n"
_NOTE = "CI re-run once: CI / test (3.12) failed on tests/test_wall.py::test_grid"
_GREEN = [_check_run("test (3.12)")]


@pytest.fixture(autouse=True)
def _own_runs_db(tmp_path, monkeypatch):
    monkeypatch.setattr(dispatcher, "DB_PATH", tmp_path / "scheduler.db")


@pytest.fixture
def notices(monkeypatch):
    said: list[tuple] = []
    monkeypatch.setattr(inbox, "ci_flake", lambda run, *, sha, note: said.append((sha, note)))
    return said


def _setup(tmp_path, rollup=None, **kw):
    wf = _wf(tmp_path)
    with dispatcher._db() as conn:
        _row(conn, workflow_path=str(wf.path))
    kw.setdefault("rerun_rc", 0)
    return wf, _FakeGh(rollup=rollup or [_check_run("test (3.12)", conclusion="FAILURE")],
                       log=_LOG, **kw)


def _run():
    return dispatcher.resolve_run("abc123")


def test_red_then_rerun_green_is_a_flake_no_rework_and_one_notice(tmp_path, notices):
    wf, fake = _setup(tmp_path)

    summary = _tick(wf, fake)

    assert (summary["ci_rerun"], summary["ci_failed"]) == (1, 0)
    assert fake.reruns == [["gh", "run", "rerun", "42", "--failed"]]
    k = fake.kwargs[fake.calls.index(fake.reruns[0])]
    assert (k["cwd"], k["timeout"], k["capture_output"]) == (str(wf.path.parent), 30, True)
    run = _run()
    assert run["status"] == "awaiting_review" and (run["rework_count"] or 0) == 0
    assert (run["ci_rerun_sha"], run["ci_rerun_state"]) == ("deadbee1", "pending")
    assert run["ci_rerun_note"] == _NOTE
    assert run["ci_failed_sha"] is None        # the re-run's own red must still be able to fire
    assert not fake.comments

    fake.rollup = _RUNNING
    assert _tick(wf, fake)["ci_flake"] == 0
    fake.rollup = _GREEN
    assert _tick(wf, fake)["ci_flake"] == 1
    assert _tick(wf, fake)["ci_flake"] == 0     # green again: said once

    run = _run()
    assert run["status"] == "awaiting_review" and (run["rework_count"] or 0) == 0
    assert run["ci_rerun_state"] == "green"
    [flake] = json.loads(run["ci_flakes"])
    assert (flake["sha"], flake["test"], flake["note"]) == (
        "deadbee1", "tests/test_wall.py::test_grid", _NOTE)
    assert notices == [("deadbee1", _NOTE)]
    assert len(fake.reruns) == 1


_RUNNING = [_check_run("test (3.12)", status="IN_PROGRESS", conclusion="")]


def test_red_then_rerun_red_is_a_real_failure_and_reworks(tmp_path, notices):
    wf, fake = _setup(tmp_path)
    _tick(wf, fake)
    fake.rollup = _RUNNING
    _tick(wf, fake)                            # the re-run is seen in flight
    assert _run()["ci_rerun_state"] == "started"
    fake.rollup = [_check_run("test (3.12)", conclusion="FAILURE")]

    summary = _tick(wf, fake)                  # the re-run settled red on the same sha

    assert (summary["ci_failed"], summary["ci_rerun"]) == (1, 0)
    assert len(fake.reruns) == 1               # ⛔ no second re-run on the same sha
    run = _run()
    assert (run["status"], run["rework_count"]) == ("running", 1)
    assert (run["ci_rerun_state"], run["ci_failed_sha"]) == ("red", "deadbee1")
    verdict = dispatcher.latest_verdict(run)
    assert _NOTE in verdict and "failed AGAIN on the re-run" in verdict
    with dispatcher._db() as conn:             # a human re-runs it green later: not a flake
        conn.execute("UPDATE runs SET status='awaiting_review' WHERE task_id='abc123'")
        conn.commit()
    fake.rollup = _GREEN
    assert _tick(wf, fake)["ci_flake"] == 0
    assert notices == []


def test_a_stale_red_right_after_the_rerun_is_waited_out_then_bounded(tmp_path):
    wf, fake = _setup(tmp_path)
    _tick(wf, fake)

    summary = _tick(wf, fake)                  # GitHub still shows the OLD red

    assert (summary["ci_failed"], fake.log_fetches) == (0, 1)
    assert (_run()["status"], _run()["ci_rerun_state"]) == ("awaiting_review", "pending")
    with dispatcher._db() as conn:             # never seen unsettled, past the settle window
        conn.execute("UPDATE runs SET ci_rerun_at=? WHERE task_id='abc123'",
                     ("2026-01-01T00:00:00+00:00",))
        conn.commit()
    assert _tick(wf, fake)["ci_failed"] == 1
    assert (_run()["rework_count"], _run()["ci_rerun_state"]) == (1, "red")


def test_a_new_push_resets_the_rerun_and_green_elsewhere_is_no_flake(tmp_path, notices):
    wf, fake = _setup(tmp_path)
    _tick(wf, fake)
    fake.sha, fake.rollup = "newpush1", _GREEN
    assert _tick(wf, fake)["ci_flake"] == 0     # green, but not on the re-run's sha
    assert (_run()["ci_rerun_state"], _run()["ci_flakes"], notices) == ("pending", None, [])

    fake.sha, fake.rollup = "newpush2", [_check_run("test (3.12)", conclusion="FAILURE")]
    assert _tick(wf, fake)["ci_rerun"] == 1
    assert len(fake.reruns) == 2 and _run()["ci_rerun_sha"] == "newpush2"


def _refuse_second(fake):
    real = fake.run

    def run(cmd, *a, **k):
        r = real(cmd, *a, **k)
        if isinstance(cmd, list) and cmd[:3] == ["gh", "run", "rerun"]:
            r.returncode = 0 if cmd[3] == "1" else 1
        return r
    fake.run = run


def _raising(exc):
    def wrap(fake):
        real = fake.run

        def run(cmd, *a, **k):
            if isinstance(cmd, list) and cmd[:3] == ["gh", "run", "rerun"]:
                raise exc
            return real(cmd, *a, **k)
        fake.run = run
    return wrap


_TWO = [_check_run("a", conclusion="FAILURE", run_id="1"),
        _check_run("b", conclusion="FAILURE", run_id="2")]


@pytest.mark.parametrize("rollup, rc, rig", [
    (None, 1, None),                                       # GitHub refused it
    (None, 0, _raising(FileNotFoundError("no gh"))),       # an OSError subclass
    (None, 0, _raising(subprocess.TimeoutExpired(["gh"], 30))),
    (_TWO, 0, _refuse_second),                             # one of several refused
    ([{"context": "ci/legacy", "state": "FAILURE"}], 0, None),   # no run id to name
])
def test_a_rerun_that_cannot_start_falls_through_to_the_rework(tmp_path, rollup, rc, rig):
    wf, fake = _setup(tmp_path, rollup, rerun_rc=rc)
    if rig:
        rig(fake)

    summary = _tick(wf, fake)

    assert (summary["ci_failed"], summary["ci_rerun"]) == (1, 0)
    run = _run()
    assert run["rework_count"] == 1 and run["ci_rerun_sha"] is None
    assert "re-run" not in dispatcher.latest_verdict(run)


def test_every_failed_run_is_rerun(tmp_path):
    wf, fake = _setup(tmp_path, _TWO)
    assert _tick(wf, fake)["ci_rerun"] == 1
    assert fake.reruns == [["gh", "run", "rerun", "1", "--failed"],
                           ["gh", "run", "rerun", "2", "--failed"]]


def test_an_infra_only_red_is_not_rerun(tmp_path):
    wf, fake = _setup(tmp_path, [_check_run("test", conclusion="STARTUP_FAILURE")])
    assert _tick(wf, fake)["ci_infra_failed"] == 1
    assert fake.reruns == []


def test_a_corrupt_flake_record_is_restarted_not_fatal(tmp_path, notices):
    wf, fake = _setup(tmp_path)
    _tick(wf, fake)
    with dispatcher._db() as conn:
        conn.execute("UPDATE runs SET ci_flakes='not json' WHERE task_id='abc123'")
        conn.commit()
    fake.rollup = _GREEN
    assert _tick(wf, fake)["ci_flake"] == 1
    assert [f["sha"] for f in json.loads(_run()["ci_flakes"])] == ["deadbee1"]


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
    assert "failed on tests/test_wall.py::test_grid" in event["summary"]
    assert event["payload"]["note"] == _NOTE
    assert inbox.ci_flake(run, sha="", note=_NOTE) is False
    monkeypatch.setattr(inbox, "INBOX_ENABLED", False)
    assert inbox.ci_flake(run, sha="deadbee1", note=_NOTE) is False
