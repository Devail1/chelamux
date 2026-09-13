"""SPEC 8.2 (sort order) + SPEC 4.1.1/12.1 (issue body → dispatch brief).

Before this, `GhIssuesSource.list_open_tasks()` appended issues in `gh issue list`'s
default response order — created-descending — and never requested `body` at all. Two
independent defects:

- **LIFO dispatch.** `_claim_order` preserves whatever order the source returns, so the
  newest issue was always claimed first and the oldest starved forever.
- **No brief.** `Task.body` was always `None`, so `_task_brief` fell back to `task.raw`
  (the issue URL) — a brief a reviewer or judge can't benchmark GUARDS/OBJECTIVEs against.
"""
from __future__ import annotations

import json
import logging
from types import SimpleNamespace

import pytest

from chela.sources import gh_issues
from chela.sources.gh_issues import GhIssuesSource


def _wf(tmp_path, **tracker):
    cfg = {"tracker": {"kind": "gh_issues", "repo": "acme/widgets", **tracker}}

    def get(*keys, default=None):
        cur = cfg
        for k in keys:
            if not isinstance(cur, dict) or k not in cur:
                return default
            cur = cur[k]
        return cur

    return SimpleNamespace(path=tmp_path / "WORKFLOW.md", get=get, config=cfg)


@pytest.fixture(autouse=True)
def _no_cross_test_log_dedupe():
    gh_issues._reported.clear()
    yield
    gh_issues._reported.clear()


def _fake_gh(monkeypatch, issues):
    calls: list[list[str]] = []

    def run(argv, **kwargs):
        calls.append(argv)
        return SimpleNamespace(returncode=0, stdout=json.dumps(issues), stderr="")

    monkeypatch.setattr(gh_issues.subprocess, "run", run)
    return calls


LABEL = [{"name": "ready-for-agent"}]


def test_oldest_issue_is_dispatched_first(tmp_path, monkeypatch):
    """THE FIX for G3. `gh` returns created-descending (its default); the source must
    reorder to oldest-first regardless of the order it was handed.

    Issue numbers are deliberately NOT monotonic with creation date (newest has the
    lowest number). A sort that silently degrades to the `int(number)` tie-breaker —
    e.g. dropping the createdAt key entirely — would produce the WRONG order here,
    where a same-direction numbering would have hidden that regression."""
    issues = [
        {"number": 5, "title": "newest", "url": "u5", "labels": LABEL,
         "author": {"login": "a"}, "createdAt": "2026-09-03T00:00:00Z"},
        {"number": 99, "title": "oldest", "url": "u99", "labels": LABEL,
         "author": {"login": "a"}, "createdAt": "2026-09-01T00:00:00Z"},
        {"number": 50, "title": "middle", "url": "u50", "labels": LABEL,
         "author": {"login": "a"}, "createdAt": "2026-09-02T00:00:00Z"},
    ]
    _fake_gh(monkeypatch, issues)
    src = GhIssuesSource(_wf(tmp_path, require_label="ready-for-agent"))
    tasks = src.list_open_tasks()
    assert [t.title for t in tasks] == ["oldest", "middle", "newest"]


def test_a_null_created_at_sorts_last(tmp_path, monkeypatch):
    """SPEC 8.2: 'created_at oldest first; null sorts last' — a malformed/missing
    timestamp must not jump the queue by sorting as an empty-string minimum.

    Covers BOTH shapes of "no usable timestamp": JSON `null` (Python `None`) and an
    empty string (a `createdAt` field present but blank). Dropping the `or None`
    normalization would let the blank string keep its empty-string sort key instead
    of being folded into the null group — since `"" < "<any non-empty date>"`, that
    would sort it FIRST, ahead of every dated issue, not last.

    The null group is ALSO where the third sort-key component (the `int(number)`
    tie-breaker) gets exercised, since both issues here tie on the first two
    components. "no date" and "blank date" are deliberately given numbers whose
    ascending order (5, 2) is the OPPOSITE of the order they appear in `gh`'s
    response and the opposite of the asserted output order below. A degraded
    tie-breaker (e.g. a constant, dropped entirely) would fall back to Python's
    stable sort and reproduce input order instead — "no date" before "blank
    date" — which fails the assertion here instead of accidentally satisfying it
    the way same-direction numbering would (see docs/defeat_shapes/365b-*.md)."""
    issues = [
        {"number": 1, "title": "has a date", "url": "u1", "labels": LABEL,
         "author": {"login": "a"}, "createdAt": "2026-09-01T00:00:00Z"},
        {"number": 5, "title": "no date", "url": "u5", "labels": LABEL,
         "author": {"login": "a"}, "createdAt": None},
        {"number": 2, "title": "blank date", "url": "u2", "labels": LABEL,
         "author": {"login": "a"}, "createdAt": ""},
    ]
    _fake_gh(monkeypatch, issues)
    src = GhIssuesSource(_wf(tmp_path, require_label="ready-for-agent"))
    tasks = src.list_open_tasks()
    assert [t.title for t in tasks] == ["has a date", "blank date", "no date"]


def test_the_issue_body_becomes_task_body(tmp_path, monkeypatch):
    """THE FIX for G4. Without this, `Task.body` is always None and the dispatch
    brief degrades to the issue URL."""
    issues = [{"number": 1, "title": "t", "url": "u1", "labels": LABEL,
               "author": {"login": "a"}, "createdAt": "2026-09-01T00:00:00Z",
               "body": "## OBJECTIVE\ndo the thing\n"}]
    _fake_gh(monkeypatch, issues)
    src = GhIssuesSource(_wf(tmp_path, require_label="ready-for-agent"))
    tasks = src.list_open_tasks()
    assert tasks[0].body == "## OBJECTIVE\ndo the thing"


def test_a_blank_body_maps_to_none_not_an_empty_string(tmp_path, monkeypatch):
    """`_task_brief`'s fallback to `task.raw` is keyed on `body` being falsy — an
    empty/whitespace body must not silently defeat that fallback."""
    issues = [{"number": 1, "title": "t", "url": "u1", "labels": LABEL,
               "author": {"login": "a"}, "createdAt": "2026-09-01T00:00:00Z",
               "body": "   \n  "}]
    _fake_gh(monkeypatch, issues)
    src = GhIssuesSource(_wf(tmp_path, require_label="ready-for-agent"))
    tasks = src.list_open_tasks()
    assert tasks[0].body is None


def test_body_is_actually_requested(tmp_path, monkeypatch):
    """Assert the request, not just the handling — mirrors the existing `author` test
    in test_gh_issues_allowlist.py."""
    calls = _fake_gh(monkeypatch, [])
    GhIssuesSource(_wf(tmp_path, require_label="ready-for-agent")).list_open_tasks()
    assert calls, "expected one `gh issue list` call"
    fields = calls[0][calls[0].index("--json") + 1]
    assert "body" in fields.split(",")
    assert "createdAt" in fields.split(",")


def test_unconfigured_still_calls_gh_zero_times(tmp_path, monkeypatch, caplog):
    """Regression guard: sorting/body changes must not disturb the fail-closed gate
    from test_gh_issues_allowlist.py — it must still refuse before any `gh` call."""
    calls = _fake_gh(monkeypatch, [])
    src = GhIssuesSource(_wf(tmp_path))  # no require_label key at all
    with caplog.at_level(logging.ERROR, logger=gh_issues.log.name):
        assert src.list_open_tasks() == []
    assert calls == []
