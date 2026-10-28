"""🔎 CMX-351: the CI-flake notice names the failing TEST, or says it could not.

Twice in two days the "CI flake, re-run green" notice named a log line instead of a test:
"failed on re-register failed nope" (a Vitest test's stderr) and "failed on Test Files 1
failed 1" (Vitest's summary). Both real logs are fixtures here, with `gh run view
--log-failed`'s own prefixes and ANSI: in each, pytest's `FAILED <nodeid>` line carries a
multi-line assertion message that pushed the line itself out of the 4000-char tail the old
parser read, and its fallback took the first line saying "failed". The id is now read off the
WHOLE log, and only from a line that names a test; anything else is "not identified".
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from chela import dispatcher, flaky, inbox
from tests.test_dispatcher_ci import _check_run, _FakeGh, _row, _tick, _wf

LOGS = Path(__file__).parent / "fixtures" / "ci_logs"
CMX_328 = "pytest_wraps_vitest_unhandled_error_2026-10-25.log"
CMX_350 = "js_suite_vitest_fail_2026-10-26.log"
_DIFF = ("tests/diff_modal_wiring.test.mjs > a real click on the wall tile's \"Files\" chip "
         "opens the diff modal island on THAT session's diff")
_GATE = "tests/test_frontend.py::test_each_ci_gate_passes_the_committed_tree[test]"


def _log(name: str) -> str:
    return (LOGS / name).read_text(encoding="utf-8", errors="replace")


@pytest.fixture(autouse=True)
def _own_runs_db(tmp_path, monkeypatch):
    monkeypatch.setattr(dispatcher, "DB_PATH", tmp_path / "scheduler.db")


@pytest.mark.parametrize("name, test_id", [(CMX_328, _GATE), (CMX_350, _DIFF)])
def test_the_two_real_logs_name_their_test(name, test_id):
    assert flaky.failing_test(_log(name)) == test_id


@pytest.mark.parametrize("name, old, in_tail", [
    (CMX_328, "re-register failed nope", None),       # the FAILED line fell out of the tail
    (CMX_350, "Test Files  1 failed (1)", _DIFF),     # the summary came before the FAIL line
])
def test_what_the_old_parser_read_off_the_real_tails(name, old, in_tail):
    """The regression's mechanism, reproduced: the old notice's line is the first one in the
    tail that says "failed"; the whole log is what names the test in both."""
    from chela.inbox import first_failure_line
    tail = dispatcher._log_tail(dispatcher.tui_sanitize(_log(name)))
    assert "truncated" in tail
    assert first_failure_line(tail) == old
    assert flaky.failing_test(tail) == in_tail


@pytest.mark.parametrize("log, test_id", [
    # pytest: the short-summary line, its message dropped
    ("FAILED tests/x.py::test_y - boom\n", "tests/x.py::test_y"),
    ("FAILED tests/x.py::test_y\n", "tests/x.py::test_y"),
    ("FAILED tests/x.py::T::test_y[a-1] - AssertionError: x\n", "tests/x.py::T::test_y[a-1]"),
    ("ERROR tests/y.py - ImportError\n", "tests/y.py"),               # a collection error
    ("FAILED tests/x.py::t[a - b] - boom\n", "tests/x.py::t[a - b]"),   # " - " in a param
    ("ERROR tests/y.py::test_z - fixture\n", "tests/y.py::test_z"),
    ("\x1b[31mFAILED\x1b[0m tests/x.py::t - boom\n", "tests/x.py::t"),  # coloured
    ("test (3.12)\tPytest\t2026-10-26T09:15:14.8261747Z FAILED tests/x.py::t - b\n",
     "tests/x.py::t"),                                                 # gh's prefix
    ("FAILED tests/a.py::one - x\nFAILED tests/b.py::two - y\n", "tests/a.py::one"),  # first
    # Vitest: the failure header, with its separators kept as the id
    (" FAIL  src/a.test.ts > suite > the test\n", "src/a.test.ts > suite > the test"),
    ("FAIL src/a.spec.tsx > t\n", "src/a.spec.tsx > t"),
    ("FAIL  tests/w.test.mjs > t\n", "tests/w.test.mjs > t"),
    ("FAIL  src/a.test.ts [ src/a.test.ts ]\n", "src/a.test.ts"),       # a suite that failed
    ("\x1b[41m\x1b[1m FAIL \x1b[22m\x1b[49m src/a.test.ts\x1b[2m > \x1b[22mt\n",
     "src/a.test.ts > t"),
    ("E           FAIL  src/a.test.ts > t\n", "src/a.test.ts > t"),   # inside pytest's E block
    # a Vitest id wins over the pytest wrapper that ran it, in either order
    ("FAILED tests/test_js.py::test_js[w] - x\nFAIL  src/a.test.ts > t\n", "src/a.test.ts > t"),
    ("FAIL  src/a.test.ts > t\nFAILED tests/test_js.py::test_js[w] - x\n", "src/a.test.ts > t"),
])
def test_a_line_that_names_a_test_gives_its_id(log, test_id):
    assert flaky.failing_test(log) == test_id


@pytest.mark.parametrize("log", [
    "",
    # ⛔ the negative control: a line that merely says "failed" is never an id
    "re-register failed nope\n",
    " Test Files  1 failed (1)\n      Tests  1 failed | 3 passed (4)\n",
    "1 failed, 10625 passed, 40 skipped in 677.99s\n",
    "stderr | src/a.test.ts > the test\nre-register failed nope\n",   # a test's own stderr
    "Error: Process completed with exit code 1.\n",
    "AssertionError: the job failed\n",
    # anchored, spaced, and only test files
    "xFAILED tests/x.py::t\n",
    "FAILEDtests/x.py::t\n",
    "FAILED test_x\n",                     # not a .py file
    "FAILED - something\n",
    "FAIL  src/a.ts > t\n",                # not a spec file
    "FAILx src/a.test.ts > t\n",
    "XFAIL  src/a.test.ts > t\n",
    "  × src/a.test.ts > t 12ms\n",
    "(could not fetch the CI log: HTTP 404)",
])
def test_a_log_without_a_test_id_gives_none(log):
    assert flaky.failing_test(log) is None


@pytest.mark.parametrize("value, ok", [
    ("tests/x.py::t", True), ("tests/x.py", True), ("src/a.test.ts > s > t", True),
    ("src/a.test.ts", True),
    (None, False), ("?", False), ("", False), ("re-register failed nope", False),
    ("Test Files 1 failed 1", False), ("tests/x.py::t - boom", False), (42, False),
    ("tests/x.py::t[a - b]", True), ("tests/x.py::t[a", False),
    ("tests/a.py::t\nFAIL src/x.test.ts > t", False), ("src/a.test.ts > t\nx", False),
])
def test_is_test_id(value, ok):
    assert flaky.is_test_id(value) is ok


@pytest.mark.parametrize("tid, path", [
    ("tests/x.py::T::t[a]", "tests/x.py"), ("src/a.test.ts > s > t", "src/a.test.ts"),
    ("tests/x.py", "tests/x.py"),
])
def test_file_of(tid, path):
    assert flaky.file_of(tid) == path


@pytest.mark.parametrize("log, note", [
    ("FAILED tests/x.py::t - boom\n", "CI re-run once: CI / test failed on tests/x.py::t"),
    (" Test Files  1 failed (1)\n",
     "CI re-run once: CI / test failed — failing test not identified"),
])
def test_the_rerun_note_names_the_test_or_says_it_is_not_identified(log, note):
    assert dispatcher._ci_rerun_note("CI / test", log) == note


def _flake_through_a_tick(tmp_path, log):
    """A red → re-run → green on one sha, with `gh` stubbed at the argv boundary."""
    wf = _wf(tmp_path)
    with dispatcher._db() as conn:
        _row(conn, workflow_path=str(wf.path))
    fake = _FakeGh(rollup=[_check_run("test (3.12)", conclusion="FAILURE")], log=log,
                   rerun_rc=0)
    said = []
    with patch.object(inbox, "ci_flake",
                      lambda run, *, sha, note: said.append(note)):
        _tick(wf, fake)
        fake.rollup = [_check_run("test (3.12)")]
        assert _tick(wf, fake)["ci_flake"] == 1
    [flake] = json.loads(dispatcher.resolve_run("abc123")["ci_flakes"])
    return said, flake


@pytest.mark.parametrize("name, test_id", [(CMX_328, _GATE), (CMX_350, _DIFF)])
def test_a_real_flake_is_noticed_and_recorded_under_its_test_id(tmp_path, name, test_id):
    said, flake = _flake_through_a_tick(tmp_path, _log(name))
    assert said == [f"CI re-run once: CI / test (3.12) failed on {test_id}"]
    assert flake["test"] == test_id


def test_a_flake_with_no_test_id_says_so_and_is_recorded_under_no_name(tmp_path):
    said, flake = _flake_through_a_tick(tmp_path, "something broke\nre-register failed nope\n")
    assert said == ["CI re-run once: CI / test (3.12) failed — failing test not identified"]
    assert flake["test"] is None


@pytest.mark.parametrize("note, test", [
    ("CI re-run once: CI / test failed on tests/x.py::t", "tests/x.py::t"),
    ("CI re-run once: CI / test failed on src/a.test.ts > s > t failed on x",
     "src/a.test.ts > s > t failed on x"),                    # the FIRST " failed on "
    ("CI re-run once: CI / test failed — failing test not identified", None),
    ("CI re-run once: CI / test failed on re-register failed nope", None),   # pre-CMX-351
    ("CI re-run once: CI / test failed on ?", None),
    ("", None),
])
def test_a_recorded_flake_keeps_only_a_test_id(tmp_path, note, test):
    with dispatcher._db() as conn:
        _row(conn)
        conn.execute("UPDATE runs SET ci_rerun_note=? WHERE task_id='abc123'", (note,))
        conn.commit()
        dispatcher._record_ci_flake(conn, dispatcher.resolve_run("abc123"), "deadbee1")
        conn.commit()
    [flake] = json.loads(dispatcher.resolve_run("abc123")["ci_flakes"])
    assert (flake["test"], flake["note"]) == (test, note)
