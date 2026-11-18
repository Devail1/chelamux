"""⛔ CMX-398 — a hand-in or self-check killed mid-mutation never hands its mutation on.

On 2026-11-17 a reboot killed a ``task-finished`` self-check AND its guardian mid-mutation;
the re-spawned agent found a security guard disabled (``if False:``) in its worktree, with
the recovery marker still beside it and nothing that applied it. These tests pin the three
answers: every entry point restores FIRST (or refuses, naming the three-way state); nothing
pushes or hands in a mutation; and the daemon's boot scan restores what a reboot left.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from chela import dispatcher, main, sweep, sweep_nightly
from tests.test_dispatcher_rework import _FakeTmux, _row, _Source, _status, _wf
from tests.test_judge_kill_recovery import AFTER, FEAT, NO_GUARDIAN, _git, _repo, _start

REAL_MUTATION_GATE = True            # the conftest stub must not hide the gate under test

MUTATED = FEAT.replace("    if x == 3:", AFTER, 1)


@pytest.fixture(autouse=True)
def _own_runs_db(tmp_path, monkeypatch):
    monkeypatch.setattr(dispatcher, "DB_PATH", tmp_path / "scheduler.db")


def _dead_marker(root: Path, mutated: str = MUTATED, *, original: str = FEAT,
                 rel: str = "feat.py") -> None:
    """A marker exactly as a killed sweep leaves it, owned by a pid that is gone."""
    sweep._write_marker(root, rel, original, mutated)
    data = json.loads(sweep.marker_path(root).read_text())
    data["pid"] = 2 ** 22 + 7                       # above pid_max's default: never alive
    sweep.marker_path(root).write_text(json.dumps(data))


# --- the acceptance test: kill a self-check, re-spawn, restored before the agent starts ----


class _Launched(Exception):
    """Raised from the stubbed ``_new_window`` — the agent's window is the first thing that
    could run a tool call, so what is on disk at that moment is what the agent would see."""


def _respawn(monkeypatch, tmp_path: Path, root: Path) -> str:
    """Drive the dispatcher's ONE spawn path into ``root`` and return ``feat.py`` as it was
    the moment the agent's window was created."""
    seen: list[str] = []
    monkeypatch.setattr(dispatcher, "reap_strays_in", lambda *a, **k: None)
    monkeypatch.setattr(dispatcher, "_kill_windows_named", lambda *a, **k: None)

    def new_window(name, cwd):
        seen.append((Path(cwd) / "feat.py").read_text())
        raise _Launched

    monkeypatch.setattr(dispatcher, "_new_window", new_window)
    wf = _wf(tmp_path / "wf-home")
    monkeypatch.setattr(dispatcher.subprocess, "run",
                        lambda *a, **k: SimpleNamespace(returncode=0, stdout="", stderr=""))
    with dispatcher._db() as conn, pytest.raises(_Launched):
        dispatcher._launch_agent(wf, "t1", "cmx-1", root, "go", conn, hook_vars={},
                                 fresh_worktree=False)
    return seen[0]


def _killed_self_check(tmp_path: Path) -> Path:
    """A real ``chela judge self-check`` SIGKILLed mid-mutation with no guardian — the reboot
    case: the host restart takes the guardian down with it."""
    (tmp_path / "wf-home").mkdir()
    root = _repo(tmp_path / "r")
    proc, _ = _start(tmp_path, root, prelude=NO_GUARDIAN)
    proc.kill()
    proc.wait(timeout=30)
    assert AFTER in (root / "feat.py").read_text() and sweep.marker_path(root).exists()
    return root


def test_a_self_check_killed_mid_mutation_is_restored_before_the_respawned_agent_starts(
        tmp_path, monkeypatch):
    root = _killed_self_check(tmp_path)
    try:
        assert _respawn(monkeypatch, tmp_path, root) == FEAT
        assert not sweep.marker_path(root).exists()
        assert _git(root, "status", "--porcelain") == ""
    finally:
        _git(root, "checkout", "--", "feat.py")      # lets the orphaned suite exit too


def test_negative_control_without_the_startup_restore_the_agent_starts_on_the_mutation(
        tmp_path, monkeypatch):
    root = _killed_self_check(tmp_path)
    monkeypatch.setattr(sweep, "recover", lambda wt: ("", ""))
    try:
        assert AFTER in _respawn(monkeypatch, tmp_path, root)
    finally:
        _git(root, "checkout", "--", "feat.py")
        sweep.marker_path(root).unlink(missing_ok=True)


