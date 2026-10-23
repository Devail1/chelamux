"""⚖️🧹🌙 CMX-303/CMX-304 — the nightly sweep: when it runs, which merges it covers, the
battery, the survivor groups and their same-shape sites, the key store, and the daemon wiring.

Every test runs against a throwaway repo whose ``origin`` is a local bare repo, a temp
``CHELA_DIR`` and a fake :class:`~chela.sweep_nightly.Filer` — nothing talks to a tracker.
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
from chela.workflow import load_workflow, parse_workflow

OLD = "2020-01-01T00:00:00+00:00"

CALC = "def clamp(x, lo):\n    if x < lo:\n        return lo\n    return x\n"
CALC_TEST = ("from calc import clamp\n\n\ndef test_clamp():\n    assert clamp(-1, 0) == 0\n"
             "    assert clamp(5, 0) == 5\n")
# PR 1 — the ONE weak guard, of ONE category (if-false): no test reads is_big().
WEAK = 'def is_big(x):\n    if x:\n        return "big"\n    return "small"\n'
# A weak guard of TWO categories (if-false, compare-flip) whose line PR 2 also carries.
WEAK_TWO = 'def is_big(x):\n    if x > 100:\n        return "big"\n    return "small"\n'
# PR 2 — the same shape, covered: killed.
COVERED = "def big_enough(x):\n    if x > 100:\n        return 1\n    return 2\n"
COVERED_TEST = ("from other import big_enough\n\n\ndef test_big_enough():\n"
                "    assert big_enough(101) == 1\n    assert big_enough(100) == 2\n")
# A weak guard a NON-standard PR carries — judged (or human-gated) per PR, never swept here.
CORE_WEAK = "def tick(y):\n    if y > 7:\n        return 3\n    return 4\n"
WEAK_TITLE_KINDS = ["if-false"]


def _git(repo: Path, *args: str, date: str | None = None) -> str:
    env = dict(os.environ, **({"GIT_COMMITTER_DATE": date, "GIT_AUTHOR_DATE": date}
                              if date else {}))
    return subprocess.run(["git", "-C", str(repo), "-c", "user.email=t@t", "-c", "user.name=t",
                           *args], check=True, capture_output=True, text=True,
                          env=env).stdout.strip()


def _workflow_text(root: Path, *, extra: str = "", base: str = "dev") -> str:
    return (f"---\nproject_key: CMX\ntracker:\n  kind: linear\n  team: CMX\nworkspace:\n"
            f"  root: {root / 'wt'}\n  base_branch: {base}\njudge:\n"
            f"  test_cmd: '\"{sys.executable}\" -m pytest -q -p no:cacheprovider tests'\n"
            f"  suite_timeout_seconds: 120\n{extra}---\nbody\n")


def _commit(repo: Path, files: dict[str, str], msg: str, date: str | None = None,
            branch: str = "dev") -> str:
    for rel, body in files.items():
        (repo / rel).parent.mkdir(parents=True, exist_ok=True)
        (repo / rel).write_text(body)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", msg, date=date)
    _git(repo, "push", "-q", "origin", branch)
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
    """A tracker stand-in: files each group, returning ``CMX-901``, ``CMX-902`` …"""

    def __init__(self, fail: BaseException | None = None):
        self.calls: list[tuple[sweep_nightly.SurvivorGroup, sweep_nightly.FileContext]] = []
        self.fail = fail

    def __call__(self, group, ctx) -> str:
        self.calls.append((group, ctx))
        if self.fail is not None:
            raise self.fail
        return f"CMX-{900 + len(self.calls)}"

    @property
    def sites(self) -> list[str]:
        return [g.mutation.key for g, _ in self.calls]

    @property
    def kinds(self) -> list[str]:
        return [g.mutation.kind for g, _ in self.calls]


def _run(repo: Path, chela_dir: Path, filer, **kw) -> dict:
    return sweep_nightly.run(load_workflow(repo / "WORKFLOW.md"), chela_dir=chela_dir,
                             filer=filer, **kw)


def _store(chela: Path) -> dict:
    return json.loads((sweep_nightly.state_dir(chela) / "filed.json").read_text())


# --- the acceptance fixture ---------------------------------------------------------------


def test_two_standard_prs_one_weak_guard_files_exactly_one_group(tmp_path):
    repo, chela = _repo(tmp_path), tmp_path / "chela"
    filer = Filer()
    res = _run(repo, chela, filer)
    assert (res["status"], res["filed"], filer.sites) == ("survivors", ["CMX-901"],
                                                          ["size.py:2"])
    group, ctx = filer.calls[0]
    assert (group.file, group.mutation.kind, group.mutation.before.strip(),
            group.mutation.after.strip()) == ("size.py", "if-false", "if x:",
                                              "if False and (x):")
    assert [m.key for m in group.survivors] == ["size.py:2"] and group.sites == ("size.py:2",)
    assert group.key == sweep_nightly.category_key(str((repo / "WORKFLOW.md").resolve()),
                                                   group.mutation)
    assert res["groups"] == [{
        "file": "size.py", "key": group.key, "kind": "if-false", "target": "if x:",
        "mutation": "if-false: `if x:` → `if False and (x):`", "survived": ["size.py:2"],
        "sites": ["size.py:2"]}]
    head = _git(repo, "rev-parse", "HEAD")
    pr1 = _git(repo, "rev-parse", "HEAD~1")
    assert (ctx.head, ctx.ref, ctx.workflow, ctx.chela_dir) == (
        head, "origin/dev", (repo / "WORKFLOW.md").resolve(), chela)
    assert ctx.merged_in == f"`{pr1[:12]}` PR 1: is_big (#1)"
    # the filer runs while the checkout of `head` is still there (slice 2 greps it)
    assert ctx.worktree == (tmp_path / "wt" / "nightly-sweep").resolve()
    assert len(res["prs"]) == 2 and res["survivors"] == 1
    assert res["mutations"] == 4 and res["unrun"] == 0 and res["unsupported"] == []
    # the key store holds one key per surviving MUTATION, each naming its issue and site
    assert [v["issue"] for v in _store(chela)["keys"].values()] == ["CMX-901"]
    assert {v["site"] for v in _store(chela)["keys"].values()} == {"size.py:2"}
    # the checkout is thrown away, and the repo's own tree is untouched
    assert not (tmp_path / "wt" / "nightly-sweep").exists()
    assert _git(repo, "status", "--porcelain") == ""


def test_the_filer_sees_the_checkout_at_head(tmp_path):
    repo, chela = _repo(tmp_path), tmp_path / "chela"
    seen = {}

    def filer(group, ctx):
        seen["text"] = (ctx.worktree / "size.py").read_text()
        seen["head"] = _git(ctx.worktree, "rev-parse", "HEAD")
        return "CMX-1"
    res = _run(repo, chela, filer)
    assert seen == {"text": WEAK, "head": res["head"]}


def test_a_second_run_with_no_change_files_nothing(tmp_path):
    repo, chela = _repo(tmp_path), tmp_path / "chela"
    _run(repo, chela, Filer())
    again = Filer()
    res = _run(repo, chela, again)
    assert again.calls == [] and res["filed"] == [] and res["already_filed"] == 1
    assert (res["status"], res["survivors"]) == ("clean", 1)


def test_a_new_survivor_in_an_already_filed_category_is_filed(tmp_path):
    """Dedupe is per surviving MUTATION: a category filed for one site whose other site is
    new is filed again — only a group whose EVERY key is filed is skipped (a PARTIAL store,
    so `all` and `any` disagree — defeat shape 303b)."""
    twin = WEAK.replace("is_big", "is_huge")
    repo = _repo(tmp_path, prs=[({"size.py": WEAK, "huge.py": twin}, "PR 1 (#1)", None)])
    chela = tmp_path / "chela"
    _run(repo, chela, Filer())
    path = sweep_nightly.state_dir(chela) / "filed.json"
    store = _store(chela)
    assert sorted(v["site"] for v in store["keys"].values()) == ["huge.py:2", "huge.py:2"]
    store["keys"].pop(sorted(store["keys"])[0])
    path.write_text(json.dumps(store))
    again = Filer()
    res = _run(repo, chela, again)
    assert (res["filed"], res["already_filed"], len(again.calls)) == (["CMX-901"], 0, 1)
    assert [m.key for m in again.calls[0][0].survivors] == ["huge.py:2", "size.py:2"]
    assert len(_store(chela)["keys"]) == 2


def test_negative_control_without_the_key_store_the_same_survivor_is_filed_again(tmp_path):
    """RED control for the dedupe: it is the store, nothing else, that stops a re-file."""
    repo, chela = _repo(tmp_path), tmp_path / "chela"
    _run(repo, chela, Filer())
    (sweep_nightly.state_dir(chela) / "filed.json").unlink()
    again = Filer()
    assert _run(repo, chela, again)["filed"] == ["CMX-901"] and len(again.calls) == 1


def test_the_key_store_keeps_other_entries_it_did_not_write(tmp_path):
    repo, chela = _repo(tmp_path), tmp_path / "chela"
    sweep_nightly.state_dir(chela).mkdir(parents=True)
    (sweep_nightly.state_dir(chela) / "filed.json").write_text(json.dumps(
        {"keys": {"older": {"issue": "CMX-1"}}, "v": 1}))
    _run(repo, chela, Filer())
    store = _store(chela)
    assert store["v"] == 1 and store["keys"]["older"] == {"issue": "CMX-1"}
    assert len(store["keys"]) == 2


def test_a_daemon_restart_between_runs_still_files_nothing(tmp_path):
    """Run 2 is a NEW process (`chela judge nightly-sweep`), so only the on-disk store
    remembers run 1. It reaches the DEFAULT filer — a re-file there would show up as a
    `would_file` site and exit 1."""
    repo, chela = _repo(tmp_path), tmp_path / "chela"
    _run(repo, chela, Filer())
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(("CHELA_", "PYTEST_")) and k != "LINEAR_API_KEY"}
    env.update(CHELA_DIR=str(chela), CHELA_INBOX_ENABLED="false")
    out = subprocess.run([sys.executable, "-m", "chela.main", "judge", "nightly-sweep",
                          "--workflow", str(repo / "WORKFLOW.md"), "--json"],
                         env=env, capture_output=True, text=True, timeout=300)
    res = json.loads(out.stdout)
    assert (out.returncode, res["filed"], res["would_file"], res["already_filed"]) == (
        0, [], [], 1)


# --- the default filer: records what WOULD be filed, files nothing --------------------------


def test_the_default_filer_records_what_it_would_file_and_stores_no_key(tmp_path):
    repo, chela = _repo(tmp_path), tmp_path / "chela"
    res = _run(repo, chela, None)
    assert (res["status"], res["filed"], res["would_file"]) == ("survivors", [], ["size.py:2"])
    recs = [json.loads(x) for x in
            (sweep_nightly.state_dir(chela) / "would-file.jsonl").read_text().splitlines()]
    pr1 = _git(repo, "rev-parse", "HEAD~1")
    assert len(recs) == 1
    rec = recs[0]
    assert (rec["site"], rec["head"], rec["workflow"], rec["merged_in"]) == (
        "size.py:2", res["head"], str((repo / "WORKFLOW.md").resolve()),
        f"`{pr1[:12]}` PR 1: is_big (#1)")
    assert rec["mutations"] == [{"site": "size.py:2", "kind": "if-false", "before": "if x:",
                                 "after": "if False and (x):"}]
    assert (rec["file"], rec["sites"], rec["key"]) == ("size.py", ["size.py:2"],
                                                       res["groups"][0]["key"])
    assert [m["kind"] for m in rec["mutations"]] == WEAK_TITLE_KINDS
    # Nothing was filed, so nothing is remembered as filed: the real filer (slice 3) files it.
    assert not (sweep_nightly.state_dir(chela) / "filed.json").exists() or \
        _store(chela).get("keys") == {}
    # ...and a night that only recorded is still a night that looked: the window moves.
    assert sweep_nightly.window_start(load_workflow(repo / "WORKFLOW.md"), res["at"] + 10,
                                      chela) == res["at"] + 10 - 86400
    again = Filer()
    assert _run(repo, chela, again)["filed"] == ["CMX-901"]


def test_a_filer_returning_none_is_not_counted_filed(tmp_path):
    repo, chela = _repo(tmp_path), tmp_path / "chela"
    res = _run(repo, chela, lambda group, ctx: None)
    assert (res["filed"], res["would_file"], res["status"]) == ([], ["size.py:2"], "survivors")


# --- the pause, the judge, the lock -----------------------------------------------------------


@pytest.mark.parametrize("pause", ["not even json", "{}", None])
def test_a_fleet_pause_skips_the_run_and_its_absence_sweeps(tmp_path, pause):
    repo, chela = _repo(tmp_path), tmp_path / "chela"
    chela.mkdir()
    if pause is not None:
        (chela / "fleet-pause.json").write_text(pause)       # chela.fleet's file name
    filer = Filer()
    res = _run(repo, chela, filer)
    if pause is None:
        assert (res["status"], filer.sites) == ("survivors", ["size.py:2"])
    else:
        assert (res["status"], res["why"], filer.calls) == (
            "skipped", "a fleet pause (`chela dispatch --pause --all`) is active", [])
        assert "covered" not in res


def test_the_pause_file_is_the_one_chela_fleet_writes(tmp_path, monkeypatch):
    from chela import config, fleet

    monkeypatch.setattr(config, "CHELA_DIR", tmp_path)
    fleet.path().write_text("{}")
    assert sweep_nightly.paused(tmp_path) is True
    fleet.path().unlink()
    assert sweep_nightly.paused(tmp_path) is False
    # A DANGLING symlink still counts as paused (lexists, not exists): unreadable ≠ unpaused.
    fleet.path().symlink_to(tmp_path / "gone")
    assert sweep_nightly.paused(tmp_path) is True


def test_another_running_sweep_holds_the_lock(tmp_path):
    repo, chela = _repo(tmp_path), tmp_path / "chela"
    sweep_nightly.state_dir(chela).mkdir(parents=True)
    with open(sweep_nightly.state_dir(chela) / "lock", "a") as held:
        fcntl.flock(held, fcntl.LOCK_EX)
        filer = Filer()
        res = _run(repo, chela, filer)
    assert (res["status"], res["why"], filer.calls) == (
        "skipped", "another nightly sweep is already running", [])
    assert _run(repo, chela, Filer())["status"] == "survivors"     # released: it sweeps


def test_the_judge_off_skips(tmp_path):
    repo, chela = _repo(tmp_path, extra="  enabled: false\n"), tmp_path / "chela"
    assert judge.judge_enabled(load_workflow(repo / "WORKFLOW.md")) is False
    filer = Filer()
    res = _run(repo, chela, filer)
    assert (res["status"], res["why"], filer.calls) == (
        "skipped", "the judge is off for this workflow — nothing to mutate against", [])


# --- the tier filter ----------------------------------------------------------------------------


# One PR of each tier that is NOT standard, each carrying a weak guard of its own, so any
# filter looser than `== STANDARD` sweeps (and files) it (defeat shape 303c).
NOT_STANDARD_PRS = {
    "core": ({"chela/dispatcher.py": CORE_WEAK}, "core PR (#3)", None),
    "human": ({"plugin/hook.py": CORE_WEAK}, "always-human PR (#4)", None),
    "prose": ({"docs/notes.md": "    if y > 7:\n"}, "prose PR (#5)", None),
}


@pytest.mark.parametrize("tier", sorted(NOT_STANDARD_PRS))
def test_a_merged_pr_of_any_tier_but_standard_is_not_swept(tmp_path, tier):
    """CORE and HUMAN PRs were judged per PR, a PROSE one has nothing to mutate: none of them
    is swept — the run is SKIPPED (no battery at all), not swept and found clean."""
    repo, chela = _repo(tmp_path, prs=[NOT_STANDARD_PRS[tier]]), tmp_path / "chela"
    filer = Filer()
    res = _run(repo, chela, filer)
    assert (res["status"], filer.calls, res["prs"], res["not_swept"]) == (
        "skipped", [], [], {t: int(t == tier) for t in ("core", "human", "prose")})
    assert res["why"] == "no standard-tier PR merged to origin/dev in the last 24h"
    assert "mutations" not in res


def test_with_every_tier_merged_only_the_standard_pr_is_swept(tmp_path):
    prs = [NOT_STANDARD_PRS[t] for t in sorted(NOT_STANDARD_PRS)] + [
        ({"size.py": WEAK}, "PR 1: is_big (#1)", None)]
    repo, chela = _repo(tmp_path, prs=prs), tmp_path / "chela"
    filer = Filer()
    res = _run(repo, chela, filer)
    assert filer.sites == ["size.py:2"]
    assert [p.split(" ", 1)[1] for p in res["prs"]] == ["PR 1: is_big (#1)"]
    assert res["not_swept"] == {"core": 1, "human": 1, "prose": 1}


def test_negative_control_a_core_pr_read_as_standard_would_be_swept(tmp_path, monkeypatch):
    """RED control for the tier filter: the same core PR, classified STANDARD, IS swept."""
    repo, chela = _repo(tmp_path, prs=[NOT_STANDARD_PRS["core"]]), tmp_path / "chela"
    monkeypatch.setattr(risktier, "classify", lambda *a, **k: (risktier.STANDARD, None))
    filer = Filer()
    assert _run(repo, chela, filer)["status"] == "survivors"
    assert filer.sites == ["chela/dispatcher.py:2", "chela/dispatcher.py:2"]


def test_the_tier_reads_the_workflows_own_core_and_always_human_paths(tmp_path):
    """`merge.always_human` / `merge.core` in WORKFLOW.md are what classify reads — a repo's
    own core path is not swept."""
    extra = "merge:\n  core: ['size.py']\n"
    repo, chela = _repo(tmp_path, extra=extra, prs=[({"size.py": WEAK}, "PR (#1)", None)]), \
        tmp_path / "chela"
    res = _run(repo, chela, Filer())
    assert (res["status"], res["not_swept"]["core"]) == ("skipped", 1)


# --- merged_commits, blamed_lines -----------------------------------------------------------


def test_merged_commits_parses_every_field_of_each_commit(tmp_path):
    """The `git log` parser: sha, subject, tier, files and introduced, exactly — on commits
    whose fields all differ, so a swapped or mis-indexed field cannot pass."""
    prs = [NOT_STANDARD_PRS["human"], ({"size.py": WEAK, "b.py": "B = 1\n"},
                                       "PR 1: is_big | two files (#1)", None)]
    repo = _repo(tmp_path, prs=prs)
    shas = _git(repo, "log", "--format=%H", "-3").split()
    wf = load_workflow(repo / "WORKFLOW.md")
    since = datetime.fromisoformat(OLD).timestamp() + 1
    got = sweep_nightly.merged_commits(repo, shas[0], since, wf)
    assert got == [
        {"sha": shas[0], "subject": "PR 1: is_big | two files (#1)", "tier": "standard",
         "files": ["b.py", "size.py"], "introduced": [shas[0]]},
        {"sha": shas[1], "subject": "always-human PR (#4)", "tier": "human",
         "files": ["plugin/hook.py"], "introduced": [shas[1]]}]


def test_the_log_reads_the_whole_window_past_a_thousand_commits(tmp_path):
    """No commit cap: a window holding more than 1000 first-parent commits is read whole."""
    repo = _repo(tmp_path, prs=[({"size.py": WEAK}, "the oldest (#1)", None)])
    for i in range(1001):
        _git(repo, "commit", "-q", "--allow-empty", "-m", f"empty {i}")
    wf = load_workflow(repo / "WORKFLOW.md")
    since = datetime.fromisoformat(OLD).timestamp() + 1
    got = sweep_nightly.merged_commits(repo, "HEAD", since, wf)
    assert len(got) == 1002 and got[-1]["subject"] == "the oldest (#1)"


def test_the_log_is_bounded_by_the_window_start(tmp_path, monkeypatch):
    repo = _repo(tmp_path, prs=[])
    seen = []
    real = sweep_nightly._git
    monkeypatch.setattr(sweep_nightly, "_git", lambda cwd, *a, **k: seen.append(a) or
                        real(cwd, *a, **k))
    sweep_nightly.merged_commits(repo, "HEAD", 1234.9, load_workflow(repo / "WORKFLOW.md"))
    assert seen[0] == ("log", "--first-parent", "--since=@1234",
                       "--format=%H%x1f%ct%x1f%P%x1f%s", "HEAD", "--")


def test_a_log_line_outside_the_window_or_malformed_is_dropped(tmp_path, monkeypatch):
    """`--since` is git's filter; the parser checks each line's own committer time too, so a
    line git returns from before the window (clock skew, a `--since` git ignores) or a line
    with no time is never swept."""
    repo = _repo(tmp_path, prs=[({"size.py": WEAK}, "in window (#1)", None)])
    head = _git(repo, "rev-parse", "HEAD")
    parent = _git(repo, "rev-parse", "HEAD~1")
    ct = int(_git(repo, "log", "-1", "--format=%ct", head))
    old = "b" * 40
    real = sweep_nightly._git

    def git(cwd, *args, **k):
        if args[0] == "log":
            return (f"{head}\x1f{ct}\x1f{parent}\x1fin window (#1)\n"
                    f"{old}\x1f{ct - 10}\x1f{parent}\x1ftoo old (#0)\n"
                    f"{'c' * 40}\x1fnot-a-time\x1f{parent}\x1fno time\n")
        return real(cwd, *args, **k)
    monkeypatch.setattr(sweep_nightly, "_git", git)
    got = sweep_nightly.merged_commits(repo, head, ct - 5, load_workflow(repo / "WORKFLOW.md"))
    assert [c["subject"] for c in got] == ["in window (#1)"]


def test_the_window_edge_is_inclusive(tmp_path):
    repo = _repo(tmp_path, prs=[({"size.py": WEAK}, "PR (#1)", "2021-06-01T00:00:00+00:00")])
    wf = load_workflow(repo / "WORKFLOW.md")
    ct = datetime.fromisoformat("2021-06-01T00:00:00+00:00").timestamp()
    assert [c["subject"] for c in sweep_nightly.merged_commits(repo, "HEAD", ct, wf)] == [
        "PR (#1)"]
    assert sweep_nightly.merged_commits(repo, "HEAD", ct + 1, wf) == []


def test_a_root_commit_in_the_window_is_diffed_against_the_empty_tree(tmp_path):
    """A parentless commit has no ``first``: its files are the whole tree, not nothing."""
    wf = load_workflow(_repo(tmp_path / "w") / "WORKFLOW.md")
    root = tmp_path / "rootrepo"
    root.mkdir()
    _git(root, "init", "-q", "-b", "dev")
    (root / "a.py").write_text("A = 1\n")
    (root / "pkg").mkdir()
    (root / "pkg" / "b.py").write_text("B = 2\n")
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "root (#1)")
    got = sweep_nightly.merged_commits(root, "HEAD", 0, wf)
    assert len(got) == 1 and sorted(got[0]["files"]) == ["a.py", "pkg/b.py"]
    assert got[0]["introduced"] == [got[0]["sha"]] and got[0]["tier"] == "standard"


def test_the_empty_tree_constant_is_gits_empty_tree(tmp_path):
    _git(tmp_path, "init", "-q")
    assert _git(tmp_path, "hash-object", "-t", "tree", "/dev/null") == \
        "4b825dc642cb6eb9a060e54bf8d69288fbee4904" == sweep_nightly.EMPTY_TREE


def test_blamed_lines_are_the_final_line_numbers_and_their_origin(tmp_path):
    """A later (core) PR pushed PR 1's lines DOWN by two, so blame's original line (1-4)
    and final line (3-6) differ: the sweep must mutate the final ones, at HEAD."""
    prs = [({"size.py": WEAK}, "PR 1 (#1)", None),
           ({"size.py": "import os\n\n" + WEAK, "chela/dispatcher.py": "X = 1\n"},
            "core (#9)", None)]
    repo = _repo(tmp_path, prs=prs)
    head = _git(repo, "rev-parse", "HEAD")
    wf = load_workflow(repo / "WORKFLOW.md")
    commits = sweep_nightly.merged_commits(repo, head, 1, wf)
    pr1 = [c for c in commits if c["tier"] == risktier.STANDARD and c["sha"] != head]
    assert [c["subject"] for c in pr1] == ["PR 1 (#1)"]
    lines, origin = sweep_nightly.blamed_lines(repo, pr1)
    assert lines == {"size.py": {3, 4, 5, 6}}
    assert origin == {("size.py", n): pr1[0]["sha"] for n in (3, 4, 5, 6)}


@pytest.mark.parametrize("line, want", [
    ("a" * 40 + " 1 3 4", ("a" * 40, "3")),
    ("a" * 40 + " 12 30", ("a" * 40, "30")),
    ("a" * 40 + " 1 3 4\n", None),               # a trailing newline is not a header
    ("A" * 40 + " 1 3", None),                   # porcelain shas are lowercase
    ("a" * 39 + " 1 3", None),
    ("x" + "a" * 40 + " 1 3", None),
    ("author " + "a" * 40, None),
    ("\t" + "a" * 40 + " 1 3", None),            # file content, not a header
    ("a" * 40 + " 1 3 4 5", None),
])
def test_the_blame_header_regex(line, want):
    m = sweep_nightly._RE_BLAME.match(line)
    assert (m.groups() if m else None) == want


def test_a_file_a_standard_pr_deleted_is_not_blamed(tmp_path):
    """A deleted file is in the PR's diff but not at HEAD: it is left out, never a failed
    blame that turns the whole night into CANNOT VERIFY."""
    prs = [({"gone.py": "G = 1\n"}, "add gone (#1)", OLD.replace("2020", "2021")),
           ({"size.py": WEAK}, "PR 1: is_big (#2)", None)]
    repo = _repo(tmp_path, prs=prs)
    (repo / "gone.py").unlink()
    _commit(repo, {}, "drop gone (#3)")
    res = _run(repo, tmp_path / "chela", Filer())
    assert (res["status"], res["filed"]) == ("survivors", ["CMX-901"])


def test_an_unsupported_file_a_standard_pr_merged_is_reported_unknown(tmp_path):
    """Not mutatable is not clean: the run says the file is UNKNOWN."""
    prs = [({"other.py": COVERED, "tests/test_other.py": COVERED_TEST, "tool.go":
             "package tool\n\nfunc F(x int) bool { return x > 1 }\n"}, "PR 2 (#2)", None)]
    repo, chela = _repo(tmp_path, prs=prs), tmp_path / "chela"
    res = _run(repo, chela, Filer())
    assert (res["status"], res["unsupported"], res["survivors"]) == ("unknown", ["tool.go"], 0)
    assert res["why"] == "tool.go UNKNOWN — no mutator handles them, not clean"
    # it will never become mutatable by waiting, so it does not hold the window back
    assert _covered_record(chela) == {str((repo / "WORKFLOW.md").resolve()): res["at"]}


def test_unsupported_is_only_what_the_standard_prs_merged(tmp_path):
    """The other side: a CORE PR's unsupported file is not this run's to report."""
    prs = [({"other.py": COVERED, "tests/test_other.py": COVERED_TEST}, "PR 2 (#2)", None),
           ({"chela/dispatcher.py": "X = 1\n", "tool.go": "package tool\n"}, "core (#3)",
            None)]
    res = _run(_repo(tmp_path, prs=prs), tmp_path / "chela", Filer())
    assert (res["status"], res["unsupported"]) == ("clean", [])


def test_a_line_a_later_core_pr_rewrote_is_not_swept(tmp_path):
    """The guard now blames to the core PR (judged per PR), so it is not swept again."""
    prs = [({"size.py": WEAK}, "PR 1 (#1)", None),
           ({"size.py": WEAK.replace("if x:", "if x != 0:"), "chela/dispatcher.py": "X = 1\n"},
            "core rewrite (#9)", None)]
    filer = Filer()
    res = _run(_repo(tmp_path, prs=prs), tmp_path / "chela", filer)
    assert (res["status"], filer.calls, res["not_swept"]["core"]) == ("clean", [], 1)


def test_a_merge_commit_brings_in_its_side_branch_lines(tmp_path):
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
    assert (res["status"], res["filed"]) == ("survivors", ["CMX-901"])
    assert filer.calls[0][1].merged_in.endswith(" Merge PR #5")


def test_no_survivor_files_nothing_and_says_so(tmp_path):
    prs = [({"other.py": COVERED, "tests/test_other.py": COVERED_TEST}, "PR 2 (#2)", None)]
    filer = Filer()
    res = _run(_repo(tmp_path, prs=prs), tmp_path / "chela", filer)
    assert (res["status"], filer.calls, res["survivors"], res["why"]) == ("clean", [], 0, "")


def test_no_survivor_never_reads_the_key_store(tmp_path):
    """A clean battery is clean even when the key store is torn: it only matters to filing."""
    prs = [({"other.py": COVERED, "tests/test_other.py": COVERED_TEST}, "PR 2 (#2)", None)]
    repo, chela = _repo(tmp_path, prs=prs), tmp_path / "chela"
    sweep_nightly.state_dir(chela).mkdir(parents=True)
    (sweep_nightly.state_dir(chela) / "filed.json").write_text("{torn")
    assert _run(repo, chela, Filer())["status"] == "clean"


# --- the base branch and the fetch -----------------------------------------------------------


def test_a_non_default_base_branch_is_the_one_swept(tmp_path):
    """``workspace.base_branch: main`` reads ``origin/main``, not a hardcoded ``origin/dev``
    (every other fixture parks on ``dev``, the default — defeat shape 02)."""
    repo, chela = _repo(tmp_path), tmp_path / "chela"
    base = _git(repo, "rev-parse", "HEAD~2")
    _git(repo, "checkout", "-q", "-b", "main", base)
    main_only = "def main_only(x):\n    if x > 9:\n        return 1\n    return 2\n"
    sha = _commit(repo, {"mainonly.py": main_only}, "PR M: main only (#9)", branch="main")
    wf_path = repo / "WORKFLOW.md"
    wf_path.write_text(wf_path.read_text().replace("base_branch: dev", "base_branch: main"))
    filer = Filer()
    res = _run(repo, chela, filer)
    assert res["head"] == sha and filer.sites == ["mainonly.py:2", "mainonly.py:3",
                                              "mainonly.py:2"]
    assert res["prs"] == [f"{sha[:12]} PR M: main only (#9)"]
    assert filer.calls[0][1].ref == "origin/main"


@pytest.mark.parametrize("yaml_base, want", [("main", "origin/main"), ("dev", "origin/dev"),
                                             (None, "origin/dev")])
def test_base_ref_is_the_workflows_base_branch(tmp_path, yaml_base, want):
    text = _workflow_text(tmp_path, base=yaml_base or "dev")
    if yaml_base is None:
        text = text.replace("  base_branch: dev\n", "")
    assert sweep_nightly.base_ref(parse_workflow(tmp_path / "WORKFLOW.md", text)) == want


def test_the_sweep_fetches_the_base_so_a_tip_that_advanced_is_read(tmp_path):
    """origin/dev moves AFTER the local clone's last fetch: only a real fetch of the base
    branch lets the sweep see the new tip (defeat shape 303d)."""
    repo, chela = _repo(tmp_path), tmp_path / "chela"
    other = tmp_path / "other"
    subprocess.run(["git", "clone", "-q", str(tmp_path / "origin.git"), str(other)],
                   check=True, capture_output=True)
    (other / "late.py").write_text(WEAK.replace("is_big", "is_late"))
    _git(other, "add", "-A")
    _git(other, "commit", "-qm", "PR late (#7)")
    _git(other, "push", "-q", "origin", "dev")
    tip = _git(other, "rev-parse", "HEAD")
    assert _git(repo, "rev-parse", "origin/dev") != tip      # the local ref is stale
    filer = Filer()
    res = _run(repo, chela, filer)
    assert res["head"] == tip and "late.py:2" in filer.sites
    assert any("PR late (#7)" in p for p in res["prs"])


def test_every_git_call_runs_with_full_argv_cwd_and_timeout(tmp_path, monkeypatch):
    """The fetch names origin and the base branch; every git runs in the repo or the
    checkout, captured, with the timeout — pinned on the SUCCESS path, whole argv."""
    repo, chela = _repo(tmp_path), tmp_path / "chela"
    wf_path = repo / "WORKFLOW.md"
    wf_path.write_text(wf_path.read_text().replace("base_branch: dev", "base_branch: main"))
    _git(repo, "push", "-q", "origin", "dev:main")
    seen = []
    real = subprocess.run

    def spy(argv, **kw):
        if sys._getframe(1).f_globals["__name__"] == "chela.sweep_nightly":
            seen.append((argv, kw))
        return real(argv, **kw)
    monkeypatch.setattr(sweep_nightly.subprocess, "run", spy)
    _run(repo, chela, Filer())
    fetch = [(a, k) for a, k in seen if a[3:4] == ["fetch"]]
    assert fetch == [(["git", "-C", str(repo.resolve()), "fetch", "--quiet", "origin", "main"],
                      {"capture_output": True, "text": True, "errors": "replace",
                       "timeout": 120})]
    assert ["git", "-C", str(repo.resolve()), "rev-parse", "--verify",
            "origin/main^{commit}"] in [a for a, _ in seen]
    assert {k["timeout"] for _, k in seen} == {120}
    blame = [a for a, _ in seen if a[3:4] == ["blame"]]
    assert blame and all(a[2] == str((tmp_path / "wt" / "nightly-sweep").resolve())
                         and a[3:7] == ["blame", "--porcelain", "HEAD", "--"] for a in blame)
    grep = [(a, k) for a, k in seen if a[3:4] == ["grep"]]
    assert grep == [(["git", "-C", str((tmp_path / "wt" / "nightly-sweep").resolve()), "grep",
                      "-n", "-z", "-I", "--full-name", "--no-color", "--no-column", "-F", "-e",
                      "if x:", "--"],
                     {"capture_output": True, "text": True, "errors": "replace",
                      "timeout": 120})]


def test_an_unreachable_origin_is_cannot_verify(tmp_path):
    repo, chela = _repo(tmp_path), tmp_path / "chela"
    _git(repo, "remote", "set-url", "origin", str(tmp_path / "gone.git"))
    filer = Filer()
    res = _run(repo, chela, filer)
    assert (res["status"], filer.calls) == ("cannot_verify", [])
    assert res["why"].startswith("could not read origin/dev: `git fetch --quiet origin` failed "
                                 "(exit 128): ")


def test_a_base_branch_origin_does_not_have_is_cannot_verify(tmp_path):
    repo, chela = _repo(tmp_path), tmp_path / "chela"
    wf_path = repo / "WORKFLOW.md"
    wf_path.write_text(wf_path.read_text().replace("base_branch: dev", "base_branch: nope"))
    res = _run(repo, chela, Filer())
    assert res["status"] == "cannot_verify" and res["why"].startswith("could not read "
                                                                      "origin/nope: ")


@pytest.mark.parametrize("exc", [subprocess.TimeoutExpired(["git"], 120),
                                 FileNotFoundError("no git")])
def test_a_git_that_cannot_run_is_cannot_verify(tmp_path, monkeypatch, exc):
    repo, chela = _repo(tmp_path), tmp_path / "chela"

    def boom(*a, **k):
        raise exc
    monkeypatch.setattr(sweep_nightly.subprocess, "run", boom)
    res = _run(repo, chela, Filer())
    assert res["status"] == "cannot_verify"
    assert res["why"].startswith("could not read origin/dev: `git fetch --quiet origin` could "
                                 "not run: ")


def test_git_accepts_a_listed_nonzero_exit(tmp_path):
    _git(tmp_path, "init", "-q")
    assert sweep_nightly._git(tmp_path, "grep", "-n", "nothing-here", ok=(0, 1)) == ""
    with pytest.raises(sweep_nightly._Git, match=r"failed \(exit 1\)"):
        sweep_nightly._git(tmp_path, "grep", "-n", "nothing-here")


# --- failures fail closed ------------------------------------------------------------------


def test_a_refused_filing_is_not_recorded_and_is_filed_next_run(tmp_path):
    repo, chela = _repo(tmp_path), tmp_path / "chela"
    res = _run(repo, chela, Filer(fail=RuntimeError("tracker said no")))
    assert (res["status"], res["filed"]) == ("survivors", [])
    assert res["failed"] == ["size.py:2: tracker said no"]
    assert _run(repo, chela, Filer())["filed"] == ["CMX-901"]


def test_one_refused_filing_never_loses_the_others(tmp_path):
    second = "def is_tiny(x):\n    if x < 3:\n        return 1\n    return 2\n"
    repo, chela = _repo(tmp_path, prs=[({"size.py": WEAK, "tiny.py": second}, "PR (#1)",
                                        None)]), tmp_path / "chela"
    calls = []

    def filer(group, ctx):
        calls.append(group.mutation.key)
        if len(calls) == 1:
            raise RuntimeError("no")
        return "CMX-7"
    res = _run(repo, chela, filer)
    assert calls == ["size.py:2", "tiny.py:2", "tiny.py:3", "tiny.py:2"]
    assert (res["filed"], res["failed"]) == (["CMX-7"] * 3, ["size.py:2: no"])


def test_a_crash_between_two_filings_keeps_the_first_key(tmp_path):
    """The store is written per issue: a crash (not an Exception — nothing catches it) on the
    2nd filing leaves the 1st recorded, so the next run files all but the 1st."""
    second = "def is_tiny(x):\n    if x < 3:\n        return 1\n    return 2\n"
    prs = [({"size.py": WEAK, "tiny.py": second}, "PR 1 (#1)", None)]
    repo, chela = _repo(tmp_path, prs=prs), tmp_path / "chela"

    class Crash(Filer):
        def __call__(self, group, ctx):
            if self.calls:
                raise KeyboardInterrupt
            return super().__call__(group, ctx)
    crash = Crash()
    with pytest.raises(KeyboardInterrupt):
        _run(repo, chela, crash)
    store = _store(chela)
    assert {v["issue"] for v in store["keys"].values()} == {"CMX-901"}
    first_site = {v["site"] for v in store["keys"].values()}
    assert len(first_site) == 1 and first_site <= {"size.py:2", "tiny.py:2"}
    again = Filer()
    res = _run(repo, chela, again)
    site = first_site.pop()
    assert res["already_filed"] == 1 and again.calls and site not in again.sites


@pytest.mark.parametrize("content", ["{torn", "[]"])
def test_an_unreadable_key_store_files_nothing(tmp_path, content):
    repo, chela = _repo(tmp_path), tmp_path / "chela"
    sweep_nightly.state_dir(chela).mkdir(parents=True)
    (sweep_nightly.state_dir(chela) / "filed.json").write_text(content)
    filer = Filer()
    res = _run(repo, chela, filer)
    assert (res["status"], filer.calls) == ("cannot_verify", [])
    assert res["why"].startswith("the filed-key store ") and res["why"].endswith(
        " — nothing filed")


def test_a_key_store_that_is_a_directory_files_nothing(tmp_path):
    """An OSError that is not FileNotFoundError is unknown, never 'nothing filed yet'."""
    repo, chela = _repo(tmp_path), tmp_path / "chela"
    (sweep_nightly.state_dir(chela) / "filed.json").mkdir(parents=True)
    filer = Filer()
    res = _run(repo, chela, filer)
    assert (res["status"], filer.calls) == ("cannot_verify", [])
    assert "is unreadable" in res["why"]


def test_a_checkout_that_fails_is_cannot_verify(tmp_path, monkeypatch):
    repo, chela = _repo(tmp_path), tmp_path / "chela"

    def boom(*a, **k):
        raise RuntimeError("disk full")
    monkeypatch.setattr("chela.worktree.detached_worktree", boom)
    res = _run(repo, chela, Filer())
    assert (res["status"], res["why"]) == ("cannot_verify",
                                           f"could not check out {res['head'][:12]}: disk full")


def test_a_checkout_that_cannot_be_removed_is_logged_not_fatal(tmp_path, monkeypatch, caplog):
    repo, chela = _repo(tmp_path), tmp_path / "chela"

    def boom(*a, **k):
        raise RuntimeError("busy")
    monkeypatch.setattr("chela.worktree.remove_worktree", boom)
    with caplog.at_level("ERROR", logger="chela.sweep_nightly"):
        assert _run(repo, chela, Filer())["filed"] == ["CMX-901"]
    assert any("could not remove" in r.getMessage() for r in caplog.records)


def test_a_red_baseline_is_cannot_verify_and_files_nothing(tmp_path):
    prs = [({"size.py": WEAK, "tests/test_red.py": "def test_red():\n    assert False\n"},
            "PR red (#4)", None)]
    filer = Filer()
    res = _run(_repo(tmp_path, prs=prs), tmp_path / "chela", filer)
    assert (res["status"], filer.calls) == ("cannot_verify", [])
    assert "NOT GREEN" in res["why"]


def test_a_failing_blame_is_cannot_verify(tmp_path, monkeypatch):
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


def test_a_crash_is_one_cannot_verify_result_and_moves_no_window(tmp_path, monkeypatch):
    repo, chela = _repo(tmp_path), tmp_path / "chela"

    def boom(*a, **k):
        raise KeyError("surprise")
    monkeypatch.setattr(sweep_nightly, "merged_commits", boom)
    res = _run(repo, chela, Filer())
    assert (res["status"], res["why"]) == ("cannot_verify",
                                           "the sweep crashed: KeyError: 'surprise'")
    assert _covered_record(chela) == {}


# --- the budget, the battery --------------------------------------------------------------------


@pytest.mark.parametrize("sweep_cfg, judge_cfg, want", [
    ("  budget_seconds: 77\n  max_mutations: 3\n", "  sweep_budget_seconds: 50\n",
     (77.0, 3, True)),
    ("", "  sweep_budget_seconds: 50\n  sweep_max_mutations: 9\n  select_tests: false\n",
     (50.0, 9, False)),
    ("", "", (900.0, 60, True)),
])
def test_the_battery_reads_its_budget_and_suite_from_the_workflow(tmp_path, monkeypatch,
                                                                   sweep_cfg, judge_cfg, want):
    """sweep.* over judge.sweep_* over the default (900 s, 60); the judge's test_cmd,
    timeout and select_tests."""
    extra = judge_cfg + (f"sweep:\n{sweep_cfg}" if sweep_cfg else "")
    repo, chela = _repo(tmp_path, extra=extra), tmp_path / "chela"
    seen = {}

    def fake(worktree, base, test_cmd, **kw):
        seen.update(kw, test_cmd=test_cmd, worktree=worktree, base=base)
        return sweep.SweepReport()
    monkeypatch.setattr(sweep_nightly.sweep, "sweep", fake)
    res = _run(repo, chela, Filer())
    assert res["status"] == "clean"
    assert (seen["budget"].seconds, seen["budget"].max_mutations, seen["select_tests"]) == want
    assert seen["timeout"] == 120 and seen["lines"] == {"size.py": {1, 2, 3, 4},
                                                        "other.py": {1, 2, 3, 4}}
    assert seen["test_cmd"] == f'"{sys.executable}" -m pytest -q -p no:cacheprovider tests'
    assert seen["worktree"] == (tmp_path / "wt" / "nightly-sweep").resolve()
    assert seen["base"] == f"origin/dev@{res['head'][:12]} (standard-tier lines)"


def test_a_budget_overflow_is_counted_unrun(tmp_path):
    """The real battery, capped at one mutation: the rest are UNRUN, and the result says how
    many (round-4 survivor of #819: `unrun=0` must go red)."""
    repo, chela = _repo(tmp_path, extra="sweep:\n  max_mutations: 1\n"), tmp_path / "chela"
    res = _run(repo, chela, Filer())
    assert (res["mutations"], res["unrun"]) == (4, 3)
    # ...and lines never measured are not covered: the window stays where it was.
    assert _covered_record(chela) == {}


def test_a_battery_with_nothing_unrun_moves_the_window(tmp_path):
    repo, chela = _repo(tmp_path, extra="sweep:\n  max_mutations: 4\n"), tmp_path / "chela"
    res = _run(repo, chela, Filer())
    assert (res["mutations"], res["unrun"]) == (4, 0)
    assert _covered_record(chela) == {str((repo / "WORKFLOW.md").resolve()): res["at"]}


def test_sweep_lines_mutates_exactly_those_lines_and_names_unsupported(tmp_path):
    repo = _repo(tmp_path)
    (repo / "x.go").write_text("package x\n")
    rep = sweep.sweep(repo, "label", f'"{sys.executable}" -m pytest -q -p no:cacheprovider '
                      "tests", select_tests=False, provision=False,
                      lines={"size.py": {2}, "x.go": {1}, "tests/test_calc.py": {2}})
    assert {(r.mutation.file, r.mutation.line) for r in rep.results} == {("size.py", 2)}
    assert rep.unsupported == ["x.go"] and rep.base == "label"
    assert {r.mutation.kind for r in rep.survivors} == {"if-false"}
    assert not any("generated" in n for n in rep.notes)


def test_sweep_lines_notes_a_generated_file_it_skips(tmp_path):
    repo = _repo(tmp_path)
    (repo / "vendor.min.js").write_text("if (x > 1) { y(); }\n")
    rep = sweep.sweep(repo, "label", "true", select_tests=False, provision=False,
                      lines={"vendor.min.js": {1}})
    assert rep.results == [] and any("generated" in n for n in rep.notes)


def test_sweep_without_lines_still_sweeps_the_branch_diff(tmp_path):
    """The other side of `lines`: the judge's own sweep (no `lines`) reads the diff vs base."""
    repo = _repo(tmp_path, prs=[])
    _git(repo, "checkout", "-qb", "feat")
    (repo / "size.py").write_text(WEAK)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "feat")
    rep = sweep.sweep(repo, "dev", f'"{sys.executable}" -m pytest -q -p no:cacheprovider tests',
                      select_tests=False, provision=False)
    assert {(r.mutation.file, r.mutation.line) for r in rep.results} == {("size.py", 2)}


