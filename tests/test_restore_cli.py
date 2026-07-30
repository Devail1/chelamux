"""🔴 GUARDS for CMX-195's two `chela/main.py` WIRING sites.

`chela/restore.py`, `chela/roster.py` and `chela/inbox.py` are all thoroughly tested as
libraries — and the judge proved every one of those tests stays green when the CLI half is
neutralised. A library nobody calls, and an exit code nobody checks, protect nothing:

* `chela restore` must exit NONZERO while any row is MANUAL. That exit code is the whole
  interface to a restart procedure ("run restore, then check the code before declaring the
  box recovered"). Neutralised, the command prints an orphan list and reports SUCCESS.
* `chela watch` must WARN when no session identity resolved. Measured live 2026-07-30:
  `orchestrator_session` was `null`, which disarmed CMX-82's self-heal before it ran and
  looked identical to a healthy registration. `inbox.register` returning the fact is only
  half the fix — nothing surfaced it to the operator.

⚠️ Round 5 lesson, recorded because it cost a round: the branch-logic tests below
monkeypatch `scan_all`, `plan` and `epoch.current`. That is correct for isolating exit-code
branching, and it is EXACTLY why four further wiring cuts survived them — a fixture that
stubs a collaborator hides the wiring to that collaborator. Stubbing moves the blind spot,
it does not remove it. So the `END-TO-END` section at the bottom stubs nothing inside
restore: it drives the real argparse dispatch against a real temp CHELA_DIR holding real
dangling rows, and only leaf I/O (tmux) is faked. Keep both halves.
"""
from __future__ import annotations

import importlib
import json
import sys
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from chela import main
from chela import restore as restore_mod

NOW = "9001-1784099999"
OLD = "786-1784045825"

# ⚠️ Session ids must satisfy `chela.sessions.SESSION_RE` (hex + dashes, 8-64 chars) or
# `wid_for_session` returns None BEFORE reaching any seam — which is why rounds 5-7 never
# observed a REVIVABLE verdict: their ids ("sid-from-a-dead-server") failed the regex.
SID_DEAD = "bbbbbbbb-1111-2222-3333-444444444444"      # nothing live runs it -> MANUAL
SID_LIVE = "cccccccc-5555-6666-7777-888888888888"      # resumed under @42  -> REVIVABLE
SID_ORCH = "aaaaaaaa-9999-0000-1111-222222222222"
CWD_FIVE = "/home/liav/projects/five"


def _verdict(verdict, store="session-ids", wid="@1"):
    return restore_mod.Verdict(
        store=store, wid=wid, label="l", stamped_epoch=OLD,
        session_id="sid-1", cwd="/home/x", verdict=verdict, new_wid="@9",
    )


@pytest.fixture()
def restore_env(monkeypatch):
    """Stub every read `cmd_restore` makes, so only the exit-code wiring is under test."""
    from chela import dispatcher, epoch, inbox, sessionids
    from chela.telegram import bindings as bindings_mod

    monkeypatch.setattr(epoch, "current", lambda: NOW)
    monkeypatch.setattr(epoch, "describe", lambda e: str(e))
    monkeypatch.setattr(inbox, "load", lambda: {"watches": {}})
    monkeypatch.setattr(dispatcher, "list_runs", lambda *a, **k: [])
    monkeypatch.setattr(sessionids, "entries", lambda: {})
    monkeypatch.setattr(restore_mod, "scan_all", lambda *a, **k: [])
    monkeypatch.setattr(bindings_mod.BindingRegistry, "load",
                        classmethod(lambda cls, *a, **k: bindings_mod.BindingRegistry("1")))
    return monkeypatch


def test_restore_exits_nonzero_while_any_row_is_MANUAL(restore_env, capsys):
    restore_env.setattr(restore_mod, "plan", lambda *a, **k: [_verdict("MANUAL")])

    with pytest.raises(SystemExit) as exc:
        main.cmd_restore(SimpleNamespace())

    assert exc.value.code == 1, (
        "a MANUAL row means an agent is still orphaned — restore must fail loudly so a "
        "restart procedure cannot read as recovered"
    )
    assert "MANUAL" in capsys.readouterr().out


def test_restore_exits_ZERO_when_every_row_is_REVIVABLE(restore_env, capsys):
    """The counterweight: a guard that only ever demands failure would be satisfied by
    `sys.exit(1)` unconditionally, which would make the command useless."""
    restore_env.setattr(restore_mod, "plan", lambda *a, **k: [_verdict("REVIVABLE")])

    main.cmd_restore(SimpleNamespace())     # must NOT raise

    assert "REVIVABLE" in capsys.readouterr().out


def test_restore_exits_ZERO_when_nothing_is_orphaned(restore_env, capsys):
    restore_env.setattr(restore_mod, "plan", lambda *a, **k: [])

    main.cmd_restore(SimpleNamespace())     # must NOT raise

    assert "nothing orphaned" in capsys.readouterr().out


# --- objective 5's operator-visible half ----------------------------------------------

def _watch_env(monkeypatch, session, self_wid="@0"):
    from chela import inbox, orchestrator
    monkeypatch.setattr(orchestrator, "self_wid", lambda: self_wid)
    monkeypatch.setattr(main, "_resolve_wid", lambda w: "@7")
    monkeypatch.setattr(inbox, "watch",
                        lambda *a, **k: {"ok": True, "wid": "@7", "note": "",
                                         "orchestrator": "@0", "epoch": NOW,
                                         "session": session})


