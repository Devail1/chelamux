"""⚖️🧹🌙 CMX-374 — an UNRUN mutation stays OWED until something runs it.

The budget is spent in the same order every night, so the mutations it never reached would hit
the same wall again — and once the window moved past their PR, nothing would sweep them. Every
test runs against the throwaway repo of :mod:`tests.test_sweep_nightly` (``origin`` a local
bare repo) and a temp ``CHELA_DIR``: never the live state dir.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import pytest

from chela import judge, sweep, sweep_nightly
from chela.workflow import load_workflow
from tests.test_sweep_nightly import (WEAK_MUT, Filer, _commit, _fake_battery, _repo, _run,
                                      _workflow_text)

DAY = 24 * 3600
# The four mutations of the default fixture, in the sweep's own priority order: with a
# one-mutation budget the first runs and the other three are UNRUN.
FIRST = ("other.py", 2, "if-false")


@pytest.fixture(autouse=True)
def notices(monkeypatch):
    """Every inbox notice a run queues, ``(kind, summary, payload)`` — none reaches a store."""
    got: list[tuple[str, str, dict]] = []
    monkeypatch.setattr("chela.inbox.enqueue",
                        lambda kind, summary, payload: got.append((kind, summary, payload)))
    return got


def _budget(repo: Path, tmp_path: Path, n: int) -> None:
    """The next night's ``sweep.max_mutations`` (the run reads the local WORKFLOW.md)."""
    (repo / "WORKFLOW.md").write_text(_workflow_text(tmp_path,
                                                     extra=f"sweep:\n  max_mutations: {n}\n"))


def _owed(chela: Path, repo: Path) -> list[dict]:
    p = sweep_nightly.state_dir(chela) / "owed.json"
    data = json.loads(p.read_text()) if p.exists() else {}
    return data.get(str((repo / "WORKFLOW.md").resolve()), [])


def _ids(entries) -> set[tuple]:
    return {(e["file"], e["line"], e["kind"]) for e in entries}


def _dropped_log(chela: Path) -> list[dict]:
    p = sweep_nightly.state_dir(chela) / "owed-dropped.jsonl"
    return [json.loads(x) for x in p.read_text().splitlines()] if p.exists() else []


def _night_one(tmp_path: Path) -> tuple[Path, Path, dict]:
    repo = _repo(tmp_path, extra="sweep:\n  max_mutations: 1\n")
    chela = tmp_path / "chela"
    res = _run(repo, chela, Filer())
    assert (res["mutations"], res["unrun"]) == (4, 3)
    return repo, chela, res


# --- the acceptance -----------------------------------------------------------------------


def test_three_mutations_over_budget_run_first_the_next_night(tmp_path):
    """Night 1 leaves 3 UNRUN; night 2 (budget 3) runs exactly those 3 — not the one night 1
    already measured, which the sweep's own order would run first again (negative control:
    without the carry-over, night 2 re-runs FIRST and one owed mutation is UNRUN again)."""
    repo, chela, res1 = _night_one(tmp_path)
    owed1 = _owed(chela, repo)
    assert len(owed1) == 3 and FIRST not in _ids(owed1)
    assert res1["owed_new"] == 3 and res1["owed_carried"] == 0
    assert "3 newly owed" in res1["summary"]

    _budget(repo, tmp_path, 3)
    res2 = _run(repo, chela, Filer())
    assert res2["owed_carried"] == 3
    assert len(res2["owed_ran"]) == 3 and res2["owed_dropped"] == []
    assert res2["unrun_sites"] == [f"{FIRST[0]}:{FIRST[1]}"]
    # The one night 1 measured is now the UNRUN one — owed in turn, the rest settled.
    assert _ids(_owed(chela, repo)) == {FIRST}
    assert "3 owed from earlier nights: 3 ran, 0 still owed" in res2["summary"]
    ran = {d["kind"] for d in _dropped_log(chela)}
    assert ran and {d["why"] for d in _dropped_log(chela)} == {"ran"}


