# Changelog

All notable changes to chelamux are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project aims to follow [Semantic Versioning](https://semver.org/).

Tracking starts at the open-source launch (2026-07-21). Earlier development
history lives in `git log`.

## [Unreleased]

### Added

- **`chela update` now refreshes the plugin too, not just the server.** A release has
  two halves: the server-side `git pull` + `uv sync` + `pm2 restart`, and the plugin
  every agent loads its hooks from — a separate copy Claude Code made at install time
  that the server-side dance never touches. `claude plugin marketplace update
  <marketplace>` and `claude plugin update <plugin>@<marketplace>` are both fully
  non-interactive, so `chela update` now runs them itself, for every marketplace an
  installed copy of the plugin came from, right after a pull. Previously this was left
  to a printed reminder telling a human to run `/plugin update` by hand — which once
  meant every agent window started after a plugin-cache sweep loaded no hooks at all,
  silently killing outbound relay until someone noticed.

### Fixed

- **The Decisions inbox search box could not be clicked.** The popover closed itself
  on any click anywhere inside it — including the click that should have focused the
  search field — so the search added alongside it was unusable with a mouse. Clicks
  inside the popover now keep it open; clicking outside still dismisses it, and
  opening a decision's ticket closes it deliberately.

### Changed

- **A pane whose status chela cannot resolve now says so, instead of claiming "idle".**
  Agent busy/idle status is matched to a pane by process id, with the working directory
  as a fallback — but on a single-user setup every agent shares one home directory, so
  that fallback would hand one agent's status to another. It now stays silent unless
  every process sharing a directory agrees, and a pane with no resolved status shows a
  muted `? unknown` rather than a confident `○ idle`. Wall ordering is unchanged.
  Background: `docs/AGENT_IDENTITY.md`.

### Fixed

- **Agent busy/idle status could silently stop updating for the whole fleet.** chela
  reads native session status by shelling out to `claude agents --json`, under a
  10-second timeout. That command's startup cost has grown past 10 s, so on affected
  machines *every* call timed out, the status cache stayed empty, and the dashboard
  drew every pane as **idle** — indistinguishable from a genuinely quiet fleet. The
  "needs you" ring, taskbar ordering, tab-title count and auto-arrange ranking all
  lost their input. Nothing surfaced it but a log line, so it could persist for
  weeks. The timeout is now 45 s and the refresh happens on a background timer
  instead of inside a web request, so a slow `claude` never stalls the dashboard.
  Both are tunable: `CHELA_STATUS_CMD_TIMEOUT_S` and `CHELA_STATUS_TTL_S`.

### Added

- **The dashboard now tells you when agent status is unavailable.** A topbar marker
  (`⚠ agent status unavailable`, shortened to `⚠ status down` on narrow screens)
  appears whenever the native status feed stops answering, so an empty status map is
  never mistaken for a calm fleet again. Backed by a new `/api/agents/status_health`
  endpoint and an `agents.native_status_feed` check in `chela doctor`, which asks the
  command directly rather than trusting a cache that was healthy before the outage.
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
