"""⚖️🧹🌙 CMX-299 — the nightly sweep over what STANDARD-tier PRs merged to the base branch.

Every test runs against a throwaway repo whose ``origin`` is a local bare repo, a temp
``CHELA_DIR`` and a fake filer — no test talks to Linear (the label path runs against a stub
transport, as tests/test_linear_new_task.py does).
"""
from __future__ import annotations

import argparse
import fcntl
import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import pytest

from chela import judge, risktier, sweep, sweep_nightly
from chela import main as main_mod
from chela.sources import linear
from chela.sources.linear import LinearError, LinearSource
from chela.workflow import load_workflow, parse_workflow

OLD = "2020-01-01T00:00:00+00:00"

CALC = "def clamp(x, lo):\n    if x < lo:\n        return lo\n    return x\n"
CALC_TEST = ("from calc import clamp\n\n\ndef test_clamp():\n    assert clamp(-1, 0) == 0\n"
             "    assert clamp(5, 0) == 5\n")
# PR 1 — the ONE weak guard: no test reads is_big().
WEAK = 'def is_big(x):\n    if x > 100:\n        return "big"\n    return "small"\n'
# PR 2 — the same shape, covered: killed, but still a same-shape site of the weak one.
COVERED = "def big_enough(x):\n    if x > 100:\n        return 1\n    return 2\n"
COVERED_TEST = ("from other import big_enough\n\n\ndef test_big_enough():\n"
                "    assert big_enough(101) == 1\n    assert big_enough(100) == 2\n")
# A CORE-tier PR with a weak guard of its own — judged per PR already, never swept here.
CORE_WEAK = "def tick(y):\n    if y > 7:\n        return 3\n    return 4\n"


def _git(repo: Path, *args: str, date: str | None = None) -> str:
    env = dict(os.environ, **({"GIT_COMMITTER_DATE": date, "GIT_AUTHOR_DATE": date}
                              if date else {}))
    return subprocess.run(["git", "-C", str(repo), "-c", "user.email=t@t", "-c", "user.name=t",
                           *args], check=True, capture_output=True, text=True,
                          env=env).stdout.strip()


def _workflow_text(root: Path, *, extra: str = "") -> str:
    return (f"---\nproject_key: CMX\ntracker:\n  kind: linear\n  team: CMX\nworkspace:\n"
            f"  root: {root / 'wt'}\n  base_branch: dev\njudge:\n"
            f"  test_cmd: '\"{sys.executable}\" -m pytest -q -p no:cacheprovider tests'\n"
            f"  suite_timeout_seconds: 120\n{extra}---\nbody\n")


def _commit(repo: Path, files: dict[str, str], msg: str, date: str | None = None) -> str:
    for rel, body in files.items():
        (repo / rel).parent.mkdir(parents=True, exist_ok=True)
        (repo / rel).write_text(body)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", msg, date=date)
    _git(repo, "push", "-q", "origin", "dev")
    return _git(repo, "rev-parse", "HEAD")


def _repo(tmp_path: Path, *, extra: str = "", prs=None) -> Path:
    """``origin/dev``: an OLD base, then two standard-tier PRs merged now (squash shape)."""
    bare, repo = tmp_path / "origin.git", tmp_path / "repo"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "dev", str(bare)], check=True)
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "dev")
    _git(repo, "remote", "add", "origin", str(bare))
    (repo / ".gitignore").write_text(".pytest_cache/\n__pycache__/\n")
    _commit(repo, {"calc.py": CALC, "tests/test_calc.py": CALC_TEST,
                   "WORKFLOW.md": _workflow_text(tmp_path, extra=extra)}, "base", date=OLD)
    for files, msg, date in (prs if prs is not None else [
            ({"size.py": WEAK}, "PR 1: is_big (#1)", None),
            ({"other.py": COVERED, "tests/test_other.py": COVERED_TEST},
             "PR 2: big_enough (#2)", None)]):
        _commit(repo, files, msg, date)
    return repo


class Filer:
    def __init__(self, fail: Exception | None = None):
        self.calls: list[tuple[str, str]] = []
        self.fail = fail

    def __call__(self, title: str, body: str) -> str:
        self.calls.append((title, body))
        if self.fail is not None:
            raise self.fail
        return f"CMX-{900 + len(self.calls)}"


@pytest.fixture
def notices(monkeypatch):
    got: list[tuple[str, str, dict]] = []
    monkeypatch.setattr("chela.inbox.enqueue", lambda k, s, p: got.append((k, s, p)) or True)
    return got


def _run(repo: Path, chela_dir: Path, filer, **kw) -> dict:
    return sweep_nightly.run(load_workflow(repo / "WORKFLOW.md"), chela_dir=chela_dir,
                             filer=filer, **kw)


# --- the acceptance fixture ---------------------------------------------------------------


def test_two_standard_prs_one_weak_guard_file_exactly_one_issue_with_its_sites(
        tmp_path, notices):
    repo, chela = _repo(tmp_path), tmp_path / "chela"
    filer = Filer()
    res = _run(repo, chela, filer)
    assert (res["status"], res["filed"], len(filer.calls)) == ("filed", ["CMX-901"], 1)
    title, body = filer.calls[0]
    assert title == "Nightly sweep: if-false, compare-flip survived at size.py:2"
    assert "- `size.py:2` ← survived" in body and "- `other.py:2`" in body
    assert "- `if-false`: `if x > 100:` → `if False and (x > 100):`" in body
    assert "PR 1: is_big (#1)" in body
    assert len(res["prs"]) == 2 and res["survivors"] == 2
    assert [(k, s) for k, s, _ in notices] == [(
        "nightly_sweep", f"🧹🌙 nightly sweep (repo): 2 survivor(s) — filed CMX-901 — 2 "
                         f"standard-tier PR(s), {res['mutations']} mutation(s)")]
    # the checkout is thrown away, and the repo's own tree is untouched
    assert not (tmp_path / "wt" / "nightly-sweep").exists()
    assert _git(repo, "status", "--porcelain") == ""


