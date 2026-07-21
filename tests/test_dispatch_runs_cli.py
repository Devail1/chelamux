"""Tests for `chela dispatch-runs` filtering + the --awaiting review view."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import patch

from chela import main

_NOW = datetime(2026, 7, 12, 12, 0, 0, tzinfo=timezone.utc)


def _run(task_id, status, **kw):
    row = {
        "task_id": task_id,
        "status": status,
        "attempt": 1,
        "title": kw.get("title", "do a thing"),
        "window_name": kw.get("window_name"),
        "pr_url": kw.get("pr_url"),
        "started_at": kw.get("started_at"),
    }
    return row


def test_filter_runs_none_returns_all():
    runs = [_run("a", "running"), _run("b", "awaiting_review")]
    assert main._filter_runs(runs, None) == runs


def test_filter_runs_by_status_exact_match():
    runs = [
        _run("a", "running"),
        _run("b", "awaiting_review"),
        _run("c", "awaiting_review"),
        _run("d", "done"),
    ]
    got = main._filter_runs(runs, "awaiting_review")
    assert [r["task_id"] for r in got] == ["b", "c"]


def test_filter_runs_unknown_status_yields_nothing():
    runs = [_run("a", "running")]
    assert main._filter_runs(runs, "nope") == []


def test_run_age_str_buckets():
    assert main._run_age_str((_NOW - timedelta(seconds=5)).isoformat(), now=_NOW) == "5s"
    assert main._run_age_str((_NOW - timedelta(minutes=3)).isoformat(), now=_NOW) == "3m"
    assert main._run_age_str((_NOW - timedelta(hours=2)).isoformat(), now=_NOW) == "2h"
    assert main._run_age_str((_NOW - timedelta(days=4)).isoformat(), now=_NOW) == "4d"


def test_run_age_str_missing_or_bad():
    assert main._run_age_str(None, now=_NOW) == "?"
    assert main._run_age_str("not-a-date", now=_NOW) == "?"


def test_run_age_str_future_clamps_to_zero():
    assert main._run_age_str((_NOW + timedelta(minutes=5)).isoformat(), now=_NOW) == "0s"


def test_format_awaiting_run_carries_pr_and_age():
    r = _run(
        "abc123", "awaiting_review",
        pr_url="https://github.com/o/r/pull/7",
        started_at=(_NOW - timedelta(minutes=10)).isoformat(),
    )
    line = main._format_awaiting_run(r, now=_NOW)
    assert "abc123" in line
    assert "awaiting_review" in line
    assert "age=10m" in line
    assert "https://github.com/o/r/pull/7" in line


def test_format_awaiting_run_missing_pr_shows_dash():
    r = _run("abc123", "awaiting_review", started_at=_NOW.isoformat())
    assert " -  " in main._format_awaiting_run(r, now=_NOW) or "-" in main._format_awaiting_run(r, now=_NOW)


def test_cmd_dispatch_runs_awaiting_filters_and_shows_pr(capsys):
    runs = [
        _run("a", "running", started_at=_NOW.isoformat()),
        _run("b", "awaiting_review", pr_url="https://x/pull/1", started_at=_NOW.isoformat()),
    ]
    with patch.object(main.dispatcher, "list_runs", return_value=runs):
        main.cmd_dispatch_runs(SimpleNamespace(awaiting=True, status=None))
    out = capsys.readouterr().out
    assert "b" in out and "https://x/pull/1" in out
    # The running row is filtered out of the awaiting view.
    assert "running" not in out


def test_cmd_dispatch_runs_awaiting_shows_the_WHOLE_review_loop(capsys):
    """--awaiting is "what is parked in review?", and since the rework loop that is three
    states, not one: a run the reviewer sent back (changes_requested) and a run the loop
    gave up on (needs_human) are exactly what a "what still needs me?" question is after.
    Showing only awaiting_review would hide the loop this filter now feeds."""
    runs = [
        _run("a", "running", started_at=_NOW.isoformat()),
        _run("b", "awaiting_review", pr_url="https://x/pull/1", started_at=_NOW.isoformat()),
        _run("c", "changes_requested", pr_url="https://x/pull/2", started_at=_NOW.isoformat()),
        _run("d", "needs_human", pr_url="https://x/pull/3", started_at=_NOW.isoformat()),
        _run("e", "done", started_at=_NOW.isoformat()),
    ]
    with patch.object(main.dispatcher, "list_runs", return_value=runs):
        main.cmd_dispatch_runs(SimpleNamespace(awaiting=True, status=None))
    out = capsys.readouterr().out
    assert "changes_requested" in out and "https://x/pull/2" in out
    assert "needs_human" in out and "https://x/pull/3" in out
    assert "awaiting_review" in out
    # Neither in-flight nor shipped work belongs in the review view.
    assert "running" not in out and "done" not in out


def test_filter_runs_takes_several_statuses():
    runs = [_run("a", "running"), _run("b", "changes_requested"), _run("c", "done")]
    got = main._filter_runs(runs, ["changes_requested", "needs_human"])
    assert [r["task_id"] for r in got] == ["b"]


def test_cmd_dispatch_runs_status_no_match_prints_message(capsys):
    runs = [_run("a", "running", started_at=_NOW.isoformat())]
    with patch.object(main.dispatcher, "list_runs", return_value=runs):
        main.cmd_dispatch_runs(SimpleNamespace(awaiting=False, status="awaiting_review"))
    out = capsys.readouterr().out
    assert "No runs in status 'awaiting_review'" in out


def test_cmd_dispatch_runs_no_filter_keeps_legacy_output(capsys):
    runs = [_run("a", "running", window_name="w1", started_at=_NOW.isoformat())]
    with patch.object(main.dispatcher, "list_runs", return_value=runs):
        main.cmd_dispatch_runs(SimpleNamespace(awaiting=False, status=None))
    out = capsys.readouterr().out
    assert "attempt=1" in out and "w1" in out
