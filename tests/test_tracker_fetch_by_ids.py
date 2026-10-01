"""🧾🔎 CMX-430 — Symphony SPEC 11.1 `fetch_issues_by_ids`: reconcile acts on a POSITIVE
read of a task's tracker state, never on its absence from a read that failed.

Before this, `tick()` inferred "done" from a task's ABSENCE in `list_open_tasks()`, so a
failed or partial read (a `gh` timeout, an auth blip, a vanished TODO.md) could flip a live
review row to `done`, kill its window and delete its worktree. Now both adapters carry
`fetch_by_ids(ids) -> list[Task] | None` — `None` = the read FAILED, `[]` = read OK and
none of those ids exist — and `tick()` re-reads, by id, every run it would close out.
"""
from __future__ import annotations

import json
import subprocess
from unittest.mock import patch

import pytest

from chela import dispatcher
from chela.sources.gh_issues import GhIssuesSource, _task_id as gh_task_id
from chela.sources.markdown import MarkdownSource
from chela.workflow import WorkflowDef

from tests.test_dispatcher_rework import _FakeTmux, _row, _status, _wf

REPO = "o/r"
ISSUE = 7
PR_URL = "https://github.com/o/r/pull/80"
TRANSCRIPT_PR = "https://github.com/o/r/pull/999"


@pytest.fixture(autouse=True)
def _own_runs_db(tmp_path, monkeypatch):
    monkeypatch.setattr(dispatcher, "DB_PATH", tmp_path / "scheduler.db")
    dispatcher._refresh_failed.clear()


# --- the markdown adapter ------------------------------------------------------------------


def _md(tmp_path, text: str | None) -> MarkdownSource:
    if text is not None:
        (tmp_path / "TODO.md").write_text(text)
    return MarkdownSource(WorkflowDef(
        path=tmp_path / "WORKFLOW.md",
        config={"tracker": {"kind": "markdown", "path": "TODO.md"}},
        prompt_template="",
    ))


def _md_id(src: MarkdownSource, title: str) -> str:
    from chela.sources.markdown import _task_id
    return _task_id(src.path, title)


def test_markdown_fetch_by_ids_reports_open_parked_and_closed(tmp_path):
    src = _md(tmp_path, "- [ ] alpha\n- [x] beta\n- [ ] gamma <!-- blocked: later -->\n")
    a, b, g, gone = (_md_id(src, t) for t in ("alpha", "beta", "gamma", "deleted"))

    snap = {t.id: t.state for t in src.fetch_by_ids([a, b, g, gone])}

    # ⭐ a struck line is POSITIVELY closed; a parked one is still live work, not done.
    assert snap == {a: "open", b: "closed", g: "open"}
    assert src.fetch_by_ids([gone]) == []


def test_markdown_fetch_by_ids_is_None_when_the_file_is_missing(tmp_path):
    src = _md(tmp_path, None)
    assert src.fetch_by_ids(["abc123"]) is None


# --- the gh_issues adapter -----------------------------------------------------------------


def _gh(tmp_path) -> GhIssuesSource:
    return GhIssuesSource(WorkflowDef(
        path=tmp_path / "WORKFLOW.md",
        config={"tracker": {"kind": "gh_issues", "repo": REPO, "require_label": "ready"}},
        prompt_template="",
    ))


class _GhOut:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode, self.stdout, self.stderr = returncode, stdout, stderr


def _issues(*pairs) -> str:
    return json.dumps([
        {"number": n, "title": f"issue {n}", "url": f"https://github.com/{REPO}/issues/{n}",
         "state": st, "labels": [{"name": "ready"}], "author": {"login": "x"},
         "createdAt": "2026-01-01T00:00:00Z", "body": "b"}
        for n, st in pairs
    ])


def _timeout(*a, **k):
    raise subprocess.TimeoutExpired(cmd="gh", timeout=60)


@pytest.mark.parametrize("behaviour", [
    _timeout,
    lambda *a, **k: _GhOut(returncode=1, stderr="HTTP 401: Bad credentials"),
    lambda *a, **k: _GhOut(stdout="not json"),
])
def test_gh_fetch_by_ids_is_None_on_a_failed_read(tmp_path, behaviour):
    src = _gh(tmp_path)
    with patch("chela.sources.gh_issues.subprocess.run", side_effect=behaviour):
        assert src.fetch_by_ids([gh_task_id(REPO, ISSUE)]) is None


