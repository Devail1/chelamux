"""⚖️🧹🌙 CMX-433 — the nightly sweep files ONE issue per merged PR, not one per category.

On 2026-11-18 a night with 33 survivors in 29 categories from 4 PRs filed 29 issues. The filer
now groups survivors by the PR their line blames to (by file when none is known), files one
issue per group listing every mutation, caps the new issues a night (``sweep.max_issues``,
default 5) by folding the rest into the largest, and adds to an issue still open for a PR
instead of filing another. Every test runs against the throwaway repo of
:mod:`tests.test_sweep_nightly` and a temp ``CHELA_DIR``.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from types import SimpleNamespace

import pytest

from chela import judge, sweep, sweep_nightly
from tests.test_sweep_nightly import (Filer, _covered_record, _fake_battery, _git, _repo, _run,
                                      _store, _wf)

# 33 survivors from 4 PRs: 9 + 8 + 8 + 8 weak guards, each its OWN category (a distinct
# line), so a per-category filer would file 33 issues.
PR_SIZES = (9, 8, 8, 8)


@pytest.fixture(autouse=True)
def notices(monkeypatch):
    got: list[tuple[str, str, dict]] = []
    monkeypatch.setattr("chela.inbox.enqueue",
                        lambda kind, summary, payload: got.append((kind, summary, payload)))
    return got


def _guards(base: int, n: int) -> str:
    """``n`` distinct guard lines (line 2, 4, 6 … of the file)."""
    return "".join(f"def f{base + i}(x):\n    if x == {base + i}:\n        return 1\n"
                   for i in range(n))


def _guard_lines(base: int, n: int, file: str) -> list[sweep.Mutation]:
    return [sweep.Mutation(file, 3 * i + 2, "if-false", f"    if x == {base + i}:",
                           f"    if False and (x == {base + i}):") for i in range(n)]


def _four_prs(tmp_path: Path, extra: str = "") -> tuple[Path, list[sweep.Mutation]]:
    prs, survivors, base = [], [], 0
    for n, size in enumerate(PR_SIZES, 1):
        prs.append(({f"p{n}.py": _guards(base, size)}, f"PR {n}: part {n} (#{n})", None))
        survivors += _guard_lines(base, size, f"p{n}.py")
        base += 100
    return _repo(tmp_path, extra=extra, prs=prs), survivors


def _survive(monkeypatch, survivors):
    _fake_battery(monkeypatch, *[(m, judge.SURVIVED) for m in survivors])


def _labels(repo: Path) -> dict[str, str]:
    """``p<n>.py`` → the PR label its lines blame to."""
    shas = _git(repo, "log", "--format=%H", "-4").split()[::-1]
    return {f"p{n}.py": f"`{sha[:12]}` PR {n}: part {n} (#{n})"
            for n, sha in enumerate(shas, 1)}


class Appender(Filer):
    """A filer that can also add to an issue: ``open`` is what the tracker reports open
    (``None``: it cannot say)."""

    def __init__(self, open_ids=(), ok=True, fail=None):
        super().__init__()
        self.open = set(open_ids) if open_ids is not None else None
        self.ok, self.append_fail = ok, fail
        self.appends: list[tuple[str, sweep_nightly.IssueGroup]] = []
        self.asked: list[list[str]] = []

    def open_issues(self, idents):
        self.asked.append(list(idents))
        return None if self.open is None else self.open & set(idents)

    def append(self, ident, group, ctx):
        self.appends.append((ident, group))
        if self.append_fail is not None:
            raise self.append_fail
        return self.ok


# --- the acceptance: 33 survivors from 4 PRs → 4 issues ----------------------------------------


def test_33_survivors_from_4_prs_file_4_issues_each_listing_all_its_mutations(tmp_path,
                                                                             monkeypatch):
    repo, survivors = _four_prs(tmp_path)
    _survive(monkeypatch, survivors)
    filer = Filer()
    res = _run(repo, tmp_path / "chela", filer)
    assert (res["status"], res["survivors"], len(res["groups"])) == ("survivors", 33, 33)
    assert len(filer.calls) == 4 and res["filed"] == ["CMX-901", "CMX-902", "CMX-903",
                                                      "CMX-904"]
    labels = _labels(repo)
    for (group, ctx), (n, size) in zip(filer.calls, enumerate(PR_SIZES, 1)):
        (section,) = group.sections
        assert (section.label, ctx.merged_in) == (labels[f"p{n}.py"], labels[f"p{n}.py"])
        assert [m.key for m in group.survivors] == [
            m.key for m in survivors if m.file == f"p{n}.py"]
        assert len(group.categories) == size
    assert res["folded"] == 0
    assert [i["survivors"] for i in res["issues"]] == [9, 8, 8, 8]


def test_negative_control_per_category_filing_files_33_not_4(tmp_path, monkeypatch):
    """RED control for the count: split by category (what the filer did before) and the same
    night files 33 issues — so the count test above reads the PR grouping, nothing else."""
    repo, survivors = _four_prs(tmp_path, extra="sweep:\n  max_issues: 100\n")
    _survive(monkeypatch, survivors)
    monkeypatch.setattr(sweep_nightly, "by_pr", lambda survs, merged: [
        (merged.get(m.key, ""), m.file, [m]) for m in survs])
    filer = Filer()
    _run(repo, tmp_path / "chela", filer)
    assert len(filer.calls) == 33


def test_the_cap_folds_the_rest_into_the_largest_and_drops_nothing(tmp_path, monkeypatch,
                                                                   notices):
    repo, survivors = _four_prs(tmp_path, extra="sweep:\n  max_issues: 2\n")
    _survive(monkeypatch, survivors)
    filer = Filer()
    res = _run(repo, tmp_path / "chela", filer)
    labels = _labels(repo)
    assert len(filer.calls) == 2
    assert [[s.label for s in g.sections] for g, _ in filer.calls] == [
        [labels["p1.py"], labels["p3.py"]], [labels["p2.py"], labels["p4.py"]]]
    assert sorted(m.key for g, _ in filer.calls for m in g.survivors) == sorted(
        m.key for m in survivors)
    assert (res["folded"], res["max_issues"]) == (2, 2)
    assert [i["survivors"] for i in res["issues"]] == [17, 16]
    assert ("filed CMX-901 (17 survivor(s)), CMX-902 (16 survivor(s)); 2 more PR/file "
            "group(s) folded into the largest issues (cap 2 a night)") in notices[0][1]
    # every survivor is recorded against the issue it went into, every PR to its issue
    store = _store(tmp_path / "chela")
    assert len(store["keys"]) == 33
    assert sorted(v["issue"] for v in store["issues"].values()) == [
        "CMX-901", "CMX-901", "CMX-902", "CMX-902"]


def test_the_default_cap_is_five(tmp_path, monkeypatch):
    prs, survivors = [], []
    for n in range(1, 8):
        prs.append(({f"q{n}.py": _guards(100 * n, 1)}, f"PR {n} (#{n})", None))
        survivors += _guard_lines(100 * n, 1, f"q{n}.py")
    repo = _repo(tmp_path, prs=prs)
    _survive(monkeypatch, survivors)
    filer = Filer()
    res = _run(repo, tmp_path / "chela", filer)
    assert (len(filer.calls), res["folded"], res["max_issues"]) == (5, 2, 5)
    assert len(filer.survived) == 7


def test_a_survivor_with_no_known_pr_is_grouped_by_its_file(tmp_path, monkeypatch):
    repo, survivors = _four_prs(tmp_path)
    _survive(monkeypatch, survivors)
    monkeypatch.setattr(sweep_nightly, "blamed_lines", lambda wt, commits: (
        {f: set(range(1, 40)) for f in ("p1.py", "p2.py", "p3.py", "p4.py")}, {}))
    filer = Filer()
    res = _run(repo, tmp_path / "chela", filer)
    assert [(s.label, s.file) for g, _ in filer.calls for s in g.sections] == [
        ("", "p1.py"), ("", "p2.py"), ("", "p3.py"), ("", "p4.py")]
    assert [g.sections[0].name for g, _ in filer.calls] == ["p1.py", "p2.py", "p3.py",
                                                           "p4.py"]
    assert res["filed"] == ["CMX-901", "CMX-902", "CMX-903", "CMX-904"]


# --- a second night: append to the open issue --------------------------------------------------


def _two_nights(tmp_path, monkeypatch, filer2):
    repo, survivors = _four_prs(tmp_path)
    p1 = [m for m in survivors if m.file == "p1.py"]
    chela = tmp_path / "chela"
    _survive(monkeypatch, p1[:5])
    first = Appender()
    res1 = _run(repo, chela, first)
    assert (res1["filed"], first.asked) == (["CMX-901"], [])    # nothing known: no read
    _survive(monkeypatch, p1)                    # tonight: 4 more survivors on PR 1's lines
    return repo, p1, chela, _run(repo, chela, filer2)


def test_a_second_night_appends_to_the_open_issue_instead_of_filing(tmp_path, monkeypatch,
                                                                     notices):
    filer = Appender(open_ids={"CMX-901"})
    repo, p1, chela, res = _two_nights(tmp_path, monkeypatch, filer)
    assert (filer.calls, res["filed"], res["appended"]) == ([], [], ["CMX-901"])
    assert filer.asked == [["CMX-901"]]
    (ident, group), = filer.appends
    assert ident == "CMX-901"
    assert [m.key for m in group.survivors] == [m.key for m in p1[5:]]   # only the new ones
    assert res["issues"] == [{"issue": "CMX-901", "survivors": 4,
                              "sources": [_labels(repo)["p1.py"].replace("`", "")],
                              "appended": True}]
    assert "no new issue; appended to CMX-901 (4 survivor(s))" in notices[-1][1]
    assert {v["issue"] for v in _store(chela)["keys"].values()} == {"CMX-901"}
    assert len(_store(chela)["keys"]) == 9
    assert res["status"] == "survivors"


def test_a_closed_issue_for_the_pr_gets_a_new_one(tmp_path, monkeypatch):
    filer = Appender(open_ids=set())
    _, p1, chela, res = _two_nights(tmp_path, monkeypatch, filer)
    assert (filer.appends, res["filed"], res["appended"]) == ([], ["CMX-901"], [])
    assert [m.key for m in filer.calls[0][0].survivors] == [m.key for m in p1[5:]]
    assert [v["issue"] for v in _store(chela)["issues"].values()] == ["CMX-901"]
    assert _store(chela)["issues"][filer.calls[0][0].key]["at"] == res["at"]


@pytest.mark.parametrize("make", ["none", "raises"])
def test_an_unreadable_open_state_files_nothing_and_holds_the_window(tmp_path, monkeypatch,
                                                                     make):
    filer = Appender(open_ids=None)
    if make == "raises":
        filer.open_issues = lambda idents: (_ for _ in ()).throw(RuntimeError("down"))
    _, p1, chela, res = _two_nights(tmp_path, monkeypatch, filer)
    assert (filer.calls, filer.appends, res["filed"], res["appended"]) == ([], [], [], [])
    assert res["failed"] == [f"{p1[5].key}: could not read whether CMX-901 is still open "
                             "— not filed"]
    assert res["status"] == "survivors"
    assert len(_store(chela)["keys"]) == 5          # the new ones are NOT recorded
    assert _covered_record(chela) != {} and res["at"] not in _covered_record(chela).values()


@pytest.mark.parametrize("ok, fail, why", [
    (False, None, "CMX-901 did not take the comment"),
    (None, None, "CMX-901 did not take the comment"),
    ("set", None, "CMX-901 did not take the comment"),
    (True, RuntimeError("tracker said no"), "tracker said no")])
def test_a_refused_comment_is_a_failed_filing(tmp_path, monkeypatch, ok, fail, why):
    filer = Appender(open_ids={"CMX-901"}, ok=ok, fail=fail)
    _, p1, chela, res = _two_nights(tmp_path, monkeypatch, filer)
    assert (filer.calls, res["appended"], res["failed"]) == ([], [], [f"{p1[5].key}: {why}"])
    assert len(_store(chela)["keys"]) == 5


def test_a_filer_that_cannot_append_files_a_new_issue(tmp_path, monkeypatch):
    filer = Filer()
    _, p1, chela, res = _two_nights(tmp_path, monkeypatch, filer)
    assert (res["filed"], res["appended"]) == (["CMX-901"], [])
    assert [m.key for m in filer.calls[0][0].survivors] == [m.key for m in p1[5:]]


def test_a_night_with_nothing_new_neither_files_nor_appends(tmp_path, monkeypatch):
    repo, survivors = _four_prs(tmp_path)
    _survive(monkeypatch, survivors[:3])
    _run(repo, tmp_path / "chela", Appender())
    again = Appender(open_ids={"CMX-901"})
    res = _run(repo, tmp_path / "chela", again)
    assert (again.calls, again.appends, again.asked, res["already_filed"]) == ([], [], [], 1)


@pytest.mark.parametrize("issues", [[], {"k": "CMX-1"}, {"k": {"issue": 3}}, {"k": {}}])
def test_a_malformed_issue_map_files_nothing(tmp_path, monkeypatch, issues):
    repo, survivors = _four_prs(tmp_path)
    _survive(monkeypatch, survivors[:3])
    chela = tmp_path / "chela"
    sweep_nightly.state_dir(chela).mkdir(parents=True)
    (sweep_nightly.state_dir(chela) / "filed.json").write_text(json.dumps(
        {"keys": {}, "issues": issues}))
    filer = Appender()
    res = _run(repo, chela, filer)
    assert (res["status"], filer.calls) == ("cannot_verify", [])
    assert "malformed key or issue map" in res["why"]


def test_a_malformed_key_map_files_nothing(tmp_path, monkeypatch):
    repo, survivors = _four_prs(tmp_path)
    _survive(monkeypatch, survivors[:3])
    chela = tmp_path / "chela"
    sweep_nightly.state_dir(chela).mkdir(parents=True)
    (sweep_nightly.state_dir(chela) / "filed.json").write_text(json.dumps({"keys": []}))
    res = _run(repo, chela, Filer())
    assert (res["status"], res["filed"]) == ("cannot_verify", [])


# --- the units ---------------------------------------------------------------------------------


def _m(file, line, before="    if x:"):
    return sweep.Mutation(file, line, "if-false", before, "    if False and (x):")


def test_by_pr_splits_by_label_then_falls_back_to_the_file():
    a, b, c, d, e = _m("a.py", 1), _m("b.py", 2), _m("a.py", 3), _m("c.py", 4), _m("c.py", 5)
    merged = {"a.py:1": "`aaa` PR A", "b.py:2": "`aaa` PR A", "a.py:3": "`bbb` PR B"}
    assert sweep_nightly.by_pr([a, b, c, d, e], merged) == [
        ("`aaa` PR A", "a.py", [a, b]), ("`bbb` PR B", "a.py", [c]), ("", "c.py", [d, e])]


def test_section_key_is_the_prs_sha_or_the_file():
    k = sweep_nightly.section_key
    assert k("w", "`abc1234` one subject", "a.py") == k("w", "`abc1234` another", "b.py")
    assert k("w", "`abc1234` s", "a.py") != k("w", "`abc1235` s", "a.py")
    assert k("w", "", "a.py") == k("w", "", "a.py") != k("w", "", "b.py")
    assert k("w", "", "a.py") != k("w", "`abc1234` s", "a.py")
    assert k("w2", "`abc1234` s", "a.py") != k("w", "`abc1234` s", "a.py")
    # a label that is not "`<sha>` …" is its own identity
    assert k("w", "no sha", "a.py") != k("w", "other", "a.py")
    assert len(k("w", "", "a.py")) == 24


def _sec(name, n):
    cats = tuple(sweep_nightly.SurvivorGroup(file=name, mutation=_m(name, i),
                                             survivors=(_m(name, i),), sites=(), key=str(i))
                 for i in range(1, n + 1))
    return sweep_nightly.Section(label=f"`{name}`", file=name, categories=cats, key=name)


@pytest.mark.parametrize("sizes, cap, want", [
    ([1, 3, 2], 5, [["b"], ["c"], ["a"]]),                       # under the cap: by size
    ([1, 3, 2, 3], 2, [["b", "c"], ["d", "a"]]),                 # tie: the sweep's order
    ([5, 1, 1, 1, 1], 1, [["a", "b", "c", "d", "e"]]),
    ([2, 2, 2], 3, [["a"], ["b"], ["c"]]),
    ([], 3, []),
])
def test_cap_issues_folds_round_robin_into_the_largest(sizes, cap, want):
    secs = [_sec(chr(ord("a") + i), n) for i, n in enumerate(sizes)]
    got = sweep_nightly.cap_issues(secs, cap)
    assert [[s.file for s in g.sections] for g in got] == want
    assert sum(len(g.survivors) for g in got) == sum(sizes)


@pytest.mark.parametrize("raw, want, warns", [
    (None, 5, False), (3, 3, False), (1, 1, False), (0, 5, True), (-2, 5, True),
    ("3", 5, True), (True, 5, True), (2.5, 5, True)])
def test_max_issues(tmp_path, caplog, raw, want, warns):
    wf = _wf(tmp_path, "" if raw is None else f"sweep:\n  max_issues: {json.dumps(raw)}\n")
    with caplog.at_level(logging.WARNING, logger="chela.sweep_nightly"):
        assert sweep_nightly.max_issues(wf) == want
    assert ("sweep.max_issues" in caplog.text) is warns


def test_the_workflow_cap_reaches_the_run(tmp_path, monkeypatch):
    repo, survivors = _four_prs(tmp_path, extra="sweep:\n  max_issues: 3\n")
    _survive(monkeypatch, survivors)
    filer = Filer()
    res = _run(repo, tmp_path / "chela", filer)
    assert (len(filer.calls), res["folded"], res["max_issues"]) == (3, 1, 3)


# --- the tracker filer's append ----------------------------------------------------------------


def _group():
    m = _m("a.py", 3)
    cat = sweep_nightly.SurvivorGroup(file="a.py", mutation=m, survivors=(m,),
                                      sites=("a.py:3",), key="k")
    return sweep_nightly.IssueGroup((sweep_nightly.Section("`abc` PR 1 (#1)", "a.py", (cat,),
                                                           "s"),))


def _ctx(tmp_path):
    return sweep_nightly.FileContext(workflow=tmp_path / "W", worktree=tmp_path, head="e" * 40,
                                     ref="origin/dev", merged_in="`abc` PR 1 (#1)",
                                     chela_dir=tmp_path)


@pytest.mark.parametrize("reply, ok", [("set", True), ("missing", False), ("failed", False),
                                       ("skipped", False)])
def test_the_tracker_filer_appends_through_comment(tmp_path, reply, ok):
    calls = []
    filer = sweep_nightly.TrackerFiler(None, comment=lambda i, b: calls.append((i, b)) or reply,
                                       fetch=lambda ids: [])
    assert filer.append("CMX-5", _group(), _ctx(tmp_path)) is ok
    assert calls == [("CMX-5", "\n".join([
        f"⚖️🧹 The nightly sweep of `origin/dev` at `{'e' * 12}` found 1 more surviving "
        "mutation(s) here.", "",
        "### Merged in: `abc` PR 1 (#1)", "",
        "**Category:** `if-false` on `if x:`", "",
        "- `a.py:3`: `if x:` → `if False and (x):`", "",
        "Same-shape sites (every source line that reads exactly `if x:`):", "",
        "- `a.py:3` ← survived", "",
        "Add a test that goes RED under each mutation above — not a weaker assertion — and "
        "check every same-shape site for the same gap."]))]


def test_the_tracker_filer_reads_open_issues_from_the_tracker():
    asked = []
    tasks = [SimpleNamespace(id="CMX-1", terminal_state=None),
             SimpleNamespace(id="CMX-2", terminal_state="done"),
             SimpleNamespace(id="CMX-3", terminal_state="canceled")]
    filer = sweep_nightly.TrackerFiler(None, comment=lambda i, b: "set",
                                       fetch=lambda ids: asked.append(ids) or tasks)
    assert filer.open_issues(["CMX-1", "CMX-2", "CMX-3", "CMX-4"]) == {"CMX-1"}
    assert asked == [["CMX-1", "CMX-2", "CMX-3", "CMX-4"]]
    unreadable = sweep_nightly.TrackerFiler(None, comment=lambda i, b: "set",
                                            fetch=lambda ids: None)
    assert unreadable.open_issues(["CMX-1"]) is None


@pytest.mark.parametrize("comment, fetch, can", [
    (True, True, True), (False, True, False), (True, False, False), (False, False, False)])
def test_can_append_needs_both_comment_and_fetch(comment, fetch, can):
    filer = sweep_nightly.TrackerFiler(lambda *a, **k: {"identifier": "X"},
                                       comment=(lambda i, b: "set") if comment else None,
                                       fetch=(lambda ids: []) if fetch else None)
    assert sweep_nightly.can_append(filer) is can


def test_can_append_of_a_plain_callable_and_of_would_file():
    assert sweep_nightly.can_append(lambda g, c: "X") is False
    assert sweep_nightly.can_append(sweep_nightly.WouldFile()) is False
    assert sweep_nightly.can_append(Appender()) is True


def test_default_filer_wires_the_sources_comment_and_fetch(tmp_path, monkeypatch):
    class Src:
        def create_issue(self, title, description="", labels=()):
            return {"identifier": "CMX-3"}

        def comment(self, task_id, body):
            return "set"

        def fetch_by_ids(self, ids):
            return []
    src = Src()
    monkeypatch.setattr("chela.sources.get_source", lambda wf: src)
    filer = sweep_nightly.default_filer(_wf(tmp_path))
    assert (filer.comment.__self__, filer.comment.__func__) == (src, Src.comment)
    assert (filer.fetch.__self__, filer.fetch.__func__) == (src, Src.fetch_by_ids)
    assert sweep_nightly.can_append(filer)


def test_a_run_with_no_filer_appends_through_the_tracker(tmp_path, monkeypatch):
    """🔴 WIRING: night 2 reaches the real source's `fetch_by_ids` and `comment`."""
    repo, survivors = _four_prs(tmp_path)
    p1 = [m for m in survivors if m.file == "p1.py"]
    seen = {"comment": [], "fetch": []}

    class Src:
        def create_issue(self, title, description="", labels=()):
            return {"identifier": "CMX-77"}

        def comment(self, task_id, body):
            seen["comment"].append((task_id, body))
            return "set"

        def fetch_by_ids(self, ids):
            seen["fetch"].append(list(ids))
            return [SimpleNamespace(id="CMX-77", terminal_state=None)]
    monkeypatch.setattr("chela.sources.get_source", lambda wf: Src())
    chela = tmp_path / "chela"
    _survive(monkeypatch, p1[:2])
    assert _run(repo, chela, None)["filed"] == ["CMX-77"]
    _survive(monkeypatch, p1[:3])
    res = _run(repo, chela, None)
    assert (res["filed"], res["appended"], seen["fetch"]) == ([], ["CMX-77"], [["CMX-77"]])
    (ident, body), = seen["comment"]
    assert ident == "CMX-77" and f"- `{p1[2].key}`:" in body and p1[0].key not in body


