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
    ARCHIVED,
    LEFT_TO_DAEMON,
    RACED,
    REVIVED,
    Orphan,
    Verdict,
    _classify,
    apply,
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


# 🔴 GUARDS (CMX-195 round 17): `_classify`'s fallback CHAIN has a direction.
#
# `sid = session_id or roster_row.get("session_id")` — the row's OWN session id wins, and
# the roster is the fallback for stores that carry none of their own (telegram-bindings
# stamps no session; that is stated as the reason plan() needs the roster at all). Every
# prior test either had both halves agreeing or only one present, so the precedence was
# never observable and passing `None` for the row's own id changed nothing.

def _cls(session_id, roster_row, wid_for_session=lambda sid: None):
    return _classify("session-ids", "@5", OLD, session_id, NEW,
                     lambda *a, **k: roster_row, wid_for_session)


def test_the_rows_OWN_session_id_wins_over_the_rosters():
    v = _cls("sid-from-the-row", {"session_id": "sid-from-the-roster", "cwd": "/x"})
    assert v.session_id == "sid-from-the-row", (
        "the store's own session id is the source; the roster is only a fallback"
    )


def test_the_roster_supplies_the_session_when_the_row_carries_none():
    """The fallback direction — this is what a telegram-bindings row depends on entirely."""
    v = _cls(None, {"session_id": "sid-from-the-roster", "cwd": "/x"})
    assert v.session_id == "sid-from-the-roster"


def test_a_rows_own_session_id_alone_can_make_it_REVIVABLE():
    """⭐ The consequence, not just the field: drop the row's own id and a session-ids row
    whose roster entry is missing (or predates the snapshot) can never be REVIVABLE."""
    v = _cls("sid-live", {"cwd": "/x"}, wid_for_session=lambda sid: "@42" if sid == "sid-live" else None)
    assert v.verdict == "REVIVABLE" and v.new_wid == "@42"


def test_no_session_anywhere_is_MANUAL_not_a_crash():
    v = _cls(None, {"cwd": "/x"})
    assert v.verdict == "MANUAL" and v.session_id is None


def test_plan_HANDS_classify_the_session_ids_rows_own_session_id():
    """🔴 The call site, not the function. The four guards above pin `_classify`'s fallback
    chain — and all four pass when `plan()` hands it `None` for the row's own id, because
    they call `_classify` directly. This drives `plan`, with a roster row carrying NO
    session of its own, so the store's id is the only thing that can make the row REVIVABLE.

    ⚠️ Same shape as round 5's lesson one level in: a guarded function is not a guarded call.
    """
    entries = {"@5": {"session_id": "sid-live", "epoch": OLD}}
    verdicts = plan({}, {}, entries, NEW,
                    roster_lookup=lambda *a, **k: {"cwd": "/home/x"},   # no session_id
                    wid_for_session=lambda sid: "@42" if sid == "sid-live" else None)

    assert len(verdicts) == 1
    assert verdicts[0].verdict == "REVIVABLE" and verdicts[0].new_wid == "@42", (
        "plan() must pass the session-ids row's OWN session id to _classify — with the "
        "roster carrying none, dropping it makes every such row MANUAL forever"
    )


# 🔴 GUARDS (CMX-195 round 19): the session argument at ALL THREE of plan()'s call sites.
#
# `_classify(store, wid, stamped, session_id, ...)` resolves `session_id or roster.session_id`,
# so what each arm PASSES decides which source wins — and the three arms pass three different
# things on purpose:
#
#   inbox.orchestrator -> the store's own `orchestrator_session`
#   telegram.bindings  -> None, deliberately: that file stamps no session, so the roster
#                         fallback is the ONLY way such a row can ever be REVIVABLE
#   session-ids        -> the row's own `session_id`  (guarded round 17)
#
# Round 17 closed the third; the judge then filed the second. Closing all three together —
# the same two-member-class lesson that has now recurred five times.

_ROSTER_SID = {"session_id": "sid-in-the-roster", "cwd": "/home/x"}


