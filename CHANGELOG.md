# Changelog

All notable changes to chelamux are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project aims to follow [Semantic Versioning](https://semver.org/).

Tracking starts at the open-source launch (2026-07-21). Earlier development
history lives in `git log`.

## [Unreleased]

### Added

- **A terminal run's straggler tmux window is now reaped every tick, not just at the moment
  it transitions.** Each terminal transition already fires a best-effort `tmux kill-window`,
  but that call is fire-and-forget and can silently fail (a transient tmux hiccup, or the
  self-kill race of an agent's own `chela task-finished` tearing down the window it is running
  in) — the run still looked done everywhere except the dashboard, which kept showing a live
  window sitting in a directory that no longer existed. `_reap_terminal_windows` now re-checks
  every `done`/`closed` row of a workflow on every tick and kills any window still alive for
  one, verified by matching both the recorded `window_id` and the current tmux server's epoch
  so a restarted server's renumbered ids are never mistaken for a stale run's own window.
  (CMX-329)

## [0.9.0] — 2026-08-30

### Added

- **The CI ref-state assertion is now EXECUTED by the test suite, not just string-matched.**
  Every assertion in `tests/test_ci_workflow.py` read `ci.yml` as text, so a change that
  edited the workflow and its pin consistently passed every one of them — the
  source-constant-vs-rendered-value residual `docs/defeat_shapes/314-*.md` named and left
  open. The block is now read out of the parsed YAML and run under GitHub's own shell flags
  (`bash --noprofile --norc -eo pipefail`) against throwaway git repos: it must ACCEPT
  `cmx-999`, `dev`, `main`, `release/1.2` and `docs-only`, and must REJECT a detached HEAD
  and a missing `origin/dev`. A reintroduced `cmx-N` branch-naming requirement — the CMX-314
  production regression — now fails on behaviour whatever spelling it uses, including the
  `case` glob that defeated three successive text-based guards. (CMX-317)

- **A watched agent's completion notice now carries the agent's own closing words.**
  `chela watch` reported only that a window finished — a fixed template — so the
  orchestrator learned that an event had happened and nothing about what happened, and its
  next move was always `chela read @N` by hand. The notice now quotes the agent's last
  message: an excerpt in the one line that is pushed, and the untruncated text in the
  event payload as `final_message`. Tool-only final turns, unreadable transcripts and
  unresolvable windows all fall back to the previous template — losing the event would be
  worse than losing the excerpt. Resolved by window id, never by cwd, so a sibling agent in
  a shared directory can never have its words quoted as another agent's summary (CMX-191).
  (CMX-318)

- **The `$CHELA_WID` gate CMX-319 shipped is now proven by behaviour, not just by the
  shape of the generated command string.** Every existing test asserted the command
  *starts with* the `[ -n "${CHELA_WID:-}" ] &&` check; none of them actually ran it. New
  tests execute each gated hook's real command through a shell against a `curl` stub on
  `PATH` and count invocations: an unset `$CHELA_WID` now proves zero `curl` calls for
  every gated event, and a set one proves exactly one. Also measured and documented what
  the gate costs: `PreToolUse`/`PostToolUse` moved off `http` (zero process spawns) onto a
  `command` hook that now fork/execs a shell on every tool call regardless of gate state
  (~0.5ms) and, when the gate is open, a `curl` process too (~3.6ms total) — twice per
  tool call, a cost the old transport never had. (CMX-322)

### Changed

- **The CI ref-state assertion's shell text is pinned in one place instead of two.**
  `tests/test_ci_workflow.py` typed the same three-line block out twice — inline in the
  `_EXPECTED_STEPS` table and again as a local `expected_run` — so editing `ci.yml` and only
  one copy left the other test red for an intended change, and the natural way to clear that
  red is to make the pin agree, which teaches it the mutation instead of catching it. Both
  readers now derive from a single `_REF_STATE_RUN` constant. This removes drift between two
  copies only; it does not close the source-constant-vs-rendered-value residual named in
  `docs/defeat_shapes/314-*.md`, which CMX-317 closes by executing the block. (CMX-316)

### Fixed

- **A release's changelog fragments can no longer be published twice.** Steps 1–3 of
  "Releasing" run on `main` and delete the `changelog.d/` fragments they consume, but
  nothing carried those deletions back to `dev` — so every fragment a release ate stayed
  alive on `dev`, and the next release collected and republished entries readers had
  already seen under the previous version. Found in the tree on 2026-08-21, one day after
  0.8.0: `CMX-309.md`, `CMX-312.md` and `CMX-314.md` were still on `dev` with their text
  already in `## [0.8.0]`, primed to appear again in 0.9.0, while `dev`'s own CHANGELOG
  still showed `0.6.0` as newest. `python -m chela.release_notes --release` now refuses
  before writing anything (`StaleFragmentError`) when a fragment's task id already appears
  in a dated section, naming the skipped `main` -> `dev` back-merge as the cause; a test
  fails on any such fragment in the tree; and CONTRIBUTING's "Releasing" gains the
  back-merge as an explicit step 5. (CMX-315)

- **The judge's staleness check no longer takes its reference from the row it is checking.**
  It read the PR's "current" head out of the run row's own `pr_head_sha` — the same column
  `_spawn_judge` checks the worktree out from — so whenever that column was itself stale the
  comparison was `verified_sha != verified_sha`, False by construction, and a verdict about a
  commit that no longer exists was published as a **confirmed** finding. Measured 2026-08-21
  on `adopt-393`: the run had gone `done`, so nothing refreshed the column after a rework
  pushed; the judge re-checked-out the dead commit, found the guards the rework had added to
  be missing (they were, on that commit), posted three false `SURVIVED` findings to the PR,
  and the decisions inbox escalated them as "needs a human look NOW". The reference now comes
  from GitHub (`pr_live_head_sha`), with the row as fallback only; an unreachable GitHub
  returns `None` — UNKNOWN, never "not stale" — degrading to exactly the previous behaviour
  rather than inventing staleness or asserting freshness. (CMX-319)
- **`PreToolUse`/`PostToolUse`/`PermissionRequest`/`PermissionDenied` no longer fire for a
  session chela did not launch.** `enabledPlugins` in `~/.claude/settings.json` is
  user-wide, so every `claude` process on the machine loaded chela's plugin — including a
  headless `claude -p` call some unrelated tool makes, in some unrelated directory. Those
  calls posted `PreToolUse`/`PostToolUse` into the fleet's event log with no window to
  attribute them to, and opened a `PermissionRequest` hook with a 120s wait for a human no
  chela daemon was ever bound to. These four events now ride a `command` hook gated on
  `$CHELA_WID` — the one signal a chela-managed launch always exports and an unrelated
  `claude` process never has — instead of `http`: an `http` hook cannot see the agent's own
  environment, so gating it needed the transport `SessionStart` already uses for the same
  reason. When `$CHELA_WID` is unset the hook's own curl never runs at all: no socket, no
  event logged, no gate opened. Plugin bumped to 0.2.4; the other ten events stay on
  `http`, which fires far less often and was not the shape measured to matter. (CMX-319)

- **chela can no longer delete a real repository as if it were a worktree.** On 2026-08-21 it
  deleted its own main working copy — twice — as ordinary task-completion cleanup: a run row
  recorded `worktree_path` as the main repo (the task's branch was checked out there when its
  rework spawned, so worktree resolution fell back to the existing checkout), and cleanup
  removed "its worktree". `TODO.md` is gitignored, so ~614KB of tracker history existed in no
  clone, worktree or branch and was unrecoverable; the second deletion took the fresh clone,
  because the poisoned column persists in the database and re-arms on every daemon start
  (issue #398). `remove_worktree` now refuses, before any deletion path runs, to touch the
  repository itself, any directory containing it, or anything whose `.git` is a **directory**
  rather than a file — the structural difference between a real clone and a linked worktree,
  which needs no configuration to check. The refusal raises rather than returning `False`:
  that value already means "an ordinary removal failed, carry on", and a corrupt instruction
  must never be indistinguishable from a routine no-op. Cleanup call sites catch it, log at
  ERROR and leave the path alone, so a poisoned row cannot take the daemon down. (CMX-320)

- **An adopted PR no longer drops out of the judge loop after its first rework.** Reconcile
  must not strike a row `done` for "leaving the tracker" when the tracker never owned it —
  CMX-276 fixed that, but inferred "adopted" from `worktree_path IS NULL`, which holds only
  until the row's first rework round: the rework gets a worktree, the inference flips, and
  the next tick strikes the run. Its PR stays open while nothing judges it, and repairing the
  row by hand does not survive one tick. Measured 2026-08-21 on three adopted PRs at once.
  The origin is now recorded as a fact on the row (`adopted`, written by `adopt_pr`) rather
  than derived from mutable state, with a one-time backfill for rows adopted before the
  column existed. (CMX-321)

## [0.8.0] — 2026-08-20

### Added

- **Dispatched agent windows no longer get their own Telegram forum topic.** `dispatcher._spawn`
  claims a run row with `window_id=NULL` *before* calling `tmux new-window` and stamps the id in
  a separate commit; an auto-topics reconcile tick landing in that gap saw a live, already-named
  window with no id it could match and read it as an untracked human session — minting a real
  topic, pinned title and relay for a worker that had never blocked on a human. Dispatcher
  ownership is now also matched by the row's `window_name`, which is recorded at the claim before
  tmux is touched at all. (CMX-308, #384)

- **The judge now flags a missing CHANGELOG entry as a non-blocking note on every verdict
  comment.** CONTRIBUTING.md said "any user-facing change adds a CHANGELOG entry" — prose
  nobody checked, and it had already failed twice (4 of the last 8 merges before 0.7.0 carried
  no entry, backfilled by hand in #382). A diff that changes non-prose files without touching
  `CHANGELOG.md` (or, per CMX-312, without adding a `changelog.d/CMX-<id>.md` fragment) now
  gets a "No CHANGELOG.md entry" note — it never blocks a merge, since "is this actually
  user-facing" stays a human call, but a PR that skips the entry no longer does so silently.
  (CMX-309, #385)

### Changed

- **The live-terminal message timestamp is bold.** `**[HH:MM]**` rather than `[HH:MM]`, so the
  marker separates from the message's own first words at a glance. The emphasis is markdown, not
  ANSI, and deliberately so: Claude Code markdown-renders `displayContent`, while raw escapes are
  filtered selectively — measured on 2.1.233, ANSI bold and italic survived but ANSI *dim* was
  stripped. Asking the renderer for emphasis does not depend on which escapes are permitted today.
  ⛔ Colour remains impossible on this path; `systemMessage` preserves full SGR but renders on its
  own line prefixed `<Event> says:`, which is the presentation CMX-285 moved away from to get the
  marker inline.

- **Changelog entries are now per-PR fragment files under `changelog.d/`, not shared edits
  to `CHANGELOG.md`'s `## [Unreleased]` section.** Every dispatched branch used to append
  its own entry to that one section, and two branches open at once collided on that exact
  spot — `.gitattributes`' `merge=union` driver (CMX-241) only smooths over a *local* git
  merge, so GitHub's own PR-mergeability check still reported `CONFLICTING`, with **no CI
  checks at all** (GitHub can't compute a merge commit), until a human dropped one side's
  entry. Measured on CMX-308 and CMX-309 (2026-08-18); CMX-309 alone spent five rounds
  stuck there. A PR now adds `changelog.d/CMX-<task-id>.md` instead — a file unique per
  task can never collide with a sibling's. `python -m chela.release_notes --release X.Y.Z`
  collects every fragment (in filename order), merges duplicate categories the same way
  concurrent `## [Unreleased]` entries always were, promotes the combined body into the new
  dated section, resets `## [Unreleased]` to empty, and deletes the consumed fragments — one
  command in place of the two hand-typed "Releasing" edits step 1 used to require. See
  `changelog.d/README.md` and "Releasing" in `CONTRIBUTING.md`. (CMX-312)

### Fixed

- **A Telegram message lost to flood control now says so, instead of hiding among identical
  warnings.** Once `_call` exhausts its 429 retries the content is gone, but that logged the same
  generic warning as a recoverable formatting rejection and as an intentional, expected skip — so
  a dropped question and ordinary noise were indistinguishable, and the only way to be sure was a
  screenshot. A genuine drop is now an `error` naming it as flood control, and the relay logs the
  window id and content type that no lower layer knows. (CMX-311, #387)

- **CI's ref-state assertion no longer fails every PR opened from a non-`cmx-N` branch.**
  CMX-305 rework round 5 added a "Assert the ref state the CMX-301 guard needs" step that
  hard-failed unless `git rev-parse --abbrev-ref HEAD` matched `cmx-[0-9]+` — but
  `tests/test_judge.py`'s own CMX-301 guard treats a non-`cmx-N` branch (`dev`, `main`,
  `release/*`, ...) as a legitimate, tested skip, not a fault. The step turned that designed
  skip into a red build for every such PR, including the `dev` -> `main` release-promotion
  PR itself. The assertion now only checks what CMX-305 actually needed: HEAD attached to a
  real branch (not the detached default of a `pull_request` checkout) and `origin/dev`
  resolvable. (CMX-314)

## [0.7.0] — 2026-08-18
### Added

- **PARKED `TODO.md` bullets now appear on the Work board's Backlog lane.** A task marked
  `<!-- blocked: ... -->` is deliberately not claimable, and used to be invisible
  everywhere as a result: not in To Do, and — since it lives in `TODO.md` rather than
  `BACKLOG.md` — not in Backlog either, so parked work sat in the tracker with nothing on
  the board to show for it. Parked bullets now render in the Backlog lane as their own
  card style, carrying a 🔒 cue and their blocked reason as visible text, and without the
  Promote affordance a real `BACKLOG.md` idea gets. They remain unclaimable — only their
  visibility changed. (CMX-298, #372)
- **A per-session changed-files / diff surface.** A wall tile's bottom bar has a new
  "Files" chip — every file that session's live pane cwd has changed since its last
  commit (staged, unstaged, and untracked, merged into one list with per-file +/-
  counts), and a click-through unified diff for any one of them. Backed by two new
  read-only endpoints (`/api/agents/<wid>/diff`, `/api/agents/<wid>/diff/patch`) and
  works for any git checkout a session's window is sitting in — a dispatcher worktree
  or a plain attended checkout alike, no dispatcher bookkeeping required. (CMX-299, #373)
- **The sidebar now marks each session's role — Orchestrator, Dispatched, or plain.** The
  fleet already carried both facts (the single decisions-inbox slot from `orchestrator.js`,
  and the API-provided `dispatched` flag the dispatcher stamps on windows it owns) but
  neither showed up next to the session it described, so telling a hand-opened shell apart
  from a dispatched worker or the orchestrator meant opening its detail view. Each row (and
  the agent-detail panel) now renders a text badge — never colour alone — reusing the same
  colourblind-safe convention as the window-type glyph; a plain session, the common case,
  gets no badge at all. The badge is an icon — a crown for the orchestrator, a bot for a
  dispatched worker — carrying its own `title`/`aria-label`, so the cue is a distinct SHAPE
  rather than colour alone and the row keeps its width for the session name. The badge
  updates live off the same orchestrator-change event the pane toggle and decisions owner
  chip already listen to. (CMX-300, CMX-302)

### Changed

- **New defeat-shape catalog entries are numbered off their own CMX task id.** The
  previous instruction — number one past the current highest file — was a decentralized
  guess every concurrent branch computed independently, so a collision was inevitable
  rather than incidental: three branches in flight on 2026-08-16 collided six ways and
  needed disjoint ranges hand-allocated from outside them. The task number comes from a
  single centrally serialized counter, so two branches can never draw the same one, and a
  mechanical check now fails when an added entry is numbered any other way. Numbers only
  ever had to be unique, not contiguous, so existing entries are untouched. (CMX-301, #375)
- **A guard comparing a numeric constant against the module's own symbol now fails CI.**
  `assert timeout == module._TIMEOUT` cannot see `_TIMEOUT` itself change — both sides move
  together — so such a guard silently stops protecting its bound. A static check now flags
  any test comparing a numeric module constant re-imported from the module under test with
  no companion literal pin; run once repo-wide it found seven live instances that had
  nothing to do with the bug that prompted it. Scoped to numeric constants on purpose:
  comparing a string/enum sentinel against its symbol is the correct shape, not the defect.
  (CMX-304, #378)
- **The live-terminal message timestamp is quieter.** The `MessageDisplay` marker CMX-285
  added dropped seconds and its clock emoji — `[HH:MM]` in place of `🕐 HH:MM:SS` — since
  the marker's job is "roughly when did this land," not a stopwatch, and a variable-width
  glyph at the start of every message read as content rather than as a marker. (CMX-297)

### Fixed

- **`agent-terminals.sh`'s idle `nap()` no longer leaves its backgrounded `sleep` behind on
  teardown.** A trap-driven `exit` only ends the supervisor's own shell — it never touched
  a `sleep` still backgrounded under `nap()` at the moment the signal landed, so that child
  was reparented to PID 1 and outlived the supervisor for up to an hour. This was cleanliness,
  not a leak: each stranded `sleep` still expired on its own hour, bounding the pool at
  rate × lifetime (~36) and draining it unassisted. `nap()` now publishes the backgrounded
  sleep's pid, and `cleanup()` kills it, so a hard teardown can no longer orphan it.
  (CMX-307, #381)
- **The live-terminal message timestamp now actually appears.** CMX-285/CMX-297 shipped a
  correct `MessageDisplay` response — measured directly, via `curl`, against the daemon's
  own endpoint — but registered the hook over the `http` transport, and a live fleet on a
  current Claude Code build never rendered it: zero `[HH:MM]` markers across thousands of
  lines of scrollback despite the flag being on and the hook confirmed present in the
  loaded manifest. `MessageDisplay` now rides the same `command`-relay shape this repo's
  `SessionStart` hook already proved Claude Code actually acts on — a `curl` into the
  identical endpoint, printing its response as the command's own stdout — changing only
  the transport, not what the daemon computes. Existing installs need a plugin refresh
  (`/plugin uninstall chela@chela` + `/plugin install chela@chela`, or `chela doctor`
  will name the stale copy) since hooks are read at agent startup. (CMX-303)

## [0.6.0] — 2026-08-15

### Fixed

- **The Telegram relay no longer goes silent for one of two sessions running in the same
  directory.** A window is matched to the transcript it is writing by session id, and the
  event log is where that id normally comes from — but `event_log.RING_SIZE` bounds **one
  ring shared across the whole fleet**, not one per window. For two windows in a directory,
  exactly one record ever carries the right window id: that session's own `SessionStart`,
  the only hook that rides the `command` transport and carries `$CHELA_WID`. Every other
  hook arrives over plain HTTP with nothing to disambiguate it, so a single record is all a
  session ever has — and a busy fleet wraps past it in minutes. After that, resolution falls
  through to the cwd, which correctly *refuses* to guess between two windows sharing one
  directory, and the topic simply stops updating. It looked intermittent because it depended
  on how much other traffic had happened since that session started. A resolved session id
  is now promoted into a durable pin that lives outside the ring, and consulted on later
  resolutions — bounded so it is only trusted for a live pane whose transcript has grown
  since that pane's current process began, so a relaunched window can never inherit a dead
  predecessor's session. (CMX-295, CMX-296)

- **A release no longer silently consumes the CHANGELOG's `## [Unreleased]` section.** The
  documented release step said to rename that heading to the new version and stopped there,
  so the section ceased to exist and every PR merged afterwards had nowhere to add an entry
  — the convention lapsed with nothing failing until the *next* release shipped empty notes.
  Measured: eleven merges after `0.4.0` carried no changelog entries at all. The runbook now
  states the missing step and why it matters, and a test asserts the heading is always
  present — anchored to the line, because the same string appears several times as prose
  inside earlier entries and an unanchored check passes on those while the heading is gone.
  (CMX-294)


## [0.5.0] — 2026-08-15

### Changed

- **The Settings drawer is now a tabbed modal, and Cost has a home in it.** The drawer
  shared the screen with the terminal wall and grew past what one scroll could hold, so
  finding a knob meant hunting a single long column. Settings now open as a centered modal
  with a seven-entry tab rail (General, Timing, Dispatch, Notifications, Cost, Appearance,
  Collaboration), and `cost.js` — orphaned when the Cost view was removed from the nav — is
  revived as the Cost tab rather than left dead in the tree. The modal reuses the
  command-palette shell instead of introducing a second bespoke overlay, so Esc, the
  backdrop, and focus handling behave the way every other overlay in the dashboard does.
  (CMX-287)

- **The Decisions inbox is a centered modal too, instead of a full-width panel that
  occluded the wall.** It rendered as an anchored popover spanning the whole dashboard, so
  reading a decision meant losing sight of the agents the decision was about — transient
  content presented as permanent furniture. It now opens on the same modal shell as the
  palette and the settings tabs: open it, act, dismiss it. (CMX-288)

- **`docs/DEFEAT_SHAPES.md` is now one file per shape under `docs/defeat_shapes/`, and the
  monolith is a static pointer.** Every dispatched rework appends a shape it discovered, so
  concurrent branches all edited the same append-only file and collided on every refresh —
  five open runs were stranded on that one conflict at once, two of them until their retry
  budgets ran out. A new shape is now a new file, which cannot conflict. The index carries
  no list to maintain, and the shape *numbers* — which cross-references and the test suite's
  `DEFEAT_SHAPES #N` citations actually point at — are enforced unique by a test rather than
  assumed. (CMX-284, CMX-293)

### Fixed

- **An expired login no longer costs the judge a full 60-minute timeout.** When a judge's
  Claude session hit `Login expired · Please run /login`, nothing distinguished it from a
  judge that was simply thinking: the watchdog waited out `JUDGE_TIMEOUT_SECONDS` before
  reaping, and the run's retry budget drained meanwhile. Measured live on 2026-08-14, two
  judges sat at that banner for the full hour. The watchdog now reads the judge's *own*
  window for the expired-session banner and reaps immediately — a third, affirmative reason
  to reap alongside the timeout and the lock cross-check, checked only while the window is
  alive so it never widens what already reaped without it. (CMX-282)

- **Judge self-check scratch files can no longer reach the repository.** `chela
  task-finished --self-check-experiments` writes a throwaway JSON at the repo root, and an
  autonomous `git add -A` swept it in: five incidents across four different filenames in a
  single day, two of which landed on the default branch and had to be reverted by hand, and
  one of which blocked its own pull request's rebase as a modify/delete conflict. The ignore
  rule now matches the naming *convention* rather than the one literal spelling that
  happened to be reported, and the guard that proves it asks `git check-ignore` about a real
  filename per spelling — so a pattern that stops matching fails loudly instead of sitting
  in the file looking correct. (CMX-286, CMX-289)


## [0.4.0] — 2026-08-14

### Added

- **`scripts/smoke-fresh-install.sh` — a real, isolated fresh-install smoke test for
  adopters, walking the documented adopter order end to end.** Nobody had ever verified
  that a brand-new clone can `uv sync` and get through install → plugin render → first
  dashboard → `chela doctor` → `chela update` → `chela dispatch --dry-run` → teardown
  without breaking — every install on the maintainer's own box predates months of changes,
  so "it worked when I set it up" was never actual evidence about current `dev`/`main`. The
  script does a real `git clone` into an isolated temp dir (defaults to the public GitHub
  repo; accepts a local path for offline runs), `uv sync --all-extras`, renders the plugin
  (`chela plugin --dir`, the scriptable half of the documented install — the two
  `/plugin marketplace add` / `/plugin install` slash commands are Claude Code REPL-only
  and have no headless equivalent, stated as a scope boundary rather than faked), starts
  `chela dashboard` on an isolated port and checks `/api/agents` answers 200, runs
  `chela doctor` / `chela update --check` / `chela update`, runs `chela dispatch --dry-run`
  against a self-contained fixture tracker, and asserts teardown leaves nothing root-owned
  behind. Fails loudly on an uncaught exception (distinguished from an ordinary reported
  finding/refusal by scanning for a real traceback, not just a non-zero exit — verified live
  against a deliberately malformed fixture workflow). Strips every inherited `CHELA_*` env
  var and pins `CHELA_TMUX_SESSION` to a guaranteed-nonexistent name — a box that already
  runs a live chela install (this project's own dev machine, notably) otherwise leaks its
  real `CHELA_DISPATCH_WORKFLOWS`/`CHELA_TMUX_SESSION` into what is supposed to simulate an
  adopter's untouched shell, and an unpinned tmux session falls back to this script's own
  calling pane — which on this box resolves to a `webterm_chela__*` mirror GROUPED with the
  live `chela` session, so the dashboard step would otherwise read the box's real live
  fleet. `tests/test_smoke_fresh_install.py` runs it for real (offline, against a local
  clone) as part of the normal suite. (CMX-263)

- **`chela rework-disputed` — a rework agent's "nothing to push" escape hatch.** A rework
  agent that reads the verdict and concludes it is wrong, already fixed, or otherwise
  unfixable has no new commit to offer. The rework prompt used to just tell such an agent
  to say so in its final message and stop — which left the run in `running` forever:
  `task-finished` assumes a fresh commit landed (the dispatcher judges once per head
  commit, so an unchanged sha never gets re-judged), and the idle watchdog just re-sends
  the same rework prompt on a timer, so every liveness signal kept reading healthy while
  the run itself never moved again. `chela rework-disputed <id> "<reason>"` is the
  in-contract alternative: it moves the row straight to `needs_human` (never
  `awaiting_review`, which would carry the same already-judged head) without touching the
  branch, worktree, PR, or `rework_count` — a human resolves it from there with `chela
  retry`, `chela reopen`, or by closing the PR. (CMX-248, re-scope of CMX-244)

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

- **`chela doctor` now has a STANDING signal that a judge's blocking verdict was
  lost, not just a one-shot notification.** CMX-239 gave the CAS-refused race on a
  BLOCKING verdict its own state (`judge_state=blocked_race`) and its own urgent
  inbox event (`run_judge_blocked_race`) — but that event is edge-triggered: it
  fires once, the moment the run row first lands in that state, and never again
  while the row sits stuck. A guard that survived corruption on a commit that may
  already have shipped is exactly the kind of finding that can be missed by
  whoever happened to be watching at that one moment, and until now there was
  nowhere a LATER `chela doctor` run could still find it — nothing read
  `judge_state` at all. A new fact, `judge.blocked_race`, closes that: every run
  stuck in `blocked_race` is reported every time doctor runs, for as long as it
  stays stuck. (CMX-240)

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

- **`chela rework-disputed` now routes back to `awaiting_review`, not `needs_human`, when
  the head already moved past the disputed verdict.** CMX-248 always landed a dispute on
  `needs_human`, reasoning that `awaiting_review` would carry the SAME head the judge
  already ruled on, unjudged again — right when the head really is unchanged, wrong when
  it isn't. A rework round can push several commits before the agent decides the
  REMAINING finding is wrong or unfixable: it fixes what it can, pushes, and disputes the
  rest. The PR's live head is then already past `judge_sha`, and there is an un-judged
  commit sitting on the branch right now — sending that to `needs_human` stranded a
  fixable PR behind a human with nothing to decide. `mark_rework_disputed` now re-reads
  the PR's live head from GitHub (never trusted stale off the row, same discipline as
  `chela reopen`'s new-commit gate) and compares it to `judge_sha`; a genuine mismatch
  routes to `awaiting_review` instead, where the judge/CI/merge gates pick the new head up
  automatically, same as any other push. An unset sha on either side is not treated as
  positive "moved" evidence (the same conservatism CMX-246's stale-head guard and
  CMX-238's merge gate already use) — everything else still falls back to the original
  `needs_human` behavior unchanged. (CMX-251)

- **A `needs_human` escalation's inbox summary now says WHY, not always "the PR still
  fails review."** `dispatcher._escalate` has several call sites, each handing
  `last_error` a DIFFERENT reason — a spent rework budget, checks stuck pending, a dead
  judge, a rework that couldn't re-attach its worktree — but `inbox.run_events` asserted
  the first of those for every one of them, so a human paged for a stuck-check or a
  missing-branch escalation was told a review verdict that never happened. The summary
  now extracts the actual reason: the first paragraph of `last_error` (the part
  `dispatcher._format_escalation`, CMX-242, composes as the reason itself — the
  `Recommendation:`/`Options:` paragraphs that follow it stay in the full `last_error`
  payload, not pasted into the one line typed at an operator's prompt), excerpted to
  `SUMMARY_TITLE_CHARS` the same way a judge's cannot-verify reason already is. The
  `reworks: N · verdicts on the row: M` counts are unchanged. (CMX-247)
- **A CI job that never ran the suite no longer spends a rework round.** The automatic
  CI-red gate (CMX-69) treated every failing check as evidence about the code, but GitHub
  reports two conclusions — `STARTUP_FAILURE` and `ACTION_REQUIRED` — that mean the job's
  steps never executed at all: a runner/workflow-file problem, or a pending approval gate,
  neither fixable by a coding agent. A third shape is not visible at the conclusion level
  at all: a runner that dies mid-job (a `checkout` step failing on a network/TLS fault)
  reports plain `FAILURE`, indistinguishable from a genuine test failure without reading
  the job's own steps. All three now short-circuit before `request_changes`: the PR still
  shows red (the merge gate still refuses it) and a comment still explains why, but no
  agent is spawned and `rework_count`, the bounded loop's budget, is never touched. A real
  failure alongside an infra one is still charged (real evidence wins, conservatively), and
  a run stuck in infra failures is bounded on its own separate streak (capped the same as
  `CHELA_MAX_REWORKS`, reset the next time CI is seen green) so a permanently broken
  workflow file still reaches a human instead of looping quietly forever. The plain-`FAILURE`
  shape is decided by ONE validator (`_validate_ci_jobs`) that turns the untrusted
  `gh run view --json jobs` payload into a fully-typed `tuple[CIJob, ...] | None` — treating
  ANY structural deviation, anywhere in the tree, as unreadable — feeding a classifier
  (`_suite_step_ran`) that only ever sees already-validated data. (CMX-245, a re-scope of
  CMX-243, which spent 8 reworks discovering one malformed-shape branch at a time; this
  closes that family by construction instead of one round at a time.)

- **An automatic escalation to `needs_human` now names a recommendation and concrete next
  steps, not just a bare reason — and so does a refused `chela merge`.** `chela escalate`
  — the human-typed escalation path — has always taken a `--recommendation` and
  `--options`, but nothing that escalated on its own used them: not the dispatcher (a
  stuck CI check, a rework that spent its budget, a rework that could not re-enter its
  branch/worktree), and not `contract.merge`'s own `escalate`-tier refusals, which is the
  case that actually produced this ticket — an autonomous merge refused on CI (no checks
  registered at all) with nothing but a bare reason, leaving the caller to derive the
  option set itself and, worse, to not know that pushing an empty commit to retrigger CI
  quietly invalidates the judge's already-clean verdict (a new sha the judge never saw).
  Every automatic escalation site in the dispatcher, and every `escalate`-tier refusal in
  `contract.merge`, now carries a recommendation and options in the same shape
  `chela escalate` does — `needs_human` and a refused merge both read as "here's what
  happened, here's what I'd try, here's what you can do about it." `never`-tier refusals
  (a PR targeting a production branch) are untouched: that is a hard line, not a human's
  decision to be handed options for. (CMX-242)

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

### Fixed

- **A judge's BLOCKING verdict that loses the CAS no longer downgrades to "cannot
  verify."** CMX-228 and CMX-229 made a blocking finding (a guard SURVIVED corruption)
  survive the race where a run leaves `awaiting_review` while the judge is still
  working — the PR comment posts unconditionally, and an inbox notification fires
  regardless of what the run's status became. But the run row's `judge_state` — and
  therefore `chela status`, the dashboard badge, and which inbox event actually fired —
  still recorded the SAME `cannot_verify` state as a launch failure or a flaky
  worktree, silently downgrading a CONFIRMED, urgent finding to an unknown shrug. A
  human skimming "cannot verify, needs a look" reads as "the judge couldn't do its
  job"; a run that already merged with a guard proven to survive corruption is not an
  unknown, it is the most urgent verdict the judge can produce. This race now records
  its own state (`blocked_race`, never plain `blocked` — that value also persists on a
  row long after an ordinary blocked run settles, through rework rounds and even an
  eventual `needs_human` escalation, so reusing it would misread that unrelated later
  status change as this race) and raises a dedicated, correctly-urgent inbox event
  (`run_judge_blocked_race`) regardless of what the run became. It also no longer
  burns the bounded `cannot_verify` retry budget re-discovering a verdict that is
  already definitive. (CMX-239)

- **A judge verdict about a head that no longer exists no longer spends a rework round.**
  The judge's mutation battery takes minutes to run; if a newer commit lands on the PR in
  that window (the once-per-sha trigger already re-spawns a fresh judge for it), a still-
  running judge's eventual verdict is about a commit the PR no longer presents as its head.
  Nothing compared the two before spending anything: a stale BLOCKING verdict burned a round
  of `CHELA_MAX_REWORKS` through `request_changes` — and at the cap, escalated the run to
  `needs_human` for a finding the newer commit may have already fixed — while a stale CLEAN
  verdict could silently overwrite a newer, different verdict (e.g. a genuine `blocked`) a
  fresh judge had already recorded on the same row. `judge_run` now re-reads the run's live
  `pr_head_sha` right before either branch would act; a KNOWN mismatch against the commit
  actually tested discards the verdict without spending a round or touching `judge_state` —
  the finding still posts to the PR as a comment, and the per-sha trigger re-judges the new
  head on its own. The PR comment and a new `judge.stale_head` event log entry both name the
  judged sha and the PR's live head, so a superseded verdict is never mistaken for a live
  one. (CMX-246)
- **Concurrent `cmx-N` branches no longer collide on `CHANGELOG.md`.** Every dispatched
  branch adds its own entry to the top of the same `## [Unreleased]` section, so any two
  branches open at once conflicted on that exact spot the moment the judge's worktree
  refresh (`_refresh_judge_worktree`, CMX-176) pulled a moved-on `base_branch` back in. The
  conflict was never semantic — both entries belong — but plain 3-way merge can't know
  that from two edits landing on the same line, so a branch fell behind and its judge
  reported `cannot_verify` for reasons that had nothing to do with the change under review.
  A new `.gitattributes` marks `CHANGELOG.md merge=union` — a driver built into git itself —
  so a conflicting hunk there now keeps both sides' lines instead of stopping the merge.
  (CMX-241)
- **The autonomous merge gate no longer trusts a judge-clean verdict recorded against a
  stale commit.** `judge_state == 'clean'` alone was treated as sufficient evidence to
  autonomously merge — nothing compared the commit the judge actually verified
  (`judge_sha`) against the PR's live head. A slow judge still mid-run on an older commit,
  or one whose verdict raced a newer commit landing on the PR and lost, could leave
  `clean` sitting on the row while CI and GitHub's mergeability check both settled on a
  commit the judge never saw — the run would then present as approved and mergeable with
  no record that anything was wrong. `chela.contract.merge` now refuses (as an escalation)
  whenever `judge_sha` and the PR's live head commit are both known and differ; an unset
  `judge_sha` is left exactly as trusted as before, so no existing judge-clean run is
  affected. (CMX-238)

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

[0.6.0]: https://github.com/Devail1/chelamux/compare/v0.5.0...v0.6.0
[0.5.0]: https://github.com/Devail1/chelamux/compare/v0.4.0...v0.5.0
[0.4.0]: https://github.com/Devail1/chelamux/compare/v0.3.0...v0.4.0
[0.3.0]: https://github.com/Devail1/chelamux/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/Devail1/chelamux/releases/tag/v0.2.0
