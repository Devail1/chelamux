"""🧹 The daily worktree GC (CMX-357).

On 2026-10-27 the chelamux worktrees root held 26 worktrees (1.9 GB); 21 belonged to closed
runs or to task ids with no run row at all, because ``reap_run_worktree`` only fires on a
run's own lifecycle path. These pin: which worktrees are collected (orphan / terminal ≥3 days)
and which never are (a live run, a young terminal one, one with a process in it, anything
git does not know), that a dirty one is salvaged BEFORE it is removed, and that every unknown
keeps the worktree.
"""
from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
import tarfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from chela import config, main, worktree, worktree_gc
from tests.test_doctor_notify import _run_daemon_ticks

NOW = time.time()
DAY = 24 * 3600


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True,
                          text=True).stdout


@pytest.fixture
def env(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "dev")
    for k, v in (("user.email", "t@example.com"), ("user.name", "T"), ("commit.gpgsign", "false")):
        _git(repo, "config", k, v)
    (repo / "app.py").write_text("x = 1\n")
    (repo / ".gitignore").write_text("ignored.log\n")
    _git(repo, "add", "app.py", ".gitignore")
    _git(repo, "commit", "-qm", "seed")
    chela_dir = tmp_path / "chela"
    root = chela_dir / "worktrees" / "proj"
    root.mkdir(parents=True)
    db = chela_dir / "scheduler.db"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE runs (task_id TEXT PRIMARY KEY, status TEXT, worktree_path TEXT, "
                 "ended_at TEXT)")
    conn.commit()
    conn.close()
    proc = tmp_path / "proc"
    proc.mkdir()
    return SimpleNamespace(repo=repo, root=root, db=db, chela_dir=chela_dir, proc=proc)


def add_wt(env, name: str, *, age_days: float = 10) -> Path:
    path = env.root / name
    _git(env.repo, "worktree", "add", "-q", "-b", f"br-{name}", str(path), "dev")
    age(path, age_days)
    return path


def age(path: Path, days: float) -> None:
    t = NOW - days * DAY
    for p in (path, path / ".git"):
        os.utime(p, (t, t))


def add_row(env, task: str, status: str, path: Path | None, ended_days_ago: float | None):
    ended = (None if ended_days_ago is None else
             (datetime.now(timezone.utc) - timedelta(days=ended_days_ago)).isoformat())
    conn = sqlite3.connect(env.db)
    conn.execute("INSERT INTO runs VALUES (?,?,?,?)",
                 (task, status, str(path) if path else None, ended))
    conn.commit()
    conn.close()


def add_proc(env, pid: int, cwd: Path) -> None:
    d = env.proc / str(pid)
    d.mkdir()
    os.symlink(cwd, d / "cwd")
    (d / "cmdline").write_bytes(b"pytest\0-x\0")


def make_dirty(path: Path) -> None:
    (path / "app.py").write_text("x = 2\n")
    (path / "notes.md").write_text("half-done work\n")
    (path / ".venv" / "lib").mkdir(parents=True)
    (path / ".venv" / "lib" / "big.so").write_text("binary")
    (path / "node_modules").mkdir()
    (path / "node_modules" / "dep.js").write_text("module")
    (path / "ignored.log").write_text("noise")
    age(path, 10)


def collect(env, **kw):
    return worktree_gc.collect(env.repo, env.root, db_path=env.db, chela_dir=env.chela_dir,
                               proc_root=env.proc, now=NOW, **kw)


def spy(calls, real=True):
    def remove(repo, path, root):
        archives = sorted(p.name for p in worktree_gc.salvage_dir(calls.chela_dir).glob("*"))
        calls.seen.append((path.name, archives))
        return worktree.remove_worktree(repo, path, root) if real else True
    return remove


# --- the core table: only the right ones go, the dirty one salvaged first --------------------

