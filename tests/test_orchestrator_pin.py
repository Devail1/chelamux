"""CMX-394 — the orchestrator pin must never move to a DIFFERENT session that shares its cwd.

2026-09-29: two Claude sessions shared one cwd (`@8`, the orchestrator; `@45`, a sibling).
After `@45` was restarted the inbox pinned `@45` and a merge verdict landed there. Three
things were wrong, and each has a guard here:

  * the self-heal may rebind ONLY to the session whose IDENTITY is the recorded one — never
    to "a live session in that directory" — and with no identity it must not heal at all;
  * `@8` had no identity anywhere: a plain `claude` window sharing its cwd files every hook
    `wid: null` (the cwd cannot say which window), so once its SessionStart record aged out
    of the event log nothing named its session. Claude Code's own session registry
    (`<config>/sessions/<pid>.json`) does, keyed by the pane's claude PID;
  * the pin moved and nothing said so: `orchestrator.moved` now does, once.

The heal scenarios run in a SUBPROCESS (``tests/pin_scenarios.py``) with an explicit env whose
every chela/claude path is a temp dir — never the live inbox, event log or tmux socket.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from chela import epoch, event_log, hooks, inbox, sessions

REPO = Path(__file__).resolve().parent.parent
S8 = "ce2140f3-0000-4000-8000-000000000008"
S45 = "8b394f16-0000-4000-8000-000000000045"
HOME = "/home/someone"


def _scenario(tmp_path: Path, name: str) -> dict:
    root = tmp_path / name
    root.mkdir()
    env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": str(root / "home"),
        "PYTHONPATH": str(REPO),
        "PYTHONDONTWRITEBYTECODE": "1",
        "CHELA_DIR": str(root / "chela"),
        "CHELA_INBOX_FILE": str(root / "chela" / "inbox.json"),
        "CHELA_EVENTS_FILE": str(root / "chela" / "events.jsonl"),
        "CLAUDE_CONFIG_DIR": str(root / "claude"),
        "CHELA_TMUX_SESSION": "cmx-394-no-such-session",
        "CHELA_INBOX_ENABLED": "true",
    }
    proc = subprocess.run([sys.executable, str(REPO / "tests" / "pin_scenarios.py"), name,
                           str(root)], env=env, cwd=str(root), capture_output=True, text=True,
                          timeout=60)
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout.strip().splitlines()[-1])


# --- the heal: identity, never cwd ---------------------------------------------------

def test_the_pin_never_moves_to_a_sibling_session_sharing_the_orchestrators_cwd(tmp_path):
    """🔴 GUARD. `@8`'s address is undeliverable and `@45` — a DIFFERENT session in the same
    cwd — is live and idle. The pin stays on `@8`, nothing is typed into `@45`, and the
    undeliverable alarm fires. A heal that matched "a session in that directory" would hand
    the orchestrator's merge verdicts to the wrong agent."""
    out = _scenario(tmp_path, "sibling_only")
    assert out["orchestrator"] == "@8", out
    assert out["session"] == "ce2140f3-0000-4000-8000-000000000008"
    assert out["sent"] == [], "nothing may be delivered to the sibling"
    assert "inbox_undeliverable" in out["kinds"]
    assert "inbox_self_healed" not in out["kinds"]
    assert inbox.MOVED_KIND not in out["kinds"]


def test_the_orchestrators_OWN_session_in_a_new_window_is_healed_to(tmp_path):
    """⭐ The case that must be ACCEPTED: after a tmux restart the orchestrator's own session
    is running again under `@60` (a plain `claude`, so only its registry entry names it),
    beside the sibling `@45` in the same cwd. The heal rebinds to `@60` — the identity — and
    the move is announced exactly once."""
    out = _scenario(tmp_path, "own_session_reappears")
    assert out["orchestrator"] == "@60", out
    assert [wid for wid, _ in out["sent"]] == ["@60", "@60"], "held verdict + move notice"
    assert sum("orchestrator pin moved @8 to @60" in text for _, text in out["sent"]) == 1
    assert "inbox_self_healed" in out["kinds"]
    assert out["kinds"].count(inbox.MOVED_KIND) == 1
    assert out["queued"] == []


