"""⚖️🧹🌙 CMX-374 — an UNRUN mutation stays OWED until something runs it.

The budget is spent in the same order every night, so the mutations it never reached would hit
the same wall again, and once the window moved past their PR nothing would sweep them. Every
test runs against the throwaway repo of :mod:`tests.test_sweep_nightly` (``origin`` a local
bare repo) and a temp ``CHELA_DIR`` — never the live state dir.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from chela import judge, sweep, sweep_nightly
from tests.test_sweep_nightly import (WEAK_MUT, Filer, _commit, _covered_record, _fake_battery,
                                      _repo, _run, _workflow_text)

DAY = 24 * 3600
# The default fixture's four mutations in the sweep's own order: with a one-mutation budget
# `other.py:2 if-false` runs and these three are UNRUN (and owed).
OWED3 = {("size.py", 2, "if-false"), ("other.py", 3, "return-code"),
         ("other.py", 2, "compare-flip")}
FIRST = ("other.py", 2, "if-false")
PR1 = "PR 1: is_big (#1)"


@pytest.fixture(autouse=True)
def notices(monkeypatch):
    """Every inbox notice a run queues, ``(kind, summary, payload)`` — none reaches a store."""
    got: list[tuple[str, str, dict]] = []
    monkeypatch.setattr("chela.inbox.enqueue",
                        lambda kind, summary, payload: got.append((kind, summary, payload)))
    return got


def _wf_key(repo: Path) -> str:
    return str((repo / "WORKFLOW.md").resolve())


def _owed_store(chela: Path) -> dict:
    p = sweep_nightly.state_dir(chela) / "owed.json"
    return json.loads(p.read_text()) if p.exists() else {}


def _owed(chela: Path, repo: Path) -> list[dict]:
    return _owed_store(chela).get(_wf_key(repo), [])


def _ids(entries) -> set[tuple]:
    return {(e["file"], e["line"], e["kind"]) for e in entries}


def _budget(repo: Path, tmp_path: Path, n: int) -> None:
    """The next night's ``sweep.max_mutations`` (the run reads the local WORKFLOW.md)."""
    (repo / "WORKFLOW.md").write_text(
        _workflow_text(tmp_path, extra=f"sweep:\n  max_mutations: {n}\n"))


def _night_one(tmp_path: Path) -> tuple[Path, Path, dict]:
    repo = _repo(tmp_path, extra="sweep:\n  max_mutations: 1\n")
    chela = tmp_path / "chela"
    res = _run(repo, chela, Filer())
    assert (res["mutations"], res["unrun"], res["owed"]) == (4, 3, 3)
    assert _ids(_owed(chela, repo)) == OWED3
    return repo, chela, res


class _Spy:
    """Wraps the real battery, keeping each report and the kwargs it was called with."""

    def __init__(self, monkeypatch):
        self.real, self.calls = sweep.sweep, []
        monkeypatch.setattr(sweep_nightly.sweep, "sweep", self)

    def __call__(self, *a, **kw):
        rep = self.real(*a, **kw)
        self.calls.append((kw, rep))
        return rep

    def statuses(self, n=-1) -> dict[tuple, str]:
        return {(r.mutation.file, r.mutation.line, r.mutation.kind): r.status
                for r in self.calls[n][1].results}


# --- the acceptance -----------------------------------------------------------------------


def test_night_one_persists_every_unrun_mutation_with_its_pr(tmp_path):
    repo, chela, res = _night_one(tmp_path)
    by_id = {(e["file"], e["line"], e["kind"]): e for e in _owed(chela, repo)}
    size = by_id[("size.py", 2, "if-false")]
    assert (size["before"], size["after"]) == ("    if x:", "    if False and (x):")
    assert size["pr"].endswith(f"` {PR1}") and size["since"] == res["at"]
    assert by_id[("other.py", 3, "return-code")]["pr"].endswith("` PR 2: big_enough (#2)")
    assert res["summary"].endswith(" — 3 owed to the next night")
    assert "owed from earlier nights" not in res["summary"]


