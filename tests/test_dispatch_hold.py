"""The queue hold — "claim nothing, I am rewriting the queue."

The dispatcher wins every race for the queue: a PR merges, the slot frees, and the
orchestrator spends MINUTES writing the next task while the tick claims the old top item.
Twice in one day that cost the only concurrency slot for a full agent run. The hold is the
fix — ordering by intent instead of by who is faster — so these tests pin the properties
that make it one, and the ones that keep it from becoming a new failure mode:

* it blocks a CLAIM, and does NOT block reconciliation (CMX-53: those two ride one tick,
  and pausing dispatch must not stop merged PRs from freeing their slots),
* it survives a daemon restart (it is a file, not module state — the CMX-42 trap),
* it EXPIRES, loudly, so a crashed orchestrator cannot strand the fleet forever,
* and an unreadable hold file cannot stop the fleet either.
"""
from __future__ import annotations

import json
import logging
import time

import pytest

from chela import config, hold


@pytest.fixture
def chela_dir(tmp_path, monkeypatch):
    """CHELA_DIR under tmp — the hold must never be the developer's real ~/.chela one."""
    monkeypatch.setattr(config, "CHELA_DIR", tmp_path)
    return tmp_path


def test_take_then_read_holds_the_queue(chela_dir):
    held = hold.take(reason="rewriting the queue", ttl_seconds=600, by="@0")
    assert hold.active() is not None
    assert held.reason == "rewriting the queue"
    assert "rewriting the queue" in hold.active().summary()


def test_the_hold_is_a_file_so_it_survives_a_daemon_restart(chela_dir):
    # A hold that lives in one process's memory is not a hold: the CLI that takes it and
    # the PM2 daemon that honours it are different processes, and the daemon restarts.
    hold.take(reason="queue rewrite", ttl_seconds=600)
    assert (chela_dir / hold.HOLD_FILE_NAME).exists()

    # "Restart": nothing in memory, everything re-read from disk.
    reread = hold.active()
    assert reread is not None and reread.reason == "queue rewrite"


def test_release_resumes_and_is_idempotent(chela_dir):
    hold.take(ttl_seconds=600)
    released = hold.release()
    assert released is not None
    assert hold.active() is None
    # Releasing twice is a no-op, so an orchestrator can release unconditionally in its
    # error path without having to know whether it ever took one.
    assert hold.release() is None
    assert hold.active() is None


def test_an_expired_hold_is_not_active(chela_dir):
    hold.take(ttl_seconds=1)
    assert hold.active(now=time.time() + 2) is None


def test_expire_if_stale_self_releases_and_hands_back_the_hold_to_be_shouted_about(chela_dir):
    hold.take(reason="mid-rewrite", ttl_seconds=1, by="@0")
    time.sleep(1.05)

    expired = hold.expire_if_stale()
    assert expired is not None and expired.reason == "mid-rewrite"
    # Self-released: the file is GONE, so a crashed orchestrator cannot strand the fleet.
    assert not (chela_dir / hold.HOLD_FILE_NAME).exists()
    assert hold.active() is None
    # And it only fires once — the next tick has nothing to shout about.
    assert hold.expire_if_stale() is None


def test_an_unreadable_hold_file_is_no_hold_at_all(chela_dir, caplog):
    # A byte of corruption must never be able to park the fleet forever — and it must not
    # do it QUIETLY either, because a hold nobody can parse is a hold nobody can release.
    (chela_dir / hold.HOLD_FILE_NAME).write_text("{not json")
    with caplog.at_level(logging.WARNING):
        assert hold.read() is None
        assert hold.active() is None
    assert "unreadable" in caplog.text


def test_a_hold_missing_its_expiry_is_no_hold(chela_dir, caplog):
    # The expiry is the only thing between a crashed orchestrator and a dead fleet. A hold
    # without one is not a hold we are willing to honour.
    (chela_dir / hold.HOLD_FILE_NAME).write_text(json.dumps({"reason": "forever"}))
    with caplog.at_level(logging.WARNING):
        assert hold.active() is None


def test_the_ttl_is_capped_so_no_hold_can_outlive_the_day(chela_dir):
    held = hold.take(ttl_seconds=10 * hold.MAX_TTL_SECONDS)
    assert held.remaining() <= hold.MAX_TTL_SECONDS + 1


@pytest.mark.parametrize("value,expected", [
    ("900", 900), ("30m", 1800), ("2h", 7200), ("45s", 45), (" 90 ", 90),
])
def test_parse_ttl_reads_what_a_human_types(value, expected):
    assert hold.parse_ttl(value) == expected


@pytest.mark.parametrize("value", ["", "soon", "0", "-5m", "forever"])
def test_parse_ttl_refuses_a_duration_it_would_have_to_guess_at(value):
    # A hold whose duration we guessed at is a hold whose expiry we cannot promise.
    with pytest.raises(ValueError):
        hold.parse_ttl(value)


def test_parse_ttl_clamps_to_the_maximum(chela_dir):
    assert hold.parse_ttl("100h") == hold.MAX_TTL_SECONDS


def test_the_summary_says_expired_out_loud(chela_dir):
    held = hold.take(reason="r", ttl_seconds=60)
    assert "expires in" in held.summary()
    assert "EXPIRED" in held.summary(now=time.time() + 120)