def _plan_one(**kw):
    """plan() with a roster that always carries `sid-in-the-roster`, live under @42."""
    return plan(kw.get("orchestrator", {}), kw.get("bindings", {}),
                kw.get("entries", {}), NEW,
                roster_lookup=lambda *a, **k: _ROSTER_SID,
                wid_for_session=lambda sid: "@42" if sid == "sid-in-the-roster" else None)


def test_the_bindings_arm_passes_NO_session_so_the_roster_fallback_supplies_one():
    """⭐ telegram-bindings.json stamps no session of its own — the roster is not a
    convenience here, it is the only source. Pass anything truthy in that slot and the
    `or` short-circuits, the roster is never consulted, and a bindings row can never be
    REVIVABLE no matter what the snapshot holds."""
    verdicts = _plan_one(bindings={"@2": OLD})

    assert len(verdicts) == 1
    assert verdicts[0].session_id == "sid-in-the-roster", (
        "the bindings arm must pass None so _classify's roster fallback runs"
    )
    assert verdicts[0].verdict == "REVIVABLE" and verdicts[0].new_wid == "@42"


def test_the_orchestrator_arm_passes_the_stores_OWN_recorded_session():
    """The inbox row DOES carry its own identity (`orchestrator_session`) — the row CMX-82
    self-heals from. Drop it and the roster silently stands in, which would make the two
    sources indistinguishable on the one row whose identity is recorded deliberately."""
    verdicts = _plan_one(orchestrator={
        "orchestrator": "@1", "orchestrator_epoch": OLD,
        "orchestrator_session": "sid-recorded-at-registration",
    })

    assert len(verdicts) == 1
    assert verdicts[0].session_id == "sid-recorded-at-registration", (
        "the orchestrator arm must pass the store's own session, not fall through to the roster"
    )


def test_the_session_ids_arm_passes_the_rows_OWN_session():
    """The third arm, kept beside its siblings so none can rot alone (round 17's finding)."""
    verdicts = _plan_one(entries={"@5": {"session_id": "sid-in-the-row", "epoch": OLD}})

    assert len(verdicts) == 1
    assert verdicts[0].session_id == "sid-in-the-row"


# 🔴 GUARDS (CMX-195 round 24): both `scan_runs` labels, and both `or '?'` fallbacks.
#
# `Orphan.label` is the only thing saying WHICH row a dangling `@N` is. Each half of a run
# row builds its own — `f"{task} ({status})"` and `f"{task} judge ({judge_state})"` — and
# each falls back to `'?'` when the state is absent. `'?'` is not decoration: it marks
# "chela does not know", which is a different report from a state that is genuinely blank.

def _runs_row(**kw):
    base = {"task_id": "abc123", "status": "running", "window_id": "@9",
            "window_epoch": OLD, "judge_window_id": "@10", "judge_window_epoch": OLD,
            "judge_state": "running"}
    base.update(kw)
    return [base]


def test_both_run_halves_label_themselves_with_their_state():
    orphans = scan_runs(_runs_row(), NEW)
    labels = {o.store: o.label for o in orphans}
    assert labels["dispatcher.runs"] == "abc123 (running)"
    assert labels["dispatcher.runs (judge)"] == "abc123 judge (running)"


def test_an_absent_state_renders_as_UNKNOWN_not_as_blank():
    """⭐ `or '?'` — a blank reads as "there is no state", which is a claim; `?` reads as
    "chela could not tell", which is the truth. Same distinction the CANNOT VERIFY arms
    carry, one surface down."""
    orphans = scan_runs(_runs_row(status=None, judge_state=None), NEW)
    labels = {o.store: o.label for o in orphans}
    assert labels["dispatcher.runs"] == "abc123 (?)"
    assert labels["dispatcher.runs (judge)"] == "abc123 judge (?)"


def test_an_absent_task_id_still_identifies_the_row_as_unknown():
    """The third `or '?'`-shaped fallback on the same function."""
    orphans = scan_runs(_runs_row(task_id=None), NEW)
    assert all(o.label.startswith("?") for o in orphans), (
        f"a row with no task id must say so, got {[o.label for o in orphans]}"
    )


