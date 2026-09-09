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



from chela import sessions
from chela.restore import (
    ARCHIVED,
    KEPT,
    LEFT_TO_DAEMON,
    RACED,
    RESUME_FAILED,
    RESUMED,
    REVIVED,
    SKIPPED,
    ApplyResult,
    Orphan,
    Verdict,
    _classify,
    _default_check_resumed,
    apply,
    plan,
    resume,
    retire_empty,
    scan_all,
    scan_runs,
    scan_session_ids,
    scan_watches,
)
from chela.spawn import SpawnResult

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


# --- CMX-261: a row whose trial is OVER is not a `chela restore` fixable orphan ---------
#
# `chela doctor`'s dead-epoch fact reported 112 stamped rows and pointed at `chela
# restore` — but `chela restore --apply`, run twice, never moved the count. The rows were
# all `done`/merged runs: `task-finished` kills the agent's tmux window as its LAST step,
# so a closed row's window stamp is EXPECTED to go dead the moment the epoch it was
# stamped under rolls over, and stays that way forever — `restore.plan`/`apply` have never
# classified this store at all (see the module docstring), so counting a terminal row here
# names a command that cannot, and never could, fix it.

def test_scan_runs_skips_a_row_whose_pr_MERGED():
    runs = [{"task_id": "cmx-77", "status": "awaiting_review", "pr_state": "merged",
             "window_id": "@9", "window_epoch": OLD}]
    assert scan_runs(runs, NEW) == []


def test_scan_runs_skips_a_DONE_row():
    runs = [{"task_id": "cmx-77", "status": "done", "window_id": "@9",
             "window_epoch": OLD, "judge_window_id": "@10", "judge_window_epoch": OLD,
             "judge_state": "clean"}]
    assert scan_runs(runs, NEW) == []


def test_scan_runs_skips_a_FAILED_row_that_exhausted_every_retry():
    from chela.dispatcher import MAX_ATTEMPTS
    runs = [{"task_id": "cmx-77", "status": "failed", "attempt": MAX_ATTEMPTS,
             "window_id": "@9", "window_epoch": OLD}]
    assert scan_runs(runs, NEW) == []


def test_scan_runs_still_flags_a_FAILED_row_with_retries_left():
    """The counterweight: a `failed` row below MAX_ATTEMPTS may still be re-claimed by
    the SAME task_id, so its dangling window is real, current-cycle information — not
    permanent historical noise — and must keep surfacing."""
    from chela.dispatcher import MAX_ATTEMPTS
    runs = [{"task_id": "cmx-77", "status": "failed", "attempt": MAX_ATTEMPTS - 1,
             "window_id": "@9", "window_epoch": OLD}]
    orphans = scan_runs(runs, NEW)
    assert orphans == [Orphan("dispatcher.runs", "@9", "cmx-77 (failed)", OLD)]


def test_scan_runs_tolerates_a_row_with_no_pr_state_or_attempt_at_all():
    """A row shaped like the minimal dicts elsewhere in this file (no `pr_state`/
    `attempt` key at all) must read as still-pending, not raise — `run_is_terminal`
    reads with `.get()` for exactly this."""
    runs = [{"task_id": "cmx-77", "status": "running", "window_id": "@9",
             "window_epoch": OLD}]
    orphans = scan_runs(runs, NEW)
    assert orphans == [Orphan("dispatcher.runs", "@9", "cmx-77 (running)", OLD)]


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
    # ⭐ ...and WHY. Blank, `=> raced` on a REVIVABLE row is indistinguishable from a
    # bug: nothing was written and nothing says the row simply moved on since plan().
    assert results[0].detail == "the row moved on before it could be re-stamped"


def test_apply_reports_RACED_when_the_rekey_writer_declines():
    calls, kit = _writers(rekey_session=lambda *a: False)
    v = _revivable("session-ids")

    results = apply([v], **kit)

    assert results[0].action == RACED
    # ⭐ ...and WHY. Blank, `=> raced` on a REVIVABLE row is indistinguishable from a
    # bug: nothing was written and nothing says the row simply moved on since plan().
    assert results[0].detail == "the row moved on before it could be re-stamped"


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
    # ⭐ ...and the detail must say the archive DID land. Blank, `=> raced` on a MANUAL row
    # is indistinguishable from one where nothing was preserved at all, and the operator
    # cannot tell whether the row still has a trace anywhere.
    assert results[0].detail == "archived, but the row moved on before it could be removed"


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
    assert results[0].detail == "archived, but the row moved on before it could be removed"


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