# --- when ---------------------------------------------------------------------------------


def _wf(tmp_path: Path, sweep_yaml: str = ""):
    return parse_workflow(tmp_path / "WORKFLOW.md", _workflow_text(tmp_path, extra=sweep_yaml))


@pytest.mark.parametrize("yaml_text, want", [
    ("", (2, 0)),
    ("sweep:\n  nightly_at: '03:15'\n", (3, 15)),
    ("sweep:\n  nightly_at: '3:05'\n", (3, 5)),
    ("sweep:\n  nightly_at: 2:30\n", (2, 30)),          # YAML 1.1 sexagesimal → 150
    ("sweep:\n  nightly_at: 0\n", (0, 0)),
    ("sweep:\n  nightly_at: 1439\n", (23, 59)),
    ("sweep:\n  nightly_at: '23:59'\n", (23, 59)),
    ("sweep:\n  nightly_at: '00:00'\n", (0, 0)),
    ("sweep:\n  nightly_at: ' 04:10 '\n", (4, 10)),
    ("sweep:\n  nightly_at: 'off'\n", None),
    ("sweep:\n  nightly_at: false\n", None),
    ("sweep:\n  nightly_at: 'never'\n", None),
    ("sweep:\n  nightly_at: ' Off '\n", None),
    ("sweep:\n  enabled: false\n  nightly_at: '03:00'\n", None),
    ("sweep:\n  enabled: true\n  nightly_at: '03:00'\n", (3, 0)),
    ("sweep:\n  nightly_at: '24:00'\n", (2, 0)),
    ("sweep:\n  nightly_at: '03:60'\n", (2, 0)),
    ("sweep:\n  nightly_at: '003:15'\n", (2, 0)),
    ("sweep:\n  nightly_at: 'x03:15'\n", (2, 0)),
    ("sweep:\n  nightly_at: 'soon'\n", (2, 0)),
    ("sweep:\n  nightly_at: '03:15x'\n", (2, 0)),
    ("sweep:\n  nightly_at: \"03:15\\n04:00\"\n", (2, 0)),
    ("sweep:\n  nightly_at: true\n", (2, 0)),
    ("sweep:\n  nightly_at: 1440\n", (2, 0)),
    ("sweep:\n  nightly_at: -1\n", (2, 0)),
])
def test_nightly_at(tmp_path, yaml_text, want):
    assert sweep_nightly.nightly_at(_wf(tmp_path, yaml_text)) == want