def test_watch_warns_when_no_session_identity_resolved(monkeypatch, capsys):
    _watch_env(monkeypatch, None)

    main.cmd_watch(SimpleNamespace(wid="@7", note=""))

    err = capsys.readouterr().err
    assert "could not resolve a session identity" in err, (
        "a registration with no identity is indistinguishable from a healthy one once it "
        "is on disk — CMX-82's self-heal is disarmed and nothing says so"
    )
    assert "@0" in err, "the warning must name the window whose identity failed"
    # ...and what to DO about it. A warning that only says something failed leaves the
    # operator with a disarmed self-heal and no way to re-arm it.
    assert "chela watch" in err and "hook" in err, (
        f"the warning must name the remedy, not just the failure. Got: {err!r}"
    )
    # ⭐ ...and the CONSEQUENCE, which is objective 5's entire point: a registration with no
    # identity looks healthy while CMX-82's self-heal is disarmed for it. "Try again later"
    # keeps the remedy and drops the reason it matters.
    assert "self-heal is unavailable" in err, (
        f"the warning must say WHAT is broken, not merely that a lookup failed. Got: {err!r}"
    )


def test_watch_stays_QUIET_when_the_caller_is_not_REGISTERING_itself(monkeypatch, capsys):
    """🔴 The warning is scoped to a registration. `_identity_of` only runs when a caller
    registers ITSELF as orchestrator (`by=self_wid`); with no self_wid there is no
    registration, so `session` is absent for a reason that is not a failure. Drop the
    `self_wid and` guard and every `chela watch @N` from a non-registering caller warns
    about a disarmed self-heal that was never being armed — noise on the exact channel
    objective 5 exists to keep meaningful.
    """
    _watch_env(monkeypatch, None, self_wid=None)

    main.cmd_watch(SimpleNamespace(wid="@7", note=""))

    assert "could not resolve a session identity" not in capsys.readouterr().err


def test_watch_stays_QUIET_when_an_identity_did_resolve(monkeypatch, capsys):
    """The counterweight: warning unconditionally would train the operator to ignore it."""
    _watch_env(monkeypatch, "sid-abc")

    main.cmd_watch(SimpleNamespace(wid="@7", note=""))

    assert "could not resolve a session identity" not in capsys.readouterr().err


# --- END-TO-END: the real dispatch, over real stores ------------------------------------
#
# 🔴 GUARDS (CMX-195 round 5). Everything above isolates ONE branch by stubbing the rest.
# The judge showed what that leaves open: `chela restore`'s argparse dispatch, its call to
# `restore.scan_all`, and the dict it builds from the real telegram-bindings.json were all
# revertible with 2068 tests still green. These drive the real thing instead.
#
# Convention borrowed from tests/test_contract_cli.py, whose docstring states the rule:
# "corrupt `elif args.command == "merge": cmd_merge(args)` to `… : pass` and contract.merge
# is never called ... or a reverted dispatch merges silently."

def _drive(argv):
    """Run ``main.main()`` with ``argv`` as process args (argparse reads ``sys.argv``)."""
    with patch.object(sys, "argv", ["chela", *argv]):
        main.main()