def test_gh_fetch_by_ids_reports_state_and_absence(tmp_path):
    src = _gh(tmp_path)
    tid, closed_tid, missing = (gh_task_id(REPO, n) for n in (ISSUE, 8, 99))
    with patch("chela.sources.gh_issues.subprocess.run",
               return_value=_GhOut(stdout=_issues((ISSUE, "OPEN"), (8, "CLOSED"), (9, "OPEN")))):
        snap = {t.id: t.state for t in src.fetch_by_ids([tid, closed_tid, missing])}
    assert snap == {tid: "open", closed_tid: "closed"}


def test_gh_fetch_by_ids_fails_rather_than_guess_past_its_limit(tmp_path, monkeypatch):
    """SPEC 11.1: an ID refresh MUST fail instead of silently omitting — a listing cut off at
    its limit cannot say an unseen id is gone."""
    import chela.sources.gh_issues as gh
    monkeypatch.setattr(gh, "_REFRESH_LIMIT", 2)
    src = _gh(tmp_path)
    with patch("chela.sources.gh_issues.subprocess.run",
               return_value=_GhOut(stdout=_issues((1, "OPEN"), (2, "OPEN")))):
        assert src.fetch_by_ids([gh_task_id(REPO, ISSUE)]) is None


# --- tick(): what reconcile does with it ---------------------------------------------------


def _tick(wf, source, gh_behaviour=None, pr_status=None):
    """One real `tick()` against `source`, tmux and every other `gh` call stubbed. Returns
    (summary, killed window names)."""
    fake = _FakeTmux()
    fake.windows = [("@1", "test-1")]
    killed: list[str] = []

    def run(cmd, *a, **k):
        if gh_behaviour is not None and isinstance(cmd, list) and cmd[:2] == ["gh", "issue"]:
            return gh_behaviour(cmd, *a, **k)
        return fake.run(cmd, *a, **k)

    def read_pr_status(url, repo_dir):
        if pr_status is not None:
            return pr_status(url)
        return ("merged" if url == TRANSCRIPT_PR else "open"), "MERGEABLE"

    with patch.object(dispatcher, "load_workflow_cached", return_value=_status(wf)), \
         patch.object(dispatcher, "get_source", return_value=source), \
         patch.object(dispatcher, "_claim_order", return_value=[]), \
         patch.object(dispatcher, "_tmux_windows", return_value={"test-1"}), \
         patch.object(dispatcher, "_kill_window", side_effect=killed.append), \
         patch.object(dispatcher, "_respawn_rework", return_value=False), \
         patch.object(dispatcher, "_fire_after_done", lambda wf: None), \
         patch.object(dispatcher, "_read_pr_url", return_value=TRANSCRIPT_PR), \
         patch.object(dispatcher, "_read_pr_status", side_effect=read_pr_status), \
         patch.object(dispatcher.subprocess, "run", side_effect=run):
        summary = dispatcher.tick(wf.path)
    return summary, killed


def _seed(wf, task_id, status, tmp_path):
    """A live run: a review row with an open PR, or a running row (live window) whose
    transcript names a PR that has merged — the evidence the claimed/running branch acts on
    ONLY once the tracker says the task is gone."""
    wt = tmp_path / "wt" / task_id
    wt.mkdir(parents=True)
    with dispatcher._db() as conn:
        _row(conn, task_id=task_id, workflow_path=str(wf.path), status=status,
             window_name="test-1", worktree_path=str(wt),
             pr_url=None if status == "running" else PR_URL,
             pr_state=None if status == "running" else "open",
             started_at=dispatcher._now())
    return wt


def _status_of(task_id):
    return dispatcher.resolve_run(task_id)["status"]


LIVE_STATUSES = (*dispatcher.REVIEW_STATUSES, "running")


class _PartialRead:
    """A source whose LISTING succeeded but came back short (read_failed False, the task
    missing) while its id refresh FAILED — the state `not in open_ids and not
    tracker_read_failed` read as "removed from source"."""

    read_failed = False

    def list_open_tasks(self):
        return []

    def fetch_by_ids(self, ids):
        return None


@pytest.mark.parametrize("status", LIVE_STATUSES)
def test_a_failed_id_refresh_changes_no_row_even_when_the_listing_looked_fine(tmp_path, status):
    """🔴 GUARD: drop the `fetch_by_ids` call from `_tracker_gone` (or read its `None` as
    `[]`) ⇒ the row goes `done` off a short listing ⇒ RED."""
    wf = _wf(tmp_path)
    wt = _seed(wf, "abc123", status, tmp_path)

    summary, killed = _tick(wf, _PartialRead())

    assert summary["tracker_refresh_failed"] is True
    assert summary["reconciled_done"] == 0
    assert _status_of("abc123") == status
    assert killed == []
    assert wt.exists()


