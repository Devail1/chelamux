"""Tests for ``chela.roster`` — the durable, epoch-keyed fleet snapshot
:mod:`chela.restore` joins a dangling row against (CMX-195 objective 1).

Exercised against a temp ``CHELA_DIR`` (same pattern as ``tests/test_sessionids.py``) so no
real ``~/.chela/roster.json`` is touched.
"""
from __future__ import annotations

import importlib
import json
import time

import pytest

OLD = "786-1784045825"
NEW = "9001-1784099999"


@pytest.fixture()
def roster(tmp_path, monkeypatch):
    """Reload ``chela.roster`` (and ``chela.config``) with ``CHELA_DIR`` pointed at a temp
    dir, so the module-level ``_STORE`` path picks up the override."""
    monkeypatch.setenv("CHELA_DIR", str(tmp_path / "chela"))
    import chela.config as config
    importlib.reload(config)
    import chela.roster as roster_mod
    importlib.reload(roster_mod)
    return roster_mod


def _cwd_for(mapping):
    return lambda wid: mapping.get(wid)


def _session_for(mapping):
    return lambda wid: mapping.get(wid)


def test_record_writes_first_seen_last_seen_and_windows(roster):
    rec = roster.record(
        {"@1": "orch", "@2": "cmx-9"}, {"@1", "@2"}, OLD,
        _cwd_for({"@1": "/a", "@2": "/b"}),
        session_for=_session_for({"@1": "sid-1", "@2": "sid-2"}),
    )
    assert rec["windows"] == {
        "@1": {"name": "orch", "cwd": "/a", "session_id": "sid-1"},
        "@2": {"name": "cmx-9", "cwd": "/b", "session_id": "sid-2"},
    }
    assert rec["first_seen"] == rec["last_seen"]


def test_unknown_epoch_records_nothing(roster):
    assert roster.record({"@1": "x"}, {"@1"}, None, _cwd_for({}),
                         session_for=lambda w: None) is None
    assert roster.window(None, "@1") is None


def test_only_agent_windows_are_recorded(roster):
    live = {"@1": "orch", "@2": "cmx-9", "@3": "a shell"}
    rec = roster.record(live, {"@1", "@2"}, OLD, _cwd_for({"@1": "/a", "@2": "/b"}),
                        session_for=_session_for({"@1": "s1", "@2": "s2"}))
    assert set(rec["windows"]) == {"@1", "@2"}


# --------------------------------------------------------------------------
# GUARD: the roster survives the flip — epoch-keyed, not flat
# --------------------------------------------------------------------------

def test_roster_survives_the_flip(roster):
    """Record epoch A with 3 windows; the server restarts into epoch B. `window()` must
    still name all 3 of A's windows with their cwds — a flat (non-epoch-keyed) store would
    have exactly one `windows` bucket and B's record would overwrite A's."""
    roster.record(
        {"@1": "x", "@2": "y", "@3": "z"}, {"@1", "@2", "@3"}, "A",
        _cwd_for({"@1": "/x", "@2": "/y", "@3": "/z"}),
        session_for=_session_for({"@1": "s1", "@2": "s2", "@3": "s3"}),
    )
    roster.record({"@1": "new"}, {"@1"}, "B", _cwd_for({"@1": "/new"}),
                  session_for=_session_for({"@1": "s-new"}))

    for wid, cwd in (("@1", "/x"), ("@2", "/y"), ("@3", "/z")):
        row = roster.window("A", wid)
        assert row is not None, f"{wid} from epoch A should still be recorded"
        assert row["cwd"] == cwd

    # epoch B's own record is unaffected and distinct
    assert roster.window("B", "@1") == {"name": "new", "cwd": "/new", "session_id": "s-new"}


def test_retains_only_the_last_5_epochs(roster):
    for i in range(7):
        roster.record({"@1": "x"}, {"@1"}, f"E{i}", _cwd_for({"@1": "/x"}),
                      session_for=lambda w: "sid")
    data = json.loads(roster._STORE.read_text())
    assert len(data["epochs"]) == 5
    assert set(data["epochs"]) == {f"E{i}" for i in range(2, 7)}
    # the oldest two were pruned
    assert roster.window("E0", "@1") is None
    assert roster.window("E1", "@1") is None
    assert roster.window("E6", "@1") is not None


