---
name: chela-setup
description: Install chela and wire its work-item dispatcher into a git repo — author a starter WORKFLOW.md + TODO.md so each `- [ ] task` becomes an agent → PR. Use when the user wants to set up chela, onboard a repo to chela, "init" a chela workflow, or get the dispatcher running.
---

# Set up chela on a repo

chela is a tmux-driven orchestrator for Claude Code agents. Its headline feature
is the **work-item dispatcher**: drop a `WORKFLOW.md` + `TODO.md` in a repo and
each unchecked `- [ ] task` becomes a git worktree → an agent that implements it
and opens a PR → an adversarial **judge** that corrupts each new guard → a merge
you approve. The dispatcher strikes the line when the PR merges.

This skill does two things: **install chela**, then **seed a starter dispatcher
config in the current repo**. Do them in order. Don't invent flags or fields —
everything below matches the real CLI. The human-paced version of the same path
is `docs/GETTING_STARTED.md` in the chela repo.

## 1. Check prerequisites

```bash
python3 --version   # need ≥ 3.11
tmux -V; git --version; uv --version; claude --version; gh --version
```

- `tmux`, `git`, [`uv`](https://docs.astral.sh/uv/), the `claude` CLI, and `gh`
  (for the PR flow) must be on `PATH`.
- The `claude` CLI must be **logged in already** (`claude` → `/login`, or
  `claude setup-token` for a headless token). chela does not manage credentials —
  every agent window reuses the cached `~/.claude` login, so the whole fleet runs
  as one Claude account sharing its rate limits. If it's not authenticated, tell
  the user to run `claude` / `/login` themselves; you can't do it for them.

## 2. Install chela

```bash
git clone https://github.com/Devail1/chelamux && cd chelamux
uv sync --all-extras         # core + dashboard + Telegram bridge
uv run chela status          # smoke test — lists agent windows in the tmux session
```

⚠️ `uv sync --extra X` *replaces* the environment: name every extra you want in one
command (`--extra dashboard --extra telegram`), or use `--all-extras`. All `chela`
invocations below assume `uv run chela …` from the checkout (or `chela …` on `PATH`).

Then the one-time wiring:

```bash
mkdir -p ~/.chela && cp examples/chela.env ~/.chela/chela.env   # the ONE config file; edit it
chela doctor                   # checks the running config against that file (exit 1 = broken)
chela install-statusline --write   # exact context / rate-limit numbers for the dashboard
```

- **The hooks plugin** (recommended — blocked-agent handling, answering from your phone,
  and the merge gate that denies `gh pr merge`). Inside Claude Code:
  `/plugin marketplace add Devail1/chelamux`, then `/plugin install chela@chela`.
  Sessions load hooks only when they start.
- **Secrets never go in `chela.env`.** Put tokens in `~/.chela/secrets.env` (`chmod 600`);
  see `docs/CONFIG.md`.
- **The daemon is the engine.** `chela run` runs the scheduler, the dispatcher, the judge
  and the needs-input scan. For anything long-lived, run it under a process manager with
  `examples/ecosystem.config.js` (every app starts through `scripts/run-chela.sh`, which
  sources `chela.env` and `secrets.env`).

## 3. Seed the dispatcher config in the target repo

Work in the **root of the repo the user wants chela to work on** (a git repo with
a clean default branch and a GitHub remote, since the agent opens PRs).

Copy the canonical templates from the chela checkout — they are the source of truth:

```bash
cp /path/to/chelamux/examples/WORKFLOW.md /path/to/chelamux/examples/TODO.md ./
```

(The dashboard's **Init a repo** button seeds the same pair and never overwrites.)
Then set `project_key`, `workspace.root` and `workspace.base_branch` in the
frontmatter. For `tracker.kind: gh_issues`, `require_label:` is **required** — it is
the security gate between "anyone can open an issue" and "code runs on this machine".

If the checkout isn't handy, this is the minimum `WORKFLOW.md`. The frontmatter is the
config; the markdown body below `---` is the prompt every dispatched agent receives:

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
4. Request the push and PR — do NOT run `git push` or `gh pr create` yourself (masking
   the GitHub token keeps `gh` working but not `git push`, which sends it Basic-encoded
   rather than verbatim). Write the PR body to a file, then run:
   `chela request-push {{task_id}} --pr-title "{{project_key}}-{{task_number}}: <summary>" --pr-body-file <path>`
   (put `{{task_id}}` in the body).
5. Run `chela task-finished {{task_id}}` as your last step — marks the run
   `awaiting_review`, records the PR URL, and kills your tmux window.

**Do not touch the TODO file.** You never tick your own checkbox — the dispatcher
strikes it on `{{base_branch}}` once your PR actually merges, and it is that file's
only writer. That is what keeps your PR from conflicting with the items added to
`{{base_branch}}` while you work.

## If you get stuck

Stop and say why in your final message — name the blocker. Don't record it in the
TODO file, and don't open a half-done PR.

## Boundaries

- Don't edit the TODO file, touch other worktrees, or push `{{base_branch}}`.
```


Then create `TODO.md` at the repo root. Each unchecked `- [ ]` bullet is one work
item. Its **first line is the title and the task id**, so never edit a claimed task's
first line. Write the four-field brief the judge enforces:

```markdown
# TODO

## Open

- [ ] **Add a --version flag to the CLI.** <!-- risk: low -->
  **OBJECTIVE.** Print the package version and exit 0.
  **BOUNDARIES.** The CLI entrypoint + its test only.
  **GUARDS.** A test asserting `--version` prints the version; break the flag → RED.
  **VERIFY.** `mytool --version` prints the version.
```

Markers on the first line: `<!-- risk: high|normal|low -->` (sizes the judge's battery
and the rework cap — `docs/RISK_LEVELS.md`), `<!-- blocked: why -->` (skipped), and
`<!-- depends: "other task title" -->` (held until that task is struck).

**Seed `TODO.md` with real tasks for *this* repo.** Skim the codebase (README,
open issues, obvious gaps) and propose 3–5 small, independent, well-scoped items —
each should be completable by one agent in one PR without coordinating with the
others. Confirm the list with the user before kicking off a run.

## 4. Run the dispatcher

```bash
# dry run first — see what it would pick up without spawning anything
chela dispatch ./WORKFLOW.md --dry-run

chela dispatch ./WORKFLOW.md --once     # one pass
```

For the real thing, add the workflow to the daemon: set
`CHELA_DISPATCH_WORKFLOWS=/abs/path/to/WORKFLOW.md` in `~/.chela/chela.env` and
(re)start `chela run`.

Inspect runs with `chela dispatch-runs`. `chela dashboard` shows the fleet and the Work
board of the same run state, plus the live terminal wall.

## 5. Review and merge

**Auto-merge is off by default.** When a PR opens, the judge corrupts each new guard in a
throwaway worktree and posts a verdict on the PR. Then:

- `chela merge <run>` merges only when the judge is clean on the PR's current head, CI is
  green and the PR is mergeable, into the workflow's `base_branch`.
- `chela review <run> --request-changes --body-file verdict.md` sends it back to the agent.

The `orchestrate` skill covers the full review loop.

## Notes

- For **scheduled, long-lived agents** (a researcher poked every hour, etc.) —
  distinct from these ephemeral one-task-per-worktree dispatch agents — use a
  standing `CLAUDE.md` per agent and `chela schedule add <agent> --every 1h
  --prompt "..."`. See `examples/agent-template.md` in the chela repo.
- Canonical reference: the chela `README.md`, `docs/`, and `examples/`.