def test_a_second_run_with_no_change_files_nothing(tmp_path, notices):
    repo, chela = _repo(tmp_path), tmp_path / "chela"
    _run(repo, chela, Filer())
    again = Filer()
    res = _run(repo, chela, again)
    assert again.calls == [] and res["filed"] == [] and res["already_filed"] == 1
    assert "1 already filed" in notices[-1][1] and "filed CMX" not in notices[-1][1]


def test_a_new_survivor_category_on_an_already_filed_guard_is_filed(tmp_path, notices):
    """Dedupe is per surviving MUTATION: a guard line filed for one category whose other
    category is new is filed again — only a group whose EVERY key is filed is skipped."""
    repo, chela = _repo(tmp_path), tmp_path / "chela"
    _run(repo, chela, Filer())
    path = sweep_nightly.state_dir(chela) / "filed.json"
    store = json.loads(path.read_text())
    assert len(store["keys"]) == 2              # if-false + compare-flip on size.py:2
    store["keys"].pop(sorted(store["keys"])[0])
    path.write_text(json.dumps(store))
    again = Filer()
    res = _run(repo, chela, again)
    assert (res["filed"], res["already_filed"], len(again.calls)) == (["CMX-901"], 0, 1)
    assert len(json.loads(path.read_text())["keys"]) == 2


def test_negative_control_without_the_key_store_the_same_survivor_is_filed_again(
        tmp_path, notices):
    """RED control for the dedupe: it is the store, nothing else, that stops a re-file."""
    repo, chela = _repo(tmp_path), tmp_path / "chela"
    _run(repo, chela, Filer())
    (sweep_nightly.state_dir(chela) / "filed.json").unlink()
    again = Filer()
    assert _run(repo, chela, again)["filed"] == ["CMX-901"] and len(again.calls) == 1


def test_a_daemon_restart_between_runs_still_files_nothing(tmp_path, notices):
    """Run 2 is a NEW process (`chela judge nightly-sweep`), so only the on-disk store
    remembers run 1. It reaches the real tracker filer — which has no Linear key here, so
    a re-file would come back NOT filed and exit 1."""
    repo, chela = _repo(tmp_path), tmp_path / "chela"
    _run(repo, chela, Filer())
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(("CHELA_", "PYTEST_")) and k != "LINEAR_API_KEY"}
    env.update(CHELA_DIR=str(chela), CHELA_INBOX_ENABLED="false")
    out = subprocess.run([sys.executable, "-m", "chela.main", "judge", "nightly-sweep",
                          "--workflow", str(repo / "WORKFLOW.md"), "--json"],
                         env=env, capture_output=True, text=True, timeout=300)
    res = json.loads(out.stdout)
    assert (out.returncode, res["filed"], res["failed"], res["already_filed"]) == (0, [], [], 1)


def test_a_fleet_pause_skips_the_run(tmp_path, notices):
    repo, chela = _repo(tmp_path), tmp_path / "chela"
    chela.mkdir()
    (chela / "fleet-pause.json").write_text("not even json")
    filer = Filer()
    res = _run(repo, chela, filer)
    assert (res["status"], filer.calls) == ("skipped", [])
    assert notices[-1][1] == ("🧹🌙 nightly sweep (repo): skipped — a fleet pause (`chela "
                              "dispatch --pause --all`) is active")


def test_negative_control_without_the_pause_the_same_run_sweeps(tmp_path, notices):
    repo, chela = _repo(tmp_path), tmp_path / "chela"
    chela.mkdir()
    assert _run(repo, chela, Filer())["status"] == "filed"


CORE_PRS = [({"chela/dispatcher.py": CORE_WEAK}, "core PR (#3)", None)]


def test_a_core_tier_merged_pr_is_not_swept(tmp_path, notices):
    repo, chela = _repo(tmp_path, prs=CORE_PRS), tmp_path / "chela"
    filer = Filer()
    res = _run(repo, chela, filer)
    assert (res["status"], filer.calls, res["not_swept"]["core"]) == ("skipped", [], 1)
    assert notices[-1][1] == ("🧹🌙 nightly sweep (repo): skipped — no standard-tier PR "
                              "merged to origin/dev in the last 24h")


def test_negative_control_a_core_pr_read_as_standard_would_be_swept(tmp_path, notices,
                                                                     monkeypatch):
    """RED control for the tier filter: the same core PR, classified STANDARD, IS swept."""
    repo, chela = _repo(tmp_path, prs=CORE_PRS), tmp_path / "chela"
    monkeypatch.setattr(risktier, "classify", lambda *a, **k: (risktier.STANDARD, None))
    filer = Filer()
    assert _run(repo, chela, filer)["status"] == "filed"
    assert filer.calls[0][0].endswith("at chela/dispatcher.py:2")


def test_only_the_last_24h_is_swept(tmp_path, notices):
    old = [({"size.py": WEAK}, "old PR", "2020-06-01T00:00:00+00:00")]
    repo, chela = _repo(tmp_path, prs=old), tmp_path / "chela"
    filer = Filer()
    assert (_run(repo, chela, filer)["status"], filer.calls) == ("skipped", [])
    # the same commit, swept as of the night after it merged
    when = datetime.fromisoformat("2020-06-01T10:00:00+00:00").timestamp()
    assert _run(repo, chela, filer, now=when)["filed"] == ["CMX-901"]