# --------------------------------------------------------------------------
# apply — the write half (CMX-196): REVIVABLE re-stamped, MANUAL archived-then-removed
# --------------------------------------------------------------------------

def _writers(**overrides):
    """A DI kit for `apply()` recording every call it makes, in order — `calls` is the
    single source of truth every ordering/skip guard below reads."""
    calls = []
    kit = {
        "readdress_orchestrator": lambda wid, stamped, new: (
            calls.append(("readdress", wid, stamped, new)), {"ok": True})[1],
        "unregister_orchestrator": lambda wid, stamped: (
            calls.append(("unregister", wid, stamped)), {"ok": True})[1],
        "rekey_session": lambda wid, new, sid, stamped: (
            calls.append(("rekey", wid, new, sid, stamped)), True)[1],
        "remove_session": lambda wid, sid, stamped: (
            calls.append(("remove", wid, sid, stamped)), True)[1],
        "archive": lambda entry: calls.append(("archive", entry)),
    }
    kit.update(overrides)
    return calls, kit


def _revivable(store, wid="@1", new_wid="@42"):
    return Verdict(store=store, wid=wid, stamped_epoch=OLD, verdict="REVIVABLE",
                   session_id="sid-live", new_wid=new_wid, cwd="/home/x", label="l")


def _manual(store, wid="@1"):
    return Verdict(store=store, wid=wid, stamped_epoch=OLD, verdict="MANUAL",
                   session_id="sid-dead", new_wid=None, cwd="/home/x", label="l")


def test_apply_skips_telegram_bindings_entirely_never_writing_anything():
    calls, kit = _writers()
    v = _revivable("telegram.bindings")

    results = apply([v], **kit)

    assert calls == [], "telegram-bindings.json must NEVER be written by apply()"
    assert len(results) == 1 and results[0].action == LEFT_TO_DAEMON


def test_apply_skips_a_MANUAL_telegram_bindings_row_too():
    """The counterweight: the skip must not be conditioned on the verdict, only the store —
    a MANUAL bindings row must be just as untouched as a REVIVABLE one."""
    calls, kit = _writers()
    v = _manual("telegram.bindings")

    results = apply([v], **kit)

    assert calls == []
    assert results[0].action == LEFT_TO_DAEMON


def test_apply_revives_the_inbox_orchestrator_arm_via_readdress():
    calls, kit = _writers()
    v = _revivable("inbox.orchestrator", wid="@1", new_wid="@42")

    results = apply([v], **kit)

    assert calls == [("readdress", "@1", OLD, "@42")]
    assert results[0].action == REVIVED


def test_apply_revives_the_session_ids_arm_via_rekey():
    calls, kit = _writers()
    v = _revivable("session-ids", wid="@5", new_wid="@42")

    results = apply([v], **kit)

    assert calls == [("rekey", "@5", "@42", "sid-live", OLD)]
    assert results[0].action == REVIVED


def test_apply_reports_RACED_when_the_readdress_writer_declines():
    calls, kit = _writers(readdress_orchestrator=lambda *a: {"ok": False})
    v = _revivable("inbox.orchestrator")

    results = apply([v], **kit)

    assert results[0].action == RACED


def test_apply_reports_RACED_when_the_rekey_writer_declines():
    calls, kit = _writers(rekey_session=lambda *a: False)
    v = _revivable("session-ids")

    results = apply([v], **kit)

    assert results[0].action == RACED


def test_apply_archives_the_inbox_orchestrator_arm_THEN_unregisters():
    calls, kit = _writers()
    v = _manual("inbox.orchestrator", wid="@1")

    results = apply([v], **kit)

    assert [c[0] for c in calls] == ["archive", "unregister"], (
        "archive must land BEFORE the row is removed — reversed, a crash in between loses "
        "the row with no trace"
    )
    archive_entry = calls[0][1]
    assert archive_entry == {"store": "inbox.orchestrator", "wid": "@1",
                              "session_id": "sid-dead", "cwd": "/home/x",
                              "label": "l", "stamped_epoch": OLD}
    assert calls[1] == ("unregister", "@1", OLD)
    assert results[0].action == ARCHIVED


