"""CMX-503 — a tmux window never takes a size too small to read.

On 11-24 a dashboard restart refused 10 of 13 sessions: each window was 66x1 with no client
attached, so its prompt could not be read. The first half drives :mod:`chela.winsize` against
a ``subprocess.run`` recorder; the second is the acceptance test, on a REAL tmux server on a
private ``chelatest-`` socket: a one-row client attaches and detaches, the window keeps a usable
size, and a restart reads the prompt.
"""
from __future__ import annotations

import fcntl
import inspect
import os
import pty
import shutil
import struct
import subprocess
import sys
import tempfile
import termios
import threading
import time
import uuid

import pytest

from chela import discovery, messenger, restart, winsize

TMUX_BIN = shutil.which("tmux")


# --------------------------------------------------------------------------
# a subprocess.run recorder
# --------------------------------------------------------------------------

class Rec:
    """Records each argv; answers from ``replies`` (argv-prefix tuple -> reply, a
    CompletedProcess, an exception to raise, or a list consumed one per call)."""

    def __init__(self, replies=None):
        self.replies = dict(replies or {})
        self.calls: list[tuple[list, dict]] = []

    def __call__(self, argv, **kw):
        self.calls.append((argv, kw))
        for prefix, reply in self.replies.items():
            if tuple(argv[:len(prefix)]) == prefix:
                if isinstance(reply, list):
                    reply = reply.pop(0) if len(reply) > 1 else reply[0]
                if isinstance(reply, BaseException):
                    raise reply
                return reply
        return subprocess.CompletedProcess(argv, 0, "", "")

    def argvs(self):
        return [a for a, _ in self.calls]


def _cp(out="", rc=0, err=""):
    return subprocess.CompletedProcess([], rc, out, err)


@pytest.fixture
def rec(monkeypatch):
    def make(replies=None):
        r = Rec(replies)
        monkeypatch.setattr(winsize.subprocess, "run", r)
        return r
    return make


# --------------------------------------------------------------------------
# the gate's hooks
# --------------------------------------------------------------------------

def test_the_documented_floor_and_normal_size():
    assert (winsize.MIN_COLS, winsize.MIN_ROWS) == (20, 6)
    assert (winsize.NORMAL_COLS, winsize.NORMAL_ROWS) == (120, 40)
    assert winsize.HOOK_INDEX == 93


def test_the_hooks_compare_numerically_never_as_strings():
    """tmux's bare `<` compares strings ("29" < "6"): the gate must use `e|<|`."""
    resized = winsize.HOOKS["window-resized"]
    assert resized.startswith(
        "if-shell -F '#{||:#{e|<|:#{window_height},6},#{e|<|:#{window_width},20}}' { ")
    assert "#{<:" not in resized
    for name in ("client-attached", "client-resized", "session-window-changed"):
        assert winsize.HOOKS[name] == (
            "if-shell -F '#{&&:#{@chela-min-pin},#{&&:#{e|>=|:#{client_height},6},"
            "#{e|>=|:#{client_width},20}}}' "
            "{ set-window-option -u window-size ; set-window-option -u @chela-min-pin }")
    assert sorted(winsize.HOOKS) == ["client-attached", "client-resized",
                                     "session-window-changed", "window-resized"]


def test_window_resized_restores_the_last_usable_size_else_records_it():
    resized = winsize.HOOKS["window-resized"]
    small, usable = resized.split(" } { ")
    assert small.endswith(
        "set-window-option window-size manual ; set-window-option @chela-min-pin 1 ; "
        "run-shell \"tmux resize-window -t '#{window_id}' "
        "-x #{?@chela-ok-w,#{@chela-ok-w},120} -y #{?@chela-ok-h,#{@chela-ok-h},40}\"")
    assert usable == ('set-window-option -F @chela-ok-w "#{window_width}" ; '
                      'set-window-option -F @chela-ok-h "#{window_height}" }')


def test_install_gate_sets_each_hook_in_chela_s_own_slot(rec):
    r = rec()
    assert winsize.install_gate() is True
    assert r.argvs() == [["tmux", "set-hook", "-g", f"{name}[93]", cmd]
                         for name, cmd in winsize.HOOKS.items()]
    assert {tuple(sorted(kw.items())) for _, kw in r.calls} == {
        (("capture_output", True), ("text", True), ("timeout", 5))}


def test_a_refused_hook_is_reported_but_the_rest_still_install(rec):
    r = rec({("tmux", "set-hook", "-g", "window-resized[93]"): _cp(rc=1, err="syntax error")})
    assert winsize.install_gate() is False
    assert len(r.calls) == len(winsize.HOOKS)


@pytest.mark.parametrize("exc", [FileNotFoundError("tmux"),
                                 subprocess.TimeoutExpired("tmux", 5)])
