# Changelog

All notable changes to chelamux are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project aims to follow [Semantic Versioning](https://semver.org/).

Tracking starts at the open-source launch (2026-07-21). Earlier development
history lives in `git log`.

## [Unreleased]

### Added

- **`chela retry` — "keep going" on a `needs_human` run, no fix required.** `chela reopen`
  (CMX-96) covers one human intent: "I fixed the branch myself, re-verify the new head" —
  and its new-commit gate correctly refuses an unchanged one, since flipping straight to
  `awaiting_review` on a stale head would let an already-rejected commit reach `merge`
  unjudged. That left the *other* intent a `needs_human` verdict provokes just as often
  with no in-contract exit: not wanting to fix it by hand, not wanting to merge past it
  either — just wanting the automatic rework loop to have one more swing at the same code.
  Hit live on CMX-231, twice, with the only escape being to hand-edit the runs database.
  `chela retry <run>` sends the run back to `changes_requested` — the automatic loop's own
  carrier — so the next dispatcher tick re-spawns the agent exactly like a normal rework
  round, on the SAME head, with the SAME verdict it failed on. It spends a *separate*
  `retry_count` grant, never the automatic loop's own `rework_count` budget, and the
  escalation cap check now honors that grant: a run given one retry gets exactly one extra
  round past `CHELA_MAX_REWORKS`, not an unbounded exemption. (CMX-237)

### Fixed

- **A release body could ship the same `### Added`/`### Changed`/`### Fixed` heading
  two or three times.** Parallel worktree agents each append their own subsection
  under `## [Unreleased]` per PR, blind to each other's concurrent edits, so nothing
  in that workflow could stop the same category heading from landing more than once
  before a release shipped — this file's own `## [Unreleased]` section had exactly
  that duplication live. `chela.release_notes.extract_release_notes` — the one place
  every release body (and this repo's own `gh release create --notes-file`) is
  assembled — now collapses repeated `### <Category>` headings in the extracted
  section into one block each, content concatenated in the order it appeared, and
  emitted in Keep a Changelog's canonical category order (Added, Changed,
  Deprecated, Removed, Fixed, Security) rather than first-appearance order — the
  latter is itself merge-order-dependent, so it would only push the same race down
  a level. Titles outside those six categories aren't dropped; they're kept, after
  the known ones, in first-appearance order among themselves. A section with no
  duplicate titles is returned byte-for-byte unchanged. `python -m chela.release_notes
  --write` applies the same coalescing to `CHANGELOG.md`'s own `## [Unreleased]`
  section in place, so what's in the repo matches what the next release ships;
  already-published sections are never touched by `--write` — cleaning those up is
  a separate, deliberate call by the operator. (CMX-235)

- **`TODO.example.md` no longer claims the dispatcher scopes claiming to the `## Open`
  section.** It never did — `chela/sources/markdown.py` claims every unchecked `- [ ]`
  line in the file regardless of which heading precedes it, so a task moved under a
  different heading (a `## Backlog` or `## Later` section, say) stayed just as claimable
  as one left under `## Open`. The doc now says so, and points at the markers that
  actually gate claiming: `<!-- blocked: ... -->` to park a task unclaimed, and
  `<!-- depends: "..." -->` to hold one back until another is done. (CMX-233)

### Changed

- **A judge verdict of "cannot verify" now reaches the inbox even when the run has already
  left review, and a merge can no longer silently kill a judge that is still working.**
  Two follow-ups to the unconditional PR comment below (CMX-228), both measured live on a
  run whose PR merged while its judge was still mid-check: first, the notification that a
  judge could not verify a commit — the CAS-refused race, where the run moves on out from
  under a still-running judge — only ever fired while the run's status was still
  `awaiting_review`; a run that had already merged got no notification at all, so the one
  outcome that means "I could not do my job" was also the one nobody heard about. It now
  fires (and survives delivery) regardless of what the run's status became. Second, the
  dispatcher's judge watchdog reaped a judge's worktree and killed its window off a single
  tick's tmux snapshot — a signal that can miss a genuinely live judge — which SIGKILLed
  one mid-run. The watchdog now cross-checks the judge's own claim lock (pid and start
  time) before tearing anything down, and holds if a live owner still has it; the hold is
  bounded by the existing stuck-judge timeout, so a judge that really did hang is still
  reaped.

- **The judge's verdict now reaches the pull request even when the run moves out from
  under it.** A blocking verdict — a guard that survived deliberate corruption — used to
  reach the PR only as a side-effect of recording a changes-requested state, so if the run
  left review while the judge was still working (someone merged it, or the CI gate got
  there first), the finding was computed and then silently discarded. A clean verdict had
  never been gated that way, which inverted the severity: the finding that mattered most
  was the one most likely to vanish, and being slower to compute gave it more time to lose
  that race. The comment is now posted unconditionally, before any state check, and a
  verdict that fails to post says so in the log instead of disappearing. The run row is
  still guarded — an already-merged run is never resurrected — it just no longer decides
  whether the finding is shown.