def test_a_three_way_state_refuses_the_launch_and_writes_nothing(tmp_path, monkeypatch):
    (tmp_path / "wf-home").mkdir()
    root = _repo(tmp_path / "r")
    _dead_marker(root)
    edited = FEAT.replace("    if x == 3:", "    if x == 4:", 1)
    (root / "feat.py").write_text(edited)
    with pytest.raises(sweep.MutationLeftover) as e:
        _respawn(monkeypatch, tmp_path, root)
    assert str(e.value).startswith(f"⛔ not launching an agent into {root}: ")
    assert ("Three-way state at feat.py:2: original `if x == 3:`, mutation "
            "`if False and (x == 3):`, on disk `if x == 4:`. Nothing was written.") in str(e.value)
    assert (root / "feat.py").read_text() == edited and sweep.marker_path(root).exists()


def test_a_live_owner_refuses_the_launch(tmp_path, monkeypatch):
    (tmp_path / "wf-home").mkdir()
    root = _repo(tmp_path / "r")
    sweep._write_marker(root, "feat.py", FEAT, MUTATED)
    data = json.loads(sweep.marker_path(root).read_text())
    data["pid"] = os.getppid()                      # alive, and not this process
    sweep.marker_path(root).write_text(json.dumps(data))
    (root / "feat.py").write_text(MUTATED)
    with pytest.raises(RuntimeError, match=f"another sweep \\(pid {os.getppid()}\\)") as e:
        _respawn(monkeypatch, tmp_path, root)
    # transient — the ordinary retry, never the needs-a-human MutationLeftover
    assert type(e.value) is RuntimeError
    assert str(e.value).startswith(f"not launching an agent into {root} yet: ")
    assert (root / "feat.py").read_text() == MUTATED


@pytest.mark.parametrize("exc, status", [
    (sweep.MutationLeftover("⛔ three-way"), "needs_human"),
    (RuntimeError("tmux gone"), "failed"),
])
def test_a_claim_refused_for_a_mutation_needs_a_human_not_a_retry(tmp_path, exc, status):
    wf = _wf(tmp_path)
    source = _Source("abc123")
    with patch.object(dispatcher, "load_workflow_cached", return_value=_status(wf)), \
         patch.object(dispatcher, "get_source", return_value=source), \
         patch.object(dispatcher, "_claim_order", return_value=source.list_open_tasks()), \
         patch.object(dispatcher, "_spawn", side_effect=exc), \
         patch.object(dispatcher.subprocess, "run", side_effect=_FakeTmux().run):
        dispatcher.tick(wf.path)
    run = dispatcher.resolve_run("abc123")
    assert (run["status"], run["last_error"]) == (status, str(exc))


@pytest.mark.parametrize("exc, status", [
    (sweep.MutationLeftover("⛔ three-way"), "needs_human"),
    (RuntimeError("tmux gone"), "changes_requested"),
])
def test_a_rework_refused_for_a_mutation_escalates_instead_of_burning_a_round(tmp_path, exc,
                                                                             status):
    wf = _wf(tmp_path)
    with dispatcher._db() as conn:
        _row(conn, workflow_path=str(wf.path), status="changes_requested", rework_count=1,
             review_history=json.dumps([{"round": 1, "at": "t", "body": "fix it"}]))
    with patch.object(dispatcher, "load_workflow_cached", return_value=_status(wf)), \
         patch.object(dispatcher, "get_source", return_value=_Source("abc123")), \
         patch.object(dispatcher, "_claim_order", return_value=[]), \
         patch.object(dispatcher, "_read_pr_status", return_value=("open", "MERGEABLE")), \
         patch.object(dispatcher, "_respawn_rework", side_effect=exc) as respawn, \
         patch.object(dispatcher.subprocess, "run", side_effect=_FakeTmux().run):
        dispatcher.tick(wf.path)
    assert respawn.called
    run = dispatcher.resolve_run("abc123")
    assert run["status"] == status
    assert str(exc) in run["last_error"]


# --- never push a mutation -----------------------------------------------------------------


def _running(root: Path) -> None:
    with dispatcher._db() as conn:
        _row(conn, task_id="t1", status="running", worktree_path=str(root), pr_url=None)


