"""🔎 CMX-209 — task-finished refuses a code PR whose body has no ``## Review`` section."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path
from unittest.mock import call, patch

import pytest

from chela import dispatcher, reviewgate
from chela.workflow import parse_workflow
from tests.test_judge_sweep import _git, _run_row, _task_finished

REAL_REVIEW_GATE = True
REVIEWED = "## Summary\nx\n\n## Review\n0 findings.\n"


def _repo(tmp_path, monkeypatch, changed: str) -> Path:
    """A run on ``feature`` that changes only ``changed`` vs ``origin/dev``."""
    monkeypatch.setattr(dispatcher, "DB_PATH", tmp_path / "scheduler.db")
    wt = tmp_path / "wt"
    (wt / "skills" / "x").mkdir(parents=True)
    (wt / "base.py").write_text("x = 1\n")
    _git(wt, "init", "-q", "-b", "dev")
    _git(wt, "add", "-A")
    _git(wt, "commit", "-qm", "base")
    _git(wt, "update-ref", "refs/remotes/origin/dev", _git(wt, "rev-parse", "HEAD"))
    _git(wt, "checkout", "-qb", "feature")
    (wt / changed).write_text("changed\n")
    _git(wt, "add", "-A")
    _git(wt, "commit", "-qm", "feature")
    _run_row(tmp_path, wt)
    return wt


def _push(wt: Path, body) -> None:
    dispatcher._push_request_path(wt).write_text(json.dumps({"pr_title": "t", "pr_body": body}))


@pytest.mark.parametrize("body, ok", [
    (REVIEWED, True),
    ("## Review\n3 findings: 2 fixed, 1 dismissed\n### Fixed\na.py:1 fixed\n", True),
    ("## Review  \r\n0 findings\r\n", True),
    ("## Review\n\n   \n## Test plan\nran it\n", False),     # empty section is a dodge
    ("### Review\n0 findings\n", False),                     # wrong level
    ("## Reviewer notes\n0 findings\n", False),              # a prefix is not the heading
    ("text ## Review\n0 findings\n", False),                 # not at a line start
])
def test_has_review(body, ok):
    assert reviewgate.has_review(body) is ok


@pytest.mark.parametrize("changed", ["feat.py", "skills/x/run"])
def test_a_code_pr_without_review_is_refused_and_with_it_accepted(tmp_path, monkeypatch, changed):
    wt = _repo(tmp_path, monkeypatch, changed)
    _push(wt, "## Summary\nno review\n")
    assert dispatcher.check_review_section("cmx-88") == {"ok": False, "error": reviewgate.REFUSAL}
    _push(wt, REVIEWED)
    assert dispatcher.check_review_section("cmx-88") == {"ok": True, "record": None}


@pytest.mark.parametrize("changed", ["README.md", "docs.txt"])
def test_a_prose_only_pr_needs_no_review(tmp_path, monkeypatch, changed):
    _repo(tmp_path, monkeypatch, changed)            # no push marker at all: never read
    assert dispatcher.check_review_section("cmx-88") == {"ok": True, "record": None}


def test_no_review_reason_is_accepted_and_recorded(tmp_path, monkeypatch):
    wt = _repo(tmp_path, monkeypatch, "feat.py")
    _push(wt, "no review")
    assert dispatcher.check_review_section("cmx-88", "  /code-review unavailable  ") == {
        "ok": True, "record": {"no_review_reason": "/code-review unavailable"}}
    assert dispatcher.check_review_section("cmx-88", "   ")["ok"] is False


def test_an_unreadable_body_fails_closed(tmp_path, monkeypatch):
    _repo(tmp_path, monkeypatch, "feat.py")          # code diff, no PR, no push marker
    out = dispatcher.check_review_section("cmx-88")
    assert out["ok"] is False
    assert out["error"].startswith("could not read this PR's body — no PR is open and no "
                                   "`chela request-push` is pending")
    assert out["error"].endswith("Unknown is not reviewed.")
    assert dispatcher.check_review_section("cmx-1")["error"] == \
        "no run with a worktree on record for cmx-1"


def test_an_open_pr_is_read_from_github_not_the_marker(tmp_path, monkeypatch):
    wt = _repo(tmp_path, monkeypatch, "feat.py")
    _push(wt, REVIEWED)                              # stale: the open PR is authoritative
    with dispatcher._db() as conn:
        conn.execute("UPDATE runs SET pr_url='https://github.com/o/r/pull/7'").connection.commit()
    real, done = subprocess.run, subprocess.CompletedProcess([], 0, "## Summary\nx\n", "")
    with patch.object(reviewgate.subprocess, "run",
                      side_effect=lambda cmd, **kw: done if cmd[0] == "gh" else real(cmd, **kw)
                      ) as run:
        assert dispatcher.check_review_section("cmx-88")["ok"] is False
    assert [c for c in run.call_args_list if c.args[0][0] == "gh"] == [call(
        ["gh", "pr", "view", "https://github.com/o/r/pull/7", "--json", "body", "-q", ".body"],
        cwd=str(wt), capture_output=True, text=True, timeout=30)]


@pytest.mark.parametrize("effect, error", [
    (subprocess.CompletedProcess([], 1, "", "no auth"), "`gh pr view u` failed: no auth"),
    (FileNotFoundError("gh"), "`gh pr view` could not run"),
    (subprocess.TimeoutExpired("gh", 30), "`gh pr view` could not run"),
])
def test_gh_that_fails_raises(tmp_path, effect, error):
    kw = {"return_value": effect} if isinstance(effect, subprocess.CompletedProcess) \
        else {"side_effect": effect}
    with patch.object(reviewgate.subprocess, "run", **kw), \
            pytest.raises(RuntimeError, match=error.replace("`", ".")):
        reviewgate.read_body(tmp_path, "u", tmp_path / "m.json")


@pytest.mark.parametrize("marker, error", [
    (None, "no PR is open and no `chela request-push` is pending"),
    ("{not json", "no PR is open and no `chela request-push` is pending"),
    ('{"pr_title": null, "pr_body": null}', "carries no PR body"),
    ('["x"]', "carries no PR body"),
])
def test_a_marker_without_a_body_raises(tmp_path, marker, error):
    path = tmp_path / "m.json"
    if marker is not None:
        path.write_text(marker)
    with pytest.raises(RuntimeError, match=error.replace("`", ".")):
        reviewgate.read_body(tmp_path, None, path)
    path.write_text('{"pr_body": "b"}')
    assert reviewgate.read_body(tmp_path, None, path) == "b"


def test_cli_refuses_before_the_sweep(tmp_path, monkeypatch, capsys):
    _push(_repo(tmp_path, monkeypatch, "feat.py"), "no review")
    with patch.object(dispatcher, "verify_sweep") as swept, \
            patch.object(dispatcher, "request_task_finished") as req:
        assert _task_finished() == 1
    assert "task-finished: 🔎 this PR's body has no `## Review` section" in \
        capsys.readouterr().out
    assert not swept.called and not req.called


@pytest.mark.parametrize("extra, record", [
    ((), None), (("--no-review-reason", "why"), {"no_review_reason": "why"})])
def test_cli_hands_the_record_to_the_marker(tmp_path, monkeypatch, extra, record):
    wt = _repo(tmp_path, monkeypatch, "feat.py")
    _push(wt, "no review" if record else REVIEWED)
    with patch.object(dispatcher, "check_no_new_guards", return_value=False), \
            patch.object(dispatcher, "verify_sweep", return_value={"ok": True, "skipped": "x"}):
        assert _task_finished(*extra) is None
    assert dispatcher._read_task_finished_size_gate(wt, "review_gate") == record


@pytest.mark.parametrize("record", [{"no_review_reason": "no tool"}, None])
def test_the_run_records_it_and_the_judge_and_pr_see_it(tmp_path, monkeypatch, record):
    wt = _repo(tmp_path, monkeypatch, "feat.py")
    with dispatcher._db() as conn:
        conn.execute("UPDATE runs SET pr_url='https://github.com/o/r/pull/7', review_gate='{}'")
        conn.commit()
    with patch.object(dispatcher, "_read_pr_status", return_value=(None, None)), \
            patch.object(dispatcher, "_append_pr_body", return_value=(True, "")) as body:
        assert dispatcher.mark_awaiting_review("cmx-88", review_gate=record)["ok"]
    with dispatcher._db() as conn:
        row = conn.execute("SELECT * FROM runs").fetchone()
    assert row["review_gate"] == (record and json.dumps(record))
    line = "🔎 **No self-review:** agent's reason: no tool"
    assert [c.args[2] for c in body.call_args_list] == ([line] if record else [])
    wf = parse_workflow(Path("W.md"), "---\nproject_key: TT\n---\n")
    prompt = dispatcher.render_prompt(dispatcher.JUDGE_PROMPT,
                                      dispatcher._judge_vars(wf, row, wt, "deadbeef"))
    assert (line in prompt) is bool(record) and ("No self-review" in prompt) is bool(record)
