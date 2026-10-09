"""🔌🤝 CMX-434 — a live share survives a restart of the process hosting it.

The bridges live in their own process (``chela collab``; the dashboard only hosts them
when that service isn't running), and every live share is persisted to an owner-only
store so whichever host comes up next restores it with the SAME room and pairing code.

What these pin:
  * restore brings a share back with the same room + code (and a stopped one never);
  * the host never reuses an AES-GCM nonce across a restart (the seq ceiling);
  * a restored host refuses a relay-replayed keystroke until the guest answers its
    fresh resume challenge;
  * a window that is gone / changed, or a typing share whose window no longer verifies
    as a sandbox, is NOT restored; an expired UNSANDBOXED override is not restored;
  * the store is 0600 and refuses to live inside a git work tree;
  * ⭐ with no live share, startup does nothing at all;
  * the `chela collab` service serves the shares over its owner-only socket and restores
    them after its own restart; `chela update` leaves it alone unless its code changed,
    and warns BEFORE a restart that interrupts live shares.

No relay, tmux, ttyd or docker is touched: the bridge's pump threads are no-ops, the
window identity and port map are stubbed, conftest gives each test its own CHELA_DIR.
"""
from __future__ import annotations

import json
import os
import stat
import threading
import time
from types import SimpleNamespace

import pytest

from chela import collab_host, config, event_log, share_sandbox, share_store, update
from chela import collab_stream as cs
from chela.dashboard import app as dash
from tests.test_share_typing_gate import sandbox, typing_on  # noqa: F401 — fixtures

WID = "@9"


class FakeRelay:
    def __init__(self):
        self.frames: list[bytes] = []

    def send(self, data):
        self.frames.append(bytes(data))

    def close(self):
        pass


@pytest.fixture
def host(monkeypatch):
    """Bridges that start no threads, on a stubbed window ``@9``."""
    monkeypatch.setattr(config, "COLLAB_RELAY", "wss://relay.example")
    monkeypatch.setattr(cs.Bridge, "_pump_relay_control", lambda self: None)
    monkeypatch.setattr(cs.Bridge, "_pump_ttyd_to_relay", lambda self: None)
    win = {WID: "111 @9 4242"}
    ports = {WID: 5301}
    monkeypatch.setattr(cs, "window_key", lambda wid: win.get(wid))
    monkeypatch.setattr(cs, "_port_map", lambda: ports)
    cs._bridges.clear()
    yield SimpleNamespace(win=win, ports=ports)
    cs._bridges.clear()


def _crash():
    """The hosting process dies without ceremony (SIGKILL): nothing more is written."""
    cs._bridges.clear()


def _events(kind):
    return [e for e in event_log.read()["events"] if e["type"] == kind]


# --- restore: same room, same code ------------------------------------------------------

def test_a_persisted_share_is_restored_with_the_same_room_and_code(host):
    code = cs.start_bridge(WID)
    room = cs._bridges[WID].room
    epoch_before = share_store.load()[WID]["share_epoch"]
    cs.shutdown_all()
    assert WID not in cs._bridges and WID in share_store.load(), "an exit keeps the share"

    got = cs.restore_bridges()

    assert [r["wid"] for r in got if r["restored"]] == [WID]
    b = cs._bridges[WID]
    assert (b.pairing_code, b.room) == (code, room)
    assert cs.share_info(WID)["pairing_code"] == code
    assert cs.share_info(WID)["share_epoch"] == epoch_before


def test_a_deliberately_stopped_share_is_never_restored(host):
    cs.start_bridge(WID)
    cs.stop_bridge(WID)
    assert share_store.load() == {}
    assert cs.restore_bridges() == []
    assert WID not in cs._bridges


def test_an_exit_tells_the_guest_restarting_never_ended(host, monkeypatch):
    cs.start_bridge(WID)
    sent = []
    monkeypatch.setattr(cs._bridges[WID], "_seal_send", lambda typ, pt: sent.append(json.loads(pt)))
    cs.shutdown_all()
    assert sent == [{"t": "restarting"}]


