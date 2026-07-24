# Contributing to chelamux

Thanks for helping out. chelamux is a tmux-driven orchestrator for Claude Code
agents — much of its own development is done *by* dispatched agents working against
[`WORKFLOW.md`](WORKFLOW.md), so the contribution rules below are the same ones the
autonomous agents and the adversarial judge enforce.

## Dev environment

Requires **Python ≥ 3.11** and [`uv`](https://docs.astral.sh/uv/).

```bash
git clone https://github.com/Devail1/chelamux
cd chelamux
uv sync --all-extras          # see the note below — always --all-extras
```

> **Always `--all-extras`, never a named `--extra`.** The dashboard and telegram
> test suites false-fail on a default-only sync, and `uv sync --extra X` *drops* the
> other extras. A `uv run` in a fresh checkout also auto-syncs without extras — the
> "CMX-21 trap". If you run chela's live services from this checkout, a named-extra
> sync evicts the rest and crash-loops them.

## The loop

Before opening a PR, run exactly what CI runs:

```bash
uv run ruff check chela tests     # lint — CI gates on this
uv run pytest -q                  # tests (parallel via pytest-xdist: `-n 4 --dist loadfile`)
```

The JS suites (`*.test.mjs`) run inside pytest via `tests/test_js_suites.py`, so
`pytest` covers them too — no separate `npm test` step.

## Guard discipline (the one hard rule)

Every test that guards an invariant **must go RED when that invariant is corrupted.**
A guard that still passes after you break the thing it protects is decoration, and the
adversarial judge will reject the PR for it.

Concretely, when you add or change a guard, prove it fails:

- Temporarily break the code the guard protects (flip a branch, drop a `finally`,
  invert a condition) and confirm the test goes **RED**.
- Assert on the *behaviour/assertion*, not on a line number or an incidental string.
- Then restore the code. The guard is only trustworthy once you've watched it fail.

This is what the judge does automatically on every green PR: it corrupts each guard
in a throwaway worktree and re-runs the suite, blocking only on a guard that *survives*
its own corruption. Write guards that would catch you.

## Pull requests

- **Branch from `dev`, and target `dev`.** `main` is the promotion branch and is
  sacrosanct — never PR straight to it. Changes land `feature-branch → dev`, then a
  maintainer promotes `dev → main`.
- **Keep PRs focused** — one concern per PR is easier to review and to revert.
- **Update the [CHANGELOG](CHANGELOG.md).** Any user-facing change adds an entry under
  `## [Unreleased]` (Added / Changed / Fixed), with the PR number.
- **Update docs** when you change a `CHELA_*` knob, a command, or a hook — the README
  config table and `docs/` are adopter-facing and drift is user-visible.

## How the dispatcher builds tasks (optional context)

If you're curious how chela develops itself: the daemon reads unchecked `- [ ]` items
from a workflow's `TODO.md`, runs each as an isolated git-worktree agent seeded with
[`WORKFLOW.md`](WORKFLOW.md), has the judge adversarially review the PR, and strikes the
item on merge. Each task is a four-field brief — **OBJECTIVE / BOUNDARIES / GUARDS /
VERIFY** — precisely so the guard discipline above can be enforced mechanically. See
[`docs/`](docs/) (`EVENTS.md`, `HOOKS.md`, `RESOURCE_ISOLATION.md`) for the internals.

## Reporting security issues

Please follow [SECURITY.md](SECURITY.md) rather than opening a public issue.
