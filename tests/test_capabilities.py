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

import json
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


def test_a_malformed_state_file_is_no_daemon_at_all(chela_dir):
    """A whole-FILE failure — unparseable JSON, or valid JSON missing the shape ``live()``
    needs — must degrade to "no daemon at all": ``live()`` returns ``None``, full stop.
    Contrast with the malformed-ROW case below, where ONE bad entry inside an otherwise
    valid ``capabilities`` list must NOT take the rest of the file down with it."""
    capabilities.state_file().write_text("{not valid json", encoding="utf-8")
    assert capabilities.live() is None
    assert capabilities.live_capability("dispatch") is None

    capabilities.state_file().write_text(
        json.dumps({"pid": os.getpid()}), encoding="utf-8")   # no "capabilities" key at all
    assert capabilities.live() is None

    capabilities.state_file().write_text(
        json.dumps({"pid": os.getpid(), "capabilities": "not-a-list"}), encoding="utf-8")
    assert capabilities.live() is None


def test_a_malformed_row_degrades_in_place_not_the_whole_file(chela_dir, monkeypatch):
    """One bad ROW inside an otherwise well-formed ``capabilities`` list is a DIFFERENT
    failure than the whole-FILE case above, and must degrade differently: ``live()`` still
    returns the file, the bad row rides along unchanged, and every OTHER capability in the
    list stays readable by key. A fix that makes one bad row null out the whole response
    (matching the whole-FILE behavior instead) collapses a recoverable, partial failure
    into a total one."""
    caps = list(_caps(monkeypatch, [Path("/repo/WORKFLOW.md")]).values())
    capabilities.publish(caps, boot_id="row-test")

    data = json.loads(capabilities.state_file().read_text(encoding="utf-8"))
    assert isinstance(data["capabilities"][0], dict)
    data["capabilities"][0] = "not-a-capability-row"       # corrupt exactly one row
    capabilities.state_file().write_text(json.dumps(data), encoding="utf-8")

    live = capabilities.live()
    assert live is not None                                 # the FILE is still fine
    assert live["capabilities"][0] == "not-a-capability-row"   # bad row passed through as-is
    # every other, well-formed row is still reachable by key — the bad row didn't poison it
    assert capabilities.live_capability("dispatch") is not None
    assert capabilities.live_capability("dispatch")["on"] is True


def test_clear_removes_the_state_and_is_safe_twice(chela_dir, monkeypatch):
    capabilities.publish(list(_caps(monkeypatch, []).values()))
    capabilities.clear()
    capabilities.clear()
    assert capabilities.live() is None


def test_no_state_file_means_unknown_not_off(chela_dir):
    """`None` is "we cannot see the daemon" — a caller must never read it as "off"."""
    assert capabilities.live() is None
    assert capabilities.live_capability("dispatch") is None


def test_update_available_row_never_crashes_effective(monkeypatch):
    """CMX-142 part 1: whatever state the real checkout is in (no upstream, offline,
    not even a git repo), `effective()` must still return a well-formed row — never raise."""
    caps = _caps(monkeypatch, [])
    assert "update_available" in caps
    assert isinstance(caps["update_available"].on, bool)
    assert caps["update_available"].detail


def test_auto_merge_is_off_by_default_and_silent(monkeypatch, caplog):
    """OFF must not warn about auto-merge specifically — even though a *different* capability
    (the empty-workflows dispatcher, exercised elsewhere in this file) legitimately does."""
    caps = list(_caps(monkeypatch, []).values())
    auto_merge = next(c for c in caps if c.key == "auto_merge")
    assert auto_merge.on is False
    with caplog.at_level(logging.INFO):
        capabilities.announce(caps, logging.getLogger("chela.test.automerge-off"))
    assert not [r for r in caplog.records
                if r.levelno >= logging.WARNING and auto_merge.label in r.getMessage()]


def test_auto_merge_on_is_a_LOUD_warning_not_a_quiet_info(monkeypatch, caplog):
    """🔴 The whole point of `warn_when_on`: a risky capability staying ON must announce itself
    as loudly as a needed one going silently OFF. Corrupt `announce()` to fall through to the
    plain `log.info` branch for an ON+warn_when_on capability and this goes red."""
    monkeypatch.setattr(config, "AUTO_MERGE_ENABLED", True)
    caps = list(_caps(monkeypatch, []).values())
    auto_merge = next(c for c in caps if c.key == "auto_merge")
    assert auto_merge.on is True
    assert auto_merge.warn_when_on is True

    log = logging.getLogger("chela.test.automerge-on")
    with caplog.at_level(logging.WARNING, logger=log.name):
        capabilities.announce(caps, log)
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert any(auto_merge.label in r.getMessage() for r in warnings)
    assert any("UNATTENDED" in r.getMessage() for r in warnings)


def test_auto_update_is_off_by_default_and_silent(monkeypatch, caplog):
    """Same contract as auto_merge above, for the other fully-unattended act (CMX-148)."""
    caps = list(_caps(monkeypatch, []).values())
    auto_update = next(c for c in caps if c.key == "auto_update")
    assert auto_update.on is False
    with caplog.at_level(logging.INFO):
        capabilities.announce(caps, logging.getLogger("chela.test.autoupdate-off"))
    assert not [r for r in caplog.records
                if r.levelno >= logging.WARNING and auto_update.label in r.getMessage()]


def test_auto_update_on_is_a_LOUD_warning_not_a_quiet_info(monkeypatch, caplog):
    """🔴 Corrupt `announce()` to fall through to the plain `log.info` branch for an
    ON+warn_when_on capability and this goes red."""
    monkeypatch.setattr(config, "AUTO_UPDATE_ENABLED", True)
    caps = list(_caps(monkeypatch, []).values())
    auto_update = next(c for c in caps if c.key == "auto_update")
    assert auto_update.on is True
    assert auto_update.warn_when_on is True

    log = logging.getLogger("chela.test.autoupdate-on")
    with caplog.at_level(logging.WARNING, logger=log.name):
        capabilities.announce(caps, log)
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert any(auto_update.label in r.getMessage() for r in warnings)
    assert any("UNATTENDED" in r.getMessage() for r in warnings)


def test_a_daemon_that_boots_into_a_held_queue_announces_the_hold(chela_dir, monkeypatch, caplog):
    """A dispatcher that is ON and claiming nothing is a disabled subsystem wearing a
    green badge. Booting into a held queue and logging only "ON" is the nine-hour silence
    all over again."""
    from chela import hold

    hold.take(reason="rewriting the queue", ttl_seconds=600, by="@0")
    caps = list(_caps(monkeypatch, [Path("/tmp/WORKFLOW.md")]).values())
    dispatch = next(c for c in caps if c.key == "dispatch")
    assert dispatch.on is True                       # the capability exists...
    assert dispatch.extra["hold"]["reason"] == "rewriting the queue"   # ...and is HELD

    log = logging.getLogger("caps-hold-test")
    with caplog.at_level(logging.WARNING, logger=log.name):
        capabilities.announce(caps, log)
    assert "HELD" in caplog.text
    assert "--resume" in caplog.text