def test_with_no_recorded_identity_the_heal_does_nothing_and_the_alarm_fires(tmp_path):
    """No identity recorded (e.g. `chela watch` could not resolve one): no heal at all, even
    though a live session sits in the orchestrator's cwd. The address is kept and the
    undeliverable alarm fires — never a guess."""
    out = _scenario(tmp_path, "no_identity")
    assert out["orchestrator"] == "@8", out
    assert out["session"] is None
    assert out["sent"] == []
    assert "inbox_undeliverable" in out["kinds"]
    assert "inbox_self_healed" not in out["kinds"]


# --- the identity: Claude Code's own session registry --------------------------------

def _proc(root: Path, pid: int, ticks: str) -> None:
    d = root / str(pid)
    d.mkdir(parents=True, exist_ok=True)
    (d / "stat").write_text(f"{pid} (claude) S " + " ".join(["0"] * 18 + [ticks]) + "\n")


def _claude_registry_dir() -> Path:
    """Claude Code's REAL registry layout, ``$CLAUDE_CONFIG_DIR/sessions``, spelled out here —
    NOT via :func:`sessions.claude_sessions_dir`. A fixture that writes through the helper
    under test follows it into any wrong directory, so the reader always finds the file."""
    return Path(os.environ["CLAUDE_CONFIG_DIR"]) / "sessions"


def _registry(pid: int, sid: str, wid: str, ticks: str) -> None:
    reg = _claude_registry_dir()
    reg.mkdir(parents=True, exist_ok=True)
    (reg / f"{pid}.json").write_text(json.dumps({
        "pid": pid, "sessionId": sid, "cwd": HOME, "procStart": ticks,
        "tmux": f"chela:{wid}.%{wid[1:]}"}))


@pytest.fixture
def fleet(tmp_path, monkeypatch):
    """`@8` (a plain `claude`, the orchestrator) and `@45` (`claude --resume`) in ONE cwd,
    each with a registry entry and a fixture `/proc`. No tmux, no event-log evidence."""
    proc = tmp_path / "proc"
    monkeypatch.setattr(sessions, "PROC", proc)
    _proc(proc, 808, "5656")
    _proc(proc, 4545, "31815")
    _registry(808, S8, "@8", "5656")
    _registry(4545, S45, "@45", "31815")
    panes = {
        "@8": sessions.Pane("@8", HOME, "claude", 808, HOME, None, 1.0),
        "@45": sessions.Pane("@45", HOME, "claude", 4545, HOME, S45, 1.0),
    }
    monkeypatch.setattr(sessions, "panes", lambda force=False: dict(panes))
    monkeypatch.setattr(hooks, "_panes", lambda force=False: dict(panes))
    return panes


def test_the_registry_names_a_plain_claude_windows_session_despite_a_shared_cwd(fleet):
    """The @8 half of the incident: a window with no `--resume` and no surviving hook record.
    The registry, keyed by the pane's own claude pid, names its session — so the window's
    identity resolves, the self-heal can find it, and its hooks stop filing `wid: null`."""
    assert sessions.session_of_window("@8") == S8
    assert sessions.wid_for_session(S8) == "@8"
    assert hooks.wid_for_session(S8, None) == "@8", "hook events for @8 must carry its wid"
    assert hooks.wid_for_session(S45, None) == "@45"


def test_the_registry_is_read_from_claude_codes_own_sessions_directory(fleet, tmp_path):
    """Claude Code writes ``<config dir>/sessions/<pid>.json``. The path is built here from
    ``$CLAUDE_CONFIG_DIR`` by hand, so a reader looking anywhere else finds nothing."""
    real = tmp_path / "claude-config" / "sessions" / "808.json"
    assert real.is_file(), "fixture: the entry sits where Claude Code puts it"
    assert sessions.claude_sessions_dir() == real.parent
    assert sessions.registry_session(808) == S8


def test_a_registry_entry_left_by_a_recycled_pid_is_refused(fleet, tmp_path):
    """The file says `procStart` 5656 but the pid now started at another tick: a different
    process got the pid. Its stale entry must not lend it the dead session's identity."""
    _proc(tmp_path / "proc", 808, "9999")
    assert sessions.registry_session(808) is None
    assert sessions.session_of_window("@8") is None
    assert sessions.wid_for_session(S8) is None


