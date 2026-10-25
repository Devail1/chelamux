"""⛔ CMX-340: ``--pause --all`` must never SIGSTOP a process that is not THIS run's judge.

Measured 2026-10-25: adopt-2 (``status=done``, ``judge_state=running``, ``judge_window_id=@261``
from a window that died in a crash) resolved to another agent's claude, because ``tmux
display-message -t <missing>`` falls back to the current pane and exits 0. The freeze stopped
that agent's whole tree, its running suite included, for 2h27m.

``os.kill`` is a spy everywhere here: no real pid is ever signalled.
"""
from __future__ import annotations

import os
import shutil
import signal
import subprocess
from types import SimpleNamespace

import pytest

from chela import agent_manager, dispatcher, epoch, fleet, judge, probecache, procguard
from tests.test_fleet_freeze import _judge_identity

OTHER_PANE, OTHER_CLAUDE = 500, 3527247        # the unrelated agent tmux falls back to
TREE = {OTHER_CLAUDE: OTHER_PANE, 3527300: OTHER_CLAUDE, OTHER_PANE: 1}


class _FallbackTmux:
    """``subprocess.run`` for a tmux that, like the real one, answers ANY ``display-message``
    target — a missing ``@id`` or a name prefix — with the window it falls back to."""

    def __init__(self, windows: dict[str, tuple[str, int]], current: str):
        self.windows, self.current = windows, current       # wid -> (name, pane pid)
        self.calls: list[list[str]] = []

    def __call__(self, argv, **kw):
        self.calls.append(list(argv))
        if argv[:2] == ["tmux", "display-message"]:
            target = argv[argv.index("-t") + 1].split(":", 1)[1]
            wid = next((w for w, (name, _) in self.windows.items()
                        if target in (w, name) or name.startswith(target)), self.current)
            name, pane = self.windows[wid]
            return subprocess.CompletedProcess(argv, 0, stdout=f"{wid}\t{name}\t{pane}\n",
                                               stderr="")
        if argv[0] == "pgrep":
            pane = int(argv[argv.index("-P") + 1])
            kids = [p for p, parent in TREE.items() if parent == pane]
            return subprocess.CompletedProcess(argv, 0 if kids else 1,
                                               stdout="".join(f"{k}\n" for k in kids), stderr="")
        raise AssertionError(f"unexpected spawn {argv}")


@pytest.fixture
def tmux(monkeypatch):
    monkeypatch.setenv("CHELA_TMUX_SESSION", "s")
    t = _FallbackTmux({"@29": ("cmx-338", OTHER_PANE)}, current="@29")
    monkeypatch.setattr(agent_manager.subprocess, "run", t)
    from chela import sessions
    monkeypatch.setattr(sessions, "pane_root_is_claude", lambda pid: False)
    probecache.clear()
    yield t
    probecache.clear()


# --- 1. the wid -> pid resolvers ask for the window that EXISTS ---------------------------

def test_a_missing_window_id_resolves_to_no_pid_not_the_current_window(tmux):
    assert agent_manager.claude_pid("@261") is None
    assert agent_manager.is_claude_running("@261") is False
    assert [c[0] for c in tmux.calls] == ["tmux", "tmux"]           # never reached pgrep


def test_the_window_that_does_exist_still_resolves(tmux):
    assert agent_manager.claude_pid("@29") == OTHER_CLAUDE
    assert agent_manager.is_claude_running("@29") is True
    assert tmux.calls[0] == ["tmux", "display-message", "-t", "s:@29", "-p",
                             "#{window_id}\t#{window_name}\t#{pane_pid}"]


@pytest.mark.parametrize("target, found", [
    ("cmx-338", OTHER_CLAUDE),      # the exact name
    ("cmx-3", None),                # tmux takes a prefix: not the window asked for
    ("judge-x", None),              # no such name: tmux falls back to the current window
])
def test_a_window_name_must_match_exactly(tmux, target, found):
    assert agent_manager.claude_pid(target) == found


@pytest.mark.parametrize("rc, out", [
    (1, "@29\tcmx-338\t500\n"),           # tmux said no
    (0, "500\n"),                          # not the three fields asked for
    (0, "@29\tcmx-338\t\n"),               # no pane pid
])
def test_an_unreadable_answer_resolves_to_none(monkeypatch, rc, out):
    monkeypatch.setattr(agent_manager.subprocess, "run",
                        lambda argv, **kw: subprocess.CompletedProcess(argv, rc, stdout=out))
    probecache.clear()
    assert agent_manager._exact_pane_pid("@29") is None