def test_only_orphans_and_old_terminal_runs_are_removed_and_dirty_is_salvaged_first(env):
    live = add_wt(env, "T-live")
    add_row(env, "T-live", "running", live, None)
    review = add_wt(env, "T-review")
    add_row(env, "T-review", "awaiting_review", review, 9)
    young = add_wt(env, "T-young")
    add_row(env, "T-young", "done", young, 1)
    old = add_wt(env, "T-old")
    add_row(env, "T-old", "closed", old, 4)
    add_wt(env, "T-clean")
    dirty = add_wt(env, "T-dirty")
    make_dirty(dirty)

    calls = SimpleNamespace(seen=[], chela_dir=env.chela_dir)
    result = collect(env, remove=spy(calls))

    assert sorted(result["removed"]) == ["T-clean", "T-dirty", "T-old"]
    assert sorted(p.name for p in env.root.iterdir()) == ["T-live", "T-review", "T-young"]
    archive = f"T-dirty-{datetime.fromtimestamp(NOW).strftime('%Y-%m-%d')}.tar.gz"
    assert dict(calls.seen)["T-dirty"] == [archive]      # salvaged BEFORE its removal
    assert dict(calls.seen)["T-clean"] == []              # a clean one is never archived
    assert result["salvaged"] == [{"name": "T-dirty", "archive":
                                   str(env.chela_dir / "worktree-salvage" / archive)}]
    kept = {k["name"]: k["why"] for k in result["kept"]}
    assert kept == {"T-live": "run T-live is running",
                    "T-review": "run T-review is awaiting_review",
                    "T-young": "run T-young went done under 3 days ago"}
    # the branch stays
    assert "br-T-dirty" in _git(env.repo, "branch", "--list", "br-T-dirty")
    assert "br-T-clean" in _git(env.repo, "branch", "--list", "br-T-clean")


def test_the_salvage_holds_the_diff_the_untracked_files_and_meta_minus_venv_and_caches(env):
    dirty = add_wt(env, "T-dirty")
    make_dirty(dirty)
    head = _git(dirty, "rev-parse", "HEAD").strip()
    collect(env)
    (archive,) = (env.chela_dir / "worktree-salvage").glob("*.tar.gz")
    with tarfile.open(archive) as tar:
        names = sorted(tar.getnames())
        patch = tar.extractfile("diff.patch").read().decode()
        meta = json.loads(tar.extractfile("meta.json").read())
        notes = tar.extractfile("untracked/notes.md").read()
    assert names == ["diff.patch", "meta.json", "untracked/notes.md"]
    assert "-x = 1" in patch and "+x = 2" in patch
    assert notes == b"half-done work\n"
    assert (meta["name"], meta["branch"], meta["head"], meta["why"]) == (
        "T-dirty", "br-T-dirty", head, "no run row")


def test_a_second_salvage_on_the_same_day_never_overwrites_the_first(env, tmp_path):
    dirty = add_wt(env, "T-dirty")
    make_dirty(dirty)
    dest = env.chela_dir / "worktree-salvage"
    first = worktree_gc.salvage(dirty, "why", dest, NOW)
    second = worktree_gc.salvage(dirty, "why", dest, NOW)
    stem = f"T-dirty-{datetime.fromtimestamp(NOW).strftime('%Y-%m-%d')}"
    assert (first.name, second.name) == (f"{stem}.tar.gz", f"{stem}-2.tar.gz")
    assert not list(dest.glob("*.tmp"))


# --- negative control: the liveness checks are what keeps a live worktree ----------------------

def test_negative_control_without_the_status_check_the_live_one_is_removed(env, monkeypatch):
    live = add_wt(env, "T-live")
    add_row(env, "T-live", "running", live, 30)
    calls = SimpleNamespace(seen=[], chela_dir=env.chela_dir)
    assert collect(env, remove=spy(calls, real=False))["removed"] == []
    monkeypatch.setattr(worktree_gc, "COLLECT_STATUSES", ("done", "closed", "running"))
    assert collect(env, remove=spy(calls, real=False))["removed"] == ["T-live"]
    assert [n for n, _ in calls.seen] == ["T-live"]
    assert live.is_dir()                                  # the spy never really removed


def test_negative_control_without_the_process_check_the_busy_one_is_removed(env, monkeypatch):
    wt = add_wt(env, "T-busy")
    (wt / "sub").mkdir()
    add_proc(env, 4242, wt / "sub")
    age(wt, 10)
    calls = SimpleNamespace(seen=[], chela_dir=env.chela_dir)
    result = collect(env, remove=spy(calls, real=False))
    assert result["removed"] == []
    assert result["kept"] == [{"name": "T-busy", "why": "a live process has its cwd in it"}]
    monkeypatch.setattr(worktree_gc, "busy", lambda *a: False)
    assert collect(env, remove=spy(calls, real=False))["removed"] == ["T-busy"]
    assert wt.is_dir()