def test_a_registry_file_whose_own_pid_field_disagrees_is_refused(fleet, tmp_path):
    """`808.json` must describe pid 808. A file under that name whose own ``pid`` field says
    4545 — procStart and sessionId otherwise valid for 808 — is not 808's claim about itself,
    and must not name @8's session. (Control: the same file with ``pid: 808`` does.)"""
    reg = _claude_registry_dir()
    good = json.loads((reg / "808.json").read_text())
    assert sessions.registry_session(808) == S8, "control: the honest file is accepted"
    (reg / "808.json").write_text(json.dumps({**good, "pid": 4545}))
    assert sessions.registry_entry(808) is None
    assert sessions.registry_session(808) is None
    assert sessions.session_of_window("@8") is None


def test_two_panes_whose_registry_entries_claim_one_session_resolve_to_none(fleet, tmp_path):
    """No `--resume` anywhere, and two live claude pids both register session S8. Picking
    either would file one agent's events under the other: ambiguity is None, never the
    first match. (Control: with only one claimant the same call names it.)"""
    _proc(tmp_path / "proc", 909, "7777")
    _registry(909, S8, "@9", "7777")
    both = {
        "@8": sessions.Pane("@8", HOME, "claude", 808, HOME, None, 1.0),
        "@9": sessions.Pane("@9", HOME, "claude", 909, HOME, None, 1.0),
    }
    assert sessions.wid_claiming_session(S8, {"@8": both["@8"]}) == "@8", "control"
    assert sessions.wid_claiming_session(S8, {"@9": both["@9"]}) == "@9", "control"
    assert sessions.wid_claiming_session(S8, both) is None


def test_a_registry_file_whose_sessionId_is_not_a_session_id_is_refused(fleet, tmp_path):
    """`808.json` with its `pid` and `procStart` honest but a `sessionId` that is not a
    well-formed session id (a path, a shell word) is not a session claim: refused. Only the
    sessionId field differs from the accepted control."""
    reg = _claude_registry_dir()
    good = json.loads((reg / "808.json").read_text())
    assert sessions.registry_session(808) == S8, "control: the honest file is accepted"
    (reg / "808.json").write_text(json.dumps({**good, "sessionId": "../not a session"}))
    assert sessions.registry_entry(808) is None
    assert sessions.registry_session(808) is None
    assert sessions.session_of_window("@8") is None


def test_ambiguous_resume_claims_never_fall_through_to_the_registry(fleet, tmp_path):
    """Two panes' command lines BOTH say `--resume S8`, and only @8's registry entry names S8.
    The command line is the stronger signal and it is ambiguous, so the answer is None — the
    registry must not be consulted to break the tie. (Control: with one `--resume` claimant
    that claimant wins; with none, the registry names @8.)"""
    _proc(tmp_path / "proc", 909, "7777")
    _registry(909, S45, "@9", "7777")
    p8 = sessions.Pane("@8", HOME, "claude", 808, HOME, S8, 1.0)
    p9 = sessions.Pane("@9", HOME, "claude", 909, HOME, S8, 1.0)
    assert sessions.wid_claiming_session(S8, {"@9": p9}) == "@9", "control: one claimant"
    assert sessions.wid_claiming_session(S8, {"@8": fleet["@8"]}) == "@8", "control: registry"
    assert sessions.wid_claiming_session(S8, {"@8": p8, "@9": p9}) is None


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setenv("CHELA_INBOX_FILE", str(tmp_path / "inbox.json"))
    monkeypatch.setenv("CHELA_EVENTS_FILE", str(tmp_path / "events.jsonl"))
    monkeypatch.delenv("CHELA_ORCHESTRATOR_WID", raising=False)
    monkeypatch.delenv("CHELA_WID", raising=False)
    monkeypatch.setattr(epoch, "current", lambda: "1-1")
    monkeypatch.setattr(inbox.discovery, "get_windows_by_id",
                        lambda: {"@8": "liav", "@45": "liav"})


def test_chela_watch_records_the_identity_from_CHELA_WID_and_the_registry(
        store, tmp_path, monkeypatch):
    """`chela watch` from `@8` where the pane walk found no claude pid at all: the caller's own
    ancestry finds its claude process, and the registry entry — which ALSO says `@8` — names
    the session. Before, this registered `@8` with self-heal disarmed."""
    proc = tmp_path / "proc"
    monkeypatch.setattr(sessions, "PROC", proc)
    _proc(proc, 808, "5656")
    _registry(808, S8, "@8", "5656")
    monkeypatch.setattr(sessions, "panes", lambda force=False: {
        "@8": sessions.Pane("@8", HOME, "claude", None, None, None, None)})
    monkeypatch.setattr(sessions, "own_claude_pid", lambda pid=None: 808)
    monkeypatch.setenv("CHELA_WID", "@8")

    result = inbox.register("@8")

    assert result["session"] == S8
    assert inbox.orchestrator_session(inbox.load()) == S8


