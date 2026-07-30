"""Tests for ``chela.roster`` — the durable, epoch-keyed fleet snapshot
:mod:`chela.restore` joins a dangling row against (CMX-195 objective 1).

Exercised against a temp ``CHELA_DIR`` (same pattern as ``tests/test_sessionids.py``) so no
real ``~/.chela/roster.json`` is touched.
"""
from __future__ import annotations

import importlib
import json

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
        seen["panes_called"] = True
        return LIVE_PANES

    def _fake_session_of_window(wid, pane_map=None):
        seen["pane_map"] = pane_map
        return {"@1": "sid-alpha"}.get(wid)

    monkeypatch.setattr(sessions, "panes", _fake_panes)
    monkeypatch.setattr(sessions, "session_of_window", _fake_session_of_window)

    # NO session_for argument — this is exactly how _reconcile_loop calls it.
    rec = roster.record({"@1": "orch"}, {"@1"}, OLD, _cwd_for({"@1": "/home/x"}))

    assert rec["windows"]["@1"]["session_id"] == "sid-alpha", (
        "the default session_for must resolve through sessions.session_of_window — "
        "a blank session_id makes every REVIVABLE verdict impossible"
    )
    assert seen.get("panes_called"), "one panes() snapshot must be shared across the tick"
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