# --- 🔐 no nonce reuse across a restart ---------------------------------------------------

def test_a_restored_host_never_reuses_a_nonce(host, monkeypatch):
    monkeypatch.setattr(cs, "SEQ_BLOCK", 4)   # cross several reservations
    cs.start_bridge(WID)
    b = cs._bridges[WID]
    b._relay = FakeRelay()
    for _ in range(10):
        b._seal_send(cs.e2e.T_OUTPUT, b"before")
    before = b._relay.frames
    joiner = cs.e2e.Session(b.secret, b.room, role="joiner")
    for f in before:
        joiner.open(f)
    _crash()

    cs.restore_bridges()
    b2 = cs._bridges[WID]
    b2._relay = FakeRelay()
    b2._seal_send(cs.e2e.T_OUTPUT, b"after")
    (after,) = b2._relay.frames

    nonces = {f[2:cs.e2e.HEADER_LEN] for f in before}
    assert after[2:cs.e2e.HEADER_LEN] not in nonces, "same key + same nonce = AES-GCM broken"
    assert joiner.open(after)[1] == b"after", "the guest's open session keeps accepting the host"


# --- 🔐 no replayed keystroke into a restored share -------------------------------------

def test_a_restored_host_drops_a_replayed_keystroke_until_the_guest_answers(
        host, monkeypatch, sandbox, typing_on):  # noqa: F811
    cs.start_bridge(WID, allow_typing=True)
    b = cs._bridges[WID]
    monkeypatch.setattr(b, "_forward_input", lambda data: None)
    joiner = cs.e2e.Session(b.secret, b.room, role="joiner")
    old_keystroke = joiner.seal(cs.e2e.T_INPUT, b"rm -rf ~\r")
    b._handle_relay(old_keystroke)
    _crash()

    cs.restore_bridges()
    b2 = cs._bridges[WID]
    forwarded, sent = [], []
    monkeypatch.setattr(b2, "_forward_input", lambda data: forwarded.append(data))
    monkeypatch.setattr(b2, "_seal_send", lambda typ, pt: sent.append(json.loads(pt)))

    b2._handle_relay(old_keystroke)   # the relay replays it
    assert forwarded == [], "a replayed keystroke must never reach the pane"
    (challenge,) = [m for m in sent if m.get("t") == "resume"]

    b2._handle_relay(joiner.seal(cs.e2e.T_CTL, json.dumps(
        {"t": "hello", "cols": 80, "rows": 24, "resume": challenge["n"]}).encode()))
    b2._handle_relay(joiner.seal(cs.e2e.T_INPUT, b"ls\r"))
    assert forwarded == [b"ls\r"], "a guest that answered the challenge types again"
    b2._handle_relay(old_keystroke)
    assert forwarded == [b"ls\r"]


# --- what is NOT restored -----------------------------------------------------------------

def test_a_share_whose_window_is_gone_is_not_restored(host):
    cs.start_bridge(WID)
    _crash()
    host.ports.clear()
    (r,) = cs.restore_bridges()
    assert r == {"wid": WID, "restored": False, "mode": None, "reason": "window gone"}
    assert WID not in cs._bridges and share_store.load() == {}
    assert _events("share.not_restored")


def test_a_share_whose_window_was_recycled_is_not_restored(host):
    cs.start_bridge(WID)
    _crash()
    host.win[WID] = "999 @9 777"   # tmux restarted: same @9, a different window
    (r,) = cs.restore_bridges()
    assert not r["restored"] and WID not in cs._bridges


def test_a_typing_share_that_no_longer_verifies_is_not_restored(host, monkeypatch):
    monkeypatch.setattr(share_sandbox, "check_share_session", lambda wid: (True, ""))
    cs.start_bridge(WID, allow_typing=True)
    _crash()
    monkeypatch.setattr(share_sandbox, "check_share_session", lambda wid: (False, "no container"))
    (r,) = cs.restore_bridges()
    assert not r["restored"] and r["mode"] is None
    assert WID not in cs._bridges, "not as typing — and not laundered into anything else"


