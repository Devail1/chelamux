"""CMX-476: "the device you're using wins".

CMX-175 gives a client `ignore-size` only when its tab is hidden or its pane is
docked, so a desktop tab left VISIBLE while its owner is away keeps sizing the
window under `window-size largest`, and the phone they are holding scrolls
sideways. Now each connection also reports user INPUT, and the watched client
with the most recent input (within config.TERM_ACTIVE_INPUT_MINUTES) is the only
one `largest` sees; every other client of the window gets `ignore-size`. No
recent input anywhere falls back to the CMX-175 rule.

The acceptance tests drive the real routes into a REAL tmux server on a private
`-L` socket (a PATH shim, as tests/test_epoch_live.py does), with two attached
clients on ptys of different sizes: never the live server.
"""

from __future__ import annotations

import fcntl
import os
import pty
import shutil
import struct
import subprocess
import tempfile
import termios
import threading
import time
import uuid

import pytest

from chela import config
from chela.dashboard import app as dash

TMUX_BIN = shutil.which("tmux")


@pytest.fixture(autouse=True)
def _isolate():
    dash._TERM_CID_TTY.clear()
    dash._TERM_CID_STATE.clear()
    yield
    dash._TERM_CID_TTY.clear()
    dash._TERM_CID_STATE.clear()


# --- the pure rule ------------------------------------------------------------------------

W = 600.0  # a 10-minute active window, in seconds


def st(watching=True, input_at=None):
    return {"watching": watching, "input_at": input_at, "applied": None}


def test_rule_no_input_anywhere_falls_back_to_watched():
    flags = dash._size_flags({"desk": st(True), "phone": st(True), "bg": st(False)}, now=1000.0, window_s=W)
    assert flags == {"desk": False, "phone": False, "bg": True}


def test_rule_latest_input_wins_and_every_other_client_is_ignored_even_visible():
    flags = dash._size_flags({"desk": st(True, 900.0), "phone": st(True, 950.0)}, now=1000.0, window_s=W)
    assert flags == {"desk": True, "phone": False}
    flags = dash._size_flags({"desk": st(True, 960.0), "phone": st(True, 950.0)}, now=1000.0, window_s=W)
    assert flags == {"desk": False, "phone": True}


def test_rule_input_exactly_at_the_window_edge_still_counts_and_one_past_does_not():
    assert dash._size_flags({"a": st(True, 400.0), "b": st(True)}, now=1000.0, window_s=W) == {"a": False, "b": True}
    assert dash._size_flags({"a": st(True, 399.0), "b": st(True)}, now=1000.0, window_s=W) == {"a": False, "b": False}


def test_rule_an_unwatched_client_never_is_active_whatever_its_input():
    # The phone locked (tab hidden) right after typing: the visible desk falls back to watched.
    flags = dash._size_flags({"desk": st(True, None), "phone": st(False, 990.0)}, now=1000.0, window_s=W)
    assert flags == {"desk": False, "phone": True}


def test_rule_a_stale_active_input_loses_to_a_recent_one():
    flags = dash._size_flags({"desk": st(True, 100.0), "phone": st(True, 990.0)}, now=1000.0, window_s=W)
    assert flags == {"desk": True, "phone": False}


def test_rule_no_limit_on_devices():
    states = {f"c{i}": st(True, float(i)) for i in range(12)}
    flags = dash._size_flags(states, now=20.0, window_s=W)
    assert [c for c, ig in flags.items() if not ig] == ["c11"]


# --- config ------------------------------------------------------------------------------

@pytest.mark.parametrize("raw, want", [("10", 10.0), ("2.5", 2.5), ("0", 10.0), ("-3", 10.0), ("x", 10.0), ("", 10.0), ("nan", 10.0)])
def test_active_minutes_parse(raw, want):
    assert config._positive_float(raw, 10.0) == want


def test_active_minutes_parse_none_is_the_default():
    assert config._positive_float(None, 10.0) == 10.0   # TypeError, not ValueError