def test_a_malformed_nightly_at_warns_and_an_unset_one_does_not(tmp_path, caplog):
    with caplog.at_level("WARNING", logger="chela.sweep_nightly"):
        assert sweep_nightly.nightly_at(_wf(tmp_path)) == (2, 0)
    assert caplog.records == []
    with caplog.at_level("WARNING", logger="chela.sweep_nightly"):
        assert sweep_nightly.nightly_at(_wf(tmp_path, "sweep:\n  nightly_at: soon\n")) == (2, 0)
    assert [r.getMessage().split(": ", 1)[1] for r in caplog.records] == [
        "sweep.nightly_at 'soon' is not HH:MM — using 02:00"]


def test_due_once_a_day_after_the_hour(tmp_path):
    wf, chela = _wf(tmp_path, "sweep:\n  nightly_at: '02:30'\n"), tmp_path / "chela"
    assert not sweep_nightly.due(wf, datetime(2026, 10, 21, 2, 29), chela)
    assert not sweep_nightly.due(wf, datetime(2026, 10, 21, 1, 45), chela)
    assert sweep_nightly.due(wf, datetime(2026, 10, 21, 2, 30), chela)
    assert sweep_nightly.due(wf, datetime(2026, 10, 21, 3, 0), chela)
    sweep_nightly.mark_started(wf, datetime(2026, 10, 21, 2, 30), chela)
    assert not sweep_nightly.due(wf, datetime(2026, 10, 21, 23, 0), chela)
    assert sweep_nightly.due(wf, datetime(2026, 10, 22, 2, 31), chela)
    assert not sweep_nightly.due(_wf(tmp_path, "sweep:\n  nightly_at: 'off'\n"),
                                 datetime(2026, 10, 22, 2, 31), chela)


