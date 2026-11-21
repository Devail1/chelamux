"""⏳ CMX-455 — the nightly sweep keeps up: a fixed sample per merged PR, no start without one
mutation's time left, and a notice that counts instead of listing.

11-21 measured 142 mutations in 2.8 h while 15 PRs added 1,145 (1,007 owed and growing); a
04:30 resume with ~2 min left ran nothing, used 8 min and sent an "UNRUN" notice; and the 00:00
notice listed 49 dropped lines one by one.
"""
from __future__ import annotations

import json
import logging
import time
from datetime import datetime
from pathlib import Path

import pytest

from chela import judge, sweep, sweep_nightly
from chela.workflow import load_workflow, parse_workflow
from tests.test_sweep_nightly import WEAK_MUT, Filer, _repo, _run, _workflow_text

NIGHT = datetime(2026, 11, 21, 4, 30)


@pytest.fixture(autouse=True)
def notices(monkeypatch):
    got: list[tuple[str, str, dict]] = []
    monkeypatch.setattr("chela.inbox.enqueue",
                        lambda kind, summary, payload: got.append((kind, summary, payload)))
    return got


def _m(file="a.py", line=1, kind="if-false", after=None):
    return sweep.Mutation(file, line, kind, f"x{line}", after or f"y{line}{kind}")


def _wf(tmp_path: Path, extra: str = ""):
    return parse_workflow(tmp_path / "WORKFLOW.md", _workflow_text(tmp_path, extra=extra))


def _pace(chela: Path, wf, mutation, overhead) -> None:
    path = sweep_nightly.state_dir(chela) / "pace.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({str(wf.path): {"mutation": mutation, "overhead": overhead}}))


# --- the sample --------------------------------------------------------------------------


def test_stratified_takes_one_of_each_operator_in_turn_up_to_k():
    a1, a2, a3 = _m(line=1), _m(line=2), _m(line=3)
    b1, b2 = _m(line=4, kind="compare-flip"), _m(line=5, kind="compare-flip")
    c1 = _m(line=6, kind="return-code")
    items = [a1, a2, a3, b1, b2, c1]
    assert sweep_nightly.stratified(items, 4) == [a1, b1, c1, a2]
    assert sweep_nightly.stratified(items, 99) == [a1, b1, c1, a2, b2, a3]
    assert sweep_nightly.stratified(items, 1) == [a1]
    assert sweep_nightly.stratified([], 5) == []


def _two_prs():
    """PR "new" (2 if-false, 1 compare-flip) and PR "old" (3 if-false), in sweep order."""
    n1, n2 = _m("n.py", 1), _m("n.py", 2)
    n3 = _m("n.py", 3, "compare-flip")
    o1, o2, o3 = _m("o.py", 1), _m("o.py", 2), _m("o.py", 3)
    origin = {("n.py", i): "new" for i in (1, 2, 3)} | {("o.py", i): "old" for i in (1, 2, 3)}
    blamed = {"n.py": {1, 2, 3}, "o.py": {1, 2, 3}}
    return (n1, n2, n3, o1, o2, o3), origin, blamed


def test_the_sample_takes_k_per_pr_stratified_and_interleaves_the_prs():
    (n1, n2, n3, o1, o2, o3), origin, blamed = _two_prs()
    rank = {"new": 0, "old": 1}
    out, counts = sweep_nightly.sample_order([o1, o2, o3, n1, n2, n3], [], blamed, origin,
                                             k=2, cap=None, pr_rank=rank)
    # Each PR's first before any PR's second; the newer PR's two cover both its operators.
    assert out == [n1, o1, n3, o2]
    assert counts == {"new": 6, "sampled": 4, "dropped": 2, "prs": 2}


def test_the_sample_follows_the_pr_rank_and_an_unranked_pr_comes_last():
    (n1, n2, n3, o1, o2, o3), origin, blamed = _two_prs()
    out, _ = sweep_nightly.sample_order([n1, n2, n3, o1, o2, o3], [], blamed, origin, k=1,
                                        cap=None, pr_rank={"old": 0})
    assert out == [o1, n1]


