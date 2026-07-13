---
# chela work-item dispatcher — chelamux dogfooding its own dispatcher.
# Run:  chela dispatch ./WORKFLOW.md --dry-run   (preview)
#       chela dispatch ./WORKFLOW.md --once      (one pass)

# This file is HOT-RELOADED: the daemon re-reads it when it changes and applies
# the new config (and prompt body) from the next tick — no restart. If an edit
# leaves it unparseable, the daemon keeps running on the last known-good config,
# keeps reconciling, and pauses NEW dispatches until it parses again (the error
# shows up in the dashboard's Settings drawer).

project_key: CMX

tracker:
  kind: markdown
  path: TODO.md

# polling:
#   interval_ms: 60000      # seconds between dispatcher ticks for this workflow.
#                           # Unset → CHELA_DISPATCH_TICK_INTERVAL (60s). Floor: 5s.

workspace:
  root: ~/.chela/worktrees/chelamux
  base_branch: dev          # active branch — worktrees fork from dev, PRs target dev

concurrency:
  max: 1                    # pilot: one task in flight

agent:
  # No `cmd:` here on purpose. An explicit agent.cmd is an authoritative
  # per-workflow override that SHADOWS the permission mode set in the dashboard's
  # Settings drawer (precedence: agent.cmd → Settings mode → the built-in
  # `claude --permission-mode auto`; see dispatcher.resolve_agent_cmd). Leaving it
  # unset is what makes the Settings control reachable for this workflow. Set it
  # only to pin a workflow to a command regardless of Settings.
  startup_delay_seconds: 4
  ready_timeout_seconds: 60

hooks:
  # Sync the per-worktree venv with ALL extras before the agent starts, so
  # dashboard/telegram tests don't false-fail on a default-only sync (a `uv run`
  # in a fresh worktree auto-syncs without extras — the CMX-21 trap). `--extra X`
  # DROPS other extras, so it must be `--all-extras`.
  before_run: uv sync --all-extras --quiet
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
4. **Push and open a PR.** `git push -u origin {{branch_name}}` then
   `gh pr create --base {{base_branch}} --title "{{project_key}}-{{task_number}}: <summary>" --body ...`
   (put `{{task_id}}` in the body).
5. **Run `chela task-finished {{task_id}}` as your last step** — marks the run
   `awaiting_review`, records the PR URL, and kills your tmux window.

**Do NOT touch the tracker file.** You do not tick your own checkbox — the dispatcher
strikes it on `{{base_branch}}` once your PR actually **merges**. It is the file's only
writer, on purpose: when agents struck their own line in their branch while the
orchestrator kept appending items to `{{base_branch}}`, every dispatched PR conflicted
on it. Leave it alone and your PR merges clean.

## Public-repo boundaries (load-bearing — this is a public MIT repo)

- **No secrets or private data in committed code**: no real tokens, chat ids, absolute
  `/home/<user>` paths, or private project/host names. Config must be env-driven; use
  generic placeholders in docs/examples.
- Don't edit the tracker file, touch other worktrees, or push `{{base_branch}}`.

## If you get stuck

Stop and say why, plainly, in your final message — name the blocker. Don't edit the
tracker to record it (that file is the dispatcher's) and don't open a half-done PR. A
human picks it up from there; the dispatcher gives a task a bounded number of attempts,
so a genuinely blocked task stops being retried rather than spinning.
