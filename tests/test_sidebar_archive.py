"""The sidebar archive (CMX-75): server-side, archiving a session CLOSES it, Unarchive resumes it.

Three layers, each against what it actually touches:

* **Real tmux, real store** — :func:`chela.sidebar_archive.archive_window` /
  :func:`~chela.sidebar_archive.unarchive_session` run in a SUBPROCESS with an explicit
  ``CHELA_DIR`` and every ``tmux`` pinned to a private ``-L`` socket by a PATH shim (the
  ``tests/test_terminals_selfheal.py`` pattern), plus a ``claude`` stub that logs how it was
  launched. Nothing here can reach the live tmux server or ``~/.chela``.
* **The refusals** — in-process, with the window facts stubbed: each one must leave the
  window open and write no record.
* **The routes and the Telegram hand-off** — the Flask routes (two clients, one archive; a
  settled run hidden, never closed) and :func:`chela.telegram.reconcile.reconcile_bindings`
  reopening the archived session's topic for its resumed window.
"""
from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path

import pytest

from chela import sidebar_archive as sa
from chela import spawn as spawn_mod
from chela.telegram.bindings import BindingRegistry
from chela.telegram.reconcile import reconcile_bindings

REPO = Path(__file__).resolve().parent.parent
TMUX_BIN = shutil.which("tmux")
SESSION = "archive-test"
SID = "aaaaaaaa-1111-2222-3333-444444444444"

_DRIVER = """
import json, sys
from chela import sidebar_archive as sa
mode, arg = sys.argv[1], sys.argv[2]
if mode == "archive":
    if len(sys.argv) > 3:
        # The window runs a stub, not Claude: say what a live idle Claude session would.
        sid = sys.argv[3]
        sa.resolve_session_id = lambda wid, pane=None: sid
        sa.window_status = lambda wid: (True, "idle")
    out = sa.archive_window(arg, orch_wid=None, dispatched=set())
    print(json.dumps({"ok": out.ok, "error": out.error, "record": out.record}))
else:
    out = sa.unarchive_session(arg)
    print(json.dumps({"ok": out.ok, "error": out.error, "wid": out.wid, "name": out.name}))
"""


# --- real tmux on a private socket ------------------------------------------------------

@pytest.fixture
def live(tmp_path):
    """A private tmux server + an explicit CHELA_DIR + a ``claude`` stub, for subprocesses."""
    if TMUX_BIN is None:
        pytest.skip("tmux not installed")
    sock = f"chelatest-{os.getpid()}-{uuid.uuid4().hex[:6]}"
    assert sock.startswith("chelatest-")
    shim = tmp_path / "shim"
    shim.mkdir()
    (shim / "tmux").write_text(f'#!/bin/sh\nexec {TMUX_BIN} -L {sock} "$@"\n')
    launches = tmp_path / "claude-launches.log"
    (shim / "claude").write_text(f'#!/bin/sh\necho "$PWD|$*" >> {launches}\nexec sleep 600\n')
    for f in shim.iterdir():
        f.chmod(0o755)
    tmuxdir = tempfile.mkdtemp(prefix="cmx", dir="/tmp")
    chela_dir = tmp_path / "chela"
    chela_dir.mkdir()
    project = tmp_path / "myproj"
    project.mkdir()
    driver = tmp_path / "driver.py"
    driver.write_text(_DRIVER)
    env = {k: v for k, v in os.environ.items() if k not in ("TMUX", "TMUX_PANE", "CHELA_WID")}
    env.update(
        PATH=f"{shim}:{os.environ['PATH']}",
        TMUX_TMPDIR=tmuxdir,
        SHELL="/bin/sh",
        CHELA_TMUX_SESSION=SESSION,
        CHELA_DIR=str(chela_dir),            # explicit — never inherited, never setdefault
        CHELA_ENV_FILE="",
        CHELA_TELEGRAM_BINDINGS=str(chela_dir / "telegram-bindings.json"),
        PYTHONPATH=str(REPO),
    )

    def tmux(*args, check=True):
        return subprocess.run([TMUX_BIN, "-L", sock, *args], env=env,
                              capture_output=True, text=True, check=check)

    def run(*args):
        proc = subprocess.run([sys.executable, str(driver), *args], env=env, cwd=str(tmp_path),
                              capture_output=True, text=True, timeout=60)
        assert proc.returncode == 0, proc.stderr
        return json.loads(proc.stdout.strip().splitlines()[-1])

    def windows():
        out = tmux("list-windows", "-t", SESSION, "-F", "#{window_id}\t#{window_name}\t#{pane_current_path}")
        return [tuple(line.split("\t")) for line in out.stdout.splitlines()]

    tmux("new-session", "-d", "-s", SESSION, "-n", "anchor", "sleep 600")
    # A window chela opens runs tmux's default command — by default a LOGIN shell, which
    # re-reads /etc/profile and would put the REAL `claude` back ahead of the stub. Pin a
    # plain shell with the stub's PATH, so nothing but the stub can ever be launched.
    tmux("set-option", "-g", "default-command", f"env PATH={shlex.quote(env['PATH'])} /bin/sh")
    # named apart from its folder, so "same name" cannot pass by the folder-name default
    wid = tmux("new-window", "-t", f"{SESSION}:", "-n", "mywin", "-c", str(project),
               "-P", "-F", "#{window_id}", "sleep 600").stdout.strip()
    ns = type("Live", (), {})()
    ns.tmux, ns.run, ns.windows, ns.wid = tmux, run, windows, wid
    ns.chela_dir, ns.project, ns.launches = chela_dir, project, launches
    yield ns
    tmux("kill-server", check=False)
    shutil.rmtree(tmuxdir, ignore_errors=True)