def test_a_process_in_a_sibling_with_a_shared_prefix_does_not_hold_it(env):
    add_wt(env, "T-1")
    sib = add_wt(env, "T-10")
    add_proc(env, 4242, sib)
    calls = SimpleNamespace(seen=[], chela_dir=env.chela_dir)
    assert collect(env, remove=spy(calls, real=False))["removed"] == ["T-1"]


def test_busy_reads_cwds_from_proc_and_spares_this_process(env):
    wt = add_wt(env, "T-1")
    assert worktree_gc.busy(wt, env.root, env.proc) is False
    add_proc(env, os.getpid(), wt)
    assert worktree_gc.busy(wt, env.root, env.proc) is False   # the GC itself never counts
    add_proc(env, 4242, wt)
    assert worktree_gc.busy(wt, env.root, env.proc) is True


# --- ownership: judge-<task>, a requeued -r2, name fallback, reserved -------------------------

@pytest.mark.parametrize("status,ended,collected", [
    ("awaiting_review", 9, False), ("closed", 9, True), ("closed", 1, False),
])
def test_a_judge_worktree_follows_its_run(env, status, ended, collected):
    add_wt(env, "judge-T-9")
    add_row(env, "T-9", status, env.root / "T-9", ended)
    calls = SimpleNamespace(seen=[], chela_dir=env.chela_dir)
    removed = collect(env, remove=spy(calls, real=False))["removed"]
    assert removed == (["judge-T-9"] if collected else [])


def test_a_requeued_runs_old_worktree_is_owned_by_name_and_its_new_one_by_path(env):
    old = add_wt(env, "T-3")
    new = add_wt(env, "T-3-r2")
    add_row(env, "T-3", "running", new, None)
    kept = {k["name"]: k["why"] for k in collect(env)["kept"]}
    assert kept == {"T-3": "run T-3 is running", "T-3-r2": "run T-3 is running"}
    assert old.is_dir() and new.is_dir()


def test_a_row_naming_another_path_does_not_own_this_one(env):
    add_wt(env, "T-a")
    add_row(env, "T-b", "running", env.root / "T-a-elsewhere", None)
    assert collect(env)["removed"] == ["T-a"]


def test_the_nightly_sweep_checkout_is_never_collected(env):
    add_wt(env, "nightly-sweep")
    result = collect(env)
    assert result["removed"] == []
    assert result["kept"] == [{"name": "nightly-sweep",
                               "why": "reserved (the nightly sweep's checkout)"}]


def test_a_young_orphan_is_kept(env):
    add_wt(env, "T-new", age_days=1)
    result = collect(env)
    assert result["removed"] == []
    assert result["kept"] == [{"name": "T-new", "why": "no run row, but touched under 3 days ago"}]


def test_an_unregistered_directory_under_the_root_is_never_touched(env):
    stray = env.root / "stray"
    (stray / "x").mkdir(parents=True)
    (env.root / "notes.json").write_text("{}")
    assert collect(env)["removed"] == []
    assert stray.is_dir() and (env.root / "notes.json").exists()


def test_the_main_checkout_is_not_a_candidate_even_under_the_root(env, tmp_path):
    assert worktree_gc.registered(env.repo, env.repo.parent) == []


# --- every unknown keeps the worktree ----------------------------------------------------------

def test_no_runs_table_keeps_everything(env):
    add_wt(env, "T-1")
    env.db.unlink()
    result = collect(env)
    assert result["removed"] == [] and result["errors"] == [f"{env.db} does not exist"]
    env.db.write_text("not a database")
    result = collect(env)
    assert result["removed"] == [] and "unreadable" in result["errors"][0]


def test_a_terminal_row_with_no_readable_ended_at_is_kept(env):
    wt = add_wt(env, "T-1")
    add_row(env, "T-1", "done", wt, None)
    assert collect(env)["kept"] == [
        {"name": "T-1", "why": "run T-1 is done with no readable ended_at"}]


def test_a_naive_ended_at_reads_as_utc(env):
    wt = add_wt(env, "T-1")
    conn = sqlite3.connect(env.db)
    naive = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=4)
    conn.execute("INSERT INTO runs VALUES ('T-1','done',?,?)", (str(wt), naive.isoformat()))
    conn.commit()
    conn.close()
    assert collect(env)["removed"] == ["T-1"]


