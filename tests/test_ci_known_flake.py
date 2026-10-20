"""🧪 CMX-282: a CI red ONLY on registered flaky tests is re-run, not sent back for rework.

Driven end-to-end through the dispatcher tick with `gh` stubbed at the argv boundary (the
CMX-255 harness): registered-only ⇒ 1 + 2 re-runs, then to the judge with `ci_known_flake`
recorded and no rework; one unregistered failure, an expired entry or a closed issue ⇒
today's single re-run and rework. Plus the registry guard CI runs on `tests/flaky.toml`.
"""
from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from chela import dispatcher, flaky, inbox
from tests.test_dispatcher_ci import _check_run, _FakeGh, _row, _tick, _wf

_RED = [_check_run("test (3.12)", conclusion="FAILURE")]
_RUNNING = [_check_run("test (3.12)", status="IN_PROGRESS", conclusion="")]
_P = "test (3.12)\tRun tests\t2026-10-20T10:00:00.0000000Z "
_FLAKE = _P + "FAILED tests/a.py::t[x] - assert 0\n"
_REAL = _P + "FAILED tests/b.py::u - assert 0\n"
_TODAY = dt.date(2026, 10, 20)


@pytest.fixture(autouse=True)
def _own_runs_db(tmp_path, monkeypatch):
    monkeypatch.setattr(dispatcher, "DB_PATH", tmp_path / "scheduler.db")


@pytest.fixture
def notices(monkeypatch):
    said: list[tuple] = []
    monkeypatch.setattr(inbox, "ci_flake",
                        lambda run, *, sha, note, known=False: said.append((note, known)))
    return said


def _setup(tmp_path, monkeypatch, log, expires="2099-01-01", states=None):
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "flaky.toml").write_text(
        f'[[flaky]]\ntest = "tests/a.py::t"\nissue = "CMX-9"\nexpires = "{expires}"\n')
    seen = []
    monkeypatch.setattr(flaky, "issue_states",
                        lambda wf, issues: seen.append(issues) or states)
    wf = _wf(tmp_path)
    with dispatcher._db() as conn:
        _row(conn, workflow_path=str(wf.path))
    return wf, _FakeGh(rollup=_RED, log=log, rerun_rc=0), seen


def _red_again(wf, fake):
    fake.rollup = _RUNNING
    _tick(wf, fake)                            # the re-run is seen in flight
    fake.rollup = _RED
    return _tick(wf, fake)


def _run():
    return dispatcher.resolve_run("abc123")


def _candidates(wf):
    with dispatcher._db() as conn:
        return [r["task_id"] for r in dispatcher._judge_candidates(conn, wf)]


def test_red_only_on_registered_is_rerun_then_judged_not_sent_back(tmp_path, monkeypatch,
                                                                   notices):
    wf, fake, seen = _setup(tmp_path, monkeypatch, _FLAKE, states={"CMX-9": None})
    assert _tick(wf, fake)["ci_rerun"] == 1                       # CMX-255's own re-run
    assert _red_again(wf, fake)["ci_rerun"] == 1
    assert _red_again(wf, fake)["ci_rerun"] == 1
    assert _run()["ci_flake_reruns"] == 2

    summary = _red_again(wf, fake)

    assert (summary["ci_known_flake"], summary["ci_failed"], summary["ci_rerun"]) == (1, 0, 0)
    assert len(fake.reruns) == 3 and seen[-1] == ["CMX-9"]
    run = _run()
    assert (run["status"], run["rework_count"], run["ci_failed_sha"]) == (
        "awaiting_review", 0, "deadbee1")
    assert json.loads(run["ci_known_flake"]) == {
        "sha": "deadbee1", "tests": [{"test": "tests/a.py::t[x]", "issue": "CMX-9"}]}
    assert notices == [("tests/a.py::t[x] (CMX-9)", True)]
    assert _candidates(wf) == ["abc123"]
    assert _tick(wf, fake)["ci_known_flake"] == 0                 # delivered once
    fake.sha = "newpush1"                                         # a new head is not judged
    _tick(wf, fake)
    assert _candidates(wf) == []