def test_the_four_outcome_words_are_the_operators_vocabulary():
    """🔴 GUARD (CMX-196 round 6): pin all FOUR outcome literals in one place.

    `--apply` prints these verbatim (`=> revived`, `=> archived`, ...), and three of them
    are pinned only INCIDENTALLY, by the CLI end-to-end asserting those literals. RACED is
    not, because the e2e fixture cannot produce a raced row: it needs the store to move
    between `plan()` and `apply()`, which a single-process test never does.

    ⚠️ Same fixture-data lesson as CMX-195: an outcome the fixture cannot REACH is unpinned
    no matter how thoroughly the other branches are asserted. Pinning the vocabulary here
    covers the one the e2e structurally cannot.
    """
    assert (LEFT_TO_DAEMON, REVIVED, ARCHIVED, RACED) == (
        "left-to-daemon", "revived", "archived", "raced")
    assert len({LEFT_TO_DAEMON, REVIVED, ARCHIVED, RACED}) == 4, (
        "the four outcomes must stay DISTINGUISHABLE — two that render the same word are "
        "one word as far as the operator is concerned"
    )


# --------------------------------------------------------------------------
# retire_empty — the narrow write half (CMX-323): MANUAL-with-nothing-on-record ONLY
# --------------------------------------------------------------------------

def _empty_manual(store, wid="@1"):
    """A MANUAL row `manual_command()` cannot build anything from — no cwd, no session."""
    return Verdict(store=store, wid=wid, stamped_epoch=OLD, verdict="MANUAL",
                   session_id=None, new_wid=None, cwd=None, label="l")


def test_retire_empty_archives_and_removes_a_session_ids_row_with_nothing_on_record():
    calls, kit = _writers()
    v = _empty_manual("session-ids", wid="@5")

    results = retire_empty([v], **kit)

    assert [c[0] for c in calls] == ["archive", "remove"], (
        "an empty MANUAL row must be retired through the exact same archive-then-remove "
        "path --apply uses"
    )
    assert results == [ApplyResult(v, ARCHIVED, "")]


def test_retire_empty_archives_and_unregisters_an_orchestrator_row_with_nothing_on_record():
    calls, kit = _writers()
    v = _empty_manual("inbox.orchestrator", wid="@1")

    results = retire_empty([v], **kit)

    assert [c[0] for c in calls] == ["archive", "unregister"]
    assert results == [ApplyResult(v, ARCHIVED, "")]


def test_retire_empty_never_writes_a_REVIVABLE_row_reports_it_KEPT():
    calls, kit = _writers()
    v = _revivable("session-ids", wid="@7", new_wid="@42")

    results = retire_empty([v], **kit)

    assert calls == [], (
        "a REVIVABLE row must never be re-stamped by retire_empty — that is --apply's job"
    )
    assert results == [ApplyResult(v, KEPT)]


def test_retire_empty_never_writes_a_MANUAL_row_that_still_carries_a_cwd_and_session():
    """The counterweight to the empty-row test: a MANUAL row with SOMETHING on record — a
    relaunch command a human could still read and act on — must be left completely alone,
    not silently swept up because it is also MANUAL."""
    calls, kit = _writers()
    v = _manual("session-ids", wid="@5")   # cwd="/home/x", session_id="sid-dead" — has a command
    assert v.manual_command() is not None, "fixture sanity: this row must carry a command"

    results = retire_empty([v], **kit)

    assert calls == [], "a MANUAL row with a cwd/session must never be archived or removed"
    assert results == [ApplyResult(v, KEPT)]


def test_retire_empty_still_reports_telegram_bindings_as_LEFT_TO_DAEMON_never_writes():
    """Even an empty telegram.bindings row is not this feature's to clear — it routes
    through apply()'s own permanent exclusion, unconditionally."""
    calls, kit = _writers()
    v = _empty_manual("telegram.bindings", wid="@2")

    results = retire_empty([v], **kit)

    assert calls == []
    assert results == [ApplyResult(v, LEFT_TO_DAEMON,
                                    "chela-telegram owns telegram-bindings.json; its own "
                                    "reconcile tick reaps this row")]


def test_retire_empty_preserves_order_one_result_per_verdict_mixed_batch():
    """🔴 GUARD: needs >= 2 *retirable* targets, each with a DIFFERENT outcome, or a
    reversed/shuffled `results` list is invisible — a single target can't show a mismatch,
    and two targets with the SAME action can't show one either (see docs/defeat_shapes).
    `empty_orch` (@1) is forced RACED via `unregister_orchestrator`; `empty_sess` (@9) takes
    the default ARCHIVED path. If retire_empty ever zips a target's own result to the WRONG
    verdict, this comes back either mis-ordered (`r.verdict is not verdicts[i]`) or with
    ARCHIVED/RACED swapped onto the other row's line.
    """
    calls, kit = _writers(unregister_orchestrator=lambda wid, stamped: (
        calls.append(("unregister", wid, stamped)), {"ok": False})[1])
    revivable = _revivable("session-ids", wid="@7", new_wid="@42")
    informative = _manual("session-ids", wid="@5")
    empty_orch = _empty_manual("inbox.orchestrator", wid="@1")
    empty_sess = _empty_manual("session-ids", wid="@9")
    verdicts = [revivable, informative, empty_orch, empty_sess]

    results = retire_empty(verdicts, **kit)

    assert [r.verdict for r in results] == verdicts, "results must line up with input order"
    assert results[2] == ApplyResult(
        empty_orch, RACED, "archived, but the row moved on before it could be removed"
    ), "the @1 row's own (RACED) outcome must land on the @1 row, not @9's"
    assert results[3] == ApplyResult(empty_sess, ARCHIVED, ""), (
        "the @9 row's own (ARCHIVED) outcome must land on the @9 row, not @1's"
    )
    assert [r.action for r in results] == [KEPT, KEPT, RACED, ARCHIVED]
    assert [c[0] for c in calls] == ["archive", "unregister", "archive", "remove"], (
        "only the two empty rows may write anything, and only through archive-then-remove, "
        "in input order"
    )


