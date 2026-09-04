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
import ast
import inspect
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path
from unittest.mock import MagicMock, call, patch

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


def _reap(proc, term_timeout=10, kill_timeout=5):
    """Teardown-safe reap: SIGTERM, bounded wait, escalate to SIGKILL, wait again.

    A supervisor slow to exit after SIGTERM must not fail the TEST that is merely
    cleaning up after it — every assertion in that test already ran and passed, so a
    `TimeoutExpired` out of teardown misreports "the supervisor is broken" when
    nothing under test was. Escalating to SIGKILL (which a process cannot ignore) is
    what guarantees no supervisor outlives the test. The final `wait` is deliberately
    NOT wrapped in its own try/except: if the child survives SIGKILL too, that is a
    real "process will not die" regression and must still fail the test, not vanish
    into a blanket `except Exception: pass`.

    Tests that assert SIGTERM promptness as their actual behavior-under-test (see the
    "must still answer SIGTERM promptly" checks below) call `proc.wait()` directly for
    that — this helper is for `finally:` cleanup only, not a replacement for them.
    """
    proc.terminate()
    try:
        proc.wait(timeout=term_timeout)
        return
    except subprocess.TimeoutExpired:
        pass
    proc.kill()
    proc.wait(timeout=kill_timeout)


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
        _reap(proc)


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
        _reap(proc)


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
        _reap(proc)


@pytest.mark.skipif(shutil.which("pgrep") is None, reason="pgrep not installed")
def test_disabled_wall_sigterm_does_not_orphan_the_idle_sleep(env):
    """Regression for 2026-08-17: 32 orphaned `sleep 3600`s (all PPID=1) found after
    hard teardown of this exact disabled-wall idle loop. `nap()`'s backgrounded sleep
    made the wait interruptible, but the trap's `exit` only ends the script's own
    shell — it never killed the still-running child, so `sleep` was reparented to
    PID 1 and outlived the supervisor by up to an hour. Assert the child is gone too,
    not just that the supervisor process itself exited promptly.
    """
    proc = _run_bg(env, {"CHELA_TERMINALS_ENABLED": "false"})
    try:
        map_file = Path(env["CHELA_DIR"]) / "agent_terminals.json"
        assert _wait(lambda: map_file.exists(), timeout=10, proc=proc,
                     msg="it exited before writing the empty map"), "no map file written"

        # find the backgrounded `sleep 3600` — a direct child of the supervisor
        sleep_pid = None
        deadline = time.time() + 5
        while time.time() < deadline and sleep_pid is None:
            out = subprocess.run(["pgrep", "-P", str(proc.pid)],
                                  capture_output=True, text=True, check=False).stdout.split()
            for pid in out:
                cmd = subprocess.run(["ps", "-o", "comm=", "-p", pid],
                                      capture_output=True, text=True, check=False).stdout.strip()
                if cmd == "sleep":
                    sleep_pid = int(pid)
                    break
            if sleep_pid is None:
                time.sleep(0.2)
        assert sleep_pid is not None, "never observed the idle-loop's backgrounded sleep"

        proc.terminate()
        proc.wait(timeout=5)

        deadline = time.time() + 5
        while time.time() < deadline and os.path.exists(f"/proc/{sleep_pid}"):
            time.sleep(0.1)
        assert not os.path.exists(f"/proc/{sleep_pid}"), \
            f"sleep pid {sleep_pid} outlived the supervisor — orphaned onto PID 1"
    finally:
        _reap(proc)


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
        _reap(proc)


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
        _reap(proc)


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


# --- issue #436: teardown reap must never fail the test it is cleaning up after ---

def test_reap_survives_a_sigterm_ignoring_child(tmp_path):
    """Regression for issue #436: a `finally:` block's `proc.wait(timeout=10)` used to
    have no SIGKILL fallback, so a supervisor that (for whatever reason) does not exit
    within 10s of SIGTERM fails the TEST that was cleaning up after it, even though
    every assertion in that test's body already passed. `_reap` must escalate to
    SIGKILL instead — which the child cannot ignore — and the whole thing must still
    reap cleanly with no leaked pid."""
    stub = tmp_path / "ignore_term.py"
    stub.write_text(
        "import signal, time\n"
        "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
        "time.sleep(30)\n"
    )
    proc = subprocess.Popen([sys.executable, str(stub)])
    try:
        time.sleep(0.3)  # let it install the SIGTERM handler before we send one
        assert proc.poll() is None, "stub exited before the test even started"
        _reap(proc, term_timeout=1, kill_timeout=5)  # must NOT raise TimeoutExpired
        # Asserted HERE, inside the try and immediately after _reap returns — not
        # after this function's own `finally:` below, which reaps the stub itself
        # and would make this assertion pass regardless of what `_reap` did.
        assert proc.poll() is not None, "SIGTERM-ignoring child was never reaped"
        assert not os.path.exists(f"/proc/{proc.pid}"), \
            f"child pid {proc.pid} outlived the reap — leaked past teardown"
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=5)


