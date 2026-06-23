---
# Example chela work-item dispatcher workflow.
#
# Copy this file (and TODO.md) into the root of a git repo you want chela to
# work on, then point chela at it:
#
#   chela dispatch /path/to/your/repo/WORKFLOW.md --once     # one pass
#   chela dispatch /path/to/your/repo/WORKFLOW.md            # poll forever
#
# Or run it inside the daemon by exporting:
#   CHELA_DISPATCH_WORKFLOWS=/path/to/your/repo/WORKFLOW.md
# then `chela run`.

# Short uppercase key (2-5 letters). Branches/windows are <key>-<n>, e.g. proj-1.
project_key: PROJ

tracker:
  kind: markdown          # markdown TODO.md (also: gh_issues)
  path: TODO.md           # relative to this file

workspace:
  # Where per-task git worktrees are created. ~ and $VARS expand.
  root: ~/.chela/worktrees/proj
  base_branch: main       # branch worktrees fork from and PRs target

concurrency:
  max: 1                  # how many tasks may be in flight at once

agent:
  # Default is `claude --permission-mode auto`: a classifier auto-approves safe
  # ops and gates dangerous ones, so the agent rarely hangs on a prompt. Other
  # modes (claude --help): `acceptEdits` (auto-accept edits, gate the rest),
  # `plan` (read-only), `bypassPermissions` (no gates — zero-hang autonomy on a
  # repo you fully trust). E.g. cmd: claude --permission-mode acceptEdits
  cmd: claude --permission-mode auto
  startup_delay_seconds: 4    # minimum wait before polling the pane for readiness
  ready_timeout_seconds: 60   # cap on the readiness poll, then send anyway

hooks:
  # Optional. Runs ONCE in a freshly-created worktree, before the agent starts.
  # The canonical use is seeding a least-privilege .claude/settings.local.json
  # so the agent launches with pre-approved permissions. Failure aborts dispatch.
  # after_create: |
  #   mkdir -p .claude && cat > .claude/settings.local.json <<'JSON'
  #   { "permissions": { "allow": ["Read", "Edit", "Bash(git *)"], "defaultMode": "default" } }
  #   JSON
  #
  # Optional. Runs in the worktree before the agent starts — lockfile sync, codegen.
  # before_run: |
  #   uv sync --quiet || true
  #
  # Optional. Runs in the repo dir (detached) when a PR merges (a run goes
  # awaiting_review -> done). Customize per project — e.g. a deploy.
  # after_done: |
  #   echo "merged $(date)"
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
4. **Strike the TODO line in your branch.** In the worktree, edit
   `{{task_file_relative}}` and change `- [ ] {{task_title}}` to
   `- [x] {{task_title}}`. Include this in your commit. It lands on
   `{{base_branch}}` only when the PR merges — never commit it there directly.
5. **Push and open a PR.** `git push -u origin {{branch_name}}` then
   `gh pr create --base {{base_branch}} --title "{{project_key}}-{{task_number}}: <summary>" --body ...`.
   Put the task ID `{{task_id}}` in the body so the run is traceable.
6. **Run `chela task-finished {{task_id}}` as your last step.** This marks the
   run `awaiting_review`, records the PR URL, and kills your tmux window. chela
   owns the window lifecycle — you don't need to exit manually. When the PR
   merges, the TODO line disappears from `{{base_branch}}` and the run flips to
   `done` on the next tick.

## If you get stuck

Append `<!-- blocked: <reason> -->` to the TODO line and commit it. The next
poll will skip this task. Then stop.

## Boundaries

- Do not modify other TODO lines.
- Do not touch other worktrees under the workspace root.
- Do not push `{{base_branch}}`; only push your feature branch.