def test_a_typing_share_that_still_verifies_is_restored_as_typing(host, monkeypatch):
    monkeypatch.setattr(share_sandbox, "check_share_session", lambda wid: (True, ""))
    cs.start_bridge(WID, allow_typing=True)
    _crash()
    (r,) = cs.restore_bridges()
    assert r["restored"] and cs._bridges[WID].mode() == cs.MODE_TYPING


def _override(ttl_s):
    return {"granted_by": "op@example", "window": "shell-3", "ttl_s": ttl_s}


def test_an_expired_unsandboxed_override_is_not_restored(host):
    cs.start_bridge(WID, unsandboxed=_override(60.0))
    _crash()
    (r,) = cs.restore_bridges(now=time.time() + 120)
    b = cs._bridges[WID]
    assert b.mode() == cs.MODE_VIEW and b.state()["expires_at"] is None
    assert r["mode"] == cs.MODE_VIEW
    assert share_store.load()[WID]["override"] is None


def test_an_override_with_time_left_is_restored_with_only_that_time(host):
    cs.start_bridge(WID, unsandboxed=_override(60.0))
    expires = share_store.load()[WID]["override"]["expires_at"]
    _crash()
    (r,) = cs.restore_bridges(now=expires - 10)
    b = cs._bridges[WID]
    assert r["mode"] == b.mode() == cs.MODE_UNSANDBOXED
    assert b.state()["expires_at"] == expires
    assert b._override["until"] - b._clock() <= 10.0 + 0.5


# --- the store ------------------------------------------------------------------------------

def test_the_store_is_owner_only(host):
    cs.start_bridge(WID)
    mode = stat.S_IMODE(os.stat(share_store.path()).st_mode)
    assert mode == 0o600