# --------------------------------------------------------------------------
# GUARD: an interrupted save leaves the previous roster readable
# --------------------------------------------------------------------------

def test_interrupted_save_leaves_the_previous_roster_readable(roster, monkeypatch):
    roster.record({"@1": "x"}, {"@1"}, "A", _cwd_for({"@1": "/x"}),
                  session_for=lambda w: "sid-a")

    def boom(*_a, **_kw):
        raise OSError("simulated kill mid-write")

    monkeypatch.setattr(roster.os, "replace", boom)
    with pytest.raises(OSError):
        roster.record({"@1": "y"}, {"@1"}, "B", _cwd_for({"@1": "/y"}),
                      session_for=lambda w: "sid-b")
    monkeypatch.undo()   # restore the real os.replace before reading back

    data = json.loads(roster._STORE.read_text())
    assert "A" in data["epochs"]
    assert "B" not in data["epochs"]


# --------------------------------------------------------------------------
# archive() — used by restore.apply() for MANUAL rows
# --------------------------------------------------------------------------

def test_record_defaults_to_resolving_the_session_from_the_live_panes(roster, monkeypatch):
    from chela import sessions

    seen = {}

    LIVE_PANES = {"@1": object(), "@9": object()}      # a distinctive, NON-empty map

    def _fake_panes():
        seen["panes_calls"] = seen.get("panes_calls", 0) + 1
        return LIVE_PANES

    def _fake_session_of_window(wid, pane_map=None):
        seen["pane_map"] = pane_map
        return {"@1": "sid-alpha"}.get(wid)

    monkeypatch.setattr(sessions, "panes", _fake_panes)
    monkeypatch.setattr(sessions, "session_of_window", _fake_session_of_window)

    # NO session_for argument — this is exactly how _reconcile_loop calls it.
    # THREE windows, because the promise is one snapshot per TICK, not per window.
    rec = roster.record({"@1": "orch", "@9": "a", "@8": "b"}, {"@1", "@9", "@8"}, OLD,
                        _cwd_for({"@1": "/home/x", "@9": "/a", "@8": "/b"}))

    assert rec["windows"]["@1"]["session_id"] == "sid-alpha", (
        "the default session_for must resolve through sessions.session_of_window — "
        "a blank session_id makes every REVIVABLE verdict impossible"
    )
    # ⛔ NOT "was it called" — the promise in record()'s docstring is ONE snapshot shared
    # across the tick ("this adds no tmux call of its own beyond session_for"). Calling
    # panes() per window still passes any was-it-called or identity check, while turning
    # one tmux+/proc scan into N on the daemon's hot path.
    assert seen.get("panes_calls") == 1, (
        f"panes() must be called ONCE per tick, not once per window — got "
        f"{seen.get('panes_calls')} calls for 3 windows"
    )
    # ⛔ NOT `is not None` — `{}` is not None, and handing `session_of_window` an EMPTY map
    # makes it resolve nothing while still "passing a pane map". Assert IDENTITY with what
    # panes() actually returned; that is the only thing an empty dict cannot satisfy.
    assert seen["pane_map"] is LIVE_PANES, (
        f"the LIVE pane snapshot must be handed through, got {seen['pane_map']!r} — an "
        "empty map resolves no session, so every recorded session_id would be blank"
    )


def test_record_default_session_lands_on_disk_not_just_in_the_return_value(roster):
    """The return value is a convenience; the FILE is what restore joins against."""
    from chela import sessions

    sessions_panes = {}

    def _fake_session_of_window(wid, pane_map=None):
        return {"@2": "sid-beta"}.get(wid)

    orig_panes, orig_sow = sessions.panes, sessions.session_of_window
    sessions.panes = lambda: sessions_panes
    sessions.session_of_window = _fake_session_of_window
    try:
        roster.record({"@2": "cmx-9"}, {"@2"}, NEW, _cwd_for({"@2": "/tmp"}))
    finally:
        sessions.panes, sessions.session_of_window = orig_panes, orig_sow

    on_disk = json.loads((roster._STORE).read_text())
    assert on_disk["epochs"][NEW]["windows"]["@2"]["session_id"] == "sid-beta"


