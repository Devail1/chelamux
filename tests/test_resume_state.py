"""Tests for ``chela.resume_state`` — the durable per-session-id retry bound `chela restore
--resume` (issue #468) reads BEFORE relaunching a session whose previous resume did not come
up alive.

Exercised against a temp ``CHELA_DIR`` (the same pattern ``tests/test_sessionids.py`` uses)
so no real ``~/.chela/resume-attempts.json`` is touched.
"""
from __future__ import annotations

import importlib

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