def test_a_line_a_later_core_pr_rewrote_is_not_swept(tmp_path, notices):
    """The guard now blames to the core PR (judged per PR), so it is not swept again."""
    prs = [({"size.py": WEAK}, "PR 1 (#1)", None),
           ({"size.py": WEAK.replace("x > 100", "x >= 100"), "chela/dispatcher.py": "X = 1\n"},
            "core rewrite (#9)", None)]
    repo, chela = _repo(tmp_path, prs=prs), tmp_path / "chela"
    filer = Filer()
    res = _run(repo, chela, filer)
    assert (res["status"], filer.calls, res["not_swept"]["core"]) == ("clean", [], 1)


def test_no_survivor_files_nothing_and_says_so(tmp_path, notices):
    prs = [({"other.py": COVERED, "tests/test_other.py": COVERED_TEST}, "PR 2 (#2)", None)]
    repo, chela = _repo(tmp_path, prs=prs), tmp_path / "chela"
    filer = Filer()
    res = _run(repo, chela, filer)
    assert (res["status"], filer.calls, res["survivors"]) == ("clean", [], 0)
    assert len(notices) == 1 and notices[0][1].startswith(
        "🧹🌙 nightly sweep (repo): clean — no survivors, nothing filed — 1 standard-tier PR(s)")


# --- failures fail closed -----------------------------------------------------------------


def test_a_refused_filing_is_not_recorded_and_is_filed_next_run(tmp_path, notices):
    repo, chela = _repo(tmp_path), tmp_path / "chela"
    res = _run(repo, chela, Filer(fail=RuntimeError("Linear said no")))
    assert (res["status"], res["filed"]) == ("filed", [])
    assert res["failed"] == ["size.py:2: Linear said no"]
    assert "1 NOT filed (size.py:2: Linear said no)" in notices[-1][1]
    assert _run(repo, chela, Filer())["filed"] == ["CMX-901"]


def test_a_tracker_that_cannot_file_is_reported_not_filed(tmp_path, notices, monkeypatch):
    repo, chela = _repo(tmp_path), tmp_path / "chela"
    monkeypatch.setattr(sweep_nightly, "tracker_filer", lambda wf: None)
    res = _run(repo, chela, None)
    assert res["failed"] == ["size.py:2: tracker 'linear' cannot file issues"]


def test_an_unreadable_key_store_files_nothing(tmp_path, notices):
    repo, chela = _repo(tmp_path), tmp_path / "chela"
    sweep_nightly.state_dir(chela).mkdir(parents=True)
    (sweep_nightly.state_dir(chela) / "filed.json").write_text("{torn")
    filer = Filer()
    res = _run(repo, chela, filer)
    assert (res["status"], filer.calls) == ("cannot_verify", [])
    assert "nothing filed" in res["why"]
    assert notices[-1][1].startswith("🧹🌙 nightly sweep (repo): CANNOT VERIFY — the filed-key "
                                     "store ")


def test_an_unreachable_origin_is_cannot_verify(tmp_path, notices):
    repo, chela = _repo(tmp_path), tmp_path / "chela"
    _git(repo, "remote", "set-url", "origin", str(tmp_path / "gone.git"))
    filer = Filer()
    res = _run(repo, chela, filer)
    assert (res["status"], filer.calls) == ("cannot_verify", [])
    assert res["why"].startswith("could not read origin/dev: `git fetch --quiet origin` failed")


def test_a_hung_git_is_cannot_verify(tmp_path, notices, monkeypatch):
    repo, chela = _repo(tmp_path), tmp_path / "chela"

    def hang(*a, **k):
        raise subprocess.TimeoutExpired(a[0], 120)
    monkeypatch.setattr(sweep_nightly.subprocess, "run", hang)
    res = _run(repo, chela, Filer())
    assert res["status"] == "cannot_verify" and "could not run" in res["why"]


def test_a_checkout_that_fails_is_cannot_verify(tmp_path, notices, monkeypatch):
    repo, chela = _repo(tmp_path), tmp_path / "chela"

    def boom(*a, **k):
        raise RuntimeError("disk full")
    monkeypatch.setattr("chela.worktree.detached_worktree", boom)
    res = _run(repo, chela, Filer())
    assert (res["status"], res["why"]) == ("cannot_verify",
                                           f"could not check out {res['head'][:12]}: disk full")


def test_a_checkout_that_cannot_be_removed_is_logged_not_fatal(tmp_path, notices, monkeypatch):
    repo, chela = _repo(tmp_path), tmp_path / "chela"

    def boom(*a, **k):
        raise RuntimeError("busy")
    monkeypatch.setattr("chela.worktree.remove_worktree", boom)
    assert _run(repo, chela, Filer())["filed"] == ["CMX-901"]


def test_a_red_baseline_is_cannot_verify_and_files_nothing(tmp_path, notices):
    prs = [({"size.py": WEAK, "tests/test_red.py": "def test_red():\n    assert False\n"},
            "PR red (#4)", None)]
    repo, chela = _repo(tmp_path, prs=prs), tmp_path / "chela"
    filer = Filer()
    res = _run(repo, chela, filer)
    assert (res["status"], filer.calls) == ("cannot_verify", [])
    assert "NOT GREEN" in res["why"]


def test_a_failing_blame_is_cannot_verify(tmp_path, notices, monkeypatch):
    repo, chela = _repo(tmp_path), tmp_path / "chela"
    real = sweep_nightly._git

    def git(cwd, *args, **k):
        if args[0] == "blame":
            raise sweep_nightly._Git("blame broke")
        return real(cwd, *args, **k)
    monkeypatch.setattr(sweep_nightly, "_git", git)
    res = _run(repo, chela, Filer())
    assert (res["status"], res["why"]) == ("cannot_verify",
                                           "could not blame the merged lines: blame broke")