def test_reap_propagates_if_the_child_survives_sigkill_too():
    """Guard: escalating to SIGKILL must not become a blanket `except Exception: pass`.
    A process that is still alive after SIGKILL (the only realistic real-world case is
    "the process never actually died") is a genuine "will not exit" regression — the
    exact thing this file's other tests exist to catch — and must still fail the test,
    not vanish silently.

    The sequence is pinned as ONE `mock_calls` equality, not three independent
    `assert_called_once`/`call_count` checks — those are all satisfied by ANY
    permutation of the four calls (e.g. kill-then-terminate), which is exactly the
    ordering mutation docs/defeat_shapes/339 records. `mock_calls` pins the method,
    the arguments AND the order in one assertion.
    """
    fake = MagicMock()
    fake.wait = MagicMock(side_effect=subprocess.TimeoutExpired(cmd="stub", timeout=1))

    with pytest.raises(subprocess.TimeoutExpired):
        _reap(fake, term_timeout=1, kill_timeout=1)

    assert fake.mock_calls == [
        call.terminate(),
        call.wait(timeout=1),
        call.kill(),
        call.wait(timeout=1),
    ], "must SIGTERM, wait, then SIGKILL, wait again — in that order, not any permutation"


# statements whose only job is to abort the current test outright — an ordinary
# ast.Expr/ast.Call to a static walk, yet fatal at runtime, so a Return/Raise-only
# reachability check is blind to them (docs/defeat_shapes/345f)
_ABORTS_EXECUTION = frozenset({
    "pytest.skip", "pytest.xfail", "pytest.exit", "sys.exit", "os._exit",
})


def _diverts_control_flow(stmt):
    """True if `stmt` can prevent every statement after it in the same body from ever
    running: a `return`/`raise`, or a call to a function in `_ABORTS_EXECUTION`
    (`pytest.skip(...)` etc.) — the latter is a plain `ast.Expr` to a static walk, so
    it is invisible to a check that only looks for `ast.Return`/`ast.Raise` despite
    raising at runtime."""
    if isinstance(stmt, (ast.Return, ast.Raise)):
        return True
    if isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Call):
        func = stmt.value.func
        if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
            if f"{func.value.id}.{func.attr}" in _ABORTS_EXECUTION:
                return True
    return False


def _reap_order_assertion():
    """The `assert fake.mock_calls == [...]` statement inside
    test_reap_propagates_if_the_child_survives_sigkill_too — the actual ORDER claim this
    guard exists to protect, read off this file's own source the same way
    `_promptness_check_try_bodies` reads it below."""
    tree = ast.parse(Path(__file__).read_text())
    for func in ast.walk(tree):
        if not (
            isinstance(func, ast.FunctionDef)
            and func.name == "test_reap_propagates_if_the_child_survives_sigkill_too"
        ):
            continue
        asserts = [stmt for stmt in func.body if isinstance(stmt, ast.Assert)]
        assert len(asserts) == 1, (
            f"{func.name}: expected exactly one top-level assert pinning the call order, "
            f"found {len(asserts)}"
        )
        assert_stmt = asserts[0]
        preceding = func.body[: func.body.index(assert_stmt)]
        assert not any(_diverts_control_flow(stmt) for stmt in preceding), (
            f"{func.name}: a statement that aborts execution (return/raise/"
            f"pytest.skip/pytest.xfail/pytest.exit/sys.exit) precedes the pinned "
            f"order assert — it is dead code, never actually reached, so the ORDER "
            f"claim it names goes unasserted despite the assert's own AST shape "
            f"being untouched"
        )
        return assert_stmt
    raise AssertionError(
        "test_reap_propagates_if_the_child_survives_sigkill_too not found in this file"
    )