@pytest.mark.parametrize("env, want", [(None, "10.0"), ("2", "2.0")])
def test_active_minutes_from_env_else_ten(env, want):
    import sys
    e = {k: v for k, v in os.environ.items() if k != "CHELA_TERM_ACTIVE_INPUT_MINUTES"}
    if env is not None:
        e["CHELA_TERM_ACTIVE_INPUT_MINUTES"] = env
    out = subprocess.run([sys.executable, "-c", "from chela import config; print(config.TERM_ACTIVE_INPUT_MINUTES)"],
                         env=e, capture_output=True, text=True, timeout=60)
    assert out.stdout.strip() == want, out.stderr


def test_active_window_is_minutes_times_sixty(monkeypatch):
    monkeypatch.setattr(config, "TERM_ACTIVE_INPUT_MINUTES", 3.0)
    assert dash._term_active_window_s() == 180.0


# --- the routes, with tmux stubbed ---------------------------------------------------------

@pytest.fixture
def stubbed(monkeypatch):
    monkeypatch.setattr(dash, "_terminals_port_map", lambda: {"@9": 5301})
    calls = []
    monkeypatch.setattr(dash, "_set_client_ignore_size", lambda tty, ignore: calls.append((tty, ignore)) or True)
    return calls


def post(path, body):
    return dash.app.test_client().post(path, json=body)


def test_input_route_makes_the_reporter_active_and_ignores_the_visible_sibling(stubbed):
    dash._TERM_CID_TTY[("@9", "desk")] = "/dev/pts/1"
    dash._TERM_CID_TTY[("@9", "phone")] = "/dev/pts/2"
    resp = post("/api/term/@9/input", {"cid": "phone"})
    assert resp.status_code == 200
    assert resp.get_json() == {"ok": True, "tracked": True}
    # cleared first, then set: never a moment with every client ignored
    assert stubbed == [("/dev/pts/2", False), ("/dev/pts/1", True)]
    stubbed.clear()
    post("/api/term/@9/input", {"cid": "desk"})
    assert stubbed == [("/dev/pts/1", False), ("/dev/pts/2", True)]


def test_reconcile_sends_only_changed_flags(stubbed):
    dash._TERM_CID_TTY[("@9", "a")] = "/dev/pts/1"
    post("/api/term/@9/input", {"cid": "a"})
    assert stubbed == [("/dev/pts/1", False)]
    stubbed.clear()
    post("/api/term/@9/input", {"cid": "a"})
    assert stubbed == []


def test_a_failed_apply_is_retried_on_the_next_reconcile(monkeypatch):
    monkeypatch.setattr(dash, "_terminals_port_map", lambda: {"@9": 5301})
    calls, ok = [], [False]
    monkeypatch.setattr(dash, "_set_client_ignore_size", lambda tty, ignore: calls.append((tty, ignore)) or ok[0])
    dash._TERM_CID_TTY[("@9", "a")] = "/dev/pts/1"
    post("/api/term/@9/input", {"cid": "a"})
    assert dash._TERM_CID_STATE[("@9", "a")]["applied"] is None
    ok[0] = True
    dash._reconcile_term_size("@9")
    assert calls == [("/dev/pts/1", False), ("/dev/pts/1", False)]
    assert dash._TERM_CID_STATE[("@9", "a")]["applied"] is False


def test_claimed_client_is_tracked_and_ignored_while_another_device_is_active(stubbed):
    dash._TERM_CID_TTY[("@9", "phone")] = "/dev/pts/2"
    post("/api/term/@9/input", {"cid": "phone"})
    stubbed.clear()
    dash._term_client_claimed("@9", "desk", "/dev/pts/1")
    assert dash._TERM_CID_TTY[("@9", "desk")] == "/dev/pts/1"
    assert ("@9", "desk") in dash._TERM_CID_STATE
    assert stubbed == [("/dev/pts/1", True)]


def test_gone_client_is_forgotten_and_the_rest_fall_back(stubbed):
    dash._TERM_CID_TTY[("@9", "desk")] = "/dev/pts/1"
    dash._TERM_CID_TTY[("@9", "phone")] = "/dev/pts/2"
    post("/api/term/@9/input", {"cid": "phone"})
    stubbed.clear()
    dash._term_client_gone("@9", "phone", "/dev/pts/2")
    assert ("@9", "phone") not in dash._TERM_CID_TTY
    assert ("@9", "phone") not in dash._TERM_CID_STATE
    assert stubbed == [("/dev/pts/1", False)]