def test_no_tmux_or_a_hung_one_never_raises(rec, exc):
    r = rec({("tmux",): exc})
    assert winsize.install_gate() is False
    assert len(r.calls) == 1                 # gives up at once


# --------------------------------------------------------------------------
# window_size / undersized
# --------------------------------------------------------------------------

def test_window_size_reads_cols_rows_and_viewing_clients(rec):
    r = rec({("tmux", "display-message"): _cp("66 1 2\n")})
    assert winsize.window_size("@7") == (66, 1, 2)
    [(argv, kw)] = r.calls
    assert argv == ["tmux", "display-message", "-p", "-t", "@7",
                    "#{window_width} #{window_height} #{window_active_clients}"]
    assert kw == {"capture_output": True, "text": True, "timeout": 5}


@pytest.mark.parametrize("reply", [
    _cp("66 1 2\n", rc=1), _cp(""), _cp("66 1\n"), _cp("66 x 0\n"), _cp("66 1 0 9\n"),
    FileNotFoundError("tmux"), subprocess.TimeoutExpired("tmux", 5),
])
def test_an_unreadable_size_is_none(rec, reply):
    rec({("tmux",): reply})
    assert winsize.window_size("@7") is None


@pytest.mark.parametrize("cols, rows, small", [
    (20, 6, False), (19, 6, True), (20, 5, True), (66, 1, True), (120, 40, False),
])
def test_undersized_below_20_columns_or_6_rows(cols, rows, small):
    assert winsize.undersized(cols, rows) is small


# --------------------------------------------------------------------------
# ensure_readable
# --------------------------------------------------------------------------

def test_a_usable_window_is_left_alone(rec):
    r = rec({("tmux", "display-message"): _cp("80 24 0\n")})
    assert winsize.ensure_readable("@7") is None
    assert [a[1] for a in r.argvs()] == ["display-message"]


def test_an_unknown_size_lets_the_read_decide(rec):
    r = rec({("tmux", "display-message"): _cp(rc=1)})
    assert winsize.ensure_readable("@7") is None
    assert [a[1] for a in r.argvs()] == ["display-message"]


@pytest.mark.parametrize("out, why", [
    ("66 1 1\n", "window is 1 row tall (66x1), and 1 attached client keeps it that size"),
    ("66 3 2\n", "window is 3 rows tall (66x3), and 2 attached clients keep it that size"),
])
def test_a_window_a_client_holds_small_is_not_resized_and_says_how_tall(rec, out, why):
    r = rec({("tmux", "display-message"): _cp(out)})
    assert winsize.ensure_readable("@7") == why
    assert [a[1] for a in r.argvs()] == ["display-message"]   # never resized under a client


def test_an_unviewed_small_window_is_resized_and_pinned(rec):
    r = rec({("tmux", "display-message"): [_cp("66 1 0\n"), _cp("120 40 0\n")]})
    assert winsize.ensure_readable("@7") is None
    argvs = r.argvs()
    assert [a[1] for a in argvs[1:1 + len(winsize.HOOKS)]] == ["set-hook"] * len(winsize.HOOKS)
    assert argvs[1 + len(winsize.HOOKS):] == [
        ["tmux", "set-window-option", "-t", "@7", "window-size", "manual"],
        ["tmux", "set-window-option", "-t", "@7", "@chela-min-pin", "1"],
        ["tmux", "resize-window", "-t", "@7", "-x", "120", "-y", "40"],
        ["tmux", "display-message", "-p", "-t", "@7",
         "#{window_width} #{window_height} #{window_active_clients}"],
    ]


@pytest.mark.parametrize("after, why", [
    (_cp("66 1 0\n"), "window is 1 row tall (66x1) and could not be resized"),
    (_cp("120 4 0\n"), "window is 4 rows tall (120x4) and could not be resized"),
    (_cp(rc=1), "window is 1 row tall (66x1) and could not be resized"),
])
def test_a_resize_that_did_not_take_is_said(rec, after, why):
    rec({("tmux", "display-message"): [_cp("66 1 0\n"), after]})
    assert winsize.ensure_readable("@7") == why


def test_a_failing_resize_step_stops_the_rest(rec):
    r = rec({("tmux", "display-message"): _cp("66 1 0\n"),
             ("tmux", "set-window-option"): subprocess.TimeoutExpired("tmux", 5)})
    assert winsize.ensure_readable("@7") == "window is 1 row tall (66x1) and could not be resized"
    assert [a[1] for a in r.argvs()].count("resize-window") == 0


# --------------------------------------------------------------------------
# wiring
# --------------------------------------------------------------------------

def test_the_dashboard_installs_the_gate_when_it_serves_terminals():
    from chela.dashboard import app as dash
    src = inspect.getsource(dash.main)
    assert "if config.TERMINALS_ENABLED:\n" in src
    assert src.index("if config.TERMINALS_ENABLED:\n") < src.index("winsize.install_gate()")


