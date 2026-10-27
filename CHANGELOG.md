# Changelog

All notable changes to chelamux are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project aims to follow [Semantic Versioning](https://semver.org/).

Tracking starts at the open-source launch (2026-07-21). Earlier development
history lives in `git log`.

## [Unreleased]

## [0.18.1] — 2026-10-27

### Added

- **`chela keeper digest <WORKFLOW.md>` prints what happened in a workflow's repo since the
  last digest.** For each live session launched in the repo or one of its worktrees it lists
  the window, its live session id, busy/idle, the latest recap, the prompts typed and the
  files edited. It also lists the commits on any branch, the PRs opened or merged, the issue
  ids mentioned anywhere, and every open issue on the workflow's tracker. It uses no LLM and
  never writes to the tracker. Each section is capped and says how many rows it dropped. A
  git, `gh` or tracker read that fails is shown as unreadable, never as empty. A cursor in
  `$CHELA_DIR/keeper/` makes the next run start where this one ended; `--since 90m`
  overrides it, and `--json` is the input for the next keeper slice. (CMX-346)

- **An agent's own pytest run is memory-capped per test process.** A suite started by
  `chela task-finished` or the judge runs in a memory-capped `chela-suite-*` scope; an
  agent's own `pytest` did not, and one runaway xdist worker reached 11 GB and got other
  agents' suites reaped. `tests/conftest.py` now sets a soft `RLIMIT_AS` of 3 GiB on each
  process that runs tests, unless a `chela-suite-*` or `memcap` cgroup already caps it, so
  a runaway test fails with `MemoryError` in its own worker. Subprocesses (node, vitest,
  Chromium) are exempt. `CHELA_TEST_MEMORY_CAP=6G` raises it; `0` turns it off. The
  standing rules now say never to run a full suite yourself. (CMX-354)

- Tests no longer inherit the live `CHELA_*` environment (e.g. `CHELA_AUTO_MERGE=true` from chela.env), which turned every nightly-sweep and agent-sweep baseline red (CMX-355).

### Changed

- **A blocking verdict lists every same-shape site, not only the one the judge named.** Each
  surviving mutation is grouped by its shape (a dropped or swapped call, a flipped comparison,
  or the exact line) and its target symbol, and the verdict lists every line of the PR's diff
  with that shape, found from the diff rather than recalled by the model. The rework is asked
  to fix the whole group and to corrupt each site by hand before hand-in. Held-out survivors
  are never grouped or named. (CMX-335)

### Fixed

- **A Telegram photo is no longer reported "not submitted" when the agent got it.** The
  bridge now confirms the submit from the agent's own `UserPromptSubmit` hook, which fires
  when Claude Code takes the turn; the pane read is only the fallback. When neither confirms
  it, the reply says the submit is unconfirmed and asks you to check `/screenshot` before
  pressing Enter, since an Enter on an already-submitted prompt sends an empty or duplicate
  turn. (CMX-344)

- **The frontend gate test no longer runs the whole Vitest suite a second time.**
  `test_each_ci_gate_passes_the_committed_tree[test]` was `vitest run` again, beside
  `test_frontend_vitest_suite`, with a 180 s budget; it timed out twice in CI while xdist
  shared the runner. That case is gone, and the remaining full Vitest run over the fixture
  copy gets the 300 s budget the main suite already had (the suite's measured p95 is 112 s
  on an idle runner). (CMX-352)

- **`chela ops scan` no longer reports a live suite's test PM2 daemon as leaked.** It
  decided liveness by matching `PM2_HOME` in other processes' `/proc` environ, which an
  xdist worker never shows (conftest sets it after start-up), so a running suite's daemon
  was flagged, killed, and the suite hung to its timeout. A `/tmp/chela-tests-pm2-*` God
  Daemon is now spared while any suite of its run is live: a process in its
  `chela-suite-*` scope, one of the run's worktree in such a scope, or a test run of that
  worktree. Once no suite is live it is reported only on its second scan in a row, and the
  finding says "do not kill while a suite runs". (CMX-353)

## [0.18.0] — 2026-10-26

### Added

- **Agents review their own diff before handing in.** `chela task-finished` refuses a code
  PR whose body has no `## Review` section (prose-only PRs are exempt). An agent that cannot
  review passes `--no-review-reason "…"`, which is recorded on the run and shown to the judge
  and on the PR. (CMX-209)

- **Every judge and self-check suite run now has its own memory ceiling.** chela wraps each
  run in its own `systemd-run --user --scope` (`CHELA_SUITE_MEMORY_MAX`, default 8.3G, which
  is 3x the measured 2.75 GiB anon peak of a full suite run; `0` turns it off). A run that
  blows through its ceiling is killed inside its scope and reported as CANNOT VERIFY ("suite
  exceeded 8.3G"), never as a pass. chela claims the ceiling only when the suite's own
  `/proc/self/cgroup` names the scope. Whatever a suite leaks into its scope, such as a
  daemon that ignores SIGTERM, is SIGKILLed when the run ends. (CMX-119)
- **At most K suite runs at once, judges and agents together.** Each suite run takes one of
  K slots first (`CHELA_SUITE_SLOTS`, default half the RAM over the measured peak: 2 on a
  16 GB box). A queued run says `⏳ waiting for a suite slot`, is served first come first
  served, and is never reported as stuck, and the wait does not count against its budgets or
  the judge's wall. (CMX-119)

- **Spike: chela's merge gate and event posting as a Claude Code Mod.** `mods/chela-hooks/`
  runs the same gate script and posts the same event bodies as the shell plugin. It needs
  Claude Code 2.1.301+, chela never installs it, and it replaces nothing.
  `docs/MODS_SPIKE.md` holds the side-by-side measurements: it posts faster than the shell
  plugin, missed no events, and denied the same commands. Its recommendation is to build
  the in-session UI items as an opt-in Mod and to keep the shell plugin as the baseline.
  (CMX-157)

- **The judge keeps a failing suite's output.** Every non-green infrastructure suite run in a
  judge round (the baseline, its coverage run, and the base-branch diagnostic) is written to
  `$CHELA_DIR/judge-logs/<task>/<round>-suite-<n>.log` with the command, cwd, env diff vs the
  operator, start/end time, exit code and up to 200,000 chars of output. Before this, only a
  one-line summary survived, so a `cannot_verify` caused by a flaky baseline could not be
  diagnosed later. `chela judge show` lists each round's logs, and a `cannot_verify` verdict
  line and PR comment start with a `~`-relative pointer to them. A green baseline writes
  nothing. Logs older than 30 days, or past the newest 20 per task, are pruned whenever a
  new one is written. (CMX-163)

- **Settings > Dispatch has "Agents at once" and "Judges at once" rows.** One stepper per
  workflow sets an operator-local override of WORKFLOW.md's `concurrency.max` (Reset goes
  back to the file value), and one stepper sets the judge count, which now wins over
  `CHELA_JUDGE_MAX_CONCURRENT`. Both are bounded 1 to 8, validated by
  `/api/config/concurrency`, read on the dispatcher's next tick (no file edit, no restart),
  logged to the event log as `settings.concurrency` (who, old, new), and shown with what the
  count means for memory (N x the measured judge peak, next to available memory). Lowering
  a count never stops a running agent or judge. `judge_max_concurrent` left the generic
  Dispatch knob list for this row. (CMX-164)

- **`chela judge sweep` mutates TypeScript and TSX.** A changed `.ts`/`.tsx` file is no
  longer UNKNOWN: it gets the JS mutations (`if`, comparison flips, `return 0/1`) plus
  `&&`↔`||`, `return true`↔`false`, a ternary's condition forced each way, and JSX
  conditional rendering (`{c && <X/>}` → `{false && <X/>}`). Every mutant is parse-checked
  with the repo's own `typescript` package; one that does not parse is skipped, never counted
  as killed or survived. With no `typescript` to parse with, the sweep reports it cannot
  verify. `.d.ts` files and `*.spec.ts`/`__tests__/` files are not mutated. (CMX-173)

- **A TS mutation runs only its related Vitest tests.** When `judge.test_cmd` is one Vitest
  invocation (`pnpm exec vitest run`), the baseline also writes Vitest's JSON report, and each
  mutated `.ts`/`.tsx` file runs only the test files whose import graph reaches it, directly or
  through other modules (`./a`, `./a.js` and `./dir` resolve to `a.ts`/`a.tsx`/`dir/index.ts`).
  A TS file no test reaches is **UNTESTED**: it still runs the full suite, and a survivor there
  is reported as `[UNTESTED]`, a survivor-class finding. (CMX-174)

- **The judge and the self-check grade TypeScript.** `chela judge run`, `chela judge
  self-check` and `task-finished --self-check-experiments` apply experiments to `.ts`/`.tsx`
  files with the sweep's parse proof and Vitest test selection, within the same budgets. A
  TS-only PR gets `blocked` or `clean` like any other. If the repo has no `typescript` (or no
  node) to prove a TS file parses, the run is cannot-verify, never clean. Vitest's
  `Test Files` summary line no longer counts as tests run. (CMX-175)

- **A React + TypeScript frontend scaffold for the dashboard** in `chela/dashboard/frontend/`
  (Vite, strict TypeScript, Vitest, React Testing Library, ESLint), installed by the root
  `pnpm install` as a workspace package. dependency-cruiser rules keep each `src/features/*`
  and `src/shared/*` module reachable only through its root entry files and forbid cycles. CI
  gains a `frontend` job (typecheck, lint, module boundaries, test), and the Vitest suite runs
  inside pytest too, as do tests that feed each boundary, type and lint rule a deliberate
  violation. Nothing is wired into the dashboard page yet; users still need no Node.
  (CMX-176)

- **The dashboard frontend now builds into Flask's static dir, and the build is committed.**
  `pnpm -C chela/dashboard/frontend build` writes hashed files plus `manifest.json` to
  `chela/dashboard/static/app/`, served under `static/v/<version>/app/` with the same
  cache headers as every other asset. Because the output is tracked, a wheel, an sdist and
  a plain `git clone` + `uv sync` all serve it with no Node installed. CI and pytest fail
  when it differs from a fresh build of `src/`, and `chela release` rebuilds it before it
  commits. `chela release --dry-run` runs every release step (frontend build included) and
  leaves no commit, tag, request or push. (CMX-177)

- **The dashboard loads its first React island.** The page now loads the built frontend
  (named by the Vite manifest) before the legacy modules, and the sidebar foot shows the
  dashboard build version, rendered by React from `/api/version`. Legacy code publishes the
  agents list and the sidebar layout to a small shared store React reads, so components can
  be ported one at a time. `docs/FRONTEND.md` documents the stack, islands, the store, the
  dev loop (`pnpm dev`) and how the judge grades TypeScript. (CMX-178)

- **chela's status line as an opt-in Mod.** On Claude Code 2.1.301 or later,
  `mods/chela-hooks/` draws a status line with the context bar, the window id and an
  orchestrator or dispatched-run marker, through `$.ui.status`. After every turn it also
  writes the context cache the dashboard reads, through the same
  `scripts/cache-statusline.sh`, so loading the Mod needs no `settings.json` edit.
  `chela install-statusline` is unchanged and stays the default. The Mod's event posting
  now runs only when `CHELA_MOD_HOOKS_URL` is set, so a chela pane that loads the Mod does
  not file each event twice. (CMX-181)

- **A "needs you" band in the opt-in Mod.** With `mods/chela-hooks/` loaded (Claude Code
  2.1.301 or later), the orchestrator session shows one line above the prompt with the
  pending override approvals, access requests and runs in `needs_human`: counts plus the
  oldest, each linking to the dashboard. It polls the new read-only `GET /api/needs-you`
  every 10 s, shows nothing when the daemon is down, and hides a reading older than 30 s.
  Approving stays in Telegram, the dashboard or a plain terminal. (CMX-183)

- **Classifier denials reach the inbox straight from the opt-in Mod.** With
  `mods/chela-hooks/` loaded, a `classic.PermissionDenied` is POSTed to the new
  `POST /api/inbox/denial`, which files CMX-155's orchestrator-inbox notice (tool, one-line
  reason, session, window) without tailing the event log. With `plugin/` loaded too, one
  denial is still one notice: both copies are keyed on its `tool_use_id`. A dead daemon
  never affects the tool call. Under `--permission-mode dontAsk` no denial fires in either
  layer. `plugin/` drops nothing yet (`docs/MODS_SPIKE.md` §8.0). (CMX-184)

- **Settings > Telegram topics.** "Telegram: delete a topic when its window closes" (off by
  default) makes the bridge delete a dead window's topic instead of archiving it, falling
  back to archive if the delete is refused. "Delete archived topics (N)" deletes every
  remembered archived topic after a confirm, never a bound one or General. Both apply on
  the bridge's next tick, no restart. (CMX-204)