@pytest.mark.skipif(shutil.which("tmux") is None, reason="tmux not installed")
def test_real_tmux_falls_back_and_the_resolver_refuses_it(tmp_path, monkeypatch):
    """⭐ The negative control on a real, private tmux server: a raw ``display-message -t``
    on a missing window DOES answer with another window, and the resolver does not."""
    monkeypatch.setenv("TMUX_TMPDIR", str(tmp_path))
    monkeypatch.delenv("TMUX", raising=False)
    monkeypatch.setenv("CHELA_TMUX_SESSION", "cmx340")
    env = {**os.environ}
    subprocess.run(["tmux", "new-session", "-d", "-s", "cmx340", "-n", "alive", "sleep", "60"],
                   check=True, env=env)
    try:
        raw = subprocess.run(["tmux", "display-message", "-t", "cmx340:@9999", "-p",
                              "#{window_id}"], capture_output=True, text=True, env=env)
        assert raw.returncode == 0 and raw.stdout.strip().startswith("@")    # the fallback
        probecache.clear()
        assert agent_manager._exact_pane_pid("@9999") is None
        assert agent_manager._exact_pane_pid(raw.stdout.strip()).isdigit()
        assert agent_manager._exact_pane_pid("aliv") is None                   # a prefix
        assert agent_manager._exact_pane_pid("alive").isdigit()
    finally:
        subprocess.run(["tmux", "kill-server"], env=env, capture_output=True)


# --- 2. the freeze: never a finished run, never an unproven pid ---------------------------

@pytest.fixture
def freeze(tmp_path, monkeypatch, tmux):
    """A fleet with one judging run whose window is GONE; tmux falls back to @29."""
    monkeypatch.setattr(fleet.config, "CHELA_DIR", tmp_path)
    monkeypatch.setattr(judge, "judge_logs_dir", lambda: tmp_path / "judge-logs")
    sent: list[tuple[int, int]] = []
    monkeypatch.setattr(fleet.os, "kill", lambda pid, sig: sent.append((pid, sig)))
    monkeypatch.setattr(procguard, "_ppids", lambda: dict(TREE))
    monkeypatch.setattr(judge, "live_judge_run", lambda task: None)
    monkeypatch.setattr(fleet.inbox, "_selfcheck_scan", lambda: [])
    _judge_identity(monkeypatch, tmp_path, {OTHER_CLAUDE: tmp_path / "wt" / "CMX-338"})
    run = {"task_id": "adopt-2", "status": "awaiting_review", "judge_state": judge.J_RUNNING,
           "judge_window_id": "@261", "judge_window_epoch": "e1", "judge_started_at": "r1",
           "workflow_path": str(tmp_path / "WORKFLOW.md")}
    monkeypatch.setattr(dispatcher, "list_runs", lambda: [run])
    return SimpleNamespace(sent=sent, run=run, tmp=tmp_path)


def test_a_stale_window_id_signals_nothing(freeze):
    state = fleet.pause_all()
    assert freeze.sent == []
    assert state.get("judges") == {}


def test_negative_control_the_old_resolver_stops_the_unrelated_agent(freeze, monkeypatch):
    """⭐ Proves the spy can see the incident: the pre-CMX-340 resolver (tmux's answer,
    unchecked) hands the freeze the other agent, and its whole tree is stopped."""
    monkeypatch.setattr(fleet, "_judge_agent_pid",
                        lambda run: _old_claude_pid(run["judge_window_id"]))
    fleet.pause_all()
    assert (OTHER_CLAUDE, signal.SIGSTOP) in freeze.sent
    assert (3527300, signal.SIGSTOP) in freeze.sent


def _old_claude_pid(wid: str) -> int | None:
    """The resolver as it was: whatever pane tmux answered with."""
    out = subprocess.run(["tmux", "display-message", "-t", f"s:{wid}", "-p",
                          "#{window_id}\t#{window_name}\t#{pane_pid}"],
                         capture_output=True, text=True, timeout=5)
    pane = out.stdout.strip().split("\t")[-1]
    kids = subprocess.run(["pgrep", "-P", pane, "-f", "claude"], capture_output=True,
                          text=True, timeout=5).stdout.split()
    return int(kids[0]) if kids else None