def test_the_cap_cuts_the_sample_after_the_owed_but_never_below_one_per_pr():
    (n1, n2, n3, o1, o2, o3), origin, blamed = _two_prs()
    owed_mut = _m("z.py", 9)
    owed = [sweep_nightly.mutation_id(owed_mut)]
    muts = [n1, n2, n3, o1, o2, o3, owed_mut]
    rank = {"new": 0, "old": 1}
    # cap 4: the owed one, then 3 of the sample.
    out, counts = sweep_nightly.sample_order(muts, owed, blamed, origin, k=3, cap=4,
                                             pr_rank=rank)
    assert out == [owed_mut, n1, o1, n3]
    assert (counts["sampled"], counts["dropped"]) == (3, 3)
    # cap 1 is all taken by the owed — still one per PR.
    out, counts = sweep_nightly.sample_order(muts, owed, blamed, origin, k=3, cap=1,
                                             pr_rank=rank)
    assert out == [owed_mut, n1, o1]
    assert (counts["sampled"], counts["dropped"], counts["prs"]) == (2, 4, 2)


def test_the_sample_at_the_11_21_scale_measures_every_pr_and_owes_no_more_than_it_fits():
    """15 PRs, 1,145 mutations, K=20, a 180-minute budget at the measured ~71 s a mutation
    (142 in 2.8 h) with a 5-minute baseline: the sample fits the night, every PR gets its
    share (at least one, at most K), and the rest is dropped — not owed."""
    muts, origin, blamed = [], {}, {}
    for p in range(15):
        for i in range(76 if p < 5 else 77):
            m = _m(f"p{p}.py", i + 1, ("if-false", "compare-flip", "return-code")[i % 3])
            muts.append(m)
            origin[(m.file, m.line)] = f"sha{p}"
            blamed.setdefault(m.file, set()).add(m.line)
    assert len(muts) == 1150
    cap = sweep_nightly.capacity(sweep.SweepBudget(seconds=10800.0, max_mutations=400),
                                 71.0, 300.0)
    assert cap == 147
    out, counts = sweep_nightly.sample_order(muts, [], blamed, origin, k=20, cap=cap)
    per_pr = {f"p{p}.py": sum(1 for m in out if m.file == f"p{p}.py") for p in range(15)}
    assert len(out) == 147 and counts["dropped"] == 1150 - 147
    assert all(9 <= n <= 10 for n in per_pr.values())
    # Without a pace the K-per-PR rule alone holds: min(K, count) each.
    out, counts = sweep_nightly.sample_order(muts, [], blamed, origin, k=20, cap=None)
    assert len(out) == 300 and counts["dropped"] == 850


@pytest.mark.parametrize("raw, want", [(None, 20), (5, 5), (0, 20), (-1, 20), (True, 20),
                                       ("7", 20), (2.5, 20)])
def test_per_pr_sample_is_a_positive_whole_number_or_the_default(tmp_path, raw, want):
    extra = "" if raw is None else f"sweep:\n  per_pr_sample: {json.dumps(raw)}\n"
    assert sweep_nightly.per_pr_sample(_wf(tmp_path, extra)) == want


def test_trim_owed_logs_each_entry_it_drops(caplog):
    e = {"file": "a.py", "line": 3, "kind": "if-false", "before": "b", "after": "a", "pr": ""}
    with caplog.at_level(logging.INFO, logger="chela.sweep_nightly"):
        sweep_nightly.trim_owed([dict(e, line=1), e], 1)
    assert "over the per-PR share: a.py:3 if-false (PR unknown)" in caplog.text
    assert "a.py:1" not in caplog.text


def test_trim_owed_keeps_k_per_pr_stratified_oldest_first_and_counts_the_rest():
    def e(file, line, kind, pr):
        return {"file": file, "line": line, "kind": kind, "before": "b", "after": "a",
                "pr": pr}
    owed = [e("a.py", 1, "if-false", "P1"), e("a.py", 2, "if-false", "P1"),
            e("a.py", 3, "compare-flip", "P1"), e("b.py", 1, "if-false", "P2"),
            e("c.py", 1, "if-false", ""), e("c.py", 2, "if-false", ""),
            e("d.py", 1, "if-false", "")]
    kept, dropped = sweep_nightly.trim_owed(owed, 2)
    # P1: one if-false and its compare-flip; P2 whole; no PR: by file, c.py keeps 2, d.py 1.
    assert kept == [owed[0], owed[2], owed[3], owed[4], owed[5], owed[6]]
    assert dropped == 1
    assert sweep_nightly.trim_owed(owed, 1) == ([owed[0], owed[3], owed[4], owed[6]], 3)


