"""🔐 CMX-403 — the guest-typing gate on the HOST side of a shared terminal.

A share is VIEW ONLY unless (a) the ``share_typing`` setting is on, (b) the share was
created with typing allowed, and (c) the window verifies LIVE as a sandboxed session —
re-checked at least every ``SANDBOX_RECHECK_INTERVAL``, never cached from share creation.
The one exception is the trusted-peer UNSANDBOXED override: per share, typed-name
confirmation, time-boxed, bound to one joiner, audited, killed by the share kill switch.

No real container, tmux, ``/proc`` or token is touched: ``share_sandbox``'s probes
(``_pane_root`` / ``_proc_shape`` / ``_inspect``) and the pane writer
(``Bridge._forward_input``) are stubbed, time is a fake clock, and conftest gives every
test its own scratch ``CHELA_DIR`` (so the audit events land in a temp ``events.jsonl``).
"""
from __future__ import annotations

import os
import sys

import pytest

from chela import collab_stream as cs
from chela import config, event_log, share_sandbox
from chela.dashboard import app as dash

UID, GID = os.getuid(), os.getgid()
SID = "0123456789ab"
WORKSPACE = "/tmp"   # any existing absolute dir; verify_container realpaths it


class Clock:
    def __init__(self, t: float = 1000.0) -> None:
        self.t = t

    def __call__(self) -> float:
        return self.t


# --- a stubbed LIVE sandbox: the pane, /proc and docker inspect ---------------------

def _good_argv():
    return ["/usr/bin/python3", "-m", share_sandbox.LAUNCH_MODULE, "run", "--id", SID,
            "--net", "none", os.path.realpath(WORKSPACE)]


def _good_container():
    net = share_sandbox.network_name(SID)
    info = {
        "State": {"Running": True},
        "Config": {"Labels": {share_sandbox.LABEL: SID, share_sandbox.NET_LABEL: "none"},
                   "User": f"{UID}:{GID}", "Env": ["HOME=/home/guest", "LANG=C.UTF-8"]},
        "HostConfig": {"CapDrop": ["ALL"], "CapAdd": None, "Privileged": False,
                       "ReadonlyRootfs": True, "SecurityOpt": ["no-new-privileges"],
                       "Memory": 2 << 30, "PidsLimit": 512, "NetworkMode": net},
        "NetworkSettings": {"Networks": {net: {}}},
        "Mounts": [
            {"Type": "bind", "Source": os.path.realpath(WORKSPACE),
             "Destination": share_sandbox.GUEST_WORKDIR, "RW": True},
            {"Type": "bind", "Source": "/usr/bin/true",
             "Destination": share_sandbox.CLAUDE_MOUNT, "RW": False},
        ],
    }
    network = {"Internal": True,
               "Options": {"com.docker.network.bridge.inhibit_ipv4": "true"},
               "Containers": {"c1": {"Name": share_sandbox.container_name(SID)},
                              "c2": {"Name": share_sandbox.proxy_name(SID)}}}
    return info, network, None


@pytest.fixture
def sandbox(monkeypatch):
    """A window that verifies as a sandboxed session; mutate ``state`` to break it."""
    state = {"argv": _good_argv(), "parent": "tmux: server", "kids": ["docker"],
             "proc_error": None, "inspect": _good_container()}

    def proc_shape(pid):
        if state["proc_error"]:
            raise state["proc_error"]
        return state["argv"], state["parent"], state["kids"]

    monkeypatch.setattr(share_sandbox, "_pane_root", lambda wid: 4242)
    monkeypatch.setattr(share_sandbox, "_proc_shape", proc_shape)
    monkeypatch.setattr(share_sandbox, "_inspect", lambda sid: state["inspect"])
    return state


@pytest.fixture
def typing_on(monkeypatch):
    monkeypatch.setenv("CHELA_SHARE_TYPING", "true")


@pytest.fixture
def typing_off(monkeypatch):
    monkeypatch.setenv("CHELA_SHARE_TYPING", "false")


def _bridge(monkeypatch, **kw):
    """A Bridge whose pane writer and relay sends are captured, on a fake clock."""
    clock = Clock()
    b = cs.Bridge("@9", clock=clock, wallclock=lambda: 1_700_000_000.0 + clock.t, **kw)
    forwarded, sent = [], []
    monkeypatch.setattr(b, "_forward_input", lambda data: forwarded.append(data))
    monkeypatch.setattr(b, "_seal_send", lambda typ, pt: sent.append((typ, pt)))
    return b, clock, forwarded, sent


def _joiner(b, stream_id=None):
    return cs.e2e.Session(b.secret, b.room, role="joiner", stream_id=stream_id)


def _type(b, joiner, data=b"ls\r"):
    b._handle_relay(joiner.seal(cs.e2e.T_INPUT, data))


def _notices(sent):
    return [pt for typ, pt in sent if typ == cs.e2e.T_CTL and b'"notice"' in pt]


# --- the setting ---------------------------------------------------------------------

def test_share_typing_defaults_off(monkeypatch):
    monkeypatch.delenv("CHELA_SHARE_TYPING", raising=False)
    assert config.share_typing_enabled() is False
    assert config.share_typing_setting() == (False, "default")


def test_setting_off_drops_input_even_for_a_verified_sandbox(monkeypatch, sandbox, typing_off):
    assert share_sandbox.check_share_session("@9") == (True, "")   # the sandbox IS fine
    b, _clock, forwarded, sent = _bridge(monkeypatch, allow_typing=True)
    _type(b, _joiner(b))
    assert forwarded == []
    assert len(_notices(sent)) == 1