def _store(chela_dir: Path) -> dict:
    return json.loads((chela_dir / "sidebar-archive.json").read_text())


def test_archive_closes_an_idle_session_and_unarchive_resumes_it_in_a_new_window(live):
    (live.chela_dir / "telegram-bindings.json").write_text(json.dumps(
        {"chat_id": "1", "bindings": {live.wid: "77"}}))

    out = live.run("archive", live.wid, SID)
    assert out["ok"], out
    assert live.wid not in [w for w, _, _ in live.windows()], "the archived window must be CLOSED"
    rec = _store(live.chela_dir)["sessions"][f"s:{SID}"]
    assert rec["session_id"] == SID
    assert os.path.realpath(rec["cwd"]) == os.path.realpath(live.project)
    assert rec["name"] == "mywin"
    assert rec["thread_id"] == "77", "the bound topic is recorded, to reopen on resume"

    back = live.run("unarchive", f"s:{SID}")
    assert back["ok"], back
    new = {w: (n, p) for w, n, p in live.windows()}
    assert back["wid"] in new and back["wid"] != live.wid
    name, path = new[back["wid"]]
    assert name == "mywin", "same window name"
    assert os.path.realpath(path) == os.path.realpath(live.project), "same cwd"
    deadline = time.time() + 10
    while time.time() < deadline and not (live.launches.exists() and live.launches.read_text()):
        time.sleep(0.1)
    if not live.launches.exists():
        pane = live.tmux("capture-pane", "-p", "-t", back["wid"]).stdout
        raise AssertionError(f"claude never launched; pane:\n{pane}")
    launched = live.launches.read_text().strip().splitlines()
    assert len(launched) == 1, launched
    cwd, argv = launched[0].split("|", 1)
    assert os.path.realpath(cwd) == os.path.realpath(live.project)
    assert f"--resume {SID}" in argv, f"claude was not resumed: {argv!r}"
    assert f"s:{SID}" not in _store(live.chela_dir)["sessions"], "a resumed session leaves the archive"
    rebinds = json.loads((live.chela_dir / "topic-rebinds.json").read_text())
    assert rebinds[back["wid"]]["thread_id"] == "77", "its old topic is requested back"
    pins = json.loads((live.chela_dir / "session-ids.json").read_text())
    assert pins[back["wid"]]["session_id"] == SID


def test_archive_with_no_resolvable_session_id_is_refused_and_the_window_is_untouched(live):
    # The REAL resolver and status probe: the window runs no Claude, so nothing names a session.
    out = live.run("archive", live.wid)
    assert not out["ok"]
    assert "session id cannot be determined" in out["error"]
    assert live.wid in [w for w, _, _ in live.windows()], "a refused archive must not close the window"
    assert not (live.chela_dir / "sidebar-archive.json").exists() or \
        not _store(live.chela_dir)["sessions"], "no resume record for a refused archive"


# --- the refusals, in-process ------------------------------------------------------------