def test_the_day_record_is_per_workflow(tmp_path):
    chela = tmp_path / "chela"
    (tmp_path / "b").mkdir()
    a = _wf(tmp_path)
    b = parse_workflow(tmp_path / "b" / "WORKFLOW.md", _workflow_text(tmp_path))
    at = datetime(2026, 10, 21, 3, 0)
    sweep_nightly.mark_started(a, at, chela)
    assert not sweep_nightly.due(a, at, chela) and sweep_nightly.due(b, at, chela)
    sweep_nightly.mark_started(b, at, chela)
    assert json.loads((sweep_nightly.state_dir(chela) / "last.json").read_text()) == {
        str(a.path): "2026-10-21", str(b.path): "2026-10-21"}


@pytest.mark.parametrize("make", [lambda p: p.write_text("[]"), lambda p: p.write_text("{x"),
                                  lambda p: p.mkdir()])
def test_an_unreadable_day_record_is_not_due_and_is_rewritten_on_mark(tmp_path, make):
    wf, chela = _wf(tmp_path), tmp_path / "chela"
    sweep_nightly.state_dir(chela).mkdir(parents=True)
    path = sweep_nightly.state_dir(chela) / "last.json"
    make(path)
    assert not sweep_nightly.due(wf, datetime(2026, 10, 21, 3, 0), chela)
    if path.is_dir():
        path.rmdir()
    sweep_nightly.mark_started(wf, datetime(2026, 10, 21, 3, 0), chela)
    assert json.loads(path.read_text()) == {str(wf.path): "2026-10-21"}


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
                        lambda p, d=None: spawned.append((p, d)) or _Child(True))
    at = datetime(2026, 10, 21, 2, 30)
    child = sweep_nightly.daemon_tick(paths, None, at, chela)
    assert spawned == [(paths[0], chela)]
    assert sweep_nightly.daemon_tick(paths, child, at, chela) is child and len(spawned) == 1
    child.alive = False
    second = sweep_nightly.daemon_tick(paths, child, at, chela)
    assert spawned == [(paths[0], chela), (paths[1], chela)] and second is not child
    done = _Child(False)
    assert sweep_nightly.daemon_tick(paths, done, at, chela) is done       # both done today
    assert len(spawned) == 2
    before = sweep_nightly.daemon_tick(paths, None, datetime(2026, 10, 22, 1, 0), chela)
    assert before is None and len(spawned) == 2


