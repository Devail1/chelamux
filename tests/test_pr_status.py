"""CMX-41: the Wall's PR chip / Review bar rests on the PR's REAL state.

Claude Code keeps re-writing a session's ``pr-link`` record after its PR merged, so the
record's presence alone put "✓ PR ready for review" + ``⚑ #613`` on a dead PR.
``chela.pr_status`` resolves open/merged/closed/unknown (+ draft) and ``/api/agents``
ships it on ``pr``; the client (wallmodel.js, tests/wall_tile.test.mjs) gates the bar on
a confirmed open, non-draft state.
"""
from __future__ import annotations

import json
import subprocess
from contextlib import contextmanager
from unittest.mock import patch

import pytest

from chela import pr_status
from chela.dashboard import app as dash

URL = "https://github.com/acme/widgets/pull/613"


@pytest.fixture(autouse=True)
def _fresh_cache():
    pr_status.clear_cache()
    yield
    pr_status.clear_cache()


class _GH:
    """A fake ``subprocess.run`` for ``gh pr view``: counts calls, answers ``payload``."""

    def __init__(self, payload=None, *, rc=0, raise_=None):
        self.payload, self.rc, self.raise_ = payload, rc, raise_
        self.calls: list[list[str]] = []

    def __call__(self, argv, **kw):
        self.calls.append(list(argv))
        if self.raise_:
            raise self.raise_
        out = json.dumps(self.payload) if self.payload is not None else ""
        return subprocess.CompletedProcess(argv, self.rc, stdout=out, stderr="")


# --- the lookup itself -------------------------------------------------------

@pytest.mark.parametrize("payload,expected", [
    ({"state": "OPEN", "isDraft": False}, {"state": "open", "draft": False}),
    ({"state": "OPEN", "isDraft": True}, {"state": "open", "draft": True}),
    ({"state": "MERGED", "isDraft": False}, {"state": "merged", "draft": False}),
    ({"state": "CLOSED", "isDraft": False}, {"state": "closed", "draft": False}),
    ({"state": "WEIRD"}, {"state": "unknown", "draft": False}),
])
def test_lookup_maps_gh_state(payload, expected):
    gh = _GH(payload)
    with patch("chela.pr_status.subprocess.run", gh):
        assert pr_status.lookup(URL) == expected
    # It asks about the PR the URL NAMES, not whatever repo a cwd resolves to.
    assert gh.calls[0][:6] == ["gh", "pr", "view", "613", "--repo", "acme/widgets"]


@pytest.mark.parametrize("gh", [
    _GH(None, rc=1),
    _GH(None, raise_=FileNotFoundError("gh")),
    _GH(None, raise_=subprocess.TimeoutExpired("gh", 8)),
    _GH("not json"),
])
def test_lookup_failure_is_unknown_never_open(gh):
    """⛔ Fail closed: any failure is ``unknown`` — never ``open``."""
    with patch("chela.pr_status.subprocess.run", gh):
        assert pr_status.lookup(URL) == {"state": "unknown", "draft": False}


def test_lookup_is_cached_within_ttl_and_refreshed_after():
    gh = _GH({"state": "OPEN", "isDraft": False})
    clock = [1000.0]
    with patch("chela.pr_status.subprocess.run", gh), \
         patch("chela.pr_status.time.monotonic", lambda: clock[0]):
        for _ in range(5):
            pr_status.lookup(URL)
        assert len(gh.calls) == 1
        clock[0] += pr_status.TTL_SECONDS + 1
        pr_status.lookup(URL)
        assert len(gh.calls) == 2


def test_a_merged_answer_is_cached_past_the_ttl():
    """A merged PR cannot un-merge: once gh says MERGED, no tick ever asks again."""
    gh = _GH({"state": "MERGED", "isDraft": False})
    clock = [1000.0]
    with patch("chela.pr_status.subprocess.run", gh), \
         patch("chela.pr_status.time.monotonic", lambda: clock[0]):
        assert pr_status.lookup(URL)["state"] == "merged"
        clock[0] += pr_status.TTL_SECONDS * 10
        assert pr_status.lookup(URL)["state"] == "merged"
    assert len(gh.calls) == 1


@pytest.mark.parametrize("state", ["MERGED", "CLOSED"])
def test_draft_is_only_ever_reported_on_an_open_pr(state):
    """gh can echo isDraft=true on a closed draft — ``draft`` describes an OPEN PR only."""
    with patch("chela.pr_status.subprocess.run", _GH({"state": state, "isDraft": True})):
        assert pr_status.lookup(URL) == {"state": state.lower(), "draft": False}