def test_a_stale_close_never_drops_the_reconnected_connection(stubbed):
    dash._TERM_CID_TTY[("@9", "phone")] = "/dev/pts/7"   # the same cid, already reconnected
    post("/api/term/@9/input", {"cid": "phone"})
    stubbed.clear()
    dash._term_client_gone("@9", "phone", "/dev/pts/2")  # the OLD socket's cleanup
    assert dash._TERM_CID_TTY[("@9", "phone")] == "/dev/pts/7"
    assert dash._TERM_CID_STATE[("@9", "phone")]["input_at"] is not None
    assert stubbed == []


def test_term_ws_wires_claim_and_gone():
    src = open(dash.__file__).read()
    ws = src[src.index("def term_ws"):src.index('@app.route("/api/term/ready")')]
    assert "_term_client_claimed(wid, cid, tty)" in ws
    assert "_term_client_gone(wid, cid, tty)" in ws


def test_input_route_unregistered_cid_is_a_noop(stubbed):
    resp = post("/api/term/@9/input", {"cid": "nobody"})
    assert resp.get_json() == {"ok": True, "tracked": False}
    assert stubbed == []
    assert dash._TERM_CID_STATE == {}


def test_input_route_404s_for_unknown_window(stubbed):
    assert post("/api/term/@404/input", {"cid": "x"}).status_code == 404


def test_input_marks_the_client_watched(stubbed):
    dash._TERM_CID_TTY[("@9", "a")] = "/dev/pts/1"
    post("/api/term/@9/watch", {"cid": "a", "watching": False})
    assert dash._TERM_CID_STATE[("@9", "a")]["watching"] is False
    post("/api/term/@9/input", {"cid": "a"})
    assert dash._TERM_CID_STATE[("@9", "a")]["watching"] is True


def test_reconcile_all_hands_the_size_back_once_input_ages_out(stubbed, monkeypatch):
    dash._TERM_CID_TTY[("@9", "desk")] = "/dev/pts/1"
    dash._TERM_CID_TTY[("@9", "phone")] = "/dev/pts/2"
    post("/api/term/@9/watch", {"cid": "desk", "watching": True})
    post("/api/term/@9/input", {"cid": "phone"})
    stubbed.clear()
    later = time.monotonic() + config.TERM_ACTIVE_INPUT_MINUTES * 60 + 5
    monkeypatch.setattr(dash.time, "monotonic", lambda: later)
    dash._reconcile_all_term_sizes()
    assert stubbed == [("/dev/pts/1", False)]   # the desk counts again; the phone already did


def test_a_state_without_a_tty_is_skipped(stubbed):
    dash._TERM_CID_STATE[("@9", "ghost")] = st(True, time.monotonic())
    dash._reconcile_term_size("@9")
    assert stubbed == []


def test_api_agents_tick_reconciles(monkeypatch):
    src = open(dash.__file__).read()
    tick = src[src.index("def api_agents"):]
    tick = tick[:tick.index("return jsonify(agents)")]
    assert "_reconcile_all_term_sizes()" in tick


# --- the real tmux, on a private socket --------------------------------------------------

def _assert_scratch(sock: str) -> None:
    assert sock.startswith("chelatest-") and sock != "default", f"unsafe tmux socket: {sock}"


