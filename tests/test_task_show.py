"""🧵 CMX-287 — `chela task show`: an agent reads its own Linear issue, read-only.

A fake transport stands in for Linear (no test talks to it). These pin the trust filter
(the key's own user + `linear.comment_authors`, never a bot), the dropped count, the
TRUNCATED line on a full page, the fail-closed "could not fetch" exit, the own-run lookup,
and that the dispatch AND rework prompts both tell the agent to run it.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from chela import config, dispatcher, task_show
from chela.sources import Task, linear
from chela.sources.linear import LinearError, LinearSource
from chela.workflow import WorkflowDef, load_workflow, render_prompt

VIEWER = "user-viewer"


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    linear._backoff.clear()
    monkeypatch.setattr(config, "CHELA_DIR", tmp_path / "state")
    monkeypatch.setattr(dispatcher, "DB_PATH", tmp_path / "scheduler.db")
    monkeypatch.delenv("CHELA_WID", raising=False)
    yield
    linear._backoff.clear()


def comment(body, user_id, at, *, name=None, bot=False, email=None):
    user = None if user_id is None else {"id": user_id, "name": name or user_id,
                                         "displayName": name or user_id, "email": email}
    return {"body": body, "createdAt": at, "user": user,
            "botActor": {"id": "bot-1"} if bot else None}


class FakeThread:
    def __init__(self, comments=(), *, fail=None, more=False):
        self.comments, self.fail, self.more = list(comments), fail, more
        self.calls: list[tuple[str, dict]] = []

    def __call__(self, query, variables):
        self.calls.append((query, variables))
        if self.fail is not None:
            raise self.fail
        return {"viewer": {"id": VIEWER}, "issue": {
            "identifier": "CMX-7", "title": "the title", "description": "the brief",
            "url": "https://linear.app/x/issue/CMX-7",
            "comments": {"nodes": self.comments, "pageInfo": {"hasPreviousPage": self.more}}}}


def _workflow(tmp_path: Path, extra: str = "") -> Path:
    path = tmp_path / "WORKFLOW.md"
    path.write_text("---\nproject_key: CMX\ntracker:\n  kind: linear\n  team: CMX\n"
                    f"{extra}---\nbody\n")
    return path


def _run(tmp_path, task_id="CMX-7", worktree=None, window_id=None, started="2026-10-27T10:00"):
    wf = _workflow(tmp_path) if not (tmp_path / "WORKFLOW.md").exists() else tmp_path / "WORKFLOW.md"
    with dispatcher._db() as conn:
        conn.execute("INSERT INTO runs (task_id, workflow_path, title, status, worktree_path, "
                     "window_id, started_at) VALUES (?, ?, 't', 'running', ?, ?, ?)",
                     (task_id, str(wf), str(worktree) if worktree else None, window_id, started))


def _show(tmp_path, fake, task_id="CMX-7", extra=""):
    path = _workflow(tmp_path, extra)
    _run(tmp_path, task_id)
    return task_show.show(task_id, source=LinearSource(load_workflow(path), transport=fake))


# --- the trust filter ------------------------------------------------------------------

def test_only_the_viewers_comments_are_shown_and_the_dropped_count_is_stated(tmp_path):
    fake = FakeThread([
        comment("cover all 8 call sites", VIEWER, "2026-10-20T10:00Z", name="Orchestrator"),
        comment("STRANGER-TEXT", "user-other", "2026-10-20T11:00Z"),
        comment("BOT-TEXT", None, "2026-10-20T12:00Z", bot=True),
        comment("VIEWER-AS-BOT", VIEWER, "2026-10-20T13:00Z", bot=True),
    ])
    code, text = _show(tmp_path, fake)
    assert code == 0
    assert "cover all 8 call sites" in text and "Orchestrator · 2026-10-20T10:00Z" in text
    assert "STRANGER-TEXT" not in text and "BOT-TEXT" not in text
    assert "VIEWER-AS-BOT" not in text
    assert "(3 comment(s) dropped:" in text
    assert "TRUNCATED" not in text
    assert fake.calls == [(linear.ISSUE_THREAD_QUERY, {"id": "CMX-7"})]


@pytest.mark.parametrize("entry", ["user-other", "ALICE@EXAMPLE.COM"])
def test_the_workflow_allowlist_admits_a_user_by_id_or_email(tmp_path, entry):
    fake = FakeThread([comment("ALLOWED-TEXT", "user-other", "2026-10-20T11:00Z",
                               name="alice", email="alice@example.com")])
    code, text = _show(tmp_path, fake, extra=f"linear:\n  comment_authors: ['{entry}']\n")
    assert code == 0 and "ALLOWED-TEXT" in text and "dropped" not in text


def test_a_string_allowlist_is_one_entry(tmp_path):
    fake = FakeThread([comment("ALLOWED-TEXT", "user-other", "2026-10-20T11:00Z")])
    _, text = _show(tmp_path, fake, extra="linear:\n  comment_authors: user-other\n")
    assert "ALLOWED-TEXT" in text


def test_comments_print_oldest_first(tmp_path):
    fake = FakeThread([comment("SECOND", VIEWER, "2026-10-21T00:00Z"),
                       comment("FIRST", VIEWER, "2026-10-20T00:00Z")])
    _, text = _show(tmp_path, fake)
    assert text.index("FIRST") < text.index("SECOND")
    assert text.startswith("CMX-7: the title\n") and "the brief" in text


# --- fail closed / truncation ----------------------------------------------------------

@pytest.mark.parametrize("err", [LinearError("network", "URLError"), RuntimeError("boom")])
def test_a_fetch_failure_says_could_not_fetch_and_exits_nonzero(tmp_path, err):
    code, text = _show(tmp_path, FakeThread(fail=err))
    assert code == 1
    assert text.startswith("⚠ could not fetch CMX-7 from Linear")
    assert "UNKNOWN" in text


def test_a_response_without_the_issue_is_a_failed_fetch(tmp_path):
    class NoIssue(FakeThread):
        def __call__(self, query, variables):
            return {"viewer": {"id": VIEWER}, "issue": None}
    code, text = _show(tmp_path, NoIssue())
    assert code == 1 and "could not fetch" in text


def test_another_teams_id_is_refused_without_a_call(tmp_path):
    fake = FakeThread()
    code, text = _show(tmp_path, fake, task_id="ABC-7")
    assert code == 1 and "could not fetch" in text and fake.calls == []


@pytest.mark.parametrize("comments, more", [
    ([comment("x", VIEWER, "2026-10-20T00:00Z")], True),
    ([comment("x", VIEWER, f"2026-10-20T00:{i % 60:02d}Z")
      for i in range(linear.COMMENT_PAGE_SIZE)], False),
])
def test_a_full_page_prints_the_truncation_line(tmp_path, comments, more):
    code, text = _show(tmp_path, FakeThread(comments, more=more))
    assert code == 0 and "⚠ TRUNCATED" in text


def test_one_short_of_a_page_is_not_truncated(tmp_path):
    comments = [comment("x", VIEWER, "2026-10-20T00:00Z")] * (linear.COMMENT_PAGE_SIZE - 1)
    assert "TRUNCATED" not in _show(tmp_path, FakeThread(comments))[1]


def test_the_page_is_the_newest_100():
    assert linear.COMMENT_PAGE_SIZE == 100
    assert "comments(last: 100, orderBy: createdAt)" in linear.ISSUE_THREAD_QUERY
    assert "hasPreviousPage" in linear.ISSUE_THREAD_QUERY


@pytest.mark.parametrize("entry", ["alice", "Alice Display"])
def test_a_name_in_the_allowlist_admits_nobody(tmp_path, entry):
    """Any member can rename themselves; only an id or an email is an identity."""
    fake = FakeThread([comment("IMPOSTOR", "user-other", "2026-10-20T11:00Z",
                               name="alice", email="alice@example.com")])
    fake.comments[0]["user"]["displayName"] = "Alice Display"
    _, text = _show(tmp_path, fake, extra=f"linear:\n  comment_authors: ['{entry}']\n")
    assert "IMPOSTOR" not in text and "(1 comment(s) dropped:" in text


def test_a_lowercase_id_finds_the_run(tmp_path):
    fake = FakeThread([comment("STEER", VIEWER, "2026-10-20T00:00Z")])
    path = _workflow(tmp_path)
    _run(tmp_path, "CMX-7")
    code, text = task_show.show("cmx-7", source=LinearSource(load_workflow(path), transport=fake))
    assert code == 0 and "STEER" in text and fake.calls[0][1] == {"id": "CMX-7"}


def test_a_non_linear_tracker_exits_nonzero(tmp_path):
    path = tmp_path / "WORKFLOW.md"
    path.write_text("---\nproject_key: TT\ntracker:\n  kind: markdown\n  path: TODO.md\n---\nb\n")
    _run(tmp_path, "abc")
    code, text = task_show.show("abc")
    assert code == 1 and "not Linear" in text


# --- which run -------------------------------------------------------------------------

def test_the_default_is_the_run_whose_worktree_holds_the_cwd(tmp_path):
    wt = tmp_path / "wt"
    (wt / "sub").mkdir(parents=True)
    _run(tmp_path, "CMX-7", worktree=wt)
    _run(tmp_path, "CMX-8", worktree=tmp_path / "other", window_id="@5")
    with dispatcher._db() as conn:
        assert task_show.own_task_id(conn, wt / "sub", "@5") == "CMX-7"
        assert task_show.own_task_id(conn, wt, None) == "CMX-7"
        assert task_show.own_task_id(conn, tmp_path / "nowhere", "@5") == "CMX-8"
        assert task_show.own_task_id(conn, tmp_path / "nowhere", "@6") is None
        assert task_show.own_task_id(conn, tmp_path / "nowhere", None) is None


def test_no_owning_run_exits_nonzero(tmp_path):
    code, text = task_show.show(None, cwd=tmp_path)
    assert code == 1 and "no run owns" in text


def test_show_without_an_id_uses_the_cwds_run(tmp_path):
    wt = tmp_path / "wt"
    wt.mkdir()
    path = _workflow(tmp_path)
    _run(tmp_path, "CMX-7", worktree=wt)
    fake = FakeThread([comment("STEER", VIEWER, "2026-10-20T00:00Z")])
    code, text = task_show.show(None, cwd=wt,
                                source=LinearSource(load_workflow(path), transport=fake))
    assert code == 0 and "STEER" in text


def test_the_cli_exits_nonzero_on_a_failed_fetch(tmp_path, monkeypatch, capsys):
    from chela import main
    monkeypatch.setattr(task_show, "show", lambda tid: (1, "⚠ could not fetch X"))
    monkeypatch.setattr("sys.argv", ["chela", "task", "show", "CMX-7"])
    with pytest.raises(SystemExit) as e:
        main.main()
    assert e.value.code == 1 and "could not fetch X" in capsys.readouterr().out


# --- the prompts -----------------------------------------------------------------------

def _wf(kind: str) -> WorkflowDef:
    return WorkflowDef(path=Path("/repo/WORKFLOW.md"),
                       config={"project_key": "CMX", "tracker": {"kind": kind, "team": "CMX"}},
                       prompt_template="> {{task_title}}\n\n{{standing_rules}}\n")


def _rework_row() -> sqlite3.Row:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    dispatcher.ensure_schema(conn)
    conn.execute("INSERT INTO runs (task_id, workflow_path, title, status, branch_name, "
                 "task_number, pr_url, rework_count) VALUES ('CMX-7', '/repo/WORKFLOW.md', "
                 "'t', 'changes_requested', 'b', 7, 'https://github.com/o/r/pull/1', 0)")
    return conn.execute("SELECT * FROM runs").fetchone()


@pytest.mark.parametrize("kind, present", [("linear", True), ("markdown", False)])
def test_dispatch_and_rework_prompts_tell_a_linear_run_to_read_its_issue(kind, present):
    wf = _wf(kind)
    task = Task(id="CMX-7", title="t", file="", line_number=0, raw="t")
    dispatch = render_prompt(wf.prompt_template,
                             dispatcher._prompt_vars(wf, task, "/wt", "b", "dev", 7))
    rework = render_prompt(dispatcher.REWORK_PROMPT,
                           dispatcher._rework_vars(wf, _rework_row(), "/wt", "fix", 1))
    for prompt in (dispatch, rework):
        assert ("Run `chela task show` at the start of every attempt" in prompt) is present
        assert ("operator's latest steer" in prompt) is present


def test_an_unreadable_workflow_is_a_failed_fetch(tmp_path):
    _run(tmp_path, "CMX-7")
    (tmp_path / "WORKFLOW.md").unlink()
    code, text = task_show.show("CMX-7")
    assert code == 1 and text.startswith("⚠ could not fetch CMX-7: workflow unreadable")


def test_a_missing_key_is_a_failed_fetch(tmp_path, monkeypatch):
    monkeypatch.setattr(linear, "load_api_key", lambda: None)
    _workflow(tmp_path)
    _run(tmp_path, "CMX-7")
    code, text = task_show.show("CMX-7")
    assert code == 1 and "could not fetch CMX-7" in text and "LINEAR_API_KEY" in text