def test_request_push_with_a_mutated_tracked_file_is_refused_naming_file_and_line(tmp_path):
    """The marker is gone (deleted by hand, say) but the bytes on disk are still exactly a
    recorded mutation — the journal in the git dir is what catches it."""
    root = _repo(tmp_path / "r")
    _dead_marker(root)
    sweep.marker_path(root).unlink()
    (root / "feat.py").write_text(MUTATED)
    _running(root)
    result = dispatcher.request_push("t1", pr_title="CMX-1: x", pr_body="b")
    assert result == {"ok": False, "task_id": "t1", "restored": "", "error": (
        "refusing to push — a sweep/self-check mutation: feat.py:2 is a sweep/self-check "
        "MUTATION (`if False and (x == 3):`) in the working tree — restore it"
        "; (only if that exact content is deliberate: remove its entry from "
        f"{sweep.journal_path(root)})")}
    assert not (root / ".chela-push-request.json").exists()


def test_request_push_refuses_a_mutation_already_committed(tmp_path):
    root = _repo(tmp_path / "r")
    _dead_marker(root)
    sweep.marker_path(root).unlink()
    (root / "feat.py").write_text(MUTATED)
    _git(root, "commit", "-qam", "oops")
    (root / "feat.py").write_text(FEAT)            # the working tree is fine; HEAD is not
    _running(root)
    result = dispatcher.request_push("t1")
    assert result["ok"] is False
    assert ("feat.py:2 is a sweep/self-check MUTATION (`if False and (x == 3):`) COMMITTED in "
            "HEAD — revert it before pushing; (only if") in result["error"]


def test_request_push_restores_a_provable_mutation_first_then_proceeds(tmp_path):
    root = _repo(tmp_path / "r")
    _dead_marker(root)
    (root / "feat.py").write_text(MUTATED)
    _running(root)
    result = dispatcher.request_push("t1", pr_title="CMX-1: x")
    assert result["ok"] is True
    assert result["restored"].startswith("restored feat.py from a killed sweep's recovery marker")
    assert (root / "feat.py").read_text() == FEAT
    assert (root / ".chela-push-request.json").exists()


def test_request_push_three_way_stops_rather_than_overwrites(tmp_path):
    root = _repo(tmp_path / "r")
    _dead_marker(root)
    (root / "feat.py").write_text("edited\n")
    _running(root)
    result = dispatcher.request_push("t1")
    assert result["ok"] is False and "Three-way state at feat.py:2" in result["error"]
    assert (root / "feat.py").read_text() == "edited\n"
    assert not (root / ".chela-push-request.json").exists()


def test_the_daemon_push_refuses_a_committed_mutation_and_never_runs_git_push(tmp_path):
    root = _repo(tmp_path / "r")
    _dead_marker(root)
    sweep.marker_path(root).unlink()
    (root / "feat.py").write_text(MUTATED)
    _git(root, "commit", "-qam", "oops")
    dispatcher._push_request_path(str(root)).write_text(json.dumps({"task_id": "t1"}))
    with dispatcher._db() as conn:
        row = _row(conn, task_id="t1", status="running", worktree_path=str(root))
        with patch.object(dispatcher, "_git") as git:
            out = dispatcher._apply_push_request(conn, _wf(tmp_path), row)
    git.assert_not_called()
    assert out == {"ok": False, "error": "refusing to push a sweep/self-check mutation: "
                   "feat.py:2 is a sweep/self-check MUTATION (`if False and (x == 3):`) "
                   "COMMITTED in HEAD — revert it before pushing; (only if that exact content "
                   f"is deliberate: remove its entry from {sweep.journal_path(root)})"}


def test_the_daemon_push_ignores_a_live_self_checks_marker_on_disk(tmp_path):
    """A self-check mutating the working tree right now is not what leaves: HEAD is."""
    root = _repo(tmp_path / "r")
    sweep._write_marker(root, "feat.py", FEAT, MUTATED)
    (root / "feat.py").write_text(MUTATED)
    assert sweep.mutation_blockers(root, head_only=True) == []
    assert sweep.mutation_blockers(root)[0].startswith("a recovery marker is still in the "
                                                       "worktree")