def test_an_owed_entry_records_where_it_came_from(tmp_path):
    repo, chela, _ = _night_one(tmp_path)
    by_file = {e["file"]: e for e in _owed(chela, repo)}
    assert by_file["size.py"] == {
        "file": "size.py", "line": 2, "kind": "if-false", "before": "    if x:",
        "after": "    if False and (x):", "since": by_file["size.py"]["since"],
        "pr": by_file["size.py"]["pr"]}
    assert by_file["size.py"]["pr"].endswith(" PR 1: is_big (#1)")
    assert by_file["other.py"]["pr"].endswith(" PR 2: big_enough (#2)")


def test_a_deleted_line_drops_out_of_the_owed_set_and_the_report_says_so(tmp_path):
    repo, chela, _ = _night_one(tmp_path)
    assert ("size.py", 2, "if-false") in _ids(_owed(chela, repo))
    (repo / "size.py").write_text('def is_big(x):\n    return "small"\n')
    _commit(repo, {}, "PR 3: is_big goes (#3)")
    _budget(repo, tmp_path, 60)
    res = _run(repo, chela, Filer())
    assert ("size.py", 2, "if-false") not in _ids(_owed(chela, repo))
    gone = "size.py:2 if-false — its line is no longer on origin/dev"
    assert res["owed_dropped"] == [gone]
    assert f"1 dropped ({gone})" in res["summary"]
    log = [d for d in _dropped_log(chela) if d["file"] == "size.py"]
    assert [(d["why"], d["kind"]) for d in log] == [("line_gone", "if-false")]


def test_a_night_with_no_new_merge_still_runs_what_is_owed_and_says_so(tmp_path, notices):
    """CMX-371's quiet night is a night with NOTHING to sweep: owed mutations are work."""
    repo, chela, _ = _night_one(tmp_path)
    _budget(repo, tmp_path, 60)
    res = _run(repo, chela, Filer(), now=time.time() + 3 * DAY)
    assert res["prs"] == [] and res["status"] != "skipped"
    # Exactly the owed three: the measured mutation sharing an owed line is not swept again.
    assert (res["mutations"], res["unrun"], len(res["owed_ran"])) == (3, 0, 3)
    assert _owed(chela, repo) == []
    assert len(notices) == 2 and "3 owed from earlier nights" in notices[-1][1]


def test_a_night_with_no_merge_and_nothing_owed_stays_quiet(tmp_path, notices):
    repo, chela = _repo(tmp_path, extra="sweep:\n  max_mutations: 4\n"), tmp_path / "chela"
    _run(repo, chela, Filer())
    res = _run(repo, chela, Filer(), now=time.time() + 3 * DAY)
    assert (res["status"], res["reason"]) == ("skipped", "no_merges")
    assert len(notices) == 1


def test_an_owed_survivor_is_filed_under_the_pr_it_came_from(tmp_path):
    repo, chela, _ = _night_one(tmp_path)
    _budget(repo, tmp_path, 60)
    filer = Filer()
    res = _run(repo, chela, filer, now=time.time() + 3 * DAY)
    assert filer.sites == ["size.py:2"] and res["filed"] == ["CMX-901"]
    assert filer.calls[0][1].merged_in.endswith(" PR 1: is_big (#1)")


# --- the store fails closed -----------------------------------------------------------------


def _seed(chela: Path, repo: Path, entries) -> Path:
    p = sweep_nightly.state_dir(chela) / "owed.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({str((repo / "WORKFLOW.md").resolve()): entries}))
    return p


def _entry(**kw) -> dict:
    return {"file": "size.py", "line": 2, "kind": "if-false", "before": "    if x:",
            "after": "    if False and (x):", "pr": "", "since": 0.0, **kw}