def test_the_store_refuses_to_live_inside_a_git_work_tree(host, tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    monkeypatch.setattr(config, "CHELA_DIR", repo / ".chela")
    with pytest.raises(share_store.UnsafeStoreLocation):
        share_store.put(WID, {"wid": WID})
    assert cs.start_bridge(WID), "the live share still works — it just won't be persisted"
    assert not share_store.path().exists()


# --- ⭐ the case that must be ACCEPTED: no live share, startup unchanged -------------------

def test_with_no_live_share_startup_is_unchanged(host, monkeypatch):
    started = []
    monkeypatch.setattr(dash.threading, "Thread", lambda *a, **k: started.append(k) or SimpleNamespace(start=lambda: None))
    dash._start_share_restore()
    assert started == [], "no restore thread"
    assert cs.restore_bridges() == [] and cs._bridges == {}
    assert not collab_host.lock_path().exists(), "no host lock taken"
    assert not share_store.path().exists()


# --- the `chela collab` service ---------------------------------------------------------------

def _run_service():
    stop = threading.Event()
    t = threading.Thread(target=collab_host.run_service, args=(stop,), kwargs={"poll": 0.05}, daemon=True)
    t.start()
    deadline = time.monotonic() + 5
    while not collab_host.sock_path().exists():
        assert time.monotonic() < deadline, "the service never bound its socket"
        time.sleep(0.02)
    return stop, t


def _stop_service(stop, t):
    stop.set()
    t.join(5)
    assert not t.is_alive()


def test_the_service_hosts_shares_and_restores_them_after_its_own_restart(host):
    stop, t = _run_service()
    try:
        assert stat.S_IMODE(os.stat(collab_host.sock_path()).st_mode) == 0o600
        code = collab_host.call("start", wid=WID, share_epoch=7)["code"]
        assert collab_host.call("list")["shares"][WID]["share_epoch"] == 7
    finally:
        _stop_service(stop, t)
    assert WID in share_store.load(), "a service restart keeps the share"
    assert collab_host.current_host() is None

    stop, t = _run_service()
    try:
        info = collab_host.call("info", wid=WID)["info"]
        assert info["pairing_code"] == code and info["share_epoch"] == 7
        collab_host.call("stop", wid=WID)
        assert collab_host.call("list")["shares"] == {}
    finally:
        _stop_service(stop, t)
    assert share_store.load() == {}


def test_a_dashboard_restart_keeps_a_share_hosted_by_the_service(host, monkeypatch):
    """The dashboard forgot everything (a restart); the service still has the share — the
    dashboard adopts it, with its code, on the next poll; a share the service lost goes."""
    dash._SHARED.clear()
    dash._share_info.clear()
    monkeypatch.setattr(cs, "_window_dims", lambda wid: (80, 24))
    listing = {WID: {"mode": "view", "expires_at": None, "share_epoch": 5}}
    monkeypatch.setattr(collab_host, "remote_listing", lambda: listing)
    monkeypatch.setattr(collab_host, "remote_info",
                        lambda wid: {"pairing_code": "CODE", "join_url": "https://r/j/x", "share_epoch": 5})
    try:
        dash._sync_shares()
        assert dash._SHARED[WID] == {"cols": 80, "rows": 24}
        assert dash._share_info[WID]["pairing_code"] == "CODE"
        listing.clear()
        dash._sync_shares()
        assert WID not in dash._SHARED and WID not in dash._share_info
    finally:
        dash._SHARED.clear()
        dash._share_info.clear()


def test_the_dashboard_delegates_to_a_live_service_and_takes_no_lock(host, monkeypatch):
    calls = []
    monkeypatch.setattr(collab_host, "call", lambda op, **kw: calls.append((op, kw)) or {"ok": True, "code": "SVC"})
    assert collab_host.start_bridge(WID, share_epoch=3, allow_typing=False) == "SVC"
    assert calls == [("start", {"wid": WID, "share_epoch": 3, "allow_typing": False})]
    assert not collab_host.is_host() and WID not in cs._bridges


# --- the deploy path ---------------------------------------------------------------------------

def test_interruption_notice_counts_live_shares_for_the_hosting_service(host, monkeypatch):
    cs.start_bridge(WID)
    monkeypatch.setattr(collab_host, "current_host", lambda: {"role": "chela-dashboard", "pid": 1})
    note = collab_host.interruption_notice(["chela-daemon", "chela-dashboard"])
    assert note.startswith("⚠️ 1 live share(s) will be interrupted by restarting chela-dashboard")
    assert collab_host.interruption_notice(["chela-daemon"]) == ""
    monkeypatch.setattr(collab_host, "current_host", lambda: {"role": "chela-collab", "pid": 1})
    assert collab_host.interruption_notice(["chela-dashboard"]) == "", \
        "a dashboard deploy doesn't touch a share the service hosts"


class _CP:
    def __init__(self, stdout=""):
        self.returncode, self.stdout, self.stderr = 0, stdout, ""


def _clone(tmp_path):
    import subprocess

    def git(repo, *a):
        subprocess.run(["git", "-C", str(repo), *a], check=True, capture_output=True)
    up = tmp_path / "up"
    git(tmp_path, "init", "-q", "-b", "main", str(up))
    for k, v in (("user.email", "t@example.com"), ("user.name", "T"), ("commit.gpgsign", "false")):
        git(up, "config", k, v)
    (up / "README.md").write_text("seed\n")
    git(up, "add", "README.md")
    git(up, "commit", "-q", "-m", "seed")
    co = tmp_path / "co"
    subprocess.run(["git", "clone", "-q", str(up), str(co)], check=True, capture_output=True)
    return up, co, git


@pytest.mark.parametrize("touched,collab_restarts", [("README.md", False), ("chela/collab_stream.py", True)])
def test_update_restarts_chela_collab_only_when_its_code_changed(tmp_path, monkeypatch, touched, collab_restarts):
    up, co, git = _clone(tmp_path)
    (up / touched).parent.mkdir(parents=True, exist_ok=True)
    (up / touched).write_text("changed\n")
    git(up, "add", touched)
    git(up, "commit", "-q", "-m", "change")
    restarts, order = [], []

    def fake_sh(args, cwd, timeout=update._SHELL_TIMEOUT_SECONDS):
        if args[:2] == ["pm2", "jlist"]:
            return _CP(json.dumps([{"name": n, "pm2_env": {"status": "online"}}
                                   for n in ("chela-collab", "chela-dashboard")]))
        if args[:2] == ["pm2", "restart"]:
            restarts.append(args[2:])
            order.append("restart")
        return _CP()

    monkeypatch.setattr(update, "_sh", fake_sh)
    monkeypatch.setattr(update, "_refresh_plugin_if_needed", lambda repo: ([], ""))
    monkeypatch.setattr(collab_host, "interruption_notice", lambda services: "N live share(s) will be interrupted"
                        if "chela-collab" in services else "")
    result = update.apply(co, on_notice=lambda n: order.append(n))
    assert result.ok
    assert ("chela-collab" in restarts[0]) is collab_restarts
    assert "chela-dashboard" in restarts[0]
    if collab_restarts:
        assert order == ["N live share(s) will be interrupted", "restart"], "warned BEFORE the restart"
        assert result.share_notice
    else:
        assert order == ["restart"] and result.share_notice == ""


# --- round 1 of review: each invariant driven through its real wiring ------------------------

def test_dashboard_main_restores_a_persisted_share_at_startup(host, monkeypatch):
    """The WIRING: ``app.main()`` — not ``_start_share_restore`` called by hand — brings a
    persisted share back (same code) and shows it, when no `chela collab` answers."""
    code = cs.start_bridge(WID)
    _crash()
    for name in ("_start_notifier",):
        monkeypatch.setattr(dash, name, lambda: None)
    monkeypatch.setattr(dash.scheduler, "init", lambda: None)
    monkeypatch.setattr(dash.agent_manager, "start_background_refresh", lambda: None)
    monkeypatch.setattr(dash.collab, "start", lambda: None)
    monkeypatch.setattr(dash.config, "publish_dashboard_port", lambda *a, **k: None)
    monkeypatch.setattr(dash.config, "clear_dashboard_port", lambda: None)
    monkeypatch.setattr(dash.atexit, "register", lambda *a, **k: None)
    monkeypatch.setattr(dash.app, "run", lambda **k: None)
    monkeypatch.setattr(dash, "SHARE_HOST_GRACE", 0.0)
    monkeypatch.setattr(collab_host, "remote_listing", lambda: None)
    monkeypatch.setattr(cs, "_window_dims", lambda wid: (80, 24))
    dash._SHARED.clear()
    dash._share_info.clear()
    try:
        dash.main()
        for t in threading.enumerate():
            if t.name == "share-restore":
                t.join(5)
        assert cs._bridges[WID].pairing_code == code, "startup must restore the persisted share"
        assert dash._share_info[WID]["pairing_code"] == code and WID in dash._SHARED
    finally:
        collab_host.release_host()
        dash._SHARED.clear()
        dash._share_info.clear()


def _restored_typing_bridge(monkeypatch):
    cs.start_bridge(WID, allow_typing=True)
    joiner = cs.e2e.Session(cs._bridges[WID].secret, cs._bridges[WID].room, role="joiner")
    _crash()
    cs.restore_bridges()
    b = cs._bridges[WID]
    forwarded, sent = [], []
    monkeypatch.setattr(b, "_forward_input", lambda data: forwarded.append(data))
    monkeypatch.setattr(b, "_seal_send", lambda typ, pt: sent.append(json.loads(pt)))
    return b, joiner, forwarded, sent


def _hello(joiner, **extra):
    return joiner.seal(cs.e2e.T_CTL, json.dumps({"t": "hello", "cols": 80, "rows": 24, **extra}).encode())


@pytest.mark.parametrize("answer", [None, "wrong", ""], ids=["no-resume", "wrong-nonce", "empty"])
def test_a_hello_that_does_not_answer_the_challenge_never_unlocks_input(
        host, monkeypatch, sandbox, typing_on, answer):  # noqa: F811
    """🔐 Only a hello carrying THIS incarnation's nonce makes a joiner stream fresh — a
    plain hello (what the relay can replay from before the restart) or a wrong one does not."""
    b, joiner, forwarded, sent = _restored_typing_bridge(monkeypatch)
    b._handle_relay(_hello(joiner) if answer is None else _hello(joiner, resume=answer))
    b._handle_relay(joiner.seal(cs.e2e.T_INPUT, b"id\r"))
    assert forwarded == [], "input before the challenge was answered must never reach the pane"
    assert any(m.get("t") == "resume" for m in sent), "the challenge is re-issued"
    b._handle_relay(_hello(joiner, resume=b._resume_nonce))
    b._handle_relay(joiner.seal(cs.e2e.T_INPUT, b"ok\r"))
    assert forwarded == [b"ok\r"]


def test_a_fresh_challenge_per_restore_an_old_answer_is_refused(host, monkeypatch, sandbox, typing_on):  # noqa: F811
    """A hello answering the PREVIOUS incarnation's nonce (replayable) does not unlock the next."""
    b, joiner, forwarded, _ = _restored_typing_bridge(monkeypatch)
    old_answer = _hello(joiner, resume=b._resume_nonce)
    b._handle_relay(old_answer)
    _crash()
    cs.restore_bridges()
    b2 = cs._bridges[WID]
    assert b2._resume_nonce and b2._resume_nonce != b._resume_nonce, "a fresh nonce per restore"
    fwd2 = []
    monkeypatch.setattr(b2, "_forward_input", lambda data: fwd2.append(data))
    monkeypatch.setattr(b2, "_seal_send", lambda typ, pt: None)
    b2._handle_relay(old_answer)
    b2._handle_relay(joiner.seal(cs.e2e.T_INPUT, b"id\r"))
    assert fwd2 == []


@pytest.mark.parametrize("live_key", [None, "111 @9 4242"], ids=["unreadable-now", "readable-now"])
def test_a_share_persisted_with_no_window_identity_is_not_restored(host, live_key):
    """A share whose window identity could not be read when it was minted has nothing to
    verify against — even if tmux can't read the identity now either (None == None)."""
    host.win[WID] = None
    cs.start_bridge(WID)
    assert share_store.load()[WID]["window_key"] is None
    _crash()
    host.win[WID] = live_key
    (r,) = cs.restore_bridges()
    assert not r["restored"] and WID not in cs._bridges and share_store.load() == {}


# --- `chela update`: chela-collab staleness on the NOTHING-TO-PULL path, and the warning order

def _commit(git, repo, path, text, *, date=None):
    (repo / path).parent.mkdir(parents=True, exist_ok=True)
    (repo / path).write_text(text)
    git(repo, "add", path)
    env = {**os.environ, "GIT_COMMITTER_DATE": f"@{date} +0000", "GIT_AUTHOR_DATE": f"@{date} +0000"} if date else None
    import subprocess
    subprocess.run(["git", "-C", str(repo), "commit", "-q", "-m", f"touch {path}"], check=True,
                   capture_output=True, env=env)


def _age_reflog(co, epoch: int) -> None:
    """Pretend the clone happened at ``epoch`` (its reflog entries are dated then)."""
    import re
    log_ = co / ".git" / "logs" / "HEAD"
    log_.write_text(re.sub(r"> \d+ ([+-]\d{4})\t", lambda m: f"> {epoch} {m.group(1)}\t", log_.read_text()))


def _fake_pm2(uptime_s: float, restarts: list, order: list | None = None):
    def fake_sh(args, cwd, timeout=update._SHELL_TIMEOUT_SECONDS):
        if args[:2] == ["pm2", "jlist"]:
            return _CP(json.dumps([{"name": n, "pm2_env": {"status": "online", "pm_uptime": uptime_s * 1000}}
                                   for n in ("chela-collab", "chela-dashboard")]))
        if args[:2] == ["pm2", "restart"]:
            restarts.append(args[2:])
            if order is not None:
                order.append("restart")
            print("PM2-RESTARTED")
        return _CP()
    return fake_sh


@pytest.mark.parametrize("touched,collab_stale", [("README.md", False), ("chela/collab_stream.py", True)])
def test_a_bare_pull_of_collab_code_makes_chela_collab_stale_and_update_restarts_it(
        tmp_path, monkeypatch, touched, collab_stale):
    """Nothing to pull (a bare `git pull` already brought the commit in) — `chela update`
    still restarts `chela-collab` when ITS code changed since it started, even though that
    commit was AUTHORED before the service started; and never when only other code moved."""
    up, co, git = _clone(tmp_path)
    started = time.time() - 600
    _age_reflog(co, int(started) - 7200)                               # the service started on it
    _commit(git, up, touched, "changed\n", date=int(started) - 3600)   # authored BEFORE the start
    git(co, "pull", "-q")                                              # …pulled AFTER it
    restarts: list = []
    monkeypatch.setattr(update, "_sh", _fake_pm2(started, restarts))
    monkeypatch.setattr(update, "_refresh_plugin_if_needed", lambda repo: ([], ""))

    fresh = update.services_running_stale_code(co)
    assert fresh.ok and ("chela-collab" in fresh.stale) is collab_stale
    # CMX-56: import-aware — the dashboard imports the collab stream, never the README.
    assert ("chela-dashboard" in fresh.stale) is collab_stale

    result = update.apply(co)
    assert result.ok and result.behind_before == 0
    if collab_stale:
        assert "chela-collab" in restarts[0] and "chela-dashboard" in restarts[0]
    else:
        assert restarts == []


def test_chela_collab_started_after_the_collab_code_landed_is_not_stale(tmp_path, monkeypatch):
    up, co, git = _clone(tmp_path)
    _commit(git, up, "chela/collab_stream.py", "changed\n", date=int(time.time()) - 7200)
    git(co, "pull", "-q")
    monkeypatch.setattr(update, "_sh", _fake_pm2(time.time() + 5, []))
    assert "chela-collab" not in update.services_running_stale_code(co).stale


def test_chela_update_prints_the_share_warning_before_the_restart(tmp_path, monkeypatch, capsys, host):
    """`chela update` (main.cmd_update, the real apply()) prints the interruption warning
    BEFORE pm2 restarts the process hosting the share."""
    import argparse

    from chela import main
    up, co, git = _clone(tmp_path)
    _commit(git, up, "chela/collab_stream.py", "changed\n")
    cs.start_bridge(WID)
    monkeypatch.setattr(collab_host, "current_host", lambda: {"role": "chela-collab", "pid": 1})
    monkeypatch.setattr(update, "repo_root", lambda: co)
    monkeypatch.setattr(update, "_sh", _fake_pm2(time.time(), []))
    monkeypatch.setattr(update, "_refresh_plugin_if_needed", lambda repo: ([], ""))
    main.cmd_update(argparse.Namespace(check=False))
    out = capsys.readouterr().out
    warn = out.find("1 live share(s) will be interrupted")
    assert warn != -1, out
    assert warn < out.find("PM2-RESTARTED"), "the warning must come BEFORE the restart"


def test_stopping_a_share_that_is_persisted_but_not_running_still_forgets_it(host):
    """Owner hits Stop between a crash and the restore: the share must not come back."""
    cs.start_bridge(WID)
    _crash()
    assert WID in share_store.load() and WID not in cs._bridges
    cs.stop_bridge(WID)
    assert share_store.load() == {}
    assert cs.restore_bridges() == [] and WID not in cs._bridges