def test_three_mutations_over_budget_run_first_the_next_night(tmp_path, monkeypatch):
    """Night 2, same window, budget 3: the three owed run, and the one night 1 measured — which
    the sweep's own order puts first — is the one left UNRUN (and owed in turn)."""
    repo, chela, _ = _night_one(tmp_path)
    _budget(repo, tmp_path, 3)
    spy = _Spy(monkeypatch)
    res = _run(repo, chela, Filer())
    st = spy.statuses()
    assert {k: v for k, v in st.items() if v == sweep.UNRUN} == {FIRST: sweep.UNRUN}
    assert all(st[k] in (judge.KILLED, judge.SURVIVED) for k in OWED3)
    assert (res["owed_carried"], res["owed_ran"], res["owed"]) == (3, 3, 1)
    assert _ids(_owed(chela, repo)) == {FIRST}
    assert " — 3 owed from earlier nights (3 ran) — 1 owed to the next night" in res["summary"]


def test_negative_control_without_the_carry_over_an_owed_one_is_unrun_again(tmp_path,
                                                                            monkeypatch):
    repo, chela, _ = _night_one(tmp_path)
    _budget(repo, tmp_path, 3)
    monkeypatch.setattr(sweep_nightly, "read_owed", lambda wf, d=None: [])
    spy = _Spy(monkeypatch)
    _run(repo, chela, Filer())
    st = spy.statuses()
    assert st[FIRST] != sweep.UNRUN
    assert any(st[k] == sweep.UNRUN for k in OWED3)


def test_a_night_with_no_new_merge_still_runs_the_owed_ones_and_tells_the_inbox(
        tmp_path, monkeypatch, notices):
    """Two days on, nothing merged: the night is NOT the quiet NO_MERGES skip (CMX-371) — it
    runs the owed three and queues its notice. The weak guard among them is filed with the PR
    it was owed from."""
    repo, chela, res1 = _night_one(tmp_path)
    _budget(repo, tmp_path, 60)
    spy = _Spy(monkeypatch)
    filer = Filer()
    notices.clear()
    res = _run(repo, chela, filer, now=res1["at"] + 2 * DAY)
    assert res["prs"] == [] and res["status"] == "survivors"
    assert set(spy.statuses()) == OWED3                 # owed lines only, nothing else
    assert spy.calls[0][0]["lines"] == {"size.py": {2}, "other.py": {2, 3}}
    assert filer.sites == ["size.py:2"]
    assert filer.calls[0][1].merged_in.endswith(f"` {PR1}")
    assert _owed(chela, repo) == [] and res["owed"] == 0
    assert [n[0] for n in notices] == [sweep_nightly.INBOX_KIND]
    assert "3 owed from earlier nights (3 ran)" in notices[0][1]


def test_a_no_merge_night_with_nothing_owed_stays_quiet(tmp_path, notices):
    repo, chela = _repo(tmp_path, prs=[]), tmp_path / "chela"
    sweep_nightly.write_owed(_load(repo), [], chela)      # an empty list is nothing owed
    res = _run(repo, chela, Filer())
    assert (res["status"], res["reason"]) == ("skipped", "no_merges") and notices == []


def _load(repo: Path):
    from chela.workflow import load_workflow
    return load_workflow(repo / "WORKFLOW.md")


def test_a_deleted_line_drops_out_and_the_report_says_so(tmp_path, monkeypatch):
    repo, chela, res1 = _night_one(tmp_path)
    _budget(repo, tmp_path, 60)
    _commit(repo, {"size.py": 'def is_big(x):\n    return "small"\n'}, "drop is_big's guard")
    spy = _Spy(monkeypatch)
    res = _run(repo, chela, Filer(), now=res1["at"] + 2 * DAY)
    assert ("size.py", 2, "if-false") not in spy.statuses()
    assert _owed(chela, repo) == []
    assert res["owed_dropped"] == ["size.py:2 if-false — line_gone"]
    assert ("3 owed from earlier nights (2 ran; 1 dropped, line gone: size.py:2 if-false — "
            "line_gone)") in res["summary"]