class Client:
    """A real `tmux attach` on its own pty, sized like a device's viewport."""

    def __init__(self, sock, env, session, cols, rows):
        self.master, slave = pty.openpty()
        fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))
        self.tty = os.ttyname(slave)
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
    tmuxdir = tempfile.mkdtemp(prefix="cmx476", dir="/tmp")
    monkeypatch.setenv("PATH", f"{shim}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("TMUX_TMPDIR", tmuxdir)
    monkeypatch.setenv("TERM", "xterm-256color")
    monkeypatch.delenv("TMUX", raising=False)
    monkeypatch.delenv("TMUX_PANE", raising=False)
    env = dict(os.environ)

    def tmux(*args):
        _assert_scratch(sock)
        return subprocess.run([TMUX_BIN, "-L", sock, *args], env=env,
                              capture_output=True, text=True, timeout=10)

    session = "cmx476"
    assert tmux("new-session", "-d", "-s", session, "-x", "100", "-y", "30").returncode == 0
    # the production options (scripts/agent-terminals.sh)
    tmux("set-option", "-g", "window-size", "largest")
    tmux("set-window-option", "-g", "aggressive-resize", "on")
    wid = tmux("display-message", "-p", "-t", session, "#{window_id}").stdout.strip()
    monkeypatch.setattr(dash, "_terminals_port_map", lambda: {wid: 5301})
    clients = []

    class R:
        pass

    r = R()
    r.wid = wid

    def attach(cid, cols, rows=40):
        before = set(tmux("list-clients", "-F", "#{client_tty}").stdout.split())
        c = Client(sock, env, session, cols, rows)
        clients.append(c)
        deadline = time.time() + 10
        while c.tty not in before | set(tmux("list-clients", "-F", "#{client_tty}").stdout.split()):
            assert time.time() < deadline, "the tmux client never attached"
            time.sleep(0.05)
        dash._TERM_CID_TTY[(wid, cid)] = c.tty
        return c

    def width_settles(want):
        deadline = time.time() + 5
        while True:
            got = int(tmux("display-message", "-p", "-t", wid, "#{window_width}").stdout.strip())
            if got == want or time.time() > deadline:
                return got
            time.sleep(0.05)

    def resize(c, cols, rows=40):
        fcntl.ioctl(c.master, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))
        os.killpg(c.proc.pid, 28)  # SIGWINCH: the client re-reads its size

    r.tmux, r.attach, r.width, r.resize = tmux, attach, width_settles, resize
    yield r
    for c in clients:
        c.close()
    tmux("kill-server")
    shutil.rmtree(tmuxdir, ignore_errors=True)


DESK, PHONE = 200, 60


def _two(real):
    desk = real.attach("desk", DESK)
    phone = real.attach("phone", PHONE)
    post(f"/api/term/{real.wid}/watch", {"cid": "desk", "watching": True})
    post(f"/api/term/{real.wid}/watch", {"cid": "phone", "watching": True})
    return desk, phone


def test_real_negative_control_today_the_visible_wide_client_wins(real):
    desk, _ = _two(real)
    assert real.width(DESK) == DESK
    real.resize(desk, 240)   # proves a pty resize reaches tmux (the bare-resize test relies on it)
    assert real.width(240) == 240


def test_real_input_moves_the_window_to_the_device_in_use(real):
    _two(real)
    assert real.width(DESK) == DESK
    post(f"/api/term/{real.wid}/input", {"cid": "phone"})
    assert real.width(PHONE) == PHONE
    post(f"/api/term/{real.wid}/input", {"cid": "desk"})
    assert real.width(DESK) == DESK


def test_real_idle_desk_past_the_window_then_phone_input_gives_the_phone(real, monkeypatch):
    _two(real)
    post(f"/api/term/{real.wid}/input", {"cid": "desk"})
    assert real.width(DESK) == DESK
    later = time.monotonic() + config.TERM_ACTIVE_INPUT_MINUTES * 60 + 1
    monkeypatch.setattr(dash.time, "monotonic", lambda: later)
    dash._reconcile_all_term_sizes()
    assert real.width(DESK) == DESK        # timed out: back to largest among the visible
    post(f"/api/term/{real.wid}/input", {"cid": "phone"})
    assert real.width(PHONE) == PHONE


def test_real_a_bare_resize_from_an_idle_client_does_not_take_the_size(real):
    desk, _ = _two(real)
    post(f"/api/term/{real.wid}/input", {"cid": "phone"})
    assert real.width(PHONE) == PHONE
    real.resize(desk, 240)   # the idle desk's viewport grows; no input
    time.sleep(0.3)
    assert real.width(PHONE) == PHONE


def test_real_the_presenter_pin_keeps_priority_over_input(real):
    _two(real)
    dash._pin_grid(real.wid, 120, 30)
    post(f"/api/term/{real.wid}/input", {"cid": "phone"})
    assert real.width(120) == 120
    post(f"/api/term/{real.wid}/input", {"cid": "desk"})
    assert real.width(120) == 120