def test_the_presenter_s_pin_is_never_the_gate_s(monkeypatch):
    from chela.dashboard import app as dash
    calls = []
    monkeypatch.setattr(dash.subprocess, "run", lambda argv, **kw: calls.append((argv, kw)))
    dash._pin_grid("@7", 100, 30)
    assert calls == [
        (["tmux", "set-window-option", "-t", "@7", "window-size", "manual"],
         {"check": True, "timeout": 5}),
        (["tmux", "set-window-option", "-t", "@7", "-u", "@chela-min-pin"],
         {"check": False, "timeout": 5}),
        (["tmux", "resize-window", "-t", "@7", "-x", "100", "-y", "30"],
         {"check": True, "timeout": 5}),
    ]


# --------------------------------------------------------------------------
# the real tmux, on a private socket (the acceptance test)
# --------------------------------------------------------------------------

# A stand-in for Claude Code's TUI: redraws its input box at the bottom on every resize, so a
# 1-row window shows no prompt at all and a usable one shows an empty `❯` under a rule.
_FAKE_TUI = r"""
import os, signal, sys, time
def draw(*_):
    c, r = os.get_terminal_size()
    sys.stdout.write("\x1b[2J\x1b[H" + "\r\n" * max(r - 3, 0) + "─" * min(c, 30)
                     + "\r\n❯ \r\n" + "status")
    sys.stdout.flush()
signal.signal(signal.SIGWINCH, draw)
draw()
while True:
    time.sleep(1)
"""


def _assert_scratch(sock: str) -> None:
    assert sock.startswith("chelatest-") and sock != "default", f"unsafe tmux socket: {sock}"


class Client:
    """A real `tmux attach` on its own pty, sized like a Wall tile."""

    def __init__(self, sock, env, session, cols, rows):
        self.master, slave = pty.openpty()
        fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))
        self.proc = subprocess.Popen([TMUX_BIN, "-L", sock, "attach", "-t", session],
                                     stdin=slave, stdout=slave, stderr=slave, env=env,
                                     start_new_session=True)
        os.close(slave)
        threading.Thread(target=self._drain, daemon=True).start()

    def _drain(self):
        try:
            while os.read(self.master, 65536):
                pass
        except OSError:
            pass

    def resize(self, cols, rows):
        fcntl.ioctl(self.master, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))
        os.killpg(self.proc.pid, 28)  # SIGWINCH

    def close(self):
        self.proc.kill()
        self.proc.wait(timeout=5)
        try:
            os.close(self.master)
        except OSError:
            pass


