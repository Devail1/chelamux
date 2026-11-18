"""CMX-395: the dashboard header's Claude account (``chela.claude_account``).

Placeholders only: this is a public repo, so no real email appears here (``*.invalid``).
"""
from __future__ import annotations

import json
import logging
import subprocess
from datetime import datetime, timezone
from unittest.mock import patch

import pytest

from chela import claude_account as ca
from chela import config, sessions
from chela.dashboard import app as dash

A = "person-a@example.invalid"
B = "person-b@example.invalid"
NOW = 1_800_000_000.0
DAY = 86400


@pytest.fixture(autouse=True)
def _fresh_cache(monkeypatch):
    monkeypatch.setattr(ca, "_cache", None)
    monkeypatch.setattr(ca, "_cache_at", 0.0)
    monkeypatch.delenv("CHELA_CLAUDE_LOGIN_WARN_DAYS", raising=False)


def _cli(stdout="", returncode=0, calls=None):
    def run(argv, **kw):
        if calls is not None:
            calls.append((argv, kw))
        return subprocess.CompletedProcess(argv, returncode, stdout=stdout, stderr="")
    return run


def _logged_in(**extra):
    return json.dumps({"loggedIn": True, "email": A, "orgName": "Example Org", "orgId": "org-0",
                       "subscriptionType": "team", "authMethod": "claude.ai", **extra})


# --- the CLI read ------------------------------------------------------------------------

def test_the_read_pins_argv_and_timeout_and_reads_every_field(monkeypatch):
    calls = []
    monkeypatch.setattr(ca.subprocess, "run", _cli(_logged_in(), calls=calls))
    got = ca.read_cli()
    assert calls == [(["claude", "auth", "status", "--json"],
                      {"capture_output": True, "text": True, "timeout": 15})]
    assert got["state"] == "ok"
    assert (got["email"], got["org_name"], got["plan"], got["auth_method"]) == (A, "Example Org", "team", "claude.ai")
    assert got["expires_at"] is None and got["expires_in_s"] is None


@pytest.mark.parametrize("exc, reason", [
    (FileNotFoundError("claude"), "claude CLI not found"),
    (subprocess.TimeoutExpired(["claude"], 15), "claude auth status timed out after 15s"),
    (PermissionError("denied"), "claude auth status could not start (PermissionError)"),
])
def test_a_cli_that_cannot_answer_is_unknown(monkeypatch, exc, reason):
    def boom(*a, **k):
        raise exc
    monkeypatch.setattr(ca.subprocess, "run", boom)
    assert ca.read_cli() == {"state": "unknown", "reason": reason}


@pytest.mark.parametrize("stdout, rc, reason", [
    ("", 0, "claude auth status gave no JSON object (exit 0)"),
    ("not json", 1, "claude auth status gave no JSON object (exit 1)"),
    ("[]", 0, "claude auth status gave no JSON object (exit 0)"),
    ('{"email": "x@example.invalid"}', 0, "claude auth status gave no loggedIn (exit 0)"),
    ('{"loggedIn": "true", "email": "x@example.invalid"}', 0, "claude auth status gave no loggedIn (exit 0)"),
    (_logged_in(), 1, "claude auth status failed (exit 1)"),
    ('{"loggedIn": true}', 0, "claude auth status reports logged in, but no account email"),
    ('{"loggedIn": true, "email": "  "}', 0, "claude auth status reports logged in, but no account email"),
])
def test_a_shapeless_answer_is_unknown_never_ok(monkeypatch, stdout, rc, reason):
    monkeypatch.setattr(ca.subprocess, "run", _cli(stdout, rc))
    assert ca.read_cli() == {"state": "unknown", "reason": reason}


@pytest.mark.parametrize("rc", [0, 1])
def test_logged_out_is_its_own_state_whatever_the_exit(monkeypatch, rc):
    monkeypatch.setattr(ca.subprocess, "run", _cli('{"loggedIn": false}', rc))
    assert ca.read_cli() == {"state": "logged_out"}


# --- expiry ------------------------------------------------------------------------------