def test_a_deleted_file_drops_its_owed_ones(tmp_path):
    repo, chela, res1 = _night_one(tmp_path)
    (repo / "size.py").unlink()
    _commit(repo, {}, "rm size.py")
    res = _run(repo, chela, Filer(), now=res1["at"] + 2 * DAY)
    assert res["owed_dropped"] == ["size.py:2 if-false — line_gone"]


def test_a_shifted_line_is_found_and_run_at_its_new_number(tmp_path, monkeypatch):
    repo, chela, res1 = _night_one(tmp_path)
    _commit(repo, {"size.py": "import os\n\n" + (repo / "size.py").read_text()}, "shift")
    _budget(repo, tmp_path, 60)
    spy = _Spy(monkeypatch)
    # The shift is a new standard merge: its own line 1-2 are swept too, after the owed.
    res = _run(repo, chela, Filer(), now=res1["at"] + 2 * DAY)
    assert spy.statuses()[("size.py", 4, "if-false")] == judge.SURVIVED
    assert res["owed_ran"] == 3 and res["owed_dropped"] == []


def test_the_window_moves_when_every_unrun_one_is_owed(tmp_path):
    repo, chela, res = _night_one(tmp_path)
    assert _covered_record(chela) == {_wf_key(repo): res["at"]}


# --- fail closed --------------------------------------------------------------------------


@pytest.mark.parametrize("content", [
    "not json", "[]",
    '{"%s": {"file": "size.py"}}',
    '{"%s": [{"file": "size.py", "line": 2, "kind": "k", "before": "b", "after": "a"}]}',
    '{"%s": [{"file": "size.py", "line": true, "kind": "k", "before": "b", "after": "a", '
    '"pr": ""}]}',
    '{"%s": [{"file": "size.py", "line": 0, "kind": "k", "before": "b", "after": "a", '
    '"pr": ""}]}',
    '{"%s": [{"file": "size.py", "line": 2, "kind": 3, "before": "b", "after": "a", '
    '"pr": ""}]}',
    '{"%s": ["x"]}',
])
def test_an_unreadable_owed_store_is_cannot_verify_and_left_as_it_was(tmp_path, monkeypatch,
                                                                       content):
    repo, chela = _repo(tmp_path), tmp_path / "chela"
    path = sweep_nightly.state_dir(chela) / "owed.json"
    path.parent.mkdir(parents=True)
    text = content.replace("%s", _wf_key(repo))
    path.write_text(text)
    monkeypatch.setattr(sweep_nightly.sweep, "sweep",
                        lambda *a, **k: pytest.fail("swept on an unknown owed set"))
    res = _run(repo, chela, Filer())
    assert res["status"] == "cannot_verify"
    assert res["why"].startswith("the owed-mutation store: ")
    assert path.read_text() == text and _covered_record(chela) == {}


def test_an_owed_store_that_is_a_directory_is_cannot_verify(tmp_path):
    repo, chela = _repo(tmp_path), tmp_path / "chela"
    (sweep_nightly.state_dir(chela) / "owed.json").mkdir(parents=True)
    assert _run(repo, chela, Filer())["status"] == "cannot_verify"


def test_a_valid_store_for_another_workflow_only_is_nothing_owed(tmp_path):
    repo, chela = _repo(tmp_path, prs=[]), tmp_path / "chela"
    path = sweep_nightly.state_dir(chela) / "owed.json"
    path.parent.mkdir(parents=True)
    path.write_text('{"/elsewhere/WORKFLOW.md": [{"file": "a.py", "line": 1, "kind": "k", '
                    '"before": "b", "after": "a", "pr": ""}]}')
    res = _run(repo, chela, Filer())
    assert res["reason"] == "no_merges"
    assert "/elsewhere/WORKFLOW.md" in json.loads(path.read_text())