@pytest.mark.parametrize("log, expires, states", [
    (_FLAKE + _REAL, "2099-01-01", {"CMX-9": None}),    # one unregistered failure
    (_FLAKE, "2026-01-01", {"CMX-9": None}),            # expired ⇒ unregistered
    (_FLAKE, "2099-01-01", {"CMX-9": "done"}),          # its issue is closed
    (_FLAKE, "2099-01-01", {}),                         # its issue is missing
    (_FLAKE, "2099-01-01", None),                       # the tracker is unreadable
    (_FLAKE + _P.replace("Run tests", "Lint") + "ruff: F401 unused\n", "2099-01-01",
     {"CMX-9": None}),                                  # a failed step naming no test
])
def test_any_other_red_is_sent_back_as_today(tmp_path, monkeypatch, notices, log, expires,
                                             states):
    wf, fake, _ = _setup(tmp_path, monkeypatch, log, expires, states)
    _tick(wf, fake)

    summary = _red_again(wf, fake)

    assert (summary["ci_failed"], summary["ci_known_flake"]) == (1, 0)
    assert len(fake.reruns) == 1
    run = _run()
    assert (run["rework_count"], run["ci_rerun_state"], run["ci_known_flake"]) == (1, "red", None)
    assert notices == [] and _candidates(wf) == []


def test_a_red_across_two_actions_runs_is_never_a_known_flake(tmp_path, monkeypatch, notices):
    wf, fake, _ = _setup(tmp_path, monkeypatch, _FLAKE, states={"CMX-9": None})
    two = [_check_run("a", conclusion="FAILURE", run_id="1"),
           _check_run("b", conclusion="FAILURE", run_id="2")]
    fake.rollup = two
    _tick(wf, fake)
    fake.rollup = _RUNNING
    _tick(wf, fake)
    fake.rollup = two
    assert _tick(wf, fake)["ci_known_flake"] == 0
    assert _run()["rework_count"] == 1 and notices == [] and len(fake.reruns) == 2


def test_a_known_flake_rerun_github_refuses_still_proceeds_to_the_judge(tmp_path,
                                                                        monkeypatch, notices):
    wf, fake, _ = _setup(tmp_path, monkeypatch, _FLAKE, states={"CMX-9": None})
    _tick(wf, fake)
    fake.rerun_rc = 1
    assert _red_again(wf, fake)["ci_known_flake"] == 1
    assert _run()["rework_count"] == 0 and len(fake.reruns) == 2


@pytest.mark.parametrize("log, ids", [
    (_FLAKE + _REAL, ["tests/a.py::t[x]", "tests/b.py::u"]),
    ("FAILED tests/a.py::t\n\nERROR tests/c.py::v - x\n", ["tests/a.py::t", "tests/c.py::v"]),
    ("xFAILED tests/a.py::t\n", None),                  # anchored
    ("FAILED tests/a.py\n", None),                      # not a node id
    (_FLAKE + _P + "ERROR tests/y.py - ImportError\n", None),   # a collection error, same step
    ("… (log truncated — this is the tail)\nFAILED tests/a.py::t\n", None),
    ("(could not fetch the CI log: boom)", None),
    (_FLAKE + "\n" + _FLAKE, ["tests/a.py::t[x]"]),     # a blank line is not a step
    ("", None),
])
def test_failed_tests(log, ids):
    assert flaky.failed_tests(log) == ids


@pytest.mark.parametrize("entry, why", [
    ({"test": "tests/a.py::t", "issue": "CMX-9", "expires": "2026-10-20"}, []),
    ({"test": "tests/a.py::t", "issue": "CMX-9", "expires": dt.date(2026, 10, 19)},
     ["tests/a.py::t: expired 2026-10-19"]),
    ({"test": "tests/a.py::t", "expires": "2099-01-01"}, ["tests/a.py::t: no tracker issue"]),
    ({"test": "tests/a.py::t", "issue": "cmx 9", "expires": "soon"},
     ["tests/a.py::t: no tracker issue", "tests/a.py::t: `expires` must be YYYY-MM-DD"]),
    ({"test": "tests/a.py", "issue": "CMX-9", "expires": "2099-01-01"},
     ["'tests/a.py': `test` must be a pytest node id"]),
])
def test_problems(entry, why):
    assert flaky.problems([entry], _TODAY) == why


