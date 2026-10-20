# Rethinking the test suite: flakes, hermeticity, speed (CMX-283)

Status: **plan**, not a design record. Nothing here is filed yet: Liav reviews it, then the
orchestrator files one Linear issue per block in §5. Measured on `dev` at `d1a1b8e`
(2026-10-20), on the dev box under ordinary fleet load (load average 16-21 on 20 cores, judges and
agents running beside the suite).

Liav, 2026-10-19: "rethink the way we are doing these tests". The suite is ~9,600 tests. CI runs
it on every push. The judge runs it as a baseline, then once more for every mutation that survives
its related-tests subset. Every `task-finished` runs it again. In the week before this was written
there were four flakes, from three root causes: process-global state shared across xdist workers
(CMX-281), real daemons in tests (CMX-254, CMX-273/279) and timing under load (CMX-257).

Contents:

1. [What was measured, and how](#1-what-was-measured-and-how)
2. [Findings](#2-findings)
3. [Diagnosis](#3-diagnosis)
4. [Target architecture](#4-target-architecture)
5. [Slices](#5-slices)
6. [Definition of done](#6-definition-of-done)
7. [Estimate](#7-estimate)
8. [Appendix: reproducing the measurements](#8-appendix-reproducing-the-measurements)

---

## 1. What was measured, and how

| # | Question (from the task) | Source | Window |
|---|---|---|---|
| M1 | Slowest tests, their share, and which spawn real processes | Two full runs at `-n 4 --dist loadfile` (the repo default): one with `--durations=0`, one under a measurement-only plugin (§8.2) that records every `subprocess.Popen` per test | 2026-10-20 |
| M2 | Tests that mutate process-global state, and whether they restore it | The same plugin snapshots `os.environ`, cwd, umask, signal handlers, non-daemon threads, child processes, `chela.*` module identity and `chela.config`'s upper-case attributes before setup and after teardown of every test; plus a static grep | 2026-10-20 |
| M3 | Flake history | 855 CI runs (`gh run list`, 2026-09-23 to 10-19), the logs of all 38 failed runs, `runs.ci_flakes` in `scheduler.db` (CMX-255), `baseline_flakes` in `~/.chela/judge-heldout/*.jsonl` (CMX-195), 218 judge logs, the deflake commits in `git log` | ~26 days |
| M4 | How often the judge's related-tests subset was enough | Every per-mutation line in `~/.chela/judge-logs/*.log` (2,661 mutations, 405 rounds) | 2026-10-01 to 10-20 |
| M5 | (added) Where in-process tests spend their time | `cProfile` on one slow file, then an A/B full run with SQLite `synchronous=OFF` injected by a plugin (§8.3) | 2026-10-20 |

Every number below is from these runs. Where a number is an estimate, it says so and names
the slice that measures it.

---

## 2. Findings

### 2.1 Wall time: 13 minutes, and the suite is waiting rather than computing

| Run (load 16-21) | Wall | Tests | Worker CPU (user + sys) |
|---|---|---|---|
| Baseline, `-n 4 --dist loadfile` | **798 s** (13:18) | 9,569 passed, 20 skipped, 1 xfailed | 1,164 s |
| Same, measurement plugin on | 822 s (+3%, inside the run-to-run noise) | same | |
| Same, SQLite `synchronous=OFF` (§2.3) | **490 s** (8:10) | 1 failed (a timing flake, §2.5), the rest the same | |

Four workers for 798 s is 3,192 worker-seconds, of which 1,164 were spent on a CPU. The workers
were idle about 64% of the time, waiting on fsync, subprocesses and sleeps. The suite is bound by
latency, not by cores. (The task's "~30 min" figure is the slow tail: the judge's own logs give a
median full-suite confirmation of 405 s and a p90 of 827 s, and `WORKFLOW.md` records a 1,850 s run
at load 12-20.)

Collection takes 3 s. Fixture setup is 146 s and teardown 19 s of the 3,005 test-seconds; the
rest is test bodies.

### 2.2 The slow tail, and what spawns processes

Concentration of the 3,005 test-seconds (tests above 5 ms):

| Slowest | Seconds | Share |
|---|---|---|
| 10 tests | 407 | 13.5% |
| 100 tests | 837 | **27.9%** |
| 500 tests | 1,626 | 54.1% |
| 1,000 tests | 2,242 | 74.6% |
| 42 tests at ≥5 s | 623 | 20.7% |

One test is 8.3% of the whole suite on its own:
`test_release.py::test_the_release_tests_pass_under_the_live_state_fence_on_a_real_layout`
(250 s). It clones the repository and runs the release step's nested pytest twice. Under
`--dist loadfile` its file (`test_release.py`, 310 s) is one worker's unit of work, so it sets a
floor of about 5 minutes on any worker count.

Top files: `test_release.py` 310 s · `test_js_suites.py` 300 s (156 JS suites, each a node
process under `chela.procguard`) · `test_tracker_fetch_by_ids.py` 202 s · `test_judge.py` 159 s ·
`test_merge_override.py` 112 s · `test_runtime_truth.py` 99 s · `test_contract.py` 85 s.

What the tests actually spawned (measured, not grepped):

| Program | Spawns | Tests | Note |
|---|---|---|---|
| `git` | 22,898 | 1,427 | `make_repo`-style fixtures; 7,003 of these spawns are in `test_release.py` |
| `systemctl` | **12,847** | 144 | `chela/memcap.py` probing the user slice; 11,173 spawns from `test_runtime_truth.py` alone |
| `tmux` | 2,203 | 591 | Only ~8 files drive a real server on a `-L chelatest-*` socket. The other ~580 tests make 1-2 incidental calls (288 tests make exactly one) against the fenced `chela-tests-no-such-session` |
| `python` (subprocess) | ~970 | ~520 | includes 160 `python -m chela.procguard` (the JS suites), 39 `python -m chela.sweep`, 7 nested `python -m pytest` |
| `pgrep` | 406 | 40 | |
| `gh` | 289 | 237 | the conftest fence's stub script, run through `bash` |
| `node` | 152 | 55 | direct; the JS suites go through procguard |
| `pm2` | 26 | 26 | a real PM2 daemon in a sandboxed `PM2_HOME` |
| `claude` (stub) | 9 | 6 | |

Share of test-seconds by what a test spawned (after the SQLite fix in §2.3, so the shares reflect
the remaining costs):

| Class | Tests | Test-seconds | Share |
|---|---|---|---|
| Spawns nothing | 7,207 | 191 | 11% |
| Spawns only `git` | 763 | 120 | 7% |
| Spawns only incidental `tmux`/`gh` (± git) | 636 | 40 | 2% |
| Spawns anything else: tmux server, pm2, node, python, systemctl, pgrep, claude stubs | 984 | 1,368 | **77%** |

**Ten percent of the tests take three quarters of the time.** Those 984 tests sit in 99 files, and
94 of those files mix them with in-process tests. A tier therefore has to be chosen per test,
not per file.

### 2.3 The hidden cost: SQLite fsync on every fresh database

`cProfile` on one test in `test_tracker_fetch_by_ids.py` (1.7 s; its `gh` is mocked) put 6.5 s of
7.7 s in `dispatcher._db()`. That is 339 `execute` calls at 17 ms each. Every `_db()` call runs
`ensure_schema()`, and a test's database is new, so the table is created and then altered column
by column. Each statement commits, and each commit fsyncs to a WSL2 ext4 virtual disk that four
workers and the fleet share.

A/B test, same files, `-n 0`:

| Files | Default | `PRAGMA synchronous=OFF` injected |
|---|---|---|
| `test_tracker_fetch_by_ids.py` + `test_merge_override.py` (289 tests) | 224.7 s | **15.6 s** (14×) |
| Whole suite at `-n 4` | 798 s | **490 s** (−39%) |

Per file, the A/B moved `test_tracker_fetch_by_ids` from 202 to 2 s, `test_merge_override` from
112 to 10 s, `test_contract` from 85 to 3 s, `test_dispatcher_rework` from 66 to 4 s, and
`test_dispatcher_reopen` from 61 to 2 s. Durability of a scratch database a test deletes buys
nothing. This is the cheapest large win in the plan (T03).

### 2.4 Process-global state

Static grep of `tests/`: no direct `os.environ[...] =`, `os.chdir`, `sys.modules` write or
`sys.path` insert in any test body. The conftest makes all of its environment writes at import,
on purpose (`CHELA_DIR`, `PM2_HOME`, `PATH` with the gh fence, `GH_CONFIG_DIR`,
`CHELA_TMUX_SESSION`, …). Tests use `monkeypatch` (116 files). 15 files call
`importlib.reload`.

Runtime probe, all 9,590 tests, state that differed **after teardown**:

| State | Tests | Where | Restored? |
|---|---|---|---|
| `os.environ` | 1 | `test_config_env.py::test_the_file_configures_a_bare_process`: `config.load_env_file()` calls `os.environ.setdefault` and leaves `CHELA_DASHBOARD_PORT=5005` behind. `test_a_junk_env_file_does_not_crash` does the same thing, but it runs second in that file and finds the key already set, so it leaks only when it runs alone. **Order-dependent, the CMX-281 class.** | No |
| `chela.config` attributes (`DISPATCH_KNOBS`, `_DISPATCH_BY_KEY`, `CONTEXT_CACHE_DIR`, `RESTORE_RESUME_ENABLED`) | 293 | 13 files that `importlib.reload(config)` under a monkeypatched env: `test_restore_cli` 51, `test_timing_settings` 45, `test_dispatch_settings` 44, `test_agent_permission_mode` 34, `test_agent_model` 31, `test_roster` 28, `test_config_env` 21, `test_sessionids` 15, plus 5 more. The env is restored; the module attributes computed from it are not, so the next test on that worker sees the previous test's values until something reloads again. | No |
| Child processes still alive after teardown | 10 | Every "the daemon loop …" test (`test_window_name_follows_folder`, `test_orchestrator_autolaunch`, `test_restore_boot` ×2, `test_main_dispatch_log`, `test_update`, `test_ticklock`, `test_doctor_notify` ×2, `test_notify_denied`): the loop under test leaves a `python` child running. | No |
| cwd, umask, signal handlers (TERM/INT/ALRM/CHLD/HUP/USR1), non-daemon threads, `chela.*` module identity | 0 | | n/a |

The conftest fences that already exist (`_no_live_state`, `_no_live_tmux_mutation`,
`_no_live_gh`, the per-test `CHELA_DIR`, the PM2 sandbox) do their job: the probe found no test
that reaches operator state. What is missing is the other direction: nothing fails a test that
leaves state behind for the next test on its worker.

### 2.5 Flake history

855 CI runs (2026-09-23 to 10-19): 38 failed, 24 re-run (`attempt > 1`), 14 of those green on the
re-run. `runs.ci_flakes`: 4 events on 3 of 84 runs. Judge `baseline_flakes`: 3 tests. Judge
experiment flips (SURVIVED → KILLED on a consistency re-run, i.e. a flaky test under a mutation)
are logged as FLAKY in 12 of the judge logs (about 4 distinct flips, 10-01 to 10-18).

| Test | Failed CI runs | Spawns | Class | State |
|---|---|---|---|---|
| `test_update_apply_route::test_unread_service_freshness_refuses_instead_of_saying_up_to_date` | 7 (dev pushes 10-16 to 10-19) | git | Red on dev repeatedly: likely environment-dependent, maybe a real regression (suspect: CMX-127 #680) | **open** |
| `test_ops_scan::test_leaked_proc_reads_argv_paths_and_skips_unreadable_procs` | 3 + 3 `ci_flakes` | none (reads the real `/proc`) | real host state | fixed, CMX-273 |
| `test_suite_containment::test_any_gh_a_test_runs_by_name_meets_the_failing_stub` | 3 + 1 `ci_flakes` | gh, sh | process-global PATH | fixed, CMX-281 (#792, merged after these measurements) |
| `test_demo_gate::test_make_repo_rebuilds_its_own_root_from_scratch` | 4 | git | a vanishing lock file | fixed, CMX-254 |
| `test_env_leak_hardening::test_supervisor_heal_create_from_a_polluted_env_is_clean` | 3 | tmux server, agent-terminals.sh | real daemon + timing | **open** |
| JS browser/DOM suites (`dashboard`, `wall_toolbar_row`, `sidebar_move_to_group` ×2, `sidebar_view`, `mobile_pills_sidebar_order`) | 6 | node via procguard | timing under load | **open** |
| `test_frontend::test_test_fails_on_a_failing_spec_and_still_runs_the_real_ones` | 2 | nested vitest | timing / nested runner | **open** |
| `test_pm2_home_isolation` (2 tests) | 2 | python, pm2 | real daemon | touched by CMX-195 |
| `test_usage` (2 API tests) | 2 | none | unpinned clock | fixed, CMX-188 |
| `test_judge_watchdog_detached`, `test_judge_battery_progress` (torn read), `test_dispatcher_worktree_gc` (one real tick), `test_judge_baseline_cause`, `test_share_typing_gate` | 1 each | mixed | timing / torn reads | **open** |
| `test_agent_permission_mode::test_spawn_judge_sends_no_remote_control_even_when_enabled`, `test_judge_docs_only_park`, `test_agent_env_strip` | 1 + judge baseline | none / git | shared judge slot, AF_UNIX path length under load | fixed, CMX-257 |
| `test_ticklock::test_a_tick_that_outlives_the_bound_is_refused_and_holds_nothing` | judge baseline | python | timing | **open** |
| `test_judge_sweep::test_a_sigkilled_sweep_is_restored_by_its_guardian_without_a_next_sweep` | seen in this audit's own A/B run | git, python | a 30 s deadline missed by 0.03 s at load 20 | **open** |

Two things follow. First, **about half of the flaky tests spawn nothing.** Their flakes come
from the clock, the real `/proc`, torn reads and shared paths, so a no-subprocess tier alone does
not remove flakes; T05 and T06 are needed too. Second, every open timing flake has a fixed
wall-clock deadline (5-30 s) that holds at load 4 and fails at load 20.

### 2.6 What a judge mutation actually needs

Selection (`chela/judge_select.py`): a mutated file's observers are the test files that import it
(AST for Python, a transitive import closure for JS/TS), the files that name its path, basename or
dotted module, and a pytest-cov map if one exists. A `conftest.py`, an unknown file type, or a file
nothing observes runs the full suite. A KILLED subset is final. A SURVIVED or INVALID subset is
re-run on the **full suite** with the mutation still applied (`judge.py:1980-1986`).

2,661 mutations over 405 rounds (2026-10-01 to 10-20):

| Path | Mutations | Median | p90 | Total time |
|---|---|---|---|---|
| Subset killed it | 1,536 | 74 s | 268 s | 52 h |
| Subset green → full suite (853 SURVIVED, **57 KILLED**, 8 INVALID) | 918 | 405 s | 827 s | **126 h** |
| No selection → full suite | 207 | 466 s | 818 s | 31 h |

- **The full-suite confirmation agreed with the subset 853 times out of 910 decided cases
  (94%)**, and that confirmation is where 126 of the 209 measured hours went. The subset was not
  enough 57 times (6.2% of confirmations, 2.3% of all 2,454 subset runs).
- The 57 cluster. 14 are on `chela/main.py`, where the "subset" was already 2,100-3,700 tests,
  because nearly every CLI test imports `chela.main`. 13 are held-out experiments. 6 are in
  `share_web_proxy.py`/`share_sandbox.py`. The rest are spread one or two per file.
- **Which test killed a confirmed mutation is not recorded.** Neither is whether that test was a
  flake. The log says only "N failed of M run". So none of the 57 can be checked as a real coverage
  gap rather than a flaky test failing under the mutation, and the logged consistency flips show
  the second case happens. T01 records it.
- A round's mutation battery takes a median of 30 min and a p90 of 75 min, about 2.8 full-suite
  runs per round on top of the baseline. A `task-finished` hand-in adds the sweep, the self-check
  and a final run.

---

## 3. Diagnosis

1. **Every surface runs the whole suite, and the whole suite is dominated by a 10% slice that
   spawns real processes** (77% of the time). Judge experiments, agent self-checks, the sweep and
   CI all pay for it, though their questions differ. A mutation experiment asks whether any test
   notices. CI asks whether everything still works end to end.
2. **Most of the in-process cost is accidental.** SQLite fsync accounts for 39% of the wall time,
   `systemctl` for 12,847 spawns, and incidental `tmux`/`gh` calls in 636 tests that only need
   "no server". None of these is what the tests are about.
3. **Isolation is enforced in one direction only.** The fences stop a test from reaching
   operator state. Nothing stops a test from leaving state for the next one on the same worker: an
   env var, a reloaded module attribute, a child process. Under xdist the next test changes from run
   to run, so this class shows up as rare, order-dependent flakes (CMX-281).
4. **Timing assertions are calibrated on an idle box.** The suite runs at load 16-21, next to
   judges and agents. Fixed deadlines (5-30 s) on subprocess progress are the open flake class.
5. **The judge's safety margin, a full-suite confirmation of every survivor, is spent blind.** In
   94% of cases it agrees with the subset. When it disagrees, nothing records why, so its value
   cannot be measured or kept for cheaper.

---

## 4. Target architecture

### 4.1 Two tiers, chosen mechanically

| Tier | What is in it | Who runs it |
|---|---|---|
| **fast** (`-m "not integration"`) | Tests that spawn no process other than `git`: ~8,600 tests (≈90%), ≈350 test-seconds after T03, so an estimated **~2 min wall** at `-n 4`. T08 measures it. | judge experiments and confirmations, `chela judge sweep` / `self-check`, agents' own loop, the `task-finished` sweep and self-check |
| **integration** (`-m integration`) | Tests that start a real tmux server, PM2, node/Vitest, a nested pytest or a `python -m chela…` subprocess, or call `systemctl`/`pgrep`/a `claude` stub: ~980 tests, ≈1,370 test-seconds, an estimated **~6 min wall** once `test_release.py`'s 250 s test stops serializing a worker (T09) | CI (a parallel job), the judge's baseline and final green check, `task-finished`'s last run |

**The tier is enforced, not declared.** In a fast-tier run, a conftest hook makes any
`subprocess.Popen` of a program outside `{git}` fail the test immediately with "mark it
`integration`, or use the fake". A test cannot sit in the fast tier while it spawns tmux, the same
way `_no_live_tmux_mutation` stops it from touching the live server today. The initial marking comes
from the measurement plugin's manifest (§8.2), which T02 commits. Marks are per test, since 94 of
the 99 files are mixed.

`git` stays in the fast tier. It is local, has no daemon, is deterministic, and it is the
fixture layer for 1,427 tests (120 test-seconds in total). Faking it would cost more than it saves.

### 4.2 Fakes at the boundary

- **tmux.** chela calls tmux from 87 argv sites in 20 modules, so a single seam module is a large
  refactor that this plan does not propose. Instead, an autouse conftest fake intercepts any
  `tmux` argv that is not on a `-L chelatest-*` socket. It answers "no server running on …" (exit 1)
  without starting a process, which is what the incidental callers already receive today from the
  fenced `chela-tests-no-such-session`. A test that wants richer behaviour asks for
  `fake_tmux(windows=…)`. A test that needs the real thing asks for `real_tmux`, which is how it
  becomes `integration`. The existing mutation fence stays as the strict mode under all of this.
- **gh.** The fence's stub on `PATH` stays for subprocesses that run `gh` by name. In-process
  callers get the same answers from a Python fake, without a `bash` startup per call (237 tests).
- **systemctl / memcap.** The memcap probe is faked by default in conftest, and the tests that
  are about memcap opt back in. This removes 12,847 spawns.
- **PM2, agent-terminals, nested pytest.** Kept real and in the integration tier. They test the
  sandboxing itself (`test_pm2_home_isolation`, `test_suite_containment`), and a fake would test
  the fake.

### 4.3 The leak guard

An autouse hook, built from the measurement plugin. It takes a snapshot before setup and
compares after teardown, and a difference fails the test with a diff that names the state:

| Checked | Exempt |
|---|---|
| `os.environ` (keys and values) | `PYTEST_CURRENT_TEST` |
| cwd, umask | |
| handlers for SIGTERM, SIGINT, SIGALRM, SIGCHLD, SIGHUP, SIGUSR1 | |
| non-daemon threads | |
| child processes of the worker (`/proc/*/stat` ppid scan) | none: a daemon test reaps its own child |
| `chela.config` upper-case attributes | none: tests that reload `config` use a `reload_config` fixture that reloads it back at teardown |

Cost measured by the probe: +3% wall, inside the noise. It does not need to cover `sys.modules`:
no test replaces a `chela.*` module object, and `importlib.reload` keeps the object, which is why
the config-attribute check exists. A guard test proves the hook is wired: a planted test that leaks
an env var fails, and the same test with the leak removed passes.

### 4.4 Timing under load

The rule for new and fixed tests: **wait on an event, never on a wall-clock budget.** Poll a
condition until it holds, and fail only at a generous outer bound (`CHELA_TEST_TIMEOUT_SCALE`
multiplies every such bound; CI and the judge set it from the load average). A test that needs time
to pass uses an injected clock (CMX-105 and CMX-188 already did this for `test_probe_batch` and
`test_usage`).

### 4.5 What the judge runs

| Step | Today | After T11 |
|---|---|---|
| Baseline | full suite | full suite, once per round. It is still the "is the PR green" fact. |
| Per-mutation subset | related tests, all tiers | related tests in the **fast** tier, plus any related **integration** tests (they are rare and are named explicitly) |
| Subset survivor confirmation | full suite (median 405 s) | **fast tier in full** (an estimated ~2 min), plus any integration tests that import or name the mutated file. Full-suite confirmation only for a mutation in `conftest.py`, a file without observers, or one listed in `judge.full_confirm` |
| Final | `task-finished` runs the full suite | unchanged |

**This row is decided by data, not by this plan.** T11 is blocked by two weeks of T01 records. If
more than a few of the 57-in-20-days kind of escapes turn out to be killed only by integration
tests that selection does not reach, T13 (selection improvements) comes first and T11 keeps
full-suite confirmation for those files.

---

## 5. Slices

**Rules for every slice.**

1. **≤600 added lines of non-test code**, with the same counting as `FRONTEND_PORT_PLAN.md` §0
   (tests and docs do not count). Most slices here are far smaller. A slice over
   `hand_in.max_pr_lines` hands in with `--oversize-reason "CMX-283 test-architecture slice, ≤600 per plan"`.
2. **No slice weakens a fence.** Every existing conftest fence (`_no_live_state`,
   `_no_live_tmux_mutation`, `_no_live_gh`, the PM2 sandbox, the `CHELA_DIR` refusals) keeps its
   negative-control test green. A slice that changes a fence names that test in its PR body.
3. **A slice that edits `tests/conftest.py` runs the full suite in the judge.** Selection treats
   `conftest.py` as observed by everything. Keep conftest slices small and separate.
4. **Always-human paths** (`WORKFLOW.md`, `.github/workflows/**`, `chela/risktier.py` …) need
   Liav's override even with a clean verdict. T10 and T11 touch them.
5. **Measure before and after.** Every slice states the wall time of the tier it touches, before
   and after, with the T02 report, and quotes the load average at the time.
6. Risk uses the tracker's labels (`docs/RISK_LEVELS.md`).

**Order.** Phase 0 is two independent enablers. Phase 1 holds the cheap, independent wins, and
can run three-wide straight away. Phase 2 builds the tiers on top. The judge change comes last
because it waits on T01's data.

```
Phase 0  T01 (judge records)     T02 (probe + report)
Phase 1  T03 (SQLite)   T04 (leak guard) ── T04b (offenders)   T05 (flakes A)   T06 (flakes B)   T07 (fakes)
Phase 2  T02,T07 ── T08 (tiers) ─┬─ T09 (xdist / test_release)
                                  ├─ T10 (CI split)
                                  └─ T12 (agents' loop)
         T01 (+2 weeks data), T08 ── T11 (judge on the fast tier)      T01 ── T13 (selection)
Phase 3  T14 (ratchets / DoD)
```

### Phase 0: measure

```text
T01 · CMX-283 tests: the judge records which tests killed a mutation, and how long each run took
Scope: every mutation verdict records the failing test ids (from the JUnit report the judge already
  writes) for the subset run and the full-suite run separately, the two durations separately, and
  whether a consistency re-run flipped it. The baseline duration and the per-phase timings
  (baseline / battery / held-out / final) persist per round. Write one structured JSONL record per
  mutation next to judge-heldout (visible and held-out alike), not only the human log line. Adds
  `chela judge stats --since 14d`, which prints the §2.6 table, including "killed only by the full
  suite" grouped by killing test and by that test's tier.
Files: chela/judge.py (_run_selected, the round summary), chela/judge_select.py (record nodes),
  a small chela/judge_stats.py, chela/main.py (subcommand wiring)
Blocked by: none
Tests: a confirmed-KILLED fixture run records the killing node id; a flaky flip records both runs;
  stats over a fixture directory print the expected counts; an unreadable or torn record is
  skipped and counted, never read as a pass
Risk: normal. ≈250 lines.
Saves: nothing directly. It is what T11 and T13 are decided on.
```

```text
T02 · CMX-283 tests: a committed spawn/leak probe and a suite report
Scope: the §8.2 plugin becomes tests/_probe.py, activated only by CHELA_TEST_PROBE=<dir> (off by
  default, zero cost). scripts/test_report.py reads its JSONL plus a --durations log and prints
  §2.1-2.4's tables: wall, top-N share, spawn classes, leaks. It also writes
  tests/tiers.manifest (one nodeid per line for tests that spawn a non-git process), the input T08
  marks from.
Files: tests/_probe.py, scripts/test_report.py, tests/tiers.manifest (generated, committed)
Blocked by: none
Tests: the probe on a fixture suite (one test spawns `true`, one leaks an env var, one leaves a
  child) reports exactly those; probe off ⇒ the plugin registers no hooks
Risk: low. ≈250 lines (mostly the report).
Saves: makes "measure before and after" (rule 5) one command.
```

### Phase 1: cheap, independent wins

```text
T03 · CMX-283 tests: test runs skip SQLite fsync
Scope: one helper, chela/sqlitedb.py connect(path, **kw), used by the five openers
  (dispatcher._db, scheduler, context, ops, judge_select). It honours
  CHELA_SQLITE_SYNCHRONOUS (OFF|NORMAL|FULL; unset keeps today's behaviour exactly). conftest sets
  OFF at import, so subprocesses inherit it. An env var rather than patching sqlite3.connect
  reaches `python -m chela…` children too. Optional, in the same slice only if it stays small:
  ensure_schema's CREATE TABLE carries every column, so a fresh DB runs zero ALTERs.
Files: chela/sqlitedb.py (new), the five openers, tests/conftest.py (one line)
Blocked by: none
Tests: unset ⇒ the PRAGMA is not issued (today's default, pinned by reading
  `PRAGMA synchronous` back); each value ⇒ that value read back; an invalid value ⇒ ignored with
  the default kept, never an error; each opener goes through the helper (one parametrized test)
Risk: normal (production DB path; the default is unchanged). ≈80 lines.
Saves: measured −308 s per full run at load ~20 (798 → 490 s, −39%). The same factor applies to
  every judge full-suite run (1,125 in the 20-day window, median 405 s): ≈50 judge-slot-hours
  per 20 days, plus CI and every task-finished. The largest single win in the plan.
```

```text
T04 · CMX-283 tests: the leak guard (§4.3), with an allowlist of today's offenders
Scope: an autouse hookwrapper in conftest snapshots the §4.3 state before setup and compares it
  after teardown; a difference fails the test with a named diff. Today's offenders are listed by
  nodeid in tests/leak_allowlist.txt (1 env, 293 config-attribute, 10 child-process) so the
  slice lands green; a test on the list that no longer leaks fails as "remove it from the
  allowlist" (the list only shrinks). A reload_config fixture is added for T04b to use.
Files: tests/conftest.py, tests/leak_allowlist.txt, tests/test_leak_guard.py
Blocked by: none (T02 shares the snapshot code; whichever lands second imports it)
Tests: a planted test (pytester) that leaks each kind fails with that kind named; the same test
  without the leak passes; an allowlisted test that stops leaking fails; the guard costs <5% on
  a 500-test sample (measured, stated in the PR)
Risk: normal (conftest ⇒ full suite in the judge). ≈200 lines.
Removes: the whole order-dependent class (CMX-281 shape) for every test written from now on.
```

```text
T04b · CMX-283 tests: fix the leak-guard offenders; the allowlist reaches zero
Scope: test_config_env's load_env_file tests use monkeypatch-scoped env (the setdefault leak);
  the 13 reload files use reload_config; the 10 daemon-loop tests reap the child the loop starts
  (or the loop is given an injectable spawner). Delete tests/leak_allowlist.txt and its code path.
Files: the 13 reload files, test_config_env.py, the 8 daemon-loop test files, tests/conftest.py
  (allowlist removal)
Blocked by: T04
Tests: the guard itself, now with an empty allowlist; each fixed file passes run alone and with
  its tests in reverse order (the repo has no pytest-randomly, so the reverse run uses a small
  `pytest_collection_modifyitems` hook enabled by an env var, test-only)
Risk: low. ≈40 lines of non-test code (the rest is tests).
```

```text
T05 · CMX-283 tests: flakes A, the open real-environment ones
Scope: test_update_apply_route::test_unread_service_freshness_refuses_instead_of_saying_up_to_date
  (7 red dev runs; first decide whether this is a real regression from CMX-127 #680, and if so the
  fix is production code and gets its own issue); test_env_leak_hardening heal-create (a real
  tmux + agent-terminals.sh); test_pm2_home_isolation's two. Each gets the event-not-deadline
  rule (§4.4) or a narrower real dependency.
Files: those test files; production code only if the first one is a real bug
Blocked by: none. CMX-281 (any_gh) was fixed separately (#792).
Tests: each fixed test passes 50× in a row under `stress-ng --cpu 20` (or the box's real load ≥16),
  stated in the PR
Risk: normal. ≈100 lines.
Removes: 4 of the open flakes in §2.5 (12 of the 38 failed CI runs).
```

```text
T06 · CMX-283 tests: flakes B, timing under load (CHELA_TEST_TIMEOUT_SCALE)
Scope: one helper, tests/helpers/waiting.py wait_until(pred, *, bound) whose bound is multiplied
  by CHELA_TEST_TIMEOUT_SCALE (default 1); the open timing flakes move onto it:
  test_judge_sweep sigkilled guardian, test_ticklock outlives-the-bound, test_judge_watchdog_detached,
  test_judge_battery_progress torn read, test_dispatcher_worktree_gc one-real-tick,
  test_frontend nested vitest. JS browser suites: the node-side waits get the same scale through an
  env var the suites read.
Files: tests/helpers/waiting.py, those tests, tests/browser/fixture.mjs (the wait helper)
Blocked by: none
Tests: wait_until fails at bound×scale and not before (fake clock); scale unset ⇒ 1; each migrated
  test 50× under load, as T05
Risk: low. ≈80 lines.
Removes: 6-8 of the open flakes in §2.5, and the JS ones (6 failed CI runs).
```

```text
T07 · CMX-283 tests: default fakes for tmux, gh and memcap at the subprocess boundary
Scope: §4.2. An autouse fake answers any tmux argv not on a -L chelatest-* socket with "no server"
  without spawning; fake_tmux(windows=…) for tests that need answers; real_tmux opts out (and is
  what T08 marks integration). An in-process gh fake for in-process callers (the PATH stub stays for
  subprocesses). memcap's systemctl probe faked by default; test_memcap* opt back in.
Files: tests/conftest.py, tests/fakes/{tmux,gh,memcap}.py, the ~8 real-tmux files (add real_tmux)
Blocked by: none (T04 if both edit the same conftest region: land T04 first)
Tests: the fake never spawns (probe count 0 for an incidental caller); a real_tmux test still
  reaches its -L socket; the mutation fence still raises on a default-socket mutation with the fake
  on (negative control); memcap's own tests still run the real probe
Risk: normal (conftest). ≈250 lines.
Saves: ~15,000 spawns per run; ≈100 test-seconds (systemctl in test_runtime_truth is the bulk);
  removes the host tmux server and binary as an input to ~580 tests.
```

### Phase 2: tiers

```text
T08 · CMX-283 tests: the integration marker, enforced
Scope: register `integration`; mark every nodeid in tests/tiers.manifest (after T07, the manifest
  is regenerated and shrinks by the incidental-tmux/gh/memcap tests); a conftest hook: when the run
  is the fast tier (`-m "not integration"`, detected from the mark expression, or
  CHELA_TEST_TIER=fast), a Popen of anything outside {git} fails the test with the fix named.
  `uv run pytest` with no -m still runs everything, so nothing changes for CI until T10.
Files: pyproject.toml (markers), tests/conftest.py, the marked test files (marks only)
Blocked by: T02, T07
Tests: in a fast-tier run a planted unmarked test that spawns `true` fails naming the rule; the same
  test marked integration is deselected; a git-only test passes; a full run is unaffected; the
  manifest and the marks agree (a test that fails if a manifest nodeid is unmarked or vice versa)
Risk: normal (conftest). ≈120 lines of non-test code; marks across ~95 test files.
Saves: makes the ~2 min fast tier exist (measured in the PR at the box's real load).
```

```text
T09 · CMX-283 tests: xdist distribution and the 250 s test
Scope: `--dist loadgroup`: the two live-tmux suites keep one worker via
  @pytest.mark.xdist_group("live-tmux"); everything else is distributed per test, not per file.
  test_release.py's live-state-fence regression (250 s, 8.3% of the suite) stops running the whole
  nested release suite twice: run it once at the new path and prove the fence trips at the old path
  with a one-test nested run (the fence, not the suite, is what it guards).
Files: pyproject.toml (addopts), test_epoch_live.py, test_terminals_selfheal.py (group marks),
  test_release.py
Blocked by: T08 (so the 250 s test is already integration-only)
Tests: the regression still goes red if the release worktree path moves back under CHELA_DIR
  (the CMX-144 corruption, run as a self-check experiment); a loadgroup run keeps both live suites
  on one worker each (assert by worker id in a fixture)
Risk: normal. ≈40 lines.
Saves: the integration tier's floor drops from ~310 s (one file on one worker) to the longest
  single test; ≈230 s of test time from the release regression.
```

```text
T10 · CMX-283 tests: CI runs the two tiers as parallel jobs
Scope: ci.yml's pytest job splits into `fast` (-m "not integration") and `integration`
  (-m integration), both required, both with CHELA_REQUIRE_JS_TESTS=1 where JS suites live (the
  JS suites are integration). CI wall = the longer job, not the sum.
Files: .github/workflows/ci.yml (always-human: needs Liav's override)
Blocked by: T08
Tests: a workflow-lint test asserts both jobs exist and are required and that their mark
  expressions are complements (so no test runs in neither)
Risk: high (always-human path). ≈40 lines.
Saves: CI wall ≈ max(fast, integration) instead of their sum; with T03 + T09 an estimated
  ~6 min instead of 13.
```

```text
T11 · CMX-283 tests: judge experiments and confirmations run on the fast tier
Scope: §4.5. WORKFLOW.md gains judge.fast_test_cmd (unset = today's behaviour). With it set: the
  per-mutation subset runs inside the fast tier plus explicitly related integration tests; a subset
  survivor is confirmed on the fast tier in full plus the integration tests that import or name the
  file; the full suite is still the baseline and the final check; judge.full_confirm (globs) keeps
  full-suite confirmation for files T01's data says need it. sweep and self-check use the same path
  (they already share _run_selected).
Files: chela/judge.py, chela/judge_select.py, chela/workflow.py (the key), WORKFLOW.md
  (always-human)
Blocked by: T08; T01 with ≥14 days of records. Go/no-go in the PR body: of the confirmations
  T01 recorded, how many were KILLED only by a test outside "fast tier ∪ related integration";
  if that is more than ~1% of confirmations, land T13 first.
Tests: a survivor is confirmed by the fast tier and not by the full suite when fast_test_cmd is
  set; unset ⇒ the full suite (today, pinned); a file matching full_confirm ⇒ the full suite; a
  fast-tier run that cannot be read is CANNOT VERIFY, never SURVIVED; precedence table for
  CLI flag > WORKFLOW key > default
Risk: high (the judge's verdict path; always-human file). ≈250 lines.
Saves (estimate, measured by T01 after it lands): confirmations move from a median 405 s to
  ~2 min; at the 20-day window's 918 confirmations that is ≈70 judge-slot-hours per
  20 days, and a median battery of ~30 min dropping to roughly 10. Removes every integration-tier
  flake from experiment verdicts (the consistency flips).
```

```text
T12 · CMX-283 tests: agents' own loop and task-finished use the fast tier
Scope: the dispatch brief's "uv run pytest -q" becomes the fast tier for iteration and the full
  suite once before hand-in; `chela judge sweep` / `self-check` inside task-finished inherit T11's
  path when fast_test_cmd is set; the budgets in WORKFLOW.md that were raised to 2400 s as a
  stopgap for this plan go back toward 900 s once T03 + T11 show the measured numbers.
Files: chela/dispatcher.py (the brief), chela/main.py (task-finished), WORKFLOW.md (budgets)
Blocked by: T11
Tests: the brief names both commands (rendered-brief test); task-finished's sweep uses the fast
  command when set and the full one when not
Risk: normal (WORKFLOW.md part is always-human). ≈60 lines.
Saves: per hand-in, the sweep + self-check budgets (2 × up to 40 min today) shrink with the tier.
```

```text
T13 · CMX-283 tests: selection reaches what the 57 escapes show it misses
Scope: driven by T01's records. Likely candidates from §2.6: a module invoked as a subprocess
  (`python -m chela.X`, `chela <cmd>` → chela/main.py's handler module) counts the invoking test as
  an observer of the handler, not of all of main.py; main.py's 2,100-3,700-test "subset" narrows
  to the tests that drive the mutated handler. Exact scope is set by the data, not here.
Files: chela/judge_select.py
Blocked by: T01 (≥14 days of records)
Tests: per new observer rule, a fixture repo where the rule is the only path from test to file
  (positive) and a near miss it must not select (negative)
Risk: normal. ≈200 lines.
Saves: fewer escapes to confirm, a smaller main.py subset (it is the largest of the 57's clusters).
```

### Phase 3: done

```text
T14 · CMX-283 tests: ratchets and the definition-of-done guards
Scope: the §6 guards as tests; docs/TESTING.md (new, short): the two tiers, the fakes, the leak
  guard, the wait rule, how to run the report. CONTRIBUTING.md points to it.
Files: tests (guards), docs/TESTING.md, CONTRIBUTING.md
Blocked by: T04b, T08, T09, T10, T11
Risk: low. ≈60 lines.
```

**Count:** 15 slices (T01-T14 plus T04b). Already owned elsewhere and not repeated here:
CMX-281 (the `any_gh` PATH flake), CMX-195/CMX-196 (judge timeouts and phase timings; T01 builds
on whatever CMX-196 recorded).

---

## 6. Definition of done

All of these hold on `dev`, each enforced by a test T14 adds (or by the slice named):

1. **Two tiers, complementary.** `-m integration` and `-m "not integration"` partition the
   collected suite, with no test in neither (T10's lint, T14).
2. **The fast tier is fast and hermetic.** It spawns nothing but `git` (enforced by T08's hook),
   and its wall time on the dev box under normal fleet load is under 3 minutes (T02's report in
   T14's PR; a budget test is not proposed, because it would be a timing flake itself).
3. **No test leaks.** The leak guard is on for every test and its allowlist file does not exist
   (T04, T04b).
4. **No incidental real tmux, gh or systemctl.** The probe report shows those programs only
   under `integration` tests (T07, T14).
5. **No fixed wall-clock deadline below the scaled bound** in tests that wait on subprocesses
   (grep guard for `time.monotonic() <` / `deadline` outside `tests/helpers/waiting.py`, with an
   allowlist that only shrinks).
6. **The judge spends its full-suite runs where T01's data says they matter**: baseline, final,
   and the `judge.full_confirm` files (T11), and `chela judge stats` shows it.
7. **Flakes are visible.** `chela judge stats` and `runs.ci_flakes` name the flaky test, and a
   recurring one (≥2 in 14 days) is surfaced to the orchestrator rather than re-run in silence.

---

## 7. Estimate

| | |
|---|---|
| Slices | 15 |
| Phase 1 width | T03, T04, T05, T06, T07 are independent: three slots clear them in about two passes |
| Critical path | T02 → T07 → T08 → T11 → T12 → T14, plus T11's 14-day wait on T01's data |
| Per slice | ≈3-4 h including judge rounds (FRONTEND_PORT_PLAN.md §7's baseline). Most slices here are small, and T03 lands first, which makes every later round faster. |
| Expected wall-clock | Phase 0-1: 2-3 calendar days. Phase 2 up to T10: another 2 days. T11-T14 wait on the 14-day data window, so they are about 2.5 weeks out from T01's merge. |

Expected effect, in order of confidence:

| Change | Full suite / CI | Judge | Flakes |
|---|---|---|---|
| T03 SQLite | **−39% measured** (798 → 490 s) | the same factor on every full run (~50 h per 20 days) | none directly |
| T07 fakes | ≈−100 test-s | | ~580 tests stop depending on the host's tmux |
| T09 + T10 | CI ≈ max of two jobs, an estimated ~6 min | | |
| T04/T04b | | | removes the order-dependent class (1 env + 293 config + 10 child leaks today) |
| T05/T06 | | fewer baseline re-runs | 10-12 of the open flakes in §2.5 |
| T11 | | confirmations ~405 s → ~2 min (est.), ≈70 h per 20 days, battery median ~30 → ~10 min (est.) | integration flakes leave experiment verdicts |

---

## 8. Appendix: reproducing the measurements

### 8.1 Durations

```bash
CHELA_REQUIRE_JS_TESTS=1 uv run pytest -q --durations=0 > full.log
# per-test totals (setup + call + teardown), top-N share:
python3 - <<'EOF'
import re, collections
t = collections.Counter()
for l in open("full.log"):
    m = re.match(r"\s*([\d.]+)s (setup|call|teardown)\s+(\S+)", l)
    if m: t[m[3]] += float(m[1])
tot = sum(t.values()); top = t.most_common()
for n in (10, 100, 500, 1000):
    s = sum(v for _, v in top[:n]); print(n, round(s), f"{100*s/tot:.1f}%")
EOF
```

### 8.2 The spawn/leak probe

A measurement-only plugin, loaded with `PYTHONPATH=<dir> LEAKPROBE_DIR=<out> uv run pytest -p
leakprobe`. Per test it wraps `subprocess.Popen.__init__` to record the program name (and
`python -m <mod>`), and around `pytest_runtest_protocol` it snapshots `os.environ`, cwd, umask, six
signal handlers, non-daemon thread names, the worker's child processes (a `/proc/*/stat` ppid scan),
the identity of every `chela.*` module, and `chela.config`'s upper-case attributes. It writes one
JSON line per test to `<out>/<worker>.jsonl`. T02 commits it as `tests/_probe.py`. Until then, this
section is the specification.

### 8.3 The SQLite A/B

```python
# fastsqlite.py: load with PYTHONPATH=<dir> uv run pytest -p fastsqlite
import sqlite3
_connect = sqlite3.connect
def connect(*a, **k):
    conn = _connect(*a, **k)
    conn.execute("PRAGMA synchronous=OFF")
    return conn
sqlite3.connect = connect
```

### 8.4 Judge logs

```bash
grep -h 'green → confirmed' ~/.chela/judge-logs/*.log | grep -c SURVIVED   # 853
grep -h 'green → confirmed' ~/.chela/judge-logs/*.log | grep -c KILLED     # 57
grep -h 'mutation battery took' ~/.chela/judge-logs/*.log                 # per-round wall
```

### 8.5 CI flakes

```bash
gh run list --workflow ci.yml --created '>=2026-09-20' --limit 1500 \
  --json databaseId,attempt,conclusion,headBranch,event,createdAt
gh run view <id> --log-failed | grep -o 'FAILED tests/[^ ]*' | sort -u
sqlite3 -readonly ~/.chela/scheduler.db 'select task_id, ci_flakes from runs where ci_flakes is not null'
```
