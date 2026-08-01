# Changelog

All notable changes to chelamux are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project aims to follow [Semantic Versioning](https://semver.org/).

Tracking starts at the open-source launch (2026-07-21). Earlier development
history lives in `git log`.

## [Unreleased]

## [0.3.0] — 2026-08-02

Honest self-reporting, and the operator controls that act on it.

`chela doctor` could report a checkout "in sync with its upstream" while it sat
commits behind and every `chela-*` service kept serving code it had loaded days
earlier — the exact shape that let five merged PRs run inert for a full day. This
release makes that condition visible from two independent angles (the checkout
against its upstream, and the RUNNING services against the checkout), gives the
dashboard controls to act on it without an SSH session, and closes the gaps that
let the judge's own verdicts go stale.

### Added

- **`chela restore` reports the rows a hard tmux death orphaned.** CMX-82 already
  self-heals the orchestrator's own address in `inbox.json`, but every other
  epoch-stamped row chela writes — `inbox.json` watches, the dispatcher's `runs`
  table (agent + judge window stamps), `telegram-bindings.json`, `session-ids.json`
  — had no self-initiated check and no report: after a tmux restart they just sit
  there, correct-looking and permanently unverifiable, invisible unless a human
  happens to open the right file. `chela restore` scans all four and prints what a
  dead tmux server left behind; for the three stores that carry (or, via the new
  `chela/roster.py` epoch-keyed fleet snapshot, can be joined to) a session id, it
  also classifies each dangling row REVIVABLE (the recorded Claude session is alive
  under a new address — `sessions.wid_for_session`, never reimplemented) or MANUAL
  (nothing live claims it; an exact `cd <cwd> && CHELA_WID=@N claude --resume <sid>`
  one-liner is printed). It exits nonzero while anything is MANUAL, so it composes
  into a restart procedure. `chela doctor` now also carries a
  `restore.dead_epoch_rows` finding, so the count surfaces without a human
  remembering to run the command by hand. (#256)

  ⚡ **`chela restore --apply` now acts on that report.** REVIVABLE rows are
  re-stamped at their new live address (`inbox.readdress`, `sessionids.rekey`);
  MANUAL rows are archived into a new `roster-archive.json` — its own file,
  deliberately never a key inside `roster.json`, since that store is dumped
  whole from the reconcile loop's in-memory copy every tick, unlocked and
  unmerged, so a second writer sharing it would lose silently in both
  directions. Rows are archived before being removed from their live store, so
  a crash between the two steps loses nothing worse than a duplicate archive
  entry, never a silently vanished row. Every writer no-ops
  (reported as `RACED`) if the row has moved on since the report was computed,
  rather than blindly clobbering current state with a stale plan.
  `telegram-bindings.json` stays untouched permanently, not just deferred:
  `chela-telegram` owns that file through its own in-memory registry, saved from
  that object every reconcile tick with no lock or merge, so a second writer
  would race its next save; those rows are still classified and reported, just
  left for the daemon's own reconcile tick to reap. The bare `chela restore` (no
  flag) stays read-only by default, and exit-code semantics are unchanged:
  nonzero while any row is MANUAL, even after `--apply` archived it, since the
  underlying orphaned agent still needs a human to look at it. (#257)

  Also fixes the disarmed-identity bug that let this happen in the first place:
  `chela watch <wid>` now reports (like `chela watch`/`register` already did) when it
  cannot resolve a session identity at registration, instead of silently storing
  `orchestrator_session: null` and disarming CMX-82's self-heal before it ever runs.
  ⚠️ The roster only helps starting from the next reboot — a run today still reports
  stale rows already in each store, but cannot enumerate an epoch chela was never
  running to observe.

- **`chela update` now refreshes the plugin too, not just the server.** A release has
  two halves: the server-side `git pull` + `uv sync` + `pm2 restart`, and the plugin
  every agent loads its hooks from — a separate copy Claude Code made at install time
  that the server-side dance never touches. `claude plugin marketplace update
  <marketplace>` and `claude plugin update <plugin>@<marketplace>` are both fully
  non-interactive, so `chela update` now runs them itself, for every marketplace an
  installed copy of the plugin came from, right after a pull. Previously this was left
  to a printed reminder telling a human to run `/plugin update` by hand — which once
  meant every agent window started after a plugin-cache sweep loaded no hooks at all,
  silently killing outbound relay until someone noticed. (#241)

- **A red `chela doctor` finding now reaches you instead of waiting to be asked.**
  Doctor findings that reach ERROR escalate through `chela notify`, edge-triggered on
  the transition into ERROR and keyed per finding, so a persistent fault reports once
  rather than on every check. The check interval was also raised from 300 s to 3600 s:
  a full `doctor.check()` measures 28–32 s on a live box, which at the old cadence was
  a ~10% permanent duty cycle. (#240)

### Fixed

- **An adopted window that never fired a hook and was never `--resume`d could not
  resolve its own session, so its outbound relay stayed dead.** Session resolution
  tried the event log, then `--resume` on the command line, then fell back to guessing
  from the working directory — and refused that guess outright when another window
  shared the same directory, which every interactive window on a single-user box does.
  It now also checks the `sessionId` that `claude agents --json` already reports for
  the window's own pid — a signal chela was fetching every refresh and discarding.
  Bounded so a recycled pid cannot inherit a dead process's session: the cwd the feed
  cached for that pid must agree with the pane's own origin, and the tier is refused
  when either is unknown. (An earlier attempt bounded it on the feed's `startedAt`
  instead; that field is the *session's* start time, not the process's fork time, and
  measuring it across every live pid on a real box gave deltas of −623 s, +16 s and
  −113 days — it disagrees in both directions, so no tolerance can rescue it.)
  Background: `docs/AGENT_IDENTITY.md`. (#239)

- **…and that fix did nothing until the processes that read it warmed their own
  cache.** The new tier reads a per-process status cache, and only the dashboard was
  populating one — so in `chela telegram` (the process that actually relays) and in the
  daemon, the tier had no data and silently fell through to the refusal it was meant to
  replace. Outbound relay stayed dead while `chela doctor` reported the window
  resolving fine, because forcing a probe warms the cache in the doctor's *own*
  process: a check that warms the thing it checks cannot observe the process that
  doesn't. Both long-lived services now warm their cache, and a cache that has never
  completed a fetch now says so rather than presenting as "the feed had nothing for
  this pid". (#243, #244)

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

### Added

- **`chela reopen` now tracks how many times a run has bounced back to a human,
  and nudges when nothing production-facing changed since the first bounce.**
  `CHELA_MAX_REWORKS` bounds only the dispatcher's *automatic* rework loop — the
  human-takeover `reopen` path was deliberately left unbounded. A new
  `reopen_count` column (surfaced as a `reopen=N` chip in `chela dispatch-runs`)
  and `first_reopen_head_sha` (the fixed baseline every later reopen is diffed
  against, not the previous round's head) let the 3rd-and-later reopen carry a
  `nudge` when a live GitHub compare shows nothing under `chela/` changed since
  that baseline. Informed consent, not a gate — the reopen still succeeds
  either way. Measured live: three tickets in one day burned 47 judge rounds
  between them, with one running 12 straight tests-only rounds before anyone
  noticed production had frozen. (#259)
- **The dashboard Settings drawer can pull and restart the fleet without an SSH
  session.** A live behind-count badge plus an "Update now" button run the
  exact same `update.apply()` `chela update` runs (same dirty-tree /
  diverged-branch refusals, nothing loosened), in a background thread so the
  dashboard's own pm2 restart can't race flushing the HTTP response. Refuses
  outright (409) while a dispatched agent run is `claimed`/`running` — the
  restart `chela update` triggers would orphan it mid-flight — and separately
  refuses (409) a second concurrent update via a module-level lock. (#260)
- **A new `chela doctor` fact for the code actually RUNNING, not just the
  checkout on disk.** A bare `git pull` (bypassing `chela update`) can leave a
  checkout that genuinely reports "in sync" while every `chela-*` PM2 service
  keeps executing whichever process image it last started from. `repo.services_current`
  compares each running service's own start time against whichever is later: the
  checked-out commit's own date, or when that commit actually landed in this
  checkout. The second half is load-bearing — a commit is always authored before
  it is pulled, so comparing against the commit date alone silently misses a
  service that restarted in that ordinary gap, which is exactly the bare-`git
  pull` scenario this fact exists to catch. WARNs by name on any service that
  predates the threshold — read-only, never restarts anything itself. (#261)

### Fixed

- **A clean judge verdict never woke the orchestrator up.** `judge.judge_run`
  only moved a run's `status` off `awaiting_review` on a BLOCKED verdict, so
  `inbox.run_events`' dedupe (keyed on `status` alone) never re-fired past the
  run's original "PR opened" announcement — a clean or `cannot_verify` verdict
  produced no event at all. Measured live twice: the judge posted "every guard
  held" on both cmx-195 and cmx-196, and a human had to notice the silence and
  merge by hand each time. `run_events` now dedupes on `status + judge_state`,
  so a run re-announces once, the moment the judge actually settles, via two
  new event kinds (`run_judge_clean`, `run_judge_cannot_verify`) wired into
  staleness and the dashboard feed the same way `run_review` already is. (#258)
- **`chela doctor` called a checkout "in sync" while it sat 5 commits behind.**
  `repo.upstream_synced` only ever checked `ahead > 0` (local divergence /
  history rewrite); a checkout that was simply stale — never pulled — read as
  fully healthy. Five PRs merged to `dev` in one day and every `chela-*` PM2
  service kept running the old code with 0 restarts, green the whole time. The
  fact now WARNs on `behind > 0` too, pointing at `chela update` or the new
  dashboard control above. (#260)
- **A judge re-run after its worktree was reaped refused to verify, instead of
  just rebuilding it.** The throwaway judge worktree is reaped the moment a
  verdict publishes, but `chela judge run <run> --experiments <file>` — the
  documented manual re-verification path — required that worktree to still
  exist, so re-running the judge against a since-fixed PR dead-ended in "the
  judge worktree is gone" short of a full agent re-spawn. `judge_run` now
  rebuilds a missing worktree at the run's current `pr_head_sha`, re-runs the
  same base-branch catch-up a fresh judge gets, and re-stamps `judge_sha` to
  the sha it just verified — never a guess, never a crash on an unresolvable
  or missing sha. (#262)

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