def test_reap_call_sequence_equality_is_order_sensitive():
    """GUARD for the ORDER claim in test_reap_propagates_if_the_child_survives_sigkill_too's
    docstring: that its `mock_calls == [...]` assertion pins ORDER, not just method+args.

    Re-proving that `unittest.mock._CallList.__eq__` is order-sensitive in isolation (build
    two differently-ordered call lists, assert they differ) is a property of the standard
    library — true no matter what the sibling assertion actually looks like, so it survives
    the sibling being rewritten to `sorted(fake.mock_calls, key=repr) == sorted([...],
    key=repr)`, which is order-INSENSITIVE despite still reading as an equality check. This
    instead reads the sibling's own assert statement off this file's AST (see
    `_reap_order_assertion` above) and requires BOTH sides to be bare — `fake.mock_calls`
    on the left, a plain list literal on the right — so wrapping either side in `sorted(...)`
    (or any other call) trips this guard directly, rather than a stdlib property that
    doesn't care.
    """
    stmt = _reap_order_assertion()
    test = stmt.test
    assert (
        isinstance(test, ast.Compare)
        and len(test.ops) == 1
        and isinstance(test.ops[0], ast.Eq)
    ), "the pinned order claim must be a single `==` comparison, not e.g. `sorted(...) == sorted(...)`"

    left, right = test.left, test.comparators[0]
    assert (
        isinstance(left, ast.Attribute)
        and left.attr == "mock_calls"
        and isinstance(left.value, ast.Name)
        and left.value.id == "fake"
    ), (
        "the LHS must be the bare `fake.mock_calls` — wrapping it (e.g. "
        "`sorted(fake.mock_calls, key=repr)`) discards the order it claims to pin"
    )
    assert isinstance(right, ast.List), (
        "the RHS must be a bare list literal — wrapping it (e.g. `sorted([...], key=repr)`) "
        "makes the comparison order-insensitive while still reading as an equality check"
    )


def test_reap_defaults_cannot_collapse_either_wait_window():
    """WIRING: `_reap`'s defaults are load-bearing at all six call sites, which all call
    bare `_reap(proc)` and take both timeouts from the signature. Nothing in the two
    tests above observes the defaults — both pass every timeout explicitly — so a
    `term_timeout=0` or `kill_timeout=0` default is invisible to them even though it
    collapses the graceful-SIGTERM window (skips the supervisor's bash EXIT trap,
    issue #436's leaked-ttyd/webterm_* regression) or turns the un-caught final
    `proc.wait(timeout=kill_timeout)` into an immediate `TimeoutExpired` out of
    `finally:` on the exact hang path `_reap` exists to absorb."""
    params = inspect.signature(_reap).parameters
    assert params["term_timeout"].default >= 5, (
        "term_timeout default collapses the graceful-SIGTERM window before a "
        "supervisor's bash EXIT trap can run (issue #436)"
    )
    assert params["kill_timeout"].default >= 1, (
        "kill_timeout default is <1s — the un-caught final wait would raise "
        "TimeoutExpired out of finally: on the hang path _reap exists to absorb"
    )


# --- wiring: every `proc = _run_bg(...)` teardown must actually route through _reap ---
#
# docs/defeat_shapes/339: the tests above prove `_reap` itself escalates to SIGKILL, but
# none of them observe whether any of the six call sites in THIS file still calls it —
# under normal (fast-exiting) conditions `finally: _reap(proc)` and a hand-rolled
# `finally: proc.terminate(); proc.wait(timeout=10)` are behaviorally identical, so
# reverting one call site to the pre-fix shape is invisible to the rest of the suite. This
# walks this file's OWN source and asserts every such teardown is structurally
# `finally: _reap(proc)` — nothing else — which a mutation on any one call site trips.

def _run_bg_teardown_sites():
    """Yield (function_name, finally_body) for every function whose body assigns
    `proc = _run_bg(...)` — the six real-process teardown call sites this guards."""
    tree = ast.parse(Path(__file__).read_text())
    for func in ast.walk(tree):
        if not isinstance(func, ast.FunctionDef):
            continue
        has_run_bg_proc = any(
            isinstance(stmt, ast.Assign)
            and len(stmt.targets) == 1
            and isinstance(stmt.targets[0], ast.Name)
            and stmt.targets[0].id == "proc"
            and isinstance(stmt.value, ast.Call)
            and isinstance(stmt.value.func, ast.Name)
            and stmt.value.func.id == "_run_bg"
            for stmt in func.body
        )
        if not has_run_bg_proc:
            continue
        tries = [stmt for stmt in func.body if isinstance(stmt, ast.Try)]
        assert len(tries) == 1, (
            f"{func.name}: expected exactly one top-level try/finally around its "
            f"`_run_bg` process, found {len(tries)}"
        )
        yield func.name, tries[0].finalbody