def test_sites_that_cannot_be_listed_say_so_in_the_issue(tmp_path, notices, monkeypatch):
    repo, chela = _repo(tmp_path), tmp_path / "chela"
    real = sweep_nightly._git

    def git(cwd, *args, **k):
        if args[0] == "grep":
            raise sweep_nightly._Git("grep broke")
        return real(cwd, *args, **k)
    monkeypatch.setattr(sweep_nightly, "_git", git)
    filer = Filer()
    _run(repo, chela, filer)
    assert "- ⚠️ could not be listed (`git grep` failed) — check by hand" in filer.calls[0][1]


def test_another_running_sweep_holds_the_lock(tmp_path, notices):
    repo, chela = _repo(tmp_path), tmp_path / "chela"
    sweep_nightly.state_dir(chela).mkdir(parents=True)
    with open(sweep_nightly.state_dir(chela) / "lock", "a") as held:
        fcntl.flock(held, fcntl.LOCK_EX)
        filer = Filer()
        res = _run(repo, chela, filer)
    assert (res["status"], res["why"], filer.calls) == (
        "skipped", "another nightly sweep is already running", [])


def test_the_judge_off_skips(tmp_path, notices):
    repo, chela = _repo(tmp_path, extra="  enabled: false\n"), tmp_path / "chela"
    assert judge.judge_enabled(load_workflow(repo / "WORKFLOW.md")) is False
    res = _run(repo, chela, Filer())
    assert (res["status"], res["why"]) == (
        "skipped", "the judge is off for this workflow — nothing to mutate against")


def test_a_failing_inbox_never_fails_the_run(tmp_path, monkeypatch):
    repo, chela = _repo(tmp_path), tmp_path / "chela"
    chela.mkdir()
    (chela / "fleet-pause.json").write_text("{}")

    def boom(*a):
        raise RuntimeError("inbox down")
    monkeypatch.setattr("chela.inbox.enqueue", boom)
    assert _run(repo, chela, Filer())["status"] == "skipped"


# --- the budget, the sweep's `lines` --------------------------------------------------------


@pytest.mark.parametrize("sweep_cfg, judge_cfg, want", [
    ("  budget_seconds: 77\n  max_mutations: 3\n", "  sweep_budget_seconds: 50\n",
     (77.0, 3)),
    ("", "  sweep_budget_seconds: 50\n  sweep_max_mutations: 9\n", (50.0, 9)),
    ("", "", (sweep.DEFAULT_BUDGET_SECONDS, sweep.DEFAULT_MAX_MUTATIONS)),
])
def test_budget_precedence_sweep_over_judge_over_default(tmp_path, monkeypatch, notices,
                                                         sweep_cfg, judge_cfg, want):
    extra = judge_cfg + (f"sweep:\n{sweep_cfg}" if sweep_cfg else "")
    repo, chela = _repo(tmp_path, extra=extra), tmp_path / "chela"
    seen = {}

    def fake(worktree, base, test_cmd, **kw):
        seen.update(kw, test_cmd=test_cmd)
        return sweep.SweepReport()
    monkeypatch.setattr(sweep_nightly.sweep, "sweep", fake)
    assert _run(repo, chela, Filer())["status"] == "clean"
    assert (seen["budget"].seconds, seen["budget"].max_mutations) == want
    assert seen["timeout"] == 120 and seen["lines"] == {"size.py": {1, 2, 3, 4},
                                                        "other.py": {1, 2, 3, 4}}
    assert seen["test_cmd"] == f'"{sys.executable}" -m pytest -q -p no:cacheprovider tests'


def test_sweep_lines_mutates_exactly_those_lines_and_names_unsupported(tmp_path):
    repo = _repo(tmp_path)
    (repo / "x.go").write_text("package x\n")
    (repo / "gen").mkdir()
    rep = sweep.sweep(repo, "label", f'"{sys.executable}" -m pytest -q -p no:cacheprovider '
                      "tests", select_tests=False, provision=False,
                      lines={"size.py": {2}, "x.go": {1}, "tests/test_calc.py": {2}})
    assert {(r.mutation.file, r.mutation.line) for r in rep.results} == {("size.py", 2)}
    assert rep.unsupported == ["x.go"] and rep.base == "label"
    assert {r.mutation.kind for r in rep.survivors} == {"if-false", "compare-flip"}


# --- when ---------------------------------------------------------------------------------


def _wf(tmp_path: Path, sweep_yaml: str = ""):
    return parse_workflow(tmp_path / "WORKFLOW.md", _workflow_text(tmp_path, extra=sweep_yaml))


@pytest.mark.parametrize("yaml_text, want", [
    ("", (2, 0)),
    ("sweep:\n  nightly_at: '03:15'\n", (3, 15)),
    ("sweep:\n  nightly_at: '3:05'\n", (3, 5)),
    ("sweep:\n  nightly_at: 2:30\n", (2, 30)),          # YAML 1.1 sexagesimal → 150
    ("sweep:\n  nightly_at: '23:59'\n", (23, 59)),
    ("sweep:\n  nightly_at: 'off'\n", None),
    ("sweep:\n  nightly_at: false\n", None),
    ("sweep:\n  nightly_at: 'never'\n", None),
    ("sweep:\n  nightly_at: ' Off '\n", None),
    ("sweep:\n  enabled: false\n  nightly_at: '03:00'\n", None),
    ("sweep:\n  nightly_at: '24:00'\n", (2, 0)),
    ("sweep:\n  nightly_at: 'soon'\n", (2, 0)),
    ("sweep:\n  nightly_at: '03:15x'\n", (2, 0)),
    ("sweep:\n  nightly_at: true\n", (2, 0)),
    ("sweep:\n  nightly_at: 1440\n", (2, 0)),
])
def test_nightly_at(tmp_path, yaml_text, want):
    assert sweep_nightly.nightly_at(_wf(tmp_path, yaml_text)) == want


