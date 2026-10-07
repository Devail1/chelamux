"""🧯 CMX-21 — the 2026-10-06 env leak: an orphaned perf-harness supervisor re-created the
LIVE tmux server from its own env (a dead-port HTTPS_PROXY, a parent Claude session's
markers, a TMUX_TMPDIR whose dir was gone), and every pane born after it was broken.

Covered here, each with a negative control:

* a heal-create from a polluted env leaves a CLEAN global env (Python and bash halves);
* a heal REFUSES when TMUX_TMPDIR names a missing dir (tmux would fall back to the
  default socket — the live server);
* an agent launch env carries no Claude session markers / dead proxy;
* the health check (`tmux.leaked_env`) fires on a polluted env and is silent on a clean one.

(The PreToolUse deny of a live `tmux kill-server` lives in tests/test_mergegate.py.)

TMUX ISOLATION: every tmux call is pinned to a `chelatest-*` socket with `-L` — directly, or
through a PATH shim that injects it into chela's own bare `tmux` calls. The live server is
never touched. See tests/test_terminals_selfheal.py for the full rationale.
"""
from __future__ import annotations

import os
import shutil
import socket
import subprocess
import tempfile
import time
import uuid
from pathlib import Path
from unittest.mock import patch

import pytest

from chela import discovery, doctor, envutil, runtime_truth

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "agent-terminals.sh"
SESSION = "leak-test"
TMUX_BIN = shutil.which("tmux")

POLLUTION = {
    "HTTPS_PROXY": "http://127.0.0.1:9",
    "https_proxy": "http://127.0.0.1:9",
    "CLAUDECODE": "1",
    "CLAUDE_CODE_SESSION_ID": "fc9c56ea-0000",
    "CLAUDE_CODE_CHILD_SESSION": "1",
    "CLAUDE_PID": "4242",
    "AI_AGENT": "claude",
    "PERF_OUT": "/tmp/cx15/runs/a",
    "H": "/tmp/cx15",
    "pm_id": "7",
}

needs_tmux = pytest.mark.skipif(TMUX_BIN is None, reason="tmux not installed")


def _assert_scratch(sock: str) -> None:
    assert sock.startswith("chelatest-") and sock != "default", f"unsafe tmux socket: {sock}"


@pytest.fixture
def sock():
    s = f"chelatest-{os.getpid()}-{uuid.uuid4().hex[:6]}"
    _assert_scratch(s)
    return s


@pytest.fixture
def env(tmp_path, sock):
    """A clean base env whose bare `tmux` reaches ONLY the scratch socket."""
    shim = tmp_path / "shim"
    shim.mkdir()
    (shim / "tmux").write_text(f'#!/bin/sh\nexec {TMUX_BIN} -L {sock} "$@"\n')
    (shim / "tmux").chmod(0o755)
    tmuxdir = tempfile.mkdtemp(prefix="cmx", dir="/tmp")
    e = {k: v for k, v in os.environ.items()
         if not (envutil.is_session_marker(k) or envutil.is_server_hazard(k)
                 or envutil.is_leaked(k))}
    e.update(PATH=f"{shim}:{os.environ['PATH']}", TMUX_TMPDIR=tmuxdir,
             CHELA_TMUX_SESSION=SESSION, CHELA_DIR=str(tmp_path / "chela"),
             TTYD="/bin/true", CHELA_TERM_POLL="1", CHELA_TERM_BASE="5931")
    e.pop("TMUX", None)
    e.pop("TMUX_PANE", None)
    yield e
    _tmux(e, sock, "kill-server", check=False)
    shutil.rmtree(tmuxdir, ignore_errors=True)


def _tmux(env, sock, *args, check=True):
    _assert_scratch(sock)
    return subprocess.run([TMUX_BIN, "-L", sock, *args], env=env,
                          capture_output=True, text=True, check=check)


