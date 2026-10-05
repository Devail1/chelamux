"""💾 CMX-12: a sandboxed session's transcripts survive a relaunch, so ``/resume`` works.

The guest's HOME is a tmpfs, so before this every relaunch (a crash, the operator's
approval of a ``chela-request`` mount, ``chela share-session <name>`` again) threw its
conversations away. Now ONE host directory per workspace is bind-mounted at
``~/.claude/projects`` in the guest, and the guards are:

* the directory is keyed by the WORKSPACE (stable across launches), never the session id,
  and two workspaces never share — or see — each other's;
* it is created owner-only, as the uid the guest runs as;
* the live sandbox check accepts exactly that mount and still fails on any other;
* a guest's ``chela-request mount`` for it (or for ``CHELA_DIR``) is still refused.

No docker is run: every docker call is stubbed, as in ``test_share_requests.py``.
"""
from __future__ import annotations

import os

import pytest

from chela import config, share_requests as sr, userconfig
from chela import share_sandbox as sb
from chela.transcripts import claude_config_dir

UID, GID = os.getuid(), os.getgid()
SID, SID2 = "0123456789ab", "ba9876543210"
T0 = 1_800_000_000.0


@pytest.fixture
def workspaces(tmp_path):
    out = []
    for name in ("alice", "bob"):
        d = tmp_path / "projects" / name
        d.mkdir(parents=True)
        out.append(os.path.realpath(d))
    return out


def _mount_of(argv: list[str], dst: str) -> list[str]:
    return [argv[i + 1].rsplit(":", 1)[0] for i, a in enumerate(argv[:-1])
            if a == "-v" and argv[i + 1].endswith(":" + dst)]


# =====================================================================================
# where the transcripts live
# =====================================================================================

def test_the_key_is_the_workspace_not_the_launch(workspaces):
    a, b = workspaces
    # stable across launches (and across a path that resolves to the same workspace)…
    assert sb.transcripts_dir(a) == sb.transcripts_dir(a + "/.")
    argv1 = sb.guest_run_argv(SID, a, UID, GID, "/usr/bin/true")
    argv2 = sb.guest_run_argv(SID2, a, UID, GID, "/usr/bin/true")
    assert _mount_of(argv1, sb.GUEST_TRANSCRIPTS) == _mount_of(argv2, sb.GUEST_TRANSCRIPTS) \
        == [os.path.realpath(sb.transcripts_dir(a))]
    assert SID not in str(sb.transcripts_dir(a))
    # …and distinct per workspace, even two that share a basename.
    assert sb.transcripts_dir(a) != sb.transcripts_dir(b)
    twin = os.path.join(os.path.dirname(os.path.dirname(a)), "other", "alice")
    assert sb.transcripts_key(twin) != sb.transcripts_key(a)


def test_two_shares_never_see_each_others_transcripts(workspaces):
    a, b = workspaces
    da, db = (os.path.realpath(sb.ensure_transcripts_dir(w)) for w in (a, b))
    assert da != db
    assert not da.startswith(db + os.sep) and not db.startswith(da + os.sep)
    argv = sb.guest_run_argv(SID, a, UID, GID, "/usr/bin/true")
    assert not [x for x in argv if db in x]
    assert _mount_of(argv, sb.GUEST_TRANSCRIPTS) == [da]


def test_the_dir_is_owner_only_and_the_guests(workspaces):
    a, _ = workspaces
    old = os.umask(0)
    try:
        d = sb.ensure_transcripts_dir(a)
    finally:
        os.umask(old)
    for p in (sb.transcripts_root(), d.parent, d):
        st = os.stat(p)
        assert st.st_mode & 0o777 == 0o700, p
        assert (st.st_uid, st.st_gid) == (UID, GID)


def test_the_guest_mounts_only_its_transcripts_from_claude(workspaces):
    """Nothing else of ``~/.claude`` — credentials, settings, other projects — and the
    ``.claude`` parent is the guest's own tmpfs (so it stays writable by the guest)."""
    a, _ = workspaces
    argv = sb.guest_run_argv(SID, a, UID, GID, "/usr/bin/true")
    mounts = [argv[i + 1] for i, x in enumerate(argv[:-1]) if x == "-v"]
    under_claude = [m for m in mounts if ":" + sb.GUEST_CLAUDE_DIR in m]
    assert under_claude == [f"{os.path.realpath(sb.transcripts_dir(a))}:{sb.GUEST_TRANSCRIPTS}"]
    cfg = os.path.realpath(str(claude_config_dir()))
    assert not [m for m in mounts if os.path.realpath(m.split(":", 1)[0]).startswith(cfg)]
    tmpfs = [argv[i + 1] for i, x in enumerate(argv[:-1]) if x == "--tmpfs"]
    assert any(t.startswith(sb.GUEST_CLAUDE_DIR + ":") and f"uid={UID}" in t for t in tmpfs)