def test_a_stale_CHELA_WID_does_not_borrow_the_callers_session(store, tmp_path, monkeypatch):
    """The caller's registry entry says it runs in `@45`, but its inherited `$CHELA_WID`
    says `@8` (CMX-192). That is not `@8`'s identity: nothing is recorded."""
    proc = tmp_path / "proc"
    monkeypatch.setattr(sessions, "PROC", proc)
    _proc(proc, 4545, "31815")
    _registry(4545, S45, "@45", "31815")
    monkeypatch.setattr(sessions, "panes", lambda force=False: {
        "@8": sessions.Pane("@8", HOME, "claude", None, None, None, None)})
    monkeypatch.setattr(sessions, "own_claude_pid", lambda pid=None: 4545)
    monkeypatch.setenv("CHELA_WID", "@8")

    assert inbox.register("@8")["session"] is None


# --- orchestrator.moved ----------------------------------------------------------------

def _pinned(session: str | None = S8) -> None:
    inbox.save({**inbox._empty(), "orchestrator": "@8", "orchestrator_epoch": "1-1",
                "orchestrator_session": session, "orchestrator_name": "liav"})


def _moved_records() -> list[dict]:
    return [e for e in event_log.read()["events"] if e["type"] == inbox.MOVED_KIND]


def test_a_watch_that_takes_the_pin_from_a_LIVE_orchestrator_is_announced_once(
        store, monkeypatch):
    """The actual 2026-09-29 mechanism: a SessionStart hook ran `chela watch` in the
    restarted sibling, which silently took the pin from the still-live orchestrator. Now the
    takeover is queued for the new pin once and recorded in the Feed."""
    monkeypatch.setattr(inbox.sessions, "session_of_window",
                        lambda wid, pane_map=None: {"@8": S8, "@45": S45}.get(wid))
    _pinned()

    inbox.register("@45")

    store_now = inbox.load()
    assert store_now["orchestrator"] == "@45"
    moved = [e for e in store_now["queue"] if e["kind"] == inbox.MOVED_KIND]
    assert len(moved) == 1 and moved[0]["payload"]["reason"] == "taken_over"
    assert moved[0]["payload"]["old"] == "@8" and moved[0]["payload"]["new"] == "@45"
    assert len(_moved_records()) == 1


def test_re_registering_or_recovering_a_dead_address_is_not_a_move(store, monkeypatch):
    """The orchestrator re-running `chela watch` in its own window, or claiming the pin after
    the old window is gone, is the normal path — no notice."""
    monkeypatch.setattr(inbox.sessions, "session_of_window",
                        lambda wid, pane_map=None: {"@8": S8, "@45": S45}.get(wid))
    _pinned()
    inbox.register("@8")
    assert _moved_records() == [] and inbox.load()["queue"] == []

    monkeypatch.setattr(inbox.discovery, "get_windows_by_id", lambda: {"@45": "liav"})
    inbox.register("@45")
    assert _moved_records() == [] and inbox.load()["queue"] == []


def test_a_registration_not_made_by_a_watch_from_that_window_is_announced(store, monkeypatch):
    """The dashboard's take-over is not the window claiming itself: announced, reason kept."""
    monkeypatch.setattr(inbox.sessions, "session_of_window", lambda wid, pane_map=None: None)
    monkeypatch.setattr(inbox.discovery, "get_windows_by_id", lambda: {"@45": "liav"})
    _pinned()

    inbox.register("@45", source="dashboard")

    assert [e["payload"]["reason"] for e in _moved_records()] == ["dashboard"]


def test_chela_restore_readdressing_the_pin_is_announced(store, monkeypatch):
    """`chela restore --apply` moves the pin with no `chela watch` from the new window."""
    monkeypatch.setattr(inbox.sessions, "session_of_window", lambda wid, pane_map=None: S8)
    _pinned()

    assert inbox.readdress("@8", "1-1", "@45")["ok"]

    assert [e["payload"]["reason"] for e in _moved_records()] == ["restore"]
    assert [e["kind"] for e in inbox.load()["queue"]] == [inbox.MOVED_KIND]


