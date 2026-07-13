"""A missing tmux session must NEVER be fatal to the ttyd supervisor — and a LIVE
session must never be touched.

Regression for the 2026-07-13 incident, both halves:

  1. After `wsl --shutdown` the tmux server was gone, scripts/agent-terminals.sh hit
     `has-session || exit 1`, and pm2 restarted it instantly — 14,501 restarts, ~14.7k
     identical error lines, hours of burnt CPU. The session is chela's own and nothing
     else recreates it, so a missing one is a normal boot-ordering condition: create it
     and carry on; if tmux is unreachable, back off. Never exit.
  2. The first cut of that self-heal then re-created the session on a poll tick while it
     was ALIVE, destroying the user's agent windows. Self-heal must be CREATE-ONLY —
     `tmux new-session -A` (attach-or-create) is the primitive that guarantees it, even
     if the has-session gate false-negatives.

TMUX ISOLATION — read before touching this file. Every tmux call here is pinned to a
scratch socket with `-L <sock>`, a PER-COMMAND flag that cannot be lost. We do NOT rely
on $TMUX_TMPDIR for isolation: it is per-process env, and any invocation that drops it
silently falls back to the DEFAULT socket — i.e. `kill-server` would destroy the live
fleet. That has happened. It is kept below only as belt-and-braces; `-L` is the actual
guarantee, and `_assert_scratch()` refuses to run if the socket ever resolves to the
default. The supervisor script calls bare `tmux`, so it is isolated with a PATH shim
that injects `-L <sock>` — also per-invocation, and it inherits into the script's own
`tmux` and `chela.discovery` subprocess calls alike.
"""
import json
import os
import shutil
import subprocess
import tempfile
import time
import uuid
from pathlib import Path
from unittest.mock import patch

import pytest

from chela import discovery

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "agent-terminals.sh"
SESSION = "selfheal-test"
TMUX_BIN = shutil.which("tmux")

pytestmark = pytest.mark.skipif(TMUX_BIN is None, reason="tmux not installed")


def _assert_scratch(sock: str) -> None:
    """Hard guard: never, ever operate on the default socket (that's the live fleet)."""
    assert sock.startswith("chelatest-") and sock != "default", f"unsafe tmux socket: {sock}"


@pytest.fixture
def sock():
    """A unique, per-test tmux socket name. Every tmux call must pass `-L` with it.

    Deliberately SHORT: a unix socket path is capped at ~108 chars, and the socket
    lives at $TMUX_TMPDIR/tmux-<uid>/<sock>. A long name (or a deep pytest tmp_path as
    the tmpdir) silently fails every tmux call with "File name too long".
    """
    s = f"chelatest-{os.getpid()}-{uuid.uuid4().hex[:6]}"
    _assert_scratch(s)
    return s


@pytest.fixture
def env(tmp_path, sock):
    """Script env: a PATH shim pinning tmux to the scratch socket, stub ttyd, tmp state."""
    shim = tmp_path / "shim"
    shim.mkdir()
    tmux_shim = shim / "tmux"
    tmux_shim.write_text(f'#!/bin/sh\nexec {TMUX_BIN} -L {sock} "$@"\n')
    tmux_shim.chmod(0o755)

    # Short, dedicated socket dir: belt-and-braces, so that even if the PATH shim were
    # somehow lost, tmux still cannot reach the DEFAULT socket (the live fleet). Not
    # tmp_path — that nests too deep for the 108-char socket-path limit.
    tmuxdir = tempfile.mkdtemp(prefix="cmx", dir="/tmp")

    e = dict(os.environ)
    e.update(
        PATH=f"{shim}:{os.environ['PATH']}",       # every bare `tmux` -> scratch socket
        TMUX_TMPDIR=tmuxdir,                       # belt-and-braces; `-L` is the guarantee
        CHELA_TMUX_SESSION=SESSION,
        CHELA_DIR=str(tmp_path / "chela"),
        TTYD="/bin/true",                          # never launch a real terminal server
        CHELA_TERM_POLL="1",
        CHELA_TERM_BASE="5901",
    )
    e.pop("TMUX", None)
    e.pop("TMUX_PANE", None)                       # don't inherit this agent's own pane
    yield e
    _tmux(e, sock, "kill-server", check=False)     # scoped to the scratch socket ONLY
    shutil.rmtree(tmuxdir, ignore_errors=True)