@pytest.fixture()
def live_stores(tmp_path, monkeypatch):
    """A REAL temp CHELA_DIR with a dangling row in EVERY source restore reads.

    ⛔ Stubs nothing inside `restore` — not `scan_all`, not `plan`, not the bindings
    dict-build. Only `dispatcher.list_runs` (a sqlite read) and `epoch.current` (tmux) are
    faked, because they are leaf I/O rather than the wiring under test.

    ⚠️ Round 7 lesson: an END-TO-END fixture is only as strong as the DATA it holds. Rounds
    5-6 called this "a dangling row in each of the three stores" — but restore reads SIX
    row sources across two entry points, and this fixture held three of them. Every source
    it left empty was a wiring cut nothing could see, no matter how the assertions were
    shaped. Enumerated from the code rather than from memory:

      scan_all -> inbox.watches · dispatcher.runs · dispatcher.runs (judge) · session-ids
      plan     -> inbox.orchestrator · telegram.bindings · session-ids

    ⛔ If a future change teaches restore a NEW source, it must be seeded here too, or it
    is unguarded by construction.
    """
    chela_dir = tmp_path / "chela"
    chela_dir.mkdir(parents=True)
    monkeypatch.setenv("CHELA_DIR", str(chela_dir))
    monkeypatch.setenv("CHELA_INBOX_FILE", str(chela_dir / "inbox.json"))
    monkeypatch.setenv("CHELA_TELEGRAM_BINDINGS", str(chela_dir / "telegram-bindings.json"))

    # inbox.json carries TWO independent sources: the orchestrator's own registration
    # (plan's arm — the row objective 5 and CMX-82 are both about) and `watches`
    # (scan_all's). Seeding only the second left the first unguarded for three rounds.
    (chela_dir / "inbox.json").write_text(json.dumps({
        "orchestrator": "@1", "orchestrator_epoch": OLD,
        "orchestrator_session": SID_ORCH,
        "orchestrator_name": "liavedunix", "queue": [], "runs_seen": {},
        "watches": {"@3": {"note": "reviewing cmx-41", "since": 1.0,
                           "name": "agent-3", "epoch": OLD}},
    }))
    (chela_dir / "telegram-bindings.json").write_text(json.dumps({
        "chat_id": "-100777", "bindings": {"@2": "5150"},
        "topic_names": {"@2": "nautilus"}, "epochs": {"@2": OLD},
    }))
    (chela_dir / "session-ids.json").write_text(json.dumps({
        "@5": {"session_id": SID_DEAD, "epoch": OLD},
        "@7": {"session_id": SID_LIVE, "epoch": OLD},
    }))
    # The roster the reconcile tick would have written while OLD was the running server.
    # Without it, `plan`'s DEFAULT `roster_lookup` has nothing to join and a severed join
    # is indistinguishable from a working one — which is what hid it for three rounds.
    (chela_dir / "roster.json").write_text(json.dumps({"epochs": {OLD: {
        "first_seen": 1.0, "last_seen": 2.0, "windows": {
            "@1": {"name": "liavedunix", "cwd": "/home/liav", "session_id": SID_ORCH},
            "@5": {"name": "agent-5", "cwd": CWD_FIVE, "session_id": SID_DEAD},
            "@7": {"name": "agent-7", "cwd": "/home/liav/projects/seven",
                   "session_id": SID_LIVE},
        }}}}))

    import chela.config as config
    importlib.reload(config)
    import chela.sessionids as sessionids_mod
    importlib.reload(sessionids_mod)
    import chela.roster as roster_mod
    importlib.reload(roster_mod)

    # The dispatcher `runs` table — the store the round-1 review credited as a real
    # improvement on the brief, and the largest scanner in restore.py. It carries BOTH
    # halves: the agent window and the judge's. Stubbed to `[]` for three rounds, which
    # made `scan_runs` decorative from the CLI's side.
    from chela import dispatcher, epoch
    monkeypatch.setattr(dispatcher, "list_runs", lambda *a, **k: [{
        "task_id": "abc123", "title": "cmx-77 do a thing", "status": "running",
        "window_id": "@9", "window_epoch": OLD,
        "judge_window_id": "@10", "judge_window_epoch": OLD,
    }])
    monkeypatch.setattr(epoch, "current", lambda: NOW)

    # ⭐ The LEAF is faked (which pane claims a session), never the seam. `plan`'s default
    # `wid_for_session` stays the real `sessions.wid_for_session`, so blanking that default
    # is observable — SID_LIVE resolves to a live window, SID_DEAD to nothing.
    from chela import sessions
    monkeypatch.setattr(sessions, "panes", lambda *a, **k: {})
    monkeypatch.setattr(sessions, "wid_claiming_session",
                        lambda sid, pane_map=None: "@42" if sid == SID_LIVE else None)
    return monkeypatch


def test_chela_restore_dispatch_reaches_the_report_and_names_all_three_stores(live_stores,
                                                                              capsys):
    """One guard, three cuts: the argparse branch, the `scan_all` call, and the bindings
    dict built from the real registry. Revert any one and this goes red."""
    with pytest.raises(SystemExit) as exc:
        _drive(["restore"])

    out = capsys.readouterr().out
    assert exc.value.code == 1, "a MANUAL row must still fail the command end-to-end"
    # scan_all's output — the inbox watch a dead server stamped.
    assert _line_with(out, "[inbox.watches]", "@3"), (
        "cmd_restore must consume restore.scan_all — emptied, it prints a clean bill of "
        "health while rows dangle in every store"
    )
    assert _line_with(out, "[session-ids]", "@5", "(tmux epoch")
    # ...and the bindings dict built from the REAL telegram-bindings.json.
    assert "telegram.bindings" in out and "@2" in out, (
        "the bindings row must reach the report — dropping the write must not become "
        "dropping the row (the round-2 review required exactly this)"
    )


def test_chela_restore_says_CANNOT_VERIFY_when_tmux_cannot_be_asked(live_stores, capsys):
    """⛔ The one thing a green check must never be. With no tmux there is no epoch, so
    NOTHING can be proven dangling — or proven current. Suppress the warning and the
    operator reads only 'nothing orphaned', which is a claim the command cannot make."""
    from chela import epoch
    live_stores.setattr(epoch, "current", lambda: None)

    _drive(["restore"])                       # unknown epoch classifies nothing → exits 0

    out = capsys.readouterr().out
    assert "CANNOT VERIFY" in out, (
        "an unreadable epoch is unknown, not healthy — saying nothing is a false pass"
    )
    assert "not a clean bill of health" in out
    # ⭐ Sibling of the doctor Fact's `owned_by` (round 14), on the CLI surface: a CANNOT
    # VERIFY that does not name WHAT could not be read is unactionable — the operator
    # cannot tell a dead tmux from a broken chela.
    cv = _line_with(out, "CANNOT VERIFY")
    assert "tmux" in cv and "unreachable" in cv, (
        f"the CANNOT VERIFY line must name what could not be read, ON THAT LINE — 'tmux' "
        f"also appears in the orphan block's own wording. Got: {cv!r}"
    )


# --- END-TO-END, asserted on the STORES rather than on stdout ----------------------------
#
# 🔴 GUARDS (CMX-195 round 6). Round 5 drove the real dispatch but asserted only on stdout,
# and stdout turned out to be satisfiable by the WRONG path: a dangling session-ids row is
# printed TWICE — once by `scan_all`'s orphan list and once by `plan`'s verdict list — so
# cutting the `plan()` half changed nothing any grep could see. Output is evidence that
# something printed; the STORE is evidence that the command did its job.
#
# The round-2 review already settled this assertion shape for the bindings arm ("assert on
# the file, not on a mock's call count"). These extend it to the command as a whole.

