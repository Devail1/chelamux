"""``/api/agents/context`` — gated on a LIVE Claude session (CMX-125).

A window name is reused once a Claude session exits back to a bare shell (same tmux
window, same name), so ``context.live_snapshot`` — which keys off the window *name*,
not liveness — can still resolve a statusLine/transcript cache left over from that
prior session. Rendered on a plain shell, that reading is just stale noise. The gate:
skip a window entirely when ``agent_manager.claude_pid`` says nothing is running there
— the exact signal ``/api/agents`` already uses (app.py ~209) — so a shell pane's
bottom bar falls back to its already-existing "no context entry" null path (client
unchanged; see terminals.js ~1236).
"""
from __future__ import annotations

from contextlib import contextmanager
from unittest.mock import patch

import pytest

from chela.dashboard import app as dash

WINDOWS = {"cmx-76": "@9", "shell-1": "@10"}

LIVE_SNAPSHOT = {
    "name": "shell-1", "used_k": 153.0, "total_k": 200.0, "used_pct": 77,
    "messages_k": None, "messages_pct": None, "free_k": 47.0, "free_pct": 23,
    "model": "claude-sonnet-5", "cost_usd": 1.23,
    "rate_limit_pct": None, "rate_limit_resets_at": None,
    "weekly_rl_pct": None, "weekly_rl_resets_at": None,
    "session_name": "s1", "branch": "main",
    "source": "transcript", "estimated": True, "ts": None,
}


@pytest.fixture
def client():
    return dash.app.test_client()


@contextmanager
def _fleet(*, claude_pids: dict, snapshots: dict):
    """``claude_pids``/``snapshots`` are keyed by window_id / agent name respectively."""
    with (
        patch("chela.discovery.get_all_windows", return_value=dict(WINDOWS)),
        patch("chela.agent_manager.claude_pid", side_effect=lambda wid: claude_pids.get(wid)),
        patch("chela.context.live_snapshot", side_effect=lambda name: snapshots.get(name)),
    ):
        yield


def _by_name(client) -> dict[str, dict]:
    rows = client.get("/api/agents/context").get_json()
    return {r["name"]: r for r in rows}


def test_a_shell_with_no_live_claude_process_gets_no_row(client):
    """The bug: shell-1 exited Claude but kept the window name, so a stale snapshot
    still resolves for it. The gate must drop the row entirely, even though
    ``live_snapshot`` is willing to hand back a (stale) reading."""
    with _fleet(
        claude_pids={"@9": 4242, "@10": None},   # @10 (shell-1): no claude process
        snapshots={"cmx-76": {**LIVE_SNAPSHOT, "name": "cmx-76"}, "shell-1": LIVE_SNAPSHOT},
    ):
        rows = _by_name(client)
    assert "shell-1" not in rows


def test_a_live_claude_agent_keeps_its_context_and_branch_intact(client):
    """The gate must not nuke a real agent just because it evaluates every window —
    with a live pid AND a snapshot present, the row still carries used_pct/branch."""
    with _fleet(
        claude_pids={"@9": 4242, "@10": None},
        snapshots={"cmx-76": {**LIVE_SNAPSHOT, "name": "cmx-76"}, "shell-1": LIVE_SNAPSHOT},
    ):
        rows = _by_name(client)
    assert rows["cmx-76"]["used_pct"] == 77
    assert rows["cmx-76"]["branch"] == "main"