def test_retire_empty_still_reports_RACED_when_the_removal_writer_declines():
    """The RACED guard must survive being reached through retire_empty, not just apply()."""
    calls, kit = _writers()
    kit["remove_session"] = lambda wid, sid, stamped: (
        calls.append(("remove", wid, sid, stamped)), False)[1]
    v = _empty_manual("session-ids", wid="@5")

    results = retire_empty([v], **kit)

    assert results == [ApplyResult(
        v, RACED, "archived, but the row moved on before it could be removed")]


def test_retire_empty_defaults_wire_to_apply(monkeypatch):
    """🔴 GUARD: retire_empty must delegate its writers to the real apply() defaults, not a
    private reimplementation — flip it to call some other function and every write path
    (and the telegram-bindings exclusion) silently stops being shared with --apply."""
    import chela.restore as restore_mod

    called = []

    def fake_apply(targets, **kw):
        called.append(targets)
        return [ApplyResult(t, KEPT) for t in targets]

    monkeypatch.setattr(restore_mod, "apply", fake_apply)

    v = _empty_manual("session-ids", wid="@5")
    restore_mod.retire_empty([v])

    assert called == [[v]], "retire_empty must call chela.restore.apply() with the filtered subset"


# --------------------------------------------------------------------------
# resume — the launch half (CMX-350, issue #457): actually relaunch a MANUAL agent
# --------------------------------------------------------------------------

# A session id that satisfies `chela.sessions.SESSION_RE` (hex + dashes, 8-64 chars) — the
# same reason test_restore_cli.py picks hex-shaped ids over "sid-1"/"sid-dead": those fail
# the regex before reaching any of resume()'s other guards.
SID_OK = "deadbeef-1111-2222-3333-444444444444"


def _launchable(store="session-ids", wid="@1", session_id=SID_OK, cwd="/home/x"):
    """A MANUAL row `manual_command()` CAN build a real one-liner from, with a session id
    that also passes resume()'s shape check — the row resume() should actually launch."""
    return Verdict(store=store, wid=wid, stamped_epoch=OLD, verdict="MANUAL",
                   session_id=session_id, new_wid=None, cwd=cwd, label="l")


def _resume_kit(**overrides):
    """`_writers()` plus the writers unique to `resume()` — sharing the SAME `calls`
    list so a single assertion can see the full launch-then-bookkeeping order.

    `check_resumed`/`resume_blocked`/`record_resume_failure`/`clear_resume_failure`
    default to the "everything is fine" shape (the launch is genuinely alive, nothing is
    blocked, and the failure hooks are no-ops) so every pre-existing test below — written
    before issue #468 added these seams — keeps exercising exactly the same
    launch-then-bookkeeping path with an unchanged `calls` list. Tests that exercise the
    NEW liveness/retry-bound behaviour override these explicitly.
    """
    calls, kit = _writers()
    kit["spawn_window"] = lambda cwd, command=None: (
        calls.append(("spawn", cwd, command)),
        SpawnResult(ok=True, name="shell-9", wid="@99", cwd=cwd))[1]
    kit["register_orchestrator"] = lambda wid: (
        calls.append(("register", wid)), {"ok": True})[1]
    kit["check_resumed"] = lambda wid, session_id: True
    kit["resume_blocked"] = lambda session_id: None
    kit["record_resume_failure"] = lambda session_id, reason: 1
    kit["clear_resume_failure"] = lambda session_id: None
    kit.update(overrides)
    return calls, kit


def test_resume_never_relaunches_a_REVIVABLE_row():
    """⛔⛔ The counterweight that matters (issue #457): a REVIVABLE row's session is
    already confirmed alive elsewhere — relaunching it would fork a live agent into two
    processes racing one worktree (CMX-346). It must go through apply()'s re-stamp path,
    unchanged, and `spawn_window` must never even be called."""
    calls, kit = _resume_kit()
    v = _revivable("session-ids", wid="@7", new_wid="@42")

    results = resume([v], [], **kit)

    assert not any(c[0] == "spawn" for c in calls), "a REVIVABLE row must never be launched"
    assert calls == [("rekey", "@7", "@42", "sid-live", OLD)]
    assert results[0].verdict == v and results[0].action == REVIVED


def test_resume_archives_a_MANUAL_row_with_nothing_on_record_never_launches():
    """A MANUAL row with no cwd/session cannot build a command — routed to apply()'s
    archive path exactly like retire_empty(), never launched against a guessed path."""
    calls, kit = _resume_kit()
    v = _empty_manual("session-ids", wid="@5")

    results = resume([v], [], **kit)

    assert not any(c[0] == "spawn" for c in calls)
    assert [c[0] for c in calls] == ["archive", "remove"]
    assert results == [ApplyResult(v, ARCHIVED, "")]