@pytest.mark.parametrize("pause", [False, True])
def test_daemon_tick_spawns_a_paused_night_whose_run_then_skips(tmp_path, monkeypatch, pause):
    """The pause is read by the RUN (the child), not the tick: a paused night is spawned,
    skips, and does not move the covered window — so the next night catches it up."""
    repo, chela = _repo(tmp_path), tmp_path / "chela"
    chela.mkdir()
    if pause:
        (chela / "fleet-pause.json").write_text("{}")
    ran = []
    monkeypatch.setattr(sweep_nightly, "spawn", lambda p, d=None: ran.append(
        _run(repo, d, Filer())) or _Child(False))
    sweep_nightly.daemon_tick([repo / "WORKFLOW.md"], None, datetime.now().replace(
        hour=23, minute=59), chela)
    assert [r["status"] for r in ran] == (["skipped"] if pause else ["survivors"])
    assert bool(_covered_record(chela)) is not pause


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


def test_daemon_tick_skips_a_workflow_that_does_not_parse(tmp_path, monkeypatch):
    bad = tmp_path / "bad" / "WORKFLOW.md"
    bad.parent.mkdir()
    bad.write_text("---\nproject_key: [\n---\n")
    good = tmp_path / "WORKFLOW.md"
    good.write_text(_workflow_text(tmp_path))
    spawned = []
    monkeypatch.setattr(sweep_nightly, "spawn", lambda p, d=None: spawned.append(p) or "child")
    assert sweep_nightly.daemon_tick([bad, good], None, datetime(2026, 10, 21, 3, 0),
                                     tmp_path / "chela") == "child"
    assert spawned == [good]


