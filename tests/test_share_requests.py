"""🙋 CMX-7: a sandboxed guest REQUESTS more access; the operator APPROVES it.

The guards, each with a negative control:

* the deny-list: an APPROVED request for ``~/.ssh`` (or ``/mnt/c``, ``$HOME``, a symlink
  into them) is still refused — at approval, at launch and by the live sandbox check;
* an unapproved request changes nothing;
* an approval expires on time, and the launcher relaunches without it;
* the kill switch (stop the share / Guest typing off) revokes approvals;
* ⭐ the case that must be ACCEPTED: an approved read-only mount of an allowed path is in
  the relaunched container, and only read-only;
* the guest can only FILE a request — the proxy has no route that reads one back, and the
  store is never mounted;
* only a human approves — chela's merge-gate hook denies it to a Claude session.

No docker is run: every docker call is stubbed, as in ``test_share_web.py``.
"""
from __future__ import annotations

import http.client
import json
import os
import threading
from http.server import ThreadingHTTPServer

import pytest

from chela import config, event_log, mergegate, share_proxy, share_requests as sr, userconfig
from chela import share_sandbox as sb

UID, GID = os.getuid(), os.getgid()
SID = "0123456789ab"
T0 = 1_800_000_000.0


@pytest.fixture
def typing_on():
    userconfig.set_(config.SHARE_TYPING_KEY, True)
    assert config.share_typing_enabled()


def _file(sid: str, **req) -> None:
    """What the proxy's RequestDrop writes for one guest request."""
    d = sb.session_dir(sid)
    d.mkdir(parents=True, exist_ok=True)
    rec = {"kind": "mount", "target": "/nowhere", "access": "ro", "reason": "need it", "ts": T0}
    rec.update(req)
    with open(d / sr.REQUESTS_NAME, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec) + "\n")


def _filed(sid=SID, **req) -> dict:
    _file(sid, **req)
    new = sr.ingest(sid, now=T0)
    assert len(new) == 1
    return new[0]


def _events(kind: str) -> list[dict]:
    return [e for e in event_log.read()["events"] if e["type"] == kind]


@pytest.fixture
def allowed(tmp_path):
    d = tmp_path / "datasets"
    d.mkdir()
    (d / "data.csv").write_text("a,b\n")
    return os.path.realpath(d)


# =====================================================================================
# the deny-list
# =====================================================================================

HOME = os.path.realpath(os.path.expanduser("~"))
REFUSED = ["~/.ssh", "~/.ssh/id_ed25519", "~/.claude", "~/.chela", "~/.config", "~/.secrets",
           "~", HOME, os.path.dirname(HOME), "/", "/mnt/c", "/mnt/c/Users", "/mnt",
           "/var/run/docker.sock", "/run", "/proc", "/etc", "/tmp"]


@pytest.mark.parametrize("path", REFUSED)
def test_secrets_home_and_mnt_are_refused(path):
    why = sr.mount_refusal(path)
    assert why and "no such" not in why, why


@pytest.mark.parametrize("path", ["~", HOME, os.path.dirname(HOME)])
def test_home_and_its_ancestors_are_refused_as_home(path):
    """Named by its OWN rule, not merely caught because $HOME happens to contain ~/.ssh —
    a home without any secrets directory in it must still never be mounted."""
    assert "home directory" in (sr.mount_refusal(path) or "")


@pytest.mark.parametrize("target", [os.path.expanduser("~/.ssh"), "/mnt/c"])
def test_a_symlink_into_a_refused_path_is_refused(tmp_path, target):
    link = tmp_path / "innocent"
    link.symlink_to(target)
    why = sr.mount_refusal(str(link))
    assert why and "no such" not in why, why


def test_chela_dir_and_the_request_store_are_refused():
    assert sr.mount_refusal(str(config.CHELA_DIR))
    assert sr.mount_refusal(str(sr.store_path()))
    assert sr.mount_refusal(str(sb.session_dir(SID)))