@pytest.fixture
def facts(monkeypatch, tmp_path):
    """One live idle window ``@5`` whose session resolves; the kill is spied, never real."""
    from chela import discovery, sessions

    monkeypatch.setattr(sa, "_STORE", tmp_path / "sidebar-archive.json")
    monkeypatch.setattr(discovery, "get_windows_by_id", lambda: {"@5": "proj", "@1": "orch"})
    monkeypatch.setattr(discovery, "get_window_cwd_by_id", lambda wid: str(tmp_path))
    monkeypatch.setattr(sessions, "panes", lambda force=False: {})
    monkeypatch.setattr(sa, "window_status", lambda wid: (True, "idle"))
    monkeypatch.setattr(sa, "resolve_session_id", lambda wid, pane=None: SID)
    monkeypatch.setattr(sa, "_rc_name", lambda wid, name: None)
    monkeypatch.setattr(sa, "_bound_thread", lambda wid: None)
    killed: list[str] = []
    monkeypatch.setattr(sa, "close_window", lambda wid: killed.append(wid))
    return killed


def _no_record() -> bool:
    return not sa._load()["sessions"]


def test_an_idle_session_with_a_known_id_IS_archived(facts):
    out = sa.archive_window("@5", orch_wid="@1", dispatched=set())
    assert out.ok and facts == ["@5"] and not _no_record()


@pytest.mark.parametrize("status", ["busy", "waiting"])
def test_a_busy_or_waiting_session_is_refused(facts, monkeypatch, status):
    monkeypatch.setattr(sa, "window_status", lambda wid: (True, status))
    out = sa.archive_window("@5", orch_wid="@1", dispatched=set())
    assert not out.ok and facts == [] and _no_record()


def test_an_unreadable_status_while_claude_runs_is_refused(facts, monkeypatch):
    monkeypatch.setattr(sa, "window_status", lambda wid: (True, None))
    out = sa.archive_window("@5", orch_wid="@1", dispatched=set())
    assert not out.ok and facts == [] and _no_record()


def test_the_orchestrator_is_refused(facts):
    out = sa.archive_window("@5", orch_wid="@5", dispatched=set())
    assert not out.ok and "orchestrator" in out.error and facts == [] and _no_record()


def test_a_dispatched_window_is_never_closed_from_the_sidebar(facts):
    out = sa.archive_window("@5", orch_wid="@1", dispatched={"@5"})
    assert not out.ok and "dispatched" in out.error and facts == [] and _no_record()


def test_an_unknown_session_id_is_refused(facts, monkeypatch):
    monkeypatch.setattr(sa, "resolve_session_id", lambda wid, pane=None: None)
    out = sa.archive_window("@5", orch_wid="@1", dispatched=set())
    assert not out.ok and facts == [] and _no_record()


def test_a_failed_close_rolls_the_record_back(facts, monkeypatch):
    monkeypatch.setattr(sa, "close_window", lambda wid: "no such window")
    out = sa.archive_window("@5", orch_wid="@1", dispatched=set())
    assert not out.ok and _no_record()


@pytest.mark.parametrize("source,ok", [("event_log", True), ("pinned", True), ("cmdline", True),
                                       ("cwd", False), ("none", False)])
def test_resolve_session_id_trusts_an_identification_never_the_cwd_guess(monkeypatch, tmp_path,
                                                                         source, ok):
    from chela import sessions

    res = sessions.Resolution("@5", SID, tmp_path / f"{SID}.jsonl", source)
    monkeypatch.setattr(sessions, "resolve_window", lambda wid, pane=None: res)
    assert sa.resolve_session_id("@5") == (SID if ok else None)


def test_resolve_session_id_needs_a_transcript_on_disk(monkeypatch):
    from chela import sessions

    monkeypatch.setattr(sessions, "resolve_window",
                        lambda wid, pane=None: sessions.Resolution("@5", SID, None, "event_log"))
    assert sa.resolve_session_id("@5") is None


# --- spawn: same name, Remote Control restored -------------------------------------------

def test_spawn_window_reuses_the_archived_name_and_remote_control_name(monkeypatch, tmp_path):
    monkeypatch.setattr(spawn_mod.discovery, "ensure_session", lambda: True)
    monkeypatch.setattr(spawn_mod.discovery, "get_all_windows", lambda: {"other": "@2"})
    monkeypatch.setattr(spawn_mod.envutil, "scrub_tmux_secrets", lambda: None)
    monkeypatch.setattr(spawn_mod.agent_manager, "lock_window_name", lambda *a, **kw: None)
    monkeypatch.setattr(spawn_mod.config, "remote_control_enabled", lambda: False)
    monkeypatch.setattr(spawn_mod.rc_rename, "mark_launched", lambda *a: None)
    argvs, sent = [], []

    class _P:
        returncode, stdout, stderr = 0, "@42", ""
    monkeypatch.setattr(spawn_mod.subprocess, "run", lambda argv, **kw: (argvs.append(argv), _P())[1])
    monkeypatch.setattr(spawn_mod, "_send", lambda target, text: sent.append(text))
    res = spawn_mod.spawn_window(tmp_path, command=f"claude --resume {SID}", name="myproj",
                                 remote_control_name="My Proj")
    assert res.ok and res.name == "myproj"
    assert argvs[0][argvs[0].index("-n") + 1] == "myproj"
    assert f"claude --remote-control 'My Proj' --resume {SID}" in sent