# --- ⭐ the case that must be ACCEPTED -------------------------------------------------

def test_typing_on_share_allows_and_check_passes_forwards(monkeypatch, sandbox, typing_on):
    b, _clock, forwarded, sent = _bridge(monkeypatch, allow_typing=True)
    _type(b, _joiner(b), b"ls -la\r")
    assert forwarded == [b"ls -la\r"]
    assert _notices(sent) == []


def test_share_created_view_only_drops_input(monkeypatch, sandbox, typing_on):
    b, _clock, forwarded, sent = _bridge(monkeypatch)   # allow_typing defaults False
    _type(b, _joiner(b))
    assert forwarded == []
    assert b"does not allow typing" in _notices(sent)[0]


# --- fail closed on every failing check ---------------------------------------------

@pytest.mark.parametrize("breakage", [
    "shell_parent", "wrong_argv", "unreadable_proc", "inspect_error", "container_not_isolated",
])
def test_failing_check_drops_input(monkeypatch, sandbox, typing_on, breakage):
    if breakage == "shell_parent":
        sandbox["parent"] = "bash"
    elif breakage == "wrong_argv":
        sandbox["argv"] = ["/bin/bash", "-l"]
    elif breakage == "unreadable_proc":
        sandbox["proc_error"] = PermissionError("/proc/4242/cmdline")
    elif breakage == "inspect_error":
        sandbox["inspect"] = "docker is unreachable"
    else:
        info, net, _web = _good_container()
        net["Internal"] = False
        sandbox["inspect"] = (info, net, None)
    ok, why = share_sandbox.check_share_session("@9")
    assert ok is False and why
    b, _clock, forwarded, sent = _bridge(monkeypatch, allow_typing=True)
    _type(b, _joiner(b))
    assert forwarded == []
    assert b"could not verify" in _notices(sent)[0]


def test_process_swap_after_share_creation_stops_input_within_recheck(monkeypatch, sandbox, typing_on):
    b, clock, forwarded, _sent = _bridge(monkeypatch, allow_typing=True)
    j = _joiner(b)
    _type(b, j, b"a")
    assert forwarded == [b"a"]
    # The pane's process is swapped for a shell after the share was created.
    sandbox["argv"], sandbox["parent"] = ["/bin/bash"], "bash"
    clock.t += cs.SANDBOX_RECHECK_INTERVAL + 0.01
    _type(b, j, b"b")
    assert forwarded == [b"a"]            # the re-check caught it


def test_sandbox_verdict_is_rechecked_not_latched(monkeypatch, typing_on):
    calls = []
    monkeypatch.setattr(share_sandbox, "check_share_session",
                        lambda wid: calls.append(wid) or (True, ""))
    b, clock, forwarded, _sent = _bridge(monkeypatch, allow_typing=True)
    j = _joiner(b)
    _type(b, j, b"a")
    clock.t += cs.SANDBOX_RECHECK_INTERVAL
    _type(b, j, b"b")
    assert len(calls) == 2 and forwarded == [b"a", b"b"]


def test_view_only_notice_is_rate_limited(monkeypatch, typing_off):
    b, clock, forwarded, sent = _bridge(monkeypatch, allow_typing=True)
    j = _joiner(b)
    for _ in range(5):
        _type(b, j)
    assert forwarded == [] and len(_notices(sent)) == 1
    clock.t += cs.VIEW_ONLY_NOTICE_INTERVAL
    _type(b, j)
    assert len(_notices(sent)) == 2


# --- trusted-peer UNSANDBOXED override ----------------------------------------------

def _not_sandboxed(monkeypatch):
    monkeypatch.setattr(share_sandbox, "check_share_session",
                        lambda wid: (False, "the window is not running the sandboxed-session launcher"))


def _grant(b, ttl_s=1800.0):
    return b.grant_unsandboxed(granted_by="op@example", window="shell-3", ttl_s=ttl_s)


def _events(kind):
    return [e for e in event_log.read()["events"] if e["type"] == kind]


def test_override_forwards_paired_joiner_input_to_a_non_sandboxed_pane(monkeypatch, typing_on):
    """⭐ ACCEPTED: within the window, the paired joiner types into a real shell."""
    _not_sandboxed(monkeypatch)
    b, _clock, forwarded, _sent = _bridge(monkeypatch)
    _grant(b)
    _type(b, _joiner(b), b"whoami\r")
    assert forwarded == [b"whoami\r"]
    assert b.mode() == cs.MODE_UNSANDBOXED


def test_no_override_means_a_non_sandboxed_pane_stays_view_only(monkeypatch, typing_on):
    _not_sandboxed(monkeypatch)
    b, _clock, forwarded, _sent = _bridge(monkeypatch)
    _type(b, _joiner(b), b"whoami\r")
    assert forwarded == []


def test_override_expires_on_the_fake_clock(monkeypatch, typing_on):
    _not_sandboxed(monkeypatch)
    b, clock, forwarded, sent = _bridge(monkeypatch)
    _grant(b, ttl_s=60.0)
    j = _joiner(b)
    clock.t += 59.0
    _type(b, j, b"a")
    assert forwarded == [b"a"]
    clock.t += 1.0                        # the deadline
    _type(b, j, b"b")
    assert forwarded == [b"a"]
    assert b.mode() == cs.MODE_VIEW       # reverted on its own
    assert any(b"expired" in n for n in _notices(sent))
    assert len(_events("share.unsandboxed_expired")) == 1


