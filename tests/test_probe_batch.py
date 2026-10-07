"""CMX-17: the polled endpoints read ONE shared pane snapshot — and say exactly the same.

``/api/agents``, ``/api/agents/context`` and ``/api/orchestrator/status`` used to probe tmux
and ``pgrep`` once per window (94% of the dashboard's spawns in the CMX-15 profile). They
now run inside :func:`chela.probecache.batch`, where those probes answer from
``sessions.panes()``. That is only a fix if nothing a client sees moves, so the core test
here is GOLDEN: the same live state — a scratch tmux server of real windows and real
processes, never the operator's — rendered with the batch switched off (the old per-window
path) and on, at 5 and 20 windows, must be byte-identical.

The fleet deliberately includes the case where the two readings could disagree: a claude
behind a wrapper script, which ``Pane.claude_pid`` (a multi-generation walk) finds and
``pgrep -P <pane_pid> -f claude`` (the old probe) does not. The dashboard must keep saying
"not running" there, so the batch reads ``Pane.direct_claude_pid``.
"""
from __future__ import annotations

import contextlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path

import pytest

from chela import agent_manager, config, discovery, epoch, inbox, probecache, sessions, transcripts
from chela.dashboard import app as dash

TMUX_BIN = shutil.which("tmux")
requires_tmux = pytest.mark.skipif(TMUX_BIN is None, reason="tmux not installed")

ENDPOINTS = ("/api/agents", "/api/agents/context", "/api/orchestrator/status")


@pytest.fixture(autouse=True)
def _fresh_caches(monkeypatch):
    """Every test starts cold: no pane snapshot or shared epoch from a previous server."""
    monkeypatch.setattr(sessions, "_panes_cache", {"ts": 0.0, "panes": {}})
    probecache.clear()
    yield
    probecache.clear()


# --- a scratch tmux server, the way tests/test_epoch_live.py builds one ----------------

class Fleet:
    """A private tmux server whose windows run known process shapes."""

    KINDS = ("direct", "wrapped", "server", "shell")

    def __init__(self, sock: str, scripts: Path, root: Path):
        assert sock.startswith("chelatest-")
        self.sock, self.scripts, self.root = sock, scripts, root
        self.session = config.current_session()
        self.n = 0

    def tmux(self, *args: str, check: bool = True) -> subprocess.CompletedProcess:
        return subprocess.run([TMUX_BIN, "-L", self.sock, *args],
                              capture_output=True, text=True, check=check)

    def _cmd(self, kind: str) -> str:
        return {"direct": f"exec sh {self.scripts}/k_direct.sh",
                "wrapped": f"exec sh {self.scripts}/k_wrapped.sh",
                # The pane process ITSELF (a python named `node`, a dev-server command): a
                # non-interactive sh keeps its child in its own process group, so tmux
                # would report the foreground command as `sh`.
                "server": f"exec {self.scripts}/node -c 'import time; time.sleep(600)'",
                "shell": "exec sh"}[kind]

    def add(self, kind: str) -> str:
        """One window of ``kind``; returns its name. The first starts the server."""
        self.n += 1
        name = f"w{self.n}-{kind}"
        cwd = self.root / name
        cwd.mkdir()
        if self.n == 1:
            self.tmux("new-session", "-d", "-s", self.session, "-n", name, "-c", str(cwd),
                      "-x", "200", "-y", "50", self._cmd(kind))
        else:
            self.tmux("new-window", "-d", "-t", f"{self.session}:", "-n", name,
                      "-c", str(cwd), self._cmd(kind))
        return name

    def build(self, n: int) -> None:
        for i in range(n):
            self.add(self.KINDS[i % len(self.KINDS)])

    def settle(self) -> None:
        """Wait until every window's processes are up (outside any batch: fresh probes)."""
        deadline = time.time() + 15
        while True:
            wins = discovery.get_all_windows()
            ready = all((agent_manager.claude_pid(wid) is not None) == name.endswith("-direct")
                        and (name.endswith(("-direct", "-shell"))
                             or agent_manager.pane_command(wid) == "node"
                             or name.endswith("-wrapped") and _grandchild_up(wid))
                        for name, wid in wins.items())
            if len(wins) == self.n and ready:
                return
            assert time.time() < deadline, f"fleet never settled: {wins} " + str(
                {n: (agent_manager.claude_pid(w), agent_manager.pane_command(w),
                     _grandchild_up(w)) for n, w in wins.items()})
            time.sleep(0.1)