def test_all_run_bg_teardowns_route_through_reap():
    """WIRING: reverting any one of the six `finally: _reap(proc)` teardowns to the
    pre-issue-#436 `proc.terminate(); proc.wait(timeout=10)` shape must be caught — it
    silently drops the SIGKILL escalation for that call site alone."""
    sites = list(_run_bg_teardown_sites())
    assert len(sites) == 6, (
        f"expected 6 `proc = _run_bg(...)` teardown call sites in this file, found "
        f"{len(sites)} — if you added one, give it `finally: _reap(proc)` and then "
        f"update this guard's count"
    )
    for name, finalbody in sites:
        assert len(finalbody) == 1, (
            f"{name}: `finally:` must be exactly `_reap(proc)` — found "
            f"{len(finalbody)} statement(s) instead, which bypasses the SIGKILL "
            f"escalation from issue #436"
        )
        [stmt] = finalbody
        is_reap_call = (
            isinstance(stmt, ast.Expr)
            and isinstance(stmt.value, ast.Call)
            and isinstance(stmt.value.func, ast.Name)
            and stmt.value.func.id == "_reap"
            and len(stmt.value.args) == 1
            and isinstance(stmt.value.args[0], ast.Name)
            and stmt.value.args[0].id == "proc"
            and not stmt.value.keywords
        )
        assert is_reap_call, (
            f"{name}: `finally:` does not call `_reap(proc)` with no other arguments — "
            f"teardown no longer escalates to SIGKILL on a hung supervisor (issue #436), "
            f"or overrides a timeout that collapses the graceful-SIGTERM window"
        )


# --- wiring: a promptness check must stay a DIRECT proc.wait(), not be absorbed into _reap ---
#
# docs/defeat_shapes/339 round 4: _reap's own docstring draws a must-never boundary — tests
# that assert SIGTERM promptness as their actual behavior-under-test call `proc.wait()`
# directly for that, in the try BODY, above `finally: _reap(proc)`; `_reap` is finally:-only
# cleanup, never a replacement for that assertion. test_all_run_bg_teardowns_route_through_reap
# above reads ONLY `tries[0].finalbody` — it has no opinion on the try body — so swallowing
# either promptness pair into the finally's `_reap(proc)` (`_reap(proc)` in the body, ANOTHER
# `_reap(proc)` in finally) is invisible to it. This walks the same file's own AST a second
# way: for the two functions whose try body contains a direct promptness check, assert that
# check is still there and still direct, not routed through `_reap`.

_PROMPTNESS_CHECK_FUNCS = frozenset({
    "test_disabled_wall_still_writes_empty_map_and_idles",
    "test_disabled_wall_sigterm_does_not_orphan_the_idle_sleep",
})


def _is_proc_method_call(stmt, method_name):
    return (
        isinstance(stmt, ast.Expr)
        and isinstance(stmt.value, ast.Call)
        and isinstance(stmt.value.func, ast.Attribute)
        and stmt.value.func.attr == method_name
        and isinstance(stmt.value.func.value, ast.Name)
        and stmt.value.func.value.id == "proc"
    )


def _wait_timeout_literal(stmt):
    """The int literal passed as `timeout=` to a `proc.wait(...)` Expr statement, or None."""
    if not _is_proc_method_call(stmt, "wait"):
        return None
    for kw in stmt.value.keywords:
        if kw.arg == "timeout" and isinstance(kw.value, ast.Constant) and isinstance(kw.value.value, int):
            return kw.value.value
    return None


def _decorator_forces_unconditional_skip(func):
    """True if `func` carries `@pytest.mark.skip` — bare or called — outright, or
    `@pytest.mark.skipif(...)` with a literal (not runtime-computed) condition — i.e.
    `skipif(True, ...)` / `skipif(False, ...)`, as opposed to a real check like
    `shutil.which("pgrep") is None`. A site hidden behind either NEVER RUNS regardless
    of environment, yet its try-body AST is byte-identical to a live site — invisible
    to every check in this file unless something looks at `func.decorator_list`, not
    just `func.body`."""
    for dec in func.decorator_list:
        if isinstance(dec, ast.Attribute):
            # bare `@pytest.mark.skip` (no parens) is a plain attribute access, not a
            # call — pytest accepts it, and it's the shortest way to write exactly
            # what this helper exists to reject
            if dec.attr == "skip":
                return True
            continue
        if not (isinstance(dec, ast.Call) and isinstance(dec.func, ast.Attribute)):
            continue
        if dec.func.attr == "skip":
            return True
        if dec.func.attr == "skipif" and dec.args and isinstance(dec.args[0], ast.Constant):
            return True
    return False