def test_a_cached_answer_is_a_copy_not_the_cache_entry():
    """``/api/agents`` merges the answer into a pane's pr dict; mutating it must not poison the cache."""
    with patch("chela.pr_status.subprocess.run", _GH({"state": "OPEN", "isDraft": False})):
        pr_status.lookup(URL)["state"] = "garbage"
        hit = pr_status.lookup(URL)
        hit["state"] = "garbage"
        assert pr_status.lookup(URL) == {"state": "open", "draft": False}


def test_a_failed_lookup_is_cached_too():
    """A dead network must not turn into one gh spawn per pane per tick."""
    gh = _GH(None, rc=1)
    with patch("chela.pr_status.subprocess.run", gh):
        for _ in range(4):
            assert pr_status.lookup(URL)["state"] == "unknown"
    assert len(gh.calls) == 1


def test_a_run_rows_terminal_state_wins_without_gh():
    gh = _GH({"state": "OPEN", "isDraft": False})
    states = pr_status.runs_pr_states([
        {"pr_url": URL, "pr_state": "merged"},
        {"pr_url": "https://github.com/acme/widgets/pull/9", "pr_state": "open"},
    ])
    assert states == {URL: "merged"}   # an `open` row can lag a tick — never trusted
    with patch("chela.pr_status.subprocess.run", gh):
        assert pr_status.lookup(URL, states) == {"state": "merged", "draft": False}
    assert gh.calls == []


def test_no_url_is_unknown_without_gh():
    gh = _GH({"state": "OPEN"})
    with patch("chela.pr_status.subprocess.run", gh):
        assert pr_status.lookup(None)["state"] == "unknown"
        assert pr_status.lookup("https://example.invalid/not-a-pr")["state"] == "unknown"
    assert gh.calls == []


# --- /api/agents wiring ------------------------------------------------------

LIVE = {"orchestrator": "@2", "worker-a": "@5", "worker-b": "@6"}
PR = {"url": URL, "number": 613, "repository": "acme/widgets", "ts": "2026-10-09T08:39:00Z"}


@contextmanager
def _fleet(gh, runs=()):
    with (
        patch("chela.discovery.get_all_windows", return_value=dict(LIVE)),
        patch("chela.dispatcher.list_runs", return_value=list(runs)),
        patch("chela.agent_manager.session_status_map", return_value={"by_pid": {}, "cwd_by_pid": {}}),
        patch("chela.agent_manager.claude_pid", return_value=None),
        patch("chela.agent_manager.window_type", return_value="claude"),
        patch("chela.scheduler.list_tasks", return_value=[]),
        patch("chela.transcripts.agent_transcript_summary",
              return_value={"recap": None, "recap_ts": None, "pr": dict(PR), "ai_title": None}),
        patch("chela.messenger.capture_pane", return_value=""),
        patch("chela.inbox.is_done", return_value=False),
        patch("chela.pr_status.subprocess.run", gh),
    ):
        yield


def _prs(client) -> dict[str, dict]:
    return {a["window_id"]: a["pr"] for a in client.get("/api/agents").get_json()}


def test_api_agents_ships_a_merged_prs_state():
    """The orchestrator pane's latest pr-link points at a MERGED PR ⇒ ``state: merged``."""
    gh = _GH({"state": "MERGED", "isDraft": False})
    with _fleet(gh):
        prs = _prs(dash.app.test_client())
    assert prs["@2"]["state"] == "merged"
    assert prs["@2"]["number"] == 613 and prs["@2"]["url"] == URL


def test_api_agents_ships_an_open_prs_state():
    gh = _GH({"state": "OPEN", "isDraft": False})
    with _fleet(gh):
        prs = _prs(dash.app.test_client())
    assert prs["@2"] == {**PR, "state": "open", "draft": False}


def test_api_agents_lookup_failure_ships_unknown():
    gh = _GH(None, rc=1)
    with _fleet(gh):
        prs = _prs(dash.app.test_client())
    assert prs["@2"]["state"] == "unknown"


def test_api_agents_uses_a_run_rows_merged_state_without_gh():
    gh = _GH({"state": "OPEN", "isDraft": False})
    with _fleet(gh, runs=[{"pr_url": URL, "pr_state": "merged", "status": "merged"}]):
        prs = _prs(dash.app.test_client())
    assert {p["state"] for p in prs.values()} == {"merged"}
    assert gh.calls == []


def test_one_render_does_not_spawn_gh_per_pane_per_tick():
    """Three panes carrying the same PR, polled over several ticks ⇒ ONE gh call."""
    gh = _GH({"state": "OPEN", "isDraft": False})
    client = dash.app.test_client()
    with _fleet(gh):
        for _ in range(3):
            prs = _prs(client)
    assert len(prs) == 3
    assert len(gh.calls) == 1