def _grandchild_up(wid: str) -> bool:
    pane = sessions._load_panes().get(wid)
    return pane is not None and pane.claude_pid is not None


@pytest.fixture
def fleet(tmp_path, monkeypatch):
    sock = f"chelatest-{os.getpid()}-{uuid.uuid4().hex[:6]}"
    # Script dir outside tmp_path, whose name embeds the test name: nothing on any command
    # line may contain "claude" except the process meant to look like one.
    scripts = Path(tempfile.mkdtemp(prefix="cmx17s", dir="/tmp"))
    sleeper = f"{sys.executable} -c 'import time; time.sleep(600)'"
    (scripts / "k_direct.sh").write_text(f"{sleeper} claude --print; true\n")
    (scripts / "k_inner.sh").write_text(f"{sleeper} claude; true\n")
    (scripts / "k_wrapped.sh").write_text(f"sh {scripts}/k_inner.sh; true\n")
    (scripts / "node").symlink_to(sys.executable)

    shim = tmp_path / "shim"
    shim.mkdir()
    (shim / "tmux").write_text(f'#!/bin/sh\nexec {TMUX_BIN} -L {sock} "$@"\n')
    (shim / "tmux").chmod(0o755)
    tmuxdir = tempfile.mkdtemp(prefix="cmx17", dir="/tmp")
    monkeypatch.setenv("PATH", f"{shim}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("TMUX_TMPDIR", tmuxdir)
    monkeypatch.delenv("TMUX", raising=False)
    monkeypatch.delenv("TMUX_PANE", raising=False)

    root = tmp_path / "cwds"
    root.mkdir()
    f = Fleet(sock, scripts, root)
    yield f
    f.tmux("kill-server", check=False)
    shutil.rmtree(tmuxdir, ignore_errors=True)
    shutil.rmtree(scripts, ignore_errors=True)


def _seed(fleet: Fleet, monkeypatch, tmp_path) -> None:
    """Give the state something to say: statuses, transcripts and an orchestrator."""
    wins = discovery.get_all_windows()
    pids = {wid: agent_manager.claude_pid(wid) for wid in wins.values()}
    by_pid, cwd_by_pid = {}, {}
    for i, (wid, pid) in enumerate(sorted(pids.items())):
        if pid is not None:
            by_pid[pid] = ("busy", "idle", "waiting")[i % 3]
            cwd_by_pid[pid] = f"/feed/{wid}"
    status = {"by_pid": by_pid, "by_cwd": {}, "cwd_by_pid": cwd_by_pid,
              "session_by_pid": {}, "started_by_pid": {}}
    monkeypatch.setattr(agent_manager, "session_status_map", lambda force=False: status)

    projects = tmp_path / "projects"
    monkeypatch.setattr(transcripts, "CLAUDE_PROJECTS_DIR", projects)
    for i, name in enumerate(sorted(wins)):
        d = projects / transcripts.encode_cwd(str(fleet.root / name))
        d.mkdir(parents=True)
        rec = {"type": "assistant", "timestamp": "2026-10-07T00:00:00Z",
               "message": {"model": "m", "usage": {"input_tokens": 1000 * (i + 1)}}}
        (d / f"{uuid.uuid4()}.jsonl").write_text(json.dumps(rec) + "\n")

    direct = next(wid for name, wid in wins.items() if name.endswith("-direct"))
    assert inbox.register(direct, source="dashboard").get("ok")


def _render(client) -> dict[str, bytes]:
    return {ep: client.get(ep).get_data() for ep in ENDPOINTS}


@contextlib.contextmanager
def _no_batch():
    """The pre-CMX-17 path: every probe asked per window."""
    yield None


@requires_tmux
@pytest.mark.parametrize("n", [5, 20])
def test_golden_batched_output_is_byte_identical_to_the_per_window_probe(
        n, fleet, monkeypatch, tmp_path):
    fleet.build(n)
    fleet.settle()
    _seed(fleet, monkeypatch, tmp_path)
    client = dash.app.test_client()

    with monkeypatch.context() as m:
        m.setattr(probecache, "batch", lambda windows=None: _no_batch())
        before = _render(client)
    after = _render(client)

    assert after == before

    # Not vacuous: the state really does exercise every distinction the batch must keep.
    agents = json.loads(after["/api/agents"])
    assert len(agents) == n
    kinds = {a["name"].split("-", 1)[1]: (a["claude_running"], a["window_type"])
             for a in agents}
    assert kinds["direct"] == (True, "claude")
    assert kinds["wrapped"][0] is False          # the wrapper case: pgrep semantics kept
    assert kinds["shell"] == (False, "shell")
    assert kinds["server"] == (False, "server")
    assert {a["session_status"] for a in agents if a["claude_running"]} <= {"busy", "idle",
                                                                              "waiting"}
    assert any(a["session_status"] for a in agents)
    ctx = json.loads(after["/api/agents/context"])
    assert ctx and all(r["name"].endswith("-direct") for r in ctx)
    orch = json.loads(after["/api/orchestrator/status"])
    assert orch["wid"] and orch["state"]


class _Spawns:
    def __init__(self):
        self.argv: list[list[str]] = []

    def __call__(self, real):
        def init(proc, args, *a, **kw):
            if isinstance(args, (list, tuple)):
                self.argv.append([str(x) for x in args])
            return real(proc, args, *a, **kw)
        return init


@requires_tmux
def test_spawns_per_poll_do_not_grow_with_the_fleet(fleet, monkeypatch, tmp_path):
    """The point of the change: a poll costs O(1) spawns, not 2-4 per window."""
    fleet.build(12)
    fleet.settle()
    _seed(fleet, monkeypatch, tmp_path)
    client = dash.app.test_client()
    spawns = _Spawns()
    monkeypatch.setattr(subprocess.Popen, "__init__", spawns(subprocess.Popen.__init__))
    for ep in ENDPOINTS:
        spawns.argv.clear()
        monkeypatch.setattr(sessions, "_panes_cache", {"ts": 0.0, "panes": {}})
        probecache.clear()
        assert client.get(ep).status_code == 200
        # A per-window probe names its window (`-t`); the one shared epoch read does not.
        per_window = [a for a in spawns.argv
                      if a[0] == "pgrep" or ("display-message" in a and "-t" in a)]
        assert per_window == [], f"{ep} still probes per window: {per_window}"
        assert len(spawns.argv) <= 3, f"{ep}: {spawns.argv}"


@requires_tmux
def test_a_new_window_shows_on_the_next_poll_and_a_closed_one_is_gone(
        fleet, monkeypatch, tmp_path):
    """The shared snapshot must not hide fleet changes: TTL is stretched to a minute here,
    so ONLY the forced re-read of a missing window can make the new one show."""
    monkeypatch.setattr(sessions, "_TTL", 60.0)
    fleet.build(3)
    fleet.settle()
    client = dash.app.test_client()
    first = {a["name"]: a for a in client.get("/api/agents").get_json()}
    assert len(first) == 3

    new = fleet.add("direct")
    fleet.settle()
    rows = {a["name"]: a for a in client.get("/api/agents").get_json()}
    assert new in rows
    assert rows[new]["claude_running"] is True
    assert rows[new]["window_type"] == "claude"

    gone = next(iter(first))
    fleet.tmux("kill-window", "-t", f"{fleet.session}:{first[gone]['window_id']}")
    rows = {a["name"]: a for a in client.get("/api/agents").get_json()}
    assert gone not in rows and new in rows


# --- the batch itself, without tmux -------------------------------------------------

def test_batch_reads_the_snapshot_and_rereads_once_for_a_missing_window(monkeypatch):
    calls = []
    maps = [{"@1": sessions.Pane(wid="@1", command="node", path="/a", direct_claude_pid=7)},
            {"@1": sessions.Pane(wid="@1", command="node", path="/a", direct_claude_pid=7),
             "@2": sessions.Pane(wid="@2", command="bash", path="/b")}]

    def fake_panes(force=False):
        calls.append(force)
        return maps[min(len(calls) - 1, 1)]

    monkeypatch.setattr(sessions, "panes", fake_panes)
    with probecache.batch({"one": "@1", "two": "@2"}):
        assert agent_manager.claude_pid("@1") == 7
        assert agent_manager.pane_command("@1") == "node"
        assert agent_manager.window_type("@1", False) == "server"
        assert calls == [False]
        assert agent_manager.claude_pid("@2") is None       # new: forces one re-read
        assert agent_manager.pane_command("@2") == "bash"
        assert discovery.get_window_cwd("two") == "/b"
        assert agent_manager.claude_pid("@9") is None       # absent: no second re-read
        assert discovery.get_window_cwd("nobody") is None
        assert calls == [False, True]
    assert probecache.active() is None


def test_epoch_is_shared_inside_a_batch_and_fresh_outside(monkeypatch):
    asked = []

    def fake_run(argv, **kw):
        asked.append(argv)
        return subprocess.CompletedProcess(argv, 0, stdout=f"100-{len(asked)}\n", stderr="")

    monkeypatch.setattr(epoch.subprocess, "run", fake_run)
    with probecache.batch():
        assert epoch.current() == "100-1"
        assert epoch.current() == "100-1"
    with probecache.batch():                  # another request inside the TTL: shared
        assert epoch.current() == "100-1"
    assert len(asked) == 1
    assert epoch.current() == "100-2"         # outside a batch: asked fresh, as before
    assert epoch.current() == "100-3"


def test_shared_expires_after_its_ttl(monkeypatch):
    clock = [1000.0]
    monkeypatch.setattr(probecache.time, "monotonic", lambda: clock[0])
    n = []
    assert probecache.shared("k", lambda: n.append(1) or len(n)) == 1
    clock[0] += probecache.TTL - 0.01
    assert probecache.shared("k", lambda: n.append(1) or len(n)) == 1
    clock[0] += 0.02
    assert probecache.shared("k", lambda: n.append(1) or len(n)) == 2


# --- the direct-child rule against a /proc fixture -----------------------------------

def _proc(tmp_path: Path, tree: dict[int, dict]) -> Path:
    root = tmp_path / "proc"
    for pid, spec in tree.items():
        d = root / str(pid)
        for tid, kids in spec.get("tasks", {pid: spec.get("kids", [])}).items():
            (d / "task" / str(tid)).mkdir(parents=True, exist_ok=True)
            (d / "task" / str(tid) / "children").write_text(" ".join(map(str, kids)))
        d.mkdir(parents=True, exist_ok=True)
        (d / "cmdline").write_bytes(b"\0".join(a.encode() for a in spec.get("argv", [])))
        (d / "comm").write_text(spec.get("comm", "x") + "\n")
    return root


def test_direct_claude_pid_is_pgreps_answer(tmp_path, monkeypatch):
    root = _proc(tmp_path, {
        # pane 10: two threads; the claude child hangs off the SECOND one, and a
        # higher-pid claude-ish helper hangs off the first — pgrep names the LOWEST.
        10: {"tasks": {10: [40, 12], 11: [30]}, "argv": ["bash"]},
        12: {"argv": ["sh", "wrapper.sh"], "kids": [13]},
        13: {"argv": ["claude"]},                         # grandchild: pgrep -P misses it
        30: {"argv": ["node", "/opt/claude/cli.js"]},
        40: {"argv": ["claude-helper"]},
        # pane 50: a zombie child with no command line — procps falls back to comm.
        50: {"kids": [51], "argv": ["bash"]},
        51: {"argv": [], "comm": "claude"},
        # pane 60: nothing that looks like claude among the direct children.
        60: {"kids": [61], "argv": ["bash"]},
        61: {"argv": ["vim", "notes.txt"]},
    })
    monkeypatch.setattr(sessions, "PROC", root)
    assert sessions._direct_claude_pid(10) == 30
    assert sessions._direct_claude_pid(50) == 51
    assert sessions._direct_claude_pid(60) is None
    assert sessions._direct_claude_pid(99) is None               # no such process
    assert sessions._claude_pid(60) is None


# --- macOS: one `ps` for the whole table --------------------------------------------

SID = "ffd591c6-d903-4505-8749-8058a3abf054"


@pytest.fixture
def no_proc(monkeypatch, tmp_path):
    monkeypatch.setattr(sessions, "PROC", tmp_path / "nonexistent-proc")
    monkeypatch.setattr(sessions, "_PROC_HOST", False)


@pytest.mark.skipif(not (shutil.which("ps") and shutil.which("pgrep")),
                    reason="ps/pgrep not installed")
def test_without_proc_a_refresh_is_one_ps_not_a_spawn_per_fact(no_proc, tmp_path,
                                                               monkeypatch):
    cwd = tmp_path / "work"
    cwd.mkdir()
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)",
                              "claude", "--resume", SID], cwd=str(cwd))
    try:
        deadline = time.time() + 5
        while child.pid not in (sessions._sh_children(os.getpid()) or []):
            assert time.time() < deadline
            time.sleep(0.05)

        # The per-pid fallbacks' answer, for comparison.
        expect_pid = sessions._claude_pid(os.getpid())
        expect_direct = sessions._direct_claude_pid(os.getpid())
        expect_resumed = sessions._resumed_session(child.pid)
        assert expect_pid == expect_direct == child.pid

        real_run = subprocess.run
        seen: list[list[str]] = []

        def run(argv, **kw):
            seen.append(list(argv))
            if argv[:2] == ["tmux", "list-windows"]:
                return subprocess.CompletedProcess(
                    argv, 0, stdout=f"@1\tpython3\t{cwd}\t{os.getpid()}\n", stderr="")
            return real_run(argv, **kw)

        monkeypatch.setattr(sessions.subprocess, "run", run)
        pane = sessions._load_panes()["@1"]
        assert pane.claude_pid == child.pid
        assert pane.direct_claude_pid == child.pid
        assert pane.resumed == expect_resumed == SID
        assert pane.started is not None and abs(pane.started - time.time()) < 120
        if shutil.which("lsof"):
            assert pane.launched_in == str(cwd)
        tools = [a[0] for a in seen]
        assert tools.count("ps") == 1 and "pgrep" not in tools, seen
        assert tools.count("lsof") <= 1, seen
    finally:
        child.terminate()
        child.wait(timeout=5)