@pytest.mark.parametrize("raw", ["{not json", "[1]"])
def test_an_unreadable_owed_store_is_cannot_verify_and_left_alone(tmp_path, raw):
    repo, chela = _repo(tmp_path), tmp_path / "chela"
    p = sweep_nightly.state_dir(chela) / "owed.json"
    p.parent.mkdir(parents=True)
    p.write_text(raw)
    res = _run(repo, chela, Filer())
    assert res["status"] == "cannot_verify" and "owed-mutation store" in res["why"]
    assert p.read_text() == raw


@pytest.mark.parametrize("bad", [
    "not a list", [{"file": "size.py"}], [_entry(line=True)], [_entry(line=0)],
    [_entry(kind=3)], [_entry(pr=None)], ["x"]])
def test_a_malformed_owed_entry_is_cannot_verify(tmp_path, bad):
    repo, chela = _repo(tmp_path), tmp_path / "chela"
    p = _seed(chela, repo, bad)
    before = p.read_text()
    res = _run(repo, chela, Filer())
    assert res["status"] == "cannot_verify" and "malformed owed list" in res["why"]
    assert p.read_text() == before


def test_a_well_formed_entry_reads_back(tmp_path):
    repo, chela = _repo(tmp_path), tmp_path / "chela"
    _seed(chela, repo, [_entry()])
    assert sweep_nightly.read_owed(load_workflow(repo / "WORKFLOW.md"), chela) == [_entry()]


def test_an_owed_file_that_cannot_be_read_is_cannot_verify_not_gone(tmp_path):
    repo, chela = _repo(tmp_path), tmp_path / "chela"
    p = _seed(chela, repo, [_entry(file="tests")])       # a directory: read_text raises
    before = p.read_text()
    res = _run(repo, chela, Filer())
    assert res["status"] == "cannot_verify" and "could not read tests" in res["why"]
    assert p.read_text() == before


def test_a_battery_that_cannot_verify_settles_nothing(tmp_path, monkeypatch):
    repo, chela = _repo(tmp_path), tmp_path / "chela"
    p = _seed(chela, repo, [_entry()])
    before = p.read_text()
    monkeypatch.setattr(sweep_nightly.sweep, "sweep",
                        lambda *a, **k: sweep.SweepReport(cannot_verify="red baseline"))
    res = _run(repo, chela, Filer())
    assert res["status"] == "cannot_verify" and p.read_text() == before
    assert "1 owed from earlier nights, all still owed" in res["summary"]


def test_an_owed_survivor_whose_filing_failed_stays_owed(tmp_path, monkeypatch):
    repo, chela = _repo(tmp_path), tmp_path / "chela"
    _seed(chela, repo, [_entry()])
    _fake_battery(monkeypatch, (WEAK_MUT, judge.SURVIVED))
    res = _run(repo, chela, Filer(fail=RuntimeError("tracker down")))
    assert res["failed"] and _ids(_owed(chela, repo)) == {("size.py", 2, "if-false")}


def test_an_owed_survivor_whose_filing_was_refused_stays_owed(tmp_path, monkeypatch):
    """The filed-key store is unreadable, so ``_file`` refuses before calling the filer: the
    night is CANNOT VERIFY with ``failed`` EMPTY — the status alone says it was not filed. The
    owed survivor must stay owed; settling it as ``ran`` would lose it for good."""
    repo, chela = _repo(tmp_path), tmp_path / "chela"
    _seed(chela, repo, [_entry()])
    (sweep_nightly.state_dir(chela) / "filed.json").write_text("{torn")
    _fake_battery(monkeypatch, (WEAK_MUT, judge.SURVIVED))
    filer = Filer()
    res = _run(repo, chela, filer)
    assert res["status"] == "cannot_verify" and "filed-key store" in res["why"]
    assert res["failed"] == [] and filer.sites == []      # the status is the only signal
    assert _ids(_owed(chela, repo)) == {("size.py", 2, "if-false")}
    assert [d for d in _dropped_log(chela) if d["why"] == "ran"] == []