def _store_bytes(chela_dir):
    return {p.name: p.read_bytes() for p in sorted(chela_dir.glob("*.json"))}


def test_a_plain_chela_restore_touches_NOTHING_on_disk(live_stores, tmp_path, capsys):
    """🔴 The dry-run promise, asserted on bytes. `cmd_restore`'s docstring and the CLI help
    both say "dry-run by default ... it does not touch any of them".

    ⭐ This is now the LOAD-BEARING guard of the whole command: the write half was split out
    to its own ticket, so read-only is the contract, not a default. Teach any code path here
    to write and the operator's LOOK silently mutates state, while printing the same store
    names and exiting the same 1. Only the files can tell.
    """
    chela_dir = tmp_path / "chela"
    before = _store_bytes(chela_dir)

    with pytest.raises(SystemExit) as exc:
        _drive(["restore"])

    assert exc.value.code == 1
    assert _store_bytes(chela_dir) == before, (
        "chela restore wrote to a store — it is READ-ONLY, with no write mode at all"
    )
    # roster.json is in the glob above, but assert it explicitly: it is the archive
    # destination, so an unchanged roster is the direct evidence nothing was archived.
    assert json.loads((chela_dir / "roster.json").read_text()) == json.loads(
        before["roster.json"]), (
        "chela restore archived a row into roster.json — it must never write"
    )


def test_a_broken_runs_db_does_not_crash_the_report(live_stores, capsys):
    """🔴 GUARD: "a DB hiccup must never crash a status report" — cmd_restore's own comment.

    Same shape as the roster write's swallow in `_reconcile_loop`: every other test stubs
    `list_runs` to return `[]`, so narrowing the `except` changes nothing. But a locked or
    half-written runs DB is the normal state right after a hard kill — which is the exact
    condition an operator runs this command in.
    """
    from chela import dispatcher

    def _boom(*a, **k):
        raise RuntimeError("database is locked")

    live_stores.setattr(dispatcher, "list_runs", _boom)

    with pytest.raises(SystemExit) as exc:      # SystemExit, NOT RuntimeError
        _drive(["restore"])

    assert exc.value.code == 1
    out = capsys.readouterr().out
    assert "session-ids" in out, "the rest of the report must survive a dead runs DB"


# --- the sources rounds 5-6 never seeded -------------------------------------------------
#
# 🔴 GUARDS (CMX-195 round 7). Each of these is a row source `live_stores` now holds and
# nothing previously observed. They are separate tests, not extra asserts on an existing
# one, so a failure names WHICH source went dark.

def test_the_dispatcher_runs_table_reaches_the_report_both_halves(live_stores, capsys):
    """🔴 `scan_runs` reads the agent window AND the judge's. Cut the runs list out of the
    scan and every dangling window in the dispatcher is invisible to the one command an
    operator runs after a hard kill — while the report still looks complete, because
    inbox.watches and session-ids keep printing."""
    with pytest.raises(SystemExit):
        _drive(["restore"])

    out = capsys.readouterr().out
    assert "dispatcher.runs" in out and "@9" in out, "the agent window half went dark"
    assert "dispatcher.runs (judge)" in out and "@10" in out, "the JUDGE window half"


def test_the_doctor_fact_reads_the_three_LIVE_stores(live_stores, tmp_path):
    """🔴 `runtime_truth._restore_scan` is the ONLY thing that joins inbox watches + the
    runs table + session-ids for the doctor fact, and every test that touches the fact
    monkeypatches it. Cut its three inputs and the fact counts 0 forever — `chela doctor`
    stays green through the next OOM, which is verbatim the hole this ticket's measured
    receipt named.

    Driven over the same real temp CHELA_DIR, so the seam itself is under test rather than
    the report that consumes it.
    """
    from chela import runtime_truth

    n = runtime_truth._restore_scan(NOW)

    # inbox.watches @3 · dispatcher.runs @9 · dispatcher.runs (judge) @10
    # · session-ids @5 and @7
    assert n == 5, f"the fact must count every dangling row across the live stores, got {n}"


def test_the_doctor_fact_counts_ZERO_when_every_row_is_current(live_stores, tmp_path):
    """The counterweight: a fact hard-wired to a nonzero count would satisfy the guard
    above while reporting a permanent false alarm."""
    from chela import runtime_truth

    assert runtime_truth._restore_scan(OLD) == 0, (
        "every fixture row is stamped OLD — asked about OLD, nothing is dangling"
    )


# --- the two DI defaults ----------------------------------------------------------------
#
# 🔴 GUARDS (CMX-195 round 8). `plan()` has exactly TWO callable defaults — verified by
# signature scan, not by reading:
#
#     chela.restore.plan(roster_lookup=)   -> chela.roster.window
#     chela.restore.plan(wid_for_session=) -> chela.sessions.wid_for_session
#
# `cmd_restore` passes neither, so the defaults ARE production, and every plan/_classify
# test in test_restore.py passes both explicitly. `roster.record`'s equivalent was closed
# in round 5; these are the last two of their kind in this feature.

def test_a_live_session_is_classified_REVIVABLE_through_plans_DEFAULT_resolver(
        live_stores, capsys):
    """🔴 Objective 2's whole distinction. Blank `wid_for_session` and every dangling row is
    MANUAL forever, and a row whose session is alive under a NEW address right now — the
    one-command fix this ticket exists to surface — reads identically to one whose agent is
    gone."""
    with pytest.raises(SystemExit):
        _drive(["restore"])

    out = capsys.readouterr().out
    rev = _line_with(out, "REVIVABLE", "[session-ids]")
    assert "@7 -> @42" in rev, (
        "no row was classified REVIVABLE — plan()'s default session resolver is severed. "
        "⛔ Scoped to the row line: 'REVIVABLE' also appears in the closing help text."
    )


