"""``chela restore`` — the report for every epoch-stamped row a hard tmux death orphaned
in the three stores CMX-82's inbox self-heal does not reach: inbox ``watches``, the
dispatcher's ``runs`` table (agent + judge window stamps), and ``session-ids.json``.

The scanner tests are pure — no live tmux, no sqlite, no filesystem. See
``chela/restore.py`` for why scanning is report-only (never touches a store, never
relaunches/spawns/resumes).

``plan``/``apply`` (CMX-195 objectives 2/3) classify + act on the three SESSION-stamped
stores (inbox orchestrator, telegram-bindings, session-ids): ``_classify``/``plan`` are
exercised purely (DI'd roster lookup + ``wid_for_session``); ``apply`` is exercised against
temp files the same way ``tests/test_sessionids.py`` and ``tests/test_inbox.py`` do, since
it is the one function in this module allowed to write.
"""
from __future__ import annotations

import importlib

import pytest

from chela.restore import (
    Orphan,
    Verdict,
    _classify,
    plan,
    scan_all,
    scan_runs,
    scan_session_ids,
    scan_watches,
)

OLD = "786-1784045825"        # the tmux server that was OOM-killed
NEW = "9001-1784099999"       # the one that came back, numbering from @0 again


# --------------------------------------------------------------------------
# inbox.json watches
# --------------------------------------------------------------------------

def test_scan_watches_flags_a_dangling_stamp():
    watches = {"@3": {"note": "reviewing cmx-41", "epoch": OLD}}
    orphans = scan_watches(watches, NEW)
    assert orphans == [Orphan("inbox.watches", "@3", "reviewing cmx-41", OLD)]


def test_scan_watches_ignores_a_current_stamp():
    watches = {"@3": {"note": "reviewing cmx-41", "epoch": NEW}}
    assert scan_watches(watches, NEW) == []


def test_scan_watches_falls_back_to_name_with_no_note():
    watches = {"@3": {"note": "", "name": "cmx-41", "epoch": OLD}}
    orphans = scan_watches(watches, NEW)
    assert orphans[0].label == "cmx-41"


def test_scan_watches_empty_store():
    assert scan_watches({}, NEW) == []
    assert scan_watches(None, NEW) == []


# --------------------------------------------------------------------------
# dispatcher runs table (agent + judge window stamps)
# --------------------------------------------------------------------------

def test_scan_runs_flags_a_dangling_agent_window():
    runs = [{"task_id": "cmx-77", "status": "running", "window_id": "@9",
             "window_epoch": OLD}]
    orphans = scan_runs(runs, NEW)
    assert orphans == [Orphan("dispatcher.runs", "@9", "cmx-77 (running)", OLD)]


def test_scan_runs_flags_a_dangling_judge_window_independently():
    # The agent's own window survived (current epoch); only its judge orphaned.
    runs = [{"task_id": "cmx-77", "status": "awaiting_review",
             "window_id": "@9", "window_epoch": NEW,
             "judge_window_id": "@10", "judge_window_epoch": OLD,
             "judge_state": "running"}]
    orphans = scan_runs(runs, NEW)
    assert orphans == [Orphan("dispatcher.runs (judge)", "@10", "cmx-77 judge (running)", OLD)]


def test_scan_runs_row_can_orphan_on_both_halves():
    runs = [{"task_id": "cmx-77", "status": "running", "window_id": "@9",
             "window_epoch": OLD, "judge_window_id": "@10",
             "judge_window_epoch": OLD, "judge_state": "running"}]
    orphans = scan_runs(runs, NEW)
    assert {o.wid for o in orphans} == {"@9", "@10"}


def test_scan_runs_ignores_current_and_unstamped_rows():
    runs = [
        {"task_id": "cmx-1", "status": "running", "window_id": "@1", "window_epoch": NEW},
        {"task_id": "cmx-2", "status": "running", "window_id": "@2", "window_epoch": None},
        {"task_id": "cmx-3", "status": "done", "window_id": None, "window_epoch": None},
    ]
    assert scan_runs(runs, NEW) == []


def test_scan_runs_empty():
    assert scan_runs([], NEW) == []
    assert scan_runs(None, NEW) == []


# --------------------------------------------------------------------------
# session-ids.json
# --------------------------------------------------------------------------

def test_scan_session_ids_flags_a_dangling_entry():
    entries = {"@5": {"session_id": "abc-123", "epoch": OLD}}
    orphans = scan_session_ids(entries, NEW)
    assert orphans == [Orphan("session-ids", "@5", "abc-123", OLD)]


