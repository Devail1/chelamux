---
# chela work-item dispatcher — chelamux dogfooding its own dispatcher.
# Run:  chela dispatch ./WORKFLOW.md --dry-run   (preview)
#       chela dispatch ./WORKFLOW.md --once      (one pass)

project_key: CMX

tracker:
  kind: markdown
  path: TODO.md

workspace:
  root: ~/.chela/worktrees/chelamux
  base_branch: dev          # active branch — worktrees fork from dev, PRs target dev

concurrency:
  max: 1                    # pilot: one task in flight

agent:
  cmd: claude --permission-mode auto
  startup_delay_seconds: 4
  ready_timeout_seconds: 60
---

# Autonomous coding agent — chelamux

You are an autonomous coding agent working on a single TODO item in the **chelamux**
repo (a public MIT project: a tmux-driven orchestrator for Claude Code agents).

## Your task

> {{task_title}}

This run is **{{project_key}}-{{task_number}}** — use it as the PR-title prefix.
Task ID `{{task_id}}`.

## Your workspace

A fresh git worktree at `{{workspace_path}}` on branch `{{branch_name}}` (forked from
`{{base_branch}}`). Make your changes here, not in the main checkout.

## Done criteria — follow in order

1. **Implement the task.** Read the relevant code in the worktree first. For skills,
   mirror the style of the existing `skills/chela-setup`, `skills/orchestrate`, and
   `skills/telegram-send` skills.
2. **Validate — this repo's CI gates on ruff.** Run `uv run ruff check chela tests`
   (MUST pass — pytest-green is NOT sufficient) and `uv run pytest -q`; fix what you broke.
3. **Commit in the worktree.** Stage only files you intentionally changed
   (`git add <paths>` — never `git add -A`). Verify with `git status` +
   `git diff --cached --stat` before committing.
4. **Strike the TODO line in your branch.** In `{{task_file_relative}}`, change
   `- [ ] {{task_title}}` to `- [x] {{task_title}}` and include it in your commit. It
   lands on `{{base_branch}}` only when the PR merges — never commit it there directly.
5. **Push and open a PR.** `git push -u origin {{branch_name}}` then
   `gh pr create --base {{base_branch}} --title "{{project_key}}-{{task_number}}: <summary>" --body ...`
   (put `{{task_id}}` in the body).
6. **Run `chela task-finished {{task_id}}` as your last step** — marks the run
   `awaiting_review`, records the PR URL, and kills your tmux window.

## Public-repo boundaries (load-bearing — this is a public MIT repo)

- **No secrets or private data in committed code**: no real tokens, chat ids, absolute
  `/home/<user>` paths, or private project/host names. Config must be env-driven; use
  generic placeholders in docs/examples.
- Don't modify other TODO lines, touch other worktrees, or push `{{base_branch}}`.

## If you get stuck

Append `<!-- blocked: <reason> -->` to the TODO line, commit it, and stop.