def test_an_unreadable_proc_keeps_the_worktree(env):
    add_wt(env, "T-1")
    env.proc.rmdir()
    result = collect(env)
    assert result["removed"] == []
    assert result["errors"] == [f"T-1: {env.proc} unreadable: "
                                f"[Errno 2] No such file or directory: '{env.proc}'"]


def test_a_failing_git_status_keeps_the_worktree(env, monkeypatch):
    add_wt(env, "T-1")
    real = worktree_gc._git

    def fake(cwd, *args):
        if "status" in args:
            raise worktree_gc.Unknown("status broke")
        return real(cwd, *args)

    monkeypatch.setattr(worktree_gc, "_git", fake)
    result = collect(env)
    assert result["removed"] == [] and result["errors"] == ["T-1: status broke"]


def test_a_salvage_that_cannot_be_written_keeps_the_dirty_worktree(env):
    dirty = add_wt(env, "T-dirty")
    make_dirty(dirty)
    env.chela_dir.joinpath("worktree-salvage").write_text("a file, not a dir")
    calls = SimpleNamespace(seen=[], chela_dir=env.chela_dir)
    with pytest.raises(OSError):
        worktree_gc.salvage(dirty, "why", env.chela_dir / "worktree-salvage", NOW)
    env.chela_dir.joinpath("worktree-salvage").unlink()
    (env.chela_dir / "worktree-salvage").mkdir()
    os.chmod(env.chela_dir / "worktree-salvage", 0o500)
    try:
        result = collect(env, remove=spy(calls, real=False))
    finally:
        os.chmod(env.chela_dir / "worktree-salvage", 0o700)
    assert calls.seen == [] and result["removed"] == []
    assert result["errors"][0].startswith("T-dirty: could not write salvage archive")
    assert dirty.is_dir()


def test_git_errors_raise_unknown_with_the_exit_code(tmp_path):
    with pytest.raises(worktree_gc.Unknown, match=r"failed \(exit 128\)"):
        worktree_gc._git(tmp_path, "rev-parse", "HEAD")


def test_git_that_cannot_run_raises_unknown(tmp_path, monkeypatch):
    def boom(*a, **kw):
        raise subprocess.TimeoutExpired("git", 1)

    monkeypatch.setattr(worktree_gc.subprocess, "run", boom)
    with pytest.raises(worktree_gc.Unknown, match="could not run"):
        worktree_gc._git(tmp_path, "status")


def test_git_argv_and_timeout_are_pinned(tmp_path, monkeypatch):
    seen = []
    monkeypatch.setattr(worktree_gc.subprocess, "run", lambda argv, **kw: seen.append(
        (argv, kw)) or SimpleNamespace(returncode=0, stdout=b"", stderr=b""))
    worktree_gc.dirty(tmp_path)
    assert seen == [(["git", "-C", str(tmp_path), "--no-optional-locks", "status", "--porcelain",
                      "--untracked-files=all"], {"capture_output": True, "timeout": 60})]


def test_a_refused_removal_is_an_error_not_a_crash(env):
    add_wt(env, "T-1")

    def refuse(repo, path, root):
        raise worktree.NotAWorktree("nope")

    result = collect(env, remove=refuse)
    assert result["removed"] == [] and result["errors"] == ["T-1: nope"]
    result = collect(env, remove=lambda *a: False)
    assert result["removed"] == [] and result["errors"] == ["T-1: removal failed"]


# --- reporting, dry run, the stamp, the daemon, the CLI ---------------------------------------

def test_a_dry_run_reports_count_size_and_dirt_and_removes_nothing(env):
    add_wt(env, "T-clean")
    dirty = add_wt(env, "T-dirty")
    make_dirty(dirty)
    result = collect(env, dry_run=True)
    assert result["removed"] == [] and result["salvaged"] == []
    assert [(o["name"], o["dirty"]) for o in result["orphans"]] == [
        ("T-clean", False), ("T-dirty", True)]
    assert all(o["bytes"] > 0 for o in result["orphans"])
    assert not (env.chela_dir / "worktree-salvage").exists()
    out = worktree_gc.render([result], dry_run=True)
    assert out.splitlines()[0] == (f"{env.root}: 2 orphan worktree(s), "
                                   f"{config.human_size(sum(o['bytes'] for o in result['orphans']))}"
                                   " (1 dirty)")
    assert "removed" not in out