def test_daemon_tick_with_nightly_at_off_never_spawns(tmp_path, monkeypatch):
    p = tmp_path / "WORKFLOW.md"
    p.write_text(_workflow_text(tmp_path, extra="sweep:\n  nightly_at: 'off'\n"))
    monkeypatch.setattr(sweep_nightly, "spawn", lambda *a: pytest.fail("spawned"))
    assert sweep_nightly.daemon_tick([p], None, datetime(2026, 10, 21, 23, 59),
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
    assert set(seen) == {"argv", "stdin", "stdout", "stderr", "start_new_session", "close_fds"}


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


def _quiet_loop(monkeypatch, n: int) -> None:
    """``n`` real passes of ``cmd_run``'s loop with every other subsystem inert."""
    from tests.test_doctor_notify import _run_daemon_ticks

    _run_daemon_ticks(monkeypatch, n=n)
    monkeypatch.setattr(main_mod.doctor, "check_and_notify", lambda red: red)
    monkeypatch.setattr(main_mod.ops, "spawn", lambda child: child)


def test_the_daemon_loop_hands_each_tick_the_child_the_last_one_returned(monkeypatch):
    """🔴 WIRING (round-1 survivor of #838) — a single tick cannot see the loop DISCARDING
    ``daemon_tick``'s return (a bare ``sweep_nightly.daemon_tick(...)`` statement): tick 1
    looks the same either way. Over three ticks, each must be handed what the last returned
    (defeat shape 328)."""
    _quiet_loop(monkeypatch, n=3)
    prevs, children = [], iter(["child-1", "child-2", "child-3"])
    monkeypatch.setattr(main_mod.sweep_nightly, "daemon_tick",
                        lambda workflows, prev: prevs.append(prev) or next(children))
    main_mod.cmd_run(argparse.Namespace())
    assert prevs == [None, "child-1", "child-2"]


def test_the_daemon_loop_never_starts_a_second_sweep_while_one_runs(tmp_path, monkeypatch):
    """The behaviour the carry exists for, through the REAL ``daemon_tick``: a workflow due on
    every tick, a child that never exits — three ticks, ONE spawn. Dropping the loop's
    assignment re-hands ``None`` each tick and spawns three."""
    _quiet_loop(monkeypatch, n=3)
    p = tmp_path / "WORKFLOW.md"
    p.write_text(_workflow_text(tmp_path))
    # The dispatcher reads DISPATCH_WORKFLOWS too, so only the sweep is pointed at `p`.
    real = sweep_nightly.daemon_tick
    monkeypatch.setattr(sweep_nightly, "daemon_tick", lambda workflows, prev: real([p], prev))
    monkeypatch.setattr(sweep_nightly, "due", lambda wf, now, d=None: True)
    monkeypatch.setattr(sweep_nightly, "mark_started", lambda wf, now, d=None: None)
    spawned = []
    monkeypatch.setattr(sweep_nightly, "spawn",
                        lambda path, d=None: spawned.append(path) or _Child(True))
    main_mod.cmd_run(argparse.Namespace())
    assert spawned == [p]
    # The other side: a child that has EXITED is replaced on the very next tick.
    _quiet_loop(monkeypatch, n=3)
    spawned.clear()
    monkeypatch.setattr(sweep_nightly, "spawn",
                        lambda path, d=None: spawned.append(path) or _Child(False))
    main_mod.cmd_run(argparse.Namespace())
    assert spawned == [p, p, p]


def test_a_raising_daemon_tick_is_logged_and_the_loop_goes_on(monkeypatch, caplog):
    from tests.test_context import _run_one_daemon_tick

    _run_one_daemon_tick(monkeypatch)
    monkeypatch.setattr(main_mod.ops, "spawn", lambda child: child)

    def boom(workflows, prev):
        raise RuntimeError("no fork")
    monkeypatch.setattr(main_mod.sweep_nightly, "daemon_tick", boom)
    with caplog.at_level("ERROR"):
        main_mod.cmd_run(argparse.Namespace())
    assert any(r.getMessage() == "Nightly sweep spawn failed" for r in caplog.records)


@pytest.mark.parametrize("status, why, code, line", [
    ("survivors", "", 1, "nightly sweep: survivors"),
    ("unrun", "2 UNRUN", 2, "nightly sweep: unrun — 2 UNRUN"),
    ("unknown", "x.go", 2, "nightly sweep: unknown — x.go"),
    ("cannot_verify", "fetch broke", 2, "nightly sweep: cannot_verify — fetch broke"),
    ("clean", "", 0, "nightly sweep: clean"),
    ("skipped", "paused", 0, "nightly sweep: skipped — paused")])
def test_cli_exit_codes(tmp_path, monkeypatch, capsys, status, why, code, line):
    p = tmp_path / "WORKFLOW.md"
    p.write_text(_workflow_text(tmp_path))
    got = []
    monkeypatch.setattr(sweep_nightly, "run", lambda wf: got.append(wf.path) or {
        "status": status, "why": why})
    with pytest.raises(SystemExit) as e:
        main_mod.cmd_judge_nightly_sweep(argparse.Namespace(workflow=str(p), json=False))
    assert e.value.code == code and capsys.readouterr().out == line + "\n"
    assert got == [p.resolve()]


def test_cli_json_prints_the_whole_result(tmp_path, monkeypatch, capsys):
    p = tmp_path / "WORKFLOW.md"
    p.write_text(_workflow_text(tmp_path))
    monkeypatch.setattr(sweep_nightly, "run", lambda wf: {"status": "clean", "x": [1]})
    with pytest.raises(SystemExit) as e:
        main_mod.cmd_judge_nightly_sweep(argparse.Namespace(workflow=str(p), json=True))
    assert e.value.code == 0 and json.loads(capsys.readouterr().out) == {"status": "clean",
                                                                         "x": [1]}


def test_cli_an_unreadable_workflow_exits_2(tmp_path, capsys):
    with pytest.raises(SystemExit) as e:
        main_mod.cmd_judge_nightly_sweep(argparse.Namespace(
            workflow=str(tmp_path / "missing.md"), json=False))
    assert e.value.code == 2
    assert capsys.readouterr().err.startswith(f"nightly-sweep: {tmp_path / 'missing.md'} could "
                                              "not be read: ")


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
    assert len(k("w", _mut())) == 24 and int(k("w", _mut()), 16) >= 0


def test_one_group_per_category_not_per_line():
    """Shape + target: two kinds on ONE line are two groups; one kind on the same stripped
    line at two sites (two files, two indents) is ONE group; another target is another."""
    a, b = _mut(), _mut(kind="compare-flip", after="    if not x:")
    c, d = _mut(file="b.py", line=9, before="if x:", after="if False and (x):"), _mut(line=9)
    e = _mut(line=12, before="    if y:", after="    if False and (y):")
    assert sweep_nightly.group_survivors([a, b, c, d, e]) == [[a, c, d], [b], [e]]


def test_category_key_is_shape_and_target_only():
    k = sweep_nightly.category_key
    assert k("w", _mut()) == k("w", _mut(file="b.py", line=40, before="if x:",
                                         after="if True or (x):"))
    for other in (_mut(kind="if-true"), _mut(before="    if y:")):
        assert k("w", other) != k("w", _mut())
    assert k("w2", _mut()) != k("w", _mut())
    assert len(k("w", _mut())) == 24 and k("w", _mut()) != sweep_nightly.survivor_key(
        "w", _mut())


# --- same-shape sites ----------------------------------------------------------------------


def test_parse_grep_pins_every_field_where_they_differ():
    """path, line and body are three DIFFERENT values per record — a parser that took the
    wrong column (or split a path holding ':') names the wrong site."""
    out = ("pkg/a:b.py\0" "17\0" "    if x:\n"
           "c.py\0" "4\0" "if x:   \n"
           "d.py\0" "9\0" "    if x: y()\n"            # contains the text, is not the shape
           "tests/test_e.py\0" "2\0" "if x:\n"           # a test measures, is not a site
           "README.md\0" "3\0" "if x:\n"                 # prose, not mutatable
           "f.py\0" "x1\0" "if x:\n"                     # not a line number
           "g.py\0" "5\n"                                # torn record
           "")
    assert sweep_nightly.parse_grep(out, "if x:") == ["pkg/a:b.py:17", "c.py:4"]
    assert sweep_nightly.parse_grep("", "if x:") == []


def test_parse_grep_keeps_a_body_holding_the_separator():
    out = "a.py\0" "3\0" "s = 'a\0b'\n"
    assert sweep_nightly.parse_grep(out, "s = 'a\0b'") == ["a.py:3"]


REGEXY = 'm = re.match(".*[(", s)'
INDEXY = "return v[0] + 1"


def _grep_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "g"
    repo.mkdir()
    _git(repo, "init", "-q")
    (repo / "a.py").write_text(f"def f(s):\n    {REGEXY}\n    return m\n")
    (repo / "b.py").write_text(f"import re\n{REGEXY}\n")
    (repo / "c.py").write_text(f"def g(v):\n    {INDEXY}\n")
    (repo / "d.py").write_text("def h(v0):\n    return v0 + 1\n")   # what a regex would hit
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "x")
    return repo


