"""Tests for the repo seeder behind the dashboard's "Init a repo" button.

Covers project-key derivation, that the dispatcher's literal `{{...}}` template
vars survive seeding, the never-overwrite rule, and bad-path rejection — all
against a temp dir so no real repo is touched.
"""
from __future__ import annotations

import pytest

from chela import starter


def test_project_key_for():
    assert starter.project_key_for("chelamux") == "CHELA"
    assert starter.project_key_for("my-cool.repo") == "MYCOO"
    assert starter.project_key_for("___") == "PROJ"   # no alnum -> fallback


def test_seed_creates_both_files_with_derived_key(tmp_path):
    repo = tmp_path / "my-repo"
    repo.mkdir()
    result = starter.seed_repo(str(repo))

    assert result["ok"] is True
    assert result["created"] == ["WORKFLOW.md", "TODO.md"]
    assert result["skipped"] == []
    assert result["is_git"] is False   # no .git in this temp dir

    wf = (repo / "WORKFLOW.md").read_text()
    assert "project_key: MYREP" in wf
    assert "~/.chela/worktrees/my-repo" in wf
    # The dispatcher's own template vars must pass through verbatim (not mangled
    # by the frontmatter token replacement).
    assert "{{task_title}}" in wf
    assert "{{base_branch}}" in wf
    # ⛔ issue #502 B2 rework round 1 (THE JUDGE), finding 5: this seeded WORKFLOW.md is
    # production code — it is the Done Criteria every adopter's dispatched agent actually
    # reads, and step 4 is the whole reason `chela request-push` gets used at all instead
    # of the sandboxed `git push`/`gh pr create` #502 B2 exists to remove. Pin the literal
    # command line, not just that the template vars survive — a revert to the pre-#502-B2
    # spelling would still pass the vars-survive checks above.
    assert ('`chela request-push {{task_id}} --pr-title '
            '"{{project_key}}-{{task_number}}: <summary>" --pr-body-file <path>`') in wf
    assert "git push -u origin {{branch_name}}" not in wf
    assert "gh pr create --base {{base_branch}}" not in wf
    assert (repo / "TODO.md").read_text().startswith("# TODO")


def test_seed_never_overwrites(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "WORKFLOW.md").write_text("KEEP ME")
    result = starter.seed_repo(str(repo))

    assert result["created"] == ["TODO.md"]
    assert result["skipped"] == ["WORKFLOW.md"]
    assert (repo / "WORKFLOW.md").read_text() == "KEEP ME"


def test_seed_detects_git(tmp_path):
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    assert starter.seed_repo(str(repo))["is_git"] is True


def test_seed_rejects_missing_dir(tmp_path):
    with pytest.raises(ValueError):
        starter.seed_repo(str(tmp_path / "does-not-exist"))