@pytest.fixture
def real(tmp_path, monkeypatch):
    if TMUX_BIN is None:
        pytest.skip("tmux not installed")
    sock = f"chelatest-{os.getpid()}-{uuid.uuid4().hex[:6]}"
    _assert_scratch(sock)
    shim = tmp_path / "shim"
    shim.mkdir()
    (shim / "tmux").write_text(f'#!/bin/sh\nexec {TMUX_BIN} -L {sock} "$@"\n')
    (shim / "tmux").chmod(0o755)
    tmuxdir = tempfile.mkdtemp(prefix="cmx503", dir="/tmp")
    monkeypatch.setenv("PATH", f"{shim}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("TMUX_TMPDIR", tmuxdir)
    monkeypatch.setenv("TERM", "xterm-256color")
    monkeypatch.delenv("TMUX", raising=False)
    monkeypatch.delenv("TMUX_PANE", raising=False)
    env = dict(os.environ)
    tui = tmp_path / "tui.py"
    tui.write_text(_FAKE_TUI)

    def tmux(*args):
        _assert_scratch(sock)
        return subprocess.run([TMUX_BIN, "-L", sock, *args], env=env,
                              capture_output=True, text=True, timeout=10)

    session = "cmx503"
    assert tmux("new-session", "-d", "-s", session, "-x", "100", "-y", "30",
                sys.executable, str(tui)).returncode == 0
    tmux("set-option", "-g", "window-size", "largest")      # scripts/agent-terminals.sh
    tmux("set-window-option", "-g", "aggressive-resize", "on")
    monkeypatch.setattr(messenger.config, "window_target", lambda w, *a, **k: w)
    clients = []

    class R:
        pass

    r = R()
    r.tmux = tmux
    r.wid = tmux("display-message", "-p", "-t", session, "#{window_id}").stdout.strip()

    def attach(cols, rows):
        c = Client(sock, env, session, cols, rows)
        clients.append(c)
        return c

    def settles(want, secs=5):
        deadline = time.time() + secs
        while True:
            got = winsize.window_size(r.wid)
            if (got and got[:2] == want) or time.time() > deadline:
                return got and got[:2]
            time.sleep(0.05)

    def clients_settle(n):
        deadline = time.time() + 5
        while (winsize.window_size(r.wid) or (0, 0, -1))[2] != n and time.time() < deadline:
            time.sleep(0.05)
        return winsize.window_size(r.wid)[2]

    r.attach, r.settles, r.clients_settle = attach, settles, clients_settle
    yield r
    for c in clients:
        c.close()
    tmux("kill-server")
    shutil.rmtree(tmuxdir, ignore_errors=True)


def _restart_reads_the_prompt(wid):
    """restart_one against the real window: readable + draft are the live ones."""
    from tests.test_restart import SID, Fleet
    f = Fleet({wid: {"name": "tap", "cwd": "/srv/tap", "sid": SID}})
    ops = f.ops()
    ops.readable, ops.draft = winsize.ensure_readable, restart._draft
    return restart.restart_one(wid, ops), f


def test_real_negative_control_without_the_gate_a_one_row_client_leaves_66x1(real):
    c = real.attach(66, 1)
    assert real.settles((66, 1)) == (66, 1)
    c.close()
    assert real.clients_settle(0) == 0
    assert real.settles((66, 1)) == (66, 1)           # the 11-24 state
    assert messenger.input_draft(messenger.capture_pane(real.wid, ansi=True)) is None


def test_real_without_the_gate_the_reader_resizes_an_unviewed_window_and_restarts(real):
    c = real.attach(66, 1)
    real.settles((66, 1))
    c.close()
    real.clients_settle(0)
    r, f = _restart_reads_the_prompt(real.wid)
    assert real.settles((120, 40)) == (120, 40)
    assert r.outcome == "restarted", r.detail
    assert (real.wid, ["-l", "/exit"]) in f.keys


def test_real_a_client_still_holding_it_small_is_refused_with_its_height(real):
    real.attach(66, 1)
    real.settles((66, 1))
    r, f = _restart_reads_the_prompt(real.wid)
    assert (r.outcome, f.keys) == ("refused", [])
    assert r.detail == ("its prompt could not be read: window is 1 row tall (66x1), and 1 "
                        "attached client keeps it that size — not typing /exit blind")


def test_real_acceptance_a_one_row_client_attaches_and_detaches_the_window_stays_usable(real):
    assert winsize.install_gate() is True
    big = real.attach(100, 30)
    assert real.settles((100, 29)) == (100, 29)     # the status line takes a row
    big.close()
    real.clients_settle(0)
    tiny = real.attach(66, 1)
    real.clients_settle(1)
    time.sleep(0.5)
    assert real.settles((100, 29)) == (100, 29)      # never sized by the one-row client
    tiny.close()
    real.clients_settle(0)
    assert real.settles((100, 29)) == (100, 29)
    r, f = _restart_reads_the_prompt(real.wid)
    assert r.outcome == "restarted", r.detail
    assert (real.wid, ["-l", "/exit"]) in f.keys


def test_real_the_gate_s_pin_lifts_when_a_usable_client_comes_back(real):
    assert winsize.install_gate() is True
    tiny = real.attach(66, 1)
    real.clients_settle(1)
    assert real.settles((120, 40)) == (120, 40)       # never sized: the normal size
    tiny.close()
    real.clients_settle(0)
    big = real.attach(90, 25)
    assert real.settles((90, 24)) == (90, 24)         # `largest` again, not pinned
    big.resize(70, 3)                                  # a tile collapsed to 3 rows
    assert real.settles((90, 24)) == (90, 24)
    time.sleep(0.3)
    assert real.settles((90, 24)) == (90, 24)
    big.resize(110, 35)
    assert real.settles((110, 34)) == (110, 34)


def test_real_a_presenter_pin_survives_a_client_attaching(real):
    from chela.dashboard import app as dash
    assert winsize.install_gate() is True
    tiny = real.attach(66, 1)
    real.clients_settle(1)
    real.settles((120, 40))
    dash._pin_grid(real.wid, 100, 30)
    tiny.close()
    real.clients_settle(0)
    real.attach(80, 24)
    real.clients_settle(1)
    time.sleep(0.3)
    assert real.settles((100, 30)) == (100, 30)


def test_real_creating_the_session_installs_the_gate(real, monkeypatch):
    real.tmux("kill-server")
    monkeypatch.setattr(discovery.envutil, "scrub_tmux_secrets", lambda: [])
    proc = discovery.open_window("agent", "/tmp", ["sleep", "30"], session="cmx503b")
    assert proc.returncode == 0, proc.stderr
    # window-resized is a window hook: `show-hooks -g` alone does not list it
    hooks = real.tmux("show-hooks", "-g").stdout + real.tmux("show-hooks", "-gw").stdout
    for name in winsize.HOOKS:
        assert f"{name}[93] " in hooks
