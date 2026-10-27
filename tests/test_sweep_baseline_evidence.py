"""⚖️🧹🌙 CMX-341 — a red nightly baseline names its failing tests, keeps its evidence, and a
single flaky test does not cost the whole night.

Two nights came back "CANNOT VERIFY — 1 failed, 11024 passed" with no test named and the JUnit
report gone with the checkout. These pin: the notice names each failing test id with the first
line of its failure; the report and the output tail are kept under
``$CHELA_DIR/sweep-nightly/<date>/<project>/``; the failing tests are re-run ONCE alone, and
green there is a ``baseline flake`` deselected from every mutation run.
"""
from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path

import pytest

import test_sweep_nightly as tsn
from chela import judge, judge_select, sweep, sweep_nightly

RED = "def test_red():\n    assert False, 'boom-341 the first line'\n"
# Red on its 1st run, green on its 2nd (the isolated re-run), red on every run after — so a
# mutation run that still includes it is KILLED by it, and only a deselect lets the battery see.
FLAKY = '''\
from pathlib import Path

def test_flaky():
    f = Path({count!r})
    n = int(f.read_text()) if f.exists() else 0
    f.write_text(str(n + 1))
    assert n == 1, "flaky-341 not this time"
'''


@pytest.fixture(autouse=True)
def notices(monkeypatch):
    got: list[tuple[str, str, dict]] = []
    monkeypatch.setattr("chela.inbox.enqueue",
                        lambda kind, summary, payload: got.append((kind, summary, payload)))
    return got


def _night(tmp_path: Path, test_file: str, body: str, filer=None) -> tuple[dict, Path]:
    prs = [({"size.py": tsn.WEAK, test_file: body}, "PR (#4)", None)]
    chela = tmp_path / "chela"
    res = tsn._run(tsn._repo(tmp_path, prs=prs), chela, filer or tsn.Filer())
    return res, chela


def _kept(chela: Path, res: dict) -> Path:
    day = datetime.fromtimestamp(res["at"]).date().isoformat()
    return chela / "sweep-nightly" / day / "repo"


# --- the acceptance fixtures ------------------------------------------------------------------


def test_a_red_baseline_names_its_test_keeps_its_junit_and_says_it_stayed_red(tmp_path, notices):
    res, chela = _night(tmp_path, "tests/test_red.py", RED)
    assert res["status"] == "cannot_verify"
    (_, summary, payload), = notices
    assert "1 failing: tests/test_red.py::test_red — AssertionError: boom-341 the first line" \
        in summary
    assert "still red when re-run alone" in summary
    kept = _kept(chela, res)
    assert f"JUnit + output kept in {kept}" in summary
    assert 'name="test_red"' in (kept / "baseline-junit.xml").read_text()
    out = (kept / "baseline-output.txt").read_text()
    assert "boom-341" in out and "=== re-run alone: " in out
    assert payload["baseline_failures"] == [
        "tests/test_red.py::test_red — AssertionError: boom-341 the first line"]
    assert payload["baseline_flakes"] == [] and payload["kept"] == str(kept)
    assert tsn._covered_record(chela) == {}     # a red night never moves the window


def test_a_baseline_flake_is_reported_and_the_battery_runs_without_it(tmp_path, notices):
    filer = tsn.Filer()
    res, chela = _night(tmp_path, "tests/test_flaky.py",
                        FLAKY.format(count=str(tmp_path / "count")), filer)
    # ⛔ Negative control built in: test_flaky is red on every run after its re-run, so the
    # weak guard is only seen to SURVIVE when every mutation run deselects it.
    assert (res["status"], res["filed"], filer.sites) == ("survivors", ["CMX-901"],
                                                          ["size.py:2"])
    assert res["baseline_flakes"] == ["tests/test_flaky.py::test_flaky"]
    (_, summary, _), = notices
    assert ("⚠️ baseline flake: tests/test_flaky.py::test_flaky (red once, green alone; "
            "deselected from the battery)") in summary
    kept = _kept(chela, res)
    assert f"evidence in {kept}" in summary
    assert 'name="test_flaky"' in (kept / "baseline-junit.xml").read_text()