def test_an_ordinary_directory_is_allowed(allowed):
    # The negative control: without it, "refuse everything" would pass the table above.
    assert sr.mount_refusal(allowed) is None
    assert sr.mount_refusal(os.path.join(allowed, "data.csv")) is None


@pytest.mark.parametrize("bad", ["relative/path", "/a:/b", "/a,b"])
def test_a_path_a_mount_cannot_carry_is_refused(bad):
    assert sr.mount_refusal(bad)


def test_approving_a_secrets_dir_is_refused_and_audited(typing_on):
    rec = _filed(target="~/.ssh")
    ok, why = sr.approve(rec["id"], by="op", now=T0)
    assert not ok and "secrets" in why
    assert sr.active_grants(SID, T0) == []
    assert _events("share.request_refused")


def test_a_record_forced_to_approved_for_ssh_still_mounts_nothing(typing_on, allowed):
    """Even a store that says "approved" (hand-edited, or a bug upstream) can't mount a
    refused path: the launcher and the live check re-apply the deny-list."""
    good = _filed(target=allowed)
    assert sr.approve(good["id"], by="op", now=T0)[0]
    bad = _filed(target="~/.ssh")
    with sr._locked() as store:
        store["requests"][bad["id"]].update(status=sr.APPROVED, path=os.path.expanduser("~/.ssh"),
                                            expires_at=T0 + 3600, rw=True)
    specs = sr.mount_specs(SID, T0 + 1)
    assert [s[0] for s in specs] == [allowed]
    argv = sb.guest_run_argv(SID, "/tmp", UID, GID, "/usr/bin/true", mounts=specs)
    assert not any(".ssh" in a for a in argv)


def test_a_symlink_swapped_after_approval_mounts_nothing(typing_on, tmp_path, allowed):
    link = tmp_path / "data"
    link.symlink_to(allowed)
    rec = _filed(target=str(link))
    assert sr.approve(rec["id"], by="op", now=T0)[0]
    assert [s[0] for s in sr.mount_specs(SID, T0 + 1)] == [allowed]
    # The grant pinned the resolved path. Re-pointing the symlink can't redirect it…
    link.unlink()
    link.symlink_to(os.path.expanduser("~/.ssh"))
    assert [s[0] for s in sr.mount_specs(SID, T0 + 1)] == [allowed]


# =====================================================================================
# an unapproved request changes nothing
# =====================================================================================

def test_an_unapproved_request_changes_nothing(typing_on, allowed, monkeypatch):
    rec = _filed(target=allowed)
    assert rec["status"] == sr.PENDING
    assert sr.active_grants(SID, T0) == []
    assert sr.mount_specs(SID, T0) == []
    removed = []
    monkeypatch.setattr(sb, "_docker", lambda *a, **k: removed.append(a))
    w = sb.GrantWatch(SID, "none")
    w.launched = w.spec(T0)
    assert w.check(T0) is False and removed == []
    argv = sb.guest_run_argv(SID, "/tmp", UID, GID, "/usr/bin/true", mounts=sr.mount_specs(SID, T0))
    assert not any(":" + sr.EXTRA_ROOT + "/" in a for a in argv)


def test_a_denied_request_changes_nothing(typing_on, allowed):
    rec = _filed(target=allowed)
    assert sr.deny(rec["id"], by="op", now=T0)[0]
    assert sr.mount_specs(SID, T0) == []
    assert sr.approve(rec["id"], by="op", now=T0)[0] is False   # decided once
    assert _events("share.request_denied")


# =====================================================================================
# expiry
# =====================================================================================

def test_an_approval_lasts_60_minutes_by_default_and_then_ends(typing_on, allowed):
    rec = _filed(target=allowed)
    assert sr.approve(rec["id"], by="op", now=T0)[0]
    assert sr.mount_specs(SID, T0 + 3599)
    assert sr.mount_specs(SID, T0 + 3600) == []
    assert sr.mount_specs(SID, T0 + 3601) == []