def test_an_owed_survivor_that_was_filed_is_settled(tmp_path, monkeypatch):
    repo, chela = _repo(tmp_path), tmp_path / "chela"
    _seed(chela, repo, [_entry()])
    _fake_battery(monkeypatch, (WEAK_MUT, judge.SURVIVED))
    res = _run(repo, chela, Filer())
    assert res["filed"] == ["CMX-901"] and _owed(chela, repo) == []


# --- the parts ------------------------------------------------------------------------------


def _wf(tmp_path: Path):
    (tmp_path / "WORKFLOW.md").write_text(_workflow_text(tmp_path))
    return load_workflow(tmp_path / "WORKFLOW.md")


@pytest.mark.parametrize("status, kept, why", [
    (judge.KILLED, False, "ran"), (judge.SURVIVED, False, "ran"),
    (sweep.UNRUN, True, None), (sweep.TIMEOUT, True, None), (judge.INVALID, True, None),
    ("SOMETHING_NEW", True, None),
    (sweep.UNPARSEABLE, False, "not_mutatable"), (sweep.NOT_APPLIED, False, "not_mutatable"),
    (None, False, "not_mutatable")])
def test_settle_by_status(tmp_path, status, kept, why):
    wf, chela = _wf(tmp_path), tmp_path / "chela"
    e = _entry(line=9, pr="`abc` PR 1")
    results = [] if status is None else [sweep.SweepResult(
        sweep.Mutation("size.py", 2, "if-false", e["before"], e["after"]), status)]
    result = {"at": 5.0}
    sweep_nightly._settle_owed(wf, sweep.SweepReport(results=results), [(e, 2)], [], {}, {},
                               chela, result)
    owed = sweep_nightly.read_owed(wf, chela)
    assert owed == ([{**e, "line": 2}] if kept else [])
    assert [d["why"] for d in _dropped_log(chela)] == ([] if kept else [why])
    # Nothing dropped writes nothing — not even an empty log.
    assert (sweep_nightly.state_dir(chela) / "owed-dropped.jsonl").exists() is not kept
    assert result["owed_left"] == int(kept) and result["owed_new"] == 0
    assert len(result["owed_ran"]) == (why == "ran")
    assert len(result["owed_dropped"]) == (why == "not_mutatable")


def test_settle_keeps_a_survivor_while_its_filing_failed(tmp_path):
    wf, chela = _wf(tmp_path), tmp_path / "chela"
    e = _entry()
    rep = sweep.SweepReport(results=[sweep.SweepResult(WEAK_MUT, judge.SURVIVED)])
    sweep_nightly._settle_owed(wf, rep, [(e, 2)], [], {}, {}, chela, {"at": 1.0},
                               keep_survivors=True)
    assert sweep_nightly.read_owed(wf, chela) == [e]
    rep = sweep.SweepReport(results=[sweep.SweepResult(WEAK_MUT, judge.KILLED)])
    sweep_nightly._settle_owed(wf, rep, [(e, 2)], [], {}, {}, chela, {"at": 1.0},
                               keep_survivors=True)
    assert sweep_nightly.read_owed(wf, chela) == []