# 🔴 GUARD (CMX-195 round 9): a malformed roster.json must DEGRADE, never crash its reader.
#
# `_load`'s shape check is the read half of this module's never-lose-data story — the same
# paragraph that justifies the atomic `os.replace` on the write side. Neuter it and a file
# that is valid JSON but the wrong SHAPE (a list, a string, `epochs` not a dict) reaches
# `data["epochs"]` and raises TypeError out of every caller: `plan()`'s roster join runs
# inside `chela restore`, and `record()` runs inside the chela-telegram daemon's reconcile
# tick — so a corrupt snapshot would take down the tick that exists to rewrite it, which is
# unrecoverable without a human deleting the file.

@pytest.mark.parametrize("garbage", ['[]', '"nope"', '{"epochs": []}', '{"epochs": "x"}',
                                     'not json at all'])
def test_a_malformed_roster_degrades_to_empty_instead_of_raising(roster, garbage):
    roster._STORE.parent.mkdir(parents=True, exist_ok=True)
    roster._STORE.write_text(garbage)

    # The read path: no row, no exception.
    assert roster.window(OLD, "@1") is None

    # ...and the WRITE path still recovers, rather than inheriting the corruption.
    rec = roster.record({"@1": "orch"}, {"@1"}, NEW, _cwd_for({"@1": "/home/x"}),
                        _session_for({"@1": "sid-1"}))
    assert rec["windows"]["@1"]["cwd"] == "/home/x"
    assert json.loads(roster._STORE.read_text())["epochs"][NEW]["windows"]["@1"]


def test_prune_evicts_the_STALEST_epoch_not_the_lowest_key(roster):
    """🔴 GUARD (CMX-195 round 17): retention is ranked by `last_seen`, not by key order.

    A tmux epoch is `<pid>-<start_time>`, and pids WRAP — so lexical/numeric key order and
    recency are unrelated. `test_retains_only_the_last_5_epochs` records E0..E6, where key
    order, insertion order and last_seen all coincide, so sorting by the KEY passes it while
    evicting whatever happens to sort first. Here they deliberately disagree: the epoch with
    the lowest key is the MOST recent, so a key-sorted prune would delete exactly the one a
    restore is most likely to need.
    """
    # keys descend while recency ascends — 'z' is stalest, 'a' is newest
    for i, key in enumerate(["z", "y", "x", "w", "v", "u", "a"]):
        roster.record({"@1": "x"}, {"@1"}, key, _cwd_for({"@1": f"/{key}"}),
                      session_for=lambda w: "sid")
        data = json.loads(roster._STORE.read_text())
        data["epochs"][key]["last_seen"] = 1000.0 + i      # recorded order == recency
        roster._save(data)
    # one more record forces a prune with the doctored last_seen values in place
    roster.record({"@1": "x"}, {"@1"}, "final", _cwd_for({"@1": "/final"}),
                  session_for=lambda w: "sid")

    kept = set(json.loads(roster._STORE.read_text())["epochs"])
    assert "z" not in kept and "y" not in kept, "the STALEST epochs must be the ones evicted"
    assert "a" in kept, (
        f"the most RECENT epoch was evicted — prune is ranked by key, not last_seen. Kept: {kept}"
    )


# --------------------------------------------------------------------------
# archive — CMX-196's audit trail for a MANUAL row `chela restore --apply` removed
# --------------------------------------------------------------------------

def test_archive_appends_the_entry_stamped_with_archived_at(roster):
    entry = {"store": "session-ids", "wid": "@5", "session_id": "sid-dead",
              "cwd": "/home/x", "label": "sid-dead", "stamped_epoch": OLD}

    roster.archive(entry)

    data = json.loads(roster._ARCHIVE_STORE.read_text())
    assert len(data["archived"]) == 1
    row = data["archived"][0]
    # The COMPLETE entry — the archive is the only remaining record of a row whose live
    # store no longer has it, so every field the caller handed over must survive.
    assert {k: row[k] for k in entry} == entry, (
        f"the archived entry lost or altered a field: {row!r}"
    )
    # ⛔ NOT `"archived_at" in row` — None and 0 are both "in row", and an entry stamped
    # with either cannot be dated later, which is the one thing archiving is FOR.
    assert isinstance(row["archived_at"], (int, float)) and row["archived_at"] > 1_700_000_000, (
        f"archived_at must be a real timestamp, got {row['archived_at']!r}"
    )
    assert abs(row["archived_at"] - time.time()) < 60, "archived_at must be NOW, not a constant"


