"""CMX-510 — after a headless reboot nothing started a tmux server, so the restore boot pass
had no epoch to key on and no session came back until a human opened a terminal (11-28:
6 h 27 min). The daemon now starts an EMPTY server (``discovery.ensure_server``) on every
tick that finds none; the restore that follows creates the ``chela`` session with its first
relaunched window (CMX-473: never a placeholder shell).

Two halves: a ``subprocess.run`` recorder pins every argv/env/timeout and every outcome, and
a REAL tmux server on a scratch socket (the ``tests/test_epoch_live.py`` harness: ``-L`` via
a PATH shim + a scratch ``TMUX_TMPDIR``) proves the acceptance: no server → the daemon's
calls create the session and restore the recorded session with no client attached; a server
already running → nothing extra.

``tests/conftest.py`` stubs ``discovery.ensure_server`` for every test (a daemon-loop test
must never boot a server on the default socket); this file puts the real one back.
"""
from __future__ import annotations

import json
import subprocess
from types import SimpleNamespace

import pytest

from chela import discovery, envutil, epoch, main, restore
from chela.spawn import SpawnResult

# The scratch-socket fixture (pytest registers it under the name it is imported as).
from tests.test_epoch_live import Scratch
from tests.test_epoch_live import tmux as scratch  # noqa: F401
from tests.test_main_dispatch_log import _stub_daemon_loop_inert

REAL_ENSURE = discovery.ensure_server      # captured before conftest's autouse stub
SESSION = "chela-tests-no-such-session"     # conftest's CHELA_TMUX_SESSION
PREV = "100-1700000000"
SID = "bbbbbbbb-1111-2222-3333-444444444444"
LIVE_SID = "cccccccc-1111-2222-3333-444444444444"


@pytest.fixture
def real_ensure(monkeypatch):
    monkeypatch.setattr(discovery, "ensure_server", REAL_ENSURE)


# --- the recorder half ---------------------------------------------------------------------

class _Run:
    """``subprocess.run`` recorder: ``list-sessions`` answers ``probe`` (an rc, or an
    exception to raise), ``start-server`` answers ``start`` the same way."""

    def __init__(self, probe=1, start=0):
        self.probe, self.start, self.calls = probe, start, []

    def __call__(self, argv, **kw):
        self.calls.append((argv, kw))
        answer = self.probe if argv[1] == "list-sessions" else self.start
        if isinstance(answer, BaseException):
            raise answer
        return subprocess.CompletedProcess(argv, answer, "", "" if answer == 0 else "boom")


@pytest.fixture
def rec(monkeypatch, real_ensure):
    scrubs: list[int] = []
    monkeypatch.delenv("TMUX_TMPDIR", raising=False)
    monkeypatch.setattr(envutil, "server_env", lambda: {
        "ENV": "server", "TMUX": "/tmp/tmux-1/default,1,0", "TMUX_PANE": "%3",
        "CHELA_TMUX_SESSION": "webterm_mirror"})
    monkeypatch.setattr(envutil, "scrub_tmux_secrets", lambda: scrubs.append(1) or [])

    def make(**kw):
        r = _Run(**kw)
        r.scrubs = scrubs
        monkeypatch.setattr(discovery.subprocess, "run", r)
        return r
    return make


PROBE = (["tmux", "list-sessions", "-F", "#{session_name}"],
         {"capture_output": True, "text": True, "timeout": 10})


def test_no_server_starts_an_empty_one_with_the_pinned_env(rec):
    r = rec(probe=1, start=0)
    assert discovery.ensure_server() == "started"
    assert r.calls == [PROBE, (
        ["tmux", "start-server", ";", "set-option", "-g", "exit-empty", "off"],
        {"capture_output": True, "text": True, "timeout": 10,
         "env": {"ENV": "server", "CHELA_TMUX_SESSION": SESSION}})]
    assert r.scrubs == [1]
    assert "new-session" not in str(r.calls), "a server, never a placeholder session"


def test_the_timeout_is_passed_to_both_calls(rec):
    r = rec(probe=1, start=0)
    discovery.ensure_server(timeout=3)
    assert [kw["timeout"] for _, kw in r.calls] == [3, 3]


def test_a_running_server_is_never_touched(rec):
    r = rec(probe=0)
    assert discovery.ensure_server() == "running"
    assert r.calls == [PROBE] and r.scrubs == []