def test_override_expiry_reverts_without_any_input(monkeypatch, typing_on):
    b, clock, _forwarded, _sent = _bridge(monkeypatch)
    _grant(b, ttl_s=60.0)
    clock.t += 61.0
    b._expire_override_if_due()           # what the control loop runs every tick
    assert b.state() == {"mode": cs.MODE_VIEW, "expires_at": None}


def test_second_joiner_stays_view_only(monkeypatch, typing_on):
    _not_sandboxed(monkeypatch)
    b, _clock, forwarded, sent = _bridge(monkeypatch)
    _grant(b)
    first, second = _joiner(b, b"\x01\x01\x01\x01"), _joiner(b, b"\x02\x02\x02\x02")
    first_hello = first.seal(cs.e2e.T_CTL, b'{"t":"hello"}')
    monkeypatch.setattr(cs, "_window_dims", lambda wid: (80, 24))
    b._handle_relay(first_hello)          # the paired joiner binds on hello
    _type(b, second, b"rm\r")
    _type(b, first, b"ok\r")
    assert forwarded == [b"ok\r"]
    assert any(b"one paired guest" in n for n in _notices(sent))


def test_setting_off_also_disables_the_override(monkeypatch, typing_off):
    _not_sandboxed(monkeypatch)
    b, _clock, forwarded, _sent = _bridge(monkeypatch)
    _grant(b)
    _type(b, _joiner(b))
    assert forwarded == []


def test_override_grant_and_revoke_are_audited(monkeypatch, typing_on):
    b, _clock, _f, _s = _bridge(monkeypatch)
    _grant(b, ttl_s=1800.0)
    g = _events("share.unsandboxed_granted")
    assert len(g) == 1
    p = g[0]["payload"]
    assert p["granted_by"] == "op@example" and p["window"] == "shell-3" and p["wid"] == "@9"
    assert p["expires_at"] - p["started_at"] == 1800.0
    b.stop()
    r = _events("share.unsandboxed_revoked")
    assert len(r) == 1 and r[0]["payload"]["reason"] == "share stopped"
    assert "revoked_at" in r[0]["payload"]


# --- app.py: the share route + the kill switch ---------------------------------------

@pytest.fixture
def share_app(monkeypatch):
    dash._SHARED.clear()
    dash._share_info.clear()
    monkeypatch.setattr(dash, "_terminals_port_map", lambda: {"@9": 5301})
    monkeypatch.setattr(dash, "_require_terminals", lambda: None)
    monkeypatch.setattr(dash.config, "COLLAB_RELAY", "wss://relay.example")
    monkeypatch.setattr(dash, "_window_name", lambda wid: "shell-3")
    monkeypatch.setattr(cs, "_window_dims", lambda wid: (80, 24))
    started = []

    def start(wid, on_revoke=None, share_epoch=None, **policy):
        started.append(policy)
        return "CODE"

    monkeypatch.setattr(dash.collab_stream, "start_bridge", start)
    monkeypatch.setattr(dash.collab_stream, "join_url", lambda wid: "https://relay/j/r")
    monkeypatch.setattr(dash.collab_stream, "stop_bridge", lambda wid: None)
    yield started
    dash._SHARED.clear()
    dash._share_info.clear()


def _post(body):
    return dash.app.test_client().post("/api/term/@9/share", json={"on": True, **body})


def test_override_without_typed_confirmation_is_not_granted(monkeypatch, share_app, typing_on):
    _not_sandboxed(monkeypatch)
    for body in ({"mode": "unsandboxed"}, {"mode": "unsandboxed", "confirm": "shell-4"}):
        r = _post(body)
        assert r.status_code == 403
    assert share_app == [] and "@9" not in dash._SHARED


def test_override_with_typed_confirmation_is_granted(monkeypatch, share_app, typing_on):
    _not_sandboxed(monkeypatch)
    r = _post({"mode": "unsandboxed", "confirm": "shell-3"})
    assert r.status_code == 200
    (policy,) = share_app
    assert policy["unsandboxed"]["window"] == "shell-3"
    assert policy["unsandboxed"]["ttl_s"] == config.share_unsandboxed_minutes() * 60.0


def test_override_is_not_offered_with_the_setting_off(monkeypatch, share_app, typing_off):
    _not_sandboxed(monkeypatch)
    r = _post({"mode": "unsandboxed", "confirm": "shell-3"})
    assert r.status_code == 403 and share_app == []


def test_typing_share_refused_on_a_non_sandboxed_window(monkeypatch, share_app, typing_on):
    _not_sandboxed(monkeypatch)
    r = _post({"mode": "typing"})
    assert r.status_code == 403
    assert r.get_json()["error"] == dash.NOT_SANDBOXED_REASON
    assert share_app == []


def test_typing_share_accepted_on_a_sandboxed_window(monkeypatch, share_app, sandbox, typing_on):
    r = _post({"mode": "typing"})
    assert r.status_code == 200
    assert share_app == [{"allow_typing": True}]


def test_default_share_is_view_only(share_app, typing_on):
    assert _post({}).status_code == 200
    assert share_app == [{}]


