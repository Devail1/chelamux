"""A disabled subsystem must ANNOUNCE itself — and a second process must be able to see
what the running daemon really came up with.

The bug these lock down: ``CHELA_DISPATCH_WORKFLOWS`` went missing from the env file, so
``DISPATCH_WORKFLOWS`` was ``[]``, so the daemon's ``if DISPATCH_WORKFLOWS:`` guard
skipped dispatch *and the reconcile loop that rides the same tick* — and said nothing.
The only tell was an absent log line, and `chela doctor` printed all-green because the
running env and the env file agreed (both wrong). So: OFF must be loud, OFF must name
BOTH things it takes down, and the daemon's real state must be readable from outside it.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path

import pytest

from chela import capabilities, config


@pytest.fixture
def chela_dir(tmp_path, monkeypatch):
    """Point CHELA_DIR at a tmp dir — the state file must never be the developer's real
    ~/.chela/daemon.json (see tests/conftest.py: a suite that reads live state is how
    green-in-CI/broken-live keeps happening)."""
    monkeypatch.setattr(config, "CHELA_DIR", tmp_path)
    return tmp_path


def _caps(monkeypatch, workflows):
    monkeypatch.setattr(config, "DISPATCH_WORKFLOWS", workflows)
    return {c.key: c for c in capabilities.effective()}


def test_empty_dispatch_workflows_is_off_and_names_both_dispatch_and_reconcile(monkeypatch):
    caps = _caps(monkeypatch, [])
    assert caps["dispatch"].on is False
    assert caps["reconcile"].on is False
    # The var must be named — a warning you cannot act on is a warning nobody acts on.
    assert "CHELA_DISPATCH_WORKFLOWS" in caps["dispatch"].detail
    assert "CHELA_DISPATCH_WORKFLOWS" in caps["dispatch"].fix
    # ...and the coupling must be spelled out: dispatch OFF also means reconcile OFF.
    assert "reconcil" in caps["reconcile"].detail.lower()
    assert "awaiting_review" in caps["reconcile"].detail
    assert caps["dispatch"].warn_when_off and caps["reconcile"].warn_when_off


def test_configured_dispatch_workflows_is_on(monkeypatch):
    caps = _caps(monkeypatch, [Path("/repo/WORKFLOW.md")])
    assert caps["dispatch"].on is True
    assert caps["reconcile"].on is True
    assert "/repo/WORKFLOW.md" in caps["dispatch"].detail


def test_every_capability_states_on_or_off_never_silence(monkeypatch, caplog):
    """Item 1: sweep every `if <config>:` guard in the daemon loop. Each one gets a
    startup line, and dispatch/reconcile get a WARNING — not an INFO nobody greps for."""
    caps = _caps(monkeypatch, [])
    with caplog.at_level(logging.INFO):
        capabilities.announce(list(caps.values()), logging.getLogger("chela.test"))

    for key in ("scheduler", "dispatch", "reconcile", "notify", "inbox", "terminals"):
        assert key in caplog.text or caps[key].label in caplog.text, key
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert {r.getMessage().split(":")[0] for r in warnings} == {
        caps["dispatch"].label, caps["reconcile"].label}


def test_a_configured_dispatcher_warns_about_nothing(monkeypatch, caplog):
    caps = _caps(monkeypatch, [Path("/repo/WORKFLOW.md")])
    with caplog.at_level(logging.INFO):
        capabilities.announce(list(caps.values()), logging.getLogger("chela.test"))
    assert not [r for r in caplog.records if r.levelno >= logging.WARNING]


def test_publish_then_live_round_trips_what_the_daemon_came_up_with(chela_dir, monkeypatch):
    caps = list(_caps(monkeypatch, [Path("/repo/WORKFLOW.md")]).values())
    capabilities.publish(caps, boot_id="b1")

    live = capabilities.live()
    assert live is not None
    assert live["pid"] == os.getpid() and live["boot_id"] == "b1"
    assert capabilities.live_capability("dispatch")["on"] is True
    assert capabilities.live_capability("dispatch")["workflows"] == ["/repo/WORKFLOW.md"]
    # The whole point of publishing: a SECOND process reads the daemon's state, not its
    # own config. Flip this process's config — the published truth must not move.
    monkeypatch.setattr(config, "DISPATCH_WORKFLOWS", [])
    assert capabilities.live_capability("dispatch")["on"] is True


def test_a_dead_daemons_state_file_is_not_a_running_daemon(chela_dir, monkeypatch):
    capabilities.publish(list(_caps(monkeypatch, []).values()))
    # pid 0 is skipped by the check; use a pid that cannot exist instead.
    text = capabilities.state_file().read_text().replace(
        f'"pid": {os.getpid()}', '"pid": 999999999')
    capabilities.state_file().write_text(text)
    assert capabilities.live() is None
    assert capabilities.live_capability("dispatch") is None


def test_clear_removes_the_state_and_is_safe_twice(chela_dir, monkeypatch):
    capabilities.publish(list(_caps(monkeypatch, []).values()))
    capabilities.clear()
    capabilities.clear()
    assert capabilities.live() is None


def test_no_state_file_means_unknown_not_off(chela_dir):
    """`None` is "we cannot see the daemon" — a caller must never read it as "off"."""
    assert capabilities.live() is None
    assert capabilities.live_capability("dispatch") is None