def test_a_missing_TMUX_TMPDIR_refuses_before_any_tmux_call(rec, monkeypatch, tmp_path):
    r = rec()
    monkeypatch.setenv("TMUX_TMPDIR", str(tmp_path / "gone"))
    assert discovery.ensure_server() == "refused"
    assert r.calls == [] and r.scrubs == []


@pytest.mark.parametrize("exc", [FileNotFoundError("tmux"),
                                 subprocess.TimeoutExpired(["tmux"], 10)])
def test_a_probe_that_cannot_run_fails_without_starting(rec, exc):
    r = rec(probe=exc)
    assert discovery.ensure_server() == "failed"
    assert r.calls == [PROBE] and r.scrubs == []


@pytest.mark.parametrize("start", [1, FileNotFoundError("tmux"),
                                   subprocess.TimeoutExpired(["tmux"], 10)])
def test_a_start_that_fails_is_failed_and_scrubs_nothing(rec, start):
    r = rec(probe=1, start=start)
    assert discovery.ensure_server() == "failed"
    assert len(r.calls) == 2 and r.scrubs == []


def test_the_conftest_fence_refuses_a_start_server_on_the_default_socket():
    """The suite-wide fence must catch the call this ticket adds, if a test ever reaches the
    real ``ensure_server`` without a scratch socket (pure classifier: nothing is run)."""
    import shutil

    from conftest import _tmux_violation

    if shutil.which("tmux") is None:
        pytest.skip("tmux not installed: the fence only judges the real binary")
    argv = ["tmux", "start-server", ";", "set-option", "-g", "exit-empty", "off"]
    assert _tmux_violation(argv, None) is not None
    assert _tmux_violation(["tmux", "list-sessions"], None) is None


def test_the_outcome_literals():
    assert (discovery.SERVER_RUNNING, discovery.SERVER_STARTED, discovery.SERVER_REFUSED,
            discovery.SERVER_FAILED) == ("running", "started", "refused", "failed")


# --- the daemon's wrapper ---------------------------------------------------------------

@pytest.fixture
def warnings(monkeypatch):
    seen: list[tuple] = []
    monkeypatch.setattr(main.log, "warning", lambda msg, *a: seen.append((msg, a)))
    return seen


@pytest.mark.parametrize("state,previous,warned", [
    ("failed", None, True), ("refused", None, True), ("refused", "failed", True),
    ("failed", "failed", False), ("refused", "refused", False),
    ("started", None, False), ("running", None, False), ("running", "failed", False),
])
def test_the_daemon_warns_once_per_change_into_a_failure(monkeypatch, warnings, state,
                                                         previous, warned):
    monkeypatch.setattr(discovery, "ensure_server", lambda: state)
    assert main.ensure_tmux_server(previous) == state
    assert bool(warnings) is warned
    if warned:
        assert warnings[0][1] == (state,)


def test_a_raising_ensure_is_logged_and_keeps_the_previous_state(monkeypatch, warnings):
    def boom():
        raise RuntimeError("tmux exploded")
    logged: list[str] = []
    monkeypatch.setattr(discovery, "ensure_server", boom)
    monkeypatch.setattr(main.log, "exception", lambda msg, *a: logged.append(msg))
    assert main.ensure_tmux_server("failed") == "failed"
    assert logged == ["Ensuring a tmux server failed"] and warnings == []


def test_the_daemon_tick_ensures_a_server_BEFORE_the_restore_boot_pass(monkeypatch):
    _stub_daemon_loop_inert(monkeypatch)
    monkeypatch.setattr(main, "DISPATCH_WORKFLOWS", [])
    order: list[str] = []
    monkeypatch.setattr(discovery, "ensure_server", lambda: order.append("ensure") or "started")
    monkeypatch.setattr(main.epoch, "current", lambda: "200-1800000000")
    monkeypatch.setattr(main.restore, "boot_pass", lambda *a, **k: order.append("boot"))
    main.cmd_run(SimpleNamespace())
    assert order == ["ensure", "boot"]


# --- the real tmux half: the acceptance ---------------------------------------------------

def _ask(t: Scratch, *args: str) -> subprocess.CompletedProcess:
    return t._tmux(*args, check=False)


def test_with_no_server_it_starts_one_that_has_an_epoch_and_no_session(
        scratch, real_ensure):  # noqa: F811
    assert epoch.current() is None
    assert discovery.ensure_server() == "started"
    born = epoch.current()
    assert born, "the started server must answer for its epoch, or restore never fires"
    listed = _ask(scratch, "list-sessions", "-F", "#{session_name}")
    assert (listed.returncode, listed.stdout) == (0, ""), "an empty server, no session"
    assert _ask(scratch, "show-options", "-gv", "exit-empty").stdout.strip() == "off"
    # The next tick finds it and does nothing: same server, still no session.
    assert discovery.ensure_server() == "running"
    assert epoch.current() == born
    assert _ask(scratch, "list-sessions", "-F", "#{session_name}").stdout == ""