@pytest.mark.parametrize("raw, want", [
    ("2027-01-15T12:00:00Z", datetime(2027, 1, 15, 12, tzinfo=timezone.utc).timestamp()),
    ("2027-01-15T14:00:00+02:00", datetime(2027, 1, 15, 12, tzinfo=timezone.utc).timestamp()),
    ("2027-01-15T12:00:00", datetime(2027, 1, 15, 12, tzinfo=timezone.utc).timestamp()),
    (1_800_000_000, 1_800_000_000.0),
    (1_800_000_000_000, 1_800_000_000.0),
    ("1800000000000", 1_800_000_000.0),
    (" 1800000000 ", 1_800_000_000.0),
    (None, None), (True, None), ("", None), ("soon", None), (0, None), (-5, None),
    (float("nan"), None), ({}, None), ([], None),
])
def test_parse_ts(raw, want):
    assert ca.parse_ts(raw) == want


def _interpret(expires, warn_days=3, rc=0):
    return ca.interpret(json.loads(_logged_in(loginExpiresAt=expires)), rc, NOW, warn_days)


@pytest.mark.parametrize("left, state", [
    (3 * DAY + 1, "ok"),
    (3 * DAY, "expiring"),
    (DAY, "expiring"),
    (-DAY, "expiring"),       # already past: still the warning, never "ok"
])
def test_expiring_within_warn_days(left, state):
    got = _interpret(NOW + left)
    assert got["state"] == state
    assert got["expires_in_s"] == left
    assert got["expires_at"] == datetime.fromtimestamp(NOW + left, tz=timezone.utc).isoformat()
    assert got["warn_days"] == 3


def test_warn_days_is_what_the_caller_passes():
    assert _interpret(NOW + 5 * DAY, warn_days=7)["state"] == "expiring"
    assert _interpret(NOW + 5 * DAY, warn_days=4)["state"] == "ok"


@pytest.mark.parametrize("env, want", [(None, 3), ("7", 7), ("0", 0), ("junk", 3), ("-2", 0), ("500", 90)])
def test_warn_days_env_over_default(monkeypatch, env, want):
    if env is not None:
        monkeypatch.setenv("CHELA_CLAUDE_LOGIN_WARN_DAYS", env)
    assert config.claude_login_warn_days() == want


def test_read_cli_uses_the_configured_warn_days(monkeypatch):
    monkeypatch.setattr(ca.time, "time", lambda: NOW)
    monkeypatch.setattr(ca.subprocess, "run", _cli(_logged_in(loginExpiresAt=NOW + 5 * DAY)))
    assert ca.read_cli()["state"] == "ok"
    monkeypatch.setenv("CHELA_CLAUDE_LOGIN_WARN_DAYS", "6")
    assert ca.read_cli()["state"] == "expiring"


# --- the cache ---------------------------------------------------------------------------