# --- the pace ----------------------------------------------------------------------------


def test_capacity_is_none_without_a_pace_and_the_seconds_over_the_median_with_one():
    b = sweep.SweepBudget(seconds=1000.0, max_mutations=3)
    assert sweep_nightly.capacity(b, None) is None
    assert sweep_nightly.capacity(b, 0.0) is None
    assert sweep_nightly.capacity(b, 100.0, 150.0) == 8
    assert sweep_nightly.capacity(b, 100.0) == 10
    assert sweep_nightly.capacity(b, 100.0, 5000.0) == 0


def _report(*pairs, seconds=0.0, battery=0.0):
    return sweep.SweepReport(
        results=[sweep.SweepResult(_m(line=i + 1), st, seconds=s)
                 for i, (st, s) in enumerate(pairs)],
        seconds=seconds, battery_seconds=battery)


def test_record_pace_keeps_measured_seconds_and_overhead_and_pace_gives_medians(tmp_path):
    wf, chela = _wf(tmp_path), tmp_path / "chela"
    assert sweep_nightly.pace(wf, chela) == (None, 0.0)
    assert sweep_nightly.min_start_seconds(wf, chela) == 300.0
    sweep_nightly.record_pace(wf, _report((judge.KILLED, 60.0), (judge.SURVIVED, 80.0),
                                          (sweep.UNRUN, 0.0), (judge.KILLED, 0.0),
                                          seconds=440.0, battery=140.0), chela)
    rec = sweep_nightly.read_pace(wf, chela)
    assert rec == {"mutation": [60.0, 80.0], "overhead": [300.0]}
    sweep_nightly.record_pace(wf, _report((judge.KILLED, 100.0), seconds=200.0,
                                          battery=100.0), chela)
    assert sweep_nightly.pace(wf, chela) == (80.0, 100.0)
    assert sweep_nightly.min_start_seconds(wf, chela) == 180.0


def test_a_run_that_measured_nothing_adds_no_pace(tmp_path):
    wf, chela = _wf(tmp_path), tmp_path / "chela"
    sweep_nightly.record_pace(wf, _report((sweep.UNRUN, 0.0), seconds=480.0), chela)
    assert not (sweep_nightly.state_dir(chela) / "pace.json").exists()


def test_the_pace_keeps_only_its_newest(tmp_path, monkeypatch):
    wf, chela = _wf(tmp_path), tmp_path / "chela"
    monkeypatch.setattr(sweep_nightly, "PACE_KEEP_MUTATIONS", 3)
    monkeypatch.setattr(sweep_nightly, "PACE_KEEP_RUNS", 2)
    for n in range(4):
        sweep_nightly.record_pace(wf, _report((judge.KILLED, 10.0 + n), (judge.KILLED, 20.0 + n),
                                              seconds=100.0 + n, battery=0.0), chela)
    assert sweep_nightly.read_pace(wf, chela) == {"mutation": [22.0, 13.0, 23.0],
                                                  "overhead": [102.0, 103.0]}


@pytest.mark.parametrize("body", ["{x", "[]", '{"W": {"mutation": [1, "a"]}}',
                                  '{"W": {"overhead": [-1]}}', '{"W": [1]}',
                                  '{"W": {"mutation": [true]}}'])
def test_an_unreadable_pace_is_unknown_and_recording_starts_it_over(tmp_path, body):
    wf, chela = _wf(tmp_path), tmp_path / "chela"
    path = sweep_nightly.state_dir(chela) / "pace.json"
    path.parent.mkdir(parents=True)
    path.write_text(body.replace('"W"', json.dumps(str(wf.path))))
    assert sweep_nightly.pace(wf, chela) == (None, 0.0)
    assert sweep_nightly.min_start_seconds(wf, chela) == 300.0
    sweep_nightly.record_pace(wf, _report((judge.KILLED, 5.0), seconds=9.0, battery=5.0), chela)
    assert sweep_nightly.read_pace(wf, chela) == {"mutation": [5.0], "overhead": [4.0]}