def test_check_mutations_restores_then_passes_or_refuses(tmp_path):
    root = _repo(tmp_path / "r")
    _running(root)
    assert dispatcher.check_mutations("t1") == {"ok": True, "restored": ""}
    _dead_marker(root)
    (root / "feat.py").write_text(MUTATED)
    out = dispatcher.check_mutations("t1")
    assert out["ok"] is True and out["restored"].startswith("restored feat.py")
    _dead_marker(root)
    (root / "feat.py").write_text("edited\n")
    out = dispatcher.check_mutations("t1")
    assert out["ok"] is False
    assert out["error"].startswith("a sweep/self-check mutation blocks this — ")
    assert dispatcher.check_mutations("nope") == {
        "ok": False, "error": "no run with a worktree on record for nope"}


def test_task_finished_refuses_before_any_other_gate_and_rechecks_before_handing_in(
        monkeypatch, capsys):
    calls: list[str] = []
    verdicts = iter([{"ok": True, "restored": "restored feat.py from a killed sweep's x"},
                     {"ok": False, "error": "feat.py:2 is a MUTATION"}])
    monkeypatch.setattr(dispatcher, "check_mutations",
                        lambda tid: calls.append(f"mut:{tid}") or next(verdicts))
    monkeypatch.setattr(dispatcher, "check_base_sync",
                        lambda *a, **k: calls.append("sync") or {"ok": True, "record": None})
    monkeypatch.setattr(dispatcher, "hand_in_tier", lambda tid: "core")
    monkeypatch.setattr(dispatcher, "hand_in_checks", lambda tier, experiments: set())
    monkeypatch.setattr(dispatcher, "request_task_finished",
                        lambda *a, **k: calls.append("handed-in") or {"ok": True})
    args = SimpleNamespace(task_id="cmx-9", no_new_guards=True)
    with pytest.raises(SystemExit) as e:
        main.cmd_task_finished(args)
    assert e.value.code == 1
    assert calls == ["mut:cmx-9", "sync", "mut:cmx-9"]
    out = capsys.readouterr().out
    assert "⚠ task-finished: restored feat.py from a killed sweep's x" in out
    assert "task-finished: ⛔ feat.py:2 is a MUTATION" in out


def test_task_finished_refuses_a_mutation_up_front(monkeypatch, capsys):
    monkeypatch.setattr(dispatcher, "check_mutations",
                        lambda tid: {"ok": False, "error": "three-way"})
    monkeypatch.setattr(dispatcher, "check_base_sync",
                        lambda *a, **k: pytest.fail("a gate ran before the restore"))
    with pytest.raises(SystemExit) as e:
        main.cmd_task_finished(SimpleNamespace(task_id="cmx-9"))
    assert e.value.code == 1
    assert capsys.readouterr().out == "task-finished: ⛔ three-way\n"


def test_request_push_cli_says_what_it_restored(monkeypatch, capsys):
    monkeypatch.setattr(dispatcher, "request_push", lambda *a, **k: {
        "ok": True, "task_id": "t1", "requested": True, "restored": "restored feat.py"})
    main.cmd_request_push(SimpleNamespace(task_id="t1", pr_title=None, pr_body_file=None))
    assert capsys.readouterr().out.startswith("⚠ request-push: restored feat.py\n")


# --- the mutation journal and blockers -----------------------------------------------------


def test_the_journal_lives_in_the_git_dir_never_in_the_tree(tmp_path):
    root = _repo(tmp_path / "r")
    sweep._write_marker(root, "feat.py", FEAT, MUTATED)
    journal = root / ".git" / "chela-mutations.jsonl"
    assert sweep.journal_path(root) == journal
    assert [json.loads(x) for x in journal.read_text().splitlines()] == [
        {"file": "feat.py", "line": 2, "blob": sweep._blob_id(MUTATED.encode()),
         "text": "if False and (x == 3):"}]
    sweep.marker_path(root).unlink()
    assert _git(root, "status", "--porcelain") == ""


def test_a_linked_worktree_journals_into_its_own_git_dir(tmp_path):
    repo = _repo(tmp_path / "r")
    wt = tmp_path / "wt"
    _git(repo, "worktree", "add", "-q", "--detach", str(wt))
    gitdir = Path((wt / ".git").read_text().split(":", 1)[1].strip())
    assert sweep.journal_path(wt) == gitdir / "chela-mutations.jsonl"
    (wt / ".git").write_text("gitdir: ../r/.git/worktrees/wt\n")       # a relative pointer
    assert sweep.journal_path(wt) == (repo / ".git/worktrees/wt/chela-mutations.jsonl")
    (wt / ".git").write_text("not a pointer\n")
    assert sweep.journal_path(wt) is None
    assert sweep.journal_path(tmp_path / "nowhere") is None