def test_settle_adds_tonights_unrun_once_with_its_pr_after_the_kept(tmp_path):
    wf, chela = _wf(tmp_path), tmp_path / "chela"
    kept = _entry(file="a.py", line=1, before="if a:", after="if False and (a):")
    late = sweep.Mutation("b.py", 4, "return-code", "    return 1", "    return 0")
    rep = sweep.SweepReport(results=[
        sweep.SweepResult(sweep.Mutation("a.py", 1, "if-false", "if a:", "if False and (a):"),
                          sweep.UNRUN),
        sweep.SweepResult(late, sweep.UNRUN), sweep.SweepResult(late, sweep.UNRUN),
        sweep.SweepResult(WEAK_MUT, judge.KILLED)])
    result = {"at": 7.0}
    sweep_nightly._settle_owed(wf, rep, [(kept, 1)], [], {("b.py", 4): "s1"},
                               {"s1": "`s1` PR 9"}, chela, result)
    assert sweep_nightly.read_owed(wf, chela) == [kept, {
        "file": "b.py", "line": 4, "kind": "return-code", "before": "    return 1",
        "after": "    return 0", "since": 7.0, "pr": "`s1` PR 9"}]
    assert (result["owed_left"], result["owed_new"], result["owed"]) == (1, 1, 2)


def test_write_owed_keeps_other_workflows_and_clears_an_empty_list(tmp_path):
    wf, chela = _wf(tmp_path), tmp_path / "chela"
    p = sweep_nightly.state_dir(chela) / "owed.json"
    p.parent.mkdir(parents=True)
    p.write_text(json.dumps({"/other/WORKFLOW.md": [_entry()]}))
    sweep_nightly.write_owed(wf, [_entry(line=3)], chela)
    assert json.loads(p.read_text()) == {"/other/WORKFLOW.md": [_entry()],
                                         str(wf.path): [_entry(line=3)]}
    sweep_nightly.write_owed(wf, [], chela)
    assert json.loads(p.read_text()) == {"/other/WORKFLOW.md": [_entry()]}


def test_locate_owed(tmp_path):
    (tmp_path / "a.py").write_text("x = 1\n    if x:\nz = 3\n    if x:\n    if x:\n")
    e = _entry(file="a.py", line=4)
    loc = sweep_nightly.locate_owed
    assert loc(tmp_path, e, set()) == 4                          # unchanged
    assert loc(tmp_path, _entry(file="a.py", line=1), set()) == 2   # shifted: the nearest
    assert loc(tmp_path, _entry(file="a.py", line=9), set()) == 5
    taken = {("a.py", 4, e["kind"], e["after"])}
    assert loc(tmp_path, e, taken) == 5                          # the nearest free one
    assert loc(tmp_path, _entry(file="a.py", before="    if y:"), set()) is None
    assert loc(tmp_path, _entry(file="gone.py"), set()) is None
    assert loc(tmp_path, _entry(file="a.py/x"), set()) is None   # a parent that is a file
    (tmp_path / "d").mkdir()
    with pytest.raises(OSError):
        loc(tmp_path, _entry(file="d"), set())
    (tmp_path / "bin.py").write_bytes(b"\xff\xfe\x00bad")
    with pytest.raises(UnicodeDecodeError):
        loc(tmp_path, _entry(file="bin.py"), set())


def test_locate_owed_counts_lines_as_the_mutators_do(tmp_path):
    """A form feed is a line break to `str.splitlines`, never to the mutators' `split("\\n")`."""
    (tmp_path / "a.py").write_text("x = 1\f\n    if x:\n")
    assert sweep_nightly.locate_owed(tmp_path, _entry(file="a.py", line=2), set()) == 2


def test_owed_first_puts_owed_first_and_drops_the_rest_of_an_owed_only_line():
    def m(f, ln, kind):
        return sweep.Mutation(f, ln, kind, f"b{ln}", f"a{ln}{kind}")
    new1, new2 = m("n.py", 1, "if-false"), m("n.py", 2, "compare-flip")
    owed_a, owed_b = m("o.py", 5, "compare-flip"), m("o.py", 7, "if-false")
    measured = m("o.py", 5, "if-false")              # on an owed line, but not owed
    muts = [new1, measured, owed_b, new2, owed_a]
    order = [sweep_nightly.mutation_id(owed_a), sweep_nightly.mutation_id(owed_b),
             ("o.py", 99, "if-false", "never generated")]
    assert sweep_nightly.owed_first(muts, order, {"n.py": {1, 2}}) == [
        owed_a, owed_b, new1, new2]
    # A line that is both blamed and owed keeps its other mutations.
    assert sweep_nightly.owed_first(muts, order, {"n.py": {1, 2}, "o.py": {5}}) == [
        owed_a, owed_b, new1, measured, new2]