def _stub_launcher(monkeypatch, tmp_path):
    """Stub every docker / tty side effect of :func:`sb.run`; returns the recorded
    ``(steps, guest)`` argv lists."""
    monkeypatch.setattr(sb, "preflight", lambda cwd, net="none": None)
    (tmp_path / "tok").write_text("t")
    monkeypatch.setenv("CHELA_SHARE_SANDBOX_TOKEN_FILE", str(tmp_path / "tok"))
    monkeypatch.setattr(sb, "claude_binary", lambda: "/usr/bin/true")
    monkeypatch.setattr(sb.signal, "signal", lambda *a: None)
    monkeypatch.setattr(sb, "cleanup", lambda sid: None)
    monkeypatch.setattr(sb, "_docker", lambda *a, **k: None)
    monkeypatch.setattr(sb.GrantWatch, "start", lambda self, interval=None: None)

    class P:
        returncode, stdout, stderr = 0, "", ""

    steps, guest = [], []
    monkeypatch.setattr(sb.subprocess, "run", lambda argv, **k: steps.append(argv) or P())
    monkeypatch.setattr(sb.subprocess, "call", lambda argv: guest.append(argv) or 0)
    return steps, guest


def test_the_launcher_creates_it_and_mounts_it(workspaces, monkeypatch, tmp_path):
    a, _ = workspaces
    _steps, guest = _stub_launcher(monkeypatch, tmp_path)
    assert not sb.transcripts_dir(a).exists()
    assert sb.run(SID, a, "none") == 0
    assert sb.transcripts_dir(a).is_dir()
    assert _mount_of(guest[0], sb.GUEST_TRANSCRIPTS) == [os.path.realpath(sb.transcripts_dir(a))]


@pytest.mark.parametrize("broken", [False, True])
def test_the_launcher_refuses_to_start_when_the_dir_cannot_be_made(workspaces, monkeypatch,
                                                                   tmp_path, broken):
    """Fail-closed: no transcripts dir ⇒ no network, no proxy, no guest — never a guest
    whose ``~/.claude/projects`` mount points at something chela didn't vet. The
    ``broken=False`` arm is the negative control: the same stubs DO launch a guest."""
    a, _ = workspaces
    steps, guest = _stub_launcher(monkeypatch, tmp_path)
    held = []
    monkeypatch.setattr(sb, "_hold", held.append)
    if broken:
        # a real failure, not a stubbed one: the root is a FILE, so mkdir under it fails
        sb.transcripts_root().parent.mkdir(parents=True, exist_ok=True)
        sb.transcripts_root().write_text("not a dir")
        assert sb.run(SID, a, "none") == 1
        assert steps == [] and guest == []
        assert any("transcripts" in m or "share-transcripts" in m for m in held), held
    else:
        assert sb.run(SID, a, "none") == 0
        assert guest


def test_a_symlinked_transcripts_path_is_refused(workspaces, tmp_path):
    """A symlink anywhere on the path (root, per-workspace dir, or ``projects``) could
    redirect a guest's writable mount at another workspace's transcripts — or anywhere
    else on the host. ``ensure_transcripts_dir`` refuses it instead of following it."""
    a, b = workspaces
    victim = sb.ensure_transcripts_dir(b)            # bob's real transcripts
    d = sb.transcripts_dir(a)
    for link in (d, d.parent):
        if link == d:
            d.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            d.symlink_to(victim, target_is_directory=True)
        else:
            d.unlink()
            d.parent.rmdir()
            d.parent.symlink_to(victim.parent, target_is_directory=True)
        with pytest.raises(OSError, match="symlink"):
            sb.ensure_transcripts_dir(a)
    d.parent.unlink()
    assert sb.ensure_transcripts_dir(a) != victim   # negative control: a real dir is fine


def test_a_workspace_containing_the_transcripts_is_refused(tmp_path, monkeypatch):
    proj = tmp_path / "proj"
    proj.mkdir()
    monkeypatch.setattr(sb, "transcripts_root", lambda: proj / "share-transcripts")
    assert "transcripts" in (sb.workspace_refusal(str(proj)) or "")
    monkeypatch.setattr(sb, "transcripts_root", lambda: tmp_path / "elsewhere")
    assert sb.workspace_refusal(str(proj)) is None        # the negative control


# =====================================================================================
# the live sandbox check
# =====================================================================================