def test_a_chosen_duration_is_honoured_and_clamped(typing_on, allowed):
    a = _filed(target=allowed)
    assert sr.approve(a["id"], by="op", minutes=5, now=T0)[0]
    assert sr.mount_specs(SID, T0 + 299) and not sr.mount_specs(SID, T0 + 301)
    assert sr.MAX_MINUTES == 1440 and sr.DEFAULT_MINUTES == 60
    assert sr.clamp_minutes(10 ** 9) == sr.MAX_MINUTES and sr.clamp_minutes(0) == 1


def test_expiry_relaunches_the_guest_without_the_mount(typing_on, allowed, monkeypatch):
    rec = _filed(target=allowed)
    assert sr.approve(rec["id"], by="op", now=T0)[0]
    removed = []
    monkeypatch.setattr(sb, "_docker", lambda *a, **k: removed.append(a))
    w = sb.GrantWatch(SID, "none")
    w.launched = w.spec(T0 + 1)
    assert w.launched[0]
    assert w.check(T0 + 60) is False
    assert w.check(T0 + 3601) is True
    assert removed == [("rm", "-f", sb.container_name(SID))]
    assert w.take_relaunch() and w.spec(T0 + 3601) == ((), ())
    assert [e["payload"]["id"] for e in _events("share.request_expired")] == [rec["id"]]


# =====================================================================================
# the kill switch
# =====================================================================================

def test_stopping_the_share_revokes_the_sessions_approvals(typing_on, allowed, monkeypatch):
    from chela.dashboard import app as dash
    rec = _filed(target=allowed)
    other = _filed(sid="ba9876543210", target=allowed)
    assert sr.approve(rec["id"], by="op", now=T0)[0]
    assert sr.approve(other["id"], by="op", now=T0)[0]
    monkeypatch.setattr(dash, "_terminals_port_map", lambda: {"@9": 5301})
    monkeypatch.setattr(dash, "_require_terminals", lambda: None)
    monkeypatch.setattr(dash.collab_host, "stop_bridge", lambda wid: None)
    monkeypatch.setattr(dash.share_sandbox, "pane_identity", lambda wid: (4242, SID, allowed, "none"))
    r = dash.app.test_client().post("/api/term/@9/share", json={"on": False})
    assert r.status_code == 200
    assert sr.active_grants(SID, T0 + 1) == []
    assert sr.active_grants("ba9876543210", T0 + 1), "another session's approval is untouched"
    assert _events("share.request_revoked")


def test_turning_guest_typing_off_revokes_every_approval(typing_on, allowed):
    from chela.dashboard import app as dash
    rec = _filed(target=allowed)
    assert sr.approve(rec["id"], by="op", now=T0)[0]
    c = dash.app.test_client()
    assert c.post("/api/config", json={"share_typing": False}).status_code == 200
    assert sr.active_grants(SID, T0 + 1) == []


def test_the_launcher_revokes_when_guest_typing_is_off(typing_on, allowed, monkeypatch):
    """Typing turned off any other way (env, another process) is caught by the launcher."""
    rec = _filed(target=allowed)
    assert sr.approve(rec["id"], by="op", now=T0)[0]
    monkeypatch.setattr(sb, "_docker", lambda *a, **k: None)
    w = sb.GrantWatch(SID, "none")
    w.launched = w.spec(T0 + 1)
    assert w.check(T0 + 2) is False                         # control: typing on, nothing moves
    userconfig.set_(config.SHARE_TYPING_KEY, False)
    assert w.check(T0 + 3) is True
    assert sr.active_grants(SID, T0 + 3) == []


def test_approving_needs_guest_typing_on(allowed):
    rec = _filed(target=allowed)
    ok, why = sr.approve(rec["id"], by="op", now=T0)
    assert not ok and "Guest typing" in why


# =====================================================================================
# ⭐ the ACCEPT case: an approved read-only mount, and only read-only
# =====================================================================================