def test_resume_leaves_telegram_bindings_untouched():
    calls, kit = _resume_kit()
    v = _manual("telegram.bindings", wid="@2")

    results = resume([v], [], **kit)

    assert calls == []
    assert results[0].action == LEFT_TO_DAEMON


def test_resume_launches_an_eligible_MANUAL_row():
    calls, kit = _resume_kit()
    v = _launchable(wid="@5", cwd="/home/x/proj")

    results = resume([v], [], **kit)

    spawn_calls = [c for c in calls if c[0] == "spawn"]
    assert spawn_calls == [("spawn", "/home/x/proj", f"claude --resume {SID_OK}")]
    assert [c[0] for c in calls] == ["spawn", "archive", "remove"], (
        "archive must land BEFORE the row is removed — reversed, a crash in between "
        "loses the row with no trace"
    )
    assert results == [ApplyResult(v, RESUMED, "resumed at @99")]


def test_resume_relaunches_and_reregisters_the_orchestrator_row():
    """The orchestrator's own row is not just archived-and-removed — it is re-registered
    at the freshly spawned address, in the same pass, so the inbox comes back armed."""
    calls, kit = _resume_kit()
    v = _launchable(store="inbox.orchestrator", wid="@0")

    results = resume([v], [], **kit)

    assert [c[0] for c in calls] == ["spawn", "archive", "register"]
    assert calls[-1] == ("register", "@99")
    assert results == [ApplyResult(v, RESUMED, "resumed at @99, orchestrator re-registered")]


def test_resume_reports_but_does_not_crash_when_orchestrator_reregistration_fails():
    calls, kit = _resume_kit(register_orchestrator=lambda wid: (
        calls.append(("register", wid)), {"ok": False})[1])
    v = _launchable(store="inbox.orchestrator", wid="@0")

    results = resume([v], [], **kit)

    assert results[0].action == RACED
    assert "re-registering the orchestrator failed" in results[0].detail


def test_resume_never_reregisters_when_the_launch_returns_no_wid():
    """⛔⛔ `SpawnResult.wid` is documented as `None` on a tmux build that echoes none, with
    `ok=True` — the window still opened. `resume()` computes
    ``ok = bool(result.wid) and bool(register_orchestrator(result.wid).get("ok"))``, which
    must short-circuit on the falsy `wid` and never call `register_orchestrator` with a
    bogus/empty address."""
    calls, kit = _resume_kit(spawn_window=lambda cwd, command=None: (
        calls.append(("spawn", cwd, command)),
        SpawnResult(ok=True, name="shell-9", wid=None, cwd=cwd))[1])
    v = _launchable(store="inbox.orchestrator", wid="@0")

    results = resume([v], [], **kit)

    assert not any(c[0] == "register" for c in calls), (
        "register_orchestrator must never be called when the launch returned no wid"
    )
    assert results[0].action == RACED


def test_resume_reports_RACED_not_RESUMED_when_remove_session_declines():
    """A session-ids row's own `remove_session` can decline exactly like `apply()`'s does
    (the row moved on since `plan()` computed it) — the launch already happened, so this
    must NOT report RESUMED (which would tell an operator the row is now clean) while the
    row is still sitting in session-ids.json."""
    calls, kit = _resume_kit(remove_session=lambda wid, sid, stamped: (
        calls.append(("remove", wid, sid, stamped)), False)[1])
    v = _launchable(store="session-ids", wid="@5")

    results = resume([v], [], **kit)

    assert results[0].action == RACED, (
        f"remove_session declining must report RACED, not RESUMED — got {results[0].action}"
    )
    assert "row moved on" in results[0].detail


def test_resume_skips_a_session_id_that_does_not_look_like_one():
    """Defense in depth: the launch command is sent through a live shell pane
    (`send-keys` then Enter) — never send anything that isn't a plausible session id."""
    calls, kit = _resume_kit()
    v = _launchable(session_id="sid-dead")   # fails SESSION_RE (leading 's')

    results = resume([v], [], **kit)

    assert calls == [], "a bad-shaped session id must never reach spawn_window"
    assert results[0].action == SKIPPED
    assert "does not look like a session id" in results[0].detail


def test_resume_never_resumes_the_same_session_twice_in_one_pass():
    """The 'never resume more than once per (session_id, epoch)' bound from issue #457:
    the three stores plan() reads can independently carry the same session (the
    orchestrator's is often stamped in both inbox.json and session-ids.json)."""
    calls, kit = _resume_kit()
    first = _launchable(store="inbox.orchestrator", wid="@0")
    second = _launchable(store="session-ids", wid="@1")   # same SID_OK, same stamped_epoch

    results = resume([first, second], [], **kit)

    assert len([c for c in calls if c[0] == "spawn"]) == 1, (
        "the second sighting of the same (session_id, epoch) must not launch a second time"
    )
    assert results[0].action == RESUMED
    assert results[1].action == SKIPPED
    assert "already resumed" in results[1].detail