def test_record_pace_never_raises(tmp_path, monkeypatch):
    wf, chela = _wf(tmp_path), tmp_path / "chela"
    monkeypatch.setattr(sweep_nightly, "_write_json",
                        lambda *a: (_ for _ in ()).throw(RuntimeError("disk")))
    sweep_nightly.record_pace(wf, _report((judge.KILLED, 5.0)), chela)


def test_the_battery_times_each_mutation_it_runs_and_none_it_never_reached(tmp_path,
                                                                            monkeypatch):
    clock = iter(range(0, 10_000, 7))
    monkeypatch.setattr(sweep, "_now", lambda: float(next(clock)))
    muts = [_m(line=1), _m(line=2)]

    def one(worktree, test_cmd, mut, baseline, timeout, selector):
        return sweep.SweepResult(mut, judge.KILLED), ""
    monkeypatch.setattr(sweep, "_run_one", one)
    monkeypatch.setattr(sweep, "collect_mutations", lambda *a: (muts, ""))
    monkeypatch.setattr(sweep, "recover", lambda w: ("", ""))
    monkeypatch.setattr(sweep, "_start_guardian", lambda w: None)
    monkeypatch.setattr(judge, "run_baseline", lambda *a, **k: (
        judge.SuiteResult(ok=True, exit_code=0, passed=1, failed=0, errors=0, tail=""),
        None, ""))
    report = sweep._sweep(tmp_path, "b", "cmd", timeout=1, select_tests=False, progress=None,
                          budget=sweep.SweepBudget(seconds=1e9, max_mutations=1),
                          started=0.0, provision=False, lines={"a.py": {1, 2}})
    ran, unrun = report.results
    assert (ran.status, ran.seconds) == (judge.KILLED, 7.0)
    assert (unrun.status, unrun.seconds) == (sweep.UNRUN, 0.0)


# --- no start without one mutation's time ------------------------------------------------


@pytest.mark.parametrize("trigger", ["idle", "fallback"])
def test_a_daemon_start_with_less_than_one_mutation_left_is_budget_spent_and_quiet(
        tmp_path, monkeypatch, notices, trigger):
    repo = _repo(tmp_path, extra="sweep:\n  budget_seconds: 3600\n")
    chela = tmp_path / "chela"
    wf = load_workflow(repo / "WORKFLOW.md")
    _pace(chela, wf, [100.0], [400.0])                       # one mutation needs 500 s
    sweep_nightly.mark_day_started(wf, datetime.now(), trigger, chela)
    sweep_nightly.finish_day(wf, {"at": time.time(), "status": "unrun", "yielded_to": "x",
                                  "sweep_seconds": 3120.0}, chela)        # 480 s left
    sweep_nightly.mark_day_started(wf, datetime.now(), trigger, chela)
    monkeypatch.setattr(sweep_nightly.sweep, "sweep", lambda *a, **k: pytest.fail("swept"))
    res = _run(repo, chela, Filer(), trigger=trigger)
    assert (res["status"], res["reason"]) == ("skipped", "budget_spent")
    assert res["why"] == "8 min of today's budget left, under one mutation's 8 min"
    assert notices == []
    day = sweep_nightly.read_day(wf, datetime.now().date().isoformat(), chela)
    assert (day["done"], day["pending"], day["used"]) == (True, False, 3120.0)


def test_a_daemon_start_with_one_mutation_left_runs(tmp_path, monkeypatch):
    repo = _repo(tmp_path, extra="sweep:\n  budget_seconds: 3600\n")
    chela = tmp_path / "chela"
    wf = load_workflow(repo / "WORKFLOW.md")
    _pace(chela, wf, [100.0], [400.0])
    sweep_nightly.mark_day_started(wf, datetime.now(), "idle", chela)
    sweep_nightly.finish_day(wf, {"at": time.time(), "status": "unrun", "yielded_to": "x",
                                  "sweep_seconds": 3100.0}, chela)        # 500 s left
    sweep_nightly.mark_day_started(wf, datetime.now(), "idle", chela)
    seen = {}

    def fake(worktree, base, test_cmd, **kw):
        seen.update(kw)
        return sweep.SweepReport(results=[sweep.SweepResult(WEAK_MUT, judge.KILLED)])
    monkeypatch.setattr(sweep_nightly.sweep, "sweep", fake)
    res = _run(repo, chela, Filer(), trigger="idle")
    assert seen["budget"].seconds == 500.0 and res["status"] != "skipped"