def test_a_restore_that_re_stamps_the_SAME_window_is_not_announced(store, monkeypatch):
    """A tmux restart reissued `@8` to the orchestrator's own session, so `chela restore
    --apply` re-addresses `@8` to `@8` under a fresh epoch. The pin did not move: no
    "moved @8 to @8" notice, in the queue or the Feed. (Control: the same restore to a
    DIFFERENT window is announced — `test_chela_restore_readdressing_the_pin_is_announced`.)"""
    monkeypatch.setattr(inbox.sessions, "session_of_window", lambda wid, pane_map=None: S8)
    monkeypatch.setattr(inbox.epoch, "current", lambda: "2-2")
    _pinned()

    out = inbox.readdress("@8", "1-1", "@8")

    assert out["ok"] and out["orchestrator"] == "@8" and out["epoch"] == "2-2"
    assert inbox.load()["orchestrator_epoch"] == "2-2", "the re-stamp itself happened"
    assert _moved_records() == [] and inbox.load()["queue"] == []


def test_the_same_session_watching_from_a_new_window_is_not_a_takeover(store, monkeypatch):
    """`@8` is still listed (not yet reaped), and the orchestrator's OWN session S8 runs
    `chela watch` from `@60` after a resume into a new window. Same identity ⇒ no notice.
    (Control: a DIFFERENT session doing the same from `@45` is announced.)"""
    monkeypatch.setattr(inbox.discovery, "get_windows_by_id",
                        lambda: {"@8": "liav", "@45": "liav", "@60": "liav"})
    monkeypatch.setattr(inbox.sessions, "session_of_window",
                        lambda wid, pane_map=None: {"@8": S8, "@45": S45, "@60": S8}.get(wid))
    _pinned()
    inbox.register("@60")
    assert inbox.load()["orchestrator"] == "@60"
    assert _moved_records() == [] and inbox.load()["queue"] == []

    _pinned()
    inbox.register("@45")
    assert [e["payload"]["reason"] for e in _moved_records()] == ["taken_over"], "control"


def test_a_takeover_of_a_live_pin_with_no_recorded_identity_is_still_announced(
        store, monkeypatch):
    """The pin on live `@8` carries NO recorded session, and `@45`'s identity is unknown too.
    Two unknowns are not the same session: `@45` taking the pin is a takeover, announced."""
    monkeypatch.setattr(inbox.sessions, "session_of_window", lambda wid, pane_map=None: None)
    _pinned(session=None)

    inbox.register("@45")

    assert inbox.load()["orchestrator"] == "@45"
    assert [e["payload"]["reason"] for e in _moved_records()] == ["taken_over"]


def test_a_dispatch_watch_that_takes_the_pin_from_a_LIVE_orchestrator_is_announced(
        store, monkeypatch):
    """The `chela watch <wid>` path (``watch(wid, by=...)``, what every dispatch runs), not the
    bare `chela watch` (``register``): `@45` watches `@60` and in doing so takes the pin from the
    still-live `@8`. That is the same takeover, and it must be announced the same way.
    (Control: `@8` itself watching `@60` re-registers its own pin — no notice.)"""
    monkeypatch.setattr(inbox.discovery, "get_windows_by_id",
                        lambda: {"@8": "liav", "@45": "liav", "@60": "worker"})
    monkeypatch.setattr(inbox.sessions, "session_of_window",
                        lambda wid, pane_map=None: {"@8": S8, "@45": S45}.get(wid))
    _pinned()
    assert inbox.watch("@60", "control", by="@8")["ok"]
    assert _moved_records() == [] and inbox.load()["queue"] == [], "control"

    _pinned()
    assert inbox.watch("@60", "dispatch", by="@45")["ok"]

    store_now = inbox.load()
    assert store_now["orchestrator"] == "@45"
    moved = [e for e in store_now["queue"] if e["kind"] == inbox.MOVED_KIND]
    assert [e["payload"]["reason"] for e in moved] == ["taken_over"]
    assert [(e["payload"]["old"], e["payload"]["new"]) for e in _moved_records()] == [("@8", "@45")]
