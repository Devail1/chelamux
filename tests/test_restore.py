"""``chela restore`` — the report for every epoch-stamped row a hard tmux death orphaned
in the three stores CMX-82's inbox self-heal does not reach: inbox ``watches``, the
dispatcher's ``runs`` table (agent + judge window stamps), and ``session-ids.json``.

The scanner tests are pure — no live tmux, no sqlite, no filesystem. See
``chela/restore.py`` for why scanning is report-only (never touches a store, never
relaunches/spawns/resumes).

``plan`` (CMX-195 objective 2) classifies the three SESSION-stamped
stores (inbox orchestrator, telegram-bindings, session-ids). ``_classify``/``plan`` are
exercised purely (DI'd roster lookup + ``wid_for_session``); nothing in this module writes,
and ``tests/test_restore_cli.py`` guards that end-to-end on the real store bytes.
"""
from __future__ import annotations



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


# 🔴 GUARDS (CMX-195 round 14): `manual_command()`'s TWO None arms, independently.
#
# The docstring states the invariant verbatim — "None when there isn't enough to build one
# (no cwd, or no session to resume)" — and it is two conditions, not one. Every earlier
# None-expecting test had BOTH halves missing (the telegram.bindings row carries neither),
# so dropping either check alone changed nothing observable. A one-liner built from a row
# with no cwd would print `cd None && ...`; one with no session, `--resume None`.

def _v(**kw):
    base = dict(store="session-ids", wid="@1", stamped_epoch=OLD, verdict="MANUAL",
                session_id="sid-1", new_wid=None, cwd="/home/x", label="l")
    base.update(kw)
    return Verdict(**base)


def test_manual_command_is_None_without_a_CWD_even_when_the_session_is_known():
    assert _v(cwd=None).manual_command() is None
    assert _v(cwd="").manual_command() is None


def test_manual_command_is_None_without_a_SESSION_even_when_the_cwd_is_known():
    assert _v(session_id=None).manual_command() is None
    assert _v(session_id="").manual_command() is None


def test_manual_command_is_built_when_BOTH_halves_are_present():
    """The counterweight: returning None unconditionally would satisfy both guards above."""
    cmd = _v(cwd="/home/liav/p", session_id="sid-9").manual_command()
    assert cmd == "cd /home/liav/p && CHELA_WID=@N claude --resume sid-9"


def test_manual_command_is_None_for_a_REVIVABLE_row():
    """A REVIVABLE row is re-registered, not relaunched — it must never carry the one-liner."""
    assert _v(verdict="REVIVABLE", new_wid="@9").manual_command() is None