def test_resume_refuses_a_row_whose_task_is_still_ACTIVE_in_the_dispatcher():
    """The CMX-282/#353 lesson (issue #457's guard): a task the dispatcher still marks
    claimed/running against this exact dangling window is the dispatcher's to reap or
    retry, not this command's to relaunch out from under it."""
    calls, kit = _resume_kit()
    v = _launchable(wid="@5")
    runs = [{"task_id": "cmx-9", "status": "running", "window_id": "@5"}]

    results = resume([v], runs, **kit)

    assert calls == [], "an in-flight task's window must never be launched"
    assert results[0].action == SKIPPED
    assert "cmx-9" in results[0].detail and "ACTIVE" in results[0].detail


def test_resume_refuses_a_row_whose_JUDGE_window_is_still_ACTIVE():
    """The counterpart half of the same guard — a judge window, not just the agent's own."""
    calls, kit = _resume_kit()
    v = _launchable(wid="@5")
    runs = [{"task_id": "cmx-9", "status": "claimed", "judge_window_id": "@5"}]

    results = resume([v], runs, **kit)

    assert calls == []
    assert results[0].action == SKIPPED


def test_resume_still_launches_when_the_matching_run_is_TERMINAL():
    """The counterweight: a run row that matches the window but is NOT active (already
    done/closed/failed-out) must not block the launch — the guard is about a task the
    dispatcher still owns, not about the mere existence of a run row."""
    calls, kit = _resume_kit()
    v = _launchable(wid="@5")
    runs = [{"task_id": "cmx-9", "status": "done", "window_id": "@5"}]

    results = resume([v], runs, **kit)

    assert any(c[0] == "spawn" for c in calls)
    assert results[0].action == RESUMED


def test_resume_reports_RESUME_FAILED_and_writes_nothing_when_the_launch_fails():
    calls, kit = _resume_kit(spawn_window=lambda cwd, command=None: (
        calls.append(("spawn", cwd, command)),
        SpawnResult(ok=False, error="tmux is unreachable"))[1])
    v = _launchable(wid="@5")

    results = resume([v], [], **kit)

    assert [c[0] for c in calls] == ["spawn"], (
        "a failed launch must never be archived or removed — the row is untouched"
    )
    assert results[0].action == RESUME_FAILED
    assert results[0].detail == "tmux is unreachable"


def test_resume_persists_a_resume_failure_when_the_SPAWN_itself_fails_not_only_on_a_dead_liveness_check():
    """🔴 GUARD (CMX-353 rework round 1): a spawn failure must persist a resume failure too —
    not only the liveness-check-failed branch below. Without this, a session whose
    `spawn_window` keeps erroring (a full tmux server, a bad cwd) is relaunched forever on
    every `chela restore --resume` pass instead of ever tripping the durable retry bound."""
    record_calls = []
    calls, kit = _resume_kit(
        spawn_window=lambda cwd, command=None: (
            calls.append(("spawn", cwd, command)),
            SpawnResult(ok=False, error="tmux is unreachable"))[1],
        record_resume_failure=lambda sid, reason: (record_calls.append((sid, reason)), 1)[1],
    )
    v = _launchable(wid="@5")

    results = resume([v], [], **kit)

    assert results[0].action == RESUME_FAILED
    assert record_calls == [(SID_OK, "tmux is unreachable")], (
        "a spawn failure must be persisted via record_resume_failure exactly like a dead "
        "liveness check is, so a session whose launch keeps erroring eventually blocks too"
    )


# --------------------------------------------------------------------------
# _default_check_resumed — the real liveness logic (CMX-353 rework round 1, issue #468)
#
# Every test above exercises `resume()` with `check_resumed` stubbed out entirely, which
# proves the SEAM is wired (test_resume_liveness_and_retry_bound_defaults_wire_to_the_real_modules
# below) but never executes a single line of `_default_check_resumed`'s own body. These call
# the real function directly, faking only its evidence source (`sessions.wid_for_session` /
# `sessions.panes`) — the same discipline defeat_shapes 319/330 prescribe for a leaf function
# whose only prior tests monkeypatched it away wholesale.
# --------------------------------------------------------------------------

def test_default_check_resumed_returns_False_immediately_for_a_falsy_wid(monkeypatch):
    """🔴 GUARD: 'UNKNOWN MUST NOT READ AS OK' — a falsy wid must return False WITHOUT ever
    consulting tmux evidence or waiting out the settle window. A spy on `sessions.panes`
    (the first thing the real alive-check touches) proves the short circuit actually fires,
    rather than merely happening to return False after looking anyway."""
    calls = []
    monkeypatch.setattr(sessions, "panes",
                         lambda force=False: (calls.append(("panes", force)), {})[1])
    monkeypatch.setattr(sessions, "wid_for_session",
                         lambda sid, pane_map=None: (calls.append(("wid_for_session", sid)), None)[1])
    sleeps = []

    result = _default_check_resumed(None, SID_OK, sleep=sleeps.append)

    assert result is False
    assert calls == [], "a falsy wid must never even look at tmux evidence"
    assert sleeps == [], "must return before waiting out the settle window too"