def test_due_once_a_day_after_the_hour(tmp_path):
    wf, chela = _wf(tmp_path, "sweep:\n  nightly_at: '02:00'\n"), tmp_path / "chela"
    assert not sweep_nightly.due(wf, datetime(2026, 10, 21, 1, 59), chela)
    assert sweep_nightly.due(wf, datetime(2026, 10, 21, 2, 0), chela)
    sweep_nightly.mark_started(wf, datetime(2026, 10, 21, 2, 0), chela)
    assert not sweep_nightly.due(wf, datetime(2026, 10, 21, 23, 0), chela)
    assert sweep_nightly.due(wf, datetime(2026, 10, 22, 2, 1), chela)
    assert not sweep_nightly.due(_wf(tmp_path, "sweep:\n  nightly_at: 'off'\n"),
                                 datetime(2026, 10, 22, 2, 1), chela)


def test_an_unreadable_day_record_is_not_due_and_is_rewritten_on_mark(tmp_path):
    wf, chela = _wf(tmp_path), tmp_path / "chela"
    sweep_nightly.state_dir(chela).mkdir(parents=True)
    (sweep_nightly.state_dir(chela) / "last.json").write_text("[]")
    assert not sweep_nightly.due(wf, datetime(2026, 10, 21, 3, 0), chela)
    sweep_nightly.mark_started(wf, datetime(2026, 10, 21, 3, 0), chela)
    assert json.loads((sweep_nightly.state_dir(chela) / "last.json").read_text()) == {
        str(wf.path): "2026-10-21"}


class _Child:
    def __init__(self, alive: bool):
        self.alive = alive

    def poll(self):
        return None if self.alive else 0


def test_daemon_tick_spawns_one_due_workflow_at_a_time(tmp_path, monkeypatch):
    chela = tmp_path / "chela"
    paths = []
    for name in ("a", "b"):
        (tmp_path / name).mkdir()
        p = tmp_path / name / "WORKFLOW.md"
        p.write_text(_workflow_text(tmp_path))
        paths.append(p)
    spawned = []
    monkeypatch.setattr(sweep_nightly, "spawn",
                        lambda p, d=None: spawned.append(p) or _Child(True))
    at = datetime(2026, 10, 21, 2, 30)
    child = sweep_nightly.daemon_tick(paths, None, at, chela)
    assert spawned == [paths[0]]
    assert sweep_nightly.daemon_tick(paths, child, at, chela) is child and len(spawned) == 1
    child.alive = False
    sweep_nightly.daemon_tick(paths, child, at, chela)
    assert spawned == [paths[0], paths[1]]
    sweep_nightly.daemon_tick(paths, _Child(False), at, chela)       # both done today
    assert len(spawned) == 2
    before = sweep_nightly.daemon_tick(paths, None, datetime(2026, 10, 22, 1, 0), chela)
    assert before is None and len(spawned) == 2


def test_daemon_tick_marks_the_day_before_it_spawns(tmp_path, monkeypatch):
    """A spawn that blows up has still used today: never a retry storm, never a second."""
    chela, p = tmp_path / "chela", tmp_path / "WORKFLOW.md"
    p.write_text(_workflow_text(tmp_path))
    seen = []

    def boom(path, d=None):
        seen.append(json.loads((sweep_nightly.state_dir(d) / "last.json").read_text()))
        raise OSError("no fork")
    monkeypatch.setattr(sweep_nightly, "spawn", boom)
    at = datetime(2026, 10, 21, 2, 30)
    with pytest.raises(OSError):
        sweep_nightly.daemon_tick([p], None, at, chela)
    assert seen == [{str(p): "2026-10-21"}]
    assert sweep_nightly.daemon_tick([p], None, at, chela) is None and len(seen) == 1


def test_summary_names_unrun_and_unsupported():
    res = {"workflow": "/r/repo/WORKFLOW.md", "status": "clean", "survivors": 0, "prs": ["a"],
           "mutations": 4, "unrun": 2, "unsupported": ["x.go", "y.rs"], "filed": [],
           "already_filed": 0, "failed": []}
    assert sweep_nightly.summary(res) == (
        "🧹🌙 nightly sweep (repo): clean — no survivors, nothing filed — 1 standard-tier "
        "PR(s), 4 mutation(s), 2 UNRUN (budget), 2 file(s) UNKNOWN (not mutatable)")


def test_daemon_tick_skips_a_workflow_that_does_not_parse(tmp_path, monkeypatch):
    bad = tmp_path / "WORKFLOW.md"
    bad.write_text("---\nproject_key: [\n---\n")
    monkeypatch.setattr(sweep_nightly, "spawn", lambda *a: pytest.fail("spawned"))
    assert sweep_nightly.daemon_tick([bad], None, datetime(2026, 10, 21, 3, 0),
                                     tmp_path / "chela") is None


