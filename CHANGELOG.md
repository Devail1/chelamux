# Changelog

All notable changes to chelamux are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project aims to follow [Semantic Versioning](https://semver.org/).

Tracking starts at the open-source launch (2026-07-21). Earlier development
history lives in `git log`.

## [Unreleased]

### Added

- **`docs/GETTING_STARTED.md`** — a clone-to-first-dispatched-agent quickstart.
- **`CODE_OF_CONDUCT.md`** (Contributor Covenant 2.1) and GitHub issue/PR templates.

### Changed

- **Docs now recommend the hooks plugin.** README and the landing docs make clear
  the event-log plugin (`chela plugin`) is strongly recommended — it unlocks
  lossless blocked-agent gates on Telegram, zero-keystroke answers, and the live
  Feed — and the statusLine hook is reframed as recommended for exact usage numbers.

## [0.2.0] — 2026-07-23

The first tracked release: chelamux went public (MIT) and hardened through a
macOS-onboarding and fleet-safety push.

### Added

- **macOS support.** A POSIX `/proc` fallback for the process/transcript facts,
  so window attribution and `chela read` work off Linux (#183), plus a `$CHELA_WID`
  header relay for rename-proof window attribution (#181).
- **Self-update.** `chela update` command with an update-available notifier (#159),
  and an opt-in `CHELA_AUTO_UPDATE` for unattended self-update sweeps (#164).
- **`CHELA_AUTO_MERGE`** — strictly opt-in, off-by-default unattended merge sweep (#154).
- **`CHELA_WORKTREE_DISK_BUDGET`** — a disk-budget rail (the `memcap` analog for
  disk) that refuses to claim a task when a workflow's worktree root exceeds the
  ceiling (#194).
- **Telegram.** Pinned per-topic session titles, edited in place (#191); distinct
  topic names for same-cwd sessions (#163); `/compact` passthrough in the `/` menu (#149).
- **Orchestration.** A dispatcher-owned trial ledger for honest N under fan-out (#119);
  orchestrator subscribe with a durable decisions log and sidebar section (#120, #121).
- **Wall UX.** A palette-first pane switcher (Ctrl+K) (#130); a keyboard-shortcut
  cheatsheet (#135); Claude's auto-generated session title in the pane title bar and
  sidebar (#162).

### Changed

- **CI is ~30× faster** — parallelized with `pytest-xdist` and dropped the
  seed-confirm sleep (~28 min → ~1 min), capped at `-n 4` for the dev box (#188, #189).
- **Worktrees self-reap** — a run's worktree is freed at `done` (#150), judge
  (`judge-*`) worktrees are reaped, and `remove_worktree` survives all four orphan
  modes including root-owned Docker remnants (#194).
- **One shared `node_modules`** install across worktrees instead of N copies (#151).
- **`MALLOC_ARENA_MAX=2`** in `scripts/run-chela.sh` to bound glibc arena bloat (#193).

### Fixed

- **TUI glyph tofu** (`⏺ ❌ ✅`, the working spinner, Nerd icons) across every render
  surface: `/screenshot` (#175), the collab-relay viewer (#177), the dashboard wall
  (#180), and the ttyd tmux client via `-u` (#185).
- **Dispatcher** no longer false-fails working agents, and AskUserQuestion stalls are
  auto-recovered (#187); a task whose PR merged out-of-band is never re-dispatched
  (#157); reconcile no longer false-`done`s sibling-workflow runs (#115).
- **Dispatch startup race** — MCP is isolated for dispatched agents and the judge (#146),
  and seed submit re-sends Enter instead of re-pasting (#147).
- **Gate-watcher** unpaired-`tool_use` backlog is now bounded (#193).
- **macOS** Alt+N pane-jump (Option composes characters, not digits) (#166); pane-header
  emoji rendered as SVG to kill tofu (#171).

---

chelamux does not tag releases yet; `0.2.0` is the current `pyproject` version and
this entry covers the public-launch arc. Add a new entry per user-facing PR
(see [CONTRIBUTING.md](CONTRIBUTING.md)).