def test_ps_table_parses_rows_and_refuses_garbage(no_proc, monkeypatch):
    out = ("    1     0 Wed Oct  7 09:00:00 2026 /sbin/launchd\n"
           "  500     1 Wed Oct  7 10:00:00 2026 -zsh\n"
           "  510   500 Wed Oct  7 10:00:05 2026 node /usr/local/bin/claude --resume x\n"
           "  505   500 Wed Oct  7 10:00:01 2026 /usr/bin/claude\n"
           "  600   500 Wed Oct  7 10:00:09 2026\n")
    monkeypatch.setattr(sessions, "_sh", lambda argv: out)
    t = sessions._ps_table()
    assert t.children(500) == [505, 510, 600]
    assert t.argv[510] == ["node", "/usr/local/bin/claude", "--resume", "x"]
    assert t.argv[600] == []
    assert t.comm(505) == "claude" and t.comm(600) == ""
    assert t.started[505] == time.mktime(time.strptime("Wed Oct 7 10:00:01 2026",
                                                       "%a %b %d %H:%M:%S %Y"))
    token = sessions._PS.set(t)
    try:
        assert sessions._direct_claude_pid(500) == 505
        assert sessions._children(500) == [505, 510, 600]
        assert sessions._cmdline_argv(510)[1] == "/usr/local/bin/claude"
        assert sessions.proc_started(505) == t.started[505]
    finally:
        sessions._PS.reset(token)

    monkeypatch.setattr(sessions, "_sh", lambda argv: "123\nnot ps output\n")
    assert sessions._ps_table() is None        # unparseable → per-pid fallbacks, not "absent"


def test_lsof_cwds_reads_one_field_listing(no_proc, monkeypatch):
    asked = []
    monkeypatch.setattr(sessions, "_sh", lambda argv: asked.append(argv) or
                        "p505\nfcwd\nn/Users/a/proj\np510\nfcwd\nn/Users/b\n")
    assert sessions._lsof_cwds([505, 510]) == {505: "/Users/a/proj", 510: "/Users/b"}
    assert asked == [["lsof", "-a", "-d", "cwd", "-Fn", "-p", "505,510"]]
    assert sessions._lsof_cwds([]) == {}