def test_problems_reads_the_tracker_and_known_covers_params_only():
    e = {"test": "tests/a.py::t", "issue": "CMX-9", "expires": "2099-01-01"}
    assert flaky.problems([e], _TODAY, {"CMX-9": "canceled"}) == ["tests/a.py::t: CMX-9 is canceled"]
    assert flaky.problems([e], _TODAY, {}) == ["tests/a.py::t: CMX-9 not found in the tracker"]
    assert flaky.known(["tests/a.py::t_other"], [e], _TODAY) is None
    assert flaky.known([], [e], _TODAY) is None


def test_no_registry_file_is_an_empty_registry(tmp_path):
    assert flaky.load(tmp_path) == []


class _Src:
    def __init__(self, result):
        self.result, self.asked = result, []

    def fetch_by_ids(self, ids):
        self.asked.append(ids)
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


@pytest.mark.parametrize("result, want", [
    ([SimpleNamespace(id="CMX-9", terminal_state=None),
      SimpleNamespace(id="CMX-8", terminal_state="done")], {"CMX-9": None, "CMX-8": "done"}),
    (None, None),                                       # the read failed
    (RuntimeError("tracker down"), None),               # it raised
])
def test_issue_states_reads_the_workflows_tracker_and_fails_closed(monkeypatch, result, want):
    import chela.sources
    src, wf = _Src(result), object()
    monkeypatch.setattr(chela.sources, "get_source", lambda w: src if w is wf else None)
    assert flaky.issue_states(wf, ["CMX-9", "CMX-8", "CMX-9"]) == want
    assert src.asked == [["CMX-8", "CMX-9"]]


def test_an_unreadable_registry_is_a_real_red(tmp_path):
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "flaky.toml").write_text("flaky = 3\n")
    wf = _wf(tmp_path)
    assert flaky.classify(wf, _FLAKE, _TODAY) is None
    (tmp_path / "tests" / "flaky.toml").write_text("[[flaky\n")
    assert flaky.classify(wf, _FLAKE, _TODAY) is None


def test_the_registry_is_not_a_hiding_place():
    """⛔ The guard CI runs: no expired, issue-less or malformed entry — and, where the
    tracker is readable (LINEAR_API_KEY), none whose issue is Done/Canceled/missing."""
    repo = Path(__file__).resolve().parent.parent
    entries = flaky.load(repo)
    today = dt.datetime.now(dt.timezone.utc).date()
    assert flaky.problems(entries, today) == []
    from chela.sources.linear import load_api_key
    from chela.workflow import load_workflow
    if entries and load_api_key():
        states = flaky.issue_states(load_workflow(repo / "WORKFLOW.md"),
                                    [e["issue"] for e in entries])
        assert states is not None, "the tracker could not be read"
        assert flaky.problems(entries, today, states) == []


def test_the_known_flake_notice_names_the_issue_and_the_judge(tmp_path, monkeypatch):
    monkeypatch.setenv("CHELA_INBOX_FILE", str(tmp_path / "inbox.json"))
    monkeypatch.setenv("CHELA_EVENTS_FILE", str(tmp_path / "events.jsonl"))
    monkeypatch.setattr(inbox, "INBOX_ENABLED", True)
    run = {"task_id": "abc123", "status": "awaiting_review", "title": "t",
           "pr_url": "https://github.com/o/r/pull/80"}
    assert inbox.ci_flake(run, sha="deadbee1", note="tests/a.py::t (CMX-9)", known=True)
    [event] = inbox.load()["queue"]
    assert event["kind"] == "run_ci_known_flake"
    assert ("CI red only on known flakes on deadbee — tests/a.py::t CMX-9 — NOT sent back "
            "for rework, on to the judge") in event["summary"]
