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
- **If your PR ships what a design doc in `docs/` proposed, update that doc's status line
  in the same PR.** Every *design/scope* doc carries one in its header (see
  `docs/wall-redesign.md`, `docs/OKF.md`, `docs/AGENT_IDENTITY.md`) — `SHIPPED <date> (<PR>)`,
  `PARTLY SHIPPED`, `SUPERSEDED by <doc>`, or `CLOSED — won't build` with the reason.
  Reference docs (`CONFIG.md`, `HOOKS.md`, `EVENTS.md`, …) describe current behaviour and
  deliberately carry **no** status line — a status on a living reference is just one more
  thing to go stale.

  Design docs are the ones that rot, because nothing forces a correction: a wrong value in
  `CONFIG.md` gets fixed the first time it bites someone, while a design doc that says "not
  yet implemented" about a shipped feature can sit for months. `docs/OKF.md` said exactly
  that for 23 days after OKF landed.
- **Any change to the rendered hooks (`hooks_spec`) MUST bump `plugin/.claude-plugin/plugin.json`
  version** — Claude Code keys plugin updates on the version, so a hook change without a
  bump ships stale hooks to every adopter.

## Releasing (maintainer-only)

The [CHANGELOG](CHANGELOG.md) is the single source of a release's notes — a tag's
GitHub Release body is that release's CHANGELOG section, pasted in verbatim, never
hand-authored a second time. `pyproject.toml`'s `version` field is the only place
the release number itself is written; `chela.__version__` derives from it at
runtime (`importlib.metadata`, see `chela/__init__.py`), so there is nothing else
to keep in sync.

To cut a release, on `main` after a `dev → main` promotion:

1. Turn `## [Unreleased]` into `## [X.Y.Z] — YYYY-MM-DD` in `CHANGELOG.md`.
2. Bump `version` in `pyproject.toml` to match `X.Y.Z`.
3. Commit both together (`Release X.Y.Z — <one-line theme>`).
4. `git tag -a vX.Y.Z -m "X.Y.Z — <one-line theme>" && git push origin vX.Y.Z`
5. `gh release create vX.Y.Z --title vX.Y.Z --notes-file -` piping in that
   CHANGELOG section's body (heading excluded) — so the release notes and the
   changelog can never drift apart.

## How the dispatcher builds tasks (optional context)

If you're curious how chela develops itself: the daemon reads unchecked `- [ ]` items
from a workflow's `TODO.md`, runs each as an isolated git-worktree agent seeded with
[`WORKFLOW.md`](WORKFLOW.md), has the judge adversarially review the PR, and strikes the
item on merge. Each task is a four-field brief — **OBJECTIVE / BOUNDARIES / GUARDS /
VERIFY** — precisely so the guard discipline above can be enforced mechanically. See
[`docs/`](docs/) (`EVENTS.md`, `HOOKS.md`, `RESOURCE_ISOLATION.md`) for the internals.

## Reporting security issues

Please follow [SECURITY.md](SECURITY.md) rather than opening a public issue.