def test_the_blob_id_is_gits_own(tmp_path):
    root = _repo(tmp_path / "r")
    assert sweep._blob_id(FEAT.encode()) == _git(root, "hash-object", "feat.py")


def test_an_unwritable_journal_never_stops_the_mutation(tmp_path, capsys, monkeypatch):
    root = _repo(tmp_path / "r")
    monkeypatch.setattr(sweep, "journal_path", lambda wt: tmp_path / "missing-dir" / "j")
    sweep._write_marker(root, "feat.py", FEAT, MUTATED)
    assert sweep.marker_path(root).exists()
    assert "could not journal the mutation of feat.py" in capsys.readouterr().err


def test_the_journal_is_trimmed_to_its_newest_entries(tmp_path, monkeypatch):
    root = _repo(tmp_path / "r")
    monkeypatch.setattr(sweep, "JOURNAL_MAX_BYTES", 10)
    monkeypatch.setattr(sweep, "JOURNAL_KEEP", 2)
    for n in range(4):
        sweep._journal(root, f"f{n}.py", 1, f"x = {n}\n")
    files = [json.loads(x)["file"] for x in sweep.journal_path(root).read_text().splitlines()]
    assert files == ["f1.py", "f2.py", "f3.py"]          # the kept 2, then the newest


def test_an_unreadable_journal_blocks_rather_than_reads_as_clean(tmp_path):
    root = _repo(tmp_path / "r")
    sweep.journal_path(root).write_text("{not json\n")
    blockers = sweep.mutation_blockers(root)
    assert len(blockers) == 1 and blockers[0].startswith(
        f"the mutation journal {sweep.journal_path(root)} is unreadable (")


def test_an_unreadable_marker_blocks(tmp_path):
    root = _repo(tmp_path / "r")
    sweep.marker_path(root).write_text("[]")
    blockers = sweep.mutation_blockers(root)
    assert blockers[0].startswith("a recovery marker is still in the worktree")
    assert blockers[1].startswith(f"{sweep.marker_path(root)} is unreadable (")


def test_an_unreadable_head_blocks(tmp_path):
    root = tmp_path / "plain"
    (root / ".git").mkdir(parents=True)               # a git dir git cannot read
    sweep._journal(root, "feat.py", 2, MUTATED)
    blockers = sweep.mutation_blockers(root)
    assert len(blockers) == 1
    assert blockers[0].startswith("could not read HEAD's tree to check it for a mutation: ")


def test_a_clean_tree_with_a_journal_has_no_blockers(tmp_path):
    root = _repo(tmp_path / "r")
    sweep._journal(root, "feat.py", 2, MUTATED)
    sweep._journal(root, "gone.py", 1, "x\n")         # a file that no longer exists
    assert sweep.mutation_blockers(root) == []


def test_a_file_that_cannot_be_read_blocks(tmp_path):
    root = _repo(tmp_path / "r")
    sweep._journal(root, "feat.py", 2, MUTATED)
    (root / "feat.py").unlink()
    (root / "feat.py").mkdir()                        # IsADirectoryError, an OSError
    blockers = sweep.mutation_blockers(root)
    assert blockers[0].startswith("feat.py could not be read to check it for a mutation: ")


@pytest.mark.parametrize("original, mutated, line", [
    ("a\nb\nc\n", "a\nX\nc\n", 2), ("a\n", "b\n", 1), ("a", "a\nb", 2), ("a\nb", "a", 2),
])
def test_first_changed_line(original, mutated, line):
    assert sweep._first_changed_line(original, mutated) == line


def test_line_text_out_of_range():
    assert sweep._line_text("a\nb", 2) == "b"
    assert sweep._line_text("a\nb", 3) == "(no such line)"
    assert sweep._line_text("a\nb", 0) == "(no such line)"


# --- the reboot path: another boot's pid is never "alive" ----------------------------------


def test_a_marker_from_another_boot_is_recovered_even_if_its_pid_now_answers(tmp_path):
    root = _repo(tmp_path / "r")
    sweep._write_marker(root, "feat.py", FEAT, MUTATED)
    data = json.loads(sweep.marker_path(root).read_text())
    data.update(pid=os.getppid(), boot_id="an-earlier-boot")   # the pid was reused
    sweep.marker_path(root).write_text(json.dumps(data))
    (root / "feat.py").write_text(MUTATED)
    assert sweep.live_owner(root) is None
    done, err = sweep.recover(root)
    assert err == "" and done.startswith("restored feat.py")
    assert (root / "feat.py").read_text() == FEAT