- **`chela doctor`'s `hooks.unattributed` fact now names its real cause instead of
  guessing.** It used to point at two candidates — two agents sharing one cwd (CMX-190)
  or a window closing before the hook POST landed — as if either explained every
  hook-blind session. Measured against four days of real production log (CMX-227): ~95%
  of orphaned `hook.*` events trace to sessions that never ran in a chela-tracked tmux
  window at all (a headless job like the nightly memory-consolidation run), not to either
  named cause. The WARN detail now leads with that check (`chela.sessionids.entries()`)
  and only points at CMX-190/teardown-race for the sessions that actually had a window to
  lose.


- **Agent-to-agent messages now travel over Claude Code's peer messaging socket
  instead of being typed into the recipient's terminal.** Claude Code 2.1.224+
  binds a per-session Unix socket, and chela launches each agent with an explicit
  `--messaging-socket-path`, so `chela msg`/`broadcast`, room `handoff`/`question`/
  `blocker` dispatch, and merge-verdict delivery hand the message straight to the
  recipient's message queue. Typing into a pane depended on that pane's input mode
  — a message arriving while the recipient sat in `!`-bash mode would have been
  executed rather than read — and on the terminal accepting a paste cleanly. The
  socket bypasses the terminal entirely.

  Delivery falls back to the previous paste transport whenever the socket can't be
  reached (an older Claude Code, a session that hasn't bound one, a stale socket
  file), so nothing regresses on a mixed fleet. **Key presses, slash commands, and
  permission-gate answers deliberately still go over the terminal**: a peer message
  can never answer a permission prompt, and Claude Code delivers slash commands in
  a peer message as inert text.

  A handoff is not a delivery: a recipient whose own `crossSessionInbound` setting
  holds or refuses the message drops it while the socket still accepts the bytes.
  chela now reads the receipt that reports this and treats it as a failure rather
  than a success — and deliberately does *not* retry over the terminal, since the
  recipient's gate is a policy decision rather than a transport fault.

- **chelamux is now licensed under the GNU Affero General Public License v3.0 or
  later**, changed from MIT. AGPL rather than plain GPL because chela is a
  long-running daemon with a web dashboard: GPL obligations trigger on
  *distribution*, so running a modified chela as a hosted service would carry
  none of them. Under AGPL, offering it to users over a network counts.

  For anyone using chela, nothing changes — run it, privately or commercially,
  and modify it freely. The obligation lands only if you **distribute a modified
  version or run one as a network service**, in which case your source must be
  published under the same licence. Note that AGPL does not restrict commercial
  use and does not entitle the author to any share of revenue.

  Releases up to and including **0.3.0 remain MIT** and stay available under it;
  a licence change applies going forward and cannot be applied retroactively.
  Third-party components keep their own licences — see [NOTICE](NOTICE).

  The Settings drawer now carries the AGPL §13 source offer, which the licence
  requires for software users interact with over a network.

### Fixed

- **A `depends:` marker naming a title with an embedded `;` could never resolve, no
  matter how it was quoted.** `_parse_depends` split the marker's payload on `;`
  *before* stripping quotes, so a title such as `fix the bug; handle the edge case`
  got cut into two garbage segments the moment someone tried to reference it — even
  wrapped in the documented `"..."` quoting. The dependency silently resolved to ids
  no real task holds, which reads exactly like a typo'd reference: the dependent task
  fails closed and stays blocked forever (`chela.dispatcher._ready` logs it as an
  "unresolved reference"), with no way for a human to fix it by writing "better"
  markdown — the title itself was the trap. The split is now quote-aware: a `;`
  inside a matched pair of quotes no longer ends the segment. (CMX-232)

### Added

- **A typo'd `depends:` reference now surfaces in `chela doctor` instead of only a
  daemon log line.** CMX-232 fixed the one CAUSE of an unresolvable edge a human could
  not work around by writing "better" markdown (a title with an embedded `;`); it never
  touched the silence — a plain typo, or a retitled/deleted blocker, still resolves to
  an id nothing on the tracker will ever strike, and `dispatcher._ready` fails that
  closed by design, forever, saying so only in a `log.warning` line wherever the
  daemon's own log happens to scroll. The new `dispatch.unresolved_depends` fact runs
  the same resolution fresh, names the stuck task and the tracker it lives in, and (like
  every other doctor fact) gets pushed by the daemon's edge-triggered
  `check_and_notify` the moment it goes red — the CMX-187 fix, applied to this class of
  bug too.