@pytest.mark.parametrize("status", LIVE_STATUSES)
def test_a_vanished_TODO_md_changes_no_row(tmp_path, status):
    """🔴 GUARD — the brief's named mutation on the markdown adapter: make `fetch_by_ids`
    return `[]` on a read failure ⇒ a live row goes `done` ⇒ RED."""
    wf = _wf(tmp_path)
    src = MarkdownSource(wf)
    tid = src.list_open_tasks()[0].id
    wt = _seed(wf, tid, status, tmp_path)
    (tmp_path / "TODO.md").unlink()

    summary, killed = _tick(wf, src)

    assert summary["tracker_refresh_failed"] is True
    assert summary["reconciled_done"] == 0
    assert _status_of(tid) == status
    assert wt.exists()


@pytest.mark.parametrize("status", LIVE_STATUSES)
def test_a_gh_timeout_changes_no_row(tmp_path, status):
    """🔴 GUARD — a `gh` timeout ⇒ `None` ⇒ no row changes. Corrupt the adapter's timeout
    branch to return `[]` ⇒ the row goes `done` ⇒ RED."""
    wf = _wf(tmp_path)
    src = _gh(tmp_path)
    tid = gh_task_id(REPO, ISSUE)
    wt = _seed(wf, tid, status, tmp_path)

    summary, killed = _tick(wf, src, gh_behaviour=_timeout)

    assert summary["tracker_refresh_failed"] is True
    assert summary["reconciled_done"] == 0
    assert _status_of(tid) == status
    assert wt.exists()


def test_the_refresh_failure_is_logged_once_not_every_tick(tmp_path, caplog):
    wf = _wf(tmp_path)
    _seed(wf, "abc123", "awaiting_review", tmp_path)
    with caplog.at_level("WARNING", logger=dispatcher.log.name):
        _tick(wf, _PartialRead())
        _tick(wf, _PartialRead())
    hits = [r for r in caplog.records if "id refresh FAILED" in r.getMessage()]
    assert len(hits) == 1


@pytest.mark.parametrize("status", LIVE_STATUSES)
def test_an_id_positively_reported_closed_is_reconciled_as_today(tmp_path, status):
    """⭐ MUST BE ACCEPTED: a closed issue (read OK) reconciles exactly as before — a review
    row goes `done` on the tracker alone; a running row goes `done` on its merged PR.
    Without this, a refresh that never says "gone" would pass every refusal above."""
    wf = _wf(tmp_path)
    src = _gh(tmp_path)
    tid = gh_task_id(REPO, ISSUE)
    _seed(wf, tid, status, tmp_path)

    summary, killed = _tick(
        wf, src,
        gh_behaviour=lambda cmd, *a, **k: _GhOut(stdout=_issues((ISSUE, "CLOSED")))
        if "all" in cmd else _GhOut(stdout="[]"),
    )

    assert summary["tracker_refresh_failed"] is False
    assert summary["reconciled_done"] == 1
    assert _status_of(tid) == "done"
    assert killed == ["test-1"]


def test_an_open_but_unlabelled_issue_is_not_done(tmp_path):
    """An issue that dropped out of the CLAIM listing (label removed) but is still OPEN is
    unroutable, not finished (SPEC 8.4) — the listing's absence alone no longer closes it."""
    wf = _wf(tmp_path)
    src = _gh(tmp_path)
    tid = gh_task_id(REPO, ISSUE)
    _seed(wf, tid, "awaiting_review", tmp_path)

    summary, _ = _tick(
        wf, src,
        gh_behaviour=lambda cmd, *a, **k: _GhOut(stdout=_issues((ISSUE, "OPEN")))
        if "all" in cmd else _GhOut(stdout="[]"),
    )

    assert summary["reconciled_done"] == 0
    assert _status_of(tid) == "awaiting_review"


def test_a_struck_markdown_line_is_reconciled_as_today(tmp_path):
    """⭐ MUST BE ACCEPTED (markdown): a human strikes the line ⇒ the review row goes done."""
    wf = _wf(tmp_path)
    src = MarkdownSource(wf)
    tid = src.list_open_tasks()[0].id
    _seed(wf, tid, "awaiting_review", tmp_path)
    (tmp_path / "TODO.md").write_text("- [x] do a thing\n")

    summary, _ = _tick(wf, src)

    assert summary["reconciled_done"] == 1
    assert _status_of(tid) == "done"