def test_a_marker_from_this_boot_with_a_live_pid_is_live(tmp_path):
    root = _repo(tmp_path / "r")
    sweep._write_marker(root, "feat.py", FEAT, MUTATED)
    data = json.loads(sweep.marker_path(root).read_text())
    data["pid"] = os.getppid()
    sweep.marker_path(root).write_text(json.dumps(data))
    assert sweep.live_owner(root) == os.getppid()
    del data["boot_id"]                               # an older marker: the pid decides
    sweep.marker_path(root).write_text(json.dumps(data))
    assert sweep.live_owner(root) == os.getppid()


def test_live_owner_of_no_or_an_unreadable_marker_is_none(tmp_path):
    assert sweep.live_owner(tmp_path) is None
    sweep.marker_path(tmp_path).write_text("{")
    assert sweep.live_owner(tmp_path) is None
    sweep.marker_path(tmp_path).write_text("[1]")
    assert sweep.live_owner(tmp_path) is None


def test_boot_id_reads_the_kernels_and_is_empty_when_unreadable(tmp_path, monkeypatch):
    assert sweep._boot_id() == Path("/proc/sys/kernel/random/boot_id").read_text().strip()
    monkeypatch.setattr(sweep, "BOOT_ID", tmp_path / "nope")
    assert sweep._boot_id() == ""


def _worktrees(tmp_path: Path) -> tuple[Path, Path, Path, Path]:
    root = tmp_path / "worktrees"
    provable = _repo(root / "proj" / "CMX-1")
    _dead_marker(provable)
    (provable / "feat.py").write_text(MUTATED)
    edited = _repo(root / "proj" / "CMX-2")
    _dead_marker(edited)
    (edited / "feat.py").write_text("edited\n")
    live = _repo(root / "proj" / "CMX-3")
    sweep._write_marker(live, "feat.py", FEAT, MUTATED)        # this process: alive
    data = json.loads(sweep.marker_path(live).read_text())
    data["pid"] = os.getppid()
    sweep.marker_path(live).write_text(json.dumps(data))
    (live / "feat.py").write_text(MUTATED)
    return root, provable, edited, live


def test_boot_scan_restores_the_provable_and_leaves_the_rest_named(tmp_path):
    root, provable, edited, live = _worktrees(tmp_path)
    nested = provable / "node_modules" / "pkg"
    nested.mkdir(parents=True)
    (nested / sweep.RECOVERY_MARKER).write_text("{}")         # inside a checkout: never seen
    result = sweep.boot_scan([root, root])                      # a root named twice: once
    assert result["restored"] == [
        f"{provable.resolve()}: restored feat.py from a killed sweep's recovery marker "
        "(line 2: `if False and (x == 3):` → `if x == 3:`)"]
    assert len(result["left"]) == 1 and result["left"][0].startswith(f"{edited.resolve()}: ")
    assert "Three-way state at feat.py:2" in result["left"][0]
    assert (provable / "feat.py").read_text() == FEAT
    assert (edited / "feat.py").read_text() == "edited\n"
    assert (live / "feat.py").read_text() == MUTATED and sweep.marker_path(live).exists()


def test_find_markers_stops_at_a_checkout_and_at_its_depth(tmp_path):
    root = tmp_path / "w"
    deep = root / "a" / "b" / "c" / "d" / "e"
    deep.mkdir(parents=True)
    (deep / sweep.RECOVERY_MARKER).write_text("{}")
    (root / "a" / sweep.RECOVERY_MARKER).write_text("{}")
    assert sweep.find_markers(root) == [root / "a" / sweep.RECOVERY_MARKER]
    assert sweep.find_markers(root, depth=5) == [root / "a" / sweep.RECOVERY_MARKER,
                                                 deep / sweep.RECOVERY_MARKER]
    (root / "a" / ".git").mkdir()                    # a checkout: nothing below it is walked
    assert sweep.find_markers(root, depth=5) == [root / "a" / sweep.RECOVERY_MARKER]
    assert sweep.find_markers(tmp_path / "absent") == []