# --- the notice --------------------------------------------------------------------------------


def _result(**kw):
    base = {"workflow": "/x/repo/WORKFLOW.md", "status": "survivors", "survivors": 3,
            "groups": [{}] * 3, "filed": [], "appended": [], "issues": [], "already_filed": 0,
            "failed": [], "would_file": [], "warnings": [], "prs": []}
    return {**base, **kw}


@pytest.mark.parametrize("kw, part", [
    ({"filed": ["CMX-1", "CMX-2"],
      "issues": [{"issue": "CMX-1", "survivors": 2, "appended": False},
                 {"issue": "CMX-2", "survivors": 1, "appended": False}]},
     "filed CMX-1 (2 survivor(s)), CMX-2 (1 survivor(s))"),
    ({"filed": ["CMX-1"], "appended": ["CMX-1"],
      "issues": [{"issue": "CMX-1", "survivors": 2, "appended": True},
                 {"issue": "CMX-1", "survivors": 5, "appended": False}]},
     "filed CMX-1 (5 survivor(s)); appended to CMX-1 (2 survivor(s))"),
    ({"filed": ["CMX-1"]}, "filed CMX-1 —"),
    ({"appended": ["CMX-4"], "issues": [{"issue": "CMX-4", "survivors": 3,
                                         "appended": True}]},
     "no new issue; appended to CMX-4 (3 survivor(s))"),
    ({}, "nothing filed —"),
    ({"filed": ["CMX-1"], "folded": 3, "max_issues": 5},
     "filed CMX-1; 3 more PR/file group(s) folded into the largest issues (cap 5 a night)"),
    ({"filed": ["CMX-1"], "folded": 0, "max_issues": 5}, "filed CMX-1 —"),
])
def test_the_notice_says_how_many_survivors_went_into_each_issue(kw, part):
    line = sweep_nightly.summary(_result(**kw))
    assert part in line + " —"