def test_the_roster_join_supplies_the_relaunch_COMMAND_for_a_manual_row(live_stores, capsys):
    """🔴 Objective 1's payoff. The snapshot exists so a dangling row can be EXPLAINED:
    `_classify` reads cwd/name/session_id out of it, and without cwd
    `Verdict.manual_command()` returns None and every MANUAL row degrades to
    '(no cwd/session on record)'. Blank the join and the tick's 7-second snapshot is read
    by nobody."""
    with pytest.raises(SystemExit):
        _drive(["restore"])

    out = capsys.readouterr().out
    assert f"cd {CWD_FIVE}" in out and f"claude --resume {SID_DEAD}" in out, (
        "the MANUAL row lost its relaunch one-liner — plan()'s roster join is severed"
    )


def test_the_orphan_list_COUNTS_every_source_including_session_ids(live_stores, capsys):
    """🔴 A count, not a substring. `plan` prints `[session-ids] @5 MANUAL` for the same row
    either way, so every previous session-ids assertion was satisfiable by the WRONG path.
    The orphan block's own count is the one thing only `scan_all` produces."""
    with pytest.raises(SystemExit):
        _drive(["restore"])

    out = capsys.readouterr().out
    # watches @3 · runs @9 · runs judge @10 · session-ids @5 and @7
    assert "5 row(s) stamped by a tmux server that is no longer running" in out, (
        "the orphan list lost a source — its count is the only assertion scan_all alone "
        f"can satisfy. Got:\n{out}"
    )


def test_the_doctor_fact_compares_against_the_RUNNING_epoch(live_stores):
    """🔴 The fourth input to the scan, supplied by `_restore_read` rather than by the
    stores. Hand it a blank epoch and `epoch.is_dangling`'s two-known-halves rule marks
    EVERY row current: the fact counts 0 forever and `chela doctor` stays green through the
    next OOM — verbatim the hole this ticket's measured receipt named.

    Round 7 drove `_restore_scan` directly, so this argument was still unobserved."""
    from chela import runtime_truth

    live_stores.setattr(runtime_truth, "_tmux_or_unverifiable", lambda: "/usr/bin/tmux")
    obs = runtime_truth._restore_read()

    assert obs.value == 5, (
        f"the doctor fact must see the dangling rows, got {obs.value} — the running epoch "
        "never reached the scan"
    )


# --- the two enumerable classes, closed exhaustively -------------------------------------
#
# 🔴 GUARDS (CMX-195 round 9), scoped by grep rather than by the verdict.
#
# CLASS A — every bare-except swallow in this feature. There are exactly FOUR:
#   chela/main.py:1349        the reconcile tick's roster.record  -> guarded round 5
#   chela/main.py:1712        cmd_restore's list_runs             -> guarded round 6
#   chela/runtime_truth.py    _restore_scan's list_runs           -> below
#   chela/roster.py:56        _load's malformed-file degrade      -> tests/test_roster.py
#
# CLASS B — every FIELD the report renders. The store and wid halves are pinned by the
# assertions above; the rest were not, and a field that no test reads can be deleted from
# the format string in silence.

def _line_with(out, *markers):
    """The single output line containing every marker — or fail loudly.

    ⚠️ `x in out` is the wrong-path trap one level down: a session id appears BOTH as a
    session-ids orphan's label and inside a MANUAL row's `claude --resume` command, so a
    whole-output substring check passes while the field under test is gone. Assertions
    about a FIELD must be scoped to the LINE that field belongs to.
    """
    hits = [ln for ln in out.splitlines() if all(m in ln for m in markers)]
    assert len(hits) == 1, f"expected exactly one line with {markers}, got {hits}"
    return hits[0]


def test_the_orphan_report_NAMES_each_row_not_just_its_dead_address(live_stores, capsys):
    """🔴 `Orphan.label` is the only thing saying WHICH row a dangling `@N` is — the watch's
    note, `task_id (status)` for a dispatcher run, the SESSION ID for a session-ids row.
    Without it the operator gets a list of bare addresses from a server that no longer
    exists, which is unactionable: `@9` names nothing once the fleet is renumbered."""
    with pytest.raises(SystemExit):
        _drive(["restore"])

    out = capsys.readouterr().out
    assert "reviewing cmx-41" in out, "the inbox watch lost its note"
    assert "abc123 (running)" in out, "the dispatcher run lost its task id / status"
    assert "abc123 judge" in out, "the judge half lost its label"
    # Line-scoped: SID_DEAD also appears in the MANUAL row's relaunch command.
    orphan_line = _line_with(out, "[session-ids]", "@5", "(tmux epoch")
    assert SID_DEAD in orphan_line, "the session-ids row lost its identifying session id"
    assert OLD in orphan_line, "the orphan line lost the epoch that stamped it"


def test_the_verdict_lines_name_the_session_and_the_address_it_moved_to(live_stores, capsys):
    """🔴 Class B for the verdict block: a REVIVABLE line without its session id cannot be
    acted on, and one without `-> @new` does not say where the agent went."""
    with pytest.raises(SystemExit):
        _drive(["restore"])

    out = capsys.readouterr().out
    line = _line_with(out, "REVIVABLE", "[session-ids]")
    assert SID_LIVE in line, "the REVIVABLE line lost the session that is alive"
    assert "@7 -> @42" in line, "the REVIVABLE line lost the address it moved to"


