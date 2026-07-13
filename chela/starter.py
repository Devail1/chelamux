"""Seed a repo with a starter ``WORKFLOW.md`` + ``TODO.md`` for the dispatcher.

Used by the dashboard's "Init a repo" button (POST /api/dispatcher/init) and
available for reuse elsewhere. The templates are embedded here (not read from
the repo's ``examples/``, which isn't shipped in the wheel) so this works the
same from a source checkout or a pip install. Mirror any change to
``examples/WORKFLOW.md`` / ``examples/TODO.md`` here.

Existing files are never overwritten — a re-init reports them as ``skipped``.
"""

from __future__ import annotations

import re
from pathlib import Path

# Frontmatter values are injected by replacing these tokens. We deliberately do
# NOT use str.format: the body is full of literal `{{task_title}}` dispatcher
# vars and JSON `{ ... }` braces that format() would trip over.
_WORKFLOW_TEMPLATE = """---
# Starter chela dispatcher workflow (seeded by the dashboard). Each unchecked
# `- [ ]` line in TODO.md becomes: a git worktree -> an agent that implements it
# and opens a PR -> a run that flips to `done` when you merge.

project_key: __PROJECT_KEY__   # short uppercase key; branches/windows are <key>-<n>

tracker:
  kind: markdown          # markdown TODO.md (also: gh_issues)
  path: TODO.md           # relative to this file

workspace:
  root: ~/.chela/worktrees/__SLUG__   # where per-task git worktrees are created
  base_branch: __BASE_BRANCH__        # branch worktrees fork from and PRs target

concurrency:
  max: 1                  # how many tasks may be in flight at once

agent:
  # Default `claude --permission-mode auto`: a classifier auto-approves safe ops
  # and gates dangerous ones, so the agent rarely hangs. Other modes (claude
  # --help): acceptEdits (auto-accept edits, gate the rest), plan (read-only),
  # bypassPermissions (no gates — trusted repos only).
  cmd: claude --permission-mode auto
  startup_delay_seconds: 4
  ready_timeout_seconds: 60

# hooks:                  # all optional — uncomment as needed
#   after_create: |       # runs once in a fresh worktree before the agent
#     mkdir -p .claude && cat > .claude/settings.local.json <<'JSON'
#     { "permissions": { "allow": ["Read","Edit","Bash(git *)"], "defaultMode": "default" } }
#     JSON
#   before_run: |         # runs in the worktree before the agent (lockfile sync, codegen)
#     uv sync --quiet || true
#   after_done: |         # runs in the repo dir when a PR merges (e.g. deploy)
#     echo "merged"
---

# Autonomous coding agent

You are an autonomous coding agent working on a single TODO item.

## Your task

> {{task_title}}

This run is **{{project_key}}-{{task_number}}** — use it as the PR-title prefix.
Task ID `{{task_id}}` (stable SHA the dispatcher uses for idempotency).

## Your workspace

A fresh git worktree at:

- Path: `{{workspace_path}}`
- Branch: `{{branch_name}}` (forked from `{{base_branch}}`)

Make your changes here, not in the main checkout.

## Done criteria — follow in order

1. **Implement the task.** Read the relevant code in the worktree, make the change.
2. **Validate.** Run the project's linter/tests if they exist; fix what you broke.
3. **Commit in the worktree.** Stage only the files you intentionally changed
   (`git add <paths>` — never `git add -A`). Confirm with `git status` and
   `git diff --cached --stat` before committing.
4. **Push and open a PR.** `git push -u origin {{branch_name}}` then
   `gh pr create --base {{base_branch}} --title "{{project_key}}-{{task_number}}: <summary>" --body ...`.
   Put the task ID `{{task_id}}` in the body so the run is traceable.
5. **Run `chela task-finished {{task_id}}` as your last step.** This marks the
   run `awaiting_review`, records the PR URL, and kills your tmux window.

**Do not touch the TODO file.** You never tick your own checkbox — the dispatcher
strikes it on `{{base_branch}}` once your PR actually **merges**, and it is that
file's only writer. That is what keeps your PR from conflicting with the items
being added to `{{base_branch}}` while you work.

## If you get stuck

Stop and say why, plainly, in your final message — name the blocker. Don't record
it in the TODO file, and don't open a half-done PR.

## Boundaries

- Do not edit the TODO file.
- Do not touch other worktrees under the workspace root.
- Do not push `{{base_branch}}`; only push your feature branch.
"""

_TODO_TEMPLATE = """# TODO

Each unchecked `- [ ]` bullet is a work item the dispatcher can pick up. It
spawns one agent in a git worktree per item; the agent opens a PR and strikes
its line. A `<!-- blocked: ... -->` marker makes the dispatcher skip a line.

## Open

- [ ] <first concrete, self-contained task>
- [ ] <second task>
"""


def project_key_for(name: str) -> str:
    """Short uppercase key (<=5 alnum chars) from a repo name; 'PROJ' fallback."""
    key = re.sub(r"[^A-Za-z0-9]", "", name).upper()[:5]
    return key or "PROJ"


def _slug_for(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return slug or "proj"


def seed_repo(path: str, base_branch: str = "main") -> dict:
    """Write WORKFLOW.md + TODO.md into ``path`` (never overwriting).

    Returns ``{ok, path, name, is_git, created: [...], skipped: [...]}``.
    Raises ValueError if ``path`` is missing or not a directory.
    """
    repo = Path(path).expanduser()
    try:
        repo = repo.resolve()
    except OSError as e:  # pragma: no cover - exotic FS errors
        raise ValueError(f"cannot resolve path: {e}") from e
    if not repo.is_dir():
        raise ValueError(f"not a directory: {repo}")

    name = repo.name
    workflow = (
        _WORKFLOW_TEMPLATE
        .replace("__PROJECT_KEY__", project_key_for(name))
        .replace("__SLUG__", _slug_for(name))
        .replace("__BASE_BRANCH__", base_branch)
    )

    created: list[str] = []
    skipped: list[str] = []
    for filename, content in (("WORKFLOW.md", workflow), ("TODO.md", _TODO_TEMPLATE)):
        target = repo / filename
        if target.exists():
            skipped.append(filename)
            continue
        target.write_text(content, encoding="utf-8")
        created.append(filename)

    return {
        "ok": True,
        "path": str(repo),
        "name": name,
        "is_git": (repo / ".git").exists(),
        "created": created,
        "skipped": skipped,
    }
