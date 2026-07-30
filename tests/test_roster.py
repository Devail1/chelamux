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

def test_archive_creates_the_epoch_record_when_it_was_never_seen_live(roster):
    """A row older than the retention window (or one the reconcile loop never observed
    live) still needs somewhere to land when it's archived — never silently discarded."""
    roster.archive(OLD, "@9", {"session_id": "sid-9", "cwd": "/gone"})
    row = roster.window(OLD, "@9")
    assert row["session_id"] == "sid-9"
    assert row["cwd"] == "/gone"
    assert row["archived"] is True


def test_archive_merges_into_an_existing_window_row(roster):
    roster.record({"@1": "x"}, {"@1"}, OLD, _cwd_for({"@1": "/x"}),
                  session_for=lambda w: "sid-1")
    roster.archive(OLD, "@1", {"note": "manual"})
    row = roster.window(OLD, "@1")
    assert row["cwd"] == "/x"           # original data preserved
    assert row["note"] == "manual"      # archive metadata merged in
    assert row["archived"] is True