def test_scan_session_ids_ignores_a_current_entry():
    entries = {"@5": {"session_id": "abc-123", "epoch": NEW}}
    assert scan_session_ids(entries, NEW) == []


def test_scan_session_ids_empty():
    assert scan_session_ids({}, NEW) == []
    assert scan_session_ids(None, NEW) == []


# --------------------------------------------------------------------------
# an unreadable current epoch accuses NOTHING (unknown, not stale)
# --------------------------------------------------------------------------

def test_unknown_now_epoch_never_flags_anything():
    """`chela restore` must report CANNOT VERIFY, never a false orphan, when tmux itself
    cannot be asked — an unstamped comparison is unknown, not proof of staleness
    (chela/epoch.py::is_dangling).
    """
    watches = {"@3": {"note": "x", "epoch": OLD}}
    runs = [{"task_id": "cmx-1", "status": "running", "window_id": "@9", "window_epoch": OLD}]
    entries = {"@5": {"session_id": "abc", "epoch": OLD}}
    assert scan_all(watches, runs, entries, None) == []


# --------------------------------------------------------------------------
# scan_all combines all three stores, in order
# --------------------------------------------------------------------------

def test_scan_all_combines_every_store():
    watches = {"@3": {"note": "watch", "epoch": OLD}}
    runs = [{"task_id": "cmx-1", "status": "running", "window_id": "@9", "window_epoch": OLD}]
    entries = {"@5": {"session_id": "abc", "epoch": OLD}}
    orphans = scan_all(watches, runs, entries, NEW)
    assert [o.store for o in orphans] == [
        "inbox.watches", "dispatcher.runs", "session-ids",
    ]


def test_scan_all_nothing_orphaned():
    assert scan_all({}, [], {}, NEW) == []


# --------------------------------------------------------------------------
# _classify — REVIVABLE / MANUAL, via a DI'd roster lookup + wid_for_session
# --------------------------------------------------------------------------

def test_classify_revivable_when_the_session_is_live_elsewhere():
    def roster_lookup(dead_epoch, wid):
        return {"cwd": "/proj", "name": "cmx-9", "session_id": "sid-1"}

    def wid_for_session(sid):
        return "@42" if sid == "sid-1" else None

    v = _classify("session-ids", "@5", OLD, "sid-1", NEW, roster_lookup, wid_for_session)
    assert v == Verdict("session-ids", "@5", OLD, "REVIVABLE", "sid-1", "@42", "/proj", "cmx-9")


def test_classify_manual_when_nothing_live_claims_the_session():
    def roster_lookup(dead_epoch, wid):
        return {"cwd": "/proj", "name": "cmx-9", "session_id": "sid-1"}

    def wid_for_session(sid):
        return None

    v = _classify("session-ids", "@5", OLD, "sid-1", NEW, roster_lookup, wid_for_session)
    assert v.verdict == "MANUAL"
    assert v.new_wid is None
    assert v.manual_command() == "cd /proj && CHELA_WID=@N claude --resume sid-1"


def test_classify_session_id_is_the_only_automatic_path():
    """⭐ A cwd/name match must never substitute for `wid_for_session` — a row whose roster
    row shows a matching cwd/name but whose session is not live anywhere is still MANUAL.
    Scoped to `restore.py`; this asserts nothing about `inbox.resolve_heal`'s separate,
    deliberately narrower unique-name authority."""
    def roster_lookup(dead_epoch, wid):
        return {"cwd": "/home/x/proj", "name": "cmx-9", "session_id": "sid-1"}

    def wid_for_session(sid):
        return None       # sid-1 is not live anywhere

    v = _classify("session-ids", "@5", OLD, "sid-1", NEW, roster_lookup, wid_for_session)
    assert v.verdict == "MANUAL"


def test_classify_falls_back_to_the_roster_session_id_when_the_row_carries_none():
    """`telegram-bindings.json` stamps no session id of its own — the roster join supplies
    it (this is why `plan` needs the roster at all for that store)."""
    def roster_lookup(dead_epoch, wid):
        return {"cwd": "/proj", "name": "x", "session_id": "sid-9"}

    def wid_for_session(sid):
        return "@7" if sid == "sid-9" else None

    v = _classify("telegram.bindings", "@2", OLD, None, NEW, roster_lookup, wid_for_session)
    assert v.verdict == "REVIVABLE"
    assert v.new_wid == "@7"


def test_classify_not_dangling_returns_none():
    assert _classify("session-ids", "@5", NEW, "sid-1", NEW, lambda *a: None,
                     lambda sid: "@9") is None


