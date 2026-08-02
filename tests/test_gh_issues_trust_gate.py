"""On a public repo, anyone can open a GitHub issue. GhIssuesSource turns every
issue it returns into a dispatchable agent run, so an unfiltered list is
remote code execution by issue creation. These tests pin the trust gate: only
issues from an author GitHub reports as OWNER/MEMBER/COLLABORATOR (or an
explicit `allowed_associations` override) are eligible.
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

from chela.sources.gh_issues import GhIssuesSource
from chela.workflow import WorkflowDef


def _wf(**tracker_overrides) -> WorkflowDef:
    tracker = {"kind": "gh_issues", "repo": "acme/widgets"}
    tracker.update(tracker_overrides)
    return WorkflowDef(
        path=Path("/tmp/does-not-matter/WORKFLOW.md"),
        config={"project_key": "ACM", "tracker": tracker},
        prompt_template="",
    )


def _issue(number, association, labels=()):
    return {
        "number": number,
        "title": f"issue {number}",
        "url": f"https://github.com/acme/widgets/issues/{number}",
        "labels": [{"name": lbl} for lbl in labels],
        "authorAssociation": association,
    }


class _FakeCompletedProcess:
    def __init__(self, stdout: str, returncode: int = 0, stderr: str = ""):
        self.stdout = stdout
        self.returncode = returncode
        self.stderr = stderr


def _run_with_issues(issues):
    return _FakeCompletedProcess(json.dumps(issues))


def test_default_gate_admits_owner_member_collaborator_only():
    issues = [
        _issue(1, "OWNER"),
        _issue(2, "MEMBER"),
        _issue(3, "COLLABORATOR"),
        _issue(4, "CONTRIBUTOR"),
        _issue(5, "FIRST_TIME_CONTRIBUTOR"),
        _issue(6, "NONE"),
    ]
    src = GhIssuesSource(_wf())
    with patch("subprocess.run", return_value=_run_with_issues(issues)):
        tasks = src.list_open_tasks()
    assert {t.line_number for t in tasks} == {1, 2, 3}


def test_anonymous_public_issue_is_dropped_by_default():
    # The exact scenario in the vulnerability report: a random public user
    # (association NONE) opens an issue. It must never become a Task.
    issues = [_issue(42, "NONE")]
    src = GhIssuesSource(_wf())
    with patch("subprocess.run", return_value=_run_with_issues(issues)):
        tasks = src.list_open_tasks()
    assert tasks == []


def test_blocked_label_still_applies_on_top_of_the_trust_gate():
    issues = [_issue(1, "OWNER", labels=["blocked"])]
    src = GhIssuesSource(_wf())
    with patch("subprocess.run", return_value=_run_with_issues(issues)):
        tasks = src.list_open_tasks()
    assert tasks == []


def test_allowed_associations_override_is_respected():
    issues = [_issue(1, "CONTRIBUTOR")]
    src = GhIssuesSource(_wf(allowed_associations=["CONTRIBUTOR"]))
    with patch("subprocess.run", return_value=_run_with_issues(issues)):
        tasks = src.list_open_tasks()
    assert {t.line_number for t in tasks} == {1}


def test_missing_author_association_field_is_rejected_not_admitted():
    # A `gh` CLI/API change that silently drops the field must fail closed,
    # not fail open into "every issue qualifies".
    issue = _issue(1, "OWNER")
    del issue["authorAssociation"]
    src = GhIssuesSource(_wf())
    with patch("subprocess.run", return_value=_run_with_issues([issue])):
        tasks = src.list_open_tasks()
    assert tasks == []