@pytest.mark.parametrize("trigger", [None, "fixed"])
def test_a_hand_or_fixed_run_is_never_held_to_the_minimum(tmp_path, monkeypatch, trigger):
    repo = _repo(tmp_path, extra="sweep:\n  budget_seconds: 60\n  blocked_hours: off\n")
    chela = tmp_path / "chela"
    wf = load_workflow(repo / "WORKFLOW.md")
    _pace(chela, wf, [100.0], [400.0])
    monkeypatch.setattr(sweep_nightly.sweep, "sweep", lambda *a, **k: sweep.SweepReport(
        results=[sweep.SweepResult(WEAK_MUT, judge.KILLED)]))
    assert _run(repo, chela, Filer(), trigger=trigger)["status"] != "skipped"


def test_a_budget_spent_skip_is_quiet():
    assert sweep_nightly.quiet({"status": "skipped", "reason": "budget_spent"})
    assert not sweep_nightly.quiet({"status": "skipped", "reason": "fleet_paused"})


@pytest.mark.parametrize("used, want", [(3120.0, None), (3100.0, "idle")])
def test_the_daemon_starts_nothing_with_less_than_one_mutation_left(tmp_path, used, want):
    wf = _wf(tmp_path, extra="sweep:\n  budget_seconds: 3600\n  blocked_hours: off\n")
    chela = tmp_path / "chela"
    _pace(chela, wf, [100.0], [400.0])
    sweep_nightly.mark_day_started(wf, NIGHT, "idle", chela)
    sweep_nightly.finish_day(wf, {"at": NIGHT.timestamp(), "status": "unrun",
                                  "yielded_to": "x", "sweep_seconds": used}, chela)
    assert sweep_nightly.start_reason(wf, NIGHT, 3600.0, chela) == want


# --- through the real night ----------------------------------------------------------------


def test_a_night_measures_k_per_pr_and_drops_the_rest_unowed(tmp_path, notices):
    repo = _repo(tmp_path, extra="sweep:\n  per_pr_sample: 1\n")
    chela = tmp_path / "chela"
    res = _run(repo, chela, Filer())
    assert (res["mutations"], res["unrun"], res["owed"]) == (2, 0, 0)
    assert (res["sampled"], res["sample_dropped"], res["sample_prs"], res["per_pr"]) == (
        2, 2, 2, 1)
    assert "sampled 2 of 4 new (≤1/PR), 2 dropped" in res["summary"]
    pace = sweep_nightly.read_pace(load_workflow(repo / "WORKFLOW.md"), chela)
    assert len(pace["mutation"]) == 2 and len(pace["overhead"]) == 1


def test_a_night_sizes_its_sample_by_the_measured_pace(tmp_path):
    repo = _repo(tmp_path, extra="sweep:\n  budget_seconds: 1000\n")
    chela = tmp_path / "chela"
    _pace(chela, load_workflow(repo / "WORKFLOW.md"), [400.0], [100.0])     # fits 2
    res = _run(repo, chela, Filer())
    assert (res["mutations"], res["sampled"], res["sample_dropped"]) == (2, 2, 2)


def test_a_night_holds_the_owed_backlog_to_k_per_pr(tmp_path):
    repo = _repo(tmp_path, extra="sweep:\n  per_pr_sample: 1\n")
    chela = tmp_path / "chela"
    wf = load_workflow(repo / "WORKFLOW.md")
    entry = {"file": "other.py", "kind": "if-false", "before": "    if x > 100:",
             "after": "    if False and (x > 100):", "pr": "`abc` PR 2"}
    owed = [{**entry, "line": 2}, {**entry, "line": 2, "kind": "compare-flip",
                                   "after": "    if x <= 100:"}]
    sweep_nightly.write_owed(wf, owed, chela)
    res = _run(repo, chela, Filer())
    assert (res["owed_carried"], res["owed_trimmed"], res["owed_ran"]) == (2, 1, 1)
    assert "2 owed from earlier nights (1 ran; 1 over ≤1/PR dropped)" in res["summary"]


# --- the notice --------------------------------------------------------------------------