def test_same_shape_sites_is_a_fixed_string_search(tmp_path):
    """`.*[(` is TEXT: as a regex it does not even compile (every site dropped), and
    `v[0]` as a regex is `v0` (the real site missed, a wrong one found)."""
    repo = _grep_repo(tmp_path)
    assert sweep_nightly.same_shape_sites(repo, REGEXY) == ["a.py:2", "b.py:2"]
    assert sweep_nightly.same_shape_sites(repo, INDEXY) == ["c.py:2"]


def test_same_shape_sites_none_is_not_found_and_failure_is_unknown(tmp_path, monkeypatch):
    repo = _grep_repo(tmp_path)
    assert sweep_nightly.same_shape_sites(repo, "nowhere_at_all()") == []    # exit 1
    assert sweep_nightly.same_shape_sites(repo, "") is None
    assert sweep_nightly.same_shape_sites(tmp_path / "not-a-repo", REGEXY) is None


def _grouped(tmp_path, *muts):
    return sweep_nightly.survivor_groups("w", _grep_repo(tmp_path), list(muts))


def test_survivor_groups_carry_file_mutation_sites_and_key(tmp_path):
    a = _mut(file="b.py", line=2, kind="return-none", before=REGEXY, after="None")
    b = _mut(file="a.py", line=2, kind="return-none", before="    " + REGEXY, after="None")
    c = _mut(file="c.py", line=2, kind="return-code", before="    " + INDEXY, after="x")
    groups, failed = _grouped(tmp_path, a, b, c)
    assert failed == []
    assert [(g.file, g.mutation, g.survivors, g.sites, g.key) for g in groups] == [
        ("b.py", a, (a, b), ("a.py:2", "b.py:2"), sweep_nightly.category_key("w", a)),
        ("c.py", c, (c,), ("c.py:2",), sweep_nightly.category_key("w", c))]


def test_a_category_whose_sites_cannot_be_listed_is_not_grouped(tmp_path, monkeypatch):
    monkeypatch.setattr(sweep_nightly, "same_shape_sites", lambda wt, text: None)
    groups, failed = _grouped(tmp_path, _mut(file="c.py", line=2, before=INDEXY))
    assert (groups, failed) == ([], ["c.py:2: its same-shape sites could not be listed"])


def test_a_listing_that_misses_its_own_survivor_is_not_grouped(tmp_path):
    """A survivor whose line the grep did not find means the listing is wrong — never
    "these are all the sites"."""
    ok = _mut(file="c.py", line=2, before=INDEXY)
    off = _mut(file="c.py", line=7, before=INDEXY)
    repo = _grep_repo(tmp_path)
    groups, failed = sweep_nightly.survivor_groups("w", repo, [ok, off])
    assert (groups, failed) == ([], ["c.py:2: the same-shape listing misses c.py:7"])
    groups, failed = sweep_nightly.survivor_groups("w", repo, [ok])
    assert ([g.sites for g in groups], failed) == ([("c.py:2",)], [])


def test_the_weak_guard_of_two_shapes_is_two_groups_listing_every_site(tmp_path):
    """Real battery: if-false and compare-flip both survive on `if x > 100:`; PR 2's same
    line (killed there) is a same-shape site of both."""
    prs = [({"size.py": WEAK_TWO}, "PR 1 (#1)", None),
           ({"other.py": COVERED, "tests/test_other.py": COVERED_TEST}, "PR 2 (#2)", None)]
    filer = Filer()
    res = _run(_repo(tmp_path, prs=prs), tmp_path / "chela", filer)
    assert (res["status"], res["filed"], filer.kinds) == (
        "survivors", ["CMX-901", "CMX-902"], ["if-false", "compare-flip"])
    assert [(g.file, g.mutation.after.strip(), g.sites) for g, _ in filer.calls] == [
        ("size.py", "if False and (x > 100):", ("other.py:2", "size.py:2")),
        ("size.py", "if x <= 100:", ("other.py:2", "size.py:2"))]
    assert len({g.key for g, _ in filer.calls}) == 2


def test_an_unlisted_category_is_not_filed_and_holds_the_window(tmp_path, monkeypatch):
    repo, chela = _repo(tmp_path), tmp_path / "chela"
    monkeypatch.setattr(sweep_nightly, "same_shape_sites", lambda wt, text: None)
    filer = Filer()
    res = _run(repo, chela, filer)
    assert (res["status"], filer.calls, res["groups"], res["failed"]) == (
        "survivors", [], [], ["size.py:2: its same-shape sites could not be listed"])
    assert _covered_record(chela) == {}
    assert not (sweep_nightly.state_dir(chela) / "filed.json").exists()


# --- UNRUN and UNKNOWN are never clean --------------------------------------------------------


WEAK_MUT = sweep.Mutation("size.py", 2, "if-false", "    if x:", "    if False and (x):")


def _fake_battery(monkeypatch, *results, unsupported=()):
    def fake(worktree, base, test_cmd, **kw):
        return sweep.SweepReport(results=[sweep.SweepResult(m, st) for m, st in results],
                                 unsupported=list(unsupported))
    monkeypatch.setattr(sweep_nightly.sweep, "sweep", fake)


def test_a_budget_overflow_with_no_survivor_is_unrun_not_clean(tmp_path, monkeypatch):
    repo, chela = _repo(tmp_path), tmp_path / "chela"
    late = sweep.Mutation("other.py", 3, "return-code", "        return 1", "        return 0")
    _fake_battery(monkeypatch, (WEAK_MUT, judge.KILLED), (late, sweep.UNRUN),
                  (late, sweep.UNRUN), unsupported=["x.go"])
    res = _run(repo, chela, Filer())
    assert (res["status"], res["survivors"], res["mutations"], res["unrun"]) == (
        "unrun", 0, 3, 2)
    assert res["unrun_sites"] == ["other.py:3"]
    assert res["why"] == ("2 mutation(s) the budget never reached are UNRUN — CANNOT VERIFY "
                          "for their lines, not clean")
    assert _covered_record(chela) == {}