def test_write_owed_keeps_every_other_workflows_list(tmp_path):
    chela = tmp_path / "chela"
    path = sweep_nightly.state_dir(chela) / "owed.json"
    path.parent.mkdir(parents=True)
    path.write_text('{"/other/WORKFLOW.md": [1]}')
    wf = type("WF", (), {"path": Path("/mine/WORKFLOW.md")})()
    sweep_nightly.write_owed(wf, [{"x": 1}], chela)
    assert json.loads(path.read_text()) == {"/other/WORKFLOW.md": [1],
                                            "/mine/WORKFLOW.md": [{"x": 1}]}


def test_a_battery_that_cannot_verify_leaves_the_owed_set_as_it_was(tmp_path, monkeypatch):
    repo, chela, res1 = _night_one(tmp_path)
    before = _owed_store(chela)
    monkeypatch.setattr(sweep_nightly.sweep, "sweep",
                        lambda *a, **k: sweep.SweepReport(cannot_verify="red baseline"))
    res = _run(repo, chela, Filer(), now=res1["at"] + 2 * DAY)
    assert res["status"] == "cannot_verify" and _owed_store(chela) == before


@pytest.mark.parametrize("make", ["dir", "binary"])
def test_an_owed_file_that_cannot_be_read_is_cannot_verify(tmp_path, monkeypatch, make):
    """``IsADirectoryError`` is an ``OSError`` that is NOT ``FileNotFoundError``; bad UTF-8 is a
    ``UnicodeDecodeError`` (a ``ValueError``): unknown, never "the line is gone"."""
    repo, chela, res1 = _night_one(tmp_path)
    if make == "dir":
        (repo / "size.py").unlink()
        _commit(repo, {"size.py/x.txt": "x\n"}, "size.py is a dir")
    else:
        (repo / "size.py").write_bytes(b"\xff\xfe\x00bad")
        _commit(repo, {}, "binary size.py")
    before = _owed_store(chela)
    monkeypatch.setattr(sweep_nightly.sweep, "sweep",
                        lambda *a, **k: pytest.fail("swept past an unreadable owed file"))
    res = _run(repo, chela, Filer(), now=res1["at"] + 2 * DAY)
    assert res["status"] == "cannot_verify"
    assert res["why"].startswith("could not read size.py to find an owed mutation: ")
    assert _owed_store(chela) == before


def test_an_owed_survivor_whose_filing_was_refused_stays_owed(tmp_path):
    repo, chela, res1 = _night_one(tmp_path)
    _budget(repo, tmp_path, 60)
    res = _run(repo, chela, Filer(fail=RuntimeError("tracker down")), now=res1["at"] + 2 * DAY)
    assert res["failed"] and _ids(_owed(chela, repo)) == {("size.py", 2, "if-false")}
    assert res["owed_ran"] == 2
    # ...and the next night, with the tracker back, files it and settles it.
    res = _run(repo, chela, Filer(), now=res1["at"] + 3 * DAY)
    assert res["filed"] == ["CMX-901"] and _owed(chela, repo) == []


def test_an_owed_survivor_with_an_unreadable_key_store_stays_owed(tmp_path):
    """Round-1 survivor of the first attempt: the filing CANNOT VERIFY (filed.json torn), so
    nothing was filed — the owed survivor must not be settled."""
    repo, chela, res1 = _night_one(tmp_path)
    (sweep_nightly.state_dir(chela) / "filed.json").write_text("{torn")
    _budget(repo, tmp_path, 60)
    res = _run(repo, chela, Filer(), now=res1["at"] + 2 * DAY)
    assert res["status"] == "cannot_verify"
    assert _ids(_owed(chela, repo)) == {("size.py", 2, "if-false")}


# --- the pieces ---------------------------------------------------------------------------


def _m(file="a.py", line=1, kind="if-false", before="    if x:", after="    if False and (x):"):
    return sweep.Mutation(file, line, kind, before, after)


def _e(m: sweep.Mutation, pr="`abc` PR (#1)", since=1.0) -> dict:
    return {"file": m.file, "line": m.line, "kind": m.kind, "before": m.before,
            "after": m.after, "pr": pr, "since": since}