def _global_env(env, sock) -> dict[str, str]:
    out = _tmux(env, sock, "show-environment", "-g").stdout
    return dict(line.split("=", 1) for line in out.splitlines()
                if "=" in line and not line.startswith("-"))


def _proc_env(pid: str) -> dict[str, str]:
    raw = Path(f"/proc/{pid}/environ").read_bytes().split(b"\0")
    return dict(e.decode(errors="replace").split("=", 1) for e in raw if b"=" in e)


def _has_session(env, sock) -> bool:
    return _tmux(env, sock, "has-session", "-t", SESSION, check=False).returncode == 0


def _wait(pred, timeout=15.0) -> bool:
    end = time.time() + timeout
    while time.time() < end:
        if pred():
            return True
        time.sleep(0.2)
    return False


def _reap(proc):
    proc.terminate()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=5)


# --- the pure halves ---------------------------------------------------------------------

def test_child_env_drops_claude_session_markers():
    base = {"PATH": "/bin", "CHELA_X": "1", **POLLUTION}
    env = envutil.child_env(base=base)
    for k in envutil.SESSION_MARKER_VARS:
        assert k not in env
    assert "pm_id" not in env
    # ⭐ negative control: unrelated vars pass, and a hook still sees a (maybe real) proxy
    assert env["CHELA_X"] == "1" and env["HTTPS_PROXY"] == "http://127.0.0.1:9"


def test_server_env_drops_proxies_and_harness_vars_but_keeps_the_socket_dir():
    env = envutil.server_env(base={"PATH": "/bin", "TMUX_TMPDIR": "/tmp/x", **POLLUTION})
    assert set(env) == {"PATH", "TMUX_TMPDIR"}, env


def test_forward_list_overrides_the_server_strip():
    env = envutil.server_env(base={"PATH": "/bin", "HTTPS_PROXY": "http://proxy:3128",
                                   envutil.FORWARD_ENV: "HTTPS_PROXY"})
    assert env["HTTPS_PROXY"] == "http://proxy:3128"


def test_tmux_scrub_names_flags_markers_and_hazards_only():
    out = "\n".join(f"{k}={v}" for k, v in POLLUTION.items()) + \
        "\nTMUX_TMPDIR=/tmp/cx15\nPATH=/bin\nHOME=/h\n-CLAUDECODE_OLD\n"
    assert set(envutil.tmux_scrub_names(out, forward=frozenset())) == \
        set(POLLUTION) | {"TMUX_TMPDIR"}


# --- Ask 2: heal-create from a polluted env yields a clean global env --------------------

@needs_tmux
def test_python_heal_create_from_a_polluted_env_is_clean(env, sock):
    """🔴 GUARD: create from `child_env()` instead of `server_env()`, or drop the scrub after
    the create, and the new server's global env carries the proxy / markers / TMUX_TMPDIR."""
    with patch.dict(os.environ, {**env, **POLLUTION}, clear=True):
        assert discovery.ensure_session() is True
    assert _has_session(env, sock)
    leaked = set(_global_env(env, sock)) & (set(POLLUTION) | {"TMUX_TMPDIR"})
    assert not leaked, f"heal-create handed every window: {sorted(leaked)}"
    # The anchor window is born BEFORE the post-create scrub, from the env the server
    # started with — so only server_env() keeps it clean.
    anchor = _proc_env(_tmux(env, sock, "display", "-p", "-t", f"{SESSION}:",
                             "#{pane_pid}").stdout.strip())
    leaked = set(anchor) & set(POLLUTION)
    assert not leaked, f"the anchor pane was born with {sorted(leaked)}"


@needs_tmux
def test_negative_control_a_plain_create_from_that_env_is_polluted(env, sock):
    """⭐ Proves the assertion above can see pollution at all: the same env, created the
    pre-CMX-21 way, DOES leave the markers in the global table."""
    _tmux({**env, **POLLUTION}, sock, "new-session", "-d", "-s", SESSION)
    genv = _global_env(env, sock)
    assert genv.get("CLAUDECODE") == "1" and genv.get("HTTPS_PROXY") == "http://127.0.0.1:9"