def test_default_check_resumed_requires_real_tmux_evidence_not_assumed(monkeypatch):
    """🔴 GUARD: liveness must come from `sessions.wid_for_session` actually matching the
    resumed wid — not be assumed true. `wid_for_session` here NEVER returns the resumed
    wid, so a correct implementation can never build a confirmation streak."""
    monkeypatch.setattr(sessions, "panes", lambda force=False: {})
    monkeypatch.setattr(sessions, "wid_for_session", lambda sid, pane_map=None: "@some-other-window")

    result = _default_check_resumed("@99", SID_OK, sleep=lambda s: None,
                                     attempts=3, confirmations=2, delay=0, settle=0)

    assert result is False, (
        "wid_for_session never once matched the resumed wid — this must never read as alive"
    )


def test_default_check_resumed_requires_CONSECUTIVE_confirmations(monkeypatch):
    """🔴 GUARD: the exact measured false positive the module comment documents — a doomed
    `claude --resume <bad sid>` process reads as alive for one brief window then vanishes.
    One sighting followed by a gap must NOT read as alive; only `_LIVENESS_CONFIRMATIONS`
    CONSECUTIVE sightings may. Left at the REAL default `attempts`/`confirmations` (only
    `sleep` is faked) — passing `confirmations=2` explicitly here would keep this green even
    if the module's own `_LIVENESS_CONFIRMATIONS` constant were corrupted to 1, the same
    "override swallows the real value" gap defeat_shapes 352c describes."""
    monkeypatch.setattr(sessions, "panes", lambda force=False: {})
    sightings = iter(["@99"] + ["@gone"] * 10)   # alive once, then gone for good
    monkeypatch.setattr(sessions, "wid_for_session",
                         lambda sid, pane_map=None: next(sightings))

    result = _default_check_resumed("@99", SID_OK, sleep=lambda s: None)

    assert result is False, (
        "alive once, then gone, must not satisfy the real CONSECUTIVE-sightings requirement"
    )


def test_default_check_resumed_waits_out_the_settle_window_before_looking_at_all(monkeypatch):
    """🔴 GUARD: the measured 0.5s-1.9s span in which a doomed relaunch still reads as alive
    — the real default `settle` (not overridden here) must actually be waited out via
    `sleep` before the first tmux evidence is even consulted. Pinned to a literal, not to
    the module's own (possibly-corrupted) constant, so a mutated constant cannot drag this
    assertion down with it."""
    monkeypatch.setattr(sessions, "panes", lambda force=False: {})
    monkeypatch.setattr(sessions, "wid_for_session", lambda sid, pane_map=None: "@99")
    sleeps = []

    result = _default_check_resumed("@99", SID_OK, sleep=sleeps.append,
                                     attempts=1, confirmations=1, delay=0.01)

    assert result is True
    assert sleeps and sleeps[0] == 2.0, (
        "the real default settle window must be waited out before the first look"
    )


def test_default_check_resumed_forces_a_fresh_pane_read(monkeypatch):
    """🔴 GUARD: must never trust the 1s `sessions.panes()` cache a concurrent caller may
    have populated before the resumed process even started — every read must pass
    `force=True`."""
    force_flags = []
    monkeypatch.setattr(sessions, "panes",
                         lambda force=False: (force_flags.append(force), {})[1])
    monkeypatch.setattr(sessions, "wid_for_session", lambda sid, pane_map=None: None)

    _default_check_resumed("@99", SID_OK, sleep=lambda s: None,
                            attempts=1, confirmations=1, delay=0, settle=0)

    assert force_flags == [True], "must never trust the cached pane read"


def test_default_check_resumed_confirms_a_genuinely_alive_relaunch(monkeypatch):
    """⛔⛔ The counterweight that matters (raised on this same PR by a peer session): a
    liveness check tightened to satisfy the guards above must still confirm a GENUINELY
    alive relaunch — otherwise a healthy resume reports RESUME_FAILED, a failure record is
    persisted, and CMX-353's own durable retry bound then refuses to ever retry it again,
    which is strictly worse than the bug issue #468 was filed to fix. Drives the REAL
    function, at its REAL default attempts/delay/confirmations/settle (only `sleep` is
    faked, so the test doesn't actually wait 2+ seconds), against evidence that
    consistently confirms the resumed wid."""
    monkeypatch.setattr(sessions, "panes", lambda force=False: {})
    monkeypatch.setattr(sessions, "wid_for_session",
                         lambda sid, pane_map=None: "@99" if sid == SID_OK else None)
    sleeps = []

    result = _default_check_resumed("@99", SID_OK, sleep=sleeps.append)

    assert result is True, "a session consistently confirmed alive must report resumed"
    assert sleeps[0] == 2.0, "must still wait out the settle window first"


# --------------------------------------------------------------------------
# resume — liveness + durable retry bound (CMX-353, issue #468)
# --------------------------------------------------------------------------