def test_kill_switch_revokes_the_override(monkeypatch, typing_on):
    """The #btn-shares Stop → POST share {on:false} → _revoke_share → stop_bridge →
    Bridge.stop, which ends the override (audited) — later input is dropped."""
    _not_sandboxed(monkeypatch)
    monkeypatch.setattr(dash, "_terminals_port_map", lambda: {"@9": 5301})
    monkeypatch.setattr(dash, "_require_terminals", lambda: None)
    b, _clock, forwarded, _sent = _bridge(monkeypatch)
    _grant(b)
    j = _joiner(b)
    _type(b, j, b"a")
    assert forwarded == [b"a"]
    cs._bridges["@9"] = b
    dash._SHARED["@9"] = {"cols": 80, "rows": 24}
    try:
        r = dash.app.test_client().post("/api/term/@9/share", json={"on": False})
        assert r.status_code == 200
    finally:
        cs._bridges.pop("@9", None)
        dash._SHARED.clear()
    _type(b, j, b"b")
    assert forwarded == [b"a"]
    assert b.mode() == cs.MODE_VIEW
    assert len(_events("share.unsandboxed_revoked")) == 1


def test_shared_report_carries_the_mode(monkeypatch, typing_on):
    monkeypatch.setattr(dash, "_require_terminals", lambda: None)
    b, _clock, _f, _s = _bridge(monkeypatch)
    _grant(b)
    cs._bridges["@9"] = b
    dash._SHARED["@9"] = {"cols": 80, "rows": 24}
    try:
        got = dash.app.test_client().get("/api/term/shared").get_json()
    finally:
        cs._bridges.pop("@9", None)
        dash._SHARED.clear()
    assert got["@9"]["mode"] == cs.MODE_UNSANDBOXED and got["@9"]["expires_at"]


def test_share_options_reasons(monkeypatch, typing_off):
    monkeypatch.setattr(dash, "_terminals_port_map", lambda: {"@9": 5301})
    monkeypatch.setattr(dash, "_require_terminals", lambda: None)
    monkeypatch.setattr(dash, "_window_name", lambda wid: "shell-3")
    _not_sandboxed(monkeypatch)
    o = dash.app.test_client().get("/api/term/@9/share-options").get_json()
    assert o["typing_allowed"] is False and o["typing_reason"] == dash.TYPING_OFF_REASON
    assert o["unsandboxed_offered"] is False
    monkeypatch.setenv("CHELA_SHARE_TYPING", "true")
    o = dash.app.test_client().get("/api/term/@9/share-options").get_json()
    assert o["typing_reason"] == dash.NOT_SANDBOXED_REASON and o["unsandboxed_offered"] is True


def test_config_api_round_trips_share_typing(monkeypatch):
    monkeypatch.delenv("CHELA_SHARE_TYPING", raising=False)
    c = dash.app.test_client()
    assert c.get("/api/config").get_json()["share_typing"] is False
    got = c.post("/api/config", json={"share_typing": True}).get_json()
    assert got["share_typing"] is True and got["share_typing_source"] == "dashboard"
    assert config.share_typing_enabled() is True
    assert c.post("/api/config", json={"share_typing": "maybe"}).status_code == 400


# --- the launcher --------------------------------------------------------------------

def test_spawn_sandbox_window_execs_the_launcher_with_no_shell(monkeypatch, tmp_path):
    from chela import spawn
    calls = []

    class P:
        returncode, stdout, stderr = 0, "@17\n", ""

    monkeypatch.setattr(share_sandbox, "preflight", lambda cwd, net="none": None)
    monkeypatch.setattr(spawn.discovery, "ensure_session", lambda: True)
    monkeypatch.setattr(spawn.discovery, "get_all_windows", lambda: ["sandbox-1"])
    monkeypatch.setattr(spawn.agent_manager, "lock_window_name", lambda t: None)
    monkeypatch.setattr(spawn, "_send", lambda *a: calls.append(("send",) + a))
    monkeypatch.setattr(spawn.subprocess, "run", lambda argv, **kw: calls.append(argv) or P())
    r = spawn.spawn_sandbox_window(str(tmp_path))
    assert r.ok and r.wid == "@17" and r.name == "sandbox-2"
    # one window-making tmux call, no send-keys at all (CMX-425's global-env secret scrub —
    # `show-environment -g` / `set-environment -gu` — runs first and is not a launch)
    (argv,) = [c for c in calls
               if not (isinstance(c, list) and c[:2] in (["tmux", "show-environment"],
                                                         ["tmux", "set-environment"]))]
    cmd = argv[argv.index("--") + 1:]
    assert cmd[:2] == [sys.executable, "-m"]
    assert share_sandbox.verify_pane(cmd, "tmux: server", []) == (cmd[5], str(tmp_path.resolve()), "none")


def test_spawn_sandbox_window_refuses_before_opening_anything(monkeypatch, tmp_path):
    from chela import spawn
    calls = []
    monkeypatch.setattr(share_sandbox, "preflight", lambda cwd, net="none": "docker is not installed")
    monkeypatch.setattr(spawn.subprocess, "run", lambda argv, **kw: calls.append(argv))
    r = spawn.spawn_sandbox_window(str(tmp_path))
    assert not r.ok and "docker" in r.error and calls == []


def test_cli_share_session_refuses_a_missing_project(tmp_path):
    import subprocess
    env = {k: v for k, v in os.environ.items() if not k.startswith("TMUX")}
    env.update(CHELA_DIR=str(tmp_path / ".chela"), CHELA_ENV_FILE="",
               CHELA_PROJECTS_DIR=str(tmp_path))
    p = subprocess.run([sys.executable, "-m", "chela.main", "share-session", "nope-not-here"],
                       capture_output=True, text=True, env=env, timeout=60)
    assert p.returncode == 1 and "no such project directory" in p.stderr