- **`chela doctor` now reports which transport each agent would actually receive a
  message over.** Peer-socket delivery falls back to typing into the terminal
  whenever the socket cannot be reached, which is correct on a mixed fleet and
  therefore invisible — a fleet can quietly degrade to the old paste transport with
  nothing saying so. The new `peer.transport` fact names every live window as
  `deterministic` (chela-owned socket), `default` (Claude Code's own path — works,
  but resolved through a pid-derived guess and worth relaunching), or
  `tmux fallback`, and warns on the last. Reachability is tested by connecting and
  closing while sending zero bytes, so a stale socket file left behind by a killed
  agent is reported as unreachable rather than mistaken for a live one, and a doctor
  run can never hand an agent a turn.

- **A stuck update-apply lock is now visible instead of silent.** The dashboard's
  `/api/update/apply` refuses a concurrent run with `409`, and that refusal used to
  say only "an update is already running" — indistinguishable from a lock wedged
  hours ago. It now reports how long the lock has been held, says *held* rather than
  *running* when it cannot know, and flags `stuck` past the longest an honest run
  could take, a ceiling derived from the update path's own subprocess timeouts rather
  than a hardcoded number. Because that lock lives inside the dashboard process, the
  hold is published for other processes to read, and a new `dashboard.update_lock`
  doctor fact reports it — so a wedged deploy path surfaces without anyone having to
  click Update again. A lock file whose process has died reads as free, not stuck.

- **chelamux now tags its releases.** `0.2.0` and `0.3.0` were shipped without a
  `git tag`, so `git tag` read empty and the GitHub repo sidebar said "No releases
  published" even though two releases were live. `v0.2.0` and `v0.3.0` are now
  tagged retroactively (at the commits that set `pyproject.toml`'s version to each),
  with a GitHub Release per tag whose body is the matching section of this file,
  pasted in verbatim — see "Releasing" in [CONTRIBUTING.md](CONTRIBUTING.md).
- **`.github/workflows/release.yml`.** A tag push (`vX.Y.Z`, the deliberate human
  act "Releasing" in `CONTRIBUTING.md` still describes) now builds that tag's
  GitHub Release automatically: `chela.release_notes` (a real, unit-tested Python
  module — `tests/test_release_notes.py` — not inline shell in the workflow YAML)
  extracts the matching `CHANGELOG.md` section and hands it to `gh release create
  --notes-file`. CI still never creates or pushes a tag itself. A `workflow_dispatch`
  input (`dry_run`, default `true`) lets the extraction be exercised against an
  already-tagged version, like `v0.3.0`, without publishing anything.

### Changed

- **The Settings dashboard no longer outranks an operator's exported `CHELA_*`
  env vars.** Every dashboard-writable knob (the new "Timing" tab's nine, plus
  `projects_dir`) now resolves env var > `config.json` (what the dashboard
  wrote) > built-in default — previously `config.json` won, which meant a
  value clicked in a browser (the dashboard binds loopback with no auth) could
  silently override a value an operator had explicitly exported. Anyone
  relying on the old order — setting both `CHELA_PROJECTS_DIR` and a dashboard
  `projects_dir` and expecting the dashboard value to win — now gets the env
  value instead. A knob whose env var is set now also reports that in the
  Timing tab (`source: "env"`) and disables the field rather than offering an
  edit the env value would silently discard. (CMX-217)
- **`chela.__version__` is now derived, not a second hardcoded literal.**
  `chela/__init__.py` previously hardcoded its own copy of the version string
  alongside `pyproject.toml`'s — two facts that could (and did) drift. It now reads
  the installed package's own metadata (`importlib.metadata.version("chelamux")`),
  so `pyproject.toml` is the only place the number is written. (CMX-214)
- **`tests/test_version.py` now also guards `pyproject.toml`'s version against
  `CHANGELOG.md`'s newest dated release heading**, not just against the installed
  package's own metadata. That first guard alone can't fail in CI — `uv sync`
  reinstalls from `pyproject.toml` before every run, so the two values it compares
  are structurally always equal there. The new guard catches the real drift: a
  version bump with no matching CHANGELOG entry, or vice versa. (CMX-214)

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

Add an entry under `## [Unreleased]` per user-facing PR. It becomes a numbered
section — and a tag, and a GitHub Release built from it — the next time chelamux
cuts one; see "Releasing" in [CONTRIBUTING.md](CONTRIBUTING.md).

[0.3.0]: https://github.com/Devail1/chelamux/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/Devail1/chelamux/releases/tag/v0.2.0