def test_the_daemon_boot_scan_posts_one_notice(tmp_path, monkeypatch):
    chela_dir = tmp_path / "chela"
    root, provable, edited, _live = _worktrees(chela_dir)
    monkeypatch.setattr(main.config, "CHELA_DIR", chela_dir)
    other = tmp_path / "elsewhere"
    wf_path = tmp_path / "WORKFLOW.md"
    monkeypatch.setattr(main.workflow, "load_workflow",
                        lambda p: SimpleNamespace(path=Path(p)) if Path(p) == wf_path
                        else (_ for _ in ()).throw(ValueError("unparseable")))
    monkeypatch.setattr(main.workflow, "resolve_workspace_root", lambda wf: other)
    third = _repo(other / "CMX-9")
    _dead_marker(third)
    (third / "feat.py").write_text(MUTATED)
    sent = []
    result = main.sweep_recovery_boot_scan([wf_path, tmp_path / "broken.md"],
                                           notify=lambda *a: sent.append(a))
    assert len(result["restored"]) == 2 and len(result["left"]) == 1
    (kind, summary, payload), = sent
    assert kind == "sweep_recovery_boot" and payload == result
    assert summary.startswith("⛔ killed sweep/self-check mutations after a restart — "
                              "restored 2: ")
    assert "; left for a human 1: " in summary and str(edited.resolve()) in summary
    assert (third / "feat.py").read_text() == FEAT


def test_the_daemon_boot_scan_is_silent_with_nothing_to_do(tmp_path, monkeypatch):
    monkeypatch.setattr(main.config, "CHELA_DIR", tmp_path)
    assert main.sweep_recovery_boot_scan([], notify=lambda *a: pytest.fail("noticed")) is None


def test_the_daemon_runs_the_boot_scan_before_its_first_tick():
    src = Path(main.__file__).read_text()
    scan = src.index("        sweep_recovery_boot_scan(DISPATCH_WORKFLOWS)")
    assert scan < src.index("    while not stop.stopping:")


# --- the nightly sweep's own worktree --------------------------------------------------------


def _nightly(tmp_path, monkeypatch, *, owner, recovered_as=("", "")):
    wf = SimpleNamespace(path=tmp_path / "repo" / "WORKFLOW.md")
    monkeypatch.setattr(sweep_nightly, "_git", lambda *a: "abc123")
    monkeypatch.setattr(sweep_nightly, "base_ref", lambda wf: "origin/dev")
    monkeypatch.setattr(sweep_nightly, "window_start", lambda *a: 0.0)
    monkeypatch.setattr(sweep_nightly, "merged_commits", lambda *a: [
        {"sha": "a" * 40, "subject": "s", "tier": sweep_nightly.risktier.STANDARD}])
    monkeypatch.setattr(sweep_nightly, "read_owed", lambda *a: [])
    monkeypatch.setattr("chela.workflow.resolve_workspace_root", lambda wf: tmp_path / "wts")
    checked_out = []
    monkeypatch.setattr("chela.worktree.detached_worktree",
                        lambda *a: checked_out.append(a) or (a[2], True))
    monkeypatch.setattr("chela.worktree.remove_worktree", lambda *a: None)
    monkeypatch.setattr(sweep_nightly, "_sweep_and_file", lambda *a: None)
    monkeypatch.setattr(sweep, "live_owner", lambda wt: owner)
    recovered = []
    monkeypatch.setattr(sweep, "recover", lambda wt: recovered.append(wt) or recovered_as)
    result: dict = {}
    sweep_nightly._run(wf, 1e9, tmp_path, None, result)
    return result, checked_out, recovered


def test_the_nightly_sweep_never_resets_a_worktree_a_live_sweep_is_mutating(tmp_path,
                                                                            monkeypatch):
    result, checked_out, recovered = _nightly(tmp_path, monkeypatch, owner=4242)
    assert result["status"] == sweep_nightly.CANNOT_VERIFY
    assert result["why"] == (f"the nightly worktree {(tmp_path / 'wts' / 'nightly-sweep')} "
                             "is being mutated by a live sweep (pid 4242) — not reset")
    assert checked_out == [] and recovered == []


def test_the_nightly_sweep_restores_first_then_checks_out(tmp_path, monkeypatch, caplog):
    result, checked_out, recovered = _nightly(tmp_path, monkeypatch, owner=None)
    assert recovered == [(tmp_path / "wts" / "nightly-sweep")]
    assert len(checked_out) == 1 and "status" not in result
    assert "nightly sweep:" not in caplog.text