def test_the_doctor_facts_OWN_swallow_survives_a_broken_runs_db(live_stores):
    """🔴 CLASS A, member 3. `_restore_scan` wraps `dispatcher.list_runs()` for exactly the
    reason cmd_restore's twin comment gives, and round 5 closed the CLI copy — this one has
    never been made to fail. A locked runs DB is the normal state right after the hard kill
    this fact exists to detect; propagating there turns `chela doctor` from a green lie into
    a crash, and the fact reports nothing either way."""
    from chela import dispatcher, runtime_truth

    def _boom(*a, **k):
        raise RuntimeError("database is locked")

    live_stores.setattr(dispatcher, "list_runs", _boom)

    # Must not raise, and must still count the rows the OTHER two stores hold.
    n = runtime_truth._restore_scan(NOW)

    assert n == 3, (  # inbox.watches @3 · session-ids @5 and @7 (the runs halves are lost)
        f"a dead runs DB must cost the runs rows and nothing else, got {n}"
    )


# 🔴 GUARD (CMX-195 round 10): the early return reads BOTH halves of the report.
#
# `scan_all` owns three sources (inbox.watches, dispatcher.runs, dispatcher.runs (judge))
# that `plan()` does NOT classify — so `verdicts == []` while `orphans` is non-empty is a
# REAL state, not a contrived one: it is exactly a box whose dead server left dispatcher
# and watch rows behind but whose orchestrator/bindings/session-ids happen to be clean.
# Drop `orphans` from the condition and `chela restore` prints "nothing orphaned — every
# stamped row matches the running tmux epoch" while three sources hold rows, and exits 0.
# That is the silent false negative this whole ticket was written from, reintroduced at the
# last branch before the report.
#
# ⚠️ `live_stores` always produces verdicts, so no test could reach this combination.

@pytest.fixture()
def orphans_but_no_verdicts(live_stores, tmp_path):
    """Strip every source `plan()` classifies, keep every source `scan_all` owns."""
    chela_dir = tmp_path / "chela"
    inbox_file = chela_dir / "inbox.json"
    store = json.loads(inbox_file.read_text())
    store["orchestrator"] = None            # plan's inbox arm -> nothing
    store["orchestrator_epoch"] = None
    store["orchestrator_session"] = None
    inbox_file.write_text(json.dumps(store))
    (chela_dir / "telegram-bindings.json").write_text(json.dumps(
        {"chat_id": "-100777", "bindings": {}, "epochs": {}}))
    (chela_dir / "session-ids.json").write_text(json.dumps({}))
    return live_stores


def test_orphans_with_no_verdicts_must_never_report_nothing_orphaned(
        orphans_but_no_verdicts, capsys):
    main.cmd_restore(SimpleNamespace())     # no MANUAL row -> exits 0

    out = capsys.readouterr().out
    assert "nothing orphaned" not in out, (
        "three sources hold dangling rows and the command claimed a clean bill of health"
    )
    # watches @3 · dispatcher.runs @9 · dispatcher.runs (judge) @10
    assert "3 row(s) stamped by a tmux server that is no longer running" in out
    assert "@3" in out and "@9" in out and "@10" in out
    # ⭐ The read-only contract is stated on EVERY report, not only the one with verdicts.
    # Indent that print into the `if verdicts:` block and this report — the pure-scan_all
    # one — silently loses the only line saying the command is safe to run and what to do.
    assert "never writes to a store" in out, (
        "a report with orphans but no verdicts must still state the contract"
    )
    # 🔴 ...and the EMPTY block must not print its header. A "0 classified row(s)" heading
    # over nothing tells the operator a section was checked and came back clean, which is
    # a different claim from "there was nothing to check here".
    assert "classified row(s)" not in out, (
        f"the verdict block printed a header with nothing to list. Got:\n{out}"
    )


def test_a_genuinely_clean_box_DOES_report_nothing_orphaned(live_stores, tmp_path, capsys):
    """The counterweight: deleting the early return entirely would satisfy the guard above
    while leaving a healthy box with no positive confirmation at all."""
    chela_dir = tmp_path / "chela"
    chela_dir.joinpath("inbox.json").write_text(json.dumps({
        "orchestrator": None, "orchestrator_epoch": None, "orchestrator_session": None,
        "orchestrator_name": None, "queue": [], "runs_seen": {}, "watches": {}}))
    chela_dir.joinpath("telegram-bindings.json").write_text(json.dumps(
        {"chat_id": "-100777", "bindings": {}, "epochs": {}}))
    chela_dir.joinpath("session-ids.json").write_text(json.dumps({}))
    from chela import dispatcher
    live_stores.setattr(dispatcher, "list_runs", lambda *a, **k: [])

    main.cmd_restore(SimpleNamespace())

    assert "nothing orphaned" in capsys.readouterr().out