def test_an_approved_mount_is_read_only_in_the_relaunched_container(typing_on, allowed):
    rec = _filed(target=allowed)
    assert sr.approve(rec["id"], by="op", now=T0)[0]
    specs = sr.mount_specs(SID, T0 + 1)
    assert specs == [(allowed, "/extra/datasets", False)]
    argv = sb.guest_run_argv(SID, "/tmp", UID, GID, "/usr/bin/true", mounts=specs)
    assert f"{allowed}:/extra/datasets:ro" in argv
    assert f"{allowed}:/extra/datasets" not in argv


def test_an_approved_directory_keeps_the_workspace_hygiene(typing_on, allowed):
    """``.env*`` masked in every extra directory; in a WRITABLE one, the entries an
    operator's own tools would run (``.git`` hooks…) stay read-only."""
    os.mkdir(os.path.join(allowed, ".git"))
    open(os.path.join(allowed, ".env"), "w").close()
    rec = _filed(target=allowed, access="rw")
    assert sr.approve(rec["id"], by="op", rw=True, now=T0)[0]
    specs = sr.mount_specs(SID, T0 + 1)
    assert specs == [(allowed, "/extra/datasets", True)]
    argv = sb.guest_run_argv(SID, "/tmp", UID, GID, "/usr/bin/true", mounts=specs)
    assert f"{allowed}:/extra/datasets" in argv
    assert "/dev/null:/extra/datasets/.env:ro" in argv
    assert f"{allowed}/.git:/extra/datasets/.git:ro" in argv


def test_write_needs_the_guest_to_ask_and_the_operator_to_allow(typing_on, allowed):
    ro = _filed(target=allowed, access="ro")
    ok, why = sr.approve(ro["id"], by="op", rw=True, now=T0)
    assert not ok and "read-only" in why
    rw = _filed(target=allowed, access="rw")
    assert sr.approve(rw["id"], by="op", now=T0)[0]          # write asked, not allowed
    assert sr.mount_specs(SID, T0 + 1) == [(allowed, "/extra/datasets", False)]


def _guest(extra_mounts=()):
    net = sb.network_name(SID)
    return {
        "State": {"Running": True},
        "Config": {"Labels": {sb.LABEL: SID, sb.NET_LABEL: "none"}, "User": f"{UID}:{GID}",
                   "Env": ["HOME=/home/guest"]},
        "HostConfig": {"CapDrop": ["ALL"], "CapAdd": None, "Privileged": False,
                       "ReadonlyRootfs": True, "SecurityOpt": ["no-new-privileges"],
                       "Memory": 2 << 30, "PidsLimit": 512, "NetworkMode": net},
        "NetworkSettings": {"Networks": {net: {}}},
        "Mounts": [{"Type": "bind", "Source": os.path.realpath("/tmp"),
                    "Destination": sb.GUEST_WORKDIR, "RW": True},
                   {"Type": "bind", "Source": "/usr/bin/true", "Destination": sb.CLAUDE_MOUNT,
                    "RW": False}, *extra_mounts],
    }


def _network():
    return {"Internal": True, "Options": {"com.docker.network.bridge.inhibit_ipv4": "true"},
            "Containers": {"a": {"Name": sb.container_name(SID)}, "b": {"Name": sb.proxy_name(SID)}}}


def _mount(src, dst, rw):
    return {"Type": "bind", "Source": src, "Destination": dst, "RW": rw}


def test_the_live_check_accepts_the_granted_mount_read_only_and_nothing_more(allowed):
    grant = [(allowed, "/extra/datasets", False)]
    v = lambda info, extra: sb.verify_container(info, _network(), SID, "/tmp", UID, GID, extra=extra)  # noqa: E731
    assert v(_guest(), grant) is None                        # not mounted yet: fine
    assert v(_guest([_mount(allowed, "/extra/datasets", False)]), grant) is None
    assert v(_guest([_mount(allowed, "/extra/datasets", True)]), grant)       # widened to rw
    assert v(_guest([_mount(allowed, "/extra/datasets", False)]), [])         # not granted
    assert v(_guest([_mount(os.path.expanduser("~/.ssh"), "/extra/datasets", False)]), grant)
    assert v(_guest([_mount("/dev/null", "/extra/datasets/.env", False)]), grant) is None