def test_with_a_server_running_it_creates_nothing_and_sets_nothing(
        scratch, real_ensure):  # noqa: F811
    scratch.start("agent")
    before = epoch.current()
    windows = _ask(scratch, "list-windows", "-a", "-F", "#{session_name}:#{window_name}").stdout
    assert discovery.ensure_server() == "running"
    assert epoch.current() == before
    assert _ask(scratch, "list-windows", "-a",
                "-F", "#{session_name}:#{window_name}").stdout == windows
    assert _ask(scratch, "show-options", "-gv", "exit-empty").stdout.strip() == "on"


def _spawn_real(spawned):
    """``spawn_window`` over the REAL ``discovery.open_window`` (a ``sleep`` stands in for
    ``claude --resume``): the window, and the session it may create, are real."""
    def spawn(cwd, command=None, name=None, **kw):
        spawned.append((cwd, command, name))
        proc = discovery.open_window(name or "five", str(cwd), ["sleep", "300"])
        wid = proc.stdout.strip() or None
        return SpawnResult(ok=proc.returncode == 0, name=name or "five", wid=wid,
                           cwd=str(cwd), error=None if wid else proc.stderr)
    return spawn


def test_headless_boot_creates_the_session_and_restores_with_no_client(
        scratch, real_ensure, tmp_path):  # noqa: F811
    """The acceptance, in the daemon's own order: no server → ``ensure_tmux_server`` →
    ``boot_pass`` on the new epoch. The recorded session comes back in the ``chela`` session
    that its own relaunch created; a session already live elsewhere is not relaunched; and
    the pass does not run twice on the same epoch."""
    work = tmp_path / "five"
    work.mkdir()
    manual = restore.Verdict("session-ids", "@3", PREV, "MANUAL", SID, None, str(work), "five")
    live = restore.Verdict("session-ids", "@4", PREV, "REVIVABLE", LIVE_SID, "@8", "/x", "six")
    marker = tmp_path / "restore-boot.json"
    marker.write_text(json.dumps({"epoch": PREV}))
    spawned: list[tuple] = []
    notices: list[tuple] = []

    def resume_fn(rows, runs):
        return restore.resume(
            rows, runs, spawn_window=_spawn_real(spawned),
            check_resumed=lambda wid, sid: wid in discovery.get_windows_by_id(),
            resume_blocked=lambda sid: None, record_resume_failure=lambda sid, why: 1,
            clear_resume_failure=lambda sid: None, kill_window=lambda wid: None,
            launch_settings_for=lambda sid: None, name_for=lambda sid: None,
            remote_control_on=lambda: False, archive=lambda entry: None,
            remove_session=lambda *a: True, rekey_session=lambda *a: True)

    def boot():
        return restore.boot_pass(
            epoch.current(), resume_enabled=True, marker=marker,
            gather=lambda: ([manual, live], []), resume_fn=resume_fn,
            notify=lambda *a: notices.append(a), roster_epochs=lambda now: [],
            roster_lifetimes=lambda: {}, closed_lookup=lambda sources: {},
            wid_for_session=lambda sid: None)

    assert epoch.current() is None
    assert boot() is None, "no server: no epoch, nothing restored (the 11-28 outage)"

    assert main.ensure_tmux_server(None) == "started"
    out = boot()

    assert spawned == [(str(work), f"claude --resume {SID}", None)], \
        "only the MANUAL row is relaunched — never the session already live at @8"
    assert discovery.session_exists(SESSION)
    assert list(discovery.get_windows_by_id().values()) == ["five"], \
        "the session is born WITH the restored window — no placeholder shell"
    assert _ask(scratch, "list-clients").stdout == "", "no client ever attached"
    assert out["resumed"] and out["resumed"][0].startswith("five (resumed at @")
    assert out["not_resumed"] == []
    assert out["skipped_live"] == ["six: already live at @8"]
    assert len(notices) == 1

    assert main.ensure_tmux_server("started") == "running"
    assert boot() is None, "the same epoch never restores twice"
    assert len(spawned) == 1 and list(discovery.get_windows_by_id().values()) == ["five"]