def test_a_green_baseline_keeps_nothing_and_says_no_flake(tmp_path, notices):
    res, chela = _night(tmp_path, "tests/test_ok.py", "def test_ok():\n    assert True\n")
    assert res["status"] == "survivors" and res["kept"] == "" and res["baseline_flakes"] == []
    assert not _kept(chela, res).exists()
    assert "baseline flake" not in notices[0][1]


# --- naming, re-run notes and keeping, unit-level ----------------------------------------------


def _red(*, tail="", failed=1, errors=0, passed=3, timed_out=False, output=None):
    run = {"cmd": "pytest -q", "output": output} if output is not None else None
    return judge.SuiteResult(True, 1, passed, failed, errors, tail, "", run, timed_out)


def _junit(path: Path, cases: list[tuple[str, str, str]]) -> Path:
    """``(classname, name, child)`` with child "" (passed), or a ``<failure …/>`` string."""
    body = "".join(f'<testcase classname="{c}" name="{n}">{x}</testcase>' for c, n, x in cases)
    path.write_text(f'<testsuites><testsuite name="pytest">{body}</testsuite></testsuites>')
    return path


def test_the_first_five_failures_are_named_and_the_rest_counted(tmp_path):
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_a.py").write_text("")
    junit = _junit(tmp_path / "j.xml", [("tests.test_a", f"t{i}", f'<failure message="m{i}"/>')
                                        for i in range(7)])
    report = sweep.SweepReport()
    sweep._read_red_baseline(report, tmp_path, _red(failed=7), [], [], junit, None)
    phrase = sweep._failing_phrase(report)
    assert phrase.startswith(" — 7 failing: tests/test_a.py::t0 — m0; ")
    assert "tests/test_a.py::t4 — m4 (+2 more)" in phrase and "t5" not in phrase


def test_an_unreadable_junit_falls_back_to_the_tails_failed_lines(tmp_path):
    report = sweep.SweepReport()
    red = _red(tail="FAILED tests/test_b.py::test_x - nope\n1 failed, 3 passed\n")
    sweep._read_red_baseline(report, tmp_path, red, [], [], tmp_path / "missing.xml", None)
    assert report.baseline_failures == ["tests/test_b.py::test_x"]


@pytest.mark.parametrize("red, flakes, reruns, said", [
    (_red(), ["a::b"], [], "green when re-run alone — a flake"),
    (_red(), [], [_red()], "still red when re-run alone"),
    (_red(tail="FAILED t.py::a - x\n", errors=1), [], [],
     "not re-run alone: 1 error(s) — an error is not a named failure"),
    (_red(tail="1 failed\n"), [], [], "not re-run alone: its failures could not be named"),
    (_red(tail="FAILED t.py::a - x\n", timed_out=True), [], [], "not re-run alone: it timed out"),
    (_red(tail="FAILED t.py::a - x\n"), [], [],
     "not re-run alone: only one pytest invocation with `judge.select_tests` on is re-run"),
])
def test_what_the_isolated_re_run_said_is_named(tmp_path, red, flakes, reruns, said):
    report = sweep.SweepReport(baseline_flakes=list(flakes))
    sweep._read_red_baseline(report, tmp_path, red, [], reruns, tmp_path / "none.xml", None)
    assert report.baseline_rerun == said


def test_a_flakes_names_come_from_its_own_red_run_not_the_green_baseline(tmp_path):
    report = sweep.SweepReport(baseline_flakes=["t.py::a"])
    green = judge.SuiteResult(True, 0, 3, 0, 0, "")
    first_red = _red(tail="FAILED t.py::a - flaky\n")
    sweep._read_red_baseline(report, tmp_path, green, [first_red], [], tmp_path / "no.xml", None)
    assert report.baseline_failures == ["t.py::a"]