def test_check_share_session_reads_the_grants_from_the_store(typing_on, allowed, monkeypatch):
    rec = _filed(target=allowed)
    info = _guest([_mount(allowed, "/extra/datasets", False)])
    monkeypatch.setattr(sb, "_pane_root", lambda wid: 4242)
    monkeypatch.setattr(sb, "_proc_shape", lambda pid: (sb.launcher_argv(SID, "/tmp"), "tmux: server", ["docker"]))
    monkeypatch.setattr(sb, "_inspect", lambda sid: (info, _network(), None))
    assert sb.check_share_session("@9")[0] is False          # mounted but never approved
    assert sr.approve(rec["id"], by="op")[0]
    assert sb.check_share_session("@9") == (True, "")
    sr.revoke(rec["id"], by="op")
    assert sb.check_share_session("@9")[0] is False          # revoked: the mount fails it


def test_the_launcher_relaunches_with_the_approved_mount(typing_on, allowed, monkeypatch, tmp_path):
    """End to end through ``run``: the guest is up, the operator approves, the watcher
    removes the container, and the NEXT ``docker run`` carries the mount read-only."""
    monkeypatch.setattr(sb, "preflight", lambda cwd, net="none": None)
    (tmp_path / "tok").write_text("t")
    monkeypatch.setenv("CHELA_SHARE_SANDBOX_TOKEN_FILE", str(tmp_path / "tok"))
    monkeypatch.setattr(sb, "claude_binary", lambda: "/usr/bin/true")
    monkeypatch.setattr(sb.signal, "signal", lambda *a: None)
    monkeypatch.setattr(sb, "cleanup", lambda sid: None)
    monkeypatch.setattr(sb, "_docker", lambda *a, **k: None)
    watches = []

    class Watch(sb.GrantWatch):
        def __init__(self, *a):
            super().__init__(*a)
            watches.append(self)

        def start(self, interval=None):
            pass

    monkeypatch.setattr(sb, "GrantWatch", Watch)

    class P:
        returncode, stdout, stderr = 0, "", ""

    monkeypatch.setattr(sb.subprocess, "run", lambda argv, **k: P())
    guest = []

    def call(argv):
        guest.append(argv)
        if len(guest) == 1:
            _file(SID, target=allowed)
            rid, = [r["id"] for r in sr.ingest(SID)]
            assert sr.approve(rid, by="op")[0]
            assert watches[0].check() is True
        return 0

    monkeypatch.setattr(sb.subprocess, "call", call)
    assert sb.run(SID, "/tmp", "none") == 0
    assert len(guest) == 2
    assert not any(":" + sr.EXTRA_ROOT + "/" in a for a in guest[0])
    assert f"{allowed}:/extra/datasets:ro" in guest[1]


# =====================================================================================
# domains and operations
# =====================================================================================

def test_a_domain_approval_extends_only_an_operator_allow_list(typing_on, monkeypatch):
    with sr._locked() as store:
        store["sessions"][SID] = {"net": "web"}
    rec = _filed(kind="domain", target="Jobs.Example.com")
    assert sr.approve(rec["id"], by="op", now=T0)[0]
    assert sr.approved_domains(SID, T0 + 1) == ["jobs.example.com"]
    monkeypatch.delenv("CHELA_SHARE_WEB_ALLOW", raising=False)
    assert sb.web_allow(["jobs.example.com"]) == ""         # no list: every host is allowed
    monkeypatch.setenv("CHELA_SHARE_WEB_ALLOW", "docs.python.org")
    assert sb.web_allow(["jobs.example.com"]) == "docs.python.org,jobs.example.com"
    argv = sb.web_proxy_run_argv(SID, UID, GID, ["jobs.example.com"])
    assert "CHELA_WEB_ALLOW=docs.python.org,jobs.example.com" in argv


