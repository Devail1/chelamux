"""CMX-113: id / version validators accept neither a trailing newline nor non-ASCII digits.

Python's ``$`` also matches just before a trailing ``\\n``, and ``\\d`` matches any Unicode
digit (``"١"`` is ARABIC-INDIC DIGIT ONE). A validator anchored with ``^…$`` therefore
admits ``"<valid>\\n"`` — and these values become file paths, route segments and tmux
targets. Each validator is driven through the exact call its call site makes (``.match``
on the compiled pattern, or the module's public helper), with fixtures written out as
literals rather than built from the module's own constants.
"""
import pytest

from chela import (gateanswer, hooks, overrides, release, rooms, sessions, share_requests,
                   share_sandbox, share_web_proxy, sidebar_archive, spawn)
from chela.dashboard import app as dashboard_app

OVERRIDE_ID = "override-0123456789abcdef01234567"

# (name, predicate, a value that must pass)
VALIDATORS = [
    ("overrides.valid_id", overrides.valid_id, OVERRIDE_ID),
    ("release._version_tuple", lambda v: release._version_tuple(v) is not None, "0.2.0"),
    ("sessions.SESSION_RE", lambda v: bool(sessions.SESSION_RE.match(v)),
     "0f8e2c1a-1b2c-4d5e-8f90-abcdef012345"),
    ("share_requests._SID_RE", lambda v: bool(share_requests._SID_RE.match(v)), "0123456789ab"),
    ("share_requests._DOMAIN_RE", lambda v: bool(share_requests._DOMAIN_RE.match(v)),
     "example.com"),
    ("share_sandbox._SID_RE", lambda v: bool(share_sandbox._SID_RE.match(v)), "0123456789ab"),
    ("share_web_proxy._HOST_RE", lambda v: bool(share_web_proxy._HOST_RE.match(v)),
     "api.example.com"),
    ("sidebar_archive._SESSION_ID_RE",
     lambda v: bool(sidebar_archive._SESSION_ID_RE.match(v)), "sess_1.a-b"),
    ("sidebar_archive.valid_key", sidebar_archive.valid_key, "w:@3"),
    ("rooms._ROOM_RE", lambda v: bool(rooms._ROOM_RE.match(v)), "room-1.a_b"),
    ("gateanswer._TUID_RE", lambda v: bool(gateanswer._TUID_RE.match(v)), "toolu_01AbC-d"),
    ("hooks._WID_RE", lambda v: bool(hooks._WID_RE.match(v)), "@12"),
    ("spawn._WINDOW_NAME_RE", lambda v: bool(spawn._WINDOW_NAME_RE.match(v)), "my-agent_1"),
    ("dashboard._WINDOW_NAME_RE", lambda v: bool(dashboard_app._WINDOW_NAME_RE.match(v)),
     "my-agent_1"),
]
_IDS = [name for name, _, _ in VALIDATORS]


@pytest.mark.parametrize("name,ok,good", VALIDATORS, ids=_IDS)
def test_a_valid_value_still_passes(name, ok, good):
    assert ok(good) is True


@pytest.mark.parametrize("suffix", ["\n", "\r\n", "\r", " "], ids=repr)
@pytest.mark.parametrize("name,ok,good", VALIDATORS, ids=_IDS)
def test_a_trailing_line_break_or_junk_is_rejected(name, ok, good, suffix):
    """🔴 GUARD: ``$`` let ``"<valid>\\n"`` through every one of these."""
    assert ok(good + suffix) is False


@pytest.mark.parametrize("name,ok,good", VALIDATORS, ids=_IDS)
def test_a_leading_line_break_or_prefix_is_rejected(name, ok, good):
    assert ok("\n" + good) is False
    assert ok("x\n" + good) is False


@pytest.mark.parametrize("name,ok,good", VALIDATORS, ids=_IDS)
def test_empty_is_rejected(name, ok, good):
    assert ok("") is False


# --- \d → [0-9]: only the two validators whose digits were \d ------------------------

@pytest.mark.parametrize("v", ["١.٢.٣", "0.٢.0", "０.2.0", "0.2.৩"])
def test_version_tuple_refuses_non_ascii_digits(v):
    """🔴 GUARD: ``\\d`` matched Arabic-Indic / fullwidth / Bengali digits and ``int()``
    happily converted them."""
    assert release._version_tuple(v) is None


@pytest.mark.parametrize("v,tup", [("0.2.0", (0, 2, 0)), ("10.20.300", (10, 20, 300))])
def test_version_tuple_still_parses_ascii(v, tup):
    assert release._version_tuple(v) == tup


@pytest.mark.parametrize("v", ["0.2.0\n", "0.2.0\r\n", "v0.2.0", "0.2", "0.2.0.1", "0.2.0-rc1"])
def test_version_tuple_refuses_malformed(v):
    assert release._version_tuple(v) is None


@pytest.mark.parametrize("wid", ["@١٢", "@１", "@1٢"])
def test_window_id_refuses_non_ascii_digits(wid):
    """🔴 GUARD: ``^@\\d+$`` admitted ``@`` + any Unicode digit."""
    assert not hooks._WID_RE.match(wid)


def test_override_id_rejects_wrong_length_and_case():
    assert overrides.valid_id(OVERRIDE_ID[:-1]) is False
    assert overrides.valid_id(OVERRIDE_ID + "0") is False
    assert overrides.valid_id(OVERRIDE_ID.upper()) is False
    assert overrides.valid_id(None) is False

