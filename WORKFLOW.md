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
  max: 2                    # 2 in flight — light dashboard/JS coding agents. NOTE: the
                            # dispatcher's active-count is GLOBAL across workflows, so this
                            # also caps total box-wide agents; lean-alpha stays at max:1 so
                            # two heavy LEAN backtests never run in parallel (memcap/OOM).

agent:
  # No `cmd:` here on purpose. An explicit agent.cmd is an authoritative
  # per-workflow override that SHADOWS the permission mode set in the dashboard's
  # Settings drawer (precedence: agent.cmd → Settings mode → the built-in
  # `claude --permission-mode auto`; see dispatcher.resolve_agent_cmd). Leaving it
  # unset is what makes the Settings control reachable for this workflow. Set it
  # only to pin a workflow to a command regardless of Settings.
  startup_delay_seconds: 4
  ready_timeout_seconds: 60

# ⚖️ The judge — the adversarial pass CI cannot run (chela/judge.py).
#
# A PR reaching awaiting_review gets ONE judge per head commit. It works in a THROWAWAY
# detached worktree, proposes mutations to the guards the PR claims to add, and chela — not
# the agent — applies each one, proves the file changed, proves it still parses, re-runs
# `test_cmd`, and restores it. A guard that survives a live, minimal, valid corruption is a
# FACT: the PR goes back through the same carrier a human reviewer uses, and it spends a
# rework round. Opinions can only ever become a PR comment.
#
# ⚠️ CHELA_REQUIRE_JS_TESTS=1 IS LOAD-BEARING, NOT DECORATION. Without it a missing `node`
# or a missing `npm ci` makes the .mjs suites SKIP — silently, and green. The judge would
# then mutate `terminals.js`, watch the suite pass, and send a GOOD PR back on the strength
# of a suite that never ran. A judge is only ever as trustworthy as the suite it measures
# against, so the suite must be the one that CANNOT quietly do nothing.
#
# `enabled: false` turns it off for this workflow; CHELA_JUDGE=0 turns it off fleet-wide.
judge:
  test_cmd: CHELA_REQUIRE_JS_TESTS=1 uv run pytest -q
  suite_timeout_seconds: 900

hooks:
  # ⛔ THIS HOOK IS THE JUDGE'S ENVIRONMENT TOO, not just the agent's — `_launch_agent` runs
  # it on every launch, and a judge worktree is a launch. It therefore has to build an
  # environment in which `judge.test_cmd` above can be GREEN. It did not, and the cost was
  # total: from the day the judge shipped (2026-07-15) it returned CANNOT VERIFY on every
  # single PR, because this line synced the venv and never installed jsdom, so the two
  # real-DOM suites FAILED under CHELA_REQUIRE_JS_TESTS=1 and the baseline was red before a
  # single mutation was applied. A judge whose baseline can never be green judges nothing.
  # (`tests/test_judge_env.py` is the guard: it fails if either half of that contract —
  # the env var above, the install below — goes away again.)
  #
  # ⛔ AND IT CANNOT BE THE JUDGE'S ONLY ENVIRONMENT — read this before "simplifying"
  # `judge.provision_suite_env` away as a duplicate of the line below. The dispatcher runs
  # hooks out of the WORKFLOW.md it LOADED (`runs.workflow_path`: the repo root, default
  # branch), NEVER the copy on the PR branch it is judging. So this line only reaches a
  # judge AFTER it is merged, and the first attempt at CMX-80 — which fixed only this line —
  # was judged by the old hook and reported CANNOT VERIFY on itself. A config fix cannot fix
  # what runs before it merges; `provision_suite_env` (in the judged tree, so it runs the
  # moment it is pushed) is the half that can. Both halves stay: this one so AGENTS get a
  # working worktree, that one so the JUDGE never depends on this one being right.
  #
  # `uv sync --all-extras`: dashboard/telegram tests false-fail on a default-only sync (a
  # `uv run` in a fresh worktree auto-syncs WITHOUT extras — the CMX-21 trap), and
  # `--extra X` DROPS the other extras, so it must be `--all-extras`.
  # `scripts/npm-shared-install.sh`: installs jsdom, the repo's one npm dep (dev-only,
  # nothing is bundled or shipped) — what CI installs, in the same breath, for the same
  # reason, but via ONE shared node_modules symlinked into every worktree rather than a
  # fresh `npm ci` copying 27M into each (CMX-151: unlike `uv sync`, which hardlinks from
  # its own cache, `npm ci` always unpacks real files, and N concurrent worktrees were
  # paying for N identical copies of the same dep).
  #
  # ⚠️ Docker-based builds: run the container as your own uid or the worktree becomes
  # UNRECLAIMABLE. A step like `docker run ... build` writes root-owned files into the
  # worktree; chela runs as your user, so both `git worktree remove` and `rm -rf` then
  # fail with EPERM and the worktree can never be freed (CMX-164's "mode 4" orphan —
  # `remove_worktree` will log a loud WARNING and give up rather than half-delete it).
  # Always pass `--user $(id -u):$(id -g)` (and mount an outside-the-worktree cache).
  #
  # Heavy ecosystems: point the build cache at ONE shared location instead of N per-worktree
  # copies — `CARGO_TARGET_DIR`, a pnpm store, `CCACHE_DIR` — the generalisation of the
  # shared node_modules above. A per-worktree `target/`/`node_modules` is what fills the
  # disk (see `CHELA_WORKTREE_DISK_BUDGET`).
  before_run: uv sync --all-extras --quiet && scripts/npm-shared-install.sh