def test_domain_requests_are_refused_on_a_none_session_and_against_the_deny_list(typing_on, monkeypatch):
    with sr._locked() as store:
        store["sessions"][SID] = {"net": "none"}
    rec = _filed(kind="domain", target="example.com")
    ok, why = sr.approve(rec["id"], by="op", now=T0)
    assert not ok and "no web access" in why
    monkeypatch.setenv("CHELA_SHARE_WEB_DENY", "evil.example")
    assert sr.domain_refusal("www.evil.example")
    assert sr.domain_refusal("10.0.0.1") and sr.domain_refusal("http://x.com/")


def test_an_operation_is_recorded_only(typing_on):
    rec = _filed(kind="operation", target="push my branch")
    assert sr.approve(rec["id"], by="op", now=T0)[0]
    assert sr.mount_specs(SID, T0 + 1) == [] and sr.approved_domains(SID, T0 + 1) == []
    assert _events("share.request_approved")


# =====================================================================================
# ingest: audit, notify once, malformed lines, the cap
# =====================================================================================

def test_ingest_audits_and_notifies_each_request_once(monkeypatch):
    sent = []
    from chela import notify
    monkeypatch.setattr(notify, "enabled", lambda: True)
    monkeypatch.setattr(notify, "send", lambda msg, title=None: sent.append(msg) or True)
    _file(SID, target="/srv/x", reason="for the report")
    assert len(sr.ingest(SID)) == 1
    assert sr.ingest(SID) == []
    assert len(sent) == 1 and "/srv/x" in sent[0]
    ev, = _events("share.request_filed")
    assert ev["payload"]["reason"] == "for the report"


def test_malformed_lines_are_skipped_and_the_cap_holds(monkeypatch):
    d = sb.session_dir(SID)
    d.mkdir(parents=True)
    (d / sr.REQUESTS_NAME).write_text("not json\n" + json.dumps({"kind": "root"}) + "\n")
    assert sr.ingest(SID) == []
    assert sr.MAX_PER_SESSION == 50
    for i in range(sr.MAX_PER_SESSION + 5):
        _file(SID, target=f"/srv/{i}")
    assert len(sr.ingest(SID)) == sr.MAX_PER_SESSION


# =====================================================================================
# the guest can only FILE a request
# =====================================================================================