@pytest.mark.parametrize("status", ["done", "closed", "failed"])
def test_a_finished_run_is_never_frozen_whatever_its_judge_state(freeze, monkeypatch, status):
    """Even with a live judge record and a provable judge window: the run is over."""
    freeze.run.update(status=status, judge_window_id="@29")
    _judge_identity(monkeypatch, freeze.tmp, {OTHER_CLAUDE: freeze.tmp / "wt" / "judge-adopt-2"})
    monkeypatch.setattr(judge, "live_judge_run", lambda task: {"pid": OTHER_PANE})
    assert fleet.pause_all()["judges"] == {}
    assert freeze.sent == []


def test_control_the_same_run_under_review_is_frozen(freeze, monkeypatch):
    freeze.run.update(judge_window_id="@29")
    _judge_identity(monkeypatch, freeze.tmp, {OTHER_CLAUDE: freeze.tmp / "wt" / "judge-adopt-2"})
    assert fleet.pause_all()["judges"]["adopt-2"]["pids"] == [OTHER_CLAUDE, 3527300]
    assert freeze.sent == [(OTHER_CLAUDE, signal.SIGSTOP), (3527300, signal.SIGSTOP)]


@pytest.mark.parametrize("cwd, frozen", [
    ("judge-adopt-2", True),             # the judge worktree itself
    ("judge-adopt-2/chela", True),       # a directory inside it
    ("judge-adopt-22", False),           # a sibling that shares the prefix
    ("CMX-338", False),                  # another agent's worktree
    (None, False),                       # unreadable cwd
])
def test_the_window_claude_must_live_in_this_runs_judge_worktree(freeze, monkeypatch, caplog,
                                                                 cwd, frozen):
    freeze.run.update(judge_window_id="@29")
    (freeze.tmp / "wt" / "judge-adopt-2" / "chela").mkdir(parents=True)
    _judge_identity(monkeypatch, freeze.tmp, {OTHER_CLAUDE: freeze.tmp / "wt" / cwd} if cwd else {})
    with caplog.at_level("WARNING", logger="chela.fleet"):
        assert (fleet._judge_agent_pid(freeze.run) == OTHER_CLAUDE) is frozen
    said = [r.getMessage() for r in caplog.records]
    if cwd and not frozen:
        assert said == [f"fleet pause: adopt-2's judge window @29 holds pid {OTHER_CLAUDE} in "
                        f"{(freeze.tmp / 'wt' / cwd).resolve()}, not its judge worktree: "
                        "not freezing it"]
    else:
        assert said == []


@pytest.mark.parametrize("over, now", [
    ({"judge_window_epoch": "e0"}, "e1"),      # stamped by a tmux server that is gone
    ({"judge_window_epoch": None}, "e1"),      # unstamped: identity unprovable
    ({}, None),                                # tmux can't say which server is running
    ({"judge_window_id": None}, "e1"),
    ({"workflow_path": None}, "e1"),
])
def test_an_unprovable_window_never_reaches_tmux(freeze, monkeypatch, tmux, over, now):
    freeze.run.update({"judge_window_id": "@29", **over})
    _judge_identity(monkeypatch, freeze.tmp, {OTHER_CLAUDE: freeze.tmp / "wt" / "judge-adopt-2"})
    monkeypatch.setattr(epoch, "current", lambda: now)
    assert fleet._judge_agent_pid(freeze.run) is None
    assert tmux.calls == []


def test_control_the_provable_window_resolves(freeze, monkeypatch, tmux):
    freeze.run.update(judge_window_id="@29")
    _judge_identity(monkeypatch, freeze.tmp, {OTHER_CLAUDE: freeze.tmp / "wt" / "judge-adopt-2"})
    assert fleet._judge_agent_pid(freeze.run) == OTHER_CLAUDE


def test_an_unloadable_workflow_never_reaches_tmux(freeze, monkeypatch, tmux):
    from chela import workflow
    freeze.run.update(judge_window_id="@29")
    monkeypatch.setattr(workflow, "load_workflow_cached", lambda p: SimpleNamespace(workflow=None))
    assert fleet._judge_agent_pid(freeze.run) is None
    assert tmux.calls == []


def test_the_judges_own_run_record_is_still_frozen_without_a_window(freeze, monkeypatch):
    """A detached judge (its agent window long gone) is still found through its OWN record."""
    monkeypatch.setattr(judge, "live_judge_run", lambda task: {"pid": 3527300})
    assert fleet.pause_all()["judges"]["adopt-2"]["pids"] == [3527300]