def test_owed_first_puts_the_owed_first_in_owed_order_and_drops_unowed_extras():
    a, b, c = _m(line=1), _m(line=2), _m(line=3)
    extra = _m(line=3, kind="compare-flip", after="    if not x:")   # same owed-only line
    blamed = {"a.py": {1, 2}}
    owed = [sweep_nightly.mutation_id(c), sweep_nightly.mutation_id(b),
            ("gone.py", 9, "if-false", "x")]
    assert sweep_nightly.owed_first([a, b, c, extra], owed, blamed) == [c, b, a]


def test_owed_first_with_nothing_owed_keeps_the_sweeps_order():
    a, b = _m(line=2), _m(line=1)
    assert sweep_nightly.owed_first([a, b], [], {"a.py": {1, 2}}) == [a, b]


def test_locate_owed_finds_the_nearest_matching_line_not_taken(tmp_path):
    (tmp_path / "a.py").write_text("    if x:\nfoo\n    if x:\nbar\n    if x:\n")
    e = _e(_m(line=4))
    assert sweep_nightly.locate_owed(tmp_path, e, set()) in (3, 5)
    assert sweep_nightly.locate_owed(tmp_path, _e(_m(line=2)), set()) in (1, 3)
    assert sweep_nightly.locate_owed(tmp_path, _e(_m(line=1)), set()) == 1
    taken = {("a.py", 1, "if-false", "    if False and (x):")}
    assert sweep_nightly.locate_owed(tmp_path, _e(_m(line=1)), taken) == 3
    # a line that only CONTAINS the text, or has other indentation, is not it
    (tmp_path / "b.py").write_text("    if x: y\nif x:\n")
    assert sweep_nightly.locate_owed(tmp_path, _e(_m(file="b.py")), set()) is None


def test_locate_owed_of_a_missing_file_or_a_file_under_a_file_is_gone(tmp_path):
    (tmp_path / "f").write_text("x\n")
    assert sweep_nightly.locate_owed(tmp_path, _e(_m(file="nope.py")), set()) is None
    assert sweep_nightly.locate_owed(tmp_path, _e(_m(file="f/a.py")), set()) is None


def _report(*pairs) -> sweep.SweepReport:
    return sweep.SweepReport(results=[sweep.SweepResult(m, st) for m, st in pairs])


@pytest.mark.parametrize("status, kept", [
    (judge.KILLED, False), (judge.SURVIVED, False), (sweep.UNRUN, True),
    (sweep.TIMEOUT, True), (judge.INVALID, True), (sweep.NOT_APPLIED, True)])
def test_settle_owed_drops_only_what_ran(status, kept):
    m = _m(line=7)
    e = _e(_m(line=5))
    owed, dropped = sweep_nightly.settle_owed(_report((m, status)), [(e, 7)], [], {}, 9.0, False)
    if kept:
        assert owed == [{**e, "line": 7}] and dropped == []
    else:
        assert owed == [] and dropped == [{**e, "line": 7, "why": "ran", "status": status}]


def test_settle_owed_keeps_a_survivor_while_its_filing_failed():
    m = _m(line=7)
    e = _e(m)
    owed, dropped = sweep_nightly.settle_owed(_report((m, judge.SURVIVED), (_m(line=8),
                                              judge.KILLED)),
                                              [(e, 7), (_e(_m(line=8)), 8)], [], {}, 9.0, True)
    assert owed == [e] and [d["why"] for d in dropped] == ["ran"]


def test_settle_owed_drops_the_gone_and_the_unyielded_as_line_gone():
    gone, unyielded = _e(_m(file="x.py")), _e(_m(line=4))
    owed, dropped = sweep_nightly.settle_owed(_report(), [(unyielded, 4)], [gone], {}, 9.0,
                                              False)
    assert owed == []
    assert dropped == [{**gone, "why": "line_gone"}, {**unyielded, "why": "line_gone"}]