def test_resume_reports_RESUME_FAILED_not_RESUMED_when_the_window_never_comes_alive():
    """🔴 GUARD (issue #468, defect 1): `spawn_window` succeeding only means a tmux window
    was CREATED — it says nothing about whether `claude --resume` actually came up. A
    launch whose liveness check fails must report RESUME_FAILED, and the row must be left
    completely untouched: never archived, never removed, never re-registered — exactly the
    same "row is untouched" contract a launch failure already gets."""
    record_calls = []
    calls, kit = _resume_kit(
        check_resumed=lambda wid, sid: False,
        record_resume_failure=lambda sid, reason: (record_calls.append((sid, reason)), 1)[1],
    )
    v = _launchable(wid="@5")

    results = resume([v], [], **kit)

    assert [c[0] for c in calls] == ["spawn"], (
        "a window that never came alive must never be archived or removed"
    )
    assert results[0].action == RESUME_FAILED
    assert "@99" in results[0].detail and SID_OK in results[0].detail
    assert record_calls == [(SID_OK, results[0].detail)], (
        "the failure must be persisted via record_resume_failure so a later pass can see it"
    )


def test_resume_still_reports_RESUMED_for_a_genuinely_alive_relaunch():
    """⛔⛔ The counterweight that matters: a liveness check tightened too far (or one that
    waits on a signal a healthy resumed agent never sends) would silently disable the whole
    feature while still passing the RESUME_FAILED guard above. A relaunch `check_resumed`
    confirms alive must still report RESUMED and still be archived/removed exactly as
    before — and the failure-clearing hook must run, not the failure-recording one."""
    check_calls = []
    clear_calls = []
    calls, kit = _resume_kit(
        check_resumed=lambda wid, sid: (check_calls.append((wid, sid)), True)[1],
        clear_resume_failure=lambda sid: clear_calls.append(sid),
    )
    v = _launchable(wid="@5")

    results = resume([v], [], **kit)

    assert check_calls == [("@99", SID_OK)]
    assert clear_calls == [SID_OK]
    assert [c[0] for c in calls] == ["spawn", "archive", "remove"]
    assert results[0].action == RESUMED


def test_resume_does_not_relaunch_a_session_whose_resume_already_failed_on_a_prior_pass():
    """🔴 GUARD (issue #468, defect 2): the durable bound. A session that FAILED to come up
    alive on one `resume()` call must not be relaunched by the NEXT `resume()` call — a
    fresh Python-level `resumed_sessions` set (scoped to one call) cannot see this; only a
    store that survives between calls can. Simulated here with a plain dict standing in for
    `resume-attempts.json`, read/written exactly the way `chela.resume_state` is, across
    two SEPARATE `resume()` invocations."""
    store: dict[str, dict] = {}

    def resume_blocked(sid):
        entry = store.get(sid)
        if not entry or entry["tries"] < 1:
            return None
        return entry["reason"]

    def record_resume_failure(sid, reason):
        tries = store.get(sid, {"tries": 0})["tries"] + 1
        store[sid] = {"tries": tries, "reason": reason}
        return tries

    calls, kit = _resume_kit(
        check_resumed=lambda wid, sid: False,   # every launch in this test dies
        resume_blocked=resume_blocked,
        record_resume_failure=record_resume_failure,
    )
    v = _launchable(wid="@5")

    first = resume([v], [], **kit)
    assert first[0].action == RESUME_FAILED
    assert [c[0] for c in calls] == ["spawn"]

    calls.clear()
    second_v = _launchable(wid="@5")   # a fresh Verdict — same session, next `chela restore` pass
    second = resume([second_v], [], **kit)

    assert calls == [], "the second pass must not even call spawn_window for a blocked session"
    assert second[0].action == SKIPPED
    assert "already failed" in second[0].detail and SID_OK in second[0].detail


def test_resume_defaults_wire_to_the_real_spawn_and_inbox_modules(monkeypatch):
    """🔴 GUARD: with no ``spawn_window``/``register_orchestrator`` kwargs, resume() must
    call the REAL `chela.spawn.spawn_window` / `chela.inbox.register` — not a silently-inert
    no-op. Both are resolved INSIDE the function body (not bound at import time) precisely
    so a caller can fake this one tmux-touching leaf without disturbing anything else —
    proven here by patching the module attribute and observing the call land.

    ``check_resumed``/``resume_blocked``/``record_resume_failure``/``clear_resume_failure``
    ARE overridden here (unlike ``spawn_window``/``register_orchestrator``) — this test is
    scoped to proving the spawn/register wiring, not the liveness/retry-bound machinery
    (covered by ``test_resume_liveness_and_retry_bound_defaults_wire_to_the_real_modules``
    below); leaving them at their real defaults would poll real tmux and touch the real
    ``resume-attempts.json`` on this machine."""
    import chela.inbox as inbox_mod
    import chela.spawn as spawn_mod

    spawn_calls = []
    register_calls = []
    monkeypatch.setattr(spawn_mod, "spawn_window", lambda cwd, command=None: (
        spawn_calls.append((cwd, command)),
        SpawnResult(ok=True, name="s", wid="@99", cwd=cwd))[1])
    monkeypatch.setattr(inbox_mod, "register", lambda wid: (
        register_calls.append(wid), {"ok": True})[1])

    v = _launchable(store="inbox.orchestrator", wid="@0")
    results = resume([v], [], check_resumed=lambda wid, sid: True,
                      resume_blocked=lambda sid: None,
                      record_resume_failure=lambda sid, reason: 1,
                      clear_resume_failure=lambda sid: None)

    assert spawn_calls == [("/home/x", f"claude --resume {SID_OK}")]
    assert register_calls == ["@99"]
    assert results[0].action == RESUMED


