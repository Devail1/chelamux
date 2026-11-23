# Faster verification: where the suite slot's time goes (CMX-480)

> **Summary**
>
> * **One full suite takes 11 minutes; waiting for the slot takes longer.** The measured run
>   passed 14,647 tests in 641 s (10 m 41 s). It waited **98 minutes** for the one suite slot
>   first. Recorded judge rounds wait 14.5 min (median) and up to 3 h 40 m.
> * **The wall-clock is one file.** `tests/test_js_suites.py` holds 534 s of test time.
>   `--dist loadfile` keeps a file on one worker, so that worker sets the suite's
>   wall-clock, and the other three finish early. Splitting that file is the cheapest win
>   (slice B1): about 2 min off **every** full suite, CI included.
> * **33 of the 50 slowest tests spawn node** (Vitest, jsdom, Playwright): 464 of the
>   top-50's 647 s. `git init` per test is not a cause: it costs ~15 ms against ~5 ms for a
>   copied template repo.
> * **Experiments already select tests** (CMX-407, CMX-383, CMX-141). The brief's "each a
>   full suite" is out of date. Time goes to three things instead: hub-module mutations
>   select 5,000 to 6,700 tests, because selection works at file-import granularity and
>   the coverage map is never built (`pytest-cov` is not installed); `.sh` and `.yml`
>   mutations run the full suite; and every subset survivor is re-run on the full suite.
>   48 of the 49 recorded re-runs did not change the verdict.
> * **A core hand-in runs two full baselines on the same tree** (the self-check's and the
>   sweep's: 7.0 + 6.8 min, median). The judge then runs a third on the same head. Slice D
>   reuses a green baseline.
> * **Peak memory of one suite: 3.39 GB anonymous** (cgroup `memory.peak` 5.06 GB; RSS
>   summed over the scope's processes 4.39 GB). The 2026-10-21 figure was 2.95 GB anonymous
>   on 10,075 tests. During a weekday morning, with one suite running, the box had
>   **4.1 GB available at its lowest and 5.2 GB at p10**. A second slot does not fit
>   unconditionally in the daytime (slice C).

Measured Monday 2026-11-23, 08:58–10:48 local time, on `dev` at `9e24e53d`. The box was not
quiet: load average was ~19 on 20 cores, with three dispatched agents, one judge and about
a dozen interactive Claude sessions open. That is a normal day, and the memory question
needs a normal day. It also means test durations are on the slow side of what an idle box
would show.

## 1. Durations

### How it was measured

The suite ran once, exactly as the judge runs it: `chela.judge.run_suite`, which takes one
of the box's suite slots (`suite_guard.slot`) and runs the command inside its own
memory-capped `systemd-run --user --scope` (`suite_guard.bounded`). The command was
`judge.test_cmd` plus two report flags:

```
CHELA_REQUIRE_JS_TESTS=1 uv run pytest -q --durations=60 --junitxml=<scratch>/junit.xml
```

Result: `14647 passed, 23 skipped, 1 xfailed in 640.79s`. The JUnit report has 14,671 cases
and **2,094 test-seconds**, about 523 s per worker across the 4 xdist workers (`-n 4`).

### Where the test-seconds are

| slice of the suite | share of test-seconds |
|---|---|
| the slowest 50 tests | 31 % |
| the slowest 200 | 57 % |
| the slowest 1,000 | 87 % |
| the 12,976 tests under 0.1 s each | 7 % |

The test count is not the cost. About a thousand heavy tests are.

### The 50 slowest tests, grouped by cause

| cause | tests in the top 50 | seconds | files |
|---|---|---|---|
| **node spawn** (Vitest, jsdom, Playwright, eslint/tsc gates) | 33 | 464 | `test_js_suites.py` (one node process per `.mjs` suite), `test_frontend.py`, `test_frontend_build.py`, `test_judge_select_frontend.py`, `test_judge_sweep_ts.py`, `test_procguard.py[*vitest*]` |
| **subprocess / CLI / nested pytest** | 11 | 116 | `test_release.py` (a release's own tests under the live-state fence), `test_smoke_fresh_install.py` (a real fresh clone per test), `test_restore_cli.py`, `test_judge.py` / `test_verdict_carry.py` / `test_judge_sweep.py` (a real judge battery: one inner pytest per experiment), `test_share_billing.py`, `test_share_web.py`, `test_public_media.py` |
| **live tmux** | 3 | 46 | `test_terminals_selfheal.py` (30.6 s, one test), `test_agent_terminals_bash32.py` (bash 3.2 and host bash), `test_probe_batch.py` |
| **git** (many commits, not `git init`) | 2 | 15 | `test_sweep_nightly.py` (`…past_a_thousand_commits`: 1,000 empty commits; `…second_run_with_no_change…`) |
| other (a repo scan) | 1 | 6 | `test_port_done.py` |
| **sleeps / timeouts** | 0 | — | none in the top 50 |

The ten slowest:

| s | test |
|---|---|
| 69.8 | `test_frontend.py::test_frontend_vitest_suite` (the whole frontend Vitest suite, ~1,900 specs) |
| 65.6 | `test_frontend.py::test_test_fails_on_a_failing_spec_and_still_runs_the_real_ones` (runs that suite a second time) |
| 30.6 | `test_terminals_selfheal.py::test_promptness_checks_are_not_absorbed_into_reap` |
| 27.4 | `test_js_suites.py::test_js_suite[tests/browser/settings_modal_height.test.mjs]` |
| 22.8 | `test_js_suites.py::test_js_suite[tests/browser/overlays_phone.test.mjs]` |
| 19.8 | `test_release.py::test_the_release_tests_pass_under_the_live_state_fence_on_a_real_layout` |
| 18.6 | `test_js_suites.py::test_js_suite[tests/browser/dashboard.test.mjs]` |
| 16.5 | `test_js_suites.py::test_js_suite[tests/sidebar_archive_guards.test.mjs]` |
| 14.1 | `test_js_suites.py::test_js_suite[tests/wall_boot_fetches.test.mjs]` |
| 13.6 | `test_frontend.py::test_each_ci_gate_passes_the_committed_tree[lint]` |

`test_share_billing.py::…placeholder_login…[none]` (12.9 s) has no visible cause: the argv
builder spawns nothing. Profile it before fixing it.

### The slowest files

| test-seconds | tests | file | patterns found in the file |
|---|---|---|---|
| **534.0** | 132 | `test_js_suites.py` | node, subprocess |
| 189.9 | 31 | `test_frontend.py` | node |
| 138.4 | 236 | `test_sweep_nightly.py` | git, subprocess |
| 74.9 | 148 | `test_judge.py` | git, tmux, subprocess, node |
| 57.3 | 360 | `test_release.py` | git, subprocess, node |
| 49.2 | 5 | `test_smoke_fresh_install.py` | subprocess, bash |
| 42.8 | 16 | `test_terminals_selfheal.py` | tmux, bash |
| 37.3 | 312 | `test_judge_sweep.py` | git, subprocess, node |
| 35.9 | 207 | `test_judge_sweep_standalone.py` | git, subprocess, bash |
| 34.1 | 44 | `test_sweep_nightly_owed.py` | subprocess |
| 32.6 | 160 | `test_runtime_truth.py` | git, tmux, subprocess |
| 27.9 | 73 | `test_judge_select.py` | git, subprocess, node |
| 27.1 | 14 | `test_frontend_build.py` | node |
| 24.3 | 36 | `test_agent_terminals_bash32.py` | tmux, bash |

⭐ **`test_js_suites.py` is the critical path.** Under `--dist loadfile` its 534 s run on
one worker. The other three workers share the remaining 1,560 s, about 520 s each. The
suite's 641 s wall-clock is therefore that one file plus collection. `test_frontend.py`
(190 s) is the next file that can be the critical path once the first is split.

### `git init` is not a cause

71 test files each build their own repo (`git init` plus commits). Building one with a
commit took **15 ms** on this box; copying a prepared template took **5 ms** (20 runs
each). A per-test template saves about 10 ms per repo. Only tests that create many commits
(the 1,000-commit sweep test) are measurably slow, and a template does not help them.

## 2. Peak memory

### How it was measured

The same run. A sampler thread started once the slot was held, found the suite's new
`chela-suite-*.scope` cgroup, and read the following every second (658 samples):

* the scope's `memory.current`, `memory.peak` and `memory.stat` `anon`;
* **RSS summed over every pid in the scope's `cgroup.procs`** (pytest, the 4 xdist
  workers, node, Vitest, Playwright, tmux, git: up to 62 processes). Shared pages count
  once per process, so this sum overstates real use. `anon` is the honest figure;
* the box's `MemAvailable` and `SwapFree` from `/proc/meminfo`.

A second sampler read the box's `MemAvailable` every 10 s from 08:58 to 10:48 (639
samples). During that window a suite was running in 630 samples and no suite in 9.

### One suite

| measure | value |
|---|---|
| anonymous memory, peak | **3.39 GB** (at +437 s: the browser and Vitest stretch, 36 processes) |
| anonymous memory, median / p90 | 1.54 GB / 2.63 GB |
| cgroup `memory.peak` (includes reclaimable page cache) | 5.06 GB |
| RSS summed over the scope's processes, peak | 4.39 GB |
| most processes in the scope at once | 62 |

The anonymous peak is short: above 2.6 GB for about 10 % of the run. It grew 15 % since
`suite_guard.SUITE_PEAK_BYTES` was measured (2.95 GB on 2026-10-21, 10,075 tests). That
constant sizes `default_slots()` and the default ceiling (`3 × peak`), so it is now
stale.

### The box on a normal morning (15 GB RAM, 32 GB swap)

| | samples | MemAvailable min | p10 | median |
|---|---|---|---|---|
| a suite running (somebody's) | 630 | **4.09 GB** | 5.20 GB | 6.17 GB |
| no suite running | 9 | 7.00 GB | — | 7.20 GB |

While the measured suite itself ran, MemAvailable was 4.52 GB at its lowest (median 5.97
GB). Swap was ~9 GB used throughout, at no measurable memory pressure
(`/proc/pressure/memory` avg300 0.06).

### Is `suite_slots: 2` safe?

**Not unconditionally in the daytime.** A second suite needs up to 3.4 GB of anonymous
memory on top of what the reading above already includes. At this morning's p10 (5.2 GB),
two suites would leave about 1.8 GB; at its minimum (4.1 GB), about 0.7 GB. That is the
overlap that got hand-ins reaped on 10-23. Both runs' peaks are short, but nothing
de-phases them.

It probably **is** safe when the interactive sessions are closed. With no suite running
the box showed ~7 GB available while a dozen sessions were open. That window was too
short to call: the overnight free memory was not measured here. See slice C.

## 3. Judge rounds and self-checks today

Read from the CMX-196 / CMX-397 records (`chela judge timings`, `--hand-in`, `--nightly`):
45 timed judge rounds and 64 hand-in attempts between 2026-10-28 and 2026-11-23.

### A judge round (45 rounds)

| phase | median | p90 | max |
|---|---|---|---|
| slot wait | 14 m 30 s | 1 h 20 m | 3 h 40 m |
| baseline (one full suite) | 6 m 52 s | 9 m 07 s | 10 m 37 s |
| visible experiments | 6 m 42 s | 25 m 09 s | 32 m 16 s |
| consistency re-runs | 6 m 33 s | 15 m 23 s | 20 m 01 s |
| held-out experiments | 3 m 12 s | 9 m 40 s | 20 m 57 s |
| setup | 44 s | 1 m 38 s | 2 m 03 s |
| **whole round** | **55 m 41 s** | 1 h 49 m | 4 h 08 m |

The phases above leave a median ~18 min unattributed (p90 ~79 min). CMX-196 does not
time it, and no suite runs in it. It is not the slot's time.

Per experiment (340: 256 experiments and 84 consistency re-runs, 6 per round median, 14
max):

| kind | count | median | share of the 15.8 h spent on experiments |
|---|---|---|---|
| ran a selected subset | 311 | 73 s | 40 % |
| fell back to the full suite | 29 | 445 s | 25 % |
| of the subset runs: survivor re-run on the full suite (`confirmed_full`) | 49 | 430 s | 35 % |

* **Every full-suite fallback is a non-Python, non-JS file.** Six of the 7 held-out ones
  are `.sh` files (`scripts/agent-terminals.sh`, `scripts/check_fresh_install.sh`); the
  other is `.github/workflows/release.yml`. All recorded with "`.sh` is not a file type
  selection can reason about".
* **Selected subsets are large for hub modules.** Quartiles of the selected count: 27 /
  168 / 520 / 3,600, maximum 6,705 (of ~14,650). By the mutated file: `chela/dispatcher.py`
  6,263–6,705; `chela/main.py` ~5,400; `chela/dashboard/app.py` 4,341–5,014; `chela/inbox.py`
  4,160; `chela/judge.py` 3,475–3,600. Leaf modules are small (`chela/judge_timing.py` 55–62;
  `chela/dashboard/humanproof.py` 30–253).
* **Full-suite confirmation rarely changes the verdict.** 48 of the 49 confirmations still
  SURVIVED. One (CMX-374, held-out, 355 selected tests) was KILLED by a test the subset did
  not select. That one is why confirmation stays.

### A hand-in (`chela task-finished`, 64 attempts, 48 tasks)

| phase | core tier (23) | standard tier |
|---|---|---|
| `suite` (one full suite, standard only) | — | 7.5 min (31) |
| `self_check` | 20.9 min (21) | 20.9 min (6) |
|   `self_check.baseline` | 7.0 min | 6.9 min |
|   `self_check.experiments` | 14.4 min | 13.7 min |
| `sweep` | 36.7 min (23) | — |
|   `sweep.baseline` | 6.8 min | — |
|   `sweep.mutations` (budget-bound) | 28.6 min | — |
| slot wait | 17.3 min | — |
| **whole hand-in, core** | **82.3 min** | |

21 of the 23 core hand-ins ran both the self-check baseline and the sweep baseline on the
**same tree**: 13.8 min of two identical full suites (median). The judge round that
follows starts with a third full baseline on the same head.

The nightly sweep (5 runs): 1 h 28 m median; baseline 7 m 42 s.

## 4. Slices

Expected savings are per the measurements above. Every slice keeps the CI run unchanged.

### B1. Split `test_js_suites.py` so xdist can spread it (smallest, biggest)

Shard the parametrized `.mjs` list across N files (`test_js_suites_{0..3}.py`, a stable
hash of the suite path picks the shard; a guard test asserts every `.mjs` lands in exactly
one shard). `--dist loadfile` stays, because the live tmux suites rely on it.

* **Saving: ~2 min off every full suite** (641 s → ~525 s + collection, about 18 %). That
  covers judge baselines, consistency and confirmation runs, fallbacks, hand-in
  `suite`/baselines and CI. A core task runs 5 to 8 full suites, so about 10 to 15 min
  per task.
* Risk: low. The `.mjs` suites are independent processes.

### B2. Stop running the frontend Vitest suite twice

`test_frontend.py::test_test_fails_on_a_failing_spec_and_still_runs_the_real_ones` (65.6 s)
re-runs the whole real suite to prove a failing spec turns it red. A two-spec fixture
(one failing, one passing) proves the same wrapper behaviour.

* **Saving: ~60 test-seconds.** That is about 15 s of wall-clock while the file is off the
  critical path, and the full 60 s once B1 makes `test_frontend.py` (190 s) the longest
  file.

### B3. Smaller fixes, profile first

* `test_smoke_fresh_install.py`: 5 tests, 49 s, a real fresh clone each. Clone once per
  module.
* `test_terminals_selfheal.py::test_promptness_checks_are_not_absorbed_into_reap`: 30.6 s
  in one live-tmux test.
* `test_share_billing.py[none]`: 12.9 s with no visible cause.
* ⛔ **Not** a per-test template git repo: measured at ~10 ms saved per repo.

Saving: tens of test-seconds, about a quarter of that in wall-clock. Do these only after
B1.

### D. One green baseline per tree, shared (new: not in the brief)

Key a green baseline's JUnit report (and its coverage map, once A1 exists) by `(tree sha,
test_cmd, deselects)` under `$CHELA_DIR`. `self_check`, `sweep` and the judge reuse it
instead of each running their own. A cache miss, an unreadable entry or a red result runs
the suite as today, so unknown never reads as green.

* **Saving: ~7 min per core hand-in** (the sweep reuses the self-check's baseline), plus
  **~7 min per judge round** whose head is the handed-in tree (the judge reuses it). About
  14 min per core task.
* Risk: medium. The judge's baseline runs in its own worktree after its own provisioning.
  Reuse must stay inside one tree sha, and the judge must still provision before its
  experiments.
* **Status: shipped (CMX-488)**, in `chela/baseline_cache.py`, read and written by
  `judge.run_baseline`. The tree sha is the working tree's (tracked edits and untracked,
  non-ignored files), so a self-check on uncommitted edits only matches a judge of a commit
  with the same contents. A flake-rescued green and a tree that changed mid-run are not
  stored. Until A1 caches the coverage map with the entry, a run that would build the map
  runs the suite instead of reusing.

### A. Narrower selection for judge and self-check experiments

Selection exists (CMX-407). It works at test-FILE granularity through imports and naming.
Its coverage rule (`judge_select.coverage_args` / `parse_coverage`) is wired but inert,
because `pytest-cov` is not installed. Keep one full baseline per round and the full-suite
confirmation of every subset survivor (it caught 1 of 49).

* **A1. Line-level coverage.** Add `pytest-cov` to the dev extras so the baseline records
  `--cov-context=test`. Then select the test NODES whose context executed the mutated
  lines, not every test in every file that imports the module.

  Fall back to today's import-based selection when:

  * the line has no test context (it runs only at import, or only in a CLI subprocess
    that coverage does not follow);
  * the map is missing or unreadable.

  An empty line selection is never "no test reaches it".

  *Measure first:* the coverage overhead on the baseline (once per head sha, cached) and on
  memory.

  *Saving:* hub-module experiments go from 5,000–6,700 selected tests (~2–3 min each) to
  the tests that execute the line. It bears on the 40 % share spent in selected runs:
  roughly 3 to 5 min per round. Acceptance as briefed: replay a set of recorded rounds;
  same verdicts, measurably less time.
* **A2. Select for shell scripts.** A `.sh` file is observed by the test files that name
  its path, plus the observers of the Python modules that name it (`scripts/agent-terminals.sh`
  is named by `chela/main.py`, `chela/service_code.py` and others). `.github/workflows/*` stays
  full-suite: it is tool config (CMX-259).

  *Saving:* the 25 % of experiment time spent in full fallbacks, which is concentrated in
  shell-script PRs. CMX-444's round ran 8 full suites (59 min). At ~1–2 min per
  experiment it would be ~15 min.

### C. `suite_slots: 2`, gated on memory (a config change for Liav)

The numbers in §2 do not support a fixed second slot in the daytime. What fits:

1. Update `SUITE_PEAK_BYTES` to the new peak (3.39 GB anonymous).
2. Admit a second suite only while `MemAvailable` ≥ peak + 2 GB headroom (~5.4 GB) at
   admission, and never inside `sweep.blocked_hours` (03:00–04:30). This ties into CMX-479.
   `suite_guard.resumable()` already reads `MemAvailable` the same way and treats an
   unreadable value as not safe.
3. Before turning it on, measure the overnight `MemAvailable` (this spike covered a
   weekday morning only).

* **Saving:** this targets the slot wait, the biggest phase (14.5 min median per judge
  round, 17.3 min per core hand-in, 98 min for this spike's own run). Gated as above, it
  would mostly help in the evening and overnight.

### Suggested order

B1 → D → A2 → A1 → B2/B3 → C.

* B1 and D are mechanical and cut every task by ~25 min of suite time.
* A2 removes the worst rounds.
* A1 needs its overhead measured first.
* C is Liav's call once the overnight numbers exist.