def test_the_kept_output_is_the_runs_output_tail_then_each_re_run(tmp_path):
    keep = tmp_path / "keep"
    big = "x" * (sweep.KEPT_OUTPUT_CHARS + 10) + "END"
    rerun = _red(output="again-red")
    report = sweep.SweepReport()
    sweep._read_red_baseline(report, tmp_path, _red(output=big), [], [rerun],
                             tmp_path / "no.xml", keep)
    text = (keep / "baseline-output.txt").read_text()
    assert text.startswith("x") and text.split("\n")[0].endswith("END")
    assert len(text.split("\n=== ")[0]) == 50_000
    assert text.endswith("\n=== re-run alone: pytest -q ===\nagain-red")
    assert not (keep / "baseline-junit.xml").exists()      # no report written, none invented
    assert report.kept == str(keep)


def test_a_run_without_a_report_drops_an_earlier_runs_report_from_the_same_dir(tmp_path):
    keep = tmp_path / "keep"
    keep.mkdir()
    (keep / "baseline-junit.xml").write_text("<stale/>")
    sweep._read_red_baseline(sweep.SweepReport(), tmp_path, _red(output="now"), [], [],
                             tmp_path / "no.xml", keep)
    assert not (keep / "baseline-junit.xml").exists()
    assert (keep / "baseline-output.txt").read_text() == "now"


def test_evidence_that_cannot_be_kept_is_said_and_never_claimed(tmp_path):
    blocker = tmp_path / "file"
    blocker.write_text("")
    report = sweep.SweepReport()
    sweep._read_red_baseline(report, tmp_path, _red(tail="t"), [], [], tmp_path / "no.xml",
                             blocker / "sub")      # mkdir under a FILE: NotADirectoryError
    assert report.kept == ""
    assert any("could NOT be kept in" in n for n in report.notes)
    assert "kept in" not in sweep._failing_phrase(report)


# --- the JUnit reader ---------------------------------------------------------------------------


def test_failed_cases_names_failures_and_errors_with_their_first_line(tmp_path):
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_a.py").write_text("")
    junit = _junit(tmp_path / "j.xml", [
        ("tests.test_a", "ok", ""),
        ("tests.test_a.TestK", "bad[1]", '<failure message="line one&#10;line two"/>'),
        ("tests.test_a", "err", "<error>setup broke\nmore</error>"),
        ("tests.test_a", "skip", '<skipped message="no"/>'),
        ("nowhere.mod", "lost", '<failure message=""/>'),
    ])
    assert judge_select.failed_cases(tmp_path, junit) == [
        ("tests/test_a.py::TestK::bad[1]", "line one"),
        ("tests/test_a.py::err", "setup broke"),
        ("nowhere.mod::lost", ""),
    ]


def test_failed_cases_of_an_unreadable_report_is_none_never_empty(tmp_path):
    (tmp_path / "bad.xml").write_text("<testsuites")
    assert judge_select.failed_cases(tmp_path, tmp_path / "bad.xml") is None
    assert judge_select.failed_cases(tmp_path, tmp_path / "absent.xml") is None


# --- the judge's isolated re-run ----------------------------------------------------------------


@pytest.mark.parametrize("red, named, why", [
    (_red(), ["a"], ""),
    (_red(), [], "its failures could not be named"),
    (_red(errors=2), ["a"], "2 error(s) — an error is not a named failure"),
    (_red(failed=2), ["a"], "2 failure(s), 1 named"),
    (_red(passed=0), ["a"], "nothing passed — the suite never ran"),
])
def test_rerun_skip_reason(red, named, why):
    assert judge.rerun_skip_reason(red, named) == why


