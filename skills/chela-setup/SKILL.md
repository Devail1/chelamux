---
name: chela-setup
description: Install chela and wire its work-item dispatcher into a git repo — author a starter WORKFLOW.md + TODO.md so each `- [ ] task` becomes an agent → PR. Use when the user wants to set up chela, onboard a repo to chela, "init" a chela workflow, or get the dispatcher running.
---

# Set up chela on a repo

chela is a tmux-driven orchestrator for Claude Code agents. Its headline feature
is the **work-item dispatcher**: drop a `WORKFLOW.md` + `TODO.md` in a repo and
each unchecked `- [ ] task` becomes a git worktree → an agent that implements it,
strikes the line, and opens a PR → a run that flips to `done` when you merge.

This skill does two things: **install chela**, then **seed a starter dispatcher
config in the current repo**. Do them in order. Don't invent flags or fields —
everything below matches the real CLI.

## 1. Check prerequisites

```bash
python3 --version   # need ≥ 3.11
tmux -V; git --version; claude --version; gh --version
```

- `tmux`, `git`, the `claude` CLI, and `gh` (for the PR flow) must be on `PATH`.
- The `claude` CLI must be **logged in already** (`claude` → `/login`, or
  `claude setup-token` for a headless token). chela does not manage credentials —
  every agent window reuses the cached `~/.claude` login, so the whole fleet runs
  as one Claude account sharing its rate limits. If it's not authenticated, tell
  the user to run `claude` / `/login` themselves; you can't do it for them.

## 2. Install chela

If you have the chela source checked out:

```bash
uv sync                      # core
uv sync --extra dashboard    # optional: web dashboard + live terminal wall
uv run chela status          # smoke test — lists agent windows in the tmux session
```

If chela isn't present, clone it first (https://github.com/Devail1/chelamux), then
`uv sync` as above. All `chela` invocations below assume `uv run chela …` from
the chela checkout (or `chela …` if installed on `PATH`).

## 3. Seed the dispatcher config in the target repo

Work in the **root of the repo the user wants chela to work on** (a git repo with
a clean `main`/default branch and a GitHub remote, since the agent opens PRs).

If the chela checkout is handy, copy the canonical templates instead of writing
from scratch:

```bash
cp /path/to/chela/examples/WORKFLOW.md /path/to/chela/examples/TODO.md ./
```

Otherwise create `WORKFLOW.md` at the repo root. The frontmatter is the config;
the markdown body below `---` is the prompt every dispatched agent receives:

```markdown
---
project_key: PROJ            # short uppercase key; branches/windows are <key>-<n>
tracker:
  kind: markdown             # markdown TODO.md (also supports: gh_issues)
  path: TODO.md              # relative to this file
workspace:
  root: ~/.chela/worktrees/proj   # where per-task git worktrees are created
  base_branch: main          # branch worktrees fork from and PRs target
concurrency:
  max: 1                     # how many tasks may be in flight at once
agent:
  cmd: claude --permission-mode auto   # safe default: auto-approves safe ops,
                                        # gates dangerous ones. Use
                                        # bypassPermissions only on a fully
                                        # trusted repo for zero-hang autonomy.
  startup_delay_seconds: 4
  ready_timeout_seconds: 60
# hooks:                     # all optional — uncomment as needed
#   after_create: |          # runs once in a fresh worktree before the agent
#     mkdir -p .claude && cat > .claude/settings.local.json <<'JSON'
#     { "permissions": { "allow": ["Read","Edit","Bash(git *)"], "defaultMode": "default" } }
#     JSON
#   before_run: |            # runs in the worktree before the agent (lockfile sync, codegen)
#     uv sync --quiet || true
#   after_done: |            # runs in the repo dir when a PR merges (e.g. deploy)
#     echo "merged"
---

# Autonomous coding agent

You are an autonomous coding agent working on a single TODO item.

## Your task

> {{task_title}}

This run is **{{project_key}}-{{task_number}}** — use it as the PR-title prefix.
Task ID `{{task_id}}`.

## Workspace

A fresh git worktree at `{{workspace_path}}` on branch `{{branch_name}}` (forked
from `{{base_branch}}`). Make changes here, not in the main checkout.

## Done criteria — in order

1. Implement the task: read the relevant code in the worktree, make the change.
2. Validate: run the project's linter/tests if they exist; fix what you broke.
3. Commit in the worktree. Stage only files you intentionally changed
   (`git add <paths>` — never `git add -A`).
4. Strike the TODO line in your branch: in `{{task_file_relative}}`, change
   `- [ ] {{task_title}}` to `- [x] {{task_title}}` and include it in your commit.
   It lands on `{{base_branch}}` only when the PR merges.
5. Push and open a PR: `git push -u origin {{branch_name}}` then
   `gh pr create --base {{base_branch}} --title "{{project_key}}-{{task_number}}: <summary>" --body ...`
   (put `{{task_id}}` in the body).
6. Run `chela task-finished {{task_id}}` as your last step — marks the run
   `awaiting_review`, records the PR URL, and kills your tmux window.

## If you get stuck

Append `<!-- blocked: <reason> -->` to the TODO line, commit it, and stop. The
next poll skips that task.

## Boundaries

- Don't modify other TODO lines, touch other worktrees, or push `{{base_branch}}`.
```

Then create `TODO.md` at the repo root. Each unchecked `- [ ]` bullet is one work
item; a `<!-- blocked: ... -->` marker makes the dispatcher skip a line:

```markdown
# TODO

## Open

- [ ] <first concrete, self-contained task>
- [ ] <second task>
```

**Seed `TODO.md` with real tasks for *this* repo.** Skim the codebase (README,
open issues, obvious gaps) and propose 3–5 small, independent, well-scoped items —
each should be completable by one agent in one PR without coordinating with the
others. Confirm the list with the user before kicking off a run.

## 4. Run the dispatcher

```bash
# dry run first — see what it would pick up without spawning anything
chela dispatch ./WORKFLOW.md --dry-run

chela dispatch ./WORKFLOW.md --once     # one pass
chela dispatch ./WORKFLOW.md            # poll forever
```

Or fold it into the daemon: `export CHELA_DISPATCH_WORKFLOWS=$PWD/WORKFLOW.md`
then `chela run` (scheduler tick + dispatcher + needs-input notify in one loop).

Inspect runs with `chela dispatch-runs`. If the dashboard extra is installed,
`chela dashboard` shows the Dispatcher (per-workflow) and Kanban (cross-workflow
board) views of the same run state, plus the live terminal wall.

## Notes

- For **scheduled, long-lived agents** (a researcher poked every hour, etc.) —
  distinct from these ephemeral one-task-per-worktree dispatch agents — use a
  standing `CLAUDE.md` per agent and `chela schedule add <agent> --every 1h
  --prompt "..."`. See `examples/agent-template.md` in the chela repo.
- Canonical reference: the chela `README.md` and `examples/` directory.