def _tmux(env, sock, *args, check=True):
    """Run the real tmux against the scratch socket (never via the shim, never bare)."""
    _assert_scratch(sock)
    return subprocess.run([TMUX_BIN, "-L", sock, *args],
                          env=env, capture_output=True, text=True, check=check)


def _run_bg(env, extra=None):
    return subprocess.Popen(
        [str(SCRIPT)], env={**env, **(extra or {})},
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
    )


def _has_session(env, sock):
    return _tmux(env, sock, "has-session", "-t", SESSION, check=False).returncode == 0


def _windows(env, sock):
    return _tmux(env, sock, "list-windows", "-t", SESSION,
                 "-F", "#{window_name}", check=False).stdout.split()


def _session_id(env, sock):
    return _tmux(env, sock, "display-message", "-p", "-t", SESSION,
                 "#{session_id}", check=False).stdout.strip()


def _wait(pred, timeout=15, proc=None, msg=""):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if pred():
            return True
        if proc is not None:
            assert proc.poll() is None, f"supervisor exited: {msg}"
        time.sleep(0.2)
    return False


# --- half 1: a missing session must not be fatal ---

def test_missing_session_is_created_not_fatal(env, sock):
    """Pre-fix: session absent -> exit 1, and pm2 hot-loops it. Now: create it, stay up."""
    assert not _has_session(env, sock)
    proc = _run_bg(env)
    try:
        assert _wait(lambda: _has_session(env, sock), proc=proc,
                     msg="it exited instead of creating the missing session"), \
            "supervisor never created the missing session"
        assert proc.poll() is None, "supervisor exited after creating the session"
    finally:
        proc.terminate()
        proc.wait(timeout=10)


def test_unreachable_tmux_backs_off_instead_of_exiting(env, tmp_path):
    """When tmux CANNOT be reached at all, the supervisor waits — it never exits(1).

    An exit is what pm2 turns into a hot loop, so the invariant is "still running", not
    "succeeded". A shim tmux that always fails simulates the worst case (dead server,
    unwritable socket dir). We shim rather than empty $PATH — an empty PATH would break
    the `#!/usr/bin/env bash` shebang and kill the script for the wrong reason, passing
    the assertion for no good reason.
    """
    broken = tmp_path / "broken"
    broken.mkdir()
    fake = broken / "tmux"
    fake.write_text("#!/bin/sh\necho 'error connecting to socket' >&2\nexit 1\n")
    fake.chmod(0o755)
    proc = _run_bg(env, {"PATH": f"{broken}:{env['PATH']}"})
    try:
        time.sleep(3)  # pre-fix this exited in ~12ms; pm2 restarted it ~80x/s
        assert proc.poll() is None, "supervisor exited with no tmux — pm2 would hot-loop"
    finally:
        proc.terminate()
        proc.wait(timeout=10)


def test_disabled_wall_still_writes_empty_map_and_idles(env, sock):
    """The feature-flag path stays exit-free, and must NOT create a session."""
    proc = _run_bg(env, {"CHELA_TERMINALS_ENABLED": "false"})
    try:
        map_file = Path(env["CHELA_DIR"]) / "agent_terminals.json"
        assert _wait(lambda: map_file.exists(), timeout=10, proc=proc,
                     msg="it exited before writing the empty map"), "no map file written"
        assert json.loads(map_file.read_text()) == {}
        assert proc.poll() is None
        assert not _has_session(env, sock), "a disabled wall must not create the session"

        # ...and it must still answer SIGTERM promptly. The idle loop slept in the
        # FOREGROUND, and bash defers traps until the foreground child exits — so
        # `pm2 stop` hung for the length of the sleep, got SIGKILLed, and the EXIT
        # trap never ran: no cleanup(), leaked ttyds and webterm_* mirror sessions.
        proc.terminate()
        proc.wait(timeout=5)  # pre-fix: hung on `sleep 3600`, i.e. up to an hour
    finally:
        proc.terminate()
        proc.wait(timeout=10)