def test_the_real_battery_overflow_is_unrun(tmp_path):
    prs = [({"other.py": COVERED, "tests/test_other.py": COVERED_TEST}, "PR 2 (#2)", None)]
    repo = _repo(tmp_path, prs=prs, extra="sweep:\n  max_mutations: 1\n")
    res = _run(repo, tmp_path / "chela", Filer())
    assert (res["status"], res["survivors"], res["mutations"], res["unrun"]) == (
        "unrun", 0, 3, 2)


@pytest.mark.parametrize("unrun, unsupported, want", [
    (0, [], "clean"), (2, [], "unrun"), (0, ["x.go"], "unknown"), (2, ["x.go"], "unrun")])
def test_the_quiet_status(unrun, unsupported, want):
    assert sweep_nightly._quiet_status({"unrun": unrun, "unsupported": unsupported}) == want


def test_survivors_with_unrun_mutations_still_say_survivors(tmp_path, monkeypatch):
    repo, chela = _repo(tmp_path), tmp_path / "chela"
    late = sweep.Mutation("other.py", 3, "return-code", "        return 1", "        return 0")
    _fake_battery(monkeypatch, (WEAK_MUT, judge.SURVIVED), (late, sweep.UNRUN))
    res = _run(repo, chela, Filer())
    assert (res["status"], res["filed"], res["unrun"]) == ("survivors", ["CMX-901"], 1)
    assert res["why"] == ("1 mutation(s) the budget never reached are UNRUN — CANNOT VERIFY "
                          "for their lines, not clean")
    # filed, but never measured everything: the window still does not move
    assert _covered_record(chela) == {}
    again = _run(repo, chela, Filer())
    assert (again["status"], again["already_filed"], again["filed"]) == ("unrun", 1, [])


def test_already_filed_with_an_unknown_file_is_unknown_not_clean(tmp_path, monkeypatch):
    repo, chela = _repo(tmp_path), tmp_path / "chela"
    _fake_battery(monkeypatch, (WEAK_MUT, judge.SURVIVED), unsupported=["x.go"])
    first = _run(repo, chela, Filer())
    assert (first["status"], first["why"]) == (
        "survivors", "x.go UNKNOWN — no mutator handles them, not clean")
    again = _run(repo, chela, Filer())
    assert (again["status"], again["already_filed"]) == ("unknown", 1)


# --- the covered window ---------------------------------------------------------------------


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts).astimezone().isoformat()


def _covered(chela: Path, repo: Path, ts) -> None:
    sweep_nightly.state_dir(chela).mkdir(parents=True, exist_ok=True)
    (sweep_nightly.state_dir(chela) / "covered.json").write_text(json.dumps(
        {str((repo / "WORKFLOW.md").resolve()): ts}))


def _covered_record(chela: Path) -> dict:
    p = sweep_nightly.state_dir(chela) / "covered.json"
    return json.loads(p.read_text()) if p.exists() else {}


def test_only_the_last_24h_is_swept(tmp_path):
    old = [({"size.py": WEAK}, "old PR", "2020-06-01T00:00:00+00:00")]
    repo, chela = _repo(tmp_path, prs=old), tmp_path / "chela"
    filer = Filer()
    assert (_run(repo, chela, filer)["status"], filer.calls) == ("skipped", [])
    # the same commit, swept as of the night after it merged
    when = datetime.fromisoformat("2020-06-01T10:00:00+00:00").timestamp()
    assert _run(repo, chela, filer, now=when)["filed"] == ["CMX-901"]


def test_a_night_that_could_not_look_is_caught_up_from_the_last_covered_run(tmp_path):
    now = datetime.now().timestamp()
    old = [({"size.py": WEAK}, "PR from 2 days ago", _iso(now - 2 * 86400))]
    repo, chela = _repo(tmp_path, prs=old), tmp_path / "chela"
    _covered(chela, repo, now - 3 * 86400)
    res = _run(repo, chela, Filer(), now=now)
    assert res["filed"] == ["CMX-901"]


def test_catch_up_never_reaches_past_a_week(tmp_path):
    now = datetime.now().timestamp()
    old = [({"size.py": WEAK}, "PR from 10 days ago", _iso(now - 10 * 86400))]
    repo, chela = _repo(tmp_path, prs=old), tmp_path / "chela"
    _covered(chela, repo, now - 30 * 86400)
    res = _run(repo, chela, Filer(), now=now)
    since = datetime.fromtimestamp(now - 7 * 86400).isoformat(timespec="minutes")
    assert (res["status"], res["why"]) == (
        "skipped", f"no standard-tier PR merged to origin/dev since {since}")


@pytest.mark.parametrize("case, recorded", [
    ("filed", True), ("no_standard", True), ("clean", True), ("would_file", True),
    ("failed", False), ("paused", False), ("cannot_verify", False), ("judge_off", False),
    ("locked", False),
    # CANNOT VERIFY *after* the commits were read: `covered` is set, so only the status
    # check keeps these nights from moving the window (a red baseline, a torn key store).
    ("red_baseline", False), ("torn_key_store", False),
])
def test_only_a_run_that_looked_moves_the_window(tmp_path, case, recorded):
    """A night that CANNOT VERIFY must not advance the window (defeat shape 303b)."""
    prs = {"no_standard": [], "clean": [({"other.py": COVERED,
                                          "tests/test_other.py": COVERED_TEST}, "PR 2", None)],
           "red_baseline": [({"size.py": WEAK,
                              "tests/test_red.py": "def test_red():\n    assert False\n"},
                             "PR red (#4)", None)]}
    extra = "  enabled: false\n" if case == "judge_off" else ""
    repo, chela = _repo(tmp_path, prs=prs.get(case), extra=extra), tmp_path / "chela"
    chela.mkdir()
    if case == "paused":
        (chela / "fleet-pause.json").write_text("{}")
    if case == "cannot_verify":
        _git(repo, "remote", "set-url", "origin", str(tmp_path / "gone.git"))
    if case == "torn_key_store":
        sweep_nightly.state_dir(chela).mkdir(parents=True)
        (sweep_nightly.state_dir(chela) / "filed.json").write_text("{torn")
    filer = {"failed": Filer(fail=RuntimeError("no")), "would_file": None}.get(case, Filer())
    now = datetime.now().timestamp() + 1
    if case == "locked":
        sweep_nightly.state_dir(chela).mkdir(parents=True)
        with open(sweep_nightly.state_dir(chela) / "lock", "a") as held:
            fcntl.flock(held, fcntl.LOCK_EX)
            res = _run(repo, chela, filer, now=now)
    else:
        res = _run(repo, chela, filer, now=now)
    if case in ("red_baseline", "torn_key_store"):
        assert (res["status"], res["covered"], res["failed"]) == ("cannot_verify", True, [])
    if case == "failed":
        assert (res["status"], res["covered"]) == ("survivors", True)
    assert (_covered_record(chela) == {str((repo / "WORKFLOW.md").resolve()): now}) is recorded


def test_recording_the_window_keeps_other_workflows(tmp_path):
    repo, chela = _repo(tmp_path, prs=[]), tmp_path / "chela"
    sweep_nightly.state_dir(chela).mkdir(parents=True)
    (sweep_nightly.state_dir(chela) / "covered.json").write_text('{"/other/WORKFLOW.md": 5}')
    res = _run(repo, chela, Filer(), now=1e9)
    assert res["status"] == "skipped"
    assert _covered_record(chela) == {"/other/WORKFLOW.md": 5,
                                      str((repo / "WORKFLOW.md").resolve()): 1e9}


def test_a_torn_window_record_is_rewritten_by_a_run_that_looked(tmp_path):
    repo, chela = _repo(tmp_path, prs=[]), tmp_path / "chela"
    sweep_nightly.state_dir(chela).mkdir(parents=True)
    (sweep_nightly.state_dir(chela) / "covered.json").write_text("{torn")
    _run(repo, chela, Filer(), now=1e9)
    assert _covered_record(chela) == {str((repo / "WORKFLOW.md").resolve()): 1e9}


@pytest.mark.parametrize("content", ["{torn", "[]", '{"x": true}', '{"x": "5"}'])
def test_window_start_is_24h_on_an_unreadable_or_odd_record(tmp_path, content):
    wf, chela = _wf(tmp_path), tmp_path / "chela"
    sweep_nightly.state_dir(chela).mkdir(parents=True)
    (sweep_nightly.state_dir(chela) / "covered.json").write_text(
        content.replace('"x"', json.dumps(str(wf.path))))
    assert sweep_nightly.window_start(wf, 1e9, chela) == 1e9 - 86400


@pytest.mark.parametrize("last, want", [
    (1e9 - 3600, 1e9 - 86400),               # a recent record never shrinks the 24 h
    (1e9 - 86400, 1e9 - 86400),
    (1e9 - 2 * 86400, 1e9 - 2 * 86400),      # float
    (int(1e9) - 3 * 86400, 1e9 - 3 * 86400),  # int
    (1e9 - 30 * 86400, 1e9 - 7 * 86400),     # capped at a week
])
def test_window_start_catches_up_from_the_last_covered_run(tmp_path, last, want):
    wf, chela = _wf(tmp_path), tmp_path / "chela"
    sweep_nightly.state_dir(chela).mkdir(parents=True)
    (sweep_nightly.state_dir(chela) / "covered.json").write_text(json.dumps({str(wf.path): last}))
    assert sweep_nightly.window_start(wf, 1e9, chela) == want


def test_window_start_reads_only_its_own_workflow(tmp_path):
    wf, chela = _wf(tmp_path), tmp_path / "chela"
    sweep_nightly.state_dir(chela).mkdir(parents=True)
    (sweep_nightly.state_dir(chela) / "covered.json").write_text(json.dumps(
        {"/other/WORKFLOW.md": 1e9 - 3 * 86400}))
    assert sweep_nightly.window_start(wf, 1e9, chela) == 1e9 - 86400