def test_render_lists_removals_salvage_and_errors():
    out = worktree_gc.render([{"root": "/r", "orphans": [], "removed": ["a"], "errors": ["x"],
                               "salvaged": [{"name": "a", "archive": "/s/a.tar.gz"}]}],
                             dry_run=False)
    assert out == ("/r: 0 orphan worktree(s), 0B (0 dirty)\n  removed 1, salvaged 1\n"
                   "  salvaged a → /s/a.tar.gz\n  ⚠️ x")
    assert worktree_gc.render([], dry_run=True) == "no workflow roots to scan"


WORKFLOW = "---\nproject_key: TT\ntracker:\n  kind: markdown\n  path: TODO.md\nworkspace:\n" \
           "  root: {root}\n  base_branch: dev\n---\n"


def test_run_collects_per_workflow_and_stamps_only_a_real_pass(env, monkeypatch):
    monkeypatch.setattr(config, "CHELA_DIR", env.chela_dir)
    (env.repo / "WORKFLOW.md").write_text(WORKFLOW.format(root=env.root))
    add_wt(env, "T-1")
    wf = env.repo / "WORKFLOW.md"
    monkeypatch.setattr(worktree_gc, "collect", lambda repo, root, **kw: {
        "repo": str(repo), "root": str(root), "dry": kw["dry_run"], "db": str(kw["db_path"])})
    got = worktree_gc.run([wf, wf, env.repo / "missing.md"], dry_run=True,
                          chela_dir=env.chela_dir)
    assert got == [{"repo": str(env.repo.resolve()), "root": str(env.root.resolve()),
                    "dry": True, "db": str(env.db)}]
    assert not (env.chela_dir / "ops" / "worktree-gc.json").exists()
    assert worktree_gc.due(env.chela_dir)
    worktree_gc.run([wf], dry_run=False, chela_dir=env.chela_dir)
    assert not worktree_gc.due(env.chela_dir)


def test_run_refuses_a_root_this_chela_dir_does_not_own(env, monkeypatch, tmp_path):
    other = tmp_path / "scratch-state"
    other.mkdir()
    monkeypatch.setattr(config, "CHELA_DIR", other)
    (env.repo / "WORKFLOW.md").write_text(WORKFLOW.format(root=env.root))
    add_wt(env, "T-1")
    assert worktree_gc.run([env.repo / "WORKFLOW.md"], dry_run=False, chela_dir=other) == []
    assert (env.root / "T-1").is_dir()


@pytest.mark.parametrize("state,expected", [
    (None, True), ({"ts": NOW - 60}, False), ({"ts": NOW - worktree_gc.INTERVAL_S}, True),
    ({"ts": "yesterday"}, True), ({}, True), ("garbage", True),
])
def test_due(tmp_path, state, expected):
    if state is not None:
        (tmp_path / "ops").mkdir()
        (tmp_path / "ops" / "worktree-gc.json").write_text(
            state if isinstance(state, str) else json.dumps(state))
    assert worktree_gc.due(tmp_path, NOW) is expected


def test_the_documented_cadence_and_grace():
    assert (worktree_gc.INTERVAL_S, worktree_gc.GRACE_S) == (86400, 259200)


def test_spawn_is_detached_niced_and_not_doubled(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "CHELA_DIR", tmp_path)
    monkeypatch.setattr(worktree_gc.shutil, "which", lambda name: f"/usr/bin/{name}")
    calls = []
    child = SimpleNamespace(poll=lambda: None)
    monkeypatch.setattr(worktree_gc.subprocess, "Popen",
                        lambda argv, **kw: calls.append((argv, kw)) or child)
    assert worktree_gc.spawn() is child and worktree_gc.spawn(child) is child
    (argv, kw), = calls
    assert argv == ["nice", "-n", "19", "ionice", "-c", "3", sys.executable, "-m",
                    "chela.main", "ops", "worktrees", "--gc"]
    assert (kw["start_new_session"], kw["stdin"], kw["stdout"].name) == (
        True, subprocess.DEVNULL, str(tmp_path / "ops" / "worktree-gc.log"))
    done = SimpleNamespace(poll=lambda: 0)
    assert worktree_gc.spawn(done) is child and len(calls) == 2
    monkeypatch.setattr(worktree_gc.shutil, "which", lambda name: None)
    assert worktree_gc.spawn_argv()[:1] == [sys.executable]