def test_settle_owed_adds_tonights_unrun_once_with_its_pr_and_time():
    kept, new = _m(line=1), _m(line=2)
    other = _m(line=3, kind="compare-flip", after="    if not x:")
    rep = _report((kept, sweep.UNRUN), (new, sweep.UNRUN), (new, sweep.UNRUN),
                  (other, judge.KILLED))
    owed, _ = sweep_nightly.settle_owed(rep, [(_e(kept, since=1.0), 1)], [],
                                        {("a.py", 2): "`f00` PR 9 (#9)"}, 9.0, False)
    assert owed == [_e(kept, since=1.0), _e(new, pr="`f00` PR 9 (#9)", since=9.0)]


def test_a_new_unrun_without_a_known_pr_records_an_empty_one():
    m = _m()
    owed, _ = sweep_nightly.settle_owed(_report((m, sweep.UNRUN)), [], [], {}, 2.0, False)
    assert owed == [_e(m, pr="", since=2.0)]


def test_the_battery_gets_owed_first_as_its_order(tmp_path, monkeypatch):
    """The run hands `owed_first` to the battery: the owed one is measured before the merge's."""
    repo, chela = _repo(tmp_path), tmp_path / "chela"
    path = sweep_nightly.state_dir(chela) / "owed.json"
    path.parent.mkdir(parents=True)
    owed = _e(sweep.Mutation("other.py", 3, "return-code", "        return 1",
                             "        return 0"))
    path.write_text(json.dumps({_wf_key(repo): [owed]}))
    seen = {}

    def fake(worktree, base, test_cmd, **kw):
        seen.update(kw)
        return sweep.SweepReport()
    monkeypatch.setattr(sweep_nightly.sweep, "sweep", fake)
    _run(repo, chela, Filer())
    late, weak = _m("other.py", 3, "return-code", "        return 1", "        return 0"), WEAK_MUT
    assert seen["order"]([weak, late]) == [late, weak]


def test_a_summary_of_a_run_that_stopped_before_the_battery_names_the_owed_carried(tmp_path,
                                                                                   monkeypatch):
    repo, chela, res1 = _night_one(tmp_path)
    monkeypatch.setattr("chela.worktree.detached_worktree",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no disk")))
    res = _run(repo, chela, Filer(), now=res1["at"] + 2 * DAY)
    assert res["status"] == "cannot_verify"
    assert res["summary"].endswith(" — 3 owed from earlier nights")
    assert _ids(_owed(chela, repo)) == OWED3


def test_the_real_sweep_honours_order_within_its_budget(tmp_path):
    """`sweep.sweep(order=…)`: the budget is spent on the head of what `order` returns."""
    repo = _repo(tmp_path)
    cmd = f'"{sys.executable}" -m pytest -q -p no:cacheprovider tests'
    lines = {"size.py": {2}, "other.py": {2, 3}}
    budget = sweep.SweepBudget(max_mutations=1)
    plain = sweep.sweep(repo, "l", cmd, select_tests=False, provision=False, lines=lines,
                        budget=budget)
    rev = sweep.sweep(repo, "l", cmd, select_tests=False, provision=False, lines=lines,
                      budget=budget, order=lambda muts: muts[::-1])
    ran = [r.mutation for r in plain.results if r.status != sweep.UNRUN]
    ran_rev = [r.mutation for r in rev.results if r.status != sweep.UNRUN]
    assert len(ran) == len(ran_rev) == 1 and ran != ran_rev
    assert ran_rev[0] == [r.mutation for r in plain.results][-1]


def test_a_fake_battery_unrun_is_owed_under_its_blamed_pr(tmp_path, monkeypatch):
    repo, chela = _repo(tmp_path), tmp_path / "chela"
    _fake_battery(monkeypatch, (WEAK_MUT, sweep.UNRUN))
    _run(repo, chela, Filer())
    [e] = _owed(chela, repo)
    assert e["pr"].endswith(f"` {PR1}") and (e["file"], e["line"]) == ("size.py", 2)