def test_the_battery_gets_the_owed_line_and_measures_owed_first(tmp_path, monkeypatch):
    """The wiring: the owed line is swept, and `order` puts it first — with the blamed lines."""
    repo, chela = _repo(tmp_path), tmp_path / "chela"
    _seed(chela, repo, [_entry(file="calc.py", line=2, before="    if x < lo:",
                               after="    if False and (x < lo):")])
    seen = {}

    def fake(worktree, base, test_cmd, **kw):
        seen.update(kw)
        return sweep.SweepReport()
    monkeypatch.setattr(sweep_nightly.sweep, "sweep", fake)
    _run(repo, chela, Filer())
    assert seen["lines"] == {"size.py": {1, 2, 3, 4}, "other.py": {1, 2, 3, 4},
                             "calc.py": {2}}
    owed = sweep.Mutation("calc.py", 2, "if-false", "    if x < lo:",
                          "    if False and (x < lo):")
    other = sweep.Mutation("calc.py", 2, "compare-flip", "    if x < lo:", "    if x >= lo:")
    new = sweep.Mutation("size.py", 2, "if-false", "    if x:", "    if False and (x):")
    assert seen["order"]([new, other, owed]) == [owed, new]


# --- the notice -----------------------------------------------------------------------------


def _summary(**kw) -> str:
    base = {"workflow": "/p/WORKFLOW.md", "status": "clean", "filed": [], "failed": [],
            "would_file": [], "already_filed": 0, "warnings": [], "survivors": 0, "groups": []}
    return sweep_nightly.summary({**base, **kw})


def test_the_notice_counts_what_is_owed():
    assert _summary() == "🧹🌙 nightly sweep (p): clean — nothing filed"
    assert _summary(owed_carried=3, owed_ran=["a", "b"], owed_left=1, owed_dropped=[]) == (
        "🧹🌙 nightly sweep (p): clean — nothing filed — 3 owed from earlier nights: 2 ran, "
        "1 still owed")
    assert _summary(owed_carried=2, owed_ran=[], owed_left=0,
                    owed_dropped=["x.py:2 if-false — gone", "y.py:3 if-true — gone"]) == (
        "🧹🌙 nightly sweep (p): clean — nothing filed — 2 owed from earlier nights: 0 ran, "
        "0 still owed, 2 dropped (x.py:2 if-false — gone; y.py:3 if-true — gone)")
    assert _summary(status="cannot_verify", why="w", owed_carried=4) == (
        "🧹🌙 nightly sweep (p): CANNOT VERIFY — w — nothing filed — 4 owed from earlier "
        "nights, all still owed (none could run tonight)")
    assert _summary(owed_new=2) == (
        "🧹🌙 nightly sweep (p): clean — nothing filed — 2 newly owed (UNRUN tonight, run "
        "first next night)")


def test_the_sweep_order_hook_reorders_and_drops(tmp_path):
    """`sweep.sweep(order=…)` measures what the hook returns, in its order; the rest of the
    generated mutations are neither run nor UNRUN."""
    repo = _repo(tmp_path)
    picked = []

    def order(muts):
        picked.extend(muts)
        return [m for m in muts if m.file == "size.py"]
    rep = sweep.sweep(repo, "label", f'"{sys.executable}" -m pytest -q -p no:cacheprovider '
                      "tests", select_tests=False, provision=False,
                      lines={"size.py": {2}, "calc.py": {2}}, order=order)
    assert {m.file for m in picked} == {"size.py", "calc.py"}
    assert [(r.mutation.file, r.status) for r in rep.results] == [("size.py", judge.SURVIVED)]