- **Risk tiers for the merge gate.** A PR whose every changed file is prose (text under
  `docs/`, a `changelog.d/` fragment, a top-level `*.md` other than `WORKFLOW.md`/`CLAUDE.md`/
  `AGENTS.md`, or an image or video) gets no judge round. The dispatcher records the verdict
  `prose_only` on its head, and `chela merge` accepts it once CI is green and GitHub says
  MERGEABLE. A PR that touches an always-human path (`WORKFLOW.md`, `.github/workflows/**`,
  `plugin/**`, the merge gate and release code, plus any workflow's `merge.always_human`)
  needs an operator-approved `--override` even with a clean judge, and both the refusal and
  the override question name the path. If the file list cannot be read, the PR is never
  treated as prose. (CMX-208)

- **`chela dispatch --pause --all` quiets the whole fleet.** It holds the queue, then
  presses Escape in each dispatched agent's window and tells it to start nothing until
  "resume". An agent inside `task-finished`, `judge sweep` or `judge self-check` gets the
  message without the Escape, so it finishes that command first. `--resume` sends "resume"
  to each agent it paused, and `--hold-status` lists them. Judges already running still
  finish. (CMX-214)

- **`chela ops scan`: a read-only anomaly scan of the fleet.** Six rules, each from a real
  incident: a test run sitting at ≤1% CPU for 20 minutes, a process left behind under a
  finished or orphaned run's worktree, a CI red that names no job or fired while checks still
  ran, the same test failing the judge baseline of two tasks in a day, a judge whose survivors
  move to a new file every round, and the same notice for one run three times in an hour. It
  prints findings with their evidence (`--json` for scripts). The daemon runs it every 30
  minutes as a niced, detached child with a 10-second budget, skips it during a fleet pause,
  appends findings to `~/.chela/ops/findings.jsonl`, and sends the orchestrator one inbox
  notice only when a finding is new (or still open after 6 hours). (CMX-260)

- **Known-flake quarantine.** `tests/flaky.toml` registers flaky tests, each with a tracker
  issue and an expiry date. When a PR's CI is red only on registered tests, the dispatcher
  re-runs the failed jobs up to 2 more times, after CMX-255's one re-run. If it is still red
  only on those tests, the PR is not sent back for rework: the run goes to the judge, records
  `ci_known_flake`, and the inbox notice names the issue. The flaky test still runs and
  reports. The registry is read from the orchestrator's own checkout, so a PR cannot
  quarantine its own red. `chela merge` still needs green CI, so a known-flake PR is judged
  but must be re-run green (or overridden) before it merges. A CI test fails when an entry is
  expired, malformed, or its issue is Done, Canceled or missing. `chela ops scan`'s
  `repeat_flake` rule names a "quarantine candidate" when the same test flakes on 2 or more
  PRs within 48 hours. (CMX-282, #797)

- **The nightly mutation sweep's scheduler (1 of 3).** Once a day (WORKFLOW.md
  `sweep.nightly_at`, default `02:00` local; `off`, `never`, `false` or `sweep.enabled: false`
  turns it off) the daemon spawns `chela judge nightly-sweep`: one at a time, outside the
  dispatch slots and in the judge's shared memory slice. It fetches `origin`, reads
  `origin/<workspace.base_branch>`, and sweeps the lines that still blame to a STANDARD-tier
  commit merged in the last 24 h (core, always-human and prose merges are not swept). After a
  night that could not verify (or left mutations unrun), the next one catches up from the last
  night that did, at most a week back. Each surviving guard is handed to a filer once; the filed keys live in
  `$CHELA_DIR/sweep-nightly/filed.json` and survive restarts. Until the filing slice lands,
  the filer files nothing and records what it would have filed in
  `$CHELA_DIR/sweep-nightly/would-file.jsonl`. It is skipped while
  `chela dispatch --pause --all` is active. Its budget is `sweep.budget_seconds` /
  `sweep.max_mutations`, else the judge's sweep budget. (CMX-303)

- **The nightly mutation sweep groups its survivors (2 of 3).** Survivors are grouped by
  category — the mutation's shape plus the guard line's stripped text — so one weak guard
  copied to five files is one group, and one line that survives two shapes is two. Each group
  lists every same-shape site in the checkout, found with a fixed-string `git grep -F` (a
  line holding `.*[(` or `v[0]` keeps all its sites). A group whose sites cannot be listed, or
  whose listing misses one of its own survivors, is not filed, and the next night retries it.
  A night with no survivor that left mutations past the budget now reports `unrun`, and one
  with a merged file no mutator handles reports `unknown`. Neither reports `clean`, and
  `chela judge nightly-sweep` exits 2 for both. (CMX-304)

- **The nightly mutation sweep files its survivors and tells the inbox (3 of 3).** Each
  survivor group becomes ONE tracker issue, labelled `risk:normal` and `sweep`, listing every
  mutation that survived and every same-shape site. A group is skipped only when every
  surviving mutation in it was filed before; a new site of a filed category files the group
  again. The filed keys are written after each issue, so a filing that fails part-way keeps
  the ones before it. A tracker that cannot create issues records what it would have filed in
  `$CHELA_DIR/sweep-nightly/would-file.jsonl` instead. Each run queues ONE inbox notice: the
  issues filed (or "nothing filed"), already filed, not filed and why, clean, or skipped and
  why, with the run's UNRUN and UNKNOWN counts. (CMX-305)
- **Linear's `create_issue` takes label names.** They are added to a parent's inherited labels,
  never in place of them. A missing label is created on the team, and is used only when Linear
  answers `success: true`. A label that can be neither found nor created is a warning, and the
  issue is still created. (CMX-305)

- **Standing agent rules.** A workflow can name a short rules file with
  `agent.standing_rules: <path relative to the repo>`, and the prompt body renders it
  wherever `{{standing_rules}}` appears; the built-in rework prompt carries it too. A missing or
  unreadable file renders as nothing and logs a warning instead of failing the dispatch.
  chelamux seeds `docs/AGENT_STANDING_RULES.md` with the rules the orchestrator used to
  repeat to each agent by hand. (CMX-317)

- **`chela park <run> --reason "…" [--until <time|run:ID>]` and `chela unpark <run>`.** An
  agent the orchestrator holds on purpose no longer draws "looks STUCK" notices: the stuck
  detector skips a parked run and sends ONE notice if the park outlives its `--until` or
  `CHELA_PARK_MAX_S` (default 6h). A run waiting for a suite slot counts as parked
  automatically ("waiting for a suite slot (holder: …)"). Parks show in `chela dispatch-runs`
  and on the Work card's flags line and the desktop Rows table's state column ("parked · <reason> ·
  <since>" or "waiting · …", dim, never orange: the hold is on purpose). An agent cannot
  park itself: the command refuses inside the run's own worktree and from any Claude Code
  session that is not the orchestrator. (CMX-318)

- **A Linear workflow can be scoped to one project of a team (`tracker.project`).** Every
  read and write it makes (the open set, the ID refresh, state writes, New task, size-gate
  splits) is limited to that project, and issues it creates go into it, so several repos
  can share one Linear team. Two dispatched workflows on one team must each set a
  different `project:`, or both refuse to claim work and `chela doctor` says why. The
  archive sweep stays team-wide, runs once per team, and keeps the largest `keep_done`
  set on the team. (CMX-326)

- **`scripts/check_fresh_install.sh`** runs the README install block verbatim in a clean
  Ubuntu container (docker or podman, as your own uid, a stub `claude`), times it, and
  checks the dashboard answers and `chela doctor` passes. The test suite runs it wherever
  docker or podman answers; without one it exits 77 and the test reports SKIPPED. (CMX-92)

- **A one-minute demo of the merge gate.** An agent opens a PR, the judge corrupts its guard
  and the PR's test stays green, `chela merge` refuses, the agent adds a boundary test, the
  judge comes back clean and the merge goes through. The alternative ending is one Telegram
  tap on an override. The clip runs chela's real judge, merge gate and Telegram card against
  a throwaway repo, with only GitHub faked. It is in the README and on the landing page, and
  `scripts/demo/record-gate.sh` re-records it. (CMX-93)

### Changed

- **The phone's session pills follow the sidebar's order**: group order, Move up / Move
  down, custom groups and each group's sort, shared by every device. A session the sidebar
  collapses or filters out keeps its pill at its group's position, and one the sidebar has
  not drawn yet goes at the end. The header swipe steps in the same order. (CMX-165)

- **The frontend's Vitest suite runs 4 workers, not one per core minus one.** On a 20-core
  box that default was 19 jsdom workers and 4.3 GB, most of a full test run's peak. (CMX-119)

- **The task modal's review timeline moved to the wide column.** The main column now has
  **Brief | Review (N)** tabs, and the modal opens on Review while a verdict is waiting to be
  read. Each review round is a collapsible card, newest first and open. A collapsed round
  shows its verdict, its visible and held-out survivor counts, and its time. The sidebar
  keeps the details and adds a round list that jumps to a round. On a phone the sidebar
  stacks above the tabs, so the modal has only one scroll area. On a Linear workflow, the
  task key in the header links to the Linear issue. (CMX-122, #721)

- **The judge Action is pinned to a release tag, not `main`.** The README now uses
  `Devail1/chelamux@v0` and says to pin `@v0.17.0` or a commit SHA for a reproducible job.
  `chela release X.Y.Z`, after the approved push of `main` and `vX.Y.Z`, force-moves the
  major tag (`v0` for 0.y.z, `vX` in general) to the release commit under the same approval.
  It is the release flow's only forced push and touches only that tag. If the move fails, the
  release stays pushed and chela reports it in the log, event log, notification and inbox.
  `chela release X.Y.Z --major-tag-only` points the major tag at an existing release (used
  once to create `v0` on 0.17.0). (CMX-158)

- **The launch pages read as written, not generated.** Every em dash (and spaced en dash)
  in the landing page, the docs page, the README, `action.yml`, every skill, the five docs
  the landing links (Getting started, Config, Hooks, Trust and approval posture, Risk
  levels) and the dashboard's visible copy was rewritten sentence by sentence, as a
  period, colon, comma or parentheses. `tests/test_no_em_dashes.py` goes red if one comes
  back. (CMX-160)

- **`orchestrate` asks for blocking edges.** Every task an orchestrator files declares its
  blockers as native tracker links (Linear *blocked by*, or `depends:` in `TODO.md`), and a
  multi-slice task is filed as one chained issue per slice under a tracking parent. Adapted
  from `to-tickets` in `mattpocock/skills`. (CMX-187)

- **`chela task-finished` refuses a branch that is behind or conflicts with `origin/<base>`.**
  It fetches the base first, and before the size gate, self-check and sweep it refuses a
  branch that would not merge cleanly (listing the conflicting files) or that is behind
  (with the exact `git fetch` / `git merge` commands; no rebase). `--allow-behind` lets a
  deliberate behind-but-clean hand-in through, recorded on the run and shown to the judge.
  A remote that cannot be reached fails closed. (CMX-189)

- **The rework cap counts only review rounds.** A judge block or a reviewer's changes
  requested still spends a rework round. A CI send-back, or a rework agent that died without
  handing in (window gone, stalled, launch failed), no longer does: those are counted as
  `infra_restarts`, which has its own cap (`max_infra_restarts` /
  `CHELA_MAX_INFRA_RESTARTS`, default 8). Reaching that cap escalates with a reason that
  names infra instead of "the PR still fails review". A `chela retry` doesn't spend
  either counter. (CMX-192)

- **Telegram auto-topics reuse a folder's old topic instead of creating a new one.** chela
  now remembers every topic it creates (`$CHELA_DIR/telegram-topics.json`: thread, window,
  name, cwd, created and closed time). A new agent window first reopens a closed topic made
  for the same cwd, renaming it only if the name changed, and creates one only when there
  is none (or the reopen fails, which drops the stale entry). Two live windows in one folder
  still get two topics, and both come back next time. (CMX-204)

- **`chela dispatch --pause --all` now freezes running judges too.** Each judge's process
  tree gets SIGSTOP, and `--resume` sends SIGCONT. Time spent frozen does not count against
  the judge's suite deadline or the daemon watchdog's wall. The watchdog leaves a frozen
  judge alone, and after a thaw its progress clock starts again from the thaw. A reaped judge
  tree that was stopped also gets SIGCONT, so it can act on its SIGTERM. `--hold-status`
  lists each suspended judge. (CMX-214 slice 3, CMX-251, #775)

- **The dashboard's JS suites (`tests/*.test.mjs`) run under Vitest** instead of
  `node --test`, through the root `vitest.config.mjs` (one process per file, as before), so a
  dashboard module can later import a ported TypeScript one. pytest still runs each file as
  its own item with the same fences: a missing Vitest is a loud skip, or a failure under
  `CHELA_REQUIRE_JS_TESTS=1`, and a Vitest run that finds no suite is red. The collab and
  browser suites stay on `node --test`. (CMX-216)

- **The dashboard's data layer is TypeScript (CMX-212 P03).** The refresh loop, the one
  Server-Sent Events stream, the HTTP client and the page state moved from `main.js`,
  `sse.js` and `util.js` to `frontend/src/shared/` (`scheduler`, `events`, `api`, `store`).
  Each feature registers its own polls and event handlers, the page reads its config from one
  JSON block instead of three inline globals, and legacy imports are extensionless. With
  `CHELA_TERMINALS_ENABLED=false` the page now really runs without terminals: it used to read
  the missing global as "on". (CMX-217)

- **The dashboard's chrome is React now**: the approvals and access-request pills and sheets,
  the agent-status warning, the CPU/RAM/Disk strip, the run toasts, the "chela was updated"
  banner, the New session menu (with its favorites picker), the gear menu with its usage
  readouts, the phone "+", and the agent detail with its send-message dialog. They look and
  behave as before, with three differences: the menus and sheets are real keyboard menus and
  dialogs (focus moves in, Escape closes, focus returns), their rows are buttons you can tab
  to, and the icons are Lucide icons instead of emoji. (CMX-219, part of CMX-212)

- **Settings, the ⌘K palette and the keyboard shortcuts sheet are React now**: every Settings
  tab (General, Timing, Dispatch, Notifications, Cost, Appearance, Collaboration) and its
  search, the palette (also driven from the sidebar's "Jump to session" box) and the shortcuts
  sheet. They work as before, with these differences: Settings and the palette are real
  dialogs (focus moves in and returns, Escape closes the top one); the tab rail is buttons you
  reach with Tab and move along with the arrow keys; the selects and switches are the kit's
  (keyboard menus and switches); a narrow Settings stacks each label above its control; and
  clicking the Cost tab's view or window you are already on refreshes it. (CMX-221, part of
  CMX-212)

- **The Decisions inbox and the task-detail modal are React (CMX-212 P07-P09).** The inbox
  button, its owner dot and unread badge, the log dialog (owner chip, re-register, search,
  click-through) and the task modal (Brief and Review tabs, rounds, details) run on the
  dashboard's UI kit: real dialogs with focus trapping, and keyboard tabs. The re-register
  picker and button are the kit's Select and Button. (CMX-228)
- **The owner chip in the Decisions inbox has its border and padding back.** A comment in
  the stylesheet closed early and swallowed the chip's own rule. (CMX-228)

- **Work's Runs and Schedules segments, New task and Init a repo are React now.** They look and
  behave as before, with three differences: the dialogs are real keyboard dialogs (focus moves
  in, Escape closes, focus returns to the button that opened them), the form fields line up in
  one column, and the icons are Lucide icons instead of symbols. (CMX-231, part of CMX-212)

- **The Work view is React now: the board, its cards and actions, the project filter, the phone
  layout and the toolbar.** It looks and behaves as before, with three differences: a board
  column keeps a normal width (about 400px at most), so one visible column no longer stretches
  across the screen and many columns scroll sideways instead of squeezing; the project pills
  are a multi-select (pick any set of projects; All clears it, and the choice is remembered);
  and the card chips use Lucide icons instead of symbols. (CMX-233, part of CMX-212)

- **The Wall's tile model, wire geometry and window-id parsing are TypeScript (CMX-212 P22).**
  `wallmodel`, `wire` and `windowid` are converted in place under `static/js/`, typechecked
  strictly with the frontend. A room badge on a Wall tile now shows a plug icon instead of the
  🔌 emoji. (CMX-236)

- **A plain shell pane says it is a shell.** A shell opened from the dashboard showed its tmux
  window name (`liavedunix-2`) and an `idle` chip, so it looked like an idle Claude session.
  Until Claude starts in it, its pane header now reads `Shell · <cwd>` with a `shell` chip;
  once `claude` runs there, the header shows the session's name and state as before. The tmux
  window is never renamed (its name feeds the Telegram topic and `--remote-control`).
  `/api/agents` carries the pane's cwd as `shell_cwd` (home written `~`) while no Claude runs
  in it. (CMX-241)
- **The Wall's pane header is React** (CMX-212 P26): the title and rename, the state pill and
  status dot, the web chip and UNSANDBOXED banner, the ⋯ menu (Wire, Share, Orchestrator, Pin,
  now keyboard-reachable), minimize, maximize, the phone's fill-screen button and kill with its
  confirm. Its share-mode marks are icons instead of emoji. (CMX-241)

- **The pane footer shows in every pane mode.** A pane filling the phone's screen hid its
  footer; it now keeps the window id, model, branch and context % there too, as on a wall
  tile and a maximized pane. (CMX-242)
- **The focus ring no longer hides the context bar.** The thin context-% strip along a pane's
  bottom edge sat under the focused pane's highlight, so it vanished on exactly the pane you
  were looking at. It is drawn inside the ring now. (CMX-242)
- **The Minimized tray is one row.** It used to wrap to a second and third row as panes were
  minimized, taking height from every pane on the wall. Its chips now scroll sideways (a
  vertical mouse wheel scrolls them too), a fade marks the edge that hides more, and the label
  counts them: `Minimized (5)`. (CMX-242)
- **The rest of the Wall's chrome is React** (CMX-212 P27, P28, P30, P32): the pane footer
  (the window id that copies itself, model, PR, cost, branch, context % and the Files chip),
  the Answer / Approve / Review bar and the recap line; the Minimized tray and the lazy dock for
  dispatched workers; the layout toolbar (presets, lock, auto-arrange, Focus); and the wire
  that puts two agents in a room. The footer's branch and PR flag, the action bar's marks and
  the wire's toast are icons or plain text instead of emoji. (CMX-242)

- **The dashboard page is one React root.** `templates/index.html` is now its `<head>`, the
  pre-paint sidebar restore, the boot block and `<div id="root">`; the app shell
  (`frontend/src/features/shell`) renders the sidebar, the canvas, the phone controls, the
  safety float and the overlays, with the same classes and ids, so the look does not change.
  The view switch, the view registry and the tab title and favicon badge moved to TypeScript
  with it, and the last plain-JS modules (`nav.js`, `views.js`, `viewreg.js`, `util.js`,
  `icons.js`) are gone. `style.css` is the build entry's stylesheet, linked from the manifest.
  `window.chela` holds only what a terminal pane calls, `openPalette` and `toggleSettings`.
  (CMX-248, CMX-212 P34)

- **A red CI re-runs its failed jobs once before sending a PR back.** On the first red on a
  head commit the dispatcher runs `gh run rerun <id> --failed` and spends no rework round. If
  the re-run goes green, the flaky test is recorded on the run (`runs.ci_flakes`) and the
  orchestrator gets one "CI flake, re-run green" notice. If it goes red again, the PR is sent
  back for rework as before, and the verdict says the re-run failed too. At most one re-run
  per head sha; a new push resets it. A re-run GitHub will not start falls straight through
  to the rework. (CMX-255)

- **The judge no longer blocks on survivors in tool config that CI enforces.** A mutation
  that survives in `eslint.config.*`, `.dependency-cruiser.*`, `tsconfig*.json`,
  `vite.config.*`, `vitest.config.*`, `package.json` `scripts` or `.github/workflows/*` is
  listed in the verdict as advisory. It still blocks when a test the PR changes names that
  file, because a guard the PR claims must be real. Code experiments now run before
  tool-config ones in the judge and in `chela judge self-check`, so a budget runs out on
  config first. `judge.tool_config` in WORKFLOW.md replaces the list, and `[]` turns the
  policy off. (CMX-259)

- **A dispatched PR that goes CONFLICTING no longer just sits.** Once GitHub has said
  CONFLICTING on two ticks in a row, the dispatcher merges `origin/<base>` into the PR's branch
  in a scratch worktree (never the run's own). A clean merge runs the targeted tests (the PR's
  own test files plus the tests importing the files it touches); if they are green, the merge
  commit is pushed and noted on the PR ("base merged automatically"), and the judge grades the
  new head as usual. A real conflict, or a merge whose targeted tests go red, goes back to the
  agent as a rework that does not count against the rework cap, with the conflicting files
  listed. A textual conflict is never resolved automatically. Once per head sha. (CMX-262)

- **The CONFLICTING-PR auto-merge (CMX-262) is now wired into the dispatcher.** At most three
  conflict reworks per run are free; past that a conflict round is charged against the rework
  cap like any other, so a run that cannot resolve its conflict still reaches a human. (CMX-264)

- **The sidebar's session list is React now, with title-first rows.** Each row gives its title
  the width; the metadata beside it is icons with tooltips (a crown for the orchestrator, a
  pull-request icon and number, a flask and `3/6` while a judge tests, the context % from
  50%). The row no longer spells out its state: the dot's shape carries it, and its tooltip
  and screen-reader name say it. The window id shows on hover. The Dispatched group is always
  the last group. (CMX-276, part of CMX-172)

- **One control for archived sessions.** When anything is archived, the sidebar shows a
  collapsed `Archived · N` group at the bottom; its open or closed state is remembered on each
  device like any other group. The "Show archived" / "Hide archived" link under the list is
  gone: View ▸ Status ▸ Active hides the group entirely, and All (now the default) shows it. A
  view saved before this release keeps the Status it saved: pick View ▸ Status ▸ All to see the
  group. (CMX-277, part of CMX-172)
- **The sidebar's group, row and View menus are keyboard menus now.** Each `⋯` and the View
  button is a real button: Enter or the arrow keys open its menu, the arrows move, Enter picks,
  Escape closes. A right-click on a group header or a row still opens its menu. The View menu's
  custom groups page lists `Rename <group>` and `Delete <group>` as items of their own.

- **The sidebar is resizable.** Drag its right edge (or focus the edge and use the arrow keys,
  Home and End) to widen or narrow it; a double-click restores the default. The width is saved
  with the sidebar layout, so it follows you to your other devices. The default is a little
  wider than before (288px). The phone drawer is unchanged. (CMX-278, part of CMX-172)
- **Hover over a session row, or press and hold it on a phone, to read its whole title.**
- **Recent sessions never lists a session that is running.** A session resumed after a restart
  is matched by its session id, so it no longer shows as closed under its old window address.
  A judge's session never offers Resume: it sits under "Show hidden dispatcher sessions", since
  chela owns its lifecycle.
- The rest of the sidebar (the Wall and Work rows, Recent sessions, the collapse control and
  Alt+1..9) now runs on the same React code as the session list. Nothing else changes.

- **The sweep and the judge no longer mutate generated files.** One shared list
  (`chela/generated.py`: the committed `chela/dashboard/static/app/**` bundle, `uv.lock`,
  `chela/dashboard/frontend/pnpm-lock.yaml`, `**/*.min.js`) is excluded from `chela judge
  sweep`'s mutation candidates and from the judge's experiments, and the hand-in size gate
  never counts those files. Each round says how many it skipped. A React port slice used to
  spend almost all of its sweep budget on minified JS that no test can guard; CI's
  "committed build is fresh" step is that bundle's guard. (CMX-284)

- **A `standard` risk tier: product and UI PRs get a judge round with no held-out experiments.**
  `chela/risktier.py` now sorts a judged PR into `core` (it touches the dispatcher, the judge,
  the workflow loader, the sources or `chela/context.py`; a workflow adds paths with
  `merge.core`) or `standard` (everything else). A standard run asks the judge for no held-out
  experiments and runs any it tags anyway as visible; a core run keeps today's quota. The tier
  is stamped on the run and shown by `chela judge show`. Prose and always-human are unchanged.
  (CMX-288)

- **A clean verdict survives a merge from the base branch.** When a judged PR's head moves
  and the move touches none of the PR's own files, the dispatcher keeps the clean verdict,
  re-stamps it on the new head with the reason `carried: base-only merge`, and spawns no
  judge; `chela merge` accepts it. When a PR file did change, the re-judge re-runs the
  previous round's experiments first and the judge is asked for new ones only on the lines
  changed since, instead of drawing a fresh sample of lines already judged. (CMX-289)

- **A `standard`-tier PR gets no judge round.** A PR that touches no core path (the
  dispatcher, the judge, the workflow loader, the sources, `chela/context.py`) and no
  always-human path is stamped `standard_no_judge` and is merge-ready on green CI +
  MERGEABLE, like `prose_only`. `chela merge` accepts it only while its live diff is still
  standard. Its `chela task-finished` runs `hand_in.lint_cmd` and one full suite, with no
  self-check battery and no sweep. Core PRs are unchanged, and the core set grows to cover
  the rest of the merge and containment machinery (`automerge`, `overrides`, `gateanswer`,
  `reviewgate`, `sizegate`, `basesync`, `sweep*`, `toolconfig`, `sandbox*`, `procguard`)
  and any `CLAUDE.md`/`AGENTS.md`, since those no longer meet a judge as standard. A workflow keeps the judge on
  every PR with `merge: {core: ["*"]}`. (CMX-296)

- **The phone pane looks like the Claude mobile app.** The pane is full-bleed (no margin,
  rounded border or focus outline) and fills the screen from the top to the key bar. Its header
  is the one title bar: the drawer's hamburger on the left, the session name over its subtitle,
  and the status chip and ⋮ on the right. Close moved into the ⋮ menu, maximize and the
  fill-screen toggle are gone (the pane is always full), and the floating "+" is removed: New
  session is in the sidebar drawer. Desktop is unchanged. (CMX-313)

- **The Work view on a phone has one title bar.** The six rows of controls above the board
  (coloured buttons, project pills, a layout toggle, column chips) are gone. The bar holds the
  drawer's hamburger, the current project as the title with its running and queued counts, New
  task and a ⋯ menu. Tapping the title opens a project switcher sheet with each project's counts
  and an "N needs you" mark. ⋯ holds the layout, Init a repo, Pause dispatch and Pause all agents,
  which now asks to confirm. Rows (one list grouped by status) is the phone's default; the Board
  layout swipes one column per screen under underlined status tabs. Cards are compact: the
  title, one id/PR/state line and one flags line, with the branch, delete and Close & requeue
  moved into the card's ⋮ menu. Desktop is unchanged, except that a queued task's card now
  shows its risk label there too. (CMX-315)

- **The Work view on desktop has one header row.** The two rows above the board (the tabs
  with four coloured buttons, then the project pills) are one: the current project as a
  dropdown (search, All projects, one row per project with its running and queued counts, and
  "dispatch on" or "paused" under the title), Board | Runs | Schedules, Pause dispatch as a quiet
  outline that reads Resume while held, New task as the one primary button, and a ⋯ menu with
  Init a repo, Pause all agents (which now asks to confirm), the Board | Rows layout and the
  board's column options. On the board, Todo, In Progress and In Review are fixed 320px columns;
  Backlog and Done (and Archived) collapse to narrow rails at the edges and open on a click,
  and the board remembers which ones you left open for each project. The "Hidden columns"
  panel is gone: hide a column from ⋯ › Column options. Cards are the phone's compact card:
  the branch, merge, delete and Close & requeue live in the card's ⋮. Rows on desktop is a
  table (ID, title, state, PR, CI, risk, age) grouped by status, sortable by any column head and
  walkable with the arrow keys. (CMX-316)

- **Hook events store a preview, not the tool's body.** Each `tool_input` /
  `tool_response` value keeps 200 characters, and `clipped_bytes` records the original
  sizes, so a 1 MB Write is a ~1 KB line instead of a ~2.5 KB one. An `AskUserQuestion` /
  `ExitPlanMode` input is still kept whole for the Telegram gate. (CMX-328)

- **`chela task-finished` no longer re-runs its whole battery when the base moved during it.**
  When `origin/<base>` moved while the self-check and sweep ran and merges cleanly, the hand-in
  merges it in, runs only the tests that observe the files the new base commits touched, and
  re-runs the self-check experiments only on PR files those commits also touched. A red test,
  a surviving experiment or a git error still refuses. The judge is told the base was merged
  mid-check. (CMX-334)

- **One-command install.** The README and docs/GETTING_STARTED.md now install with
  `uv tool install "chelamux[dashboard] @ git+https://github.com/Devail1/chelamux"`, then
  start tmux, the daemon and the wall: four commands, with the wall up about 15s after the
  first on a clean Ubuntu box. `gh`, Telegram, Linear and pm2 are follow-ups, not
  prerequisites. (CMX-92)
- **`chela doctor` separates required from optional checks.** Telegram and Linear read
  "not configured (optional)" when absent, an error from either no longer fails the run,
  and a clean run ends with `required checks: all pass`. (CMX-92)

### Removed

- **The generic planning skills are retired.** `blindspot-pass`, `interview-me` and
  `implementation-plan` were adaptations of widely used skills, so chela no longer ships
  them. Use `grill-me`, `to-spec` and `wayfinder` from Matt Pocock's `mattpocock/skills`
  instead; docs/GETTING_STARTED.md now has a "Recommended skills" section. chela keeps
  `chela-setup`, `orchestrate`, `handoff`, `telegram-setup` and `telegram-send`. (CMX-187)

- **Terminal reply timestamps are retired.** Claude Code's own turn footer now prints the
  completion time on every reply ("✻ Brewed for 7s · done 8:55 AM"), so chela no longer
  stamps `**[HH:MM]**` into replies. `CHELA_TERMINAL_TIMESTAMPS` is gone and is ignored if
  set, and the plugin no longer registers a `MessageDisplay` hook (plugin 0.2.12). The
  unreleased timestamp band in the opt-in Mod (`mods/chela-hooks`, CMX-182) is removed with
  it; the Mod's status line is unchanged. 0.x carries no compatibility promise. (CMX-266)

### Fixed

- **Terminals reconnect by themselves.** A wall tile whose socket dropped (iOS suspending a
  background tab or a locked screen, a wifi/4G switch) no longer stops at ttyd's "Press ⏎ to
  Reconnect". While visible it retries after 0.5 s, 1 s, 2 s, 5 s, then every 10 s; while
  hidden it waits until the page is visible again or the network returns. After repeated
  failures a quiet "Reconnecting…" line shows, and Enter still retries at once. A frame chela
  suspended on purpose stays suspended. (CMX-167)

- **A Telegram photo is only reported "sent" once the agent actually submitted it.** The
  bridge now re-reads the agent's prompt after delivering a photo and presses Enter once more
  if the `[Image #N]` chip is still sitting there. If the prompt never clears, the topic gets
  "⚠️ Image delivered but not submitted" instead of "📎 Image sent". A text, photo or file
  that arrives while the prompt still holds an unsubmitted message is refused with a notice
  instead of being merged into it, and a turn that carries the inbound `📎 image:` marker
  anywhere is no longer echoed back to the topic. (CMX-117)

- **Human sessions come back even when tmux restarts twice in a row.** The boot restore pass
  used to look only at the epoch that just ended. If tmux died again before the roster had
  sampled the relaunched windows, that epoch was empty and the orchestrator stayed dead (the
  inbox sat held until someone relaunched it by hand). The pass now walks back to the newest
  ended epoch that recorded any windows, and no further. It also carries the sessions it
  resumed itself in `restore-boot.json` until a later epoch samples them live, and never
  relaunches a session that is already live. The inbox notice says "tmux restarted twice;
  recovered from epoch X" when it used an older epoch. (CMX-152)

- **Phone: the terminal fits its tile on first view and after a tile switch.** With the keyboard
  open, iPhone Safari shrank the pane but the terminal inside kept its taller row count, so a
  multi-line prompt showed only its first lines and Claude's status line was clipped below the
  tile footer until you scrolled. The dashboard now re-fits the pane's terminal itself after
  every phone fit and whenever a pane's terminal finishes loading. Desktop is unchanged. (CMX-154)

- **A killed `chela judge self-check` or `judge run` no longer leaves a mutation in your
  files.** The judge's apply → measure → restore loop now uses the sweep's signal handling
  and kill guardian. SIGTERM, SIGHUP and SIGINT restore the file before the process exits,
  and after a SIGKILL a guardian process restores it from the recovery marker. (CMX-162)

- **The merge gate also denies GitHub MCP merge tools, not only `Bash`.** A session with the
  GitHub MCP (the official GitHub plugin, for one) could call `merge_pull_request` on a
  dispatched PR and skip the judge, because the `PreToolUse` gate was matched to `Bash`
  only. A new `mcp__.*` entry runs the same script, which denies `merge_pull_request`, and
  `push_files` / `create_or_update_file` / `delete_file` to a protected branch, in a chela
  repo named by the call's own `owner`/`repo`. Every other MCP tool is allowed. Plugin
  0.2.10: re-install it so existing sessions pick up the new hook. (CMX-180)

- **A dispatched run can no longer launch another Claude Code agent.** Agents and judges run
  unsandboxed, and a dispatched agent routed around a classifier denial by relaunching
  `claude` in another permission mode on a private tmux server. The dispatcher now exports
  `CHELA_RUN_ID` / `CHELA_RUN_ROLE` into every pane it spawns, and the plugin's `Bash` hook
  denies a `claude` launch there, however wrapped (`tmux new-session "…"`, `env`, `nohup`,
  `timeout`, `bash -c`, `npx @anthropic-ai/claude-code`, …). `claude --version`,
  `claude plugin test|validate` and `claude mcp list` stay allowed; human sessions and the
  orchestrator are unaffected. Plugin 0.2.11. (CMX-190)

- **The judge wastes fewer rounds on "cannot verify".** A baseline that is red only because of
  named test failures now re-runs just those tests once. If they all pass, the judge records
  them as flakes (`baseline_flakes` in the private round record) and the round continues. A
  test that fails both times is still red. A suite killed at its timeout now says `TIMEOUT`
  with the elapsed time and host load, instead of "NOT GREEN", and no longer spends a second
  full run on the same budget. A round where the judge proposed no experiments is parked for a
  human once instead of being retried with the same inputs. A verdict that arrives after its
  run was already merged or closed is still recorded, but no longer reaches the inbox as an
  action item. `test_zz_no_pm2_daemon_survives_this_files_tests` no longer counts another
  concurrent suite's live test daemon as its own leak. (CMX-195)

- **An override approval survives GitHub's `UNKNOWN` mergeability.** Approving an override
  right after the base branch moved used to fail on `mergeable: UNKNOWN` (GitHub computes it
  lazily) and burn the approval. The merge now polls for up to ~30s; if GitHub still has not
  decided, the request stays `approved` for the same head and the daemon retries it at most
  once a minute with no new human approval (a new push still expires it). `CONFLICTING`
  still fails as before. A PR that is already merged answers "already merged as <sha>"
  instead of announcing a failure. `chela override list` shows approved requests. (CMX-205)

- **Three tests that failed only under load now pass every time.** Two judge tests could
  resolve their judge worktree to the operator's real `~/.chela/worktrees/default`, so they
  failed whenever another suite running at the same time held the judge lock there. Five
  more `_spawn_judge` tests had the same hazard and are fixed too. The peer-transport doctor
  test bound a socket whose path ran past the 108-byte AF_UNIX limit on xdist worker gw10
  and above. (CMX-257)

- **A daemon restart during a judge launch no longer costs a judge round.** On SIGINT/SIGTERM
  the daemon finishes the launch already in progress and launches nothing new: no judge, no
  rework, no claim. A launch interrupted anyway (a SIGKILL after PM2's `kill_timeout`, or a
  launch whose hook the restart's signal killed) no longer reads as "the judge's window
  disappeared" CANNOT VERIFY. The next daemon relaunches it as the same round, and no retry
  is spent. The run row records who is launching its judge (`judge_launch_owner`) until the
  launch is through. `examples/ecosystem.config.js` now gives `chela-daemon`
  `kill_timeout: 90000` and `treekill: false`. Copy both into your own ecosystem file, then
  `pm2 delete chela-daemon && pm2 start <ecosystem> --only chela-daemon` (a plain restart
  keeps the old kill settings). (CMX-263)

- **A prose-only PR no longer waits for a judge slot.** The prose-only check used to run
  only once a judge slot was free, so a docs-only PR could sit "queued for the judge" behind
  running judges and `chela merge` refused it with judge "never ran". The trigger now
  classifies the PR's head commit before the slot gate, and a prose-only PR gets
  `prose_only` on the next tick however many judges are busy. (CMX-265)

- **A prose-only PR's inbox notice no longer says "merged".** It fired when the verdict was
  stamped, while the PR was still open, so a pending PR read as done. It now says the PR is
  merge-ready on green CI with no judge round needed, and names the `chela merge` command that
  lands it. (CMX-272)

- **A dispatched agent's leftover test runs no longer burn cores for hours.** When an agent
  hands in (`chela task-finished`), is closed, escalated, merged or respawned for a rework,
  chela now stops every process still running in its worktree, except the agent's own
  Claude Code and its shell: SIGCONT, then SIGTERM, then SIGKILL after a 5 s grace, each pid
  and its command line logged. `chela ops scan` also reports a test run under a running
  agent that is older than the suite timeout, and the agent brief runs the suite under
  `timeout`. (CMX-274)

- **A crash restore no longer brings back a session you closed.** A session the restore had
  resumed once, and that you then closed, was revived by the next crash. chela now records a
  window that disappears while tmux is still running as closed by you, with the time, and
  neither the automatic restore after a reboot nor `chela restore --resume` relaunches it.
  `chela restore` lists those sessions as skipped. A window that vanishes in tmux's last
  minute is treated as the crash tearing it down, so it is still restored. (CMX-275)

- **`chela ops scan` no longer reports a live test's own tmux server or helper script as a
  leaked process.** A tmux server always re-parents to pid 1, and so does a script a test
  detaches. A tmux server on a `-L chelatest-<pid>-…` socket now counts as owned while that
  test pid is alive, and any other orphan whose working directory is a worktree counts as
  owned while a test run of that worktree is alive. Either is still reported once its owner
  is gone, or once it is older than the suite timeout. An orphaned test run is still
  reported as before. (CMX-279)

- **Tapping a session in the phone sidebar opens it again.** Since the sidebar moved to React,
  a tap on a session row on a phone showed the row's tooltip ("window: …") and the session
  never opened. The dashboard's touch tooltip now leaves anything clickable alone, so the tap
  opens that session full-screen and closes the drawer. (CMX-301)

- **Hebrew reads right to left in the wall's terminal panes again**, in Claude's replies and
  in the prompt box. ttyd now starts xterm.js on its DOM renderer
  (`--client-option rendererType=dom`); the WebGL renderer it used by default paints every
  cell left to right and has no bidi, so Hebrew came out letter-reversed. Running ttyds keep
  their old options: `pm2 restart chela-agent-terminals` respawns them.
  `CHELA_TERM_RENDERER=webgl` restores the faster renderer for a wall that shows no RTL text.
  (CMX-302)

- **On a phone, every dialog, sheet and the ⌘K palette opens on top and closes by touch.**
  Settings opened from the sidebar drawer's gear came up behind the still-open drawer, and the
  palette could not be closed. Now opening any of them shuts the drawer first, they all draw
  above it, each has a close button (a 44px target on a touch screen; the palette gained one)
  and a tap on the backdrop closes it. (CMX-306)

- **A run whose task leaves the tracker while its PR is still open is no longer marked done.**
  Linear's GitHub integration closes an issue when any merged PR names it, so a task could
  leave the tracker while its own PR sat open; the dispatcher read that as done, killed the
  window, and nothing could bring the run back. Now the run keeps its state, its card shows
  "issue left the tracker's ready states; PR still open", and it finishes only when the PR
  merges or closes. `chela reopen` also accepts a `done` run on a linear tracker whose PR is
  still open: there is no struck line to check there, so the PR state decides. (CMX-308)

- **Menu item icons are back to the label's size.** The icons in the gear menu, the pane
  menu and every other dashboard menu drew at 24px, too big next to their labels. They now
  draw at 14px, as they did before the React port. (CMX-309)

- **The Settings modal no longer resizes or jumps when you switch tabs or search.** It has a
  fixed height (up to 640px on a desktop, 92% of the screen on a phone) and the tab's
  content scrolls inside it, so the header, tab rail and footer stay put. The "No settings
  match" message now shows in the panel instead of pushing the tabs down. (CMX-310)

- **A PR waiting on CI no longer gets a "no judge round has started" notice.** No judge starts
  before CI is green, and a standard or prose PR never gets a judge at all, yet a run that sat
  20 minutes on a slow or re-running CI job was told "NO judge round has started … is the
  queue held or the daemon down? Or review it yourself". The review-wait notice now names CI
  as the wait (still running, re-running a flake, red, or unreadable). A standard or prose PR
  is never "queued for the judge", and if one is green with no verdict the notice asks about
  its no-judge verdict, not a judge. (CMX-312)

- **`test_a_detached_judge_survives_the_daemon_being_killed` no longer flakes in CI.** It
  waited for the pid file to exist, but `write_text` creates the file empty before it writes,
  so on a slow runner the kill landed in that gap and the test read `/proc//stat`
  (= `/proc/stat`) and failed with `IndexError`. The pid is now published by rename. The
  other process tests in `tests/test_ticklock.py` now wait on what they can observe instead
  of fixed gaps and tight deadlines: the stand-in daemon ends its tick when the test says so,
  and a "survives the kill" check samples the process for a second instead of looking once.
  (CMX-314)

- **A pane's terminal no longer jumps in font and size while it opens.** ttyd painted at its
  own default font, then at the server's, then at your Settings > Terminal size (after a
  one-step size flicker). The terminal now stays hidden until your font and size are set and
  rendered, and shows within 1.5 s even if a font never loads. Your size and face are also
  handed to ttyd's own preferences, so a reconnect no longer drops back to the server
  default. (CMX-321)

- **A wall pane opens much faster, most of all on a phone.** Every pane used to download
  ttyd's ~720 kB client uncompressed, revalidate every font, and wait for a 2.3 MB icon
  font before applying the terminal font. ttyd's client is now served once, gzipped and
  cached for good; the terminal fonts are woff2 at content-hashed, `immutable` URLs; the
  icon font is split into small blocks a pane only downloads when it shows one of their
  glyphs; and the font apply no longer waits for icons. On a throttled phone profile, a
  pane with a warm cache shows its terminal in about 0.75 s (was 1.35 s), and a cold pane
  has its fonts applied in 1.5 s (was 5 s). (CMX-322)

- **The Decisions dialog keeps its history when agents are busy.** Every non-hook event
  (merges, send-backs, judge verdicts, stuck notices) is also written to a small
  `events-core.jsonl`, and an event-log read that asks only for non-hook types is served
  from it. Before, the hook firehose rolled the 8 MB log every ~7 h and the dialog said
  "No decisions logged yet" after a day full of them. `chela events --type …` and the ops
  scan read the same core log. On the first daemon start, the core log is seeded from the
  rolled files. (CMX-328)

- **A wall pane's title bar shows the move cursor only when you can drag it.** In Focus and
  Auto the wall lays the tiles out itself and dragging is off, yet the header still showed the
  four-arrow move cursor. It now shows only in the free/grid layout; in Focus a strip pane's
  header shows a pointer (a click brings it forward), and elsewhere the default cursor.
  (CMX-329)

- **A crash restore no longer brings back sessions from an older tmux epoch.** After a
  restore, a later crash could resume the epoch restored two restarts ago next to the one
  that replaced it, so a stale orchestrator and a stale agent woke up with a days-old plan.
  The boot pass now retires a carried session once the newer epoch's roster shows its window,
  and resumes at most one session per window lineage (name and cwd), taking the newest
  epoch's. The roster also records the window's LIVE session (hooks or Claude Code's own
  registry), so a session that a `/clear` moved on comes back under its new id, not the
  `--resume` id it was launched with. (CMX-337)

- **A re-judge re-runs at most a cap's worth of the previous round's experiments.** A round
  now runs at most twice the risk-level cap: up to the cap of new experiments, plus up to the
  cap re-run from the previous round, its survivors first. Before this, from the third round
  the previous round's re-runs could fill the whole grown cap and leave the judge's new
  experiments unrun. (CMX-338)

- **`chela dispatch --pause --all` no longer freezes an unrelated agent.** A run that had
  merged while its judge ran kept `judge_state=running` and a window id from before a crash.
  `tmux display-message -t` never fails on a missing window: it answers with the current one.
  So the pause resolved that id to another agent's claude and SIGSTOPped its whole tree,
  running suite included, until the resume. Three changes: the window-to-pid lookups
  (`claude_pid`, `is_claude_running`) now check that the window tmux answered with is the
  one asked for. The freeze skips a run that is done, closed or failed, and signals a
  judge window's claude only when the window's tmux epoch is the current one and the
  claude's cwd is that run's judge worktree. The daemon now cancels a finished run's dead
  judge, and every judge cancel clears `judge_window_id`. A judge still running on a
  merged PR is left to finish, so it can still record a blocked-race verdict. (CMX-340)

### Security

- **The Telegram bridge accepts updates only from allow-listed senders.** Set
  `CHELA_TELEGRAM_ALLOWED_USERS` (numeric Telegram user ids, in `chela.env` or
  `secrets.env`). Text, commands, button taps, photos and documents from anyone else in the
  bound chat are dropped unanswered. Each drop is logged with the sender's id and a hash of
  the text, at most once a minute per sender. Unset, the bridge keeps the old chat-only
  behaviour and warns at start-up, and `chela doctor` warns too. (CMX-185)

- **A brief's markdown links only to http(s), mailto or relative URLs.** A task brief or a
  judge report holding `[x](javascript:...)` (or `data:`, `vbscript:`, `file:`) rendered a
  live link in the task modal; it now renders as plain text. (CMX-228)

## [0.17.0] — 2026-10-16

### Added

- **`chela judge sweep --github`** publishes a sweep to GitHub Actions with only
  `GITHUB_TOKEN`: it writes the job step summary and the `verdict` / `survivors` step
  outputs, and keeps ONE sticky PR comment — found by an HTML marker and edited in place, a
  new one posted only when the result is not clean. `--no-comment` skips the comment. A
  comment list that cannot be read is an error, never a duplicate post, and publishing never
  changes the sweep's exit code: every failure is a `::warning::` line. (CMX-97, CMX-132)

### Changed

- **The README shows how to try the judge on your own repo with no setup**: the
  `uvx … chela judge sweep --base origin/main` one-liner, a real sample run, the GitHub Action
  snippet and the exit codes. Without `--base` or a workflow, `chela judge sweep` now diffs
  against the remote's default branch (`origin/HEAD`), else `origin/dev`. A standalone run no
  longer suggests `chela task-finished --sweep-equivalent`, which only exists in a chela-managed
  run (one with a `WORKFLOW.md`). (CMX-134)

### Fixed

- **`chela watch @N` from a side session no longer steals a live orchestrator's pin.** It now
  claims the role only when the pin is empty, undeliverable (dead window, new tmux epoch) or
  already the caller's; otherwise it adds the watch routed to the current orchestrator and
  prints `watching @N — events go to the orchestrator @M (live)`. A bare `chela watch` from a
  non-orchestrator gets the `--if-unclaimed` refusal plus a hint. Taking the role from a live
  holder is explicit: `chela watch [@N] --take-over`. (CMX-138)

- **The landing deploy's "production serves this commit" check no longer fails on every
  deploy since Cloudflare Web Analytics was switched on.** Cloudflare injects its beacon
  `<script>` into every page `chela.pages.dev` serves, so the served HTML never matched
  `landing/index.html` again and each deploy went red with a misleading "most likely a
  PREVIEW deployment" error. The comparison now lives in `scripts/landing_verify.py`, which
  strips only that beacon element before the whitespace-normalised token diff. Any other
  difference still fails, down to one character of body text. The workflow's verify step is
  tested by running its own `run:` block with `curl`/`sleep` stubbed. (CMX-149, #692)

- **A PR queued behind another judge no longer gets the "NO judge round has started" stall
  notice.** With `judge_max_concurrent` saturated by its workflow's running judges, a run in
  `awaiting_review` is a queue, not a stall: it stays silent for three
  stall bounds (`CHELA_JUDGE_NOT_STARTED_ALERT_S` × 3, 1h by default), then says once that it is queued behind which
  run, judging since when, and how many slots there are. The stall clock restarts from the
  last tick that saw it queued, so a freed slot gets the full 20m before the stall notice
  fires. The stall notice no longer tells the operator to run `chela judge run CMX-N`, which
  needs the judge agent's `--experiments` and is not how a round starts. It now says the
  daemon starts the round on its next dispatch tick and points at `chela dispatch
  --hold-status`. A test runs that argv through the real CLI parser. (CMX-150)

## [0.16.0] — 2026-10-15

### Added

- **Hold Alt to number the sidebar sessions; Alt+1..9 opens that row** — like the Claude
  desktop app's Ctrl-held numbers (Ctrl+1..9 is the browser's tab switch). The numbers go on
  the first nine visible session rows, top to bottom; collapsed groups and the Archived
  section are skipped. Alt+N now works in every mode, not only the wall, and from inside a
  focused terminal pane. The wall panes' corner numbers are gone — the sidebar's is the one
  numbering. (CMX-85, #657)

- **Pending approvals on the dashboard — no Telegram needed.** A ⚖️ pill on every view counts
  the override (and, once CMX-87 lands, release) requests waiting on you; its sheet shows
  what, who asked, why, age and time to expiry. Approve (a second tap confirms) and Deny go
  through the same decide path as `/override/<id>` and re-check head / CI / mergeable /
  expiry; a request that can't be approved says why instead of offering the button. Live over
  SSE, with an opt-in browser notification (Settings → Notifications, off by default).
  `chela merge --override` now prints the dashboard's absolute URL (`CHELA_DASHBOARD_URL`,
  else the bind address) and `chela merge-approve`, and names Telegram only when a bridge is
  configured. A dashboard decision is recorded as `decided_by: dashboard` (or
  `dashboard:<tailnet login>`). (CMX-103, #667)

- **The orchestrator hears every override and release decision.** Approving or denying an
  override or a release now sends one inbox notice: merged (the commit, who approved and
  where, and the deploy step: which chela services to restart, or the changed paths in
  another repo), released (version, tag, what was pushed), or denied (by whom, where, the
  reason). (CMX-123, #676)

- **`chela task-finished` refuses an oversized diff.** It measures the branch's changed lines
  against its base (code + tests; docs, media and changelog excluded via
  `hand_in.size_exclude`) and refuses over `hand_in.max_pr_lines` (default 300) BEFORE the
  sweep runs, printing the measured count. A diff that truly cannot be split may pass
  `--oversize-reason '<why>'`, which is recorded on the run, shown to the judge and appended
  to the PR body. An unmeasurable branch refuses rather than passing. (CMX-126)

- **`chela task-finished --split-plan <file>` hands in slice 1 of an oversized task.** With a
  plan (`{"slices": [{title, files, behaviour, tests}, …]}`, slice 1 = this PR, the rest
  parked on the branch `<task>-rest`), the hand-in passes the size gate only once this branch
  is back under `hand_in.max_pr_lines`. chela then files one Linear sub-issue per remaining
  slice under the run's issue, chained so they run in order, and posts a `split_filed` inbox
  notice. A failed tracker write refuses the hand-in and names what was already filed.
  (CMX-140, #683)

- **`chela task-finished --handoff "<note>"` leaves a note for the reviewer.** The agent's
  note (what changed, what to look at first, what it assumed) is stored on the run
  (`runs.handoff`) and shown, sanitised, in the orchestrator's inbox notice and on the
  Dispatch board card. It is informational only and never gates anything. (CMX-52, #653)

- **A `claude` you type by hand into a chela window gets Remote Control too.** While the
  Remote Control setting is on, every window chela opens for a human ("+ New shell", the
  dashboard launcher, Telegram `/new`, a resume) defines a `claude` shell function, so a
  hand-typed `claude …` starts with `--remote-control <window name>` and shows up in the
  Claude desktop app under the window's current name. `-p`/`--print`, help/version, a
  prompt or subcommand, or an explicit `--remote-control` are passed through unchanged.
  Dispatched agents and judges never get it. Turning the setting off affects windows opened
  afterwards; an already-open shell keeps its function until it exits. (CMX-84, #646)

- **The orchestrator now hears what it used to find by hand.** One inbox notice each, never
  repeated on a later tick: a run waiting in review with no judge round started past
  `CHELA_JUDGE_NOT_STARTED_ALERT_S` (default 20m); a dispatch hold that lapses by its TTL or
  is released by someone else; CI red on the PR's head, naming the failing job, the first
  failing line and the `gh run rerun … --failed` command (the rework the CI loop sends is no
  longer a second notice); a PR that turns CONFLICTING with its base (once per head); a
  running agent with no commit and no transcript activity past `CHELA_STUCK_AGENT_ALERT_S`
  (default 20m); and a task-finished / push-request marker left unapplied past
  `CHELA_MARKER_UNREAD_ALERT_S` (default 5m), with the daemon's uptime. (CMX-86)

- **`chela release X.Y.Z --theme "…"` — the orchestrator prepares a release, the operator
  approves it with one tap, chela pushes.** In its own worktree off `origin/dev` (refused
  unless `origin/main` is an ancestor) it runs the release steps — the notes, the
  `pyproject.toml` bump, `uv lock` (which must re-record the new version), the release tests —
  then commits and tags LOCALLY and files an approval request. It pushes nothing; a failed or
  dirty step refuses with the reason and leaves nothing behind. The request is a Telegram card
  (version, theme, the dev commit, the task ids in the new CHANGELOG section, Approve / Deny),
  the dashboard's `/override/<id>`, or `chela merge-approve <id>`. Approving re-checks that
  `origin/dev` is still the commit the release was cut from and `origin/main` an ancestor,
  then pushes main, the tag and dev — fast-forward only, once — and the card ends on the
  released commit and, once the Release workflow built it, the GitHub Release's URL. A push to
  dev expires the request; deny, expiry or a failed check push nothing and remove the local
  tag. The approval stays human-only: the merge-gate hook (plugin 0.2.9) also denies a
  session's `python` call into `chela.release.complete`. A staged file outside the release
  paths (CHANGELOG.md, pyproject.toml, uv.lock, changelog.d) refuses the release too. See
  "Releasing" in CONTRIBUTING.md. (CMX-87)

- **`chela judge sweep`: a mechanical mutation pass over every conditional the branch
  changed.** For each changed line in `.py`/`.js`/`.mjs` (not tests) vs `--base` (default
  `origin/<workspace.base_branch>`), it rewrites `if X` to `if False and (X)` (plus the
  `True or (X)` twin when there is an `else`), swaps a return/`CompletedProcess` code
  `0`↔`1`, swaps `return None`↔`return <expr>` in a function that has both, and flips
  `==`↔`!=`, `<`↔`>=`, `>`↔`<=`. Each mutation goes through the judge's own
  apply/read-back/parse/per-mutation-selection mechanics and is restored in a `finally`. A
  recovery marker (`.chela-sweep-recovery.json`) means a killed sweep is undone on the next
  start. It exits 1 on any survivor. A mutation that does not parse is reported as skipped,
  never as killed.
- **`chela task-finished` runs the sweep and refuses while a survivor remains.** To accept a
  survivor, the agent names it with a reason:
  `--sweep-equivalent '<file:line>: <why no outcome can change>'`. The note is stored in
  `runs.sweep_equivalents`, shown to the judge and appended to the PR body (one line per note — a newline in the reason cannot forge an
  extra bullet). A note that names
  a line which did not survive is refused. The judge itself is unchanged. (CMX-88)

- **`chela judge sweep` runs on any git repo, with no chela setup.** No `WORKFLOW.md`, run
  record, daemon, tmux or tracker: the suite is `--test-cmd`, else the workflow's
  `judge.test_cmd`, else detected (pytest / `npm test` / `go test` / `cargo test`); `--json`
  prints the result as JSON. A changed file in a language the sweep cannot mutate (anything
  but Python and `.js`/`.mjs`) is reported as UNKNOWN (exit `3`), never as a pass. Run
  standalone, the sweep does not provision the repo's environment. (CMX-97)

### Changed

- **Docs: the README / landing demos are re-recorded for the new sidebar.** The desktop clip
  now shows the sidebar as it is today — folder groups with "Needs you" on top, the
  orchestrator badge, hold-Alt row numbers, and a finished session archived (its window
  closes) and unarchived (resumed in a new window) — before the Work view and Settings; the
  phone clip is re-shot on the current pill switcher. Same file names. The demo recorder
  gained what that needed: the synthetic fleet registers an orchestrator, its fake `claude`
  answers `claude --resume <id>` by re-running that scripted session, and `record.mjs` can cut
  over a transient instead of freezing on it. (CMX-100)

- **`chela judge sweep` has a budget, so `task-finished` can no longer block for as long as
  the diff is big.** The sweep's cost was mutations × suite time; one hand-in ran 24+
  minutes. It now stops at a wall-clock budget (`judge.sweep_budget_seconds`, default 900)
  or a mutation cap (`judge.sweep_max_mutations`, default 60), whichever comes first; `0` or
  unset means the default, and `chela judge sweep --budget-seconds/--max-mutations` override
  both. Mutations run conditionals first, then return codes and values, then comparison
  flips, round-robin across files. Each mutation the budget did not reach is listed as
  `[UNRUN]`: CANNOT VERIFY, never counted as killed. `chela judge sweep` exits 2 on a partial
  sweep with no survivor. `task-finished` still refuses a measured survivor, but proceeds past
  the unrun remainder and reports it as "PARTIAL", never as a pass. (CMX-111)

- **The agent brief now has a guard-category checklist to walk before hand-in.** Step 3 of
  `WORKFLOW.md`'s agent prompt lists seven categories the agent checks its own diff against
  (`except` clauses, subprocess calls, literal values and regexes, kind branches, fail-closed
  reads, flag precedence, the authoritative record), writing one test per item that applies
  and naming the rest in the PR body. CMX-87 took 13 judge rounds because each round found
  the next gap in a category the tests covered one case at a time; `docs/RUNS.md` notes the
  step too. (CMX-114)

- **The sweep names what it could not measure.** `chela task-finished` and `chela judge
  sweep` list every mutation that was skipped (did not parse, did not apply) and summarise
  "N killed, M skipped (unmeasured)". A git command that hangs, or a changed `.py` file that
  git diffs as binary, is CANNOT VERIFY instead of being silently left unswept. (CMX-116, #677)

- **Update now no longer refuses while a dispatched run is in flight.** Agents live in tmux
  and judges run detached, so both outlive a restart of the daemon that spawned them (now
  proven by tests that SIGKILL the spawner's whole process group), and the daemon's own tick
  is waited for by the tick lock (CMX-95). A fleet whose freshness can't be read now answers
  **503** instead of "already up to date", and the new read-only `GET /api/update/status`
  reports the Update payload plus `boot`, `applying` and `last_result`, keyed by the `run`
  number the apply POST returns. (CMX-127)

- **`task-finished --self-check-experiments` has a budget too.** Re-running the agent's own
  mutation experiments was unbounded (one hand-in spent 4-5 hours on it). `chela judge
  self-check` now stops at a wall-clock budget (`judge.self_check_budget_seconds`, default
  900) or an experiment cap (`judge.self_check_max_experiments`, default 12), whichever comes
  first; `0` or unset means the default, and `chela judge self-check
  --budget-seconds/--max-experiments` override both. Each experiment still uses per-mutation
  test selection, with subset survivors confirmed on the full suite. Experiments the budget did
  not reach are listed as `[UNRUN]`: CANNOT VERIFY, never counted as killed, and the result is
  `partial`, never `clean` (`chela judge self-check` exits 2). `task-finished` still refuses a
  surviving guard, but proceeds past the unrun remainder and names it — "PARTIAL", never
  "every guard held". Experiments past the cap used to be dropped silently; they are now listed. (CMX-141)

- **The sidebar layout now follows you across devices.** The group order, custom groups and
  their members, and the View settings (group by, sort, status) are stored by chela
  (`/api/sidebar/layout`) instead of each browser's localStorage, so the phone and the laptop
  show the same sidebar. Collapsed groups, the collapsed sidebar and the Archived section's
  open/closed state stay per device. The first browser to load the new version imports its
  old local layout once; later devices adopt that one. (CMX-82, #649)

- **A window's name now follows its session's folder while chela still owns the name.** A
  window opened in `~` was named after your login and kept that name after its Claude
  moved to `~/projects/cute-animals`, even though the sidebar grouped it under that folder.
  The daemon now renames such a window to the new folder, adding `-2` and so on if the
  name is taken. It waits until the new folder has held for a full tick, and it reads the
  folder from the same place the sidebar does. The Telegram topic and the Remote Control
  name follow the new name. A name you chose, the orchestrator's window, and dispatched
  agent and judge windows are never renamed. (CMX-83)

- **The orchestrator's inbox stops echoing it and stops announcing "awaiting review".** A
  `chela reopen`/`retry`/`close`/`merge` or `chela dispatch --resume` run by the orchestrator
  itself no longer comes back to it as a notice; the same action by a human (terminal,
  dashboard, Telegram) still does — a human's reopen is now `run_reopened`, and a close by
  anyone else (or a PR closed on GitHub) is `run_was_closed`. The plain "awaiting review"
  notice is silent by default (still logged for the Feed and `chela wait`); set
  `CHELA_INBOX_NOTIFY_AWAITING_REVIEW=true` to bring it back. (CMX-86)

- **Docs: README leads with the gate.** The README top now pitches the merge gate (tracker
  dispatch → mutation-testing judge bound to the PR head → `chela merge` refusing without a
  clean verdict, one-tap Telegram override), puts the demo right under the banner, has one
  install block with a one-line requirements list, and states plainly that agents run
  unsandboxed by default (the sandbox is opt-in per workflow). The landing hero carries the
  same pitch. (CMX-91)

- **Restarting the daemon waits only for its current tick.** The daemon holds a lock
  (`$CHELA_DIR/daemon-tick.lock`) for each tick, and `chela update` / the dashboard's Update
  takes it before restarting `chela-daemon`, so a restart never lands mid-claim, mid-spawn or
  mid-DB-write. The wait is bounded at 60 s; past that, or when the lock cannot be read, the
  restart refuses with the reason. Other services never wait. (CMX-95, #679)

- **Landing page follows dev-tool landing conventions, in chela's own look.** The hero puts
  the README's gate-first pitch, one sub-line, *Get started* / *Star on GitHub* and the demo
  clip (muted autoplay loop with a poster, desktop and phone cuts) above the fold. Install is
  the three-line path from `docs/GETTING_STARTED.md` with its requirements line and a copy
  button that stays out of the way with JS off. *How it works* is a four-step pipeline
  (tracker → worktree agent → the judge corrupts the code → `chela merge` refuses → one-tap
  audited override), followed by a *Why chela* comparison table that is honest about the
  opt-in sandbox, an FAQ, a release-agnostic *Latest release* link to GitHub Releases in place
  of *New in 0.15*, a Docs / GitHub / License / Changelog footer, and a regenerated social
  card. (CMX-99)

### Fixed

- **Danger buttons (Schedules' "Del", an agent's "Stop") are readable in every theme.** A
  leftover rule painted them red text on a red fill. They are now outlined at rest, with a
  label that meets WCAG AA in every theme; the red is carried by the outline and the hover
  fill, not by hue alone. (CMX-101, #670)

- **Phone, keyboard open: the terminal's bottom rows are no longer hidden behind the keybar.**
  Typing a multi-line prompt into an agent showed only its first lines, and Claude's status
  line was cut in half, until you scrolled the page. With the keyboard up, the single pane is
  now sized from live geometry so it ends exactly at the keybar's top edge, and it re-fits on
  keyboard, rotation, keybar-height and scroll changes. Keyboard closed and desktop are
  unchanged. (CMX-102)

- **The Wall's ring marks the pane your keyboard is in, and only that.** The focus ring is
  drawn on an overlay above the pane's header, terminal and toolbar instead of only around
  the recap line (CMX-90, #660), and it follows the terminal that actually has keyboard focus,
  never a stale one. A crisp ring now means focus only: a pane waiting on you glows yellow, a
  room is a stripe on the header, and a maximized pane has no second frame. (CMX-104, #663)

- **A new folder group lands among the folders.** Once you had moved any sidebar group, a
  newly seen folder sorted below *Dispatched*. Groups missing from the saved order now merge
  in at their default rank; an arrangement you chose is kept and *Other* stays last.
  (CMX-106, #664)

- **A Linear issue closed as Duplicate counts as closed.** Linear reports Duplicate with its
  own state type, `duplicate`, so such issues stayed in chela's open read and on the Work
  board. They are now terminal and hidden, like Canceled. (CMX-107, #668)

- **The stuck-agent notice no longer cries wolf during a self-check, nor claims the daemon is
  down.** A live `chela task-finished` / `chela judge sweep` / pytest running in a run's
  worktree now counts as activity, so a long, silent-by-design self-check is never "STUCK";
  past `CHELA_SELFCHECK_ALERT_S` (default 60m) it gets one `run_selfcheck_long` notice that
  says exactly that ("task-finished self-check running for 1h02m"). The daemon's state on
  these notices is now measured (its state file + pid liveness) on every notice, not only
  when a marker was pending: "daemon up …", "no daemon running" only when the recorded pid
  is gone, and "daemon state unknown" when it cannot tell. (CMX-110)

- **The "self-check running past the ceiling" notice fires once, not every tick.** After
  CMX-110 a self-check still running past `CHELA_SELFCHECK_ALERT_S` re-announced itself on
  every daemon tick: the latch was keyed on the self-check's measured start, a float that
  jitters between ticks. It is now latched on the self-check's process — one notice per
  self-check, at most one more per further hour it runs — and the latch clears when the
  self-check ends or the run leaves `running`, so a re-run is news again. (CMX-120)

- **After a reboot, human sessions come back on their own.** The daemon now runs the restore
  pass once per new tmux epoch: with `CHELA_RESTORE_RESUME=true` it relaunches the MANUAL
  rows the ended epoch left behind (the `chela restore --resume` path, which re-registers the
  orchestrator), and with it off it only reports. Either way one inbox notice lists what came
  back and what didn't. The first epoch it ever sees is recorded as a baseline, never acted on.
- **Restore no longer offers a second session in a dispatched run's worktree.** A session
  whose cwd is the worktree of a run that is not over is hidden from Recent sessions, refused
  by `/api/restore/resume` (409, saying which run), and skipped by `chela restore --resume`:
  the run gets exactly one window, the dispatcher's.
- **A tmux restart no longer spends a run's attempt.** A run whose window was stamped by a
  tmux server that is no longer running is retried under the same attempt number. (CMX-125)

- **A size-gate split's sub-issues now run in order and keep the parent's priority, and the
  write is verified.** Slice 2 is blocked by the parent (whose PR carries slice 1) and each
  later slice by the one before; every slice is filed with the parent's priority. Each
  created issue is read back fresh from Linear, and `task-finished` refuses the hand-in,
  naming the mismatch and what was already filed, when the read-back lacks the `blockedBy`,
  carries a different priority, or cannot be read. (CMX-143)

- **`chela release` can succeed on a real install.** Its worktree was built under
  `CHELA_DIR/releases/`, i.e. inside the live `~/.chela`, so the release tests' own
  live-state fence failed every read of the worktree's `CHANGELOG.md`/`pyproject.toml` and
  every release refused. The worktree now lives under the repository's git dir
  (`<git-common-dir>/chela-releases/<tag>-<hex>`); the approval records stay in `CHELA_DIR`.
  A red release-tests run now writes its full output to `CHELA_DIR/releases/last-failure.log`
  and the refusal names that file and each failed test, instead of a 600-character tail.
  (CMX-144)

- **Unarchive opens one window, even when two taps race.** A second Unarchive of the same
  session while the first is still spawning is refused (409) instead of opening a second
  window; a failed spawn releases the claim. Archive keys imported from a browser's old local
  state are stamped with the tmux server's epoch, so a hidden `@N` from a dead tmux server no
  longer hides an unrelated new window. (CMX-78, #654)
- **The Usage view's 30-minute window no longer empties after UTC midnight.** It scanned
  transcripts only from the start of the UTC day, so for the first half hour of each day the
  30-minute consumers lost everything from before midnight. (CMX-78, #654)

- **The dashboard's first paint is the view it lands on.** With terminals on, the Wall is
  the server-rendered active view, so a load no longer flashes the Work view first; a phone
  no longer flashes the desktop "+ New shell" button; and a collapsed desktop sidebar stays
  collapsed from the first frame. (CMX-89, #658)

### Security

- **A dispatched agent or judge pane now inherits an allowlisted environment, not the
  daemon's.** It used to unset only `CHELA_NOTIFY_URL` and inherit everything else the tmux
  server held — every `chela.env` setting, pm2's bookkeeping, the operator's shell vars. Every
  inherited name (the daemon's env plus the tmux global and session tables) that is not on
  `envutil.AGENT_ENV_ALLOW` is now unset in the pane before the agent starts. Extend the list
  with `CHELA_CHILD_ENV_FORWARD`; `CHELA_NOTIFY_URL` stays dropped even when forwarded. See
  TRUST §2 and `docs/CONFIG.md`. (CMX-45, #652)

## [0.15.0] — 2026-10-11

### Added

- **`chela close <run> --requeue` makes a closed task claimable again.** The next tick claims
  a fresh attempt (attempt 1) on its own `-r<N>` branch and worktree, never the closed run's.
  The old PR is closed, the requeue is recorded in the review history, and a run whose PR
  merged is refused. Without `--requeue`, `close` stays terminal, and it now says so: it
  prints that the task will not be re-dispatched and the command that requeues it. Moving
  the issue back to Todo by hand does not requeue it. (CMX-65, #627)

- **The Cost tab has a Usage view: what is eating the plan limits, in tokens.** A "Cost |
  Usage" toggle shows the 5h and 7d limit bars (from the freshest statusLine `rate_limits`,
  with a %/h burn rate and whether it reaches 100% before the reset; missing, stale or reset
  data shows as unknown, never 0%) and the top token consumers over the last 30 min and today
  (UTC) across EVERY Claude Code transcript — judges, subagents, background sessions and, on
  WSL, Windows-side ones (`/mnt/c/Users/*/.claude/projects` by default; set `usage_extra_roots`
  in `~/.chela/config.json` or `CHELA_USAGE_EXTRA_ROOTS`, `[]` for none — the view shows the
  roots read-only). Each row has its requests, token kinds, a weighted total and cache-hit
  rate; a session under 50% hit over ≥10 requests and >1M tokens is flagged ▲ CACHE BROKEN. (CMX-38, #618)
- **A held decisions inbox, or a clean PR nobody merges, now pushes one ntfy alert.** When the
  inbox queue's oldest event has waited past `CHELA_INBOX_HELD_ALERT_S` (default 30m), the
  announcer pushes once per held episode, naming the orchestrator's status and the last failed
  delivery (an open prompt, an unreachable socket, an adverse receipt). A run that stays
  judge-clean on its current head and unmerged past `CHELA_CLEAN_UNMERGED_ALERT_S` (default
  30m) pushes once. `chela doctor` gains an `inbox.held` fact that goes red while the queue is
  held. (CMX-26, #610)

- **`chela spawn <cwd> [--command CMD]` opens an agent window from the terminal.** It runs
  through the same `spawn_window` path as the dashboard launcher and Telegram `/new`. With no
  `--command` it launches `$CHELA_AGENT_CMD` (default `claude`), not the bare shell that
  `spawn_window` opens on its own. It prints the new `@N` and cwd, and adds the cwd to the
  launcher's Recent list on a best-effort basis. (CMX-13)
- **`chela close @N` kills one chela window, so nobody has to reach for raw
  `tmux kill-window`.** It refuses a window that is not live in the chela session, the
  orchestrator's own window unless `--orchestrator` is passed, and a window that an
  in-flight dispatched run still claims unless `--force` is passed. For that last case,
  `chela close <task> --reason …` is the right tool. Closing a run by id works as before.
  (CMX-13)

- **A profile of chela's services under normal load (`docs/perf/2026-10-profile.md`).**
  The dashboard spends most of its CPU starting processes. About 90% of its tmux and pgrep
  spawns come from `/api/agents` and `/api/agents/context`, which probe each window
  separately every 4 s per open Wall tab. The cost grows linearly: 7% of a core at 5
  windows, 73% at 40. The report ranks three fixes: batch the probes through the existing
  `sessions.panes()`, remove a 36-second CPU burst in `chela doctor`, and have Telegram
  capture only panes that changed. It also lists what is not worth doing, including
  swapping Werkzeug for waitress. The isolated harness that produced the numbers is in
  `docs/perf/2026-10-harness/`. No production code changed. (CMX-15)

- **`chela doctor` fact `tmux.leaked_env`.** It goes red when tmux's global env holds a
  proxy aimed at a dead localhost port, a Claude session marker, or a `TMUX_TMPDIR` whose
  dir is missing. The daemon's doctor sweep pushes it once through the notify path.
  (CMX-21)
- The perf harness (`docs/perf/2026-10-harness/`) now passes `tmux -L chela-perf` on every
  call. It refuses `kill-server` on the default socket, starts the ttyd supervisor under
  `env -i` with an allowlist inside its own process group, and `stop.sh` reaps that whole
  group. (CMX-21)

- **A push when an agent is blocked by its permission classifier.** An auto-mode denial
  never shows a prompt, so the session never reaches `waiting` and the needs-input push
  stayed silent. `notify.DeniedWatch` now tails `hook.permission_denied` events and sends
  one ntfy push per window per 10 minutes, from whichever process holds the announcer
  lease (daemon or dashboard). (CMX-25, #609)

- **A judge window shows its detached mutation battery's live progress instead of
  "idle".** While `chela judge run --detach` runs, the judge pane's state pill reads
  `⚖️ testing · 3/6 · 15m` (its status dot reads working), and `chela peek` on the window adds a
  `judge:` line with the KILLED/SURVIVED/INVALID tally and the experiment running now (a held-out one
  is never named). A run whose process died before a verdict reads `⚖️ judge run died — no
  verdict`, never idle. The badge disappears when the run finishes. (CMX-40)

- **`chela close <run> --reason "…"` — walk away from a run on purpose, with a visible
  reason.** A run that was superseded or abandoned used to stay on the Work view as a
  misleading FAILED card (for example "tmux window disappeared" after its idle window was
  closed). Now it is marked `closed`: it is never claimed or retried again, its slot is
  freed, the reason goes in its review history and a `run_closed` event, and the card reads
  **"Closed — <reason>"**. It accepts `failed`, `needs_human`, `changes_requested`,
  `awaiting_review`, `running` and `claimed` runs. A running or claimed run is refused
  unless its window is gone or its agent is idle; `--force` overrides that. An open PR gets
  the reason as a comment, `--close-pr` also closes it, and on a terminal with neither
  `--close-pr` nor `--keep-pr` you are asked. The branch is never deleted, and the worktree
  is kept unless you pass `--remove-worktree`. Restart `chela-daemon` and
  `chela-dashboard` to pick it up. (CMX-406)

- **`chela judge eval` — an offline eval of the judge's experiment DESIGN.** It runs only
  the design step (the live judge prompt at each risk level, and the model returning its
  experiments JSON) against a read-only copy of a PR's tree. It never runs a test suite.
  Per risk level it reports recall on seeded and historical real regressions, the share of
  proposed experiments that are contrived, validity (each experiment is checked with the
  judge's own `apply_mutation` / `parse_check`), grader flips, and a decoy negative control.
  Every score has a 95% interval. The dataset is 85 historical cases mined from this repo's
  judge verdicts plus 4 seeded regressions. It is split train/test by PR, and a test-split
  run prints aggregates only. The cost estimate prints before any model call, and nothing is
  spent without `--yes`. `--limit`, `--max-cost` and a per-call budget cap the rest.
  Subcommands: `mine`, `label`, `controls`, `spot-check`, `agreement`. It makes no change to
  how the judge behaves. See `docs/JUDGE_EVAL.md`. (CMX-408)

- **Drop or paste a file into a Wall terminal.** Dropping a file on a terminal pane, or
  pasting one (a screenshot, "Copy image"), now saves it to that session's
  `uploads/` folder and types `@uploads/<name> ` into the prompt, without sending it, so
  Claude can read a file that lives on your phone or laptop and not on the host. A small
  toast names the saved file, and an error toast explains a refusal. Files are never
  overwritten (`-1`, `-2`… suffixes). A name with a path separator, `..`, or a leading dot
  is refused, and so is a symlinked `uploads/`. Uploads are capped at 25 MB each
  (`CHELA_UPLOAD_MAX_MB`) and 20 per minute (`CHELA_UPLOAD_PER_MINUTE`). Every upload
  records an `upload.saved` or `upload.refused` event. It is for the operator only: a
  share guest's request is refused whatever *Guest typing* says. Turn it off in
  Settings → General → *File drop into terminals* (on by default, or `CHELA_FILE_DROP`).
  Restart `chela-dashboard` to pick it up. (CMX-412)

- **Every pane shows its tmux window id (`@N`).** The orchestrator, `chela peek @N`, inbox
  notices and peer messages all address agents by `@N`, and the dashboard now shows it too:
  as a small, dim `@N` at the start of each pane's bottom bar (click it to copy, with a
  "Copied" toast), on each sidebar session row's second line and in its tooltip, and in the
  ⌘K / "Jump to session" palette, where typing `@32` jumps straight to that window. The id
  stays on one line at desktop and phone widths, however long the session or branch name.
  It is the same id `chela status` prints and renumbers after a tmux restart, as tmux does.
  Restart `chela-dashboard` to pick it up. (CMX-417)

- **Web access for a sandboxed share session (opt-in, per session).** Start one with
  **New session → Sandboxed session · allow web access…** or
  `chela share-session <project> --web`. The default is still no network. The guest
  container stays alone on its `--internal` network with no host address. One more
  sidecar joins it: `chela/share_web_proxy.py`, a filtering HTTP(S) forward proxy that
  carries all the guest's web traffic.
  - The proxy resolves each name itself and refuses any private, loopback, link-local,
    CGNAT, host or embedded-IPv4 address after resolution. It then connects to the
    address it checked, so there is no DNS-rebinding window.
  - It allows ports 80 and 443 only.
  - It rate-limits each site (1 request/s by default) and all sites together.
  - It logs every request to `~/.chela/share-web/<session>.log`.
  - Optional `CHELA_SHARE_WEB_DENY` / `CHELA_SHARE_WEB_ALLOW` lists. Unset means any
    public host.

  The guest runs a locally built `chela-share-web` image with headless Chromium and a
  `browse` command. Its WebFetch, curl and pip go through the proxy. The pane shows
  **🌐 web**. The live share check now also verifies the mode the session was launched
  with: a `none` session that gains a proxy variable, a web sidecar or any other network
  member fails. See `docs/SHARE_SANDBOX.md` → *Web mode*. (CMX-418)

- **Sandboxed sessions now relay to their Telegram topic.** Their transcript stays inside
  the container, so the share proxy writes each completed turn (the assistant's text, with
  each tool call as one line) to a per-session outbox that only the proxy sidecar mounts.
  `chela telegram` reads that outbox for a window that verifies as sandboxed, with the
  usual formatting, chunking and dedup, and `chela doctor` stops reporting such a window as
  dead outbound while its outbox is current. Everything the session says now also reaches
  the operator's topic (see `docs/SHARE_SANDBOX.md`). Turn it off for one session with
  `chela telegram --sandbox-relay @N=off`. (CMX-420)

- **Change a live share's mode without re-sharing.** Active shares now shows each share's
  mode as a control: 👁 View only, ⌨ Allow typing, or ⚠ Full access — UNSANDBOXED. Switching
  keeps the same link and pairing code, and a guest who already joined keeps the
  connection. Dropping to View only applies at once. Going up passes the same checks as
  creating a share with that mode: *Guest typing* on and a verified sandboxed window for
  typing, or the typed window name for UNSANDBOXED, which then starts its own expiry and
  binds the first guest to type. The guest sees "You can type now" or "View only now".
  Every change records a `share.mode_changed` event (from, to, by). The pane's share row
  shows the mode (👁 / ⌨ / ⚠), and each row in the sheet says what that share allows,
  replacing the one sentence used for every share. Clicking *Share current session* on a
  window that's already shared opens that share's row instead of looking like a new
  share. Restart `chela-dashboard` to pick it up. (CMX-421)

- **A Linear tracker: `tracker: kind: linear`.** A workflow can now take its queue from a
  Linear team. The issue description is the brief. *Blocked by* relations hold a task the
  same fail-closed way `depends:` does, and the claim order is priority, then the board's
  manual order. Only the `ready_states` (default `Todo`) are claimed, but every state that
  is not completed or canceled counts as open, so an issue Linear's GitHub integration
  moved to In Progress is not mistaken for finished. A failed read (network, auth, rate
  limit, malformed reply) never closes a run.

  The run's id is the identifier (`CMX-12`), its number is Linear's, and its branch and
  window are Linear's suggested branch name. That branch is never reused if the remote
  already has it. A bare `cmx-12` that names both an old TODO.md-era run and a Linear run
  resolves to the Linear one, never to an old finished run.

  When a PR merges, chela marks the issue Done if it isn't already and archives it. A sweep
  every 15 minutes archives any closed issue that is still unarchived. `chela doctor`
  (`tracker.linear_issue_cap`) warns above 200 non-archived issues, since the free plan
  stops at 250.

  The API key is read only from `chela.env` as `LINEAR_API_KEY` and never from
  `WORKFLOW.md`. It is never logged and never passed to agents, judges or hooks. Setup is
  in `docs/LINEAR_TRACKER.md`. Existing workflows are unchanged. (CMX-432)

- **Dismiss a session from the sidebar's "Recent sessions".** Each resumable row now has
  a × (or swipe left on touch). The row leaves the list at once and an Undo toast offers
  it back for a few seconds. With two or more rows, "Clear all" at the section header
  dismisses them all after a confirmation. Dismissals are stored server-side
  (`$CHELA_DIR/dismissed-sessions.json`, keyed by session id), so they hold on every
  device. Dismissing only hides the row: the transcript and the restore records are kept,
  and a dismissed session that comes back to life shows under Sessions as usual. (CMX-437)

- **Work board: a Linear-backed card links to its Linear issue.** Every card for a Linear
  task — queued, running, in review or done — shows its identifier as `CMX-12 ↗`, opening
  the issue in a new tab without triggering the card's own click. The href is exactly the
  `url` the Linear adapter fetched (recorded on the run at claim, in a new
  `runs.tracker_url` column), never built from a workspace slug. Cards from markdown and
  GitHub-issues workflows are unchanged. (CMX-5)

- **"New task" in the dashboard creates a Linear issue.** On a workflow whose tracker is
  `kind: linear`, the Work view's toolbar (and a ⌘K palette row) opens a form: title,
  markdown description, priority and optional "blocked by" (open issues). Submit creates
  the issue in the workflow's team, in its ready state, plus a `blocks` relation per
  blocker. It is never created as a sub-issue. The browser posts to
  `/api/dispatcher/linear/issue`, and only the dashboard calls Linear, so `LINEAR_API_KEY`
  never reaches the page. Share guests are refused. A Linear error is shown and the typed
  form is kept. The created issue shows in the queue on the refresh, linked to its Linear
  page by the board's existing `CMX-N ↗` link (CMX-5). (CMX-6)

- **A View menu for the dashboard sidebar, like the Claude desktop app's.** The sliders
  button on the Sessions header picks what shows and how it is laid out:
  **Status** (Active / All / Archived), **Environment** (Interactive, Dispatched agents,
  Judges, Background sessions; all but Judges by default), **Last activity** (1d / 7d /
  30d / All; default 7d), **Group by** (Date, Folder, State, Custom groups, None),
  **Sort by** (Last activity, Name, Created), plus **Show empty groups** and **Show PR
  status** (a `#N` PR badge on dispatched-run rows). Custom groups are created, renamed
  and deleted in the menu and assigned from any row's menu ("Move to group…"); they
  follow the window, not its name. Filters never hide a session that is working or
  waiting on you, or the orchestrator. Choices are kept per browser. Submenus open by
  tap, so the menu works on a phone. `/api/agents` now carries each window's
  `last_activity` and `created`. (CMX-66)

- **A closed run that silently blocks a READY task is now flagged, with a one-click way
  out.** `chela doctor` reports `dispatch.closed_run_stalls`: a task in its tracker's ready
  state whose run is `closed` (no requeue pending) is never claimed again, so doctor names it,
  says "closed run blocks this task: requeue or refile", and gives the `--requeue` command. An
  unreadable tracker reads CANNOT VERIFY, never "no stalls". On the Work board, the task's
  card carries the same flag, computed over all of the workflow's runs and not only the 10
  most recent. Its **Requeue** button (or **Close & requeue** on a run still in flight) runs
  `chela close --requeue` through the new `POST /api/dispatcher/runs/<id>/close`. (CMX-68)

- **A sandboxed guest can request more access, and the operator approves or denies it.**
  Inside a sandboxed session, the guest or its Claude runs
  `python3 ~/bin/chela-request mount|domain|operation <target> --why "…"`. The request goes
  to a write-only route on the session's token proxy and lands in a host-only file. It does
  nothing by itself, and the guest can't read it back or see the decision.
  - The operator sees each request in the dashboard (the **🙋 N access requests** pill),
    as an event in the log, and as a push when `CHELA_NOTIFY_URL` is set (ntfy or
    Telegram). They can also run `chela share-requests` in a terminal. Approvals last
    60 min by default.
  - Mounts are read-only unless the guest asked for write and the operator ticks
    *allow write*.
  - An approved mount is applied by **re-launching** the guest container with it at
    `/extra/<name>`. A running container is never widened. Expiry or revocation relaunches
    the container without the mount.
  - The secrets directories (`~/.ssh`, `~/.claude`, `~/.chela`, `~/.config`, `~/.secrets`,
    …), `$HOME` and its ancestors, the tmux socket directory, system directories and
    `/mnt/*` are refused even when approved, and so is a symlink into any of them. The
    refusal is checked at approval, at launch and by the live sandbox check, and the UI
    shows why.
  - Domain approvals extend a web session's `CHELA_SHARE_WEB_ALLOW` list. Operations are
    only recorded.
  - Every request, approval, denial, refusal, expiry and revocation is a
    `share.request_*` event. Stopping the share or turning *Guest typing* off revokes
    every approval.
  - Nothing approves automatically, and chela's merge-gate hook denies
    `chela share-requests approve` and the approve route to Claude sessions. (CMX-7)

- **Telegram Approve / Deny for override requests.** Each request is posted once to the
  orchestrator's topic (task, PR link, judge state on the head, reason) with inline buttons,
  and the message is edited to the outcome: merged `<sha>` / denied / expired / failed.
  `chela override list [--all]` shows requests. (CMX-79)

### Changed

- **With `tracker: kind: linear`, the Work board's columns are the team's Linear workflow
  states, in Linear's order.** Canceled and Duplicate are hidden, as Linear hides them. chela's
  run state (failed, judging, rework N, blocked race) now shows as a badge on the card, never as
  a column. Other tracker kinds keep chela's own lanes. chela also drives the issue's state:
  claim or rework round → In Progress, PR out of the agent's hands → In Review, merge → Done,
  `chela close` → Canceled, or Duplicate plus the relation when the reason names the issue
  that supersedes it. Each edge is written once, and a failed write never blocks anything. A run whose window
  died is re-claimed from In Progress or In Review while it has retries left. (CMX-23, #607)

- **The Telegram pane watch re-captures only panes that changed.** Each tick it reads every
  window's `#{window_activity}` in ONE `tmux list-windows` call and runs `capture-pane`
  only for windows whose stamp moved since their last capture (or whose stamp is in the
  same second that capture began, since the stamp is whole seconds). Unchanged windows are
  handed their last captured text, so every detector, hook-log lookup and re-post backoff
  still runs every tick. A full sweep re-captures every window at least every 30 s as a
  backstop for changes that write no output. The transcript relay and the pane loop now
  ask tmux for the server epoch once per tick (`epoch.per_tick()`), not once per window.
  Measured at 40 idle windows: `capture-pane` spawns 1,160 → 92 per run and the
  service's CPU 10.1–10.3% → 5.9–6.6% of a core. (CMX-18)

- **The dashboard's polled endpoints no longer probe tmux and `pgrep` once per window.**
  `/api/agents`, `/api/agents/context` and `/api/orchestrator/status` now read one shared
  pane snapshot (`sessions.panes()`: one `tmux list-windows` plus `/proc`, cached ~1 s
  across requests and tabs) and a shared tmux epoch, instead of 2–4 spawns per window per
  poll. A window that is missing from the snapshot forces one fresh read, so new windows
  still show on the next poll. The responses are unchanged: a golden test renders 5 and 20
  real windows both ways and requires byte-identical output. On a host without `/proc`
  (macOS), one `ps -axo` (plus one `lsof` for the claude cwds) now replaces the per-process
  `pgrep`/`ps`/`lsof` calls in `sessions.panes()`. Measured with the CMX-15 harness (dashboard
  alone, one phone tab on the Wall) at 5 / 20 / 40 windows: dashboard CPU 7.0 / 23.2 / 59.9% →
  2.7 / 4.1 / 7.6% of a core, `/api/agents` p50 98 / 388 / 1,044 ms → 20 / 38 / 117 ms. (CMX-20)

- **Sessions chela opens for a human now launch with a bare `--remote-control`.** chela
  passed a name, frozen at launch: the `shell-N` placeholder for a home-dir window, the folder
  name for a project. The claude.ai / desktop sidebar showed several sessions as "shell-3",
  and later tmux renames never reached it. With no name, the sidebar shows Claude's own
  generated session title; it already groups sessions by project folder. Applies to the
  dashboard launcher, Telegram `/new`, `chela spawn` and the orchestrator persona. Dispatched
  agents and judges still get no Remote Control. Running sessions keep their name until
  relaunched. (CMX-34)

- **The dashboard sidebar groups sessions by project folder, like the Claude desktop app.**
  Each folder gets a quiet header with a `+` (a new session in that folder), a collapse
  chevron and a group menu (right-click, or the ⋯ on touch): New session, Move up / Move
  down, Collapse all / Expand all, and Archive all. Home-dir sessions sit in "Other" at the
  bottom, and two folders with the same name are told apart by their parent path. Archive
  all only hides finished rows from your sidebar (reversible via "Show archived"). It
  never kills a window, and never hides one that is working or waiting on you.
  Dispatched work is one row per run in a "Dispatched" group, `CMX-N · <title>`: the
  agent and judge windows fold together, and the row shows the run's state. The
  "Finished" cluster is gone: a done session stays in its folder. (CMX-35)

- **The Remote Control name in claude.ai / the Claude desktop now equals the chela window
  name, at launch and after every rename.** Every launcher (dashboard, Telegram `/new`,
  `chela spawn`, resume) names a new window after its folder — the cwd basename, or the
  login for the home dir, collision-safe `-N` — instead of a `shell-N` placeholder, and
  starts Claude Code with `--remote-control <window name>` (the auto-launched orchestrator
  passes `orchestrator`). A dashboard rename or a duplicate's `-N` suffix queues
  `/rename <new name>`, typed into the session whenever its prompt is empty, busy or idle (a
  ghost suggestion counts as empty; a typed draft, a permission dialog or an unreadable
  status holds it); the daemon retries a held rename every tick. A window launched before
  this, whose live claude runs `--remote-control` or has Remote Control on, gets renamed
  too. A rename made in the desktop app does not sync back. (CMX-39, CMX-70)

- **A judge takes minutes, not most of an hour.** Each mutation now runs only the tests that
  can observe the mutated file: the test files that import it (Python and, transitively, JS),
  tests that name it, and a per-test coverage map when `pytest-cov` is installed. Selection
  stays inside what the baseline actually ran. The baseline is still one full-suite run, a
  red subset is still final, and every subset survivor is re-run on the full suite before it
  can block, so verdicts mean what they did. An unknown file type, a `conftest.py` or a
  `test_cmd` that is not one pytest invocation runs the full suite, and
  `judge.select_tests: false` turns selection off. The verdict header now shows how long the
  judge took, and each experiment shows how many tests decided it. Held-out experiments
  (CMX-395) are confirmed on the full suite exactly like visible ones, and the consistency
  re-run replays each experiment's own first-run selection; a subset survivor the full suite
  kills is never sampled for it, so it can never be counted flaky. Restart `chela-daemon` to
  pick it up. (CMX-407)

- **A dispatch pause now says that it holds the judges too, and lists what is still
  running.** `chela dispatch --pause`, `--hold-status`, `chela doctor` and the Settings
  "Work dispatcher" row now read "Held: new claims, judges and rework re-spawns. Still
  finishing: N agent(s), M judge(s)", followed by each in-flight run's CMX id and how long
  it has been running. The behaviour is unchanged: a hold starts no new agent of any kind
  and stops nothing already running. The first tick after `--resume` judges any PR that
  went green while the queue was held. Tests now pin both halves. (CMX-413)

- **The Wall's top toolbar row is tighter and lines up with the sidebar.** The row that
  holds the layout toggle and "+ New shell" is now exactly as tall as the sidebar's header,
  so the first pane starts on the header's bottom rule instead of below it. Both controls
  share one height, the gap between the row and the first pane is the same gap the Wall
  leaves between panes, and the height saved goes to the panes rather than showing up as
  empty space under the Wall. The expanded layout toolbar and the phone layout are
  unchanged. Restart `chela-dashboard` to pick it up. (CMX-414)

- **The shipped skills describe chela as it works now.** `orchestrate` teaches the merge
  rule (`chela merge` only; the merge-gate hook denies `gh pr merge` and pushes to the base
  branch; `--override` is approved by the operator), waiting on the judge's verdict rather
  than on the PR opening, `review --request-changes`, `retry`/`reopen` after `needs_human`,
  risk levels, held-out experiments, the tracker's first-line and marker rules, deploying by
  import graph, and releases. `telegram-setup` and `telegram-send` now describe the
  `chela telegram` bridge (a private forum group, auto-topics, the bindings file,
  `secrets.env`) instead of three bare env vars. `chela-setup` matches the 0.14.0 install
  (`--all-extras`, `chela.env`, the hooks plugin, the judge). `handoff` gains a `name`, an
  `argument-hint`, the "this session, distilled" rule and its three live-state probes. Copy
  the updated directories into `~/.claude/skills/` to pick them up. (CMX-415)
- **A test now fails when a skill names a `chela` subcommand or flag the CLI no longer
  has** (`tests/test_skills.py`), every `SKILL.md` must carry a `name` and `description`,
  and `skills/` joins the private-strings sweep in `tests/test_public_media.py`. (CMX-415)

- **The trusted-peer UNSANDBOXED share override can now last up to 14 days, and you pick
  how long in the share dialog.** The dialog offers 30 min · 4 h · 1 day · 7 days ·
  14 days, starting on `CHELA_SHARE_UNSANDBOXED_MINUTES` (still 30 when unset). That
  setting's range is now 1–20160 minutes instead of 1–240. A pick longer than 4 h needs
  the window name typed a second time, and the server refuses it without that. The chosen
  expiry is recorded as `expires_at` in `share.unsandboxed_granted`. The red
  "UNSANDBOXED — guest can type" banner and Active shares show the time left
  (e.g. "12d 4h left"); a row's end time now names the day when it isn't today.
  Upgrading a live share to UNSANDBOXED from Active shares offers the same picker and
  needs the same second confirmation past 4 h. Expiry, Stop, the kill switch, the one-joiner binding and the
  share reaper are unchanged. See `docs/SHARE_SANDBOX.md`. (CMX-419)

- **chelamux's own dispatch queue moved from `TODO.md` to Linear.** The repo's
  `WORKFLOW.md` now reads `tracker: kind: linear` (team `CMX`, ready state `Todo`, done
  state `Done`). The markdown adapter and `TODO.md` handling are unchanged for every other
  workflow. A test pins the repo's own tracker so a revert fails CI. (CMX-438)

- **Window names change only to resolve a duplicate, and a manual rename always wins.** The
  reconcile loop no longer rewrites a unique window name (`shell-N`, a spawn name, anything)
  to its cwd basename — a freshly spawned `shell-1` used to become `tradeplan-2` within one
  tick. When two windows share a name, the newer one (higher window index) gets a
  collision-safe `-N`; the older keeps it. A dashboard rename now flags the window
  `@chela_manual_name`, and a flagged name is never renamed: it keeps the name over a
  non-manual duplicate, and two colliding manual names are both left alone with a warning.
  The dashboard Start button no longer renames the window either; it only locks it. A raw
  `tmux rename-window` is not detected as manual (rename through the dashboard for that),
  though a unique name is never rewritten regardless.
- **The sidebar row and pane header lead with Claude's session title.** Label order is
  manual name > Claude's session title > window name; the short window name rides
  secondary (the pane subtitle, the row's hover). No title yet ⇒ the window name — the old
  cwd-basename fallback is gone. (CMX-62)

- **Docs: the README, `docs/` and the landing site describe 0.15.** The README opens with
  what chela is, one install command (`uv sync --all-extras`) and a first run, then a
  "What's new in 0.15" list. Two new references: `docs/RUNS.md` (a dispatched run from claim
  to merge — the judge, risk caps, `retry` / `reopen` / `close --requeue`, and the one-tap
  override whose approval completes the merge) and `docs/DASHBOARD.md` (the sidebar's folder
  groups, VIEW menu, orchestrator badge and Archive-closes / Unarchive-resumes, window naming
  and Remote Control, the Linear Work board, the Usage view). `docs/GETTING_STARTED.md` has
  one install path and no longer says the agent strikes its own `TODO.md` line or opens its
  own PR. `docs/LINEAR_TRACKER.md` covers the state transitions and the Work board, and
  `docs/RISK_LEVELS.md` the `risk:` label on a Linear issue. The landing pages drop stale
  claims (a "full access" share — shares are view-only unless a sandboxed session allows
  typing; an override that waits; a rework cap of 2). (CMX-71)
- **Docs: `docs/TRUST_AND_APPROVAL_POSTURE.md` §2 no longer says per-agent sandboxing "does
  not exist yet".** The sandbox boundary (`chela/sandbox.py`) is implemented and measured,
  opt-in per workflow (`agent.sandbox: true`) for the coding and rework roles only, and off
  for chelamux's own dispatch, so its agents still run unsandboxed. (CMX-71, CMX-47)
- **Docs: spikes, upstream requests and the dated Symphony conformance review moved to
  `docs/archive/`**, with every reference in code comments, tests and docs updated. (CMX-71)

- **Archiving a session in the sidebar now closes it, like the Claude desktop app, and
  Unarchive resumes it.** Before, Archive only hid the row in one browser: the window
  kept running, and the phone and the laptop disagreed about what was archived. The
  archive is now chela state (`~/.chela/sidebar-archive.json`), so every device shows the
  same set. Archiving a finished session records its session id, folder, window name and
  Remote Control name, then closes the window; its Telegram topic is closed. Unarchive
  opens a new window in the same folder under the same name, runs `claude --resume`, and
  reopens the same Telegram topic. Archive is refused when the session id cannot be
  determined, and for a busy or waiting session or the orchestrator. A settled dispatched
  run is only hidden, because its windows belong to the dispatcher. Archived rows now sit
  in one collapsed **Archived (N)** section at the bottom of the sidebar (View ▸ Status
  All or Archived), not inside their folders. A browser's old archive list is imported
  once, as hidden rows, without closing anything. (CMX-75)

- **CI now tests Python 3.11 and 3.13, and no longer 3.12.** 3.11 is the supported floor
  (`requires-python = ">=3.11"`). 3.13 is what a live install runs today, and CI had never
  tested it. The job count stays at two. `requires-python` and the documented ≥ 3.11 minimum
  are unchanged. (CMX-77)

- **`chela merge --override` no longer waits — it requests, and the approval merges.** The
  command (or its request-only spelling, `chela override request cmx-N --reason "…"`) runs the
  gate minus the judge, records one request bound to the PR's head SHA, prints the request id
  and exits 0. When the operator approves — a one-tap **Approve** button in Telegram, the
  dashboard's `/override/<id>`, or `chela merge-approve <id>` in a plain terminal — the merge
  completes by itself through the normal merge path, after re-checking that the head is
  unchanged, CI is green and the PR is mergeable. Any miss ⇒ no merge, the request is marked
  failed/expired with the reason, and the operator is told. A new push expires a pending
  request; `CHELA_OVERRIDE_WAIT_S` is now its safety TTL (default 24 h, was a 300 s wait).
  (CMX-79)

- **The Linear tracker keeps the 100 most recently finished issues visible and archives
  only older ones.** Before, chela archived an issue as soon as it was Done or Canceled,
  which emptied the board of recent history. A new `tracker:` option, `keep_done`
  (default 100), sets how many finished issues stay unarchived, ranked by when they
  finished. `keep_done: 0` archives every finished issue, as before. Open issues are never
  archived and never count toward it. Closing a merged task now only marks it Done; the
  sweep does the archiving, every 15 minutes and once right after a close. Once the sweep
  has caught up, the free plan's headroom is 250 − open − `keep_done`. `chela doctor`
  still WARNs above 200 non-archived issues, and now names lowering `keep_done` as the
  fix. (CMX-8)

- **Sidebar: an idle session can be archived, and every row has its own Archive.** A live
  Claude session sitting idle (waiting for your next message) now counts in a group's
  "Archive all (N)", and each row's menu (right-click, or its ⋯) has an **Archive** item,
  enabled by the same rule and sent the same way. A busy session, one waiting on you, the
  orchestrator, a judge mid-battery, an unsettled run, and a session whose status cannot be
  read are still never archived. Dispatched runs are still only hidden, never closed. (CMX-81)

### Fixed

- **A Telegram photo or file that times out downloading no longer claims it exceeded the 20 MB
  limit.** The bridge retries a timeout or network error (3 attempts, with backoff) and gives
  both the getFile call and the download a 60s read timeout. If it still fails, the reply names
  the real cause: "⏳ Download timed out (network). Please resend." for a timeout, the size
  message only when Telegram says "file is too big" or the update's `file_size` is over 20 MB
  (then no download is attempted), and "Could not download the file (<reason>)" otherwise. The warning
  log line includes the exception class. (CMX-63, #628)

- **Sandboxed share sessions keep their conversations across relaunches, so `/resume`
  works.** The guest's HOME is a tmpfs, so every relaunch (a crash, an approved
  `chela-request mount`, `chela share-session <name>` again) threw its transcripts away. Each
  workspace now gets one owner-only host directory under `$CHELA_DIR/share-transcripts/`,
  bind-mounted read-write at `~/.claude/projects` in that workspace's guest only — nothing
  else of `~/.claude` is mounted. The launcher refuses to start if the directory can't be
  made or any part of its path is a symlink, `python -m chela.share_sandbox check` accepts
  exactly that mount and still fails on any other, and a guest's `chela-request mount` for it
  (or for `CHELA_DIR`) is still refused. Relaunch the share windows to pick it up — only after
  any live conversation in them is done, since it exists only in that window's tmpfs. (CMX-12)

- **A docs-only PR is parked once for a human instead of being judged four times.** When
  every file a PR changes is documentation (under `docs/` or `changelog.d/`, or a
  top-level `*.md`/`*.rst`/LICENSE-style file other than `WORKFLOW.md`, `CLAUDE.md` or
  `AGENTS.md`), the dispatcher no longer spawns a judge. It moves the run to `needs_human`
  in one write ("docs-only — nothing to verify; needs a human OK / `chela merge
  --override`") and the inbox sends one notice. A `.py` or other non-prose file under
  `docs/` still counts as code, as does any PR that changes code alongside docs. The fix
  also covers judges that do run: a `cannot_verify` that re-running the same commit cannot
  change (docs-only, no `judge.test_cmd`, a deletion-heavy diff) now parks the run the same
  way. Before, it used up the `judge_max_unknown_retries` retry budget, sending one notice
  per attempt. Flaky unknowns are still retried. A parked run is still not mergeable without
  a clean judge or a human override. (CMX-19)

- **An agent can no longer kill or silently re-create the live tmux server.** chela's
  `PreToolUse` hook (plugin 0.2.7) now denies `tmux kill-server`, and any `kill-session`
  aimed at chela's session, unless the command pins a private socket with `-L`/`-S`. A
  `TMUX_TMPDIR=` prefix does not count, because `$TMUX` overrides it inside a pane. The
  self-heal in `scripts/agent-terminals.sh` and `chela.discovery.ensure_session` now starts
  the server from a scrubbed env (no Claude session markers, proxies, `PERF_*`) and unsets
  the same set from the new server's global env. It refuses to heal when `TMUX_TMPDIR`
  names a missing dir, since tmux would fall back to the default socket. A supervisor
  whose cwd was deleted exits instead of healing. (CMX-21)
- **Agent windows no longer inherit a parent Claude session's markers.** `child_env()`
  drops `CLAUDECODE`, `CLAUDE_CODE_SESSION_ID`, `CLAUDE_CODE_CHILD_SESSION` and the other
  markers. Every launch path also scrubs them, along with proxies and `TMUX_TMPDIR`, from
  tmux's global env, so a pm2 service that carries them no longer turns agent transcripts
  off. An operator behind a real proxy can name it in `CHELA_CHILD_ENV_FORWARD`. (CMX-21)

- **Telegram: a screenshot an agent opens with `Read` now relays as a photo.** With tool calls
  hidden (the default) the image-bearing tool result was dropped whole, and the Read tool's
  "[Image: original … Multiply coordinates …]" note relayed as a 👤 user message instead. The
  photo now posts (tool text stays hidden) and the note is never relayed. (CMX-24, #608)

- **A window whose agent moved to a Claude Code background session no longer reads
  "unknown" / "Done".** The pane's own `claude` has no `claude agents --json` entry after the
  move; chela now follows the entry that descends from it (background first, then newest) for
  status, cwd and transcript across the Wall, sidebar, `chela peek`, collab and needs-input
  pushes. `chela peek` and `chela status --sessions` show the background session's name — the
  one ListAgents/SendMessage need. (CMX-28, #612)

- **A window's context bar and live cost show that window's own session.** The statusLine
  hook names the cache file after the window resolved from `$TMUX_PANE`, which every process
  launched from a pane inherits. A Claude Code background session therefore overwrote the
  window's cache, and the bar showed its numbers (69%, $43) instead of the interactive
  session's (9%, $0.8). The hook now also writes `context/by-session/<session_id>.json`.
  `/api/agents/context` and `/api/cost?window=live` read the session the window's own claude
  process is running. The transcript fallback reads that window's own transcript, not the
  newest one in its working directory. Stale per-session files are pruned on the
  snapshot-history schedule. (CMX-29)

- **Dispatched agents and judges show their context bar and cost again.** The statusLine
  hook wrote `context/<window name>.json`, so a window named `<org>/cmx-N-<slug>` pointed
  at a subdirectory that does not exist, and since 2026-10-02 every dispatched window cached
  nothing. The file is now keyed by `chela/cachekey.py`, the one mapping both the hook and
  `chela.context` use. A failed cache write now exits non-zero and appends to
  `$CHELA_DIR/statusline-errors.log` instead of failing silently. (CMX-30)
- **The Cost tab's Today / 7d / 30d views list only agents that ran in the period.** An
  agent with no snapshot inside the window no longer gets a $0.00 row. A live session with
  no reported cost shows `—`, and a real $0.00 is no longer turned into unknown. (CMX-30)

- **iPhone Safari no longer zooms the dashboard in when you tap an input.** On a touch
  device every text control (Settings search, the Jump palette, the new-session form,
  settings fields) and the terminal's hidden input now use a 16px font, the size below which
  iOS auto-zooms on focus. Desktop sizes are unchanged, and pinch-zoom still works. The same
  fix covers the shared-terminal joiner page. Restart `chela-dashboard` to pick it up.
  (CMX-409)

- **The dispatcher watchdog now re-nudges an agent stranded behind Claude Code's ghost
  suggestion.** Claude Code draws a faint suggestion (`❯ Try "fix lint errors"`) into an
  empty prompt, and the watchdog read it from a plain pane capture as typed text, so an
  agent whose brief never landed sat idle for hours and was never re-sent it. The stuck
  check now captures the pane with its escapes and treats a faint-only input line as empty.
  A typed draft still counts as input, and an agent that is visibly working is still never
  nudged. Restart `chela-daemon` to pick it up. (CMX-410)

- **A long judge battery is no longer killed by the judge agent's own Bash tool.** The judge
  agent now runs `chela judge run … --detach`, which starts the battery in its own session
  and process group, logs to `$CHELA_DIR/judge-logs/<task>.log`, and returns at once. Before
  this, the tool backgrounded the run at 10 minutes and killed it at 30, and the agent
  restarted it from zero until the 60-minute judge wall fired. The wall now counts from the
  run's own start rather than from when the agent was spawned. A second run on the same task
  while one is live is refused. `chela doctor` lists a live judge run with its elapsed time
  and experiment progress (`k/N`). Restart `chela-daemon` to pick it up. (CMX-411)

- `docs/SHARE_SANDBOX.md` now says shift+tab cycles manual → accept edits → plan → auto
  (still no bypass). (CMX-418)

- **The active-shares pill no longer covers "+ New shell" on the Wall.** The floating
  kill-switch pill sat in the same top-right corner as the Wall toolbar's "+ New shell", so
  with a share live the two were drawn on top of each other. On desktop the toolbar row now
  carries the pill itself, in line just left of "+ New shell" and at the same height; the
  floating copy steps aside only while the Wall is showing, so a live share is still visible
  on every other tab. With no share, "+ New shell" stays where it was. Phones are unchanged.
  (CMX-422)

- **A pasted or dropped image reaches Claude as an image again.** Since CMX-412, pasting a
  screenshot into a Wall terminal typed `@uploads/paste-….png`, a plain file reference.
  Images (PNG, JPEG, WebP, GIF) now take the earlier image path: the image is saved under
  `/tmp/chela-paste-images/` and its path is typed into the prompt, so Claude Code attaches
  it and shows `[Image #N]`. Every other file still goes to the session's `uploads/` folder
  as `@uploads/<name> `. A drop of several files sends each one to its own path, in order.
  A share guest's image paste is now refused too, as its uploads already were. Restart
  `chela-dashboard` to pick it up. (CMX-423)
- **The phone keybar's Paste button works again, and says why when it can't.** It now
  reads the clipboard the same way Ctrl/Cmd+V does inside a pane. Text is pasted into the
  active terminal. An image, such as a screenshot, takes the image path above. It used to
  ask only for text, so a clipboard holding an image produced nothing and the tap did
  nothing. An empty or blocked clipboard now shows *Empty* or *Denied* on the button
  instead of failing silently. (CMX-423)

- **A live judge run no longer reads as dead when the host's clock is stepped.** A judge
  lock recorded its owner's start as a wall-clock time (`btime` + ticks since boot). The
  kernel recomputes `btime` every time the wall clock is stepped, and WSL2's time sync
  steps it about every 34s. When that happened, the same untouched process failed the
  identity check: `chela doctor` stopped listing in-flight judge runs, the per-task judge
  lock could be taken over, and the watchdog could no longer stop a detached run. Locks
  and run status now also record the start in ticks since boot, which no clock step
  changes, and the identity check compares that first. Older locks still use the
  wall-clock comparison. This also fixes the `judge.live_runs` doctor test, which passed
  when run directly but failed in judge baselines. Restart `chela-daemon` to pick it up.
  (CMX-424)

- **An open dashboard no longer keeps running old JS and CSS after a deploy.** The page
  now loads its scripts and stylesheets from a versioned path (`static/v/<version>/…`,
  a content hash computed when the dashboard starts), so a reload always gets the new
  build and never mixes old modules with new ones. Versioned files are cached for good;
  the page itself is `no-cache`. An open page checks `/api/version` on its regular poll
  and, when the dashboard was redeployed, shows a small "chela was updated — Reload"
  banner. It never reloads by itself, since a share or a typed draft may be in progress.
  Restart `chela-dashboard` to pick it up. (CMX-426)

- **A re-created share shows its guests on every open dashboard page.** Stopping a share
  and sharing the same pane again keeps its relay room but mints a new pairing code. A
  dashboard page that didn't do the stop and re-share itself (another tab, the phone)
  kept its presence session on the old code, so it couldn't read the new guest's
  presence and the pane showed only the host's avatar until a refresh. Each share now
  carries a `share_epoch` on the agents poll. When it changes, the page re-reads the
  share and restarts presence with the new code. When a share stops, its avatars go
  away. A share that hasn't changed keeps one presence connection. Restart
  `chela-dashboard` to pick it up. (CMX-427)

- **The phone keybar's Paste button pastes on an iPhone instead of showing *Empty*.**
  iOS Safari allows one clipboard read per tap, and Paste read the clipboard twice
  (`read()`, then `readText()`), so the second read came back empty. It now reads once.
  Text that iOS provides only as a link (`text/uri-list`) or as rich text (`text/html`,
  converted to plain text) is pasted too, not only `text/plain`. *Empty* now appears only
  when the clipboard has nothing usable. A refused read shows *Denied*. Restart
  `chela-dashboard` to pick it up. (CMX-428)

- **A judge whose window is gone is no longer declared lost while its detached run is
  still going.** Since `chela judge run --detach`, the judge agent stops right after
  launching the run, so its window disappearing is normal. The watchdog read it as a dead
  judge, marked the PR CANNOT VERIFY and spawned a second judge into the same worktree
  while the first run was still mutating it. The watchdog, the judge spawn and
  `--detach` now first read the run's own record (`$CHELA_DIR/judge-logs/<task>.json` and
  the slot lock, identified by pid and `/proc` start ticks). A live run is held: no
  CANNOT VERIFY, no reap, no second judge. Its 60-minute wall counts from the run's own
  start. Only a run whose process is gone (or was recycled) without a verdict becomes
  CANNOT VERIFY. Both records are on disk, so a daemon restart mid-judge finds the run
  too. Restart `chela-daemon` when no detached judge is running. (CMX-429)

- **A failed or partial tracker read no longer marks live work `done`.** Both tracker
  adapters (`markdown`, `gh_issues`) now have `fetch_by_ids(ids)`, the ID-refresh
  operation Symphony SPEC 11.1 requires. It returns `None` when the read failed (a missing
  `TODO.md`, a `gh` timeout, an auth or rate-limit error, bad JSON, or a listing cut off
  before every id was found). Reconcile re-reads by id every run it would close out
  (claimed/running and every review state). On `None` it changes no run from the tracker,
  logs once, and retries next tick. A task the tracker positively reports closed (a struck
  line, a closed issue) reconciles as before. An open issue that only lost its
  `require_label` is no longer treated as done. Restart `chela-daemon` to pick it up.
  (CMX-430)

- **A long judge run is no longer thrown away at 60 minutes while it is still working.**
  The judge wall now grows with the battery: 10 minutes, plus 6 per experiment, plus 1.5 ×
  the run's own baseline for each full-suite confirmation, never below the old 60 minutes
  and never above 3 hours. Past the wall, a run is only called "stuck, not thinking" when
  its progress has stopped. A run still moving is left alone. A run that has finished
  every experiment gets 15 more minutes to publish its verdict. Before this, a
  12-experiment high-risk run that had finished its battery was killed at 62 minutes and
  its real BLOCK was never published. `chela doctor` uses the same rule. The four limits
  are Settings → Dispatch knobs (`CHELA_JUDGE_WALL_BASE_S`,
  `CHELA_JUDGE_WALL_PER_EXPERIMENT_S`, `CHELA_JUDGE_WALL_CEILING_S`,
  `CHELA_JUDGE_WALL_GRACE_S`). Restart `chela-daemon` when no judge is running. (CMX-431)

- **A sandboxed share session keeps working after the host refreshes its Claude login.**
  The proxy sidecar used to mount `~/.claude/.credentials.json` as a single file. Claude
  Code refreshes its login by writing a new file in place of the old one, which revokes
  the old token, and a single-file mount keeps showing the old file. After a few hours
  every request failed with `401 OAuth access token has been revoked`, and the guest was
  offered `/login`. Now the launcher copies only the access token into a per-session
  directory (`~/.chela/share-token/<id>/`), checks for a refresh every 2 seconds, and the
  sidecar mounts that directory read-only. The refresh token no longer enters the
  sidecar, and the guest still mounts no token. On a 401 the proxy reads the token again
  and retries once if it changed. A 401 that persists is answered with a 502, *"The
  host's Claude login expired — ask the operator…"*, instead of sending the guest into
  `/login`. For long-running shares, `docs/SHARE_SANDBOX.md` now recommends a
  `claude setup-token` token in `CHELA_SHARE_SANDBOX_TOKEN_FILE`. (CMX-433)

- **A dashboard deploy no longer ends live shares.** A share's bridge ran inside the
  dashboard, so every `chela-dashboard` restart cut the guest off with no notice. The
  bridges now run in their own service, `chela collab` (PM2 `chela-collab`, added to
  `examples/ecosystem.config.js`), and the dashboard talks to it over an owner-only
  socket. `chela update` restarts that service only when the share code changed — including code a
  bare `git pull` brought in, found by the HEAD the service started on (the HEAD reflog). Each
  live share is also kept in `$CHELA_DIR/shares.json` (0600, never inside a git work
  tree), so whichever process hosts the shares restores them after a restart with the
  same link and pairing code. A share whose window is gone or changed isn't restored. Nor
  is a typing share whose window no longer verifies as a sandbox, or an UNSANDBOXED
  override that expired. Without `chela-collab` the dashboard hosts the shares as before
  and restores them itself. A restored host never reuses an AES-GCM nonce. It also
  refuses replayed keystrokes until the guest answers a fresh challenge. The guest page
  shows "host restarting…" instead of "This share has ended" and reconnects by itself;
  it needs a `wrangler deploy` of `chela/collab-relay`. `chela update` prints "N live
  share(s) will be interrupted" before a restart that would interrupt them, and
  `chela shares` reports the same for a hand deploy. Start the new service with
  `pm2 start ~/.chela/ecosystem.config.js --only chela-collab && pm2 save`. (CMX-434)

- **A sandboxed session's Claude banner no longer says "API Usage Billing".** The guest's
  Claude was given a placeholder `ANTHROPIC_AUTH_TOKEN`, so it labelled itself as API
  billing, though the share proxy always sends your Claude subscription's OAuth token.
  It now gets a placeholder login in its own temporary HOME that names your plan (read
  from the non-secret `subscriptionType` of your credentials file, or
  `CHELA_SHARE_SANDBOX_SUBSCRIPTION`), so the banner reads "Claude Max" / "Claude Pro".
  Every request still goes through the proxy, which still replaces the auth, and the real
  token still never enters the container. When the plan can't be read, the old
  placeholder env is kept. Applies to sandboxed sessions started after the deploy. (CMX-435)

- **Sandboxed sessions show working/idle instead of "unknown".** Claude in the container
  is invisible to chela's usual status sources, so the share proxy now records its
  in-flight model requests in the session's proxy-only directory. `chela peek` and the
  Wall read that for a window that verifies as sandboxed: working while a request is in
  flight (or finished within 4 s, to cover tool calls), idle after. The tooltip says
  "from the sandbox proxy". A permission prompt can't be seen this way, and ordinary
  windows are unchanged. (CMX-436)

- **The dispatcher-sessions toggle in "Recent sessions" reads correctly.** It said
  "Hide 1 dispatcher session hidden". Collapsed, it now reads "Show 1 hidden dispatcher
  session" ("sessions" for more than one); expanded, "Hide dispatcher sessions". Those
  rows still have no Resume button. (CMX-437)

- **Settings → Update flags a service as stale only when code it runs changed.** Before,
  any new commit marked every running `chela-*` service stale: a dashboard-only change
  flagged `chela-daemon`, `chela-telegram` and `chela-agent-terminals`, none of which run
  that code. Now each service's code set is worked out from its import graph, launcher
  scripts and data files, and compared with what changed since the commit the service
  started on. A service whose start commit can't be read is shown as "unknown". The same
  change fixes the `repo.services_current` doctor fact and the restart-only path of
  `chela update`.
- **"Update now" no longer does nothing when there is nothing to pull.** If services are
  stale, the button now reads "Restart stale services (N)" and restarts exactly those. It
  waits while a dispatched run is in flight, like the update path does. Every click now
  shows a message: "already up to date", "restarting X, Y", or the error. The card's text
  also wraps properly on a phone. (CMX-56)

### Security

- **A typing share stops streaming the moment its pane stops verifying as a sandboxed
  session.** Before, swapping the pane's process stopped the guest's typing, but until the
  share ended the guest's view could still show the new host shell (a prompt with an email
  address and cloud region in it). Now a share created with *Allow typing* checks the pane
  before every frame it sends: if the pane's process changed, the container check fails, or
  the check can't tell, that frame is dropped and the share ends, telling the guest "the
  session stopped being a verified sandbox". A view-only share of an ordinary window is
  unchanged. Restart `chela-dashboard` to pick it up. (CMX-416)

- **Secrets from `chela.env` and `secrets.env` no longer reach the processes chela launches.**
  chela loads both files into its own environment, so before this change every agent and
  judge window, workflow hook and judge test suite the daemon started carried them, including
  the `LINEAR_API_KEY` the Linear tracker will use. Those children now get the environment
  without `LINEAR_API_KEY` and without any name shaped like `*_API_KEY`, `*_TOKEN`,
  `*_SECRET` or `*PASSWORD*`. Before opening a tmux window, chela also unsets those names
  from the tmux server's global environment, which is what new windows inherit. Claude
  Code's own credentials and `GH_TOKEN`/`GITHUB_TOKEN` are still passed on. Name any other
  variable a hook needs in `CHELA_CHILD_ENV_FORWARD`. `PATH`, `HOME` and the `CHELA_*`
  settings are unchanged. Restart `chela-daemon` and `chela-dashboard` to pick it up.
  (CMX-425)

- An override approval stays human-only: `chela merge-approve` refuses in a process carrying
  `$CHELA_ACTOR` or a Claude Code session marker, and the merge-gate hook (plugin 0.2.8) also
  denies a session's `python` call into `chela.overrides.decide`. (CMX-79)

## [0.14.0] — 2026-09-30

### Added

- **`--remote-control` on the sessions chela opens for a human.** Windows started by
  `spawn_window` (Telegram `/new`, the dashboard launcher, resumed sessions) and the
  orchestrator persona now launch with Claude Code's `--remote-control <project>`, so they
  are reachable from claude.ai too. Dispatched agents, reworks and judges never get it.
  Default on; set `CHELA_REMOTE_CONTROL=false` to disable. (CMX-375, #526; guards hardened
  in CMX-376, #527)

- **A `warm` dashboard theme** (Settings > Appearance): warm greys, an off-white accent,
  Okabe-Ito sky/orange status colours, and a matching warm terminal palette whose 16 ANSI
  colours each keep their own hue family. (CMX-381)

- **Settings → General → Remote Control: an on/off switch for `--remote-control` on new
  sessions.** `CHELA_REMOTE_CONTROL` was an import-time constant, so turning it off meant
  editing the env and restarting the dashboard, Telegram bridge and daemon. It is now
  `config.remote_control_enabled()`, read per call through `dashboard_setting()` — env beats
  `~/.chela/config.json` (`remote_control`) beats the unchanged ON default — so a toggle in
  the dashboard reaches every process without a restart. It applies to NEW windows only
  (dashboard launcher, Telegram `/new`, the orchestrator persona); a running session keeps
  the flag it launched with, and dispatcher agents/judges still never get it. When
  `CHELA_REMOTE_CONTROL` is set, the switch shows the effective value, is disabled, and says
  so. `examples/chela.env` no longer exports it by default, since an exported value locks the
  switch. (CMX-382)

- **A real-browser suite for the dashboard's layout, status shapes and fonts**
  (`tests/browser/`, Playwright + headless Chromium). jsdom has no layout engine, so the
  CMX-377 restyle's cascade-emulating guards kept missing one more CSS way to break the look
  (`grid-row: 2`, `.app` not a grid, `opacity: 0`, `scale(0)`, a transparent ring border,
  an inset shadow filling the idle ring). The new suite boots the real `index.html` shell
  against stubbed API fixtures (no daemon, no tmux, no network) and asserts what a user
  sees: bounding boxes at 1440×900 and 390×844, each status mark's greyscale painted
  silhouette (same in the sidebar row and the pane header, four states pairwise different,
  never empty), and that Geist actually loads. `npm run test:browser` locally; CI caches
  Chromium and runs it inside `pytest`, where `CHELA_REQUIRE_JS_TESTS=1` makes a browser
  that cannot launch a failure. (CMX-383)

- **Ctrl+, (⌘, on macOS) opens and closes Settings**, like VS Code — from the dashboard
  and from inside a focused terminal pane (the same pane shim that carries Ctrl+K), and
  Esc now closes Settings too. Listed in the keyboard-shortcuts cheatsheet. macOS Chrome
  may keep ⌘, for its own preferences. Needs `pm2 restart chela-dashboard` to pick up the
  pane shim. (CMX-385)

- **The merge rule is enforced inside chela: a direct `gh pr merge` is denied in every Claude session.** chela's plugin (0.2.5) carries a second `PreToolUse` hook on `Bash` (`chela/mergegate.py`, a stdlib-only `command` hook that decides locally from `$CHELA_DIR/mergegate.json` — no dashboard needed) that DENIES `gh pr merge`, `gh api …/pulls/N/merge` and a `git push` to the workflow's base branch or `main` in any repo with a chela workflow, pointing the session at `chela merge cmx-N`. The command is parsed (chains, `bash -c`, `cd x &&`, `-R`, worktrees), and the hook fails open — logged to `$CHELA_DIR/mergegate.log` — when it cannot decide. ⚠️ Restart every agent and orchestrator session (and `claude plugin update chela@chela`) to load it; the orchestrator's own `git push origin dev` is now denied too. (CMX-389)
- **`chela merge cmx-N --override --reason "…"` — the one way past the judge, operator-approved and audited.** It waits `CHELA_OVERRIDE_WAIT_S` (default 300s) for the operator to approve on the dashboard (`/override/<id>`) or with `chela merge-approve <id>` in a plain terminal; a timeout is a deny. The approval (who, head sha, overridden judge state, reason) goes to the event log and the run's review history before the merge, the merge is pinned to the approved head, and the reason rides in the squash body. CI red or not-mergeable still refuse. (CMX-389)
- **Out-of-gate merges are flagged.** When the reconcile finds a chela PR merged without a clean judge on its merged head and without an approved override, it sets `runs.merged_outside_gate` and the inbox pushes `⚠️ cmx-N was merged outside chela's gate` — once. (CMX-389)

- **The judge holds some experiments out, and runs a sample twice.** The judge agent now
  tags about 30% of its mutation experiments `"held_out": true` (at least one once it
  proposes three or more; set per workflow with `judge.held_out_fraction`). They run like
  any other, and a held-out survivor still sends the PR back. The PR comment, the review
  history and the rework prompt only ever say how many held-out guards survived, never
  which, so a rework has to fix the guard itself instead of patching the listed cases.
  Separately, up to two experiments (survivors first; `judge.consistency_sample`) are run a
  second time. One whose KILLED/SURVIVED outcome flips is marked `flaky` in the comment and
  does not count toward blocking. If nothing else blocks, the verdict is `cannot_verify` and
  the operator decides. Each round is recorded privately under
  `$CHELA_DIR/judge-heldout/`. The new `chela judge show <run>` prints rounds-to-clean,
  visible vs held-out survival rates and the flip rate; add `--held-out` to see each
  held-out experiment in full. Needs a `chela-daemon` restart to pick up. (CMX-395)

- **Search in Settings.** A search box at the top of the Settings drawer filters every
  setting across all tabs, VS Code style. It matches a setting's label, its help text and
  hidden keywords (for example, "phone" or "mobile" finds Remote Control). While you
  search, the matching rows are grouped under their tab name and no tab is selected.
  Clearing the search returns you to the tab you were on. Ctrl/⌘+, opens the drawer with
  the search box focused. Esc clears the query first, and a second Esc closes the drawer.
  A query with no results says "No settings match '…'", and screen readers hear the live
  count ("3 settings"). No setting's behaviour, storage or API changed. Needs a
  `chela-dashboard` restart to pick up. (CMX-396)

- **`chela watch --if-unclaimed` — a SessionStart hook can re-claim the orchestrator slot
  after a reboot without ever stealing it.** It registers the calling session exactly like
  `chela watch` only when the pin is empty, undeliverable (dead window or stale tmux epoch —
  the inbox's own `address_state`), or already the caller's; otherwise it changes nothing,
  exits 0 and prints `orchestrator is @N (live) — not claiming`. Plain `chela watch` is
  unchanged. The recommended hook line is in `docs/HOOKS.md`. (CMX-399)

- **"Settings" is now a row in the Ctrl/⌘+K palette — and so in the phone sidebar's
  "Jump to session" box, which drives the same list.** On a phone there is no Ctrl+,, so
  this was the missing way into Settings. Type "sett" (or "settings") and pick it; it
  opens the Settings drawer. Restart `chela-dashboard` to pick it up. (CMX-401)

- **`scripts/demo/record.sh` re-records both demos.** It runs `scripts/demo/fleet.py`, a throwaway fleet on its own tmux server (`tmux -L chela-demo`) with its own `HOME` and `CHELA_DIR` under a fixed demo root (`/tmp/demo`) and a from-scratch environment. The fleet's panes are scripted, and its `claude` only reports their statuses. It looks like a healthy install on camera: chela runs from a git checkout with an upstream (Update reads "up to date"), the demo's own daemon is running, and the "+" menu's Favorites and Recent list the demo projects. Playwright then records the fleet, and ffmpeg writes the MP4s and GIFs over the published files. `tests/test_public_media.py` fails if a README or landing page names a removed view, if any file under `docs/img/` or `landing/` contains the operator's user name, a private window name or a `/home/` path, or if the recorder's environment stops being isolated. (CMX-402)

- **Shared terminals are view only by default; guests can type only into a verified
  sandboxed session.** A new **Guest typing** setting (Settings → Collaboration,
  `CHELA_SHARE_TYPING`, off by default) gates all guest input on the host. With it on, a
  share can allow typing only into a window that verifies LIVE as a sandboxed session,
  checked from the process tree and `docker inspect` at least every 2 s. Refused input
  is dropped, and the guest gets a rate-limited "view only" notice. Start a sandboxed
  session from **New session → Sandboxed session…** (desktop and phone) or with
  `chela share-session <project>`. The new share dialog defaults to *View only* and
  shows why *Allow typing* is disabled. The share pill shows 👁 or ⌨. See
  `docs/SHARE_SANDBOX.md`. (CMX-403)
- **Trusted-peer "Full access — UNSANDBOXED" override**, one share at a time: you type
  the window name to confirm it, it expires after `CHELA_SHARE_UNSANDBOXED_MINUTES`
  (default 30), it's bound to one joiner, it shows a red banner on the pane and the
  share pill, the share kill switch revokes it, and the event log records the grant,
  expiry and revocation. (CMX-403)

- **Per-task risk levels (`high` / `normal` / `low`) that size the judge's search and the
  rework budget.** Mark a TODO bullet `<!-- risk: low -->` (or give a GitHub issue a
  `risk:low` label); unmarked tasks are `normal`, or `high` when the brief's BOUNDARIES touch
  the dispatcher, judge, merge gate, sandbox, inbox or secrets. The judge runs at most 12 / 8
  / 4 experiments and a run gets 5 / 4 / 3 rework rounds by level — all Settings → Dispatch
  knobs. The judge still always runs, and a surviving mutation still blocks at every level.
  The level shows on the Work card, the task modal and the judge's verdict. See
  `docs/RISK_LEVELS.md`. Restart `chela-daemon` and `chela-dashboard` to pick it up. (CMX-405)

### Changed

- **Dashboard restyle: calm desktop-app look — no top bar, shape-coded status, sans chrome.**
  The full-width `<header class="topbar">` is gone; its contents (wordmark, inbox, CPU/RAM/Disk,
  settings) moved into the sidebar's own fixed head/foot, and the two safety-critical pills
  (active-shares kill-switch, native-status-feed warning) now float above the canvas on every
  breakpoint so they stay reachable even while the sidebar is off-canvas on a phone. Chrome type
  is now sans (Geist, vendored, with a Hebrew-capable Heebo fallback — `--ui-font`), while
  terminals and code keep the monospace `--font`. Status is shape-coded, not colour-only
  (Liav is red-weak): Working = filled dot, Needs you = filled triangle, Done = check, Idle =
  hollow ring — the SAME shapes in the sidebar rows, the pane headers, and the taskbar/mobile
  dock. The sidebar gained a prominent "New session" button and a jump-to-session search (reusing
  the existing ⌘K fuzzy jump), and its session list now groups Pinned (the orchestrator session,
  if any) → per-project → Finished (moved last), with each row reading `status mark · title` then
  `state · ctx% ctx`, relative time right-aligned. Pane headers are a fixed 40px; the focused pane
  gets a thin inset accent ring instead of the old outer glow. `--accent` is now reserved for
  focus, selection and the inbox unread dot — the sidebar wordmark is no longer accent-tinted.
  Session rows carry only their status shape, title, "state · ctx" and time — no per-row
  harness-letter badge, orchestrator/dispatched icon or favourite star, and no coloured state
  word; group headers (Pinned/per-project/Finished/Navigate/Sessions) are plain, sentence-case,
  uncoloured labels with no bordered/tinted box. The Wall still fills the full space at every
  density and the pane footer keeps every field (model · branch · context · tokens · cost); no
  theme palette or terminal colour changed.
  (CMX-377, #529)

- **chela's peer messages now arrive NAMED (`Message from @chela`) instead of as an
  anonymous "Another Claude session sent a message:" block.** Measured against Claude Code
  2.1.283 with a throwaway receiver: top-level `from_name` / `fromName` / `name` /
  `from_mode` fields are ignored. A native `SendMessage` names itself by wrapping its text
  in the receiver's own `<cross-session-message from=… from-name=… from-mode=…>` envelope
  inside `message.content`, so `send_peer` and `send_peer_to_pid` now do the same. Only the
  rendering changes. The arrival keeps `origin.kind: "peer"`, the socket-verified sender
  pid, `isMeta`, and the full anti-laundering paragraph in the receiver's context. `role`
  stays `"user"`, `from` stays the reply socket, and `msg_id` stays a uuid4. The name is
  always `chela`, including for relays of another agent's message. `from-name` is
  sender-asserted and the receiver never checks it against the verified pid, so the only
  honest value is the process that really sent the message. The originating agent is still
  attributed in the message text. No `from-mode` is sent, although native senders send
  one. The receiver's inbound gate reads that field, its effect on a bypass-mode receiver
  was never measured, and the name alone is enough to render the message named. After
  merging, restart the PM2 services that import
  `chela.messenger`: `chela-daemon`, `chela-dashboard` and `chela-telegram`. (CMX-380)

- **The repo's JS dev tooling is pnpm, not npm.** `pnpm-lock.yaml` replaces
  `package-lock.json` (same 41 resolved versions; playwright still exactly `1.63.0`), and
  `package.json`'s `packageManager` pins pnpm `10.34.5` — a 10.x, because pnpm 11 crashes on
  the pinned Node 20. pnpm's content-addressed store hardlinks one copy of each package into
  every worktree, which replaces the hand-built shared, symlinked `node_modules` of
  `scripts/npm-shared-install.sh` (CMX-151, #508). CI installs with
  `pnpm install --frozen-lockfile` and keys its Chromium cache on `pnpm-lock.yaml`;
  `hooks.before_run` runs the same install. The judge provisions a pnpm tree with pnpm and,
  for branches forked before the move, still falls back to `npm ci` on a `package-lock.json`
  tree. Run `pnpm install --frozen-lockfile` in your checkout after pulling. (CMX-388)

- **Dashboard restyle fine-tunes.** The sidebar foot is one row again: the CPU/RAM/Disk
  readouts on the left and a single settings gear on the right. The gear opens the same
  menu the ⋮ did, so every action is still one click away. The Decisions inbox moved up
  to the sidebar head. Each Wall pane's title bar now draws its state once, in the
  mockup's order: status shape · title · dim subtitle · a soft pill carrying only the
  word (`working`, `idle`, `done`, `needs you`) · icon buttons. Shape plus word remain the
  non-colour cue. The real-browser suite now guards all of this, plus the chrome font
  on pane headers, pane footers and modals and the phone "+" / `.safety-float` at 390px.
  Needs a `chela-dashboard` restart to pick up. (CMX-393)

- **The Wall's Minimized dock is slimmer and sits lower**: one row of chips is now ~30px
  tall (was ~38px) and ~8px off the bottom of the window (was 18px), and the panes still
  fill exactly down to it. Chips keep their 12px text and a 24px click target. (CMX-397)
- **The Wall's "Grid:" layout toolbar collapses** to one button that shows the current
  layout (or Focus / Auto-arrange) plus a chevron. Click it to show the full row. Picking
  a preset or mode, clicking outside, or pressing Esc folds it back. It starts collapsed
  while Focus is on and expanded otherwise, and remembers your own expand/collapse choice
  in this browser. Needs `pm2 restart chela-dashboard`. (CMX-397)

- **The README and landing-page demos are re-recorded for the new design, from a synthetic fleet.** The desktop clip shows the Wall with one agent in each state (working, needs you, done, idle), then Work's board, runs and schedules, then Settings opened with Ctrl+,. The phone clip shows the pill switcher, the "+" New session menu and the keybar. The README, `landing/index.html` and `landing/docs.html` no longer mention the removed Feed view, and each gains one line per recent feature: the judge-enforced merge gate, Remote Control per session, themes that reach the terminals (plus `warm`), the phone "+", named `@chela` peer messages and Ctrl+, for Settings. (CMX-402)

- A paired share guest can no longer type by default. Earlier shares gave full access to
  anyone holding the code. (CMX-403)

- `CHELA_MAX_REWORKS` (default raised 2 → 5) is now the ceiling over the per-level rework
  caps rather than the cap itself. (CMX-405)

### Fixed

- **The Wall no longer advertises a merged or closed PR as ready for review.** Claude Code
  keeps re-writing a session's PR link after the PR merges, so a pane showed
  "✓ PR ready for review" and `⚑ #N` for a dead PR. `/api/agents` now resolves the PR's
  state (a run record's merged/closed state, else a cached `gh pr view`). The Review bar
  and ⚑ need a confirmed open, non-draft PR. A merged, closed or draft PR shows a dim
  `#N merged`/`#N closed`/`#N draft` chip, and a failed lookup shows a plain `#N`. (CMX-41, #620)

- **`render_prompt` fails loudly on an unknown `{{var}}` instead of shipping it verbatim.**
  A misspelled variable in a `WORKFLOW.md` prompt template (or the built-in first-dispatch and
  rework prompts) now raises `TemplateRenderError` naming every unresolved reference, instead
  of silently reaching the dispatched agent as literal `{{taks_title}}`-style text. A bad
  template fails only the one run attempt (dispatch, watchdog re-nudge, or judge spawn) —
  never the whole tick — and a rework whose re-nudge hits this returns to `changes_requested`
  via the existing rework-failure path rather than being left `running` with no agent behind
  it. (CMX-373, #525)

- **Multi-line Telegram messages no longer strand unsent on the prompt (or split in two).**
  `messenger.send_tmux` pasted multi-line text with a plain `tmux paste-buffer`, which turns
  each newline into a bare CR: Claude Code read a short message as two separate prompts and
  left a longer one sitting unsubmitted on the `❯` line. Measured on 9 days of bridge traffic
  (single-line 225/226 delivered, multi-line 7/12 stranded) and reproduced on an isolated
  tmux server (12/12 stranded at 0.5s, 2s and 5s Enter gaps). It now uses bracketed paste
  (`paste-buffer -p`): 4/4 delivered. (CMX-375, #526)

- **A dispatched agent's first prompt only ever carried the TODO bullet's title — every
  OBJECTIVE/BOUNDARIES/GUARDS/VERIFY continuation line was silently dropped.** `_prompt_vars`
  (spawn), `dispatcher.dry_run`'s own prompt preview, and `_rework_vars` (rework) rendered
  only `{{task_title}}`; `Task.body` — the markdown source's title plus its dedented
  continuation block, or a `gh_issues` issue body — reached `runs.brief` for the task-detail
  modal but no template var ever carried it into a prompt an agent actually reads. Measured
  on CMX-377 (#529): the agent's first user turn held the title only, while `runs.brief` held
  the full 5.9KB body. A `task_body` var (`task.body or ""`, never the literal `None`) now
  reaches WORKFLOW.md (both the repo's own copy and `examples/WORKFLOW.md`), the `--dry-run`
  preview, and the rework prompt — a bare one-line task still renders cleanly with an empty
  body. (CMX-378)

- **A judge stopped by a Claude Code auto-mode classifier outage no longer holds the judge
  slot for an hour or spends a retry.** When every tool call comes back "the server-side
  auto mode classifier gave no verdict", the judge stops and sits idle with no verdict.
  The judge watchdog now spots that case: the judge's native session status is idle and the
  most recent tool results in its transcript carry the outage error. It reaps the judge
  early as an uncounted `cannot_verify`, like CMX-282's expired login. The run is not
  re-judged until a backoff passes, so it does not walk straight back into the same outage.
  The backoff is the new Dispatch knob `judge_outage_backoff_seconds`
  (`CHELA_JUDGE_OUTAGE_BACKOFF_S`, default 600s). The inbox reports it once as
  `⚖️🌩️ judge for <branch> hit a classifier outage — re-judging after <backoff>`. Restart
  `chela-daemon` after upgrading. (CMX-379)

- **Themes stopped at the chrome — the terminals kept GitHub-dark colours whatever theme
  you picked.** Each theme now carries its own terminal palette (`chela/dashboard/term_themes.py`:
  full xterm theme + scrollbar colours) and its own `--term-bg`, applied live inside every
  open terminal on theme change (no ttyd restart, no reload); a newly opened pane comes up
  in the current theme. `CHELA_TERM_THEME` stays the operator override and wins over every
  theme. Needs `pm2 restart chela-dashboard` to pick up. (CMX-381)

- **`depends:` chains longer than one hop blocked forever, silently.** An open task's id
  hashed its raw bullet, `<!-- depends: … -->` marker included, while a `depends:`
  reference hashed the bare title, so no task could depend on a task that itself carried a
  marker. Every task id (open, struck and parked) now hashes the bare title. An unmarked
  task's id is unchanged. A task that carries a marker gets a new id, and on its first
  tick the dispatcher re-keys any in-flight run still under the old id (one transaction,
  logged). Also, a `depends:` marker quoted inside inline code (`` ` ``) no longer counts
  as a real marker. Needs `pm2 restart chela-daemon` to pick up. (CMX-384)

- **On a phone, New session was two taps deep** (open the ☰ drawer, then its "New session"
  button) once the topbar went away in CMX-377. A round "+" now sits beside the ☰ fab at
  phone widths and opens the same New-session menu (Favorites / Recent projects, Init a
  repo, Scheduled task, Shell window) with the drawer left closed. Desktop is unchanged.
  Needs `pm2 restart chela-dashboard` to pick up. (CMX-386)

- **A `done` run whose PR was still open had no way back.** `chela reopen` refused anything
  but `needs_human` and `chela merge` refuses `done`, so the only exit was a hand edit of
  the runs DB. `chela reopen` now also accepts a `done` run — but only when its PR is
  live-read from GitHub as OPEN, its head has moved past the judged commit, and its task is
  not struck `- [x]` in the tracker; anything it cannot read is a refusal. It re-enters
  `awaiting_review` exactly like a `needs_human` reopen, on the fresh head. Needs a
  `chela-daemon` restart to pick up. (CMX-387)

- `package.json`'s `license` said `MIT`; the project is, and always was, `AGPL-3.0-or-later`.
  (CMX-388)

- **A Node program in a workflow hook aborted with exit 134 under pm2.** pm2 forks
  `chela-daemon` through Node's `child_process.fork`, so the daemon carries
  `NODE_CHANNEL_FD` / `NODE_CHANNEL_SERIALIZATION_MODE`, and every hook inherited them. Any
  Node program the hook ran (`pnpm --version` was the measured case) treated fd 3 as its IPC
  channel and dumped core, which failed `before_run` and with it every agent and judge
  launch. A new `chela.envutil.child_env()` drops Node's IPC markers (`NODE_CHANNEL_*`,
  `NODE_UNIQUE_ID`) and pm2's bookkeeping vars (`pm_*`, `PM2_*` except `PM2_HOME`,
  `NODE_APP_INSTANCE`). It is now the env for the `after_create` / `before_run` /
  `after_done` hooks, the agent/judge/human `tmux new-window`, the `tmux new-session` that can
  start the server, and the judge's suite, `uv sync`, `npm ci` and `node --check`. Needs
  `pm2 restart chela-daemon chela-dashboard` to pick up. (CMX-390)

- **A PR URL seen in an agent's transcript can no longer replace the run's real PR.** Claude Code writes a `pr-link` record whenever a PR URL appears in a session, including a test fixture's fake one echoed by pytest. The daemon's completion hop used to prefer the transcript's latest `pr-link` over the `pr_url` already on the row. A run then recorded `https://github.com/o/r/pull/5`, saw it "merged", and was closed, struck and had its worktree deleted. The row's own `pr_url` (recorded daemon-side by `request-push`) now wins, and a transcript URL is used only when it is a PR of the run repo's own GitHub remotes. The daemon also now reads a PR's state from the repo the URL names (`gh pr view N --repo owner/repo`). By number alone, `o/r/pull/5` resolved to this repo's own long-merged PR #5. (CMX-391)

- **A task whose bullet carries a `depends:` marker showed that raw HTML comment as its
  name** in run rows, the Work view and every inbox notice. `Task.title` is now the bare
  visible title (every `<!-- ... -->` marker stripped); `Task.raw` keeps the whole line,
  and the readers that need the marker (depends parsing, the CMX-384 legacy-id re-key,
  `chela doctor`'s unresolved-depends check) read it from there. Task ids are unchanged,
  and the merge strike still finds the line. Needs `pm2 restart chela-daemon` to pick up.
  (CMX-392)

- **The orchestrator pin no longer changes hands silently.** Two Claude sessions sharing one
  directory could swap the decisions-inbox pin, and merge verdicts went to the wrong
  session. The trigger was a `chela watch` that a SessionStart hook ran in the other
  session. Now any pin change that is not the window's own `chela watch` is announced once
  as an `orchestrator.moved` event: a self-heal, `chela restore`, a dashboard or
  auto-launch registration. So is a `chela watch` that takes the pin from an orchestrator
  whose window is still live. The notice goes to the Feed and is delivered once to
  whichever session now holds the pin.
- **A plain `claude` window that shares its directory now has an identity.** chela reads
  Claude Code's own session registry (`<config dir>/sessions/<pid>.json`) and checks its
  `procStart` against the live pid. A window started without `--resume` next to another
  agent in the same directory now resolves to its session. Its hook events carry its
  window instead of `wid: null`, `chela watch` records its identity so self-heal works,
  and the self-heal can find it again in a new window. The self-heal still rebinds only
  to the recorded session identity, never to "a session in that directory". With no
  identity it does nothing and the undeliverable alarm fires. Needs a `chela-daemon`
  restart, plus `chela-dashboard` for hook attribution. (CMX-394)

- **The sidebar-foot Settings/menu popover opens on screen.** Since the menu button
  moved from the old top bar to the sidebar foot, the menu still opened *below* it,
  mostly off the bottom of the window. Popovers anchored to a sidebar or footer control
  (the Settings menu, the New-session menu and the tap tooltips) now share one placement:
  below the control when there is room, otherwise above it. They stay 8px inside the
  viewport on every side, scroll when taller than the window, and follow a window resize
  while open. The phone "+" menu is unchanged. Needs a `chela-dashboard` restart to pick
  up. (CMX-398)

- **The phone sidebar drawer is whole again on iOS Safari.** Its footer (with the Settings
  button) no longer hides behind Safari's bottom toolbar, because the drawer is now `100dvh`
  tall with a `100vh` fallback. The New session + Jump row also shows even when this browser
  once collapsed the desktop sidebar to its icon rail: every collapsed-rail rule is now
  desktop-only. Restart `chela-dashboard` to pick it up. (CMX-404)

### Security

- **A suite chela runs can no longer reach live state or act as the live window.** The judge, `chela judge self-check` and `chela task-finished --self-check-experiments` all run the suite with `CHELA_DIR` set to a fresh temp dir, and with every `CHELA_*` variable, `TMUX` and `TMUX_PANE` removed. chelamux's own test suite now also fences `sqlite3.connect` on the real `~/.chela`, and it refuses to run if `CHELA_DIR` resolves to the operator's real one. (CMX-391)

## [0.13.0] — 2026-09-15

### Added

- **The sandbox boundary for coding and rework agents ships, off by default (issue #502).**
  `docs/SANDBOX_BOUNDARY.md` §6.3's frozen config — `sandbox.enabled`, a narrow
  `filesystem.allowWrite`, `network.strictAllowlist` with a measured `allowedDomains` list,
  and `deny` on the GitHub token file — is now written to a chela-owned
  `<claude config dir>/chela-sandbox.settings.json` and passed as `--settings <path>` to a
  dispatched coding or rework agent whose workflow opts in with `agent.sandbox: true` in its
  `WORKFLOW.md`. The judge is excluded unconditionally (`role="judge"` short-circuits before
  the opt-in check), since it runs a fixed command in a throwaway tree and never pushes,
  while a coding agent takes an arbitrary task description and pushes to a public repo.
  CMX-366 (the completion hop) and CMX-368 (the push/PR-open) already moved the two
  privileged effects that would have made this cosmetic onto the daemon; this lands the
  config itself. A workflow that has not opted in launches byte-identically to today.
  (CMX-369, #518)

### Changed

- **The completion hop is now the daemon's write, not the dispatched agent's (issue #502).**
  `chela task-finished` used to run `UPDATE`s against `~/.chela/scheduler.db` and kill the
  agent's tmux window *inside the agent's own process* — the single thing blocking OS-level
  sandboxing of dispatched agents, since an agent that can write the runs DB can rewrite every
  other run's row, and one that can reach the tmux control socket can `send-keys` into any
  window, including the operator's, with no `from` attribution. `chela task-finished` now
  writes a request marker into the run's own worktree (a path the agent can already write,
  sandboxed or not); `tick()`, unsandboxed and already polling every run, picks the marker up
  and performs the transition to `awaiting_review` and the window kill on the daemon's side. A
  request the daemon cannot apply for an unexplained reason (not: someone else already settled
  the row) now escalates the run to `needs_human` and keeps the marker as evidence, instead of
  logging a warning nobody reads and leaving the row `running` forever.
  (CMX-366)

- **The push and the PR open are now the daemon's, not the dispatched agent's (issue
  #502).** Done Criteria step 5 used to run `git push` then `gh pr create` *inside the
  agent's own process* — the second blocker (after CMX-366's completion hop) standing
  between OS-level sandboxing and being cosmetic. Measured with a matched unsandboxed
  control (`docs/SANDBOX_BOUNDARY.md` §5 B2): masking the GitHub token keeps `gh` working
  (it sends `Authorization: token <token>` verbatim, so the sandbox's substring
  substitution catches it) but not `git push` (it sends `Authorization: Basic
  base64(user:token)`, so the token is never present verbatim and nothing is substituted —
  GitHub rejects the push). `chela request-push <task_id> [--pr-title T --pr-body-file P]`
  now writes a request marker into the run's own worktree instead (a path the agent can
  already write, sandboxed or not); `tick()`, unsandboxed and already polling every run,
  picks the marker up, pushes the branch, and — only when the run has no `pr_url` yet —
  opens the PR, so the token never has to enter the agent's process at all. A request that
  keeps failing gets 5 ticks to succeed before it escalates to `needs_human`, preserving the
  marker as evidence, instead of retrying forever and pinning the run's concurrency slot.
  (CMX-368)

### Fixed

- **`npm-shared-install.sh` now checks whether the declared npm packages actually resolve,
  not whether the shared `node_modules` is empty.** The previous freshness check
  (`-z "$(ls -A ...)"`) still read a partially-unpacked shared install — e.g. one left by an
  `npm ci` interrupted mid-unpack — as "already installed" if it had any content at all, so a
  worktree could symlink into a shared `node_modules` still missing jsdom. The check now
  reuses `chela.judge`'s own `declared_npm_packages` / `_unresolvable` helpers. (CMX-367, #510)

- **`ensure_schema` now names the missing column and the underlying sqlite reason when a
  migration genuinely fails** (a read-only connection whose schema is missing a column,
  e.g. a sandboxed agent's denied `~/.chela` write). Anyone running chela against a database
  they cannot write now gets a loud, specific `SchemaMigrationError` instead of an inscrutable
  `no such column` surfacing later, elsewhere. (CMX-370, #519)

- **`context.ensure_schema` now names the missing column and the underlying sqlite reason when
  a migration genuinely fails** (a read-only connection whose `context_snapshots` schema is
  missing a column, e.g. a sandboxed agent's denied `~/.chela` write). Anyone running chela
  against a database it cannot write now gets a loud, specific `SchemaMigrationError` instead
  of an inscrutable `no such column` surfacing later, elsewhere. (CMX-371, #520)

## [0.12.2] — 2026-09-13

### Added

- **`docs/TRUST_AND_APPROVAL_POSTURE.md` — one page answering who can start an agent, what it
  can do while running, and what its output needs before it changes anything real.** Nothing in
  it is new behaviour: the `require_label` gate, `--permission-mode auto`, `--strict-mcp-config`,
  the pinned judge model, `workspace_escape` and `contract.merge`'s clause list all already
  existed, in four different places. Written to satisfy Symphony SPEC §1 / §10.5's MUST that an
  implementation document its chosen approval, sandbox and operator-confirmation posture, so a
  conformance reader has one citable page instead of four. States plainly what is *not* covered:
  dispatched agents are not sandboxed, which remains the blocker on unattended operation.
  (CMX-364)

### Fixed

- **A failed tracker read was indistinguishable from a genuinely empty queue.** Both
  `GhIssuesSource` and `MarkdownSource` now expose `read_failed`, and `tick()` skips
  absence-implies-completion reconciliation on a tick where the read failed instead of
  treating a broken `gh` call or an unreadable tracker file as "no open tasks." (CMX-363)

- **The `gh_issues` tracker dispatched newest-first and threw the issue body away.** `gh issue
  list` returns created-descending and nothing re-sorted it — `_claim_order` preserves source
  order and `_ready` only filters — so a `gh_issues` workflow was a LIFO stack in which the
  newest issue was always claimed first and the oldest starved forever. The adapter now sorts by
  Symphony SPEC 8.2's key: `created_at` oldest first, nulls last, with the issue number breaking
  ties. `--json` also now requests `body`, so a dispatch brief carries the issue's actual text
  instead of degrading to its URL — which the "review against the dispatch brief" contract
  depends on. Both were latent: this adapter has never been the configured tracker for this
  repo, so they would have bitten on the first day of a migration. (CMX-365)

## [0.12.1] — 2026-09-12

### Fixed

- **A merged PR whose run row was still `running` never reconciled.** A rework spawn
  (`_respawn_rework`) flips an existing review-state row — one that already owns an open
  PR — back to `running`, and that could race a human merging the PR out of band: the row
  would sit `running` forever, with nothing to close it and nothing to tear down the now-
  pointless agent's window, until the (much slower) idle watchdog eventually caught it. The
  merge-reconcile path now also closes an already-`running` row once its PR merges —
  killing its window — while leaving any `running` row whose PR is *not* merged completely
  alone. (CMX-360, #491)

- **A failed `chela restore --resume` no longer leaks the tmux window it opened.**
  When the post-launch liveness check concludes `claude --resume` never came up alive,
  `resume()` now closes exactly the window `spawn_window` handed back before reporting
  `resume-failed`. Previously that window was left behind as a bare `bash` shell —
  permanently, once the retry bound kicked in — and `chela status` counted it as a
  fleet member. A launch that opens no window (spawn itself failing) or returns no wid
  still closes nothing, and a genuinely successful resume still never touches its own
  window. (#473)

- **The test suite can no longer issue a live `tmux kill-window` against the operator's
  default socket.** `tests/test_restore_cli.py`'s own `live_stores`/`resume()` end-to-end
  tests left `_default_kill_window` unstubbed in two places, so a failed liveness check
  during `--resume` shelled out to a real `tmux kill-window -t @99` — a plausible live
  window id — on whichever tmux server the suite happened to run against. Both tests now
  stub the leaf, and `tests/conftest.py` gained a session-wide fence
  (`_no_live_tmux_mutation`) that fails any test outright if a mutating tmux subcommand
  (`kill-window`, `kill-session`, `new-window`, `new-session`, `send-keys`,
  `rename-window`, `respawn-*`) reaches the real tmux binary on the default socket from
  ANY code path, not just this one. (#494)

## [0.12.0] — 2026-09-11

### Added

- **The sidebar shows a `done` state for a regular session with unread agent output.** A
  session whose agent finished replying since the human's last prompt now gets a distinct
  `done` badge (color plus the literal word `done`, not color alone) so it isn't confused
  with `waiting`/`needs you`. (CMX-359, #485)

## [0.11.1] — 2026-09-10

### Fixed

- **`chela wait`'s poll cadence is now guarded, and `_poll` actually reads it.** `POLL_INTERVAL`
  was referenced by no test — raising it to a `sleep 30/60` value passed the whole suite, at
  which point `chela wait` *is* the polling loop it exists to replace (#478). Worse, the
  constant was partly inert: `_poll(..., interval=POLL_INTERVAL)` bound it as a default
  argument at import, so changing it at runtime had no effect. `_poll` now reads the module
  global fresh each iteration, and the cadence is bounded on **both** sides against the live
  value — a ceiling catches a too-coarse interval, a floor catches a busy-spin that ignores
  the constant and sleeps near zero.
- **`wait_for`'s total-`unknown`-on-epoch-change branch is documented as deliberate** (#479).
  Every caller runs inside a pane of the tmux server it is watching (`cmd_wait` passes
  `by=orchestrator.self_wid()`), so a restart kills the waiter along with the server — there is
  no surviving process to hand a re-resolved wid to, unlike `inbox.resolve_heal`, which heals
  *stored* state a later process reads back. This holds for `CHELA_RESTORE_RESUME`'s
  same-session/new-wid relaunch too, which can only run after the old server is already gone.
  Documented rather than building unreachable re-resolve machinery.
- Two new entries in `docs/defeat_shapes/`, both found by the judge on this PR: a paired accept
  case whose stub **discarded the argument under test** (a recurrence of shape 18 in a new
  module), and a cadence assertion that **bounded the observed interval only from above**.

- **A PR merged before its judge ran no longer ships silently unjudged.** A run whose PR was
  merged (by a human, out from under a judge that was never scheduled for it) used to reach
  `done` with `judge_state` still `''` — the same sentinel an un-judged-*yet* row carries, so
  "not yet" and "never" read as the same thing. That row now gets a distinct terminal
  `judge_state` (`unjudged_merged`), a decisions-inbox event so a human hears about it once,
  and — since the changelog gate normally rides on the judge — a non-blocking note when the
  merged diff touches non-docs files with no `changelog.d/` fragment. (CMX-358, #480)

- **`chela wait`'s cadence guard blamed `_poll` for sleeps it never made, and went red on
  an unrelated PR.** `wait.time` *is* the stdlib `time` module, so the guard's stub —
  `monkeypatch.setattr(wait.time, "sleep", ...)` — replaced `time.sleep` **process-wide**
  and recorded every sleep any thread in that process happened to make. One leaked daemon
  thread calling `time.sleep(0.001)` was enough to fail the floor assertion with
  `[0.5, 0.5, 0.5, 0.5, 0.5, 0.001, 0.5, 0.5]` — a guard reporting a defect in code it was
  not watching. `_poll` now sleeps through a `wait._sleep` seam that only this module
  calls, and the stubs observe that instead, so an unrelated sleeper cannot enter the
  recording at all. Pinned by a counterweight that runs a noisy thread alongside the wait
  and asserts it stays out of the reading.

## [0.11.0] — 2026-09-10

### Added

- **`chela wait <wid|task> [--until done|blocked] [--timeout Ns]`** — block until delegated
  work finishes or blocks, instead of hand-rolling `until … sleep 60` around a `sqlite3`
  query. It reads the durable event log rather than tmux, so it cannot miss a transition
  that happened between two samples, and it distinguishes **satisfied / timed out /
  unknown** with separate exit codes — a timeout is a result you can branch on, not silence.
  A window target is auto-watched if it isn't already, which is what stamps the tmux epoch
  the guard below relies on. Issue #456.
  - ⛔ **A wait is never satisfied by a different session that inherited the wid.** tmux
    reissues small window ids per server, so `@3` after a restart may name a stranger.
    `inbox.agent_events` refuses to attribute that stranger's busy→idle to the old watch and
    emits `watch_epoch_lost`; `chela wait` reports it as `unknown`, never as a completed
    dispatch. Guarded by driving a real epoch change with a stranger going idle *exactly as
    a genuine finish would look*.
- **`chela drive --force / --wait / --timeout`** — `chela drive` now refuses to type into a
  target that is already **blocked** on a prompt, where prose would be read as the answer to
  that prompt and race whatever it is actually waiting on. `--force` is the deliberate
  override for the one legitimate case (the prompt is a free-text question this message
  answers); `--wait`/`--timeout` make the send atomic with a wait.

- **The decisions inbox is documented on the site at last.** "The orchestration loop" —
  one of chela's three stated pillars — appeared **nowhere** on chela.pages.dev: not in
  `docs.html`, not on the landing page, which pitched schedule + dispatch + wall only. It is
  now a first-class docs section (`#orchestration`) and a fourth feature card on the landing
  page, alongside new sections for **agent rooms** (`#rooms`), **agent autonomy and the
  resource-isolation gap** (`#autonomy`), and **how it works** (`#internals`, including the
  HTTP API and why chela does not depend on a structured agent protocol).
- `docs/README.md` — an index separating the user-facing references from the internal design
  records, so the directory stops reading as one undifferentiated pile.

- **A sticky sidebar table of contents on the docs page**, with scroll-spy marking the
  section you are actually reading. At 13 sections the old inline card had stopped being
  navigation and become a list you scrolled past once. The same `<nav class="toc">` element
  is the card on narrow screens and the sidebar on wide ones — one element, two behaviours,
  so there is no second copy of the section list to drift.

### Changed

- **README cut from 811 lines to ~150** — a front door, not a manual: what chela is, install,
  a five-step quickstart, and a map into the docs site. Everything it used to carry in full
  now lives at [chela.pages.dev/docs](https://chela.pages.dev/docs).

### Fixed

- **The judge inherited the operator's own `chela.env`, so a machine configured for daily
  use failed tests the PR under judgment had nothing to do with.** `_no_color_env()` built
  the suite subprocess's environment from `dict(os.environ)` — but the judge is itself a
  normal chela process, so importing `chela.config` had already `setdefault`-ed every key
  from the real `$CHELA_DIR/chela.env` into it. That silently undid the fence
  `tests/conftest.py` maintains (`CHELA_ENV_FILE=""`) precisely to keep a developer's
  config out of a test run. Observed with `CHELA_RESTORE_RESUME=true` set for real use:
  `test_chela_restore_resume_is_disabled_by_default_falls_back_to_read_only` failed inside
  `chela judge self-check` and `chela task-finished`, stranding a run whose own code was
  fine. Every key `config.load_env_file` sets is now popped, so only a genuine `export` in
  the judge's shell reaches the suite — the same shape as the existing `FORCE_COLOR` /
  `NODE_CHANNEL_FD` pops, generalised instead of extended one variable at a time.

- **The landing site said "MIT licensed" in both footers.** chela has been **AGPL v3** since
  after 0.3.0 (`LICENSE`, and the README badge). The public site had been stating the wrong
  licence to every visitor.

- **The page heading sat flush against the left edge of a phone screen.** `.doc-hero`'s
  `padding:36px 0 8px` shorthand zeroed the side padding it was inheriting from `.wrap`;
  `padding-block` leaves the gutter alone. The landing page's `.hero` had the identical bug
  and is fixed with it.

## [0.10.5] — 2026-09-10

### Fixed

- **Closed seven post-merge proof gaps in `chela restore --resume`'s liveness check and
  `resume_state`'s durable retry bound (issue #471), no production behavior change.** The
  new fixtures now prove: a non-alive poll RESETS the consecutive-confirmation streak; the
  real `_LIVENESS_DELAY_S` actually spaces confirmations apart; a relaunch that only becomes
  alive after a couple of polls is still confirmed (the live-fleet-consequence gap — a false
  negative here combines with `MAX_TRIES=1` to permanently block a healthy session);
  `resume_state.blocked_reason` still blocks an at-limit session with an empty persisted
  reason; `resume_state._save` is atomic under an interrupted `os.replace`; `record_failure`
  raises rather than swallowing a store failure; and `record_failure` refuses a falsy session
  id. (CMX-355, issue #471)

## [0.10.4] — 2026-09-10

### Fixed

- **`chela restore --resume` verifies a relaunched session came up alive before calling it
  `RESUMED`, and stops retrying a session whose resume keeps failing.** Previously it
  reported `RESUMED` as soon as `spawn_window()` opened a tmux window — nothing confirmed
  `claude --resume <sid>` actually started or stayed up, so a relaunch that died a moment
  later was archived as a success. It now polls for the resumed session to be genuinely
  alive at its new address before archiving the row; a launch whose window opened but whose
  session never came up is reported `RESUME_FAILED` and left untouched. A failed resume is
  also now persisted per session id (`resume-attempts.json`), so a session that keeps
  coming up dead is no longer relaunched on every subsequent `chela restore --resume` pass —
  it is reported as blocked instead. (CMX-353, #468)

- **Tests and judge mutation runs can no longer reach the operator's real pm2 daemon.**
  Nothing set `PM2_HOME`, so any `pm2` subprocess the suite (or a judge's mutation cycle)
  could reach — including from a route the in-process `_no_live_pm2_restart` fence never
  sees, such as the real daemon `tests/test_graceful_shutdown.py` spawns, or a background
  thread that outlives the test that started it — defaulted to `~/.pm2`, the machine's
  real God Daemon. `tests/conftest.py` now points `PM2_HOME` at one throwaway home for the
  whole test session, with its own teardown that kills that home's daemon and removes it
  so nothing survives the run; a `pm2 jlist` read against it answers `[]`, which is the
  same "nothing to restart" case `chela.update` already treats as normal. (CMX-354, issue #466)

## [0.10.3] — 2026-09-09

### Added

- **`chela restore --resume` can now actually relaunch a MANUAL row, instead of only
  printing the `claude --resume` one-liner for a human to run by hand.** Disabled by
  default (set `CHELA_RESTORE_RESUME=true` to opt in) — without it the flag falls back to
  the read-only report. A REVIVABLE row is still only ever re-stamped, never relaunched
  (relaunching an already-live session would fork it into two processes racing one
  worktree, CMX-346's failure mode); a row is refused and left untouched if its session id
  doesn't look like one, if it was already resumed earlier in the same pass, or if a
  `dispatcher.runs` row still marks its task `claimed`/`running` (the CMX-282 lesson — a
  task the dispatcher itself still owns is not this command's to relaunch out from under
  it). A resumed `inbox.orchestrator` row is re-registered at its fresh address in the same
  pass, so the inbox comes back armed. (CMX-350, issue #457)

- **The wall now shows progress inside a dispatched run, not just its status.** Claude
  Code already writes a structured task list per session
  (`~/.claude/tasks/<session-id>/N.json`); nothing read it before. `/api/dispatcher` now
  joins each run to its own list (a new `chela.tasklists` reader, read-only) and every
  run card/row carries a `4/7`-style chip showing progress, the current in-progress
  task's subject, and any blocked-by relationships as a tooltip. The join is by SESSION
  ID, verified against the run's own recorded tmux epoch — not by window id alone — so a
  window id tmux reissues after a restart can never attach one agent's tasks to another
  run's row. A run with no task directory (most of them) renders exactly as before —
  never "0/0". (CMX-352, #462)

### Changed

- **The TUI status/gate/dialog detector reads its regex patterns from a manifest,
  not hardcoded Python.** `chela/telegram/panescan.py` used to bake every
  Claude-Code-chrome regex (permission gates, Bash approval, AskUserQuestion,
  ExitPlanMode, checkpoint restore, Settings, the status-line spinner glyphs) into
  the module — a chrome change cost a code change, a PR, a judge round, a release,
  and a deploy. The pattern tables now live in `chela/telegram/detection_manifest.toml`
  (bundled with the package) and can be replaced, without a `chela-telegram` restart,
  by dropping a full override manifest at `$CHELA_DIR/agent-detection/claude-code.toml`
  — picked up on the next pane scan via an mtime check. With no override present,
  detection is unchanged: same patterns, same order, same glyphs. A present but
  malformed/unreadable override is logged at ERROR and the bundled manifest is used
  instead of silently classifying every pane as unrecognized (the CMX-337/#434 shape
  this is meant to stop repeating). The scanning algorithm itself did not move — only
  the data it scans against.

### Fixed

- **The Telegram relay no longer dumps a skill's entire body into the chat.** A `Skill`
  invocation's full body (its whole `SKILL.md`, sometimes 100K+ chars) arrives as a separate
  synthetic record keyed by `sourceToolUseID`; it's now replaced with a short
  `Loaded skill: <name>` marker, including when the originating tool_use has fallen outside
  the current read window (e.g. the transcript monitor skipped to EOF on a large file) — that
  case previously still relayed the raw body. (CMX-348, #455)

- **A flapping `CANNOT VERIFY` no longer pages once per flap, and its diagnostic detail
  now reaches the log and the push.** `runtime_truth.audit` gives a `cannot_verify`
  observation ONE immediate re-check before it counts as red — an unknown that clears on
  retry (a transient `pytest --collect-only` hiccup, say) was never a finding, only a
  bad tick. A real ERROR is unaffected: it still pages on its first edge, with no added
  latency. `doctor.check_and_notify` also now carries the finding's `detail` (truncated)
  into both `log.error` and the notification body, instead of the title alone — the exit
  code and stderr fragment a `CANNOT VERIFY` finding already captures were previously
  logged nowhere. (CMX-349, #459)

## [0.10.2] — 2026-09-07

### Added

- **The test suite can no longer restart the operator's real PM2 services.** An autouse
  fence in `tests/conftest.py` blocks any real `pm2 restart` reaching `chela.update._sh`,
  with a test proving the fence itself raises. Found the hard way: an existing test that
  drove `update.apply()` without stubbing `_sh` read this box's actual `pm2 jlist`, found
  the real `chela-*` services older than a freshly-cloned fixture's commit, and restarted
  all four — the same shape as the notify module's 109-push incident, one call site over.
  Reads (`pm2 jlist`) are deliberately still allowed through unstubbed. (CMX-346, #452)

### Fixed

- **`chela update` no longer reports success while leaving services on old code.** Its
  "already up to date" early return skipped straight past the `pm2 restart`, so a checkout
  that advanced by any route other than `chela update` itself — a bare `git pull`, a hand
  `main → dev` back-merge, a rebase, or a previous `apply()` that died between "pull" and
  "restart" — printed `up to date — nothing to do` while `chela doctor` went on reporting
  the same services as predating the checked-out commit. The early return now consults
  `services_running_stale_code()` (the fact `doctor` already reports) and restarts exactly
  the services it names, on the same "must not live behind *we just pulled*" reasoning
  already applied to the plugin refresh three lines above it. Services
  that already match HEAD are still left alone, so a genuine no-op update stays free. The
  CLI headline, the unattended auto-update log and its notification now name this
  restart-only catch-up distinctly from both "nothing to do" and "pulled N commits".
  (CMX-346, #452, issue #451)

- **`chela update`'s restart-only catch-up (CMX-346) now syncs dependencies before
  restarting a stale service, same as the pull path.** The commit that made a service stale
  can arrive by a route that never ran `uv sync` (a bare `git pull`, a hand `main → dev`
  back-merge, a rebase, or a previous `apply()` that died between "pull" and "pm2 restart") —
  if it moved `uv.lock`, restarting straight onto it risked bringing the service up on new
  code against old dependencies. The restart-only branch now runs `uv sync --all-extras`
  first and fails the step (without restarting) if it doesn't succeed, on the same ordering
  the pull path already used. (CMX-347, #454, issue #453)

## [0.10.1] — 2026-09-04

### Fixed

- **A conflicted PR now reports `ci:blocked (conflicts)` instead of `ci:none`.** A
  `pull_request` workflow runs against the merge *preview*, and GitHub cannot build one for a
  branch that conflicts with its base — so it runs nothing at all: no queued run, no failed
  run, no annotation, just an empty checks section. That made `ci:none` mean two different
  things, and the merge gate waits for green, so a conflicted PR waited **forever** while
  looking merely queued. Observed on #420, where three consecutive pushes produced zero
  workflow runs. `ci_report_state()` now combines the checks cache with GitHub's live
  `mergeable` verdict. It deliberately does not treat an empty check list as failure (a fresh
  push has no checks either — the discriminator is `mergeable`, not absence), does not read
  `mergeable == null` as clean or conflicting (GitHub computes it asynchronously, so `null`
  is a third state and stays unknown), and cannot latch (the state is derived from the
  current value on every call, so it clears itself the moment a rebase resolves the
  conflict). (CMX-341, #426)

## [0.10.0] — 2026-09-04

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

- **`chela restore --retire-empty`** — a narrower complement to `--apply` that
  archives-then-removes only the MANUAL rows with nothing on record at all (no cwd, no
  session — `manual_command()` cannot even offer a relaunch one-liner). After two WSL
  distro teardowns in one day, `chela restore` was classifying hundreds of these
  provably-unrecoverable rows, drowning the handful that actually carried a fix; the only
  documented way to clear one was hand-editing a store. `--retire-empty` reuses `apply()`'s
  own writers verbatim, so it retires through the exact same archive-before-remove path
  `--apply` already uses — never a REVIVABLE row, never a MANUAL row that still carries a
  cwd/session, and never `telegram-bindings.json`. (CMX-323)

- **`chela judge ack-blocked-race <run> [--by NAME] [--note TEXT]`** acknowledges a
  `J_BLOCKED_RACE` verdict that can never resolve on its own — the PR is merged or closed, so
  nothing will ever push a new head past the judged commit for `_blocked_race_resolved`'s
  `judge_sha != pr_head_sha` check to catch, and `chela doctor` would otherwise report the row
  forever. It stamps who/when/why acknowledged it without rewriting `judge_state`,
  `judge_detail`, or `judge_sha` — the original verdict stays readable exactly as the judge
  wrote it. (CMX-336, #431)

### Changed

- **`chela update --check` and the daemon's update notifier now treat "no upstream
  configured" as UNKNOWN, not "up to date".** Previously a checkout with no upstream
  silently reported 0 commits behind, so `--check`'s exit code and the daemon's notifier both
  went permanently, unannouncedly silent. Both now warn once (log line + push, subject to
  `notify.enabled()`) on the transition into the unknown state, tracked via a new
  `UNKNOWN_BEHIND` sentinel, and `--check` exits nonzero for it. A checkout that later gains
  an upstream and falls genuinely behind still gets the ordinary "update available" notice.
  (CMX-328)

- **Contributing rules now say, explicitly, that session links must not be published.**
  `CONTRIBUTING.md` and the dispatcher's agent prompt in `WORKFLOW.md` both spell out that a
  `Claude-Session: https://claude.ai/code/session_…` trailer belongs in no commit message, PR
  body, issue or changelog fragment in this repo: git history here is public and permanent,
  and the link is unopenable for anyone but its author. `Co-Authored-By:` stays — it
  attributes the work without publishing a session identity.

### Fixed

- **`chela doctor`, `chela update` and `chela plugin` now catch a vanished plugin
  marketplace as a hard load failure, not a manifest-drift warning.** `claude plugin list`
  can report `✘ failed to load — Marketplace <x> not found` while the installed manifest is
  byte-identical to what chela renders — Claude Code resolves a plugin through its
  marketplace at load time, and a gone marketplace is a load failure no manifest comparison
  can ever see. All three commands now read Claude Code's own `known_marketplaces.json`,
  confirm (never guess) when an installed copy's marketplace has vanished from it, and
  report that ahead of (and instead of) any staleness comparison, distinctly worded from
  "STALE INSTALL" since only `claude plugin marketplace add` — not `chela update` — fixes
  it. (CMX-321, #409)

- **A run's `worktree_path` can no longer be recorded as, or deleted from, anywhere outside
  the configured worktrees root.** `git worktree list` reports the repo's own MAIN working
  tree exactly like any linked worktree — first entry, no marker — so when a branch was
  checked out in the main repo (git refuses the same branch in two worktrees, so rework
  resolution fell back to the existing checkout), worktree lookup returned the main repo as
  "the existing worktree for this branch" and callers trusted it enough to store it. Task
  cleanup then deleted it: `~/projects/chelamux`, twice, 90 minutes apart, because the
  poisoned row re-armed on the next daemon start (issue #398). `root` is now a **required**
  argument on `ensure_worktree`, `attach_worktree`, `detached_worktree`,
  `refuse_if_not_a_worktree` and `remove_worktree`, so no call site can silently opt out of
  the check, and root membership is enforced on the write side (a match outside the root is
  never returned as reusable) as well as the delete side. The invariant is "inside the
  worktrees root", deliberately not "is not the main repo": another clone, or another
  project's worktree, is just as unsafe a deletion target and is caught the same way.
  (CMX-325, #414)

- **The auto-merge gate now refuses a clean judge verdict it cannot tie to the commit it is
  about to merge.** `automerge.candidates()` selected on `status == "awaiting_review" and
  judge_state == CLEAN` and never looked at *which* commit had been judged, so a verdict
  about a superseded head counted as a verdict about the current one. Observed on
  2026-08-30: `cmx-321` sat at `judge_state=clean, judge_sha=888f9d6a1` while its real head
  was `5b1c708dd`, because a release-promotion conflict had to be resolved on the branch
  after the judge cleared it — a valid auto-merge candidate by the filter's own terms, whose
  code had never been judged. Latent only because `CHELA_AUTO_MERGE` defaults to off, and it
  is read per-tick, so this was one setting away from merging unjudged code unattended. The
  refusal is at the point of merge and is deliberately *not* a resurrection of the bare
  `judge_sha == pr_head_sha` equality guard, which was itself a defect once: it let a green
  PR merge unjudged on any flake by permanently retiring a commit from judgement.
  (CMX-326, #415)

- **CI now runs on `dev`, not just on `pull_request` previews and `main`.** A `pull_request`
  check only tests a merge preview against `dev` as it stood at check time; once several PRs
  merge in sequence, later merges land on a `dev` no earlier check ever saw and GitHub never
  re-runs them. `.github/workflows/ci.yml` now also triggers on `push` to `dev`, so every
  merge gets a real CI run against the actual integrated tree. (CMX-327, #404)

- **The dashboard's Resume button now checks whether a session's cwd still exists, instead
  of trusting the dispatcher-ownership join alone.** A tmux restart renumbers every window
  (`@889` → `@9`), which silently desyncs `_dispatcher_owned_wid_epochs`'s `(wid, epoch)`
  join from the `runs` table — a reaped run's row landed in the resumable bucket anyway,
  offering a Resume click into a worktree that no longer exists. `_cwd_is_live` now checks
  the filesystem directly, on both `GET /api/restore` (hides dead-cwd rows) and
  `POST /api/restore/resume` (refuses to spawn into one). (CMX-330, #421)

- **Screenshots and other images a tool produces now reach the bound Telegram
  topic again.** This is a **port gap** from the `ccbot` fold, not a regression:
  ccbot carried an outbound photo path (`image_data` → `send_photo`), and
  chelamux never had one — a `tool_result`'s `image` content block (base64
  screenshot data) matched neither of `_tool_result_text`'s branches and was
  silently dropped. `chela.telegram.parser` now extracts those blocks
  (`Message.images`) alongside the existing text, and `BotSender.send_photos`
  posts them — as a single `sendPhoto` or, for several images in one
  `tool_result`, one `sendMediaGroup` call (never N rapid sends that could trip
  flood control) — after the text part, over a hand-rolled multipart transport.
  An image over Telegram's 10 MB bot-upload cap is excluded and reported with a
  visible marker (CMX-311/CMX-322's rule: a dropped message must leave a
  trace), never silently dropped. A text-only `tool_result` is unaffected —
  no `images`, no photo call. (CMX-338)

- **The test suite can no longer push a real notification to the operator's phone.**
  `notify.enabled()` is `bool(NOTIFY_URL)`, read from the environment at import, so on the
  machine that actually runs chela any test reaching a `notify.send` call site pushed a real
  ntfy/Telegram message. `test_auto_apply_sweep_never_calls_apply_with_a_bespoke_repo_arg`
  did exactly that — it drives the real `auto_apply_sweep()` and, alone among its three
  siblings, never stubbed `notify`. CMX-115 had already stripped `CHELA_NOTIFY_URL` from
  every tmux-spawned agent and judge, which is why this stayed hidden: that covers the
  dispatcher's own paths, but not a maintainer running `pytest` or `chela judge run` from
  their own shell. `tests/conftest.py` now fences outbound notifications the way it already
  fences live `~/.chela` state — `NOTIFY_URL` blanked so every call site gates off, and
  `_post` (the single transport funnel) raising `LiveNotificationEscape` if anything reaches
  it anyway. Like `LiveStateEscape` it derives from `BaseException`, because `notify.send`
  wraps its transport in `except Exception` and would otherwise swallow the guard and report
  an ordinary send failure.

- **`chela doctor` no longer goes blind to the window it is run from.** macOS `pgrep`
  excludes the calling process *and all of its ancestors* from the match list unless `-a`
  is passed (`pgrep(1)`), and a chela CLI invoked from inside an agent window has that
  window's own `claude` as an ancestor — so `pgrep -P <pane_pid>` returned nothing and the
  window resolved to `claude_pid=None`. It then dropped out of every population keyed on
  `claude_pid`, including `doctor`'s peer-messaging check, which under-reported by exactly
  the window the operator was sitting in: run from inside a fleet window it listed one
  window without a peer socket, run from outside it listed two. The daemon was never
  affected — it descends from no agent — so the gap only ever appeared in self-diagnosis,
  which is where an operator is most likely to look.

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