def test_resume_liveness_and_retry_bound_defaults_wire_to_the_real_modules(monkeypatch):
    """🔴 GUARD: with no ``check_resumed``/``resume_blocked``/``record_resume_failure``/
    ``clear_resume_failure`` kwargs, resume() must call the REAL
    ``chela.restore._default_check_resumed`` / ``chela.resume_state.blocked_reason`` /
    ``chela.resume_state.record_failure`` / ``chela.resume_state.clear`` — not a silently-
    inert no-op. Patches the module attributes ``resume()`` actually resolves at call time
    and observes each call land, on both the alive and the dead-session path."""
    import chela.resume_state as resume_state_mod
    import chela.restore as restore_mod

    calls = []
    monkeypatch.setattr(restore_mod, "_default_check_resumed",
                         lambda wid, sid, **k: (calls.append(("check", wid, sid)), True)[1])
    monkeypatch.setattr(resume_state_mod, "blocked_reason",
                         lambda sid, *a, **k: (calls.append(("blocked", sid)), None)[1])
    monkeypatch.setattr(resume_state_mod, "clear",
                         lambda sid: calls.append(("clear", sid)))

    kit = {"spawn_window": lambda cwd, command=None: SpawnResult(
               ok=True, name="s", wid="@99", cwd=cwd),
           "register_orchestrator": lambda wid: {"ok": True},
           "archive": lambda entry: None, "remove_session": lambda *a: True}
    v = _launchable(wid="@5")
    results = resume([v], [], **kit)

    assert ("blocked", SID_OK) in calls
    assert ("check", "@99", SID_OK) in calls
    assert ("clear", SID_OK) in calls
    assert results[0].action == RESUMED

    calls.clear()
    monkeypatch.setattr(restore_mod, "_default_check_resumed",
                         lambda wid, sid, **k: (calls.append(("check", wid, sid)), False)[1])
    monkeypatch.setattr(resume_state_mod, "record_failure",
                         lambda sid, reason: (calls.append(("record", sid, reason)), 1)[1])
    v2 = _launchable(wid="@6", session_id="cafebabe-0000-1111-2222-333344445555")
    results2 = resume([v2], [], **kit)

    assert any(c[0] == "record" for c in calls)
    assert results2[0].action == RESUME_FAILED


def test_resume_preserves_order_one_result_per_verdict_mixed_batch():
    """Same ordering guard as retire_empty's — a launched row's result must land on the
    launched row, not on its neighbour, in a batch mixing every outcome."""
    calls, kit = _resume_kit()
    revivable = _revivable("session-ids", wid="@7", new_wid="@42")
    empty = _empty_manual("session-ids", wid="@1")
    launched = _launchable(wid="@5", session_id="cafebabe-0000-1111-2222-333344445555")
    bad_shape = _launchable(wid="@6", session_id="sid-dead")

    results = resume([revivable, empty, launched, bad_shape], [], **kit)

    assert [r.verdict for r in results] == [revivable, empty, launched, bad_shape]
    assert [r.action for r in results] == [REVIVED, ARCHIVED, RESUMED, SKIPPED]


def test_resume_delegates_the_non_eligible_subset_to_apply(monkeypatch):
    """🔴 GUARD: resume() must delegate REVIVABLE / telegram.bindings / empty-MANUAL rows
    to the real chela.restore.apply(), not a private reimplementation."""
    import chela.restore as restore_mod

    called = []

    def fake_apply(targets, **kw):
        called.append(targets)
        return [ApplyResult(t, KEPT) for t in targets]

    monkeypatch.setattr(restore_mod, "apply", fake_apply)

    v = _revivable("session-ids", wid="@7", new_wid="@42")
    restore_mod.resume([v], [])

    assert called == [[v]], "resume must call chela.restore.apply() with the filtered subset"


def test_the_three_resume_outcome_words_are_distinct_from_apply_and_each_other():
    """🔴 GUARD: pin the resume-specific vocabulary — a report is only useful if two
    different outcomes never render the same word as far as an operator can tell."""
    assert (RESUMED, RESUME_FAILED, SKIPPED) == ("resumed", "resume-failed", "skipped")
    all_words = {LEFT_TO_DAEMON, REVIVED, ARCHIVED, RACED, KEPT, RESUMED, RESUME_FAILED, SKIPPED}
    assert len(all_words) == 8