def test_classify_no_session_anywhere_is_manual_with_no_command():
    v = _classify("session-ids", "@5", OLD, None, NEW, lambda *a: {}, lambda sid: None)
    assert v.verdict == "MANUAL"
    assert v.manual_command() is None


# --------------------------------------------------------------------------
# plan — walks the three session-stamped stores
# --------------------------------------------------------------------------

def test_plan_unknown_epoch_classifies_nothing():
    """GUARD: `epoch.current()` unknown must classify NOTHING — the same two-known-halves
    rule `epoch.is_dangling` itself follows."""
    orchestrator = {"orchestrator": "@0", "orchestrator_epoch": OLD, "orchestrator_session": "sid"}
    bindings = {"@1": OLD}
    entries = {"@5": {"session_id": "sid-2", "epoch": OLD}}
    assert plan(orchestrator, bindings, entries, None) == []


def test_plan_classifies_across_all_three_stores():
    orchestrator = {"orchestrator": "@0", "orchestrator_epoch": OLD,
                    "orchestrator_session": "orch-sid"}
    bindings = {"@1": OLD}
    entries = {"@5": {"session_id": "sess-sid", "epoch": OLD}}

    def roster_lookup(_dead_epoch, wid):
        return {"@1": {"cwd": "/b", "name": "b"}}.get(wid, {})

    def wid_for_session(sid):
        return {"orch-sid": "@10"}.get(sid)

    verdicts = plan(orchestrator, bindings, entries, NEW, roster_lookup, wid_for_session)
    by_store = {v.store: v for v in verdicts}
    assert set(by_store) == {"inbox.orchestrator", "telegram.bindings", "session-ids"}
    assert by_store["inbox.orchestrator"].verdict == "REVIVABLE"
    assert by_store["inbox.orchestrator"].new_wid == "@10"
    assert by_store["telegram.bindings"].verdict == "MANUAL"
    assert by_store["session-ids"].verdict == "MANUAL"


def test_plan_no_orchestrator_registered_is_skipped():
    assert plan({}, {}, {}, NEW, lambda *a: {}, lambda sid: None) == []


def test_plan_ignores_current_epoch_rows():
    orchestrator = {"orchestrator": "@0", "orchestrator_epoch": NEW, "orchestrator_session": "s"}
    bindings = {"@1": NEW}
    entries = {"@5": {"session_id": "s2", "epoch": NEW}}
    assert plan(orchestrator, bindings, entries, NEW, lambda *a: {}, lambda sid: "@9") == []


# --------------------------------------------------------------------------
# apply — the only function in this module that writes; against temp files
# --------------------------------------------------------------------------

@pytest.fixture()
def apply_env(tmp_path, monkeypatch):
    """A temp CHELA_DIR + inbox file + bindings file, so `apply()`'s real writes never
    touch `~/.chela`. `sessionids`/`roster` cache their store path at import time (like
    `tests/test_sessionids.py`), so they're reloaded; `inbox`/bindings read their path from
    an env var on every call and need no reload."""
    monkeypatch.setenv("CHELA_DIR", str(tmp_path / "chela"))
    monkeypatch.setenv("CHELA_INBOX_FILE", str(tmp_path / "chela" / "inbox.json"))
    monkeypatch.setenv("CHELA_TELEGRAM_BINDINGS", str(tmp_path / "chela" / "telegram-bindings.json"))
    monkeypatch.delenv("CHELA_ORCHESTRATOR_WID", raising=False)

    import chela.config as config
    importlib.reload(config)
    import chela.sessionids as sessionids_mod
    importlib.reload(sessionids_mod)
    import chela.roster as roster_mod
    importlib.reload(roster_mod)
    import chela.inbox as inbox_mod
    monkeypatch.setattr(inbox_mod, "INBOX_ENABLED", True)
    import chela.restore as restore_mod

    return {"sessionids": sessionids_mod, "roster": roster_mod, "inbox": inbox_mod,
            "restore": restore_mod}


def test_apply_revivable_session_ids_restamps_to_the_new_wid_and_current_epoch(apply_env, monkeypatch):
    """GUARD: after apply(), the row in the store names @new and the current epoch — never
    just "apply returned truthy"."""
    sessionids_mod = apply_env["sessionids"]
    monkeypatch.setattr(sessionids_mod.epoch, "current", lambda: OLD)
    sessionids_mod.set_session_id("@5", "sess-sid")
    monkeypatch.setattr(sessionids_mod.epoch, "current", lambda: NEW)   # server restarted

    v = Verdict("session-ids", "@5", OLD, "REVIVABLE", "sess-sid", "@42", None, "")
    apply_env["restore"].apply([v], NEW)

    entries = sessionids_mod.entries()
    assert "@5" not in entries
    assert entries["@42"] == {"session_id": "sess-sid", "epoch": NEW}