def test_the_doctor_finding_TELLS_the_operator_what_to_do(live_stores):
    """🔴 GUARD (CMX-195): `Finding.detail` is rendered (`runtime_truth.py:108`), and a WARN
    that names a count but not a remedy makes the operator hunt for one. Blank it and
    `chela doctor` reports "N stamped row(s) from a dead epoch" with no way to act.

    ⛔ Asserted on the DETAIL text, not merely that a WARN was produced — a Finding with an
    empty detail still has the right level.
    """
    from chela import runtime_truth

    live_stores.setattr(runtime_truth, "_tmux_or_unverifiable", lambda: "/usr/bin/tmux")
    obs = runtime_truth._restore_read()
    findings = runtime_truth._restore_report(None, obs)

    assert len(findings) == 1 and findings[0].level == runtime_truth.WARN
    detail = findings[0].detail or ""
    assert "chela restore" in detail, "the WARN must name the command that explains the rows"
    # ⛔ Naming the two words is not saying what they MEAN. An operator who has never seen
    # this fact cannot act on "REVIVABLE or MANUAL"; the detail has to carry the difference.
    assert "alive under a new address" in detail, (
        f"REVIVABLE must be explained, not just named. Got: {detail!r}"
    )
    assert "relaunch command" in detail, (
        f"MANUAL must be explained, not just named. Got: {detail!r}"
    )
    # ...and the LABELS themselves, which are the strings `chela restore` actually prints.
    # Round 21 filed the words-without-meaning cut; this is its exact inverse, and a detail
    # that describes both verdicts without naming either cannot be matched to the output.
    assert "REVIVABLE" in detail and "MANUAL" in detail, (
        f"the detail must NAME the labels the command prints, not only describe them. "
        f"Got: {detail!r}"
    )


def test_the_doctor_finding_is_OK_and_quiet_when_nothing_is_orphaned(live_stores):
    """The counterweight: a report hard-wired to WARN would satisfy the guard above while
    crying wolf on every healthy box."""
    from chela import runtime_truth

    findings = runtime_truth._restore_report(None, runtime_truth.observed(0))
    assert len(findings) == 1 and findings[0].level == runtime_truth.OK


def test_the_verdict_block_COUNTS_what_it_classified(live_stores, capsys):
    """🔴 The twin of round 9's orphan-count guard, on the other block. `plan()` classifies
    three stores; the count is the only assertion that fails when a source stops reaching
    it AND the remaining rows still print. Substring checks on a store name cannot see it.
    """
    with pytest.raises(SystemExit):
        _drive(["restore"])

    out = capsys.readouterr().out
    # inbox.orchestrator @1 · telegram.bindings @2 · session-ids @5 and @7
    assert "4 classified row(s)" in out, (
        f"the verdict block miscounted what plan() returned. Got:\n{out}"
    )
    # ...and NAMES them. The third rendered surface with this invariant, after the doctor
    # Fact's `owned_by` and the CLI's CANNOT VERIFY: a count with no subject leaves the
    # operator unable to tell WHICH stores were even looked at.
    header = _line_with(out, "classified row(s)")
    for store in ("inbox orchestrator", "telegram-bindings", "session-ids"):
        assert store in header, (
            f"the header must name {store} as one of the three it looked at — every store "
            f"name also appears on its own ROW line, so a whole-output check cannot see a "
            f"store dropped from the header. Got: {header!r}"
        )


def test_the_report_STATES_its_own_read_only_contract(live_stores, capsys):
    """🔴 The closing line is the ONLY place `chela restore` says what it does and does not
    do, and the only remediation a REVIVABLE row ever gets — the write half is a separate
    ticket, so "act by hand" IS the instruction. Blank it and the command prints a list of
    dangling rows and stops, with nothing telling the operator it is safe to run or what to
    do next."""
    with pytest.raises(SystemExit):
        _drive(["restore"])

    out = capsys.readouterr().out
    assert "never writes to a store" in out, "the read-only contract must be stated"
    # There is no write half, so this line IS the remediation — for BOTH classes of row.
    assert "chela watch/register" in out, "a REVIVABLE row's remediation must be named"
    assert "re-dispatch" in out and "clear a row" in out, (
        f"a MANUAL row's remediations must be named too — naming only the REVIVABLE one "
        f"leaves the majority class with no next step. Got:\n{out}"
    )


def test_a_MANUAL_row_with_no_roster_join_SAYS_it_has_nothing_on_record(live_stores, capsys):
    """🔴 `manual_command()` returns None when the roster join found no cwd/session — and
    the '(no cwd/session on record)' suffix is the ONLY thing separating that row from one
    whose command was printed. Blank it and the two are indistinguishable: the operator
    reads a bare `[store] @N  MANUAL` and cannot tell whether a relaunch line was omitted
    or never existed.

    The bindings row is exactly this case: `telegram-bindings.json` stamps no session of
    its own, and @2 is absent from the roster.
    """
    with pytest.raises(SystemExit):
        _drive(["restore"])

    line = _line_with(capsys.readouterr().out, "[telegram.bindings]", "@2", "MANUAL")
    assert "no cwd/session on record" in line, (
        "a MANUAL row with nothing to relaunch from must say so, not go silent"
    )


# --- the doctor fact's remaining surfaces ------------------------------------------------
#
# 🔴 GUARDS (CMX-195 round 13). `_restore_read` has TWO unverifiable arms and the report has
# TWO rendered titles; each was half-guarded. Closed as a set, not one at a time.