def _night_1121(**kw) -> dict:
    """The 11-21 00:00 night, as a result: 3 issues, 49 lines gone, owed grown."""
    res = {"workflow": "/x/chelamux/WORKFLOW.md", "at": datetime(2026, 11, 21).timestamp(),
           "status": "survivors", "survivors": 5, "groups": [{}] * 5,
           "filed": ["CMX-460", "CMX-461", "CMX-462"], "would_file": [], "already_filed": 0,
           "failed": [], "appended": [], "warnings": [], "trigger": "idle",
           "issues": [{"issue": f"CMX-46{i}", "survivors": n, "appended": False}
                      for i, n in enumerate((2, 2, 1))],
           "prs": ["p"] * 15, "mutations": 160, "unrun": 18, "measured": 142,
           "battery_seconds": 10080.0, "per_hour": 50.7, "sampled": 147,
           "sample_dropped": 998, "per_pr": 20, "owed_carried": 620, "owed_ran": 93,
           "owed_trimmed": 0, "owed": 25, "owed_delta": -595, "owed_since": "2026-11-20",
           "owed_dropped": [f"chela/x{i}.py:{i} if-false — line_gone" for i in range(49)],
           "sweep_seconds": 10200.0, "budget_seconds": 10800.0}
    res.update(kw)
    return res


def test_the_11_21_notice_counts_the_dropped_lines_and_stays_under_the_cap():
    line = sweep_nightly.summary(_night_1121())
    # Over the cap only by the rate: that part alone is left out.
    assert "mutations/hour" not in line and "170 min used of 180" in line
    assert "620 owed from earlier nights (93 ran; 49 dropped (line gone))" in line
    assert "x0.py" not in line and "line_gone" not in line
    assert "sampled 147 of 1145 new (≤20/PR), 998 dropped" in line
    assert len(line) <= 400


def test_the_notice_cuts_long_free_text_and_caps_its_length():
    long = "w" * 500
    line = sweep_nightly.summary(_night_1121(warnings=[long], failed=[long]))
    assert len(line) == 400 and line.endswith("…")
    assert "w" * 80 not in line and "w" * 79 + "…" in line


def test_an_over_long_notice_leaves_out_whole_low_value_parts_before_it_cuts():
    """The rate and the minutes go; the owed count, its growth alert and the warning stay."""
    res = _night_1121(owed_growth_days=3, owed_trail=[620, 1007, 1100, 1200],
                      warnings=["the owed history could not be kept"],
                      prs=["p"] * 15, owed_delta=100)
    full = sweep_nightly._summary(res)
    assert len(full) > 400
    line = sweep_nightly.summary(res)
    assert len(line) <= 400 and not line.endswith("…")
    assert "mutations/hour" not in line and "min used" not in line
    # Still over after that: the same counts in fewer words.
    assert "filed CMX-460 (2), CMX-461 (2), CMX-462 (1)" in line and "15 PR(s)" in line
    assert "620 owed carried (93 ran; 49 dropped (line gone))" in line
    assert "25 owed next" in line and "owed has grown 3 days running: 620 → 1200" in line
    assert "warning(s): the owed history could not be kept" in line


def test_a_cannot_verify_notice_keeps_its_whole_why():
    why = "the suite is NOT GREEN — " + "t" * 600
    line = sweep_nightly.summary(_night_1121(status="cannot_verify", why=why))
    assert why in line


@pytest.mark.parametrize("text, limit, want", [("abc", 3, "abc"), ("abcd", 3, "ab…"),
                                               (12345, 4, "123…"), ("", 1, "")])
def test_clip(text, limit, want):
    assert sweep_nightly.clip(text, limit) == want


def test_each_dropped_line_goes_to_the_log(tmp_path, caplog):
    from tests.test_sweep_nightly_owed import _night_one
    repo, chela, res1 = _night_one(tmp_path)
    (repo / "size.py").unlink()
    from tests.test_sweep_nightly import _commit
    _commit(repo, {}, "rm size.py")
    with caplog.at_level(logging.INFO, logger="chela.sweep_nightly"):
        res = _run(repo, chela, Filer(), now=res1["at"] + 2 * 24 * 3600)
    assert res["owed_dropped"] == ["size.py:2 if-false — line_gone"]
    assert "owed mutation dropped: size.py:2 if-false — line_gone" in caplog.text
    assert "1 dropped (line gone)" in res["summary"] and "size.py:2" not in res["summary"]
