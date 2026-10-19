"""CMX-182, Mod 2/4: the opt-in Mod's turn-timestamp band, and its handshake with plugin/.

``mods/chela-hooks/hooks/timestamps-cases.ts`` is ONE fixture read by both suites. The
mod's ``claude plugin test`` cases prove it adds ``handshake`` to every MessageDisplay
payload while its band is on (and never while it is off); these prove the REAL daemon
endpoint plugin/'s hook curls then answers ``{}`` for that payload, and still stamps the
same payload without it. Together: one reply never shows its time in both places.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from chela import event_log
from chela.dashboard import app as dash

MOD = Path(__file__).resolve().parent.parent / "mods" / "chela-hooks"
TIMESTAMPS = json.loads(
    (MOD / "hooks" / "timestamps-cases.ts").read_text()
    .split("export const TIMESTAMPS =", 1)[1].rsplit("as const", 1)[0])


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("CHELA_EVENTS_FILE", str(tmp_path / "events.jsonl"))
    monkeypatch.setattr(dash.config, "TERMINAL_TIMESTAMPS", True)
    return dash.app.test_client()


def _post(client, body: dict) -> dict:
    resp = client.post("/hooks/MessageDisplay", json=body)
    assert resp.status_code == 200
    return resp.get_json()


def test_the_handshake_is_the_documented_literal():
    """The field name and value the daemon checks, spelled out, not read back from code."""
    assert TIMESTAMPS["handshake"] == {"chela_timestamp_owner": "mod"}
    assert TIMESTAMPS["body"]["index"] == 0


def test_without_the_handshake_plugin_path_stamps_the_reply(client):
    """Positive control: the same first-batch payload, unmarked, is stamped as before."""
    out = _post(client, dict(TIMESTAMPS["body"]))
    content = out["hookSpecificOutput"]["displayContent"]
    assert content.startswith("**[") and content.endswith("]** Done.\n")
    assert len(content) == len("**[HH:MM]** Done.\n")


def test_with_the_handshake_plugin_path_steps_aside(client):
    """🔴 GUARD: the payload the Mod sends while its band is on is never stamped."""
    assert _post(client, {**TIMESTAMPS["body"], **TIMESTAMPS["handshake"]}) == {}
    assert event_log.read()["events"] == []


@pytest.mark.parametrize("owner", ["Mod", "plugin", "", None, True])
def test_only_the_exact_handshake_value_steps_aside(client, owner):
    """A near-miss is not the Mod: plugin/ keeps stamping, so a reply is never left bare
    by a field some other sender happened to set."""
    out = _post(client, {**TIMESTAMPS["body"], "chela_timestamp_owner": owner})
    assert out["hookSpecificOutput"]["displayContent"].endswith("]** Done.\n")