def test_spawn_runs_the_cli_detached_inside_the_memory_slice(tmp_path, monkeypatch):
    seen = {}
    monkeypatch.setattr("chela.memcap.wrap_launch_cmd", lambda cmd: f"exec slice -- {cmd}")

    def popen(argv, **kw):
        seen.update(kw, argv=argv)
        return "child"
    monkeypatch.setattr(sweep_nightly.subprocess, "Popen", popen)
    wf_path = tmp_path / "my repo" / "WORKFLOW.md"
    assert sweep_nightly.spawn(wf_path, tmp_path / "chela") == "child"
    assert seen["argv"] == ["sh", "-c", f"exec slice -- {sys.executable} -m chela.main judge "
                                        f"nightly-sweep --workflow '{wf_path}'"]
    assert (seen["start_new_session"], seen["close_fds"], seen["stdin"],
            seen["stderr"]) == (True, True, subprocess.DEVNULL, subprocess.STDOUT)
    assert seen["stdout"].name == str(tmp_path / "chela" / "sweep-nightly" / "nightly.log")


def test_the_daemon_loop_calls_daemon_tick(monkeypatch):
    """🔴 WIRING — drives ONE real pass of ``cmd_run``'s loop. A source-substring check could
    not fail: dead-coding the call (``if False: nightly_child = ...``) keeps the text
    (defeat shape 01)."""
    from tests.test_context import _run_one_daemon_tick

    _run_one_daemon_tick(monkeypatch)
    monkeypatch.setattr(main_mod.ops, "spawn", lambda child: child)
    calls = []
    monkeypatch.setattr(main_mod.sweep_nightly, "daemon_tick",
                        lambda workflows, prev: calls.append((workflows, prev)) or prev)
    main_mod.cmd_run(argparse.Namespace())
    assert len(calls) == 1, "the daemon loop never called sweep_nightly.daemon_tick"
    assert calls[0][0] is main_mod.DISPATCH_WORKFLOWS and calls[0][1] is None


@pytest.mark.parametrize("status, code", [("filed", 1), ("cannot_verify", 2), ("clean", 0),
                                          ("skipped", 0)])
def test_cli_exit_codes(tmp_path, monkeypatch, capsys, status, code):
    p = tmp_path / "WORKFLOW.md"
    p.write_text(_workflow_text(tmp_path))
    monkeypatch.setattr(sweep_nightly, "run", lambda wf: {"status": status, "summary": "S"})
    with pytest.raises(SystemExit) as e:
        main_mod.cmd_judge_nightly_sweep(argparse.Namespace(workflow=str(p), json=False))
    assert e.value.code == code and capsys.readouterr().out == "S\n"


def test_cli_an_unreadable_workflow_exits_2(tmp_path, capsys):
    with pytest.raises(SystemExit) as e:
        main_mod.cmd_judge_nightly_sweep(argparse.Namespace(
            workflow=str(tmp_path / "missing.md"), json=False))
    assert e.value.code == 2


# --- keys, grouping -------------------------------------------------------------------------


def _mut(file="a.py", line=3, kind="if-false", before="    if x:", after="    if False and (x):"):
    return sweep.Mutation(file, line, kind, before, after)


def test_survivor_key_ignores_the_line_number_but_not_what_the_guard_is():
    k = sweep_nightly.survivor_key
    assert k("w", _mut(line=3)) == k("w", _mut(line=40))
    assert k("w", _mut()) == k("w", _mut(before="if x:", after="if False and (x):"))
    for other in (_mut(file="b.py"), _mut(kind="if-true"), _mut(before="    if y:"),
                  _mut(after="    if True or (x):")):
        assert k("w", other) != k("w", _mut())
    assert k("w2", _mut()) != k("w", _mut())


def test_one_group_per_guard_line():
    a, b, c = _mut(), _mut(kind="compare-flip", after="    if not x:"), _mut(line=9)
    assert sweep_nightly.group_survivors([a, b, c]) == [[a, b], [c]]


def test_tracker_filer_files_with_the_documented_labels(monkeypatch):
    got = {}

    class Src:
        def create_issue(self, title, body, labels=()):
            got.update(title=title, body=body, labels=labels)
            return {"identifier": "CMX-7"}
    monkeypatch.setattr("chela.sources.get_source", lambda wf: Src())
    assert sweep_nightly.tracker_filer(object())("T", "B") == "CMX-7"
    assert got == {"title": "T", "body": "B", "labels": ("risk:normal", "sweep")}
    monkeypatch.setattr("chela.sources.get_source", lambda wf: object())
    assert sweep_nightly.tracker_filer(object()) is None


# --- Linear: create_issue(labels=...) ---------------------------------------------------------


class LabelTeam:
    def __init__(self, labels, *, create_ok=True, fail_read=False, node_on_fail=False):
        self.labels, self.create_ok, self.fail_read = labels, create_ok, fail_read
        self.node_on_fail = node_on_fail
        self.calls: list[tuple[str, dict]] = []

    def __call__(self, query, variables):
        name = {linear.TEAM_STATES_QUERY: "states", linear.CREATE_ISSUE_MUTATION: "create",
                linear.LABELS_BY_NAME_QUERY: "labels",
                linear.CREATE_LABEL_MUTATION: "new_label"}[query]
        self.calls.append((name, json.loads(json.dumps(variables))))
        if name == "states":
            return {"teams": {"nodes": [{"id": "team-uuid", "states": {"nodes": [
                {"id": "st-todo", "name": "Todo", "type": "unstarted", "position": 0}]}}]}}
        if name == "labels":
            if self.fail_read:
                raise LinearError("network", "reset")
            return {"issueLabels": {"nodes": self.labels}}
        if name == "new_label":
            return {"issueLabelCreate": {"success": self.create_ok, "issueLabel": (
                {"id": f"new-{variables['input']['name']}", "name": variables["input"]["name"]}
                if self.create_ok or self.node_on_fail else None)}}
        return {"issueCreate": {"success": True, "issue": {
            "id": "i1", "identifier": "CMX-50", "title": variables["input"]["title"],
            "url": "u"}}}