def test_archive_never_touches_the_epochs_section(roster):
    """🔴 The archive lives in its OWN FILE — it must never collide with or overwrite
    `record`'s epoch-keyed snapshot in `roster.json`, which a later `window()` join still
    depends on."""
    roster.record({"@1": "orch"}, {"@1"}, OLD, _cwd_for({"@1": "/home/x"}),
                  _session_for({"@1": "sid-1"}))

    roster.archive({"store": "session-ids", "wid": "@5", "session_id": "s",
                     "cwd": None, "label": "", "stamped_epoch": OLD})

    assert roster.window(OLD, "@1") == {"name": "orch", "cwd": "/home/x", "session_id": "sid-1"}


def test_archive_writes_a_separate_file_from_roster_json(roster):
    """🔴 GUARD (round-2 rework): `archive()` and `record()` must never share a file — see
    the module docstring's "one writer per file" rationale. `roster.json` must not gain an
    `archived` key, and `roster-archive.json` must not gain an `epochs` key."""
    roster.record({"@1": "orch"}, {"@1"}, OLD, _cwd_for({"@1": "/home/x"}),
                  _session_for({"@1": "sid-1"}))
    roster.archive({"store": "session-ids", "wid": "@5", "session_id": "s",
                     "cwd": None, "label": "", "stamped_epoch": OLD})

    assert roster._STORE != roster._ARCHIVE_STORE
    roster_data = json.loads(roster._STORE.read_text())
    archive_data = json.loads(roster._ARCHIVE_STORE.read_text())
    assert "archived" not in roster_data, "archive() must not write into roster.json"
    assert "epochs" not in archive_data, "record() must not write into roster-archive.json"


def test_record_never_touches_the_archive_store(roster):
    """🔴 GUARD (round-2 rework): the daemon's per-tick writer must never touch the archive
    file, even when it already holds rows — a shared file (or a shared writer) is exactly
    the hazard the split into two files exists to close."""
    roster.archive({"store": "session-ids", "wid": "@5", "session_id": "s",
                     "cwd": None, "label": "", "stamped_epoch": OLD})
    before = roster._ARCHIVE_STORE.read_bytes()

    roster.record({"@1": "orch"}, {"@1"}, NEW, _cwd_for({"@1": "/home/x"}),
                  _session_for({"@1": "sid-1"}))

    after = roster._ARCHIVE_STORE.read_bytes()
    assert after == before, "record() must never touch roster-archive.json"


def test_interleaved_archive_and_record_both_survive(roster, monkeypatch):
    """🔴 GUARD (round-2 rework): simulate the real hazard the review flagged — `archive()`
    loads its file, a `record()` (the daemon's reconcile tick) completes in between, then
    `archive()` saves. With two separate files both writes must survive; if archive() and
    record() were ever merged back into one file, whichever finishes saving last would
    silently erase the other's write, and this must go red."""
    roster.record({"@1": "orch"}, {"@1"}, OLD, _cwd_for({"@1": "/home/x"}),
                  _session_for({"@1": "sid-1"}))

    orig_load_archive = roster._load_archive

    def load_then_interleave_a_record(*a, **kw):
        data = orig_load_archive(*a, **kw)
        roster.record({"@2": "cmx-9"}, {"@2"}, NEW, _cwd_for({"@2": "/tmp"}),
                      _session_for({"@2": "sid-2"}))
        return data

    monkeypatch.setattr(roster, "_load_archive", load_then_interleave_a_record)

    roster.archive({"store": "session-ids", "wid": "@5", "session_id": "s",
                     "cwd": None, "label": "", "stamped_epoch": OLD})

    archive_data = json.loads(roster._ARCHIVE_STORE.read_text())
    assert len(archive_data["archived"]) == 1, "the archive write must survive the interleave"
    assert roster.window(NEW, "@2") == {"name": "cmx-9", "cwd": "/tmp", "session_id": "sid-2"}, (
        "the interleaved record() write must survive too"
    )
    assert roster.window(OLD, "@1") == {"name": "orch", "cwd": "/home/x", "session_id": "sid-1"}