def test_apply_manual_session_ids_row_is_archived_and_removed(apply_env, monkeypatch):
    """GUARD: both halves — a row that disappears unarchived is data loss."""
    sessionids_mod = apply_env["sessionids"]
    roster_mod = apply_env["roster"]
    monkeypatch.setattr(sessionids_mod.epoch, "current", lambda: OLD)
    sessionids_mod.set_session_id("@5", "sess-sid")
    monkeypatch.setattr(sessionids_mod.epoch, "current", lambda: NEW)

    v = Verdict("session-ids", "@5", OLD, "MANUAL", "sess-sid", None, "/proj", "cmx-9")
    apply_env["restore"].apply([v], NEW)

    assert "@5" not in sessionids_mod.entries()                 # removed
    archived = roster_mod.window(OLD, "@5")
    assert archived is not None                                 # ...and archived
    assert archived["session_id"] == "sess-sid"
    assert archived["cwd"] == "/proj"
    assert archived["archived"] is True


def test_apply_revivable_orchestrator_restamps_via_register(apply_env, monkeypatch):
    inbox_mod = apply_env["inbox"]
    monkeypatch.setattr(inbox_mod.discovery, "get_windows_by_id", lambda: {"@42": "orch"})
    monkeypatch.setattr(inbox_mod.sessions, "session_of_window",
                        lambda wid, pane_map=None: "orch-sid")
    monkeypatch.setattr(inbox_mod.epoch, "current", lambda: NEW)

    v = Verdict("inbox.orchestrator", "@0", OLD, "REVIVABLE", "orch-sid", "@42", None, "")
    apply_env["restore"].apply([v], NEW)

    store = inbox_mod.load()
    assert store["orchestrator"] == "@42"
    assert store["orchestrator_epoch"] == NEW


def test_apply_manual_orchestrator_is_archived_and_unregistered(apply_env):
    inbox_mod = apply_env["inbox"]
    roster_mod = apply_env["roster"]
    with inbox_mod.locked_store() as store:
        store["orchestrator"] = "@0"
        store["orchestrator_epoch"] = OLD
        store["orchestrator_session"] = "orch-sid"
        store["orchestrator_name"] = "orch"

    v = Verdict("inbox.orchestrator", "@0", OLD, "MANUAL", "orch-sid", None, "/proj", "orch")
    apply_env["restore"].apply([v], NEW)

    store = inbox_mod.load()
    assert store["orchestrator"] is None
    archived = roster_mod.window(OLD, "@0")
    assert archived["session_id"] == "orch-sid"


def test_apply_revivable_bindings_rebinds_the_same_thread_to_the_new_wid(apply_env):
    from chela.telegram.bindings import BindingRegistry

    reg = BindingRegistry.load()
    reg.bind("@1", "1001", epoch=OLD)
    reg.save()

    v = Verdict("telegram.bindings", "@1", OLD, "REVIVABLE", "sess-sid", "@9", None, "")
    apply_env["restore"].apply([v], NEW)

    reg2 = BindingRegistry.load()
    assert reg2.window_for_thread("1001") == "@9"
    assert reg2.thread_for_window("@1") is None


def test_apply_manual_bindings_unbinds_and_archives(apply_env):
    from chela.telegram.bindings import BindingRegistry

    reg = BindingRegistry.load()
    reg.bind("@1", "1001", epoch=OLD)
    reg.save()

    v = Verdict("telegram.bindings", "@1", OLD, "MANUAL", None, None, "/proj", "")
    apply_env["restore"].apply([v], NEW)

    reg2 = BindingRegistry.load()
    assert reg2.thread_for_window("@1") is None
    assert apply_env["roster"].window(OLD, "@1") is not None


def test_apply_returns_revived_and_archived_lists(apply_env, monkeypatch):
    inbox_mod = apply_env["inbox"]
    monkeypatch.setattr(inbox_mod.discovery, "get_windows_by_id", lambda: {"@9": "x"})
    monkeypatch.setattr(inbox_mod.sessions, "session_of_window", lambda wid, pane_map=None: "sid")
    monkeypatch.setattr(inbox_mod.epoch, "current", lambda: NEW)

    revivable = Verdict("inbox.orchestrator", "@0", OLD, "REVIVABLE", "sid", "@9", None, "")
    manual = Verdict("session-ids", "@5", OLD, "MANUAL", "s2", None, "/proj", "")
    result = apply_env["restore"].apply([revivable, manual], NEW)
    assert result["revived"] == [revivable]
    assert result["archived"] == [manual]