def _source(tmp_path, transport):
    return LinearSource(_wf(tmp_path), transport=transport)


def test_create_issue_resolves_existing_labels_and_creates_a_missing_one(tmp_path):
    t = LabelTeam([{"id": "L-risk", "name": "risk:normal", "team": None},
                   {"id": "L-other-team", "name": "sweep", "team": {"key": "OPS"}}])
    out = _source(tmp_path, t).create_issue("T", "B", labels=("risk:normal", "sweep"))
    assert out["warnings"] == []
    assert ("new_label", {"input": {"name": "sweep", "teamId": "team-uuid"}}) in t.calls
    created = next(v for n, v in t.calls if n == "create")
    assert created["input"]["labelIds"] == ["L-risk", "new-sweep"]


def test_create_issue_uses_a_team_label_by_name(tmp_path):
    t = LabelTeam([{"id": "L-sweep", "name": "sweep", "team": {"key": "CMX"}}])
    _source(tmp_path, t).create_issue("T", "B", labels=("sweep",))
    assert [n for n, _ in t.calls] == ["states", "labels", "create"]
    assert next(v for n, v in t.calls if n == "create")["input"]["labelIds"] == ["L-sweep"]


def test_a_label_that_cannot_be_made_is_a_warning_not_a_lost_issue(tmp_path):
    t = LabelTeam([], create_ok=False)
    out = _source(tmp_path, t).create_issue("T", "B", labels=("sweep",))
    assert out["identifier"] == "CMX-50"
    assert out["warnings"] == ["label 'sweep' could not be created"]
    assert next(v for n, v in t.calls if n == "create")["input"]["labelIds"] == []


def test_a_label_create_that_says_no_is_not_used_even_with_a_node(tmp_path):
    t = LabelTeam([], create_ok=False, node_on_fail=True)
    out = _source(tmp_path, t).create_issue("T", "B", labels=("sweep",))
    assert out["warnings"] == ["label 'sweep' could not be created"]
    assert next(v for n, v in t.calls if n == "create")["input"]["labelIds"] == []


def test_an_unreadable_label_list_is_a_warning(tmp_path):
    t = LabelTeam([], fail_read=True)
    out = _source(tmp_path, t).create_issue("T", "B", labels=("sweep",))
    assert out["identifier"] == "CMX-50" and out["warnings"][0].startswith(
        "could not read labels sweep:")


def test_a_raising_label_create_is_a_warning(tmp_path):
    t = LabelTeam([])

    def call(query, variables):
        if query == linear.CREATE_LABEL_MUTATION:
            raise LinearError("graphql", "nope")
        return t(query, variables)
    out = _source(tmp_path, call).create_issue("T", "B", labels=("sweep",))
    assert out["warnings"][0].startswith("label 'sweep' could not be created: ")


def test_create_issue_without_labels_sends_no_label_ids(tmp_path):
    t = LabelTeam([])
    _source(tmp_path, t).create_issue("T", "B")
    assert "labelIds" not in next(v for n, v in t.calls if n == "create")["input"]
    assert [n for n, _ in t.calls] == ["states", "create"]


# --- review fixes: the catch-up window, a crash, the sites cap --------------------------------


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts).astimezone().isoformat()


def _covered(chela: Path, repo: Path, ts: float) -> None:
    sweep_nightly.state_dir(chela).mkdir(parents=True, exist_ok=True)
    (sweep_nightly.state_dir(chela) / "covered.json").write_text(json.dumps(
        {str((repo / "WORKFLOW.md").resolve()): ts}))


def test_a_night_that_could_not_look_is_caught_up_from_the_last_covered_run(tmp_path, notices):
    now = datetime.now().timestamp()
    old = [({"size.py": WEAK}, "PR from 2 days ago", _iso(now - 2 * 86400))]
    repo, chela = _repo(tmp_path, prs=old), tmp_path / "chela"
    _covered(chela, repo, now - 3 * 86400)
    filer = Filer()
    assert _run(repo, chela, filer, now=now)["filed"] == ["CMX-901"]


def test_catch_up_never_reaches_past_a_week(tmp_path, notices):
    now = datetime.now().timestamp()
    old = [({"size.py": WEAK}, "PR from 10 days ago", _iso(now - 10 * 86400))]
    repo, chela = _repo(tmp_path, prs=old), tmp_path / "chela"
    _covered(chela, repo, now - 30 * 86400)
    res = _run(repo, chela, Filer(), now=now)
    assert res["status"] == "skipped" and res["why"].startswith(
        "no standard-tier PR merged to origin/dev since ")


def _covered_record(chela: Path) -> dict:
    p = sweep_nightly.state_dir(chela) / "covered.json"
    return json.loads(p.read_text()) if p.exists() else {}


@pytest.mark.parametrize("case, recorded", [
    ("filed", True), ("no_standard", True), ("clean", True),
    ("failed", False), ("paused", False), ("cannot_verify", False),
    # CANNOT VERIFY *after* the commits were read: `covered` is set, so only the status
    # check keeps these nights from moving the window (a red baseline, a torn key store).
    ("red_baseline", False), ("torn_key_store", False),
])
def test_only_a_run_that_looked_moves_the_window(tmp_path, notices, case, recorded):
    prs = {"no_standard": [], "clean": [({"other.py": COVERED,
                                          "tests/test_other.py": COVERED_TEST}, "PR 2", None)],
           "red_baseline": [({"size.py": WEAK,
                              "tests/test_red.py": "def test_red():\n    assert False\n"},
                             "PR red (#4)", None)]}
    repo, chela = _repo(tmp_path, prs=prs.get(case)), tmp_path / "chela"
    chela.mkdir()
    if case == "paused":
        (chela / "fleet-pause.json").write_text("{}")
    if case == "cannot_verify":
        _git(repo, "remote", "set-url", "origin", str(tmp_path / "gone.git"))
    if case == "torn_key_store":
        sweep_nightly.state_dir(chela).mkdir(parents=True)
        (sweep_nightly.state_dir(chela) / "filed.json").write_text("{torn")
    now = datetime.now().timestamp() + 1
    res = _run(repo, chela, Filer(fail=RuntimeError("no")) if case == "failed" else Filer(),
               now=now)
    if case in ("red_baseline", "torn_key_store"):
        assert (res["status"], res["covered"], res["failed"]) == ("cannot_verify", True, [])
    assert (_covered_record(chela) == {str((repo / "WORKFLOW.md").resolve()): now}) is recorded