def test_archive_appends_rather_than_overwrites(roster):
    roster.archive({"store": "session-ids", "wid": "@5", "session_id": "s1",
                     "cwd": None, "label": "", "stamped_epoch": OLD})
    roster.archive({"store": "inbox.orchestrator", "wid": "@1", "session_id": "s2",
                     "cwd": None, "label": "", "stamped_epoch": OLD})

    data = json.loads(roster._ARCHIVE_STORE.read_text())
    assert [r["wid"] for r in data["archived"]] == ["@5", "@1"], (
        "a second archive call must append, not replace, the first"
    )


def test_archive_is_bounded_and_evicts_the_oldest_first(roster):
    """🔴 An unbounded archive would grow forever; the eviction must drop the OLDEST rows,
    keeping the ones most likely to still matter."""
    for i in range(roster._MAX_ARCHIVED + 5):
        roster.archive({"store": "session-ids", "wid": f"@{i}", "session_id": "s",
                         "cwd": None, "label": "", "stamped_epoch": OLD})

    data = json.loads(roster._ARCHIVE_STORE.read_text())
    assert len(data["archived"]) == roster._MAX_ARCHIVED
    assert data["archived"][0]["wid"] == "@5", "the oldest 5 rows must be the ones evicted"
    assert data["archived"][-1]["wid"] == f"@{roster._MAX_ARCHIVED + 4}"


@pytest.mark.parametrize("garbage", ['[]', '"nope"', '{"archived": {}}', '{"archived": 3}',
                                     'not json at all'])
def test_a_malformed_archive_store_degrades_to_empty_instead_of_raising(roster, garbage):
    """🔴 GUARD (round-4): `_load_archive`'s shape check is the twin of `_load`'s, which
    `test_a_malformed_roster_degrades_to_empty_instead_of_raising` already pins. A file that
    is valid JSON but the wrong SHAPE would reach `data.setdefault("archived", [])` on a
    list/str and raise out of `archive()` — which runs inside `chela restore --apply`, AFTER
    some rows have already been archived-and-removed. A crash there is the one moment this
    command must survive.
    """
    roster._ARCHIVE_STORE.parent.mkdir(parents=True, exist_ok=True)
    roster._ARCHIVE_STORE.write_text(garbage)

    roster.archive({"store": "session-ids", "wid": "@5", "session_id": "s",
                    "cwd": None, "label": "", "stamped_epoch": OLD})

    data = json.loads(roster._ARCHIVE_STORE.read_text())
    assert [r["wid"] for r in data["archived"]] == ["@5"], (
        "a malformed archive must be replaced, not raise and abort a half-done --apply"
    )


def test_the_archive_bound_survives_a_whole_apply_run(roster):
    """🔴 GUARD (CMX-196 round 8): the cap must comfortably exceed ONE `--apply` run.

    The archive is the only remaining record of a row `--apply` deleted from its live store,
    so a sweep must never evict rows archived in the SAME run — the operator would be left
    with rows gone from both places. A dead tmux server can orphan a row per fleet window
    plus one per dispatcher run half, so a realistic single run archives tens of rows.

    ⚠️ The existing retention test is written in terms of `_MAX_ARCHIVED` itself, so it
    passes at ANY cap — including 2. A test parameterised by the value it should pin cannot
    pin it; this asserts the literal, and then proves the behaviour at a realistic run size.
    """
    assert roster._MAX_ARCHIVED >= 100, (
        f"the archive cap is {roster._MAX_ARCHIVED} — too small to hold one --apply run "
        "without evicting its own entries"
    )

    for i in range(100):
        roster.archive({"store": "session-ids", "wid": f"@{i}", "session_id": f"s{i}",
                        "cwd": None, "label": "", "stamped_epoch": OLD})

    data = json.loads(roster._ARCHIVE_STORE.read_text())
    assert len(data["archived"]) == 100, "a single run's archives evicted each other"
    assert data["archived"][0]["wid"] == "@0", "the FIRST row of the run was swept away"
