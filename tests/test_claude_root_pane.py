"""CMX-198: a window whose ROOT process is claude — no shell — is a claude window.

The orchestrator relaunches an agent as ``tmux new-window … claude --resume <sid>``, so the
pane's own pid IS claude and it has no claude child. Every resolver used to look at
children only, so that window had ``claude_pid None``: ``/api/agents`` gave it no cwd (the
sidebar filed it under "Other"), and ``window_type`` called it a shell, so Telegram
auto-topics never bound it.

All process state here is a fake ``/proc`` tree or a fake ``ps`` table; tmux is a stubbed
``subprocess.run``. No live tmux, never the operator's windows.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from chela import agent_manager, discovery, probecache, sessions
from chela.telegram.bindings import BindingRegistry
from chela.telegram.reconcile import live_agent_windows, reconcile_bindings

SID = "ff3314ed-0000-4000-8000-000000000000"

# pid → {argv, comm, kids}
TREE = {
    # claude launched as the pane's own process, no children
    100: {"argv": ["claude", "--remote-control", "openmmo-claude", "--resume", SID],
          "comm": "claude"},
    # claude as the pane root, WITH its own Bash-tool subshell child — whose argv carries
    # "claude" too. The root, not the child, is the window's claude.
    110: {"argv": ["claude", "--resume", SID], "comm": "claude", "kids": [111]},
    111: {"argv": ["/bin/bash", "-c", "source /h/.claude/shell-snapshots/s.sh"],
          "comm": "bash"},
    # a shell pane with a claude child — unchanged: the child
    200: {"argv": ["-bash"], "comm": "bash", "kids": [201]},
    201: {"argv": ["claude", "--resume", SID], "comm": "claude"},
    # a shell pane with nothing claude — stays a shell
    300: {"argv": ["-bash"], "comm": "bash", "kids": [301]},
    301: {"argv": ["vim", "notes.txt"], "comm": "vim"},
    # a dev server whose argv mentions a claude-named PATH — not claude
    400: {"argv": ["python", "/p/openmmo-claude/app.py"], "comm": "python"},
    # comm is not claude, but argv[0]'s basename is (a node claude that set its title)
    500: {"argv": ["/usr/local/bin/claude", "--resume", SID], "comm": "node"},
    # comm is claude though argv[0] is not (a node claude that only renamed its comm)
    510: {"argv": ["node", "/opt/claude-code/cli.js"], "comm": "claude"},
    # argv[0] merely STARTS with claude — not its name
    600: {"argv": ["claude-helper"], "comm": "claude-helper"},
}


def _proc(tmp_path: Path, tree: dict[int, dict]) -> Path:
    root = tmp_path / "proc"
    for pid, spec in tree.items():
        d = root / str(pid)
        (d / "task" / str(pid)).mkdir(parents=True)
        (d / "task" / str(pid) / "children").write_text(
            " ".join(map(str, spec.get("kids", []))))
        (d / "cmdline").write_bytes(b"\0".join(a.encode() for a in spec["argv"]) + b"\0")
        (d / "comm").write_text(spec["comm"] + "\n")
    return root


@pytest.fixture
def proc(tmp_path, monkeypatch):
    monkeypatch.setattr(sessions, "PROC", _proc(tmp_path, TREE))


# --- the shared rule ----------------------------------------------------------------

@pytest.mark.parametrize("pid,want", [
    (100, True), (110, True), (500, True), (510, True),
    (200, False), (300, False), (400, False), (600, False),
    (999, False),                                   # no such process
])
def test_pane_root_is_claude(proc, pid, want):
    assert sessions.pane_root_is_claude(pid) is want


# --- the batched resolvers (/proc) ----------------------------------------------------

@pytest.mark.parametrize("pane_pid,want", [
    (100, 100),     # claude-root, no children
    (110, 110),     # claude-root: the root, NOT its claude-ish Bash subshell
    (200, 201),     # shell + claude child: unchanged
    (300, None),    # shell, no claude: stays a shell row
    (400, None),    # a dev server under a claude-named path is not claude
])
def test_both_batched_resolvers_name_the_root(proc, pane_pid, want):
    assert sessions._direct_claude_pid(pane_pid) == want
    assert sessions._claude_pid(pane_pid) == want


def test_load_panes_gives_a_claude_root_window_its_claude(proc, monkeypatch):
    """The snapshot the batched endpoints read: both pid fields are the root."""
    def run(argv, **kw):
        assert argv[:2] == ["tmux", "list-windows"]
        return subprocess.CompletedProcess(
            argv, 0, stdout="@16\tclaude\t/p/openmmo-claude\t100\t0\n"
                            "@17\tbash\t/p/x\t300\t0\n", stderr="")
    monkeypatch.setattr(sessions.subprocess, "run", run)
    panes = sessions._load_panes()
    assert panes["@16"].claude_pid == 100 and panes["@16"].direct_claude_pid == 100
    assert panes["@17"].claude_pid is None and panes["@17"].direct_claude_pid is None


# --- no /proc: the ps table, and the per-pid ps fallback ----------------------------

@pytest.fixture
def no_proc(monkeypatch, tmp_path):
    monkeypatch.setattr(sessions, "PROC", tmp_path / "nonexistent-proc")
    monkeypatch.setattr(sessions, "_PROC_HOST", False)


def test_ps_table_resolves_a_claude_root(no_proc):
    t = sessions._PsTable(
        ppid={700: 1, 701: 700, 800: 1, 801: 800, 900: 1},
        argv={700: ["claude", "--resume", SID],
              701: ["/bin/bash", "-c", "source /h/.claude/shell-snapshots/s.sh"],
              800: ["-zsh"], 801: ["/usr/bin/claude"],
              900: ["-zsh"]})
    token = sessions._PS.set(t)
    try:
        assert sessions._direct_claude_pid(700) == 700
        assert sessions._claude_pid(700) == 700
        assert sessions._direct_claude_pid(800) == 801         # shell + child: unchanged
        assert sessions._claude_pid(800) == 801
        assert sessions._direct_claude_pid(900) is None        # bare shell
        assert sessions._claude_pid(900) is None
    finally:
        sessions._PS.reset(token)


def test_per_pid_ps_fallback_resolves_a_claude_root(no_proc, monkeypatch):
    """No /proc and no table: the root is asked via ``ps``, and pgrep never runs."""
    calls: list[list[str]] = []

    def sh(argv):
        calls.append(list(argv))
        if argv[:3] == ["ps", "-o", "comm="]:
            return {"700": "/usr/local/bin/claude\n", "710": "node\n",
                    "900": "-zsh\n"}[argv[-1]]
        if argv[:3] == ["ps", "-o", "args="]:      # 710: comm misses, argv[0] names it
            return {"710": "/usr/local/bin/claude --resume x\n", "900": "-zsh\n"}[argv[-1]]
        return ""                                       # pgrep / children: none
    monkeypatch.setattr(sessions, "_sh", sh)
    assert sessions._direct_claude_pid(700) == 700
    assert sessions._claude_pid(700) == 700
    assert not any(c[0] == "pgrep" for c in calls), calls
    assert sessions._direct_claude_pid(710) == 710
    assert sessions._direct_claude_pid(900) is None


# --- the per-window probe: agent_manager -------------------------------------------

class _Tmux:
    """A stub ``subprocess.run`` for tmux display-message + pgrep — never a live server."""

    def __init__(self, pane_pids: dict[str, str], pgrep: dict[str, str]):
        self.pane_pids, self.pgrep = pane_pids, pgrep
        self.calls: list[tuple[list[str], dict]] = []

    def __call__(self, argv, **kw):
        self.calls.append((list(argv), kw))
        if argv[:2] == ["tmux", "display-message"] and argv[-1] == "#{pane_pid}":
            wid = argv[argv.index("-t") + 1].split(":", 1)[1]
            return subprocess.CompletedProcess(argv, 0, stdout=self.pane_pids[wid] + "\n",
                                               stderr="")
        if argv[:2] == ["tmux", "display-message"]:
            return subprocess.CompletedProcess(argv, 0, stdout="bash\n", stderr="")
        if argv[0] == "pgrep":
            out = self.pgrep.get(argv[argv.index("-P") + 1], "")
            return subprocess.CompletedProcess(argv, 0 if out else 1, stdout=out, stderr="")
        raise AssertionError(f"unexpected spawn {argv}")


@pytest.fixture
def tmux(proc, monkeypatch):
    monkeypatch.setenv("CHELA_TMUX_SESSION", "s")
    t = _Tmux({"@16": "100", "@20": "200", "@30": "300"}, {"200": "201\n"})
    monkeypatch.setattr(agent_manager.subprocess, "run", t)
    probecache.clear()
    yield t
    probecache.clear()


def test_per_window_claude_pid_names_the_root(tmux):
    assert agent_manager.claude_pid("@16") == 100
    assert tmux.calls == [(["tmux", "display-message", "-t", "s:@16", "-p", "#{pane_pid}"],
                           {"capture_output": True, "text": True, "timeout": 5})]
    assert agent_manager.claude_pid("@20") == 201           # shell + child: unchanged
    assert agent_manager.claude_pid("@30") is None          # bare shell


def test_is_claude_running_sees_the_root(tmux):
    assert agent_manager.is_claude_running("@16") is True
    assert [c[0][0] for c in tmux.calls] == ["tmux"]        # answered before pgrep
    assert agent_manager.is_claude_running("@20") is True
    assert agent_manager.is_claude_running("@30") is False


def test_window_type_of_a_claude_root_window_is_claude(tmux):
    assert agent_manager.window_type("@16") == "claude"
    assert agent_manager.window_type("@20") == "claude"
    assert agent_manager.window_type("@30") == "shell"


def test_auto_topics_bind_a_claude_root_window(tmux, monkeypatch):
    """The second symptom: reconcile keeps only ``window_type == "claude"`` windows."""
    live = {"@16": "openmmo-claude", "@20": "coder", "@30": "shell"}
    monkeypatch.setattr(discovery, "get_windows_by_id", lambda: dict(live))
    got_live, agents = live_agent_windows()
    assert got_live == live and agents == {"@16", "@20"}

    created: list[str] = []

    class Api:
        def create_topic(self, name):
            created.append(name)
            return str(40 + len(created))

        def rename_topic(self, thread_id, name):
            return True

        def close_topic(self, thread_id):
            return True

    reg = BindingRegistry("777")
    assert reconcile_bindings(reg, got_live, agents, Api()) is True
    assert sorted(created) == ["coder", "openmmo-claude"]
    assert reg.thread_for_window("@16") is not None
    assert reg.thread_for_window("@30") is None