def test_a_crash_is_one_cannot_verify_notice(tmp_path, notices, monkeypatch):
    repo, chela = _repo(tmp_path), tmp_path / "chela"

    def boom(*a, **k):
        raise KeyError("surprise")
    monkeypatch.setattr(sweep_nightly, "merged_commits", boom)
    res = _run(repo, chela, Filer())
    assert (res["status"], res["why"]) == ("cannot_verify", "the sweep crashed: KeyError: 'surprise'")
    assert len(notices) == 1 and "CANNOT VERIFY" in notices[0][1]
    assert _covered_record(chela) == {}


@pytest.mark.parametrize("content", ["{torn", '{"x": true}'])
def test_window_start_is_24h_on_an_unreadable_or_odd_record(tmp_path, content):
    wf, chela = _wf(tmp_path), tmp_path / "chela"
    sweep_nightly.state_dir(chela).mkdir(parents=True)
    (sweep_nightly.state_dir(chela) / "covered.json").write_text(
        content.replace('"x"', json.dumps(str(wf.path))))
    assert sweep_nightly.window_start(wf, 1e9, chela) == 1e9 - 86400


def test_window_start_a_recent_record_does_not_shrink_the_24h(tmp_path):
    wf, chela = _wf(tmp_path), tmp_path / "chela"
    sweep_nightly.state_dir(chela).mkdir(parents=True)
    (sweep_nightly.state_dir(chela) / "covered.json").write_text(
        json.dumps({str(wf.path): 1e9 - 3600}))
    assert sweep_nightly.window_start(wf, 1e9, chela) == 1e9 - 86400


def test_the_issue_lists_at_most_100_sites_and_counts_the_rest():
    sites = [f"f{i}.py:1" for i in range(105)]
    _, body = sweep_nightly.render_issue([_mut(file="f0.py", line=1)], sites, "a" * 40, {})
    assert "- `f99.py:1`" in body and "- `f100.py:1`" not in body
    assert "- … and 5 more (`git grep -nF` the line)" in body
    _, body = sweep_nightly.render_issue([_mut(file="f0.py", line=1)], sites[:100], "a" * 40, {})
    assert "more (`git grep" not in body


# --- sweep survivors ----------------------------------------------------------------------


def test_a_merge_commit_brings_in_its_side_branch_lines(tmp_path, notices):
    """A non-squash merge: the guard blames to the SIDE commit, which the merge introduced."""
    repo = _repo(tmp_path, prs=[])
    _git(repo, "checkout", "-qb", "side")
    (repo / "size.py").write_text(WEAK)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "side work")
    _git(repo, "checkout", "-q", "dev")
    _git(repo, "merge", "-q", "--no-ff", "-m", "Merge PR #5", "side")
    _git(repo, "push", "-q", "origin", "dev")
    filer = Filer()
    res = _run(repo, tmp_path / "chela", filer)
    assert (res["status"], res["filed"]) == ("filed", ["CMX-901"])
    assert "Merge PR #5" in filer.calls[0][1]


def test_an_unset_nightly_at_is_the_default_without_a_warning(tmp_path, caplog):
    with caplog.at_level("WARNING", logger="chela.sweep_nightly"):
        assert sweep_nightly.nightly_at(_wf(tmp_path)) == (2, 0)
    assert caplog.records == []


def test_label_nodes_without_an_id_or_of_another_name_are_ignored(tmp_path):
    t = LabelTeam([{"name": "sweep"}, "junk", {"id": "L-x", "name": "other", "team": None}])
    out = _source(tmp_path, t).create_issue("T", "B", labels=("sweep",))
    assert out["warnings"] == []
    assert next(v for n, v in t.calls if n == "create")["input"]["labelIds"] == ["new-sweep"]


def test_same_shape_sites_are_exact_text_lines_of_mutatable_source(tmp_path):
    repo = _repo(tmp_path, prs=[({
        "size.py": WEAK,
        "near.py": "def f(x):\n    if x > 100:  # a comment makes it another shape\n        return 1\n",
        "tests/test_size.py": "def test_x():\n    x = 1\n    if x > 100:\n        pass\n",
        "NOTES.md": "    if x > 100:\n",
    }, "PR (#1)", None)])
    assert sweep_nightly.same_shape_sites(repo, "if x > 100:") == ["size.py:2"]


def test_no_survivor_never_reads_the_key_store(tmp_path, notices):
    """A clean battery is clean even when the key store is torn: it only matters to filing."""
    prs = [({"other.py": COVERED, "tests/test_other.py": COVERED_TEST}, "PR 2 (#2)", None)]
    repo, chela = _repo(tmp_path, prs=prs), tmp_path / "chela"
    sweep_nightly.state_dir(chela).mkdir(parents=True)
    (sweep_nightly.state_dir(chela) / "filed.json").write_text("{torn")
    assert _run(repo, chela, Filer())["status"] == "clean"
