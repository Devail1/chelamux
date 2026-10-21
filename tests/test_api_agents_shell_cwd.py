"""``/api/agents`` ``shell_cwd`` (CMX-241): where a plain shell pane sits, for its header.

A shell opened from the UI showed its tmux window name (``liavedunix-2``) and an ``idle``
chip, so it looked like an idle Claude session. Its header now reads ``Shell · <cwd>`` until
Claude starts in it. The row's ``cwd`` comes from the Claude session, so a shell has none:
``shell_cwd`` is tmux's ``#{pane_current_path}``, home written ``~``, and only while no
Claude runs in the pane (``claude_running``, the same fact ``chela peek`` prints as
``claude:no``).
"""
from __future__ import annotations

from contextlib import contextmanager
from unittest.mock import patch

import pytest

from chela import probecache
from chela.dashboard import app as dash
from chela.sessions import Pane

HOME = "/home/tester"
LIVE = {"tester-2": "@2", "tester-3": "@3", "tester-4": "@4", "work": "@5", "gone": "@6"}
PATHS = {"@2": HOME, "@3": HOME + "/projects/x", "@4": "/srv/data", "@5": HOME + "/projects/y"}
CLAUDE = {"@5": 1005}   # only @5 runs Claude


@contextmanager
def _fleet():
    panes = {wid: Pane(wid=wid, path=path) for wid, path in PATHS.items()}
    with (
        patch.dict("os.environ", {"HOME": HOME}),
        patch("chela.sessions.panes", return_value=panes),
        patch("chela.discovery.get_all_windows", return_value=dict(LIVE)),
        patch("chela.dispatcher.list_runs", return_value=[]),
        patch("chela.agent_manager.session_status_map", return_value={"by_pid": {}, "cwd_by_pid": {}}),
        patch("chela.agent_manager.claude_pid", side_effect=lambda wid: CLAUDE.get(wid)),
        patch("chela.agent_manager.session_entry",
              side_effect=lambda pid, _m: {"status": "idle", "cwd": HOME, "name": None, "moved": False} if pid else None),
        patch("chela.agent_manager.window_type", return_value="shell"),
        patch("chela.agent_manager.is_manual_name", return_value=False),
        patch("chela.scheduler.list_tasks", return_value=[]),
        patch("chela.transcripts.agent_transcript_summary",
              return_value={"recap": None, "recap_ts": None, "pr": None, "ai_title": None}),
        patch("chela.messenger.capture_pane", return_value=""),
        patch("chela.inbox.is_done", return_value=False),
        patch("chela.epoch.is_dangling", return_value=False),
    ):
        yield


def _by_wid() -> dict[str, dict]:
    with _fleet():
        rows = dash.app.test_client().get("/api/agents").get_json()
    return {a["window_id"]: a for a in rows}


def test_a_shell_carries_its_pane_cwd_with_home_written_tilde():
    agents = _by_wid()
    assert agents["@2"]["claude_running"] is False
    assert agents["@2"]["shell_cwd"] == "~"
    assert agents["@3"]["shell_cwd"] == "~/projects/x"
    assert agents["@4"]["shell_cwd"] == "/srv/data"


def test_a_claude_window_carries_none_whatever_its_pane_path():
    agents = _by_wid()
    assert agents["@5"]["claude_running"] is True
    assert agents["@5"]["shell_cwd"] is None


def test_a_shell_tmux_has_no_pane_for_reads_none_never_a_guess():
    assert _by_wid()["@6"]["shell_cwd"] is None


@pytest.mark.parametrize("path, want", [
    (HOME, "~"),
    (HOME + "/", "~"),
    (HOME + "/src", "~/src"),
    (HOME + "/src/a b", "~/src/a b"),
    (HOME + "2/src", HOME + "2/src"),   # a sibling sharing the prefix is not under home
    ("/home", "/home"),
    ("/srv", "/srv"),
    ("", None),
    (None, None),
])
def test_tilde_writes_only_home_and_below_as_tilde(path, want):
    with patch.dict("os.environ", {"HOME": HOME}):
        assert dash._tilde(path) == want


def test_tilde_leaves_every_path_alone_when_home_is_root():
    with patch.dict("os.environ", {"HOME": "/"}):
        assert dash._tilde("/srv/x") == "/srv/x"
        assert dash._tilde("/") == "/"


def test_pane_path_outside_a_batch_asks_tmux_for_that_window():
    assert probecache.active() is None
    with patch("chela.discovery.get_window_cwd_by_id", return_value="/srv/q") as probe:
        assert dash._pane_path("@9") == "/srv/q"
    probe.assert_called_once_with("@9")


def test_pane_path_inside_a_batch_reads_the_snapshot_and_spawns_nothing():
    with (
        patch("chela.sessions.panes", return_value={"@2": Pane(wid="@2", path="/srv/p"), "@3": Pane(wid="@3", path="")}),
        patch("chela.discovery.get_window_cwd_by_id", side_effect=AssertionError("no spawn in a batch")),
        probecache.batch(),
    ):
        assert dash._pane_path("@2") == "/srv/p"
        assert dash._pane_path("@3") is None      # tmux reported no path
        assert dash._pane_path("@7") is None      # no such window
