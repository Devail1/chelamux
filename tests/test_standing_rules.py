"""📜 CMX-317: `{{standing_rules}}` — one short rules file every dispatch prompt renders.

The orchestrator was typing the same rules into each dispatched agent by hand. These pin
that `agent.standing_rules` (a path relative to the repo) reaches the first-dispatch
prompt, `dry_run`'s preview and the rework prompt; that a missing or unreadable file
renders as "" with a warning, never a crash and never a literal `{{standing_rules}}`; and
that the seeded file stays a short list (the length cap).
"""
from __future__ import annotations

import logging
import sqlite3
from pathlib import Path

import pytest

from chela import dispatcher
from chela.sources import Task
from chela.standing_rules import standing_rules
from chela.workflow import WorkflowDef, render_prompt

_REPO_ROOT = Path(__file__).resolve().parent.parent
_RULES_FILE = _REPO_ROOT / "docs" / "AGENT_STANDING_RULES.md"
_RULE = "- Never feed the gremlins after midnight; xyzzy317 is why."
_TEMPLATE = "> {{task_title}}\n\n{{standing_rules}}\n\nend"


@pytest.fixture(autouse=True)
def _own_runs_db(tmp_path, monkeypatch):
    monkeypatch.setattr(dispatcher, "DB_PATH", tmp_path / "scheduler.db")


def _wf(repo: Path, agent: dict | None = None, template: str = _TEMPLATE) -> WorkflowDef:
    config: dict = {"project_key": "TEST"}
    if agent is not None:
        config["agent"] = agent
    return WorkflowDef(path=repo / "WORKFLOW.md", config=config, prompt_template=template)


def _task() -> Task:
    return Task(id="abc123", title="short title", file="TODO.md", line_number=3,
                raw="- [ ] short title")


def _render(wf: WorkflowDef) -> str:
    return render_prompt(wf.prompt_template,
                         dispatcher._prompt_vars(wf, _task(), "/wt", "test-1", "dev", 1))


def _write_rules(repo: Path, rel: str = "docs/RULES.md") -> None:
    (repo / rel).parent.mkdir(parents=True, exist_ok=True)
    (repo / rel).write_text(f"## Standing rules\n\n{_RULE}\n")


# --- the first-dispatch prompt ---------------------------------------------------------

def test_a_rendered_dispatch_prompt_contains_the_files_rules(tmp_path):
    _write_rules(tmp_path)
    prompt = _render(_wf(tmp_path, {"standing_rules": "docs/RULES.md"}))
    assert _RULE in prompt
    assert "{{standing_rules}}" not in prompt