def test_apply_archives_the_session_ids_arm_THEN_removes():
    calls, kit = _writers()
    v = _manual("session-ids", wid="@5")

    results = apply([v], **kit)

    assert [c[0] for c in calls] == ["archive", "remove"]
    assert calls[1] == ("remove", "@5", "sid-dead", OLD)
    assert results[0].action == ARCHIVED


def test_apply_still_reports_RACED_after_archiving_when_removal_declines():
    """🔴 The archive already landed (it always does, unconditionally, before removal is
    even attempted) — but the row itself did not move, so the result must say RACED, not
    ARCHIVED, or an operator reading the report would believe the live store is clean."""
    calls, kit = _writers()
    kit["remove_session"] = lambda wid, sid, stamped: (
        calls.append(("remove", wid, sid, stamped)), False)[1]
    v = _manual("session-ids")

    results = apply([v], **kit)

    assert [c[0] for c in calls] == ["archive", "remove"], (
        "the archive call must still have happened, and BEFORE the declined removal"
    )
    assert results[0].action == RACED


def test_apply_reports_RACED_when_the_orchestrator_UNREGISTER_declines():
    """🔴 GUARD (round 5): the FOURTH writer's declines path — the last member of a
    four-writer class where the other three were covered.

        readdress          -> REVIVED / RACED   ✓
        rekey              -> REVIVED / RACED   ✓
        remove_session     -> ARCHIVED / RACED  ✓
        unregister_dangling-> ARCHIVED / ???    ← this

    `unregister_dangling` declines when the registration moved on since classification (a
    human re-registered, or a further restart reissued the wid). The archive already landed
    — unconditionally, before removal is attempted — but the row is STILL REGISTERED, so
    reporting ARCHIVED tells the operator the inbox is clean when it is not, on the one row
    the whole decisions inbox routes through.
    """
    calls, kit = _writers()
    kit["unregister_orchestrator"] = lambda wid, stamped: (
        calls.append(("unregister", wid, stamped)), {"ok": False, "wid": wid})[1]
    v = _manual("inbox.orchestrator", wid="@1")

    results = apply([v], **kit)

    assert [c[0] for c in calls] == ["archive", "unregister"], (
        "the archive must still have happened, and BEFORE the declined unregister"
    )
    assert results[0].action == RACED, (
        "a declined unregister leaves the row registered — ARCHIVED would be a lie"
    )


def test_apply_processes_every_verdict_in_order_one_result_each():
    calls, kit = _writers()
    verdicts = [_revivable("inbox.orchestrator", wid="@1"),
                _manual("session-ids", wid="@5"),
                _revivable("telegram.bindings", wid="@2")]

    results = apply(verdicts, **kit)

    assert len(results) == 3
    assert [r.verdict for r in results] == verdicts
    assert [r.action for r in results] == [REVIVED, ARCHIVED, LEFT_TO_DAEMON]


def test_apply_defaults_wire_to_the_real_inbox_sessionids_roster_modules():
    """🔴 GUARD (signature scan, same shape as round 8's `plan()` DI-default guard): with
    NO kwargs passed, `apply()`'s defaults must be the real production writers — the
    ones `chela restore --apply` actually calls — not a silently-inert no-op."""
    import inspect

    sig = inspect.signature(apply)
    from chela import inbox, roster, sessionids

    assert sig.parameters["readdress_orchestrator"].default is inbox.readdress
    assert sig.parameters["unregister_orchestrator"].default is inbox.unregister_dangling
    assert sig.parameters["rekey_session"].default is sessionids.rekey
    assert sig.parameters["remove_session"].default is sessionids.remove
    assert sig.parameters["archive"].default is roster.archive
