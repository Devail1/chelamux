"""Tests for ``chela.resume_state`` — the durable per-session-id retry bound `chela restore
--resume` (issue #468) reads BEFORE relaunching a session whose previous resume did not come
up alive.

Exercised against a temp ``CHELA_DIR`` (the same pattern ``tests/test_sessionids.py`` uses)
so no real ``~/.chela/resume-attempts.json`` is touched.
"""
from __future__ import annotations

import importlib
import json

import pytest


@pytest.fixture()
def resume_state(tmp_path, monkeypatch):
    """Reload ``chela.resume_state`` (and ``chela.config``) with ``CHELA_DIR`` pointed at a
    temp dir, so the module-level ``_STORE`` path picks up the override."""
    monkeypatch.setenv("CHELA_DIR", str(tmp_path / "chela"))
    import chela.config as config
    importlib.reload(config)
    import chela.resume_state as resume_state_mod
    importlib.reload(resume_state_mod)
    return resume_state_mod


def test_record_failure_bumps_the_try_count_each_call(resume_state):
    """🔴 GUARD: a count that never rises can never reach ``MAX_TRIES`` — ``blocked_reason``
    would be permanently unable to block anything."""
    assert resume_state.record_failure("sid-1", "first failure") == 1
    assert resume_state.record_failure("sid-1", "second failure") == 2
    assert resume_state.tries("sid-1") == 2


def test_blocked_reason_is_None_below_MAX_TRIES_and_set_once_reached(resume_state):
    """🔴 GUARD: ``max_tries`` must actually bound retries — below it a session may still be
    attempted; once reached, ``blocked_reason`` must return the persisted reason, not None.
    Passed explicitly (rather than relying on the module-level default) since that default
    is bound into the function signature at import time, not re-read from the module
    attribute on every call."""
    assert resume_state.blocked_reason("sid-1", max_tries=2) is None, (
        "never failed yet — not blocked"
    )

    resume_state.record_failure("sid-1", "auth wedge")
    assert resume_state.blocked_reason("sid-1", max_tries=2) is None, (
        "one failure with max_tries=2 must not block yet"
    )

    resume_state.record_failure("sid-1", "auth wedge again")
    assert resume_state.blocked_reason("sid-1", max_tries=2) == "auth wedge again", (
        "tries has reached max_tries — must now block with the persisted reason"
    )


def test_blocked_reason_defaults_to_the_module_MAX_TRIES_constant(resume_state):
    """Pin the actual default (1) — a single recorded failure blocks a session outright,
    the conservative bound the module docstring commits to."""
    assert resume_state.MAX_TRIES == 1
    resume_state.record_failure("sid-1", "spawn failed")
    assert resume_state.blocked_reason("sid-1") == "spawn failed"


def test_clear_actually_drops_the_failure_record(resume_state):
    """🔴 GUARD: a resume that later genuinely comes up alive must let a LATER, unrelated
    failure of that same session start counting from zero — ``clear`` must really delete the
    row, not silently keep it around under a disabled code path."""
    resume_state.record_failure("sid-1", "spawn failed")
    assert resume_state.blocked_reason("sid-1") == "spawn failed"

    resume_state.clear("sid-1")

    assert resume_state.blocked_reason("sid-1") is None, (
        "clear() must remove the record entirely, not just leave it unread"
    )
    assert resume_state.tries("sid-1") == 0
    # And the record really is gone from the persisted store, not just unreachable via the
    # public getters above — read the file back directly.
    data = resume_state._load()
    assert "sid-1" not in data


def test_clear_of_an_unknown_session_is_a_harmless_noop(resume_state):
    resume_state.clear("never-recorded")
    assert resume_state.blocked_reason("never-recorded") is None


# --------------------------------------------------------------------------
# GUARD (issue #471 finding 4): UNKNOWN MUST NOT READ AS OK — an empty persisted reason
# must not make an at-limit session read as retryable.
# --------------------------------------------------------------------------

def test_blocked_reason_still_blocks_when_the_persisted_reason_is_falsy(resume_state):
    """Every test above only ever persists a truthy reason via `record_failure`, so none of
    them can tell `entry.get("reason") or "<default>"` apart from a mutation that reads the
    reason bare (`entry.get("reason")`) — both pass every existing test. Write a row with an
    empty reason directly (bypassing `record_failure`, which the module docstring notes is a
    read-modify-write over the raw store) and prove `blocked_reason` still returns a truthy
    value once `tries` has reached the limit — never `None`, which the caller
    (`chela.restore.resume`) reads as "safe to relaunch"."""
    resume_state._save({"sid-1": {"tries": 1, "reason": ""}})

    result = resume_state.blocked_reason("sid-1")

    assert result is not None, (
        "an empty persisted reason at max_tries must still block — UNKNOWN MUST NOT READ AS OK"
    )
    assert result, "the fallback reason text must itself be truthy"


# --------------------------------------------------------------------------
# GUARD (issue #471 finding 5): `_save` is ATOMIC — a concurrent reader sees old or new,
# never half. Same shape as tests/test_roster.py:107's `os.replace` failure injection.
# --------------------------------------------------------------------------

def test_save_is_atomic_an_interrupted_replace_leaves_the_previous_store_readable(
    resume_state, monkeypatch
):
    resume_state.record_failure("sid-1", "first failure")

    def boom(*_a, **_kw):
        raise OSError("simulated kill mid-write")

    monkeypatch.setattr(resume_state.os, "replace", boom)
    with pytest.raises(OSError):
        resume_state.record_failure("sid-1", "second failure")
    monkeypatch.undo()   # restore the real os.replace before reading back

    data = json.loads(resume_state._STORE.read_text())
    assert data["sid-1"] == {"tries": 1, "reason": "first failure"}, (
        "a reader must see the OLD store intact, never a half-written new one"
    )


# --------------------------------------------------------------------------
# GUARD (issue #471 finding 6): `record_failure` RAISES on a store failure rather than
# swallowing it — a failure it could not record is a bound `blocked_reason` can no longer
# enforce, and the caller needs to know.
# --------------------------------------------------------------------------

def test_record_failure_raises_when_the_store_write_fails(resume_state, monkeypatch):
    def boom(_data):
        raise OSError("disk full")

    monkeypatch.setattr(resume_state, "_save", boom)

    with pytest.raises(OSError):
        resume_state.record_failure("sid-1", "spawn failed")


# --------------------------------------------------------------------------
# GUARD (issue #471 finding 7): `record_failure` refuses a falsy session id rather than
# silently recording nothing — an un-keyed failure is a bound `blocked_reason` can never
# enforce.
# --------------------------------------------------------------------------

def test_record_failure_refuses_a_falsy_session_id(resume_state):
    with pytest.raises(ValueError):
        resume_state.record_failure("", "some reason")
    with pytest.raises(ValueError):
        resume_state.record_failure(None, "some reason")

    assert resume_state._load() == {}, (
        "a refused call must record nothing at all, not a row keyed on an empty/None id"
    )