@pytest.mark.parametrize("recovered_as, said", [
    (("restored feat.py", ""), "nightly sweep: restored feat.py"),
    (("", "three-way"), "nightly sweep: three-way — the checkout resets it"),
])
def test_the_nightly_sweep_says_what_it_restored_or_discarded(tmp_path, monkeypatch, caplog,
                                                             recovered_as, said):
    caplog.set_level("WARNING", logger=sweep_nightly.log.name)
    result, checked_out, _ = _nightly(tmp_path, monkeypatch, owner=None,
                                      recovered_as=recovered_as)
    assert said in caplog.text and len(checked_out) == 1


# --- the CLI entry points restore first too (judge self-check / sweep) ----------------------


def test_a_self_check_run_on_a_killed_one_restores_it_first(tmp_path):
    """The next ``judge self-check`` in the same tree restores before it measures anything."""
    root = _killed_self_check(tmp_path)
    try:
        out = subprocess.run(
            [sys.executable, "-c", "from chela.main import main; main()", "judge",
             "self-check", "--cwd", str(root), "--experiments", str(tmp_path / "experiments.json"),
             "--test-cmd", f'"{sys.executable}" -c "pass"'],
            env={**os.environ, "CHELA_DIR": str(tmp_path / "chela-dir")},
            capture_output=True, text=True, timeout=300)
        assert (root / "feat.py").read_text() == FEAT, out.stdout + out.stderr
        assert not sweep.marker_path(root).exists()
    finally:
        _git(root, "checkout", "--", "feat.py")


def test_head_blobs_pins_its_git_call(tmp_path, monkeypatch):
    calls = []

    def run(argv, **kw):
        calls.append((argv, kw))
        return SimpleNamespace(returncode=0, stderr="",
                               stdout="100644 blob abc\tfeat.py\x00100644 blob def\tb c.py\x00")

    monkeypatch.setattr(sweep.subprocess, "run", run)
    assert sweep._head_blobs(tmp_path, ["feat.py", "b c.py"]) == (
        {"feat.py": "abc", "b c.py": "def"}, "")
    assert calls == [(["git", "-C", str(tmp_path), "ls-tree", "-r", "-z", "HEAD", "--",
                       "feat.py", "b c.py"], {"capture_output": True, "text": True,
                                              "timeout": 60})]


def test_head_blobs_fails_closed_when_git_cannot_run(tmp_path, monkeypatch):
    def run(argv, **kw):
        raise subprocess.TimeoutExpired(argv, 60)

    monkeypatch.setattr(sweep.subprocess, "run", run)
    blobs, err = sweep._head_blobs(tmp_path, ["feat.py"])
    assert blobs == {} and err.startswith("could not read HEAD's tree to check it for a "
                                          "mutation: Command ")


def test_a_torn_last_journal_line_is_skipped_but_a_corrupt_middle_one_blocks(tmp_path):
    root = _repo(tmp_path / "r")
    sweep._journal(root, "feat.py", 2, MUTATED)
    j = sweep.journal_path(root)
    j.write_text(j.read_text() + '{"file": "fe')            # killed mid-append
    (root / "feat.py").write_text(MUTATED)
    blockers = sweep.mutation_blockers(root)
    assert blockers[0].startswith("feat.py:2 is a sweep/self-check MUTATION")
    j.write_text('{"file": "fe\n' + j.read_text())          # not the last line: unknown
    assert sweep.mutation_blockers(root)[0].startswith(f"the mutation journal {j} is unreadable")


def test_no_hint_when_nothing_matched(tmp_path):
    root = _repo(tmp_path / "r")
    sweep.marker_path(root).write_text("{}")
    out = sweep.mutation_blockers(root)
    assert not any("remove its entry" in b for b in out)


def test_the_trim_is_atomic(tmp_path, monkeypatch):
    root = _repo(tmp_path / "r")
    monkeypatch.setattr(sweep, "JOURNAL_MAX_BYTES", 10)
    sweep._journal(root, "a.py", 1, "x\n")
    replaced = []
    real = sweep.os.replace
    monkeypatch.setattr(sweep.os, "replace", lambda a, b: replaced.append((a, b)) or real(a, b))
    sweep._journal(root, "b.py", 1, "y\n")
    j = sweep.journal_path(root)
    assert replaced == [(j.with_suffix(".tmp"), j)]
