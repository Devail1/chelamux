"""Tests for ``chela.sessionids`` — the dedicated ``wid -> session_id`` store
(docs/AGENT_IDENTITY.md slice 2a).

Exercised against a temp ``CHELA_DIR`` (like ``tests/test_launcher.py``) so no real
``~/.chela`` state is touched, and against a temp ``chela/telegram/bindings.py``
registry to prove the two stores are now independent files — the defect that made
round 1 of this ticket clobber the recorded id on the very next telegram reconcile
tick.
"""
from __future__ import annotations

import importlib

import pytest


@pytest.fixture()
def sessionids(tmp_path, monkeypatch):
    """Reload ``chela.sessionids`` (and ``chela.config``) with ``CHELA_DIR`` pointed
    at a temp dir, so the module-level ``_STORE`` path picks up the override."""
    monkeypatch.setenv("CHELA_DIR", str(tmp_path / "chela"))
    import chela.config as config
    importlib.reload(config)
    import chela.sessionids as sessionids_mod
    importlib.reload(sessionids_mod)
    return sessionids_mod


def test_round_trips_when_the_epoch_matches(sessionids, monkeypatch):
    monkeypatch.setattr(sessionids.epoch, "current", lambda: "111-222")
    assert sessionids.session_id_for("@3") is None       # never recorded
    sessionids.set_session_id("@3", "36358c6b-1111-4a11-8888-abc123456789")
    assert sessionids.session_id_for("@3") == "36358c6b-1111-4a11-8888-abc123456789"


def test_a_foreign_epoch_reads_as_none(sessionids, monkeypatch):
    monkeypatch.setattr(sessionids.epoch, "current", lambda: "111-222")
    sessionids.set_session_id("@3", "session-abc")

    monkeypatch.setattr(sessionids.epoch, "current", lambda: "999-888")
    assert sessionids.session_id_for("@3") is None


def test_an_unreadable_current_epoch_reads_as_none_without_deleting_the_row(
    sessionids, monkeypatch,
):
    monkeypatch.setattr(sessionids.epoch, "current", lambda: "111-222")
    sessionids.set_session_id("@3", "session-abc")

    monkeypatch.setattr(sessionids.epoch, "current", lambda: None)
    assert sessionids.session_id_for("@3") is None

    # Row survives the unreadable-epoch read: once tmux is reachable again the same
    # binding resolves rather than having been silently reaped.
    monkeypatch.setattr(sessionids.epoch, "current", lambda: "111-222")
    assert sessionids.session_id_for("@3") == "session-abc"


def test_a_second_spawn_into_the_same_wid_replaces_the_recorded_id(sessionids, monkeypatch):
    monkeypatch.setattr(sessionids.epoch, "current", lambda: "111-222")
    sessionids.set_session_id("@42", "first-uuid")
    sessionids.set_session_id("@42", "second-uuid")
    assert sessionids.session_id_for("@42") == "second-uuid"


def test_entries_returns_dangling_rows_unfiltered(sessionids, monkeypatch):
    """`entries()` is the reporting escape hatch (chela restore, CMX-195): unlike
    `session_id_for`, it must NOT withhold a row whose epoch no longer matches — a report
    that wants to say which rows are dangling needs to see them."""
    monkeypatch.setattr(sessionids.epoch, "current", lambda: "111-222")
    sessionids.set_session_id("@3", "session-abc")

    monkeypatch.setattr(sessionids.epoch, "current", lambda: "999-888")
    assert sessionids.session_id_for("@3") is None          # filtered read: gone
    assert sessionids.entries() == {"@3": {"session_id": "session-abc", "epoch": "111-222"}}


def test_survives_a_concurrent_telegram_bindings_save(sessionids, monkeypatch, tmp_path):
    """The reproduction that broke round 1: chela-telegram's daemon keeps ONE
    long-lived BindingRegistry object and saves it (to a DIFFERENT file) whenever a
    reconcile tick changes something. Recording a session id here must not be
    clobbered by that save, and vice versa — the two stores now live in separate
    files with no shared in-memory object to go stale."""
    from chela.telegram.bindings import BindingRegistry

    monkeypatch.setattr(sessionids.epoch, "current", lambda: "111-222")
    sessionids.set_session_id("@42", "aaaaaaaa-1111-4a11-8888-abc123456789")

    bindings_path = tmp_path / "telegram-bindings.json"
    daemon_registry = BindingRegistry.load(bindings_path)
    daemon_registry.bind("@7", "111", epoch="e1")
    daemon_registry.save(bindings_path)          # an unrelated reconcile-tick save

    daemon_registry2 = BindingRegistry.load(bindings_path)
    daemon_registry2.bind("@43", "222", epoch="e1")
    daemon_registry2.save(bindings_path)          # any further reconcile change

    assert sessionids.session_id_for("@42") == "aaaaaaaa-1111-4a11-8888-abc123456789"