# --- the routes ---------------------------------------------------------------------------

@pytest.fixture
def client(monkeypatch, tmp_path):
    from chela.dashboard import app as dash

    monkeypatch.setattr(sa, "_STORE", tmp_path / "sidebar-archive.json")
    monkeypatch.setattr(dash.config, "TERMINALS_ENABLED", True)
    monkeypatch.setattr(dash.discovery, "get_all_windows", lambda: {"proj": "@5", "cmx-5": "@7"})
    monkeypatch.setattr(dash.inbox, "orchestrator_wid", lambda store=None: None)
    monkeypatch.setattr(sa, "drop_resumed_elsewhere", lambda is_live: [])
    return dash


def test_two_clients_see_the_same_archive(client, monkeypatch):
    monkeypatch.setattr(client.dispatcher, "list_runs",
                        lambda: [{"task_id": "CMX-5", "status": "done", "judge_state": ""}])
    phone, laptop = client.app.test_client(), client.app.test_client()
    assert phone.post("/api/sidebar/archive", json={"keys": ["run:CMX-5"]}).get_json()["ok"]
    assert laptop.get("/api/sidebar/archive").get_json()["hidden"] == ["run:CMX-5"]
    assert laptop.post("/api/sidebar/unarchive", json={"keys": ["run:CMX-5"]}).get_json()["ok"]
    assert phone.get("/api/sidebar/archive").get_json()["hidden"] == []


def test_a_settled_run_is_HIDDEN_and_its_windows_are_not_closed(client, monkeypatch):
    monkeypatch.setattr(client.dispatcher, "list_runs", lambda: [
        {"task_id": "CMX-5", "status": "done", "judge_state": "", "window_id": "@7"}])
    killed = []
    monkeypatch.setattr(sa, "close_window", lambda wid: killed.append(wid))
    monkeypatch.setattr(sa, "archive_window", lambda *a, **k: pytest.fail("a run must not be closed"))
    body = client.app.test_client().post("/api/sidebar/archive", json={"keys": ["run:CMX-5"]}).get_json()
    assert body["ok"] and body["archived"] == ["run:CMX-5"] and body["closed"] == []
    assert killed == []
    assert sa.state()["hidden"] == ["run:CMX-5"]


def test_an_unsettled_run_is_refused(client, monkeypatch):
    monkeypatch.setattr(client.dispatcher, "list_runs",
                        lambda: [{"task_id": "CMX-5", "status": "running", "judge_state": ""}])
    body = client.app.test_client().post("/api/sidebar/archive", json={"keys": ["run:CMX-5"]}).get_json()
    assert not body["ok"] and body["refused"][0]["key"] == "run:CMX-5"
    assert sa.state()["hidden"] == []


def test_a_window_key_routes_to_archive_window_with_the_dispatched_set(client, monkeypatch):
    monkeypatch.setattr(client.dispatcher, "list_runs", lambda: [])
    monkeypatch.setattr(client, "_dispatched_wids", lambda windows, runs: {"@7"})
    seen = {}

    def fake(wid, *, orch_wid, dispatched):
        seen.update(wid=wid, dispatched=dispatched)
        return sa.Outcome(True, record={"key": f"s:{SID}"}, wid=wid)
    monkeypatch.setattr(sa, "archive_window", fake)
    body = client.app.test_client().post("/api/sidebar/archive", json={"keys": ["w:@5"]}).get_json()
    assert body["archived"] == [f"s:{SID}"] and body["closed"] == ["@5"]
    assert seen == {"wid": "@5", "dispatched": {"@7"}}


def test_the_old_localStorage_import_only_HIDES(client, monkeypatch):
    monkeypatch.setattr(sa, "archive_window", lambda *a, **k: pytest.fail("migration must not close"))
    body = client.app.test_client().post("/api/sidebar/archive/migrate",
                                         json={"keys": ["w:@5", "run:CMX-5"]}).get_json()
    assert body["state"]["hidden"] == ["run:CMX-5", "w:@5"]