---

# Autonomous coding agent — chelamux

You are an autonomous coding agent working on a single TODO item in the **chelamux**
repo (a public AGPL-3.0-or-later project: a tmux-driven orchestrator for Claude Code agents).

## Your task

> {{task_title}}

This run is **{{project_key}}-{{task_number}}** — use it as the PR-title prefix.
Task ID `{{task_id}}`.

## Your workspace

A fresh git worktree at `{{workspace_path}}` on branch `{{branch_name}}` (forked from
`{{base_branch}}`). Make your changes here, not in the main checkout.

## You are unattended — decide, don't ask

No human is watching this session. **⛔ Do NOT call `AskUserQuestion`** — there is no one
to answer it and the dispatcher will find you hung. When you hit a genuine choice, pick the
most reasonable default, note the assumption in your PR body, and proceed. Run tests and
commands **synchronously** and wait for them — never park work on a prompt for a human. The
only sanctioned stop is a real blocker (see "If you get stuck" below), stated in your final
message.

## Done criteria — follow in order

1. **Implement the task.** Read the relevant code in the worktree first. For skills,
   mirror the style of the existing `skills/chela-setup`, `skills/orchestrate`, and
   `skills/telegram-send` skills.
2. **Validate — this repo's CI gates on ruff.** Run `uv run ruff check chela tests`
   (MUST pass — pytest-green is NOT sufficient) and `uv run pytest -q`; fix what you broke.
3. **⚖️ SELF-VERIFY YOUR GUARDS — corrupt each one and watch it go RED.** For every test or
   guard you added or changed, deliberately break the invariant it *claims* to protect (flip its
   condition, empty its returned value, delete the production call-site it wires) and re-run the
   suite. ⛔ **A guard that stays GREEN under its own corruption is DECORATION — it asserts
   something other than what it claims; fix it before you hand off.** Confirm each mutation
   actually applied (the file changed) *and still parses* (`node --check` / `py_compile`), then
   revert it. **This is the exact check the judge will run — catch your own decoration first, or
   the PR comes straight back.**
4. **Commit in the worktree.** Stage only files you intentionally changed
   (`git add <paths>` — never `git add -A`). Verify with `git status` +
   `git diff --cached --stat` before committing.
5. **Push and open a PR.** `git push -u origin {{branch_name}}` then
   `gh pr create --base {{base_branch}} --title "{{project_key}}-{{task_number}}: <summary>" --body ...`
   (put `{{task_id}}` in the body).
6. **Run `chela task-finished {{task_id}}` as your last step** — marks the run
   `awaiting_review`, records the PR URL, and kills your tmux window.

**Do NOT touch the tracker file.** You do not tick your own checkbox — the dispatcher
strikes it on `{{base_branch}}` once your PR actually **merges**. It is the file's only
writer, on purpose: when agents struck their own line in their branch while the
orchestrator kept appending items to `{{base_branch}}`, every dispatched PR conflicted
on it. Leave it alone and your PR merges clean.

## Public-repo boundaries (load-bearing — this is a public AGPL-3.0-or-later repo)

- **No secrets or private data in committed code**: no real tokens, chat ids, absolute
  `/home/<user>` paths, or private project/host names. Config must be env-driven; use
  generic placeholders in docs/examples.
- Don't edit the tracker file, touch other worktrees, or push `{{base_branch}}`.

## If you get stuck

Stop and say why, plainly, in your final message — name the blocker. Don't edit the
tracker to record it (that file is the dispatcher's) and don't open a half-done PR. A
human picks it up from there; the dispatcher gives a task a bounded number of attempts,
so a genuinely blocked task stops being retried rather than spinning.