def test_spawn_sandboxed_route_uses_the_launcher(monkeypatch, tmp_path):
    from chela import spawn
    monkeypatch.setattr(dash, "_require_terminals", lambda: None)
    seen = []
    monkeypatch.setattr(spawn, "spawn_sandbox_window",
                        lambda cwd, net="none": seen.append(cwd) or spawn.SpawnResult(ok=True, name="sandbox-1", wid="@3", cwd=cwd))
    monkeypatch.setattr(dash.launcher, "record_recent", lambda p: None)
    r = dash.app.test_client().post("/api/agents/spawn-sandboxed", json={"cwd": str(tmp_path)})
    assert r.status_code == 200 and seen == [str(tmp_path)]


# --- every verify_container refusal, one field flipped at a time (judge round 1) ------
# Each breakage flips exactly ONE field of an otherwise-good inspect, so no other refusal
# can mask the one under test. The untouched fixture verifying (True, "") is the
# negative control that proves the table can pass at all.

def _flip_privileged(info, net):
    # CapAdd stays None and CapDrop stays ["ALL"], so ONLY Privileged can refuse this.
    info["HostConfig"]["Privileged"] = True


def _flip_rw_rootfs(info, net):
    info["HostConfig"]["ReadonlyRootfs"] = False


def _flip_no_nnp(info, net):
    info["HostConfig"]["SecurityOpt"] = []


def _flip_root_user(info, net):
    info["Config"]["User"] = "0:0"


def _flip_extra_network(info, net):
    # NetworkMode is still the isolated one — only the attached-network SET is wrong.
    info["NetworkSettings"]["Networks"]["bridge"] = {}


def _flip_no_inhibit(info, net):
    # Internal stays True — only the host-address inhibit is missing.
    net["Options"] = {}


def _flip_ssh_mount(info, net):
    info["Mounts"].append({"Type": "bind", "Source": os.path.expanduser("~/.ssh"),
                           "Destination": "/home/guest/.ssh", "RW": False})


@pytest.mark.parametrize("flip, why", [
    (_flip_privileged, "extra privileges"),
    (_flip_rw_rootfs, "root filesystem is writable"),
    (_flip_no_nnp, "privilege escalation"),
    (_flip_root_user, "does not run as the host user"),
    (_flip_extra_network, "not on its isolated network"),
    (_flip_no_inhibit, "can reach the host"),
    (_flip_ssh_mount, "unexpected mount"),
])
def test_each_container_refusal_fails_closed(monkeypatch, sandbox, typing_on, flip, why):
    assert share_sandbox.check_share_session("@9") == (True, "")   # negative control
    info, net, _web = _good_container()
    flip(info, net)
    sandbox["inspect"] = (info, net, None)
    ok, reason = share_sandbox.check_share_session("@9")
    assert ok is False and why in reason
    b, _clock, forwarded, _sent = _bridge(monkeypatch, allow_typing=True)
    _type(b, _joiner(b))
    assert forwarded == []


def test_a_non_docker_child_under_the_launcher_fails_closed(monkeypatch, sandbox, typing_on):
    assert share_sandbox.check_share_session("@9") == (True, "")   # negative control
    sandbox["kids"] = ["docker", "bash"]
    ok, reason = share_sandbox.check_share_session("@9")
    assert ok is False and "unexpected process" in reason
    b, _clock, forwarded, _sent = _bridge(monkeypatch, allow_typing=True)
    _type(b, _joiner(b))
    assert forwarded == []


def test_an_exception_from_the_check_fails_closed_in_the_gate(monkeypatch, typing_on):
    def boom(wid):
        raise RuntimeError("docker client exploded")

    monkeypatch.setattr(share_sandbox, "check_share_session", boom)
    b, _clock, forwarded, sent = _bridge(monkeypatch, allow_typing=True)
    _type(b, _joiner(b))
    assert forwarded == []
    assert b._sandbox_verdict[0] is False
    assert b"could not verify" in _notices(sent)[0]
    # negative control: the same bridge forwards once the check stops raising
    monkeypatch.setattr(share_sandbox, "check_share_session", lambda wid: (True, ""))
    b2, _c2, forwarded2, _s2 = _bridge(monkeypatch, allow_typing=True)
    _type(b2, _joiner(b2))
    assert forwarded2 == [b"ls\r"]


@pytest.mark.parametrize("raw, want", [("20160", 20160), ("20161", 20160), ("99999", 20160),
                                       ("10000", 10000), ("241", 241), ("240", 240),
                                       ("0", 1), ("-5", 1), ("45", 45), ("junk", None)])
def test_unsandboxed_minutes_is_clamped(monkeypatch, raw, want):
    monkeypatch.setenv("CHELA_SHARE_UNSANDBOXED_MINUTES", raw)
    got = config.share_unsandboxed_minutes()
    assert got == (config.SHARE_UNSANDBOXED_MINUTES_DEFAULT if want is None else want)


# --- start_bridge arms the REAL Bridge (route tests stub start_bridge) ----------------

@pytest.fixture
def real_bridges(monkeypatch):
    """The real start_bridge/Bridge, with only the pump threads stubbed out."""
    monkeypatch.setattr(config, "COLLAB_RELAY", "wss://relay.example")
    monkeypatch.setattr(cs.Bridge, "start", lambda self: self)
    monkeypatch.setattr(cs.Bridge, "_forward_input", lambda self, data: self.__dict__.setdefault("fwd", []).append(data))
    monkeypatch.setattr(cs.Bridge, "_seal_send", lambda self, typ, pt: None)
    cs._bridges.pop("@9", None)
    yield
    cs._bridges.pop("@9", None)