def _promptness_check_try_bodies():
    """Yield (function_name, try_body) for the two functions above — the try body, NOT the
    finally body that `_run_bg_teardown_sites` already covers."""
    tree = ast.parse(Path(__file__).read_text())
    for func in ast.walk(tree):
        if not isinstance(func, ast.FunctionDef) or func.name not in _PROMPTNESS_CHECK_FUNCS:
            continue
        assert not _decorator_forces_unconditional_skip(func), (
            f"{func.name}: carries a skip/skipif decorator with a literal (always-true) "
            f"condition — this guarded promptness site would never actually run, no "
            f"SIGTERM ever sent to a real supervisor, while still being counted, extracted "
            f"and replayed by every check below as if it executed"
        )
        tries = [stmt for stmt in func.body if isinstance(stmt, ast.Try)]
        assert len(tries) == 1, (
            f"{func.name}: expected exactly one top-level try/finally around its "
            f"`_run_bg` process"
        )
        assert not tries[0].handlers, (
            f"{func.name}: its try statement has an `except` clause — a "
            f"`proc.wait(timeout=...)` TimeoutExpired caught there, before reaching "
            f"`finally: _reap(proc)`, would never be seen by the PROPERTY block below, "
            f"which replays only the try BODY (via `_proc_call_run`), never any handler "
            f"wrapped around it"
        )
        yield func.name, tries[0].body


def _proc_call_run(body):
    """The maximal contiguous run, at the tail of a try body, of bare `proc.<method>(...)`
    Expr statements — the guarded terminate/wait pattern, whatever it currently contains.
    The run boundary is structural (any Expr call on `proc`), not the two method names, so
    a mutation that squeezes in an extra `proc.kill()` next to `proc.terminate()` /
    `proc.wait()` stays INSIDE the run and gets extracted (and executed) along with them —
    it cannot hide next to the pinned pair the way a name-based extraction would let it."""

    def is_proc_call(stmt):
        return (
            isinstance(stmt, ast.Expr)
            and isinstance(stmt.value, ast.Call)
            and isinstance(stmt.value.func, ast.Attribute)
            and isinstance(stmt.value.func.value, ast.Name)
            and stmt.value.func.value.id == "proc"
        )

    runs, current = [], []
    for stmt in body:
        if is_proc_call(stmt):
            current.append(stmt)
        elif current:
            runs.append(current)
            current = []
    if current:
        runs.append(current)

    for run in runs:
        if any(_is_proc_method_call(s, "terminate") for s in run) and any(
            _wait_timeout_literal(s) is not None for s in run
        ):
            return run
    return []


def _diverts_proc_liveness(stmt):
    """True if `stmt` is a bare `Expr` call that names `proc` but is NOT itself a
    `proc.<method>()` call — e.g. `os.kill(proc.pid, 9)`. `_proc_call_run`'s run
    boundary only recognizes `proc.<method>()` Expr statements, so a statement shaped
    like this sits OUTSIDE whatever run it precedes: it can kill or signal the real
    `proc` before the pinned terminate/wait run ever executes, silently making that
    run's promptness assertion vacuous, while being invisible to both the run-boundary
    scan and the replay (which only ever sees the run's own extracted statements)."""
    if not (isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Call)):
        return False
    call = stmt.value
    if (
        isinstance(call.func, ast.Attribute)
        and isinstance(call.func.value, ast.Name)
        and call.func.value.id == "proc"
    ):
        return False  # a proc.<method>() call itself — legitimate, joins the run
    return any(isinstance(n, ast.Name) and n.id == "proc" for n in ast.walk(call))


