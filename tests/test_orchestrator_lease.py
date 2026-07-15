"""🎭 The attended-lease — the supervision gate that keeps the auto-launched orchestrator
human-attended. These corrupt each invariant (expiry, fail-closed-on-corruption, clamp) and
watch it go red.

The lease's whole job is to be a bounded, human-refreshed "I am here". Two invariants carry
that: an EXPIRED lease is NOT active (or supervision outlives the human), and an UNREADABLE
lease is NOT active either (or a byte of corruption grants an auto-launch nobody asked for).
"""
from __future__ import annotations

from chela.personas import lease


def test_grant_makes_a_lease_active_and_release_closes_it():
    granted = lease.grant(ttl_seconds=600)
    assert lease.active() is not None
    assert lease.active().by == granted.by
    released = lease.release()
    assert released is not None
    assert lease.active() is None
    # idempotent — releasing when none is set returns None, never raises
    assert lease.release() is None


def test_an_expired_lease_is_not_active():
    """🔴 EXPIRY — the load-bearing invariant. A lease is active only until it expires; corrupt
    ``Lease.expired`` to always return False and this goes red (a lapsed lease would still
    supervise, which is exactly 'unattended')."""
    granted = lease.grant(ttl_seconds=600)
    # one second BEFORE expiry: active. one second AFTER: gone.
    assert lease.active(now=granted.expires_at - 1) is not None
    assert lease.active(now=granted.expires_at + 1) is None
    assert granted.expired(now=granted.expires_at + 1) is True
    assert granted.expired(now=granted.expires_at - 1) is False


def test_a_corrupt_lease_file_reads_as_no_lease():
    """🔴 FAIL-CLOSED — an unparseable lease file must never GRANT supervision. Corrupt
    ``read`` to return a Lease on a bad file and this goes red."""
    lease.grant(ttl_seconds=600)
    lease.path().write_text("{ this is not json", encoding="utf-8")
    assert lease.read() is None
    assert lease.active() is None


def test_a_missing_field_reads_as_no_lease():
    # A file missing expires_at is as unusable as garbage — fail closed, not "active forever".
    lease.path().parent.mkdir(parents=True, exist_ok=True)
    lease.path().write_text('{"by": "someone"}', encoding="utf-8")
    assert lease.read() is None
    assert lease.active() is None


def test_ttl_is_clamped_to_the_max():
    """A lease cannot be granted for longer than MAX_TTL_SECONDS — the supervision window is
    bounded by design (a set-and-forget lease is the opposite of 'attended')."""
    granted = lease.grant(ttl_seconds=lease.MAX_TTL_SECONDS * 10)
    assert granted.remaining(now=granted.created_at) <= lease.MAX_TTL_SECONDS + 1


def test_grant_records_who(monkeypatch):
    monkeypatch.setenv("CHELA_WID", "@7")
    granted = lease.grant(ttl_seconds=60)
    assert granted.by == "@7"
    assert "@7" in granted.summary()


def test_summary_says_expired_after_the_fact():
    granted = lease.grant(ttl_seconds=60)
    assert "left" in granted.summary(now=granted.created_at)
    assert "EXPIRED" in granted.summary(now=granted.expires_at + 5)