@pytest.fixture
def proxy(tmp_path):
    share_proxy._Handler.requests = share_proxy.RequestDrop(str(tmp_path))
    srv = ThreadingHTTPServer(("127.0.0.1", 0), share_proxy._Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield srv.server_address[1], tmp_path
    srv.shutdown()
    share_proxy._Handler.requests = None


def _req(port, method, path, body=None):
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    c.request(method, path, body=json.dumps(body).encode() if body is not None else None,
              headers={"content-type": "application/json"})
    r = c.getresponse()
    return r.status, r.read()


def test_the_proxy_files_a_request_and_never_reads_one_back(proxy):
    port, d = proxy
    st, body = _req(port, "POST", share_proxy.REQUEST_PATH,
                    {"kind": "mount", "target": "/srv/data", "reason": "dataset", "extra": "x"})
    assert st == 202 and json.loads(body)["filed"] is True
    line, = (d / share_proxy.REQUESTS_NAME).read_text().splitlines()
    assert set(json.loads(line)) == {"kind", "target", "access", "reason", "ts"}
    assert _req(port, "GET", share_proxy.REQUEST_PATH)[0] == 403
    assert _req(port, "POST", share_proxy.REQUEST_PATH, {"kind": "sudo", "target": "x"})[0] == 400


def test_the_drop_refuses_past_its_size_cap(tmp_path, monkeypatch):
    monkeypatch.setattr(share_proxy, "REQUESTS_MAX_BYTES", 10)
    drop = share_proxy.RequestDrop(str(tmp_path))
    assert drop.file({"kind": "mount", "target": "/a"}) is True
    assert drop.file({"kind": "mount", "target": "/b"}) is False


def test_the_guest_mounts_neither_the_store_nor_the_session_dir(typing_on, allowed):
    rec = _filed(target=allowed)
    assert sr.approve(rec["id"], by="op", now=T0)[0]
    argv = " ".join(sb.guest_run_argv(SID, "/tmp", UID, GID, "/usr/bin/true",
                                      mounts=sr.mount_specs(SID, T0 + 1)))
    assert str(sr.store_path()) not in argv and str(sb.session_dir(SID)) not in argv


def test_the_guest_gets_the_request_cli_and_is_told_how_to_ask():
    entry = sb.guest_entry("max")
    assert sb.REQUEST_CLI in entry and f"{sb.GUEST_HOME}/.claude/CLAUDE.md" in entry
    compile(sb._REQUEST_CLI_SRC, "chela-request", "exec")
    assert f"http://{sb.PROXY_ALIAS}:{sb.PROXY_PORT}/chela/request" in sb._REQUEST_CLI_SRC


# =====================================================================================
# only a human approves
# =====================================================================================

def _bash(cmd):
    return {"tool_name": "Bash", "tool_input": {"command": cmd}, "cwd": "/"}


@pytest.mark.parametrize("cmd", [
    "chela share-requests approve 0123456789ab-0",
    "uv run chela share-requests approve 0123456789ab-0 --rw",
    "curl -X POST http://127.0.0.1:5005/api/share-requests/0123456789ab-0/approve",
    "true && chela share-requests approve x",
])
def test_a_claude_session_cannot_approve_a_request(cmd):
    d = mergegate.decide(_bash(cmd), env={}, registry=[])
    assert d.deny and "OPERATOR" in d.reason


@pytest.mark.parametrize("cmd", ["chela share-requests", "chela share-requests deny x",
                                 "curl http://127.0.0.1:5005/api/share-requests"])
def test_listing_and_denying_stay_allowed(cmd):
    assert not mergegate.decide(_bash(cmd), env={}, registry=[]).deny


def test_the_dashboard_route_approves_and_refuses(typing_on, allowed):
    from chela.dashboard import app as dash
    c = dash.app.test_client()
    _file(SID, target=allowed)
    _file(SID, target="~/.ssh")
    rows = c.get("/api/share-requests").get_json()["requests"]
    good = next(r for r in rows if r["target"] == allowed)
    bad = next(r for r in rows if r["target"] == "~/.ssh")
    assert good["refusal"] is None and "secrets" in bad["refusal"]
    assert c.post(f"/api/share-requests/{bad['id']}/approve", json={}).status_code == 409
    r = c.post(f"/api/share-requests/{good['id']}/approve", json={"minutes": 30})
    assert r.status_code == 200
    got = next(x for x in c.get("/api/share-requests").get_json()["requests"] if x["id"] == good["id"])
    assert got["status"] == "approved" and 0 < got["seconds_left"] <= 1800 and got["rw"] is False


def test_a_domain_approval_relaunches_a_web_session_only_with_an_allow_list(typing_on, monkeypatch):
    with sr._locked() as store:
        store["sessions"][SID] = {"net": "web"}
    rec = _filed(kind="domain", target="jobs.example.com")
    assert sr.approve(rec["id"], by="op", now=T0)[0]
    monkeypatch.delenv("CHELA_SHARE_WEB_ALLOW", raising=False)
    assert sb.GrantWatch(SID, "web").spec(T0 + 1) == ((), ())
    monkeypatch.setenv("CHELA_SHARE_WEB_ALLOW", "docs.python.org")
    assert sb.GrantWatch(SID, "web").spec(T0 + 1) == ((), ("jobs.example.com",))
    assert sb.GrantWatch(SID, "none").spec(T0 + 1) == ((), ())