def test_a_tail_without_failed_lines_is_re_run_by_its_junit_names(tmp_path, monkeypatch):
    """The tail is the last 3000 chars; the JUnit report is the whole run. A red baseline whose
    tail lost pytest's FAILED lines still gets its isolated re-run — and it is handed back."""
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_a.py").write_text("")
    calls: list[str] = []
    rerun = judge.SuiteResult(True, 0, 1, 0, 0, "1 passed")

    def fake(cmd, cwd, timeout, extra_env=None):
        calls.append(cmd)
        if "--junitxml=" in cmd:
            path = cmd.split("--junitxml=", 1)[1].split()[0]
            _junit(Path(path), [("tests.test_a", "t", '<failure message="m"/>')])
            return _red(tail="1 failed, 3 passed\n")
        return rerun
    monkeypatch.setattr(judge, "run_suite", fake)
    monkeypatch.setattr(judge, "_has_pytest_cov", lambda wt: False)
    monkeypatch.setattr(judge_select, "load_cached_coverage", lambda sha: None)
    flakes, reruns = [], []
    scratch = tmp_path / "s"
    scratch.mkdir()
    base, _, _ = judge.run_baseline(tmp_path, "pytest -q", 60, scratch, flakes=flakes,
                                    reruns=reruns)
    assert calls[1] == "pytest -q tests/test_a.py::t"
    assert base.green and flakes == ["tests/test_a.py::t"] and reruns == [rerun]


def test_a_rerun_that_is_still_red_is_handed_back_and_stays_red(tmp_path, monkeypatch):
    again = _red(tail="FAILED t.py::a - x\n")
    monkeypatch.setattr(judge, "run_suite", lambda *a, **k: again)
    reruns: list = []
    out = judge._rerun_failed_once(tmp_path, "pytest -q", 60, _red(tail="FAILED t.py::a - x\n"),
                                   None, None, reruns=reruns)
    assert not out.green and reruns == [again]


# --- where the evidence lives, and how long ----------------------------------------------------


def test_evidence_dir_is_state_date_project(tmp_path):
    wf = type("W", (), {"path": Path("/x/chelamux/WORKFLOW.md")})()
    at = datetime(2026, 10, 26, 2, 10).timestamp()
    assert sweep_nightly.evidence_dir(wf, at, tmp_path) == \
        tmp_path / "sweep-nightly" / "2026-10-26" / "chelamux"


def test_prune_keeps_the_newest_dated_dirs_and_nothing_else(tmp_path):
    state = sweep_nightly.state_dir(tmp_path)
    for name in ("2026-10-01", "2026-10-02", "2026-10-03", "notes", "2026-10-x"):
        (state / name).mkdir(parents=True)
    (state / "2026-09-30").write_text("a file, not a dir")
    assert sweep_nightly.prune_evidence(tmp_path, keep=2) == ["2026-10-01"]
    assert sorted(p.name for p in state.iterdir()) == [
        "2026-09-30", "2026-10-02", "2026-10-03", "2026-10-x", "notes"]
    assert sweep_nightly.KEEP_DAYS == 14


def test_prune_of_a_missing_state_dir_is_a_no_op(tmp_path):
    assert sweep_nightly.prune_evidence(tmp_path / "nowhere") == []


def test_a_dir_that_cannot_be_pruned_is_logged_and_the_rest_still_go(tmp_path, monkeypatch,
                                                                     caplog):
    state = sweep_nightly.state_dir(tmp_path)
    for name in ("2026-10-01", "2026-10-02", "2026-10-03"):
        (state / name).mkdir(parents=True)
    import shutil
    real = shutil.rmtree

    def rmtree(p, *a, **k):
        if Path(p).name == "2026-10-01":
            raise PermissionError("held")
        return real(p, *a, **k)
    monkeypatch.setattr(shutil, "rmtree", rmtree)
    with caplog.at_level("WARNING", logger="chela.sweep_nightly"):
        assert sweep_nightly.prune_evidence(tmp_path, keep=1) == ["2026-10-02"]
    assert any("could not prune" in r.getMessage() for r in caplog.records)
    assert os.path.isdir(state / "2026-10-01")


def test_the_summary_says_flake_only_when_there_is_one():
    base = {"workflow": "/p/chelamux/WORKFLOW.md", "status": "clean", "filed": [],
            "already_filed": 0, "failed": [], "would_file": [], "warnings": [], "survivors": 0,
            "groups": [], "prs": []}
    assert "flake" not in sweep_nightly.summary(base)
    line = sweep_nightly.summary({**base, "baseline_flakes": ["t.py::a", "t.py::b"]})
    assert line.endswith("— ⚠️ baseline flake: t.py::a, t.py::b (red once, green alone; "
                         "deselected from the battery)")