def test_the_fact_CANNOT_VERIFY_when_tmux_is_not_on_PATH(live_stores):
    """🔴 The second unverifiable arm. `_tmux_or_unverifiable()` returning None means chela
    cannot even ask what epoch is running — turning that into `observed(0)` makes the fact
    report a clean bill of health it has no basis for. ⛔ A green check must never be the
    thing that could not look."""
    from chela import runtime_truth

    live_stores.setattr(runtime_truth, "_tmux_or_unverifiable", lambda: None)
    obs = runtime_truth._restore_read()

    assert obs.unverifiable, "no tmux on PATH is CANNOT VERIFY, never a pass"
    # ⛔ Pinned on text UNIQUE to this arm. "tmux" and "epoch" both appear in BOTH reasons,
    # so either word is satisfied by the OTHER arm's message — the two were swappable.
    assert "not on PATH" in obs.unverifiable, (
        f"arm 1 must say tmux is MISSING, got {obs.unverifiable!r}"
    )
    assert "no tmux server is running" not in obs.unverifiable, (
        "the two CANNOT VERIFY arms must stay distinguishable: 'install tmux' and 'start a "
        "server' are completely different next actions"
    )


def test_the_fact_CANNOT_VERIFY_when_no_tmux_server_is_running(live_stores):
    """🔴 The first arm, kept alongside its twin so neither can rot alone."""
    from chela import epoch, runtime_truth

    live_stores.setattr(runtime_truth, "_tmux_or_unverifiable", lambda: "/usr/bin/tmux")
    live_stores.setattr(epoch, "current", lambda: None)
    obs = runtime_truth._restore_read()

    assert obs.unverifiable, "no running server means no epoch to compare against"
    assert "no tmux server is running" in obs.unverifiable, (
        f"arm 2 must say the SERVER is down, not that tmux is absent, got {obs.unverifiable!r}"
    )
    assert "not on PATH" not in obs.unverifiable


def test_both_doctor_titles_NAME_the_condition(live_stores):
    """🔴 `cmd_doctor` prints every finding via `Finding.render()` == f"{symbol} {title}",
    so the title is the entire line an operator reads. Blank the OK title and a healthy box
    prints a bare tick that says nothing; blank the WARN title and the count vanishes."""
    from chela import runtime_truth

    ok = runtime_truth._restore_report(None, runtime_truth.observed(0))[0]
    assert "no stamped rows" in ok.title and "epoch" in ok.title, (
        f"the OK line must name what was checked, got {ok.title!r}"
    )

    live_stores.setattr(runtime_truth, "_tmux_or_unverifiable", lambda: "/usr/bin/tmux")
    warn = runtime_truth._restore_report(None, runtime_truth._restore_read())[0]
    assert "5" in warn.title and "chela restore" in warn.title, (
        f"the WARN line must carry the count and the command, got {warn.title!r}"
    )


def test_every_rendered_string_on_the_doctor_FACT_names_its_subject(live_stores):
    """🔴 GUARD (CMX-195 round 14): the Fact's own rendered fields, enumerated.

    Round 13 closed the reporter's OK and WARN titles; the Fact object carries three MORE
    strings a human reads — `name`, `declared_by`, and `owned_by` (the last is what the
    CANNOT VERIFY line renders as "who did not answer"). Blank any of them and doctor prints
    a row that names no subject, on the one fact whose entire job is to be noticed.

    ⭐ `unverifiable_level` is asserted too, and it is behavioural rather than cosmetic: if
    an unreadable owner rendered as OK instead of WARN, this fact would go green in exactly
    the case it exists to catch.
    """
    from chela import runtime_truth

    fact = next(f for f in runtime_truth.facts() if f.name == "restore.dead_epoch_rows")

    assert fact.declared_by and "never predicts" in fact.declared_by, (
        "the declared side must say chela does NOT predict this — it is read-only truth"
    )
    assert fact.owned_by and "tmux" in fact.owned_by, (
        "the owner must be named: a CANNOT VERIFY that does not say who failed to answer "
        "is unactionable"
    )
    for store in ("inbox.json", "runs table", "session-ids.json"):
        assert store in fact.owned_by, f"the owner must name {store} — it is one of the three"
    assert fact.unverifiable_level == runtime_truth.WARN, (
        "an unreadable owner must WARN, never render as a pass — this fact exists precisely "
        "for the case where nothing could be read"
    )


@pytest.fixture()
def verdicts_but_no_orphans(live_stores, tmp_path):
    """The mirror of `orphans_but_no_verdicts`: keep only what `plan()` classifies.

    ⚠️ Filed as a PAIR with its twin, per round 16's lesson — closing one member of a
    two-member class leaves the other exactly as unguarded as before.
    """
    chela_dir = tmp_path / "chela"
    inbox_file = chela_dir / "inbox.json"
    store = json.loads(inbox_file.read_text())
    store["watches"] = {}                     # scan_all's inbox source -> nothing
    inbox_file.write_text(json.dumps(store))
    (chela_dir / "session-ids.json").write_text(json.dumps({}))   # feeds BOTH blocks
    from chela import dispatcher
    live_stores.setattr(dispatcher, "list_runs", lambda *a, **k: [])   # scan_all's runs
    return live_stores


def test_verdicts_with_no_orphans_must_not_print_an_empty_ORPHAN_header(
        verdicts_but_no_orphans, capsys):
    """🔴 The twin. `plan()` classifies the inbox orchestrator and telegram-bindings rows,
    neither of which `scan_all` scans — so verdicts-without-orphans is a real state, and the
    orphan header over an empty list claims a section was checked and came back clean."""
    with pytest.raises(SystemExit):
        _drive(["restore"])

    out = capsys.readouterr().out
    assert "classified row(s)" in out, "the verdict block must still print"
    assert "stamped by a tmux server that is no longer running" not in out, (
        f"the orphan block printed a header with nothing to list. Got:\n{out}"
    )
    assert "never writes to a store" in out