def test_promptness_checks_are_not_absorbed_into_reap(tmp_path):
    """WIRING + PROPERTY: `_reap` is finally:-only cleanup, never a replacement for a test's
    own SIGTERM-promptness assertion (see `_reap`'s docstring). Replacing either of the two
    direct `proc.terminate(); proc.wait(timeout=<=5)` pairs above with a bare `_reap(proc)`
    call is invisible to `test_all_run_bg_teardowns_route_through_reap`, which reads only
    the `finally:` body — the AST checks below pin the try BODY directly so that substitution
    fails.

    That alone only proves the SHAPE is still there, not that the shape does anything — a
    `proc.terminate(); proc.wait(timeout=5)` whose `TimeoutExpired` got caught and discarded
    would still satisfy every AST assertion below while being just as much a no-op as routing
    through `_reap`. The block after them drives the actual PROPERTY this check exists for —
    and does so by extracting the REAL guarded statements (via `_proc_call_run`, source and
    all) out of each of the two functions above and `exec`-ing THEM against a synthetic
    SIGTERM-ignoring child, rather than a hand-written stand-in that only reproduces the
    mechanism by hand: a child that ignores SIGTERM must still be detected PROMPTLY, i.e. the
    actual call-site pattern must fail fast (raise) on such a child, rather than silently
    succeeding the way `_reap` would (its SIGKILL escalation catches exactly that
    `TimeoutExpired` and keeps going, taking up to `term_timeout + kill_timeout` seconds
    before the process is even confirmed dead — masking a real promptness regression instead
    of failing on it).
    """
    source_text = Path(__file__).read_text()

    sites = list(_promptness_check_try_bodies())
    assert len(sites) == 2, (
        f"expected 2 promptness-check call sites in this file (one per name in "
        f"_PROMPTNESS_CHECK_FUNCS), found {len(sites)} — a guarded function renamed "
        f"out of sync with _PROMPTNESS_CHECK_FUNCS silently drops out of this loop "
        f"instead of failing it"
    )
    for name, body in sites:
        assert not any(
            isinstance(stmt, ast.Expr)
            and isinstance(stmt.value, ast.Call)
            and isinstance(stmt.value.func, ast.Name)
            and stmt.value.func.id == "_reap"
            for stmt in body
        ), (
            f"{name}: its try body calls `_reap()` — that absorbs the SIGTERM-promptness "
            f"check this test exists to make, and _reap is documented as finally:-only "
            f"cleanup, not a replacement for it"
        )

        assert any(_is_proc_method_call(stmt, "terminate") for stmt in body), (
            f"{name}: missing a direct `proc.terminate()` in its try body — the "
            f"SIGTERM-promptness check this test exists to make is gone"
        )

        timeouts = [t for t in (_wait_timeout_literal(stmt) for stmt in body) if t is not None]
        assert timeouts and all(t <= 5 for t in timeouts), (
            f"{name}: missing a direct `proc.wait(timeout=<=5)` in its try body, or its "
            f"bound was widened past the 5s promptness assertion this test makes"
        )

        # PROPERTY: pull the actual guarded statements — not a rewritten stand-in — out of
        # THIS function's own source, and exec them against a synthetic SIGTERM-ignoring
        # child. This must actually detect such a child, and detect it PROMPTLY, not by
        # falling through to `_reap`'s SIGKILL fallback.
        run = _proc_call_run(body)
        assert run, f"{name}: could not locate its terminate/wait run in the try body"

        run_start = body.index(run[0])
        for preceding_stmt in body[:run_start]:
            assert not _diverts_proc_liveness(preceding_stmt), (
                f"{name}: a statement before the guarded terminate/wait run can kill "
                f"or signal `proc` (e.g. `os.kill(proc.pid, ...)`) without going "
                f"through a `proc.<method>()` call — `_proc_call_run`'s boundary "
                f"cannot see it, so the guarded process could already be dead by the "
                f"time the pinned run executes, making the promptness assertion below "
                f"vacuous while the replay (which only ever sees the run's own "
                f"statements against a freshly spawned, still-alive stub) stays "
                f"untouched"
            )

        segment = "\n".join(ast.get_source_segment(source_text, stmt) for stmt in run)
        code = compile(segment, filename=f"<{name} promptness pattern>", mode="exec")

        stub = tmp_path / f"ignore_term_{name}.py"
        stub.write_text(
            "import signal, time\n"
            "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
            "time.sleep(30)\n"
        )
        proc = subprocess.Popen([sys.executable, str(stub)])
        try:
            time.sleep(0.3)  # let it install the SIGTERM handler before we send one
            start = time.monotonic()
            with pytest.raises(subprocess.TimeoutExpired):
                exec(code, {"proc": proc})  # the pinned call site's OWN statements
            elapsed = time.monotonic() - start
            assert elapsed < 8, (
                f"{name}: its actual terminate/wait pattern must fail FAST on a "
                f"SIGTERM-ignoring child — a slow or missing failure here means detection "
                f"got routed through something with a SIGKILL fallback (like _reap), which "
                f"masks the exact promptness regression this guard exists to catch"
            )
        finally:
            _reap(proc)