def test_start_bridge_carries_allow_typing_to_the_real_bridge(real_bridges, sandbox, typing_on):
    cs.start_bridge("@9", allow_typing=True)
    b = cs._bridges["@9"]
    assert b.allow_typing is True and b.mode() == cs.MODE_TYPING
    _type(b, _joiner(b))
    assert b.fwd == [b"ls\r"]
    cs._bridges.pop("@9")
    cs.start_bridge("@9")                       # negative control: the default is view only
    assert cs._bridges["@9"].mode() == cs.MODE_VIEW


def test_start_bridge_arms_the_unsandboxed_override(monkeypatch, real_bridges, typing_on):
    _not_sandboxed(monkeypatch)
    cs.start_bridge("@9", unsandboxed={"granted_by": "op@example", "window": "shell-3",
                                       "ttl_s": 1800.0})
    b = cs._bridges["@9"]
    assert b.mode() == cs.MODE_UNSANDBOXED
    _type(b, _joiner(b), b"whoami\r")
    assert b.fwd == [b"whoami\r"]
    assert len(_events("share.unsandboxed_granted")) == 1


def test_share_route_arms_the_real_bridge(monkeypatch, real_bridges, typing_on):
    """End to end through app.py with the REAL start_bridge: the typed confirmation
    reaches the Bridge as an armed override."""
    _not_sandboxed(monkeypatch)
    dash._SHARED.clear()
    dash._share_info.clear()
    monkeypatch.setattr(dash, "_terminals_port_map", lambda: {"@9": 5301})
    monkeypatch.setattr(dash, "_require_terminals", lambda: None)
    monkeypatch.setattr(dash, "_window_name", lambda wid: "shell-3")
    monkeypatch.setattr(cs, "_window_dims", lambda wid: (80, 24))
    try:
        assert _post({"mode": "unsandboxed", "confirm": "shell-3"}).status_code == 200
        assert cs._bridges["@9"].mode() == cs.MODE_UNSANDBOXED
    finally:
        dash._SHARED.clear()
        dash._share_info.clear()


# --- CMX-419: the operator picks the override's duration, up to 14 days -----------------

def test_unsandboxed_default_is_30_minutes_without_env(monkeypatch):
    monkeypatch.delenv("CHELA_SHARE_UNSANDBOXED_MINUTES", raising=False)
    assert config.share_unsandboxed_minutes() == 30


def test_default_override_with_no_picker_change_behaves_as_before(monkeypatch, share_app, typing_on):
    """⭐ ACCEPTED: no env, no ``minutes`` in the POST ⇒ the 30-minute default, granted on
    the single typed confirmation — no second one needed."""
    monkeypatch.delenv("CHELA_SHARE_UNSANDBOXED_MINUTES", raising=False)
    _not_sandboxed(monkeypatch)
    r = _post({"mode": "unsandboxed", "confirm": "shell-3"})
    assert r.status_code == 200
    (policy,) = share_app
    assert policy["unsandboxed"]["ttl_s"] == 30 * 60.0


def test_picked_duration_reaches_the_grant(monkeypatch, share_app, typing_on):
    _not_sandboxed(monkeypatch)
    r = _post({"mode": "unsandboxed", "confirm": "shell-3", "minutes": 20160,
               "confirm_long": "shell-3"})
    assert r.status_code == 200
    (policy,) = share_app
    assert policy["unsandboxed"]["ttl_s"] == 20160 * 60.0


def test_picked_duration_is_clamped_to_14_days(monkeypatch, share_app, typing_on):
    _not_sandboxed(monkeypatch)
    r = _post({"mode": "unsandboxed", "confirm": "shell-3", "minutes": 99999,
               "confirm_long": "shell-3"})
    assert r.status_code == 200
    assert share_app[0]["unsandboxed"]["ttl_s"] == 20160 * 60.0


@pytest.mark.parametrize("extra", [{}, {"confirm_long": ""}, {"confirm_long": "shell-4"}])
def test_long_override_without_second_confirmation_is_refused(monkeypatch, share_app, typing_on, extra):
    _not_sandboxed(monkeypatch)
    r = _post({"mode": "unsandboxed", "confirm": "shell-3", "minutes": 241, **extra})
    assert r.status_code == 403
    assert "longer than 4 h" in r.get_json()["error"]
    assert share_app == [] and "@9" not in dash._SHARED


def test_four_hours_needs_only_one_confirmation(monkeypatch, share_app, typing_on):
    """Negative control for the refusal above: exactly 4 h is not 'longer than 4 h'."""
    _not_sandboxed(monkeypatch)
    r = _post({"mode": "unsandboxed", "confirm": "shell-3", "minutes": 240})
    assert r.status_code == 200
    assert share_app[0]["unsandboxed"]["ttl_s"] == 240 * 60.0


def test_bad_duration_is_refused(monkeypatch, share_app, typing_on):
    _not_sandboxed(monkeypatch)
    r = _post({"mode": "unsandboxed", "confirm": "shell-3", "minutes": "soon"})
    assert r.status_code == 400 and share_app == []