@pytest.mark.parametrize("is_due", [True, False])
def test_the_daemon_spawns_the_gc_only_when_due_and_survives_its_crash(monkeypatch, is_due):
    _run_daemon_ticks(monkeypatch, n=2)
    monkeypatch.setattr(main.doctor, "check_and_notify", lambda red: red)
    monkeypatch.setattr(main.ops, "spawn", lambda prev: prev)
    monkeypatch.setattr(main.worktree_gc, "due", lambda: is_due)
    spawned = []
    children = iter(["c1", "c2"])
    monkeypatch.setattr(main.worktree_gc, "spawn", lambda prev: spawned.append(prev) or next(children))
    main.cmd_run(SimpleNamespace())
    assert spawned == ([None, "c1"] if is_due else [])

    def boom():
        raise RuntimeError("disk gone")

    _run_daemon_ticks(monkeypatch, n=1)
    monkeypatch.setattr(main.worktree_gc, "due", boom)
    after = []
    monkeypatch.setattr(main.update, "check_and_notify", lambda b: after.append(b) or b)
    main.cmd_run(SimpleNamespace())
    assert after == [0]


@pytest.mark.parametrize("argv,dry", [([], True), (["--gc"], False)])
def test_the_cli_routes_ops_worktrees(monkeypatch, capsys, argv, dry):
    seen = []
    monkeypatch.setattr(sys, "argv", ["chela", "ops", "worktrees", *argv])
    monkeypatch.setattr(main.config, "DISPATCH_WORKFLOWS", ["W.md"])
    monkeypatch.setattr(main, "cmd_ops_scan", lambda a: pytest.fail("routed to scan"))
    monkeypatch.setattr(main.worktree_gc, "run",
                        lambda wfs, dry_run: seen.append((wfs, dry_run)) or [])
    main.main()
    assert seen == [(["W.md"], dry)]
    assert capsys.readouterr().out == "no workflow roots to scan\n"


def test_the_cli_takes_explicit_workflows_and_json(monkeypatch, capsys):
    seen = []
    monkeypatch.setattr(main.worktree_gc, "run", lambda wfs, dry_run: seen.append(wfs) or [{"a": 1}])
    main.cmd_ops_worktrees(SimpleNamespace(workflow=["X.md"], gc=False, json=True))
    assert seen == [["X.md"]]
    assert json.loads(capsys.readouterr().out) == [{"a": 1}]


def test_ops_scan_still_routes_to_scan(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["chela", "ops", "scan"])
    monkeypatch.setattr(main, "cmd_ops_worktrees", lambda a: pytest.fail("routed to worktrees"))
    seen = []
    monkeypatch.setattr(main, "cmd_ops_scan", lambda a: seen.append(a.ops_cmd))
    main.main()
    assert seen == ["scan"]


def test_a_worktree_sitting_at_the_root_itself_is_not_a_candidate(env):
    env.root.rmdir()
    _git(env.repo, "worktree", "add", "-q", "-b", "br-root", str(env.root), "dev")
    assert worktree_gc.registered(env.repo, env.root) == []
    assert worktree_gc.registered(env.repo, env.root.parent) == [env.root.resolve()]


def test_a_clean_worktree_whose_head_is_on_no_branch_is_kept(env):
    wt = add_wt(env, "judge-T-5")
    _git(wt, "checkout", "-q", "--detach")
    (wt / "app.py").write_text("x = 3\n")
    _git(wt, "commit", "-qam", "local only")
    age(wt, 10)
    result = collect(env)
    assert result["removed"] == []
    assert result["kept"] == [{"name": "judge-T-5",
                               "why": "HEAD has commits on no branch or remote"}]
    _git(wt, "branch", "keep-it")
    assert collect(env)["removed"] == ["judge-T-5"]


def test_a_pass_that_crashes_is_still_stamped_so_the_daemon_does_not_respawn_it(env, monkeypatch):
    monkeypatch.setattr(config, "CHELA_DIR", env.chela_dir)
    (env.repo / "WORKFLOW.md").write_text(WORKFLOW.format(root=env.root))

    def boom(*a, **kw):
        raise RuntimeError("unexpected")

    monkeypatch.setattr(worktree_gc, "collect", boom)
    with pytest.raises(RuntimeError):
        worktree_gc.run([env.repo / "WORKFLOW.md"], dry_run=False, chela_dir=env.chela_dir)
    assert not worktree_gc.due(env.chela_dir)