def test_the_path_is_relative_to_the_repo_not_the_cwd(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    _write_rules(repo)
    elsewhere = tmp_path / "elsewhere"
    _write_rules(elsewhere)
    (elsewhere / "docs" / "RULES.md").write_text("- the WRONG file\n")
    monkeypatch.chdir(elsewhere)
    prompt = _render(_wf(repo, {"standing_rules": "docs/RULES.md"}))
    assert _RULE in prompt
    assert "WRONG" not in prompt


def test_a_missing_file_renders_empty_and_warns(tmp_path, caplog):
    """The negative control: same template, no file on disk."""
    with caplog.at_level(logging.WARNING, logger="chela.standing_rules"):
        prompt = _render(_wf(tmp_path, {"standing_rules": "docs/NOPE.md"}))
    assert prompt == "> short title\n\n\n\nend"
    assert any("agent.standing_rules file" in r.getMessage() and "NOPE.md" in r.getMessage()
               for r in caplog.records)


def test_an_unset_key_renders_empty_without_a_warning(tmp_path, caplog):
    with caplog.at_level(logging.WARNING, logger="chela.standing_rules"):
        assert standing_rules(_wf(tmp_path, {"cmd": "claude"})) == ""
        assert standing_rules(_wf(tmp_path)) == ""
    assert caplog.records == []


@pytest.mark.parametrize("value", [["docs/RULES.md"], 7, "", "   "])
def test_a_value_that_is_not_a_path_renders_empty_and_warns(tmp_path, caplog, value):
    _write_rules(tmp_path)
    with caplog.at_level(logging.WARNING, logger="chela.standing_rules"):
        assert standing_rules(_wf(tmp_path, {"standing_rules": value})) == ""
    assert any("is not a file path" in r.getMessage() for r in caplog.records)


def test_a_directory_is_an_oserror_that_renders_empty_and_warns(tmp_path, caplog):
    """`IsADirectoryError` — an `OSError` subclass, not `FileNotFoundError`."""
    (tmp_path / "docs").mkdir()
    with caplog.at_level(logging.WARNING, logger="chela.standing_rules"):
        assert standing_rules(_wf(tmp_path, {"standing_rules": "docs"})) == ""
    assert any("could not be read" in r.getMessage() for r in caplog.records)


def test_a_file_that_is_not_utf8_renders_empty_and_warns(tmp_path, caplog):
    (tmp_path / "RULES.md").write_bytes(b"\xff\xfe\xfa not utf-8")
    with caplog.at_level(logging.WARNING, logger="chela.standing_rules"):
        assert standing_rules(_wf(tmp_path, {"standing_rules": "RULES.md"})) == ""
    assert any("could not be read" in r.getMessage() for r in caplog.records)


def test_the_value_is_stripped_of_surrounding_whitespace(tmp_path):
    (tmp_path / "RULES.md").write_text(f"\n\n{_RULE}\n\n")
    assert standing_rules(_wf(tmp_path, {"standing_rules": " RULES.md "})) == _RULE


# --- dry_run and rework ----------------------------------------------------------------

def test_dry_run_renders_the_rules(tmp_path):
    _write_rules(tmp_path)
    (tmp_path / "WORKFLOW.md").write_text(
        "---\nproject_key: TEST\ntracker:\n  kind: markdown\n  path: TODO.md\n"
        "agent:\n  standing_rules: docs/RULES.md\n---\n" + _TEMPLATE + "\n"
    )
    (tmp_path / "TODO.md").write_text("- [ ] short title\n")
    plans = dispatcher.dry_run(tmp_path / "WORKFLOW.md")
    assert len(plans) == 1
    assert _RULE in plans[0]["prompt"]


def _rework_row() -> sqlite3.Row:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    dispatcher.ensure_schema(conn)
    fields = {
        "task_id": "abc123", "workflow_path": "/repo/WORKFLOW.md", "title": "short title",
        "status": "changes_requested", "window_name": "test-1", "worktree_path": "/wt/abc123",
        "branch_name": "test-1", "started_at": "2026-07-14T10:00:00+00:00", "attempt": 1,
        "task_number": 1, "pr_url": "https://github.com/o/r/pull/1", "pr_state": "open",
        "rework_count": 0, "review_history": None,
    }
    conn.execute(f"INSERT INTO runs ({', '.join(fields)}) VALUES ({', '.join('?' * len(fields))})",
                 tuple(fields.values()))
    return conn.execute("SELECT * FROM runs WHERE task_id='abc123'").fetchone()


def test_the_builtin_rework_prompt_renders_the_rules(tmp_path):
    _write_rules(tmp_path)
    wf = _wf(tmp_path, {"standing_rules": "docs/RULES.md"})
    prompt = render_prompt(dispatcher.REWORK_PROMPT,
                           dispatcher._rework_vars(wf, _rework_row(), "/wt", "fix it", 1))
    assert _RULE in prompt
    assert "{{standing_rules}}" not in prompt


def test_the_builtin_rework_prompt_renders_with_no_rules_file(tmp_path):
    prompt = render_prompt(dispatcher.REWORK_PROMPT,
                           dispatcher._rework_vars(_wf(tmp_path), _rework_row(), "/wt", "fix", 1))
    assert "{{standing_rules}}" not in prompt
    assert "**Do NOT touch the tracker file.**\n\n\n\n⛔" in prompt


# --- the seeded file -------------------------------------------------------------------

def test_the_seeded_rules_file_stays_under_the_length_cap():
    """A list of rules, not a manual: 40 lines, headings and blanks included."""
    lines = _RULES_FILE.read_text(encoding="utf-8").splitlines()
    assert 0 < len(lines) <= 40


@pytest.mark.parametrize("needle", [
    "Never wrap `chela task-finished` in an outer `timeout`",
    "Full suites run one at a time box-wide",
    "Merge `origin/<base>`",
    "check `git diff` for a leftover mutation",
    "If the same failure repeats twice, stop and report",
    "Never put another issue's id in a PR title or body",
    "`[c]hela …`",
    "Reply to the orchestrator with SendMessage, not tmux",
])
def test_the_seeded_file_carries_each_rule_on_one_line(needle):
    rules = [ln for ln in _RULES_FILE.read_text(encoding="utf-8").splitlines()
             if ln.startswith("- ")]
    assert sum(needle in ln for ln in rules) == 1


def test_the_seeded_file_renders_through_the_real_loader():
    wf = _wf(_REPO_ROOT, {"standing_rules": "docs/AGENT_STANDING_RULES.md"})
    assert "Never wrap `chela task-finished`" in _render(wf)