def test_picked_duration_reaches_expires_at_in_the_audit_event(monkeypatch, real_bridges, typing_on):
    """End to end with the REAL start_bridge: the picker's 7 days lands in
    ``share.unsandboxed_granted``'s ``expires_at`` — not the 30-minute default."""
    _not_sandboxed(monkeypatch)
    dash._SHARED.clear()
    dash._share_info.clear()
    monkeypatch.setattr(dash, "_terminals_port_map", lambda: {"@9": 5301})
    monkeypatch.setattr(dash, "_require_terminals", lambda: None)
    monkeypatch.setattr(dash, "_window_name", lambda wid: "shell-3")
    monkeypatch.setattr(cs, "_window_dims", lambda wid: (80, 24))
    try:
        r = _post({"mode": "unsandboxed", "confirm": "shell-3", "minutes": 10080,
                   "confirm_long": "shell-3"})
        assert r.status_code == 200
        (g,) = _events("share.unsandboxed_granted")
        p = g["payload"]
        assert p["expires_at"] - p["started_at"] == 10080 * 60.0
        assert r.get_json()["expires_at"] == p["expires_at"]
    finally:
        dash._SHARED.clear()
        dash._share_info.clear()


def test_a_14_day_override_still_expires_on_the_fake_clock(monkeypatch, typing_on):
    _not_sandboxed(monkeypatch)
    b, clock, forwarded, _sent = _bridge(monkeypatch)
    _grant(b, ttl_s=20160 * 60.0)
    j = _joiner(b)
    clock.t += 20160 * 60.0 - 1.0
    _type(b, j, b"a")
    assert forwarded == [b"a"]
    clock.t += 1.0
    _type(b, j, b"b")
    assert forwarded == [b"a"]
    assert b.mode() == cs.MODE_VIEW
    assert len(_events("share.unsandboxed_expired")) == 1


def test_share_options_offer_the_duration_picker(monkeypatch, share_app, typing_on):
    _not_sandboxed(monkeypatch)
    monkeypatch.setenv("CHELA_SHARE_UNSANDBOXED_MINUTES", "45")
    got = dash._share_options("@9")
    assert got["unsandboxed_minutes"] == 45
    assert got["unsandboxed_choices"] == [30, 45, 240, 1440, 10080, 20160]
    assert got["unsandboxed_long_minutes"] == 240


# --- 🕶️ CMX-416: a typing share's OUTPUT is gated on the same live check -------------
#
# Driven through the REAL ttyd pump (``Bridge._pump_ttyd_to_relay``) with a fake ttyd
# socket that yields a scripted list of frames; a callable in the script runs between
# frames (the swap). Nothing real is opened: the port map, the window size and the
# websocket client are stubbed, and every relay send is captured.

class _FakeTtyd:
    def __init__(self, bridge, script):
        self.bridge, self.script = bridge, list(script)

    def send(self, data):
        pass

    def close(self):
        pass

    def receive(self, timeout=None):
        while self.script:
            item = self.script.pop(0)
            if callable(item):
                item()
                continue
            return item
        self.bridge._stop.set()
        raise ConnectionError("script exhausted")


def _pump(monkeypatch, script, **kw):
    """Run the real ttyd→relay pump over ``script``; returns (output payloads, CTL
    payloads, revoked wids, bridge)."""
    import simple_websocket
    b, clock, _fwd, sent = _bridge(monkeypatch, **kw)
    revoked = []
    b._on_revoke = revoked.append
    monkeypatch.setattr(cs, "_port_map", lambda: {"@9": 7681})
    monkeypatch.setattr(cs, "_window_dims", lambda wid: (80, 24))
    monkeypatch.setattr(cs, "RECONNECT_DELAY", 0)
    monkeypatch.setattr(simple_websocket, "Client", lambda *a, **k: _FakeTtyd(b, script))
    b._pump_ttyd_to_relay()
    out = [pt for typ, pt in sent if typ == cs.e2e.T_OUTPUT]
    ctl = [pt for typ, pt in sent if typ == cs.e2e.T_CTL]
    return out, ctl, revoked, b, clock


def _frame(text):
    return b"0" + text.encode()


def _swap_to_host_shell(sandbox):
    def swap():
        sandbox["argv"], sandbox["parent"], sandbox["kids"] = ["/bin/bash"], "tmux: server", []
    return swap


def test_typing_share_streams_normally_while_the_check_passes(monkeypatch, sandbox, typing_on):
    """⭐ The case that must be ACCEPTED."""
    out, ctl, revoked, b, _clock = _pump(
        monkeypatch, [_frame("one"), _frame("two"), _frame("three")], allow_typing=True)
    assert out == [b"one", b"two", b"three"]
    assert not any(b'"ended"' in p for p in ctl) and revoked == []
    assert b._sandbox_lost is None


def test_process_swap_stops_output_before_the_next_frame(monkeypatch, sandbox, typing_on):
    out, ctl, revoked, b, _clock = _pump(monkeypatch, [
        _frame("sandbox$ "), _swap_to_host_shell(sandbox),
        _frame("op@example.com eu-west-1 $ "), _frame("more host output"),
    ], allow_typing=True)
    assert out == [b"sandbox$ "]        # nothing produced after the swap reached the guest
    ended = [p for p in ctl if b'"ended"' in p]
    assert ended and cs.SANDBOX_LOST_REASON.encode() in ended[0]
    assert revoked == ["@9"]            # the share itself ends
    assert len(_events("share.sandbox_lost")) == 1