def test_recent_sessions_does_not_offer_an_archived_session_twice(client, monkeypatch, tmp_path):
    from types import SimpleNamespace

    v = SimpleNamespace(verdict="MANUAL", session_id=SID, wid="@5", stamped_epoch="e1", cwd=str(tmp_path),
                        store="session-ids", label="mywin", manual_command=lambda: "claude --resume x")
    monkeypatch.setattr(client, "_restore_verdicts", lambda: [v])
    monkeypatch.setattr(client, "_dispatcher_owned_wid_epochs", lambda: set())
    monkeypatch.setattr(client, "_terminal_run_claims", lambda: (set(), set()))
    monkeypatch.setattr(client.dismissed_sessions, "ids", lambda: set())
    c = client.app.test_client()
    assert [r["session_id"] for r in c.get("/api/restore").get_json()["rows"]] == [SID]
    with sa._LOCK:
        sa._save({"hidden": {}, "sessions": {f"s:{SID}": {"key": f"s:{SID}", "session_id": SID}}})
    assert c.get("/api/restore").get_json()["rows"] == [], \
        "an archived session is resumed from the Archived section, not offered again here"


# --- Telegram: closed on archive, reopened + rebound on unarchive -----------------------

class _Topics:
    def __init__(self, reopen_ok=True):
        self.calls: list[tuple] = []
        self.reopen_ok = reopen_ok

    def create_topic(self, name):
        self.calls.append(("create", name))
        return "900"

    def close_topic(self, thread):
        self.calls.append(("close", str(thread)))
        return True

    def reopen_topic(self, thread):
        self.calls.append(("reopen", str(thread)))
        return self.reopen_ok

    def rename_topic(self, thread, name):
        return True


def test_the_archived_windows_topic_is_closed_and_reopened_and_rebound_for_its_resume():
    reg = BindingRegistry("1")
    reg.bind("@5", "77", "e1")
    api = _Topics()
    # archive: the window is gone — its topic is CLOSED (archived, never deleted), unbound
    reconcile_bindings(reg, {}, [], api, now_epoch="e1")
    assert api.calls == [("close", "77")]
    assert reg.thread_for_window("@5") is None
    # unarchive: the resumed window @9 asks for 77 back — reopened and bound, no new topic
    rebinds = {"@9": "77"}
    reconcile_bindings(reg, {"@9": "myproj"}, ["@9"], api, now_epoch="e1", rebinds=rebinds)
    assert ("reopen", "77") in api.calls
    assert not any(c[0] == "create" for c in api.calls), "the old topic is reused, not replaced"
    assert reg.thread_for_window("@9") == "77"
    assert rebinds == {}, "a settled rebind is consumed"


def test_a_rebind_waits_for_its_window_to_run_claude_and_a_failed_reopen_gets_a_fresh_topic():
    reg = BindingRegistry("1")
    api = _Topics(reopen_ok=False)
    rebinds = {"@9": "77"}
    reconcile_bindings(reg, {"@9": "myproj"}, [], api, now_epoch="e1", rebinds=rebinds)
    assert rebinds == {"@9": "77"} and api.calls == [], "not an agent yet — kept for a later tick"
    reconcile_bindings(reg, {"@9": "myproj"}, ["@9"], api, now_epoch="e1", rebinds=rebinds)
    assert api.calls[0] == ("reopen", "77") and ("create", "myproj") in api.calls
    assert reg.thread_for_window("@9") == "900"


def test_a_rebind_never_steals_a_topic_another_window_holds():
    reg = BindingRegistry("1")
    reg.bind("@3", "77", "e1")
    api = _Topics()
    reconcile_bindings(reg, {"@3": "a", "@9": "b"}, ["@3", "@9"], api, now_epoch="e1",
                       rebinds={"@9": "77"})
    assert reg.thread_for_window("@3") == "77"
    assert ("reopen", "77") not in api.calls


def test_pending_rebinds_drop_another_epoch_and_expired_requests(monkeypatch, tmp_path):
    monkeypatch.setattr(sa, "_REBINDS", tmp_path / "topic-rebinds.json")
    monkeypatch.setattr(sa.epoch, "current", lambda: "e1")
    sa.request_rebind("@9", "77")
    assert sa.pending_rebinds("e1") == {"@9": "77"}
    assert sa.pending_rebinds("e2") == {}, "an @N from another tmux server is a stranger"
    assert sa.pending_rebinds("e1", now=time.time() + sa.REBIND_TTL_SECONDS + 1) == {}
    sa.settle_rebinds({}, "e1")
    assert sa.pending_rebinds("e1") == {}