def test_status_caches_for_the_ttl_and_fresh_rereads_past_the_floor(monkeypatch):
    calls = []
    clock = [1000.0]
    monkeypatch.setattr(ca.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(ca.subprocess, "run", _cli(_logged_in(), calls=calls))
    assert ca.status()["email"] == A and len(calls) == 1
    clock[0] += ca.TTL_S - 1
    ca.status()
    assert len(calls) == 1, "a read inside the TTL comes from the cache"
    clock[0] = 1000.0 + ca.FRESH_MIN_S - 1
    ca.status(fresh=True)
    assert len(calls) == 1, "a fresh read inside the floor still comes from the cache"
    clock[0] = 1000.0 + ca.FRESH_MIN_S
    ca.status(fresh=True)
    assert len(calls) == 2
    clock[0] += ca.TTL_S
    ca.status()
    assert len(calls) == 3


def test_status_carries_the_check_time_and_no_change_on_first_sight(monkeypatch):
    monkeypatch.setattr(ca.subprocess, "run", _cli(_logged_in()))
    got = ca.status()
    assert got["change"] is None
    assert datetime.fromisoformat(got["checked_at"]).tzinfo is not None


def test_status_survives_a_failing_observe(monkeypatch, caplog):
    monkeypatch.setattr(ca.subprocess, "run", _cli(_logged_in()))

    def boom(answer):
        raise RuntimeError("disk")
    monkeypatch.setattr(ca, "observe", boom)
    with caplog.at_level(logging.ERROR, logger="chela.claude_account"):
        got = ca.status()
    assert got["state"] == "ok" and got["change"] is None
    assert "recording the account failed" in caplog.text


def test_an_unknown_read_is_logged_with_its_reason_and_no_email(monkeypatch, caplog):
    monkeypatch.setattr(ca.subprocess, "run", _cli("garbage", 2))
    with caplog.at_level(logging.WARNING, logger="chela.claude_account"):
        assert ca.status()["state"] == "unknown"
    assert "claude account: unknown (claude auth status gave no JSON object (exit 2))" in caplog.text


# --- account changes ---------------------------------------------------------------------

def _ok(email):
    return {"state": "ok", "email": email}


def test_observe_records_a_change_between_two_accounts(caplog):
    with caplog.at_level(logging.INFO, logger="chela.claude_account"):
        assert ca.observe(_ok(A), now=NOW) is None
        assert ca.observe({"state": "expiring", "email": A}, now=NOW + 1) is None
        assert ca.observe(_ok(A.upper()), now=NOW + 2) is None, "case is not a switch"
        assert ca.observe({"state": "logged_out"}, now=NOW + 3) is None
        assert ca.observe({"state": "unknown"}, now=NOW + 4) is None
        change = ca.observe(_ok(B), now=NOW + 5)
    assert change == {"from": A, "to": B, "at": NOW + 5}
    assert ca.last_change() == change
    assert ca.observe(_ok(B), now=NOW + 6) == change, "the same account again keeps the change"
    assert "the signed-in account changed" in caplog.text
    assert A not in caplog.text and B not in caplog.text, "an email is never logged"
    stored = json.loads((config.CHELA_DIR / "claude-account.json").read_text())
    assert stored == {"email": B, "change": {"from": A, "to": B, "at": NOW + 5}}


def test_observe_without_a_clock_stamps_now(monkeypatch):
    monkeypatch.setattr(ca.time, "time", lambda: NOW)
    ca.observe(_ok(A))
    assert ca.observe(_ok(B)) == {"from": A, "to": B, "at": NOW}


def test_a_state_file_that_cannot_be_written_is_logged_without_the_email(monkeypatch, caplog):
    def boom(data):
        raise PermissionError("ro")
    monkeypatch.setattr(ca, "_save_store", boom)
    with caplog.at_level(logging.WARNING, logger="chela.claude_account"):
        assert ca.observe(_ok(A)) is None
    assert "could not record the account (PermissionError)" in caplog.text
    assert A not in caplog.text


@pytest.mark.parametrize("body", [
    "not json", "[]", '{"change": "x"}', '{"change": {"from": "a", "to": "b"}}',
    '{"change": {"from": "", "to": "b", "at": 1}}', '{"change": {"from": "a", "to": "b", "at": true}}',
    '{"change": {"from": "a", "to": "b", "at": "1"}}',
])
def test_a_corrupt_state_file_has_no_change(body):
    config.CHELA_DIR.mkdir(parents=True, exist_ok=True)
    (config.CHELA_DIR / "claude-account.json").write_text(body)
    assert ca.last_change() is None


def test_no_state_file_has_no_change():
    assert ca.last_change() is None


def test_status_publishes_the_change_as_iso(monkeypatch):
    ca.observe(_ok(A), now=NOW - 10)
    monkeypatch.setattr(ca.time, "time", lambda: NOW)
    monkeypatch.setattr(ca.subprocess, "run", _cli(_logged_in(email=B)))
    got = ca.status()
    assert got["email"] == B
    assert got["change"] == {"from": A, "to": B, "at": datetime.fromtimestamp(NOW, tz=timezone.utc).isoformat()}


# --- the per-session badge -------------------------------------------------------------

CHANGE = {"from": A, "to": B, "at": NOW}


def _iso(ts):
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()


def test_a_session_started_before_the_switch_gets_the_badge():
    assert ca.switch_badge(_iso(NOW - 1), CHANGE) == {
        "from": A, "to": B, "at": _iso(NOW), "started_unknown": False}


@pytest.mark.parametrize("created", [_iso(NOW), _iso(NOW + 60), NOW + 60])
def test_a_session_started_at_or_after_the_switch_gets_none(created):
    assert ca.switch_badge(created, CHANGE) is None


def test_no_change_ever_observed_gets_none():
    assert ca.switch_badge(_iso(NOW - 1), None) is None
    assert ca.switch_badge(None, None) is None


@pytest.mark.parametrize("created", [None, "", "whenever"])
def test_an_unknown_start_is_not_after_the_switch(created):
    assert ca.switch_badge(created, CHANGE)["started_unknown"] is True


# --- the dashboard -----------------------------------------------------------------------

def test_the_route_passes_fresh_and_returns_the_answer(monkeypatch):
    seen = []

    def status(fresh=False):
        seen.append(fresh)
        return {"state": "ok", "email": A}
    monkeypatch.setattr(dash.claude_account, "status", status)
    c = dash.app.test_client()
    assert c.get("/api/claude-account").get_json() == {"state": "ok", "email": A}
    c.get("/api/claude-account?fresh=1")
    c.get("/api/claude-account?fresh=0")
    assert seen == [False, True, False]


def test_the_route_never_500s(monkeypatch):
    def boom(fresh=False):
        raise RuntimeError("x")
    monkeypatch.setattr(dash.claude_account, "status", boom)
    r = dash.app.test_client().get("/api/claude-account")
    assert r.status_code == 200
    assert r.get_json() == {"state": "unknown", "reason": "the dashboard could not read the account"}


def _agents(started, claude_pid: int | None = 1234, change_raises=False):
    panes = {"@3": sessions.Pane(wid="@3", started=started, claude_pid=claude_pid, direct_claude_pid=claude_pid)}
    summary = {"recap": None, "recap_ts": None, "pr": None, "ai_title": None, "last_activity": None}
    spawned = []

    def no_spawn(*a, **k):
        spawned.append(a)
        raise AssertionError("/api/agents must not run claude auth status")
    patches = [
        patch("chela.discovery.get_all_windows", return_value={"proj": "@3"}),
        patch("chela.dispatcher.list_runs", return_value=[]),
        patch("chela.sessions.panes", return_value=panes),
        patch("chela.agent_manager.session_status_map", return_value={"by_pid": {}, "cwd_by_pid": {}}),
        patch("chela.agent_manager.session_entry", return_value=None),
        patch("chela.agent_manager.window_type", return_value="claude"),
        patch("chela.agent_manager.is_manual_name", return_value=False),
        patch("chela.scheduler.list_tasks", return_value=[]),
        patch("chela.transcripts.agent_transcript_summary", return_value=summary),
        patch("chela.messenger.capture_pane", return_value=""),
        patch("chela.inbox.is_done", return_value=False),
        patch("chela.epoch.is_dangling", return_value=False),
        patch.object(ca, "read_cli", no_spawn),
    ]
    if change_raises:
        patches.append(patch.object(ca, "last_change", side_effect=RuntimeError("x")))
    for p in patches:
        p.start()
    try:
        (row,) = dash.app.test_client().get("/api/agents").get_json()
    finally:
        for p in reversed(patches):
            p.stop()
    assert spawned == []
    return row


def test_api_agents_marks_a_session_started_before_the_switch():
    ca.observe(_ok(A), now=NOW - 100)
    ca.observe(_ok(B), now=NOW)
    assert _agents(NOW - 50)["account_switch"] == {
        "from": A, "to": B, "at": _iso(NOW), "started_unknown": False}
    assert _agents(NOW + 50)["account_switch"] is None


def test_api_agents_no_badge_without_claude_or_without_a_change():
    assert _agents(NOW - 50)["account_switch"] is None
    ca.observe(_ok(A), now=NOW - 100)
    ca.observe(_ok(B), now=NOW)
    assert _agents(NOW - 50, claude_pid=None)["account_switch"] is None


def test_api_agents_survives_an_unreadable_change(caplog):
    ca.observe(_ok(A), now=NOW - 100)
    ca.observe(_ok(B), now=NOW)
    with caplog.at_level(logging.ERROR):
        assert _agents(NOW - 50, change_raises=True)["account_switch"] is None
    assert "account change unreadable" in caplog.text


def test_the_background_refresh_reads_until_stopped(monkeypatch):
    import threading
    reads = []
    stop = threading.Event()

    def status(fresh=False):
        reads.append(fresh)
        stop.set()
    monkeypatch.setattr(ca, "status", status)
    t = ca.start_background_refresh(stop)
    t.join(5)
    assert not t.is_alive() and reads == [False]
    assert t.daemon and t.name == "chela-claude-account-refresh"


def test_the_background_refresh_survives_a_failing_read(monkeypatch, caplog):
    import threading
    stop = threading.Event()
    n = []

    def status(fresh=False):
        n.append(1)
        if len(n) == 1:
            raise RuntimeError("x")
        stop.set()
    monkeypatch.setattr(ca, "status", status)
    monkeypatch.setattr(ca, "TTL_S", 0)
    with caplog.at_level(logging.ERROR, logger="chela.claude_account"):
        ca.start_background_refresh(stop).join(5)
    assert len(n) == 2
    assert "background refresh failed" in caplog.text


def test_dashboard_main_starts_the_account_refresh():
    import inspect
    src = inspect.getsource(dash.main)
    assert "claude_account.start_background_refresh()" in src