def _guest(cwd, extra_mounts=()):
    net = sb.network_name(SID)
    return {
        "State": {"Running": True},
        "Config": {"Labels": {sb.LABEL: SID, sb.NET_LABEL: "none"}, "User": f"{UID}:{GID}",
                   "Env": ["HOME=/home/guest"]},
        "HostConfig": {"CapDrop": ["ALL"], "CapAdd": None, "Privileged": False,
                       "ReadonlyRootfs": True, "SecurityOpt": ["no-new-privileges"],
                       "Memory": 2 << 30, "PidsLimit": 512, "NetworkMode": net},
        "NetworkSettings": {"Networks": {net: {}}},
        "Mounts": [_mount(cwd, sb.GUEST_WORKDIR, True),
                   _mount("/usr/bin/true", sb.CLAUDE_MOUNT, False), *extra_mounts],
    }


def _network():
    return {"Internal": True, "Options": {"com.docker.network.bridge.inhibit_ipv4": "true"},
            "Containers": {"a": {"Name": sb.container_name(SID)}, "b": {"Name": sb.proxy_name(SID)}}}


def _mount(src, dst, rw):
    return {"Type": "bind", "Source": src, "Destination": dst, "RW": rw}


def test_the_live_check_accepts_its_own_transcripts_and_nothing_else(workspaces):
    a, b = workspaces
    own = str(sb.ensure_transcripts_dir(a))
    other = str(sb.ensure_transcripts_dir(b))
    v = lambda *m: sb.verify_container(_guest(a, m), _network(), SID, a, UID, GID)  # noqa: E731
    assert v() is None                                          # a pre-CMX-12 guest
    assert v(_mount(own, sb.GUEST_TRANSCRIPTS, True)) is None   # ⭐ the new mount verifies
    # the other workspace's transcripts in this guest: refused
    assert v(_mount(other, sb.GUEST_TRANSCRIPTS, True))
    # the operator's own Claude transcripts, or all of CHELA_DIR, at the same place: refused
    assert v(_mount(str(claude_config_dir() / "projects"), sb.GUEST_TRANSCRIPTS, True))
    assert v(_mount(str(config.CHELA_DIR), sb.GUEST_TRANSCRIPTS, True))
    # its own dir anywhere else, or any other extra bind mount: refused
    assert v(_mount(own, sb.GUEST_CLAUDE_DIR, True))
    assert v(_mount(own, sb.GUEST_TRANSCRIPTS, True), _mount(own, "/home/guest/x", True))
    assert v(_mount(own, sb.GUEST_TRANSCRIPTS, True), _mount("/etc", "/home/guest/.claude/etc", False))


# =====================================================================================
# a guest's request can never reach it
# =====================================================================================

def test_a_mount_request_for_the_transcripts_or_chela_dir_is_refused(workspaces):
    a, _ = workspaces
    d = sb.ensure_transcripts_dir(a)
    for p in (d, d.parent, sb.transcripts_root(), config.CHELA_DIR):
        assert sr.mount_refusal(str(p)), p


def test_a_record_forced_to_approved_for_the_transcripts_mounts_nothing(workspaces):
    """Even a store that says "approved" can't hand a guest another workspace's
    transcripts: the launcher re-applies the deny-list."""
    userconfig.set_(config.SHARE_TYPING_KEY, True)
    a, b = workspaces
    other = str(sb.ensure_transcripts_dir(b))
    d = sb.session_dir(SID)
    d.mkdir(parents=True, exist_ok=True)
    with open(d / sr.REQUESTS_NAME, "a", encoding="utf-8") as f:
        f.write('{"kind": "mount", "target": "%s", "access": "rw", "reason": "x", "ts": %s}\n'
                % (other, T0))
    rec, = sr.ingest(SID, now=T0)
    assert not sr.approve(rec["id"], by="op", now=T0)[0]
    with sr._locked() as store:
        store["requests"][rec["id"]].update(status=sr.APPROVED, path=other,
                                            expires_at=T0 + 3600, rw=True)
    assert sr.mount_specs(SID, T0 + 1) == []


def test_the_transcripts_root_is_refused_even_outside_chela_dir(tmp_path, monkeypatch):
    """Named on the deny-list itself, not only inherited from ``CHELA_DIR`` — so moving
    the root out of ``CHELA_DIR`` can't hand it to a guest's request."""
    root = tmp_path / "elsewhere" / "share-transcripts"
    (root / "k" / "projects").mkdir(parents=True)
    monkeypatch.setattr(sb, "transcripts_root", lambda: root)
    assert sr.mount_refusal(str(root / "k" / "projects"))
    assert sr.mount_refusal(str(tmp_path / "elsewhere"))     # a parent of it, too