@needs_tmux
def test_supervisor_heal_create_from_a_polluted_env_is_clean(env, sock):
    """The bash half: agent-terminals.sh's heal goes through the same scrubbed create."""
    proc = subprocess.Popen([str(SCRIPT)], env={**env, **POLLUTION},
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    try:
        assert _wait(lambda: _has_session(env, sock)), "supervisor never healed"
        leaked = set(_global_env(env, sock)) & (set(POLLUTION) | {"TMUX_TMPDIR"})
        assert not leaked, f"supervisor heal handed every window: {sorted(leaked)}"
    finally:
        _reap(proc)


# --- Ask 2: refuse to heal on a missing TMUX_TMPDIR dir ----------------------------------

@needs_tmux
def test_python_heal_refuses_on_a_missing_tmux_tmpdir(env, sock, caplog):
    """🔴 GUARD: drop the refusal and tmux falls back to the default socket — the 2026-10-06
    orphan's exact move. Here the shim keeps even that on the scratch socket name."""
    gone = env["TMUX_TMPDIR"] + "-deleted"
    with patch.dict(os.environ, {**env, "TMUX_TMPDIR": gone}, clear=True):
        with patch("chela.discovery.subprocess.run",
                   side_effect=AssertionError("tmux must not be called")):
            assert discovery.ensure_session() is False
    assert "REFUSING" in caplog.text


@needs_tmux
def test_negative_control_heal_creates_when_the_dir_exists(env, sock):
    with patch.dict(os.environ, env, clear=True):
        assert discovery.ensure_session() is True
    assert _has_session(env, sock)


@needs_tmux
def test_supervisor_refuses_to_heal_on_a_missing_tmux_tmpdir(env, sock):
    gone = env["TMUX_TMPDIR"] + "-deleted"
    proc = subprocess.Popen([str(SCRIPT)], env={**env, "TMUX_TMPDIR": gone},
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    try:
        time.sleep(3)
        assert proc.poll() is None, "not orphaned: it must back off, not exit"
    finally:
        _reap(proc)
    out = proc.stdout.read()
    # the supervisor's OWN refusal, before any tmux call — not the Python heal's log line
    assert "agent-terminals: REFUSING to heal" in out, out
    assert not _has_session(env, sock)
    assert not os.path.exists(gone)


@needs_tmux
def test_supervisor_exits_once_its_cwd_is_deleted(env, sock, tmp_path):
    """The 2026-10-06 orphan outlived the harness dir it ran from. A supervisor whose cwd is
    gone EXITS instead of healing; one whose cwd is intact keeps backing off (control)."""
    repo = SCRIPT.parent.parent
    copy = Path(tempfile.mkdtemp(prefix="cmx21", dir="/tmp"))
    shutil.copytree(repo / "scripts", copy / "scripts")
    py = repo / ".venv" / "bin" / "python"
    run_env = {**env, "TMUX_TMPDIR": env["TMUX_TMPDIR"] + "-deleted",
               "PYTHON": str(py if py.exists() else shutil.which("python3")),
               "PYTHONPATH": str(repo)}
    proc = subprocess.Popen([str(copy / "scripts" / "agent-terminals.sh")], env=run_env,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    try:
        time.sleep(2)
        assert proc.poll() is None, "it exited while its cwd was still there"
        shutil.rmtree(copy)
        proc.wait(timeout=15)
    finally:
        if proc.poll() is None:
            _reap(proc)
        shutil.rmtree(copy, ignore_errors=True)
    assert proc.returncode == 1
    assert "my cwd was deleted" in proc.stdout.read()


# --- Ask 3: an agent launch env lacks the markers ----------------------------------------

def _pane_env(env, sock, tmp_path, name) -> dict[str, str]:
    out = tmp_path / f"{name}.env"
    _tmux(env, sock, "new-window", "-d", "-t", f"{SESSION}:", "-n", name,
          f"env > {out}; sleep 30")
    assert _wait(lambda: out.exists() and out.stat().st_size > 0)
    time.sleep(0.2)
    return dict(line.split("=", 1) for line in out.read_text().splitlines() if "=" in line)


@needs_tmux
def test_an_agent_window_on_a_polluted_server_lacks_the_markers(env, sock, tmp_path):
    """🔴 GUARD: stop scrubbing markers/proxies from the global table before a launch and
    the agent's pane starts with transcripts off and every request refused."""
    _tmux({**env, **POLLUTION}, sock, "new-session", "-d", "-s", SESSION)
    before = _pane_env(env, sock, tmp_path, "before")
    assert before.get("CLAUDECODE") == "1", "negative control: the pane IS polluted first"

    with patch.dict(os.environ, env, clear=True):
        envutil.scrub_tmux_secrets()          # what every launch path runs first
    after = _pane_env(env, sock, tmp_path, "after")
    leaked = set(after) & set(POLLUTION)
    assert not leaked, f"agent window inherited {sorted(leaked)}"


# --- Ask 5: the health check -------------------------------------------------------------

def test_pollution_check_fires_on_a_polluted_env():
    found = envutil.tmux_env_pollution(
        {**POLLUTION, "TMUX_TMPDIR": "/nonexistent/cx15", "PATH": "/bin"},
        port_open=lambda h, p: False)
    assert set(found) == {"HTTPS_PROXY", "https_proxy", "CLAUDECODE",
                          "CLAUDE_CODE_SESSION_ID", "CLAUDE_CODE_CHILD_SESSION",
                          "TMUX_TMPDIR"}


def test_pollution_check_is_silent_on_a_clean_env(tmp_path):
    """⭐ Negative control: a real proxy elsewhere, a live local proxy, an existing dir."""
    with socket.socket() as srv:
        srv.bind(("127.0.0.1", 0))
        srv.listen()
        port = srv.getsockname()[1]
        assert envutil.tmux_env_pollution({
            "PATH": "/bin", "HTTPS_PROXY": "http://proxy.corp:3128",
            "http_proxy": f"http://127.0.0.1:{port}", "TMUX_TMPDIR": str(tmp_path),
        }) == {}


def test_a_dead_local_proxy_port_is_flagged():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]            # bound, never listening: refused
        assert "HTTP_PROXY" in envutil.tmux_env_pollution(
            {"HTTP_PROXY": f"http://127.0.0.1:{port}"})


class _StubNotify:
    def __init__(self):
        self.sent: list[tuple] = []

    def enabled(self):
        return True

    def send(self, message, title=None):
        self.sent.append((message, title))
        return True


def test_daemon_doctor_sweep_alerts_once_and_only_when_polluted(monkeypatch):
    """The daemon tick's doctor sweep pushes the polluted state ONCE (dedupe), and nothing
    for a clean server."""
    stub = _StubNotify()
    monkeypatch.setattr(doctor, "notify", stub)
    fact, = [f for f in runtime_truth.facts() if f.name == "tmux.leaked_env"]
    monkeypatch.setattr(doctor, "check", lambda: runtime_truth.audit(fact))
    genv: dict[str, str] = {"PATH": "/bin"}
    monkeypatch.setattr(runtime_truth, "_tmux_global_env", lambda: dict(genv))

    red = doctor.check_and_notify(set())
    assert red == set() and stub.sent == [], "a clean server must stay silent"

    genv.update(HTTPS_PROXY="http://127.0.0.1:9", CLAUDE_CODE_SESSION_ID="x")
    red = doctor.check_and_notify(red)
    red = doctor.check_and_notify(red)
    assert len(stub.sent) == 1, stub.sent
    assert "CLAUDE_CODE_SESSION_ID" in stub.sent[0][0] and "HTTPS_PROXY" in stub.sent[0][0]