# --- half 2: a LIVE session must never be recreated (the destructive regression) ---

def test_live_session_and_its_windows_are_never_recreated(env, sock):
    """THE regression test. The first self-heal re-created the session on a poll tick
    while it was alive, destroying the user's agent windows (and the agent watching it).
    Pre-create the session with an agent window, run the supervisor across several poll
    ticks, and demand the session id AND the window survive untouched.
    """
    _tmux(env, sock, "new-session", "-d", "-s", SESSION, "-n", "agentwin")
    sid = _session_id(env, sock)

    proc = _run_bg(env)
    try:
        time.sleep(5)  # >= 4 poll ticks at CHELA_TERM_POLL=1
        assert proc.poll() is None
        assert _session_id(env, sock) == sid, "the live session was RECREATED (new session id)"
        assert "agentwin" in _windows(env, sock), "self-heal destroyed a live agent window"
        assert discovery.ANCHOR_WINDOW not in _windows(env, sock), \
            "self-heal added an anchor window to a live session"
    finally:
        proc.terminate()
        proc.wait(timeout=10)


def test_create_is_a_noop_even_if_the_has_session_gate_lies(env, sock):
    """The incident's mechanism, isolated: a false-negative existence check must NOT
    become a recreate. `new-session -A` is what guarantees that, so we force the gate to
    lie and assert the live session survives untouched anyway."""
    _tmux(env, sock, "new-session", "-d", "-s", SESSION, "-n", "agentwin")
    sid = _session_id(env, sock)

    with patch.dict(os.environ, env, clear=False):  # PATH shim -> scratch socket
        with patch("chela.discovery.session_exists", side_effect=[False, True]):
            assert discovery.ensure_session() is True  # gate says "missing" — it isn't

    assert _session_id(env, sock) == sid, "a lying gate recreated the live session"
    assert _windows(env, sock) == ["agentwin"], "a lying gate mutated a live session's windows"


def test_session_recreated_after_it_disappears(env, sock):
    """The session vanishing mid-run heals on the next poll tick, not just at boot."""
    proc = _run_bg(env)
    try:
        assert _wait(lambda: _has_session(env, sock), proc=proc, msg="never created")
        # kill only OUR scratch session, on OUR scratch socket
        _tmux(env, sock, "kill-session", "-t", SESSION, check=False)
        assert not _has_session(env, sock)

        assert _wait(lambda: _has_session(env, sock), proc=proc,
                     msg="it exited when the session was killed"), \
            "supervisor did not recreate the vanished session"
    finally:
        proc.terminate()
        proc.wait(timeout=10)


# --- the Python half (chela.discovery), used by the dashboard's spawn path ---

def test_ensure_session_creates_when_missing(env, sock):
    with patch.dict(os.environ, env, clear=False):
        assert not _has_session(env, sock)
        assert discovery.ensure_session() is True
        assert _has_session(env, sock)
        assert _windows(env, sock) == [discovery.ANCHOR_WINDOW]


def test_ensure_session_is_idempotent(env, sock):
    with patch.dict(os.environ, env, clear=False):
        assert discovery.ensure_session() is True
        sid = _session_id(env, sock)
        assert discovery.ensure_session() is True   # no-op the second time
        assert _session_id(env, sock) == sid


def test_ensure_session_false_when_tmux_unreachable(env):
    with patch.dict(os.environ, env, clear=False):
        with patch("chela.discovery.subprocess.run", side_effect=FileNotFoundError):
            assert discovery.ensure_session() is False