def test_a_different_launcher_in_the_pane_also_stops_output(monkeypatch, sandbox, typing_on):
    """The swap is caught by the pane's IDENTITY, not only its shape: a new, equally
    well-formed launcher (new pane pid) is still not the session the share verified."""
    def respawn():
        monkeypatch.setattr(share_sandbox, "_pane_root", lambda wid: 5151)
    out, _ctl, revoked, _b, _clock = _pump(
        monkeypatch, [_frame("a"), respawn, _frame("b")], allow_typing=True)
    assert out == [b"a"] and revoked == ["@9"]


@pytest.mark.parametrize("unknown", ["unreadable_proc", "exception"])
def test_an_unknown_check_stops_output_too(monkeypatch, sandbox, typing_on, unknown):
    def flip():
        if unknown == "unreadable_proc":
            sandbox["proc_error"] = PermissionError("/proc/4242/cmdline")
        else:
            def boom(wid):
                raise RuntimeError("tmux exploded")
            monkeypatch.setattr(share_sandbox, "pane_identity", boom)
    out, _ctl, revoked, b, _clock = _pump(
        monkeypatch, [_frame("a"), flip, _frame("b"), _frame("c")], allow_typing=True)
    assert out == [b"a"] and revoked == ["@9"]
    assert b._sandbox_lost


@pytest.mark.parametrize("bad_pane", ["host_shell", "exception"])
def test_the_per_frame_pane_read_refuses_on_its_own(monkeypatch, sandbox, typing_on, bad_pane):
    """The very first frame, before any identity is bound, with the (cached, slower) full
    check still saying OK: the per-frame pane read alone must refuse — a bad shape and an
    exception (UNKNOWN) both count as failed."""
    monkeypatch.setattr(share_sandbox, "check_share_session", lambda wid: (True, ""))
    if bad_pane == "host_shell":
        sandbox["argv"], sandbox["parent"], sandbox["kids"] = ["/bin/bash"], "tmux: server", []
    else:
        def boom(wid):
            raise RuntimeError("tmux exploded")
        monkeypatch.setattr(share_sandbox, "pane_identity", boom)
    out, _ctl, revoked, _b, _clock = _pump(
        monkeypatch, [_frame("op@example.com $ ")], allow_typing=True)
    assert out == [] and revoked == ["@9"]


def test_a_failed_container_recheck_stops_output(monkeypatch, sandbox, typing_on):
    clock_box = {}

    def container_gone():
        sandbox["inspect"] = "the sandbox container is not running"
        clock_box["b"]._clock.t += cs.SANDBOX_RECHECK_INTERVAL + 0.01

    real_bridge = cs.Bridge

    def capture(*a, **k):
        b = real_bridge(*a, **k)
        clock_box["b"] = b
        return b
    monkeypatch.setattr(cs, "Bridge", capture)
    out, _ctl, revoked, _b, _clock = _pump(
        monkeypatch, [_frame("a"), container_gone, _frame("b")], allow_typing=True)
    assert out == [b"a"] and revoked == ["@9"]


def test_input_is_refused_once_the_sandbox_is_lost(monkeypatch, sandbox, typing_on):
    out, _ctl, _revoked, b, _clock = _pump(
        monkeypatch, [_frame("a"), _swap_to_host_shell(sandbox), _frame("b")], allow_typing=True)
    sandbox["argv"], sandbox["parent"], sandbox["kids"] = _good_argv(), "tmux: server", ["docker"]
    assert b._input_refusal(b"\x00" * cs.e2e.STREAM_ID_LEN) is not None


def test_view_only_share_of_a_normal_window_streams_unchanged(monkeypatch, typing_on):
    _not_sandboxed(monkeypatch)

    def never(wid):
        raise AssertionError("a view-only share must not run the sandbox check")
    monkeypatch.setattr(share_sandbox, "pane_identity", never)
    out, ctl, revoked, _b, _clock = _pump(
        monkeypatch, [_frame("plain shell $ "), _frame("ls")])   # allow_typing defaults False
    assert out == [b"plain shell $ ", b"ls"]
    assert not any(b'"ended"' in p for p in ctl) and revoked == []


def test_an_idle_typing_share_ends_on_a_swap_before_any_frame(monkeypatch, sandbox, typing_on):
    """The IDLE path: ttyd's receive times out (``None``) right after the swap, before the
    new process prints. The idle tick alone must end the share — the pump must not read
    on and wait for the next frame to be refused (that next frame is the leak window).
    Corrupt the idle branch's re-check (``why = None``) ⇒ the pump reads past the tick."""
    read_past_idle = []
    out, ctl, revoked, b, _clock = _pump(monkeypatch, [
        _frame("sandbox$ "), _swap_to_host_shell(sandbox), None,
        lambda: read_past_idle.append(True), _frame("op@example.com eu-west-1 $ "),
    ], allow_typing=True)
    assert read_past_idle == []         # ended AT the idle tick, nothing read after it
    assert out == [b"sandbox$ "] and revoked == ["@9"]
    ended = [p for p in ctl if b'"ended"' in p]
    assert ended and cs.SANDBOX_LOST_REASON.encode() in ended[0]
    assert b._sandbox_lost == "the window is not running the sandboxed-session launcher"


def test_an_idle_tick_keeps_a_verified_typing_share_streaming(monkeypatch, sandbox, typing_on):
    """⭐ Positive control for the idle path: idle ticks over a still-verified pane end
    nothing, and the frames around them stream."""
    out, ctl, revoked, _b, _clock = _pump(
        monkeypatch, [_frame("a"), None, None, _frame("b")], allow_typing=True)
    assert out == [b"a", b"b"] and revoked == []
    assert not any(b'"ended"' in p for p in ctl)
