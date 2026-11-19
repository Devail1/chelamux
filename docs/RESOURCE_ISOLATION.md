# Resource isolation — a per-job memory ceiling does not bound the box

> **A per-job memory ceiling does not bound the box. Only a shared one does.**

That sentence is the whole page. Everything below is the incident that proves it, the fix
that holds, and the way it fails when you push it — which is *not* the way you expect.

**CMX-264 built the shared-slice half of this into chela itself** (`chela/memcap.py`) —
every dispatched agent AND judge now launches into one shared cgroup slice
(`chela-agents.slice`), bounding their SUM rather than trusting an operator to hand-tune
`concurrency.max` down to whatever they hope fits in RAM. Off by default
(`CHELA_MEMORY_SLICE_BUDGET` unset/0), Linux + a working `systemd --user` session only —
see [Built into chela (CMX-264)](#built-into-chela-cmx-264) below. Everything else on this
page — the personal wrapper's per-job cap, `choom`, the swap-thrashing behaviour, sizing
concurrency to the working set — is still a **known, accepted gap**: chela does not chase
*those* out of the box, on purpose (see
[Why chela cares](#why-chela-cares-and-does-not-fix-it)).

## The failure mode

On 2026-07-14 a workflow fanned out **4 agents**, and every one of them did exactly what
it was told: `MEMCAP=6G memcap python …`. Four correct caps. Four cgroups, each binding.

That authorises **24 GB on a 19 GB box.**

The workers were not even greedy — each sat **under** its own ceiling, **4.4–4.7 GB**
measured — so **no cgroup ever came under pressure**, and no per-job limit ever fired.
There was nothing for them to fire *against*: each job was inside its own budget. The
**box** ran out of memory, and the kernel OOM killer went **global**.

⛔ **The global killer does not kill the greediest process. It kills whatever scores
worst.** What it took was **tmux**, the **PM2 God daemon**, and **two Claude sessions** —
including the orchestrator that would have noticed the machine was in trouble. The jobs
that caused it were not the jobs that died.

**The caps were decorative: individually binding, collectively meaningless.**

Earlier the same day, a **bare backtest** — no wrapper at all — reached **14.6 GB** and
did the same thing to the same box.

⛔ **An instruction in a prompt is NOT an enforcement mechanism.** The workflow *told* its
agents to run under `memcap`; one job ran bare anyway. This generalises well past memory:
if the only thing standing between you and a dead machine is a sentence in a prompt that
an agent has to remember, you are not isolated — you are hoping. The enforcement has to
live somewhere the job cannot decline to use.

## The fix: one shared slice, bounding the SUM

The mitigation is three guards, in the order that matters. Guard 1 is now built into chela
itself for its own fleet (`chela/memcap.py`, CMX-264 — see
[Built into chela](#built-into-chela-cmx-264) below); guards 2 and 3, and guard 1 for any
heavy job chela did not launch (a backtest run by hand, a test fan), still live in a
personal `~/bin/memcap` wrapper outside chela:

**1. A shared cgroup slice — this is the one that actually bounds the box.** Every job,
however many you fan out, is launched *into the same slice*, so the ceiling applies to
their **sum**:

```ini
# ~/.config/systemd/user/memcap.slice   (survives reboot)
[Unit]
Description=memcap — the SHARED memory ceiling for heavy jobs (backtests, test fans, agent workers)

[Slice]
# THE POINT: this bounds the SUM of every memcap job at once.
# A per-job ceiling does NOT bound the box -- 4 agents x 6G authorises 24G on a 19G
# machine, which is exactly how the 2026-07-14 global OOM happened: all four workers
# sat UNDER their own caps (4.4-4.7G) while the box ran out and the kernel went global,
# taking tmux and two Claude sessions with it.
#
# Sized for a 19G box: ~4G fleet/baseline + ~2G for the agents' own Claude processes.
MemoryMax=12G
MemorySwapMax=16G
```

```bash
systemctl --user daemon-reload
systemd-run --user --scope -q \
    --slice=memcap.slice \
    -p MemoryMax=6G \
    -p MemorySwapMax=16G \
    -- choom -n 800 -- python heavy_thing.py
```

Four jobs in that slice cannot exceed 12 G between them. Not 4 × 6 G. **12 G, total.**

**2. The per-job ceiling** (`MemoryMax=6G` on the scope above) — still worth having: it
stops one runaway job from starving its siblings. It just never protected the machine, and
was never going to.

**3. `choom -n 800`** — raise the job's OOM score so that *if* the global killer ever does
run, it reaches for the batch job and not for tmux or the supervisor. A last resort that
biases the blast radius; not a limit.

## 🔴 The counterintuitive bit — the shared cap does not kill anything

This is the part most likely to be written down wrong, so read it twice.

**The shared ceiling does not kill the excess job. It caps RAM and spills to swap.**

Measured, deliberately over-subscribed: two jobs against a slice capped at 1024 MB pinned
it at **`memory.current` = 1023 MB / cap 1024 MB**, with **`memory.swap.current` =
409 MB** — and **both jobs completed.** Nothing was killed. Nothing OOMed.

So:

- A **global** OOM is **impossible while the slice holds** — that is the win, and it is a
  real one.
- But over-subscribing no longer kills the box; it makes your jobs **thrash on swap**.
  Slowly. Successfully. For hours.

**→ The slice is a SAFETY NET, not a plan.** Size concurrency so the working set fits in
**RAM**:

```
concurrency × working-set < SLICECAP
```

Four agents at 4.4-4.7 GB each is ~18 GB. Under a 12 G slice that does not crash —
it swaps, and you will wonder why everything got slow. The answer is that you planned for
the net.

## ⛔ Never raise the ceiling to make a job fit

`SLICECAP`, `MemoryMax`, any of them. Raising the ceiling re-opens the incident — that is
*precisely* the mistake that produced it, since 4 × 6 G was itself a ceiling someone was
comfortable with. If a job does not fit:

- **lower concurrency**, or
- **make the job leaner** (chunk it, stream it, drop the columns you don't read).

Mid-incident, on a box that was **already OOMing**, `claude`'s Node heap was raised to
8 GB. That does not give a process more room; on a full box it just makes it a **fatter
target** for the killer.

## Verify, don't assume

```bash
journalctl -k | grep -i oom                              # did the kernel actually kill something?
systemctl --user show memcap.slice -p MemoryMax          # is the ceiling the one you think it is?
cat /sys/fs/cgroup/user.slice/user-$(id -u).slice/user@$(id -u).service/memcap.slice/memory.current
cat /sys/fs/cgroup/user.slice/user-$(id -u).slice/user@$(id -u).service/memcap.slice/memory.swap.current
```

(The cgroup path is where a *user* slice lands under systemd; confirm yours with
`systemctl --user status memcap.slice` rather than trusting the path above.)

⚠️ **`dmesg` returning nothing means UNREADABLE, not empty.** On a kernel with
`kernel.dmesg_restrict=1`, an unprivileged `dmesg` prints nothing at all — which reads
exactly like "no OOM kills". That is **cannot verify**, not a pass. This mistake was made
*during this very incident*, and it is why `journalctl -k` is the command above.

## Built into chela (CMX-264)

`chela/memcap.py` puts the **shared-slice half** of the fix above directly on the launch
path (`dispatcher._launch_agent`, the one function every coding agent AND judge funnels
through — see its own docstring). Set `CHELA_MEMORY_SLICE_BUDGET` (a bare byte count or a
K/M/G/T size, e.g. `12G`) and every agent/judge launched after that starts under
`exec systemd-run --user --scope --collect --slice=chela-agents.slice -- <cmd>` instead of
running bare. `exec` replaces the pane's shell in place and `systemd-run --scope` forks
exactly once to realise the scope before exec'ing straight into `<cmd>` — no extra shell
layer, so the launched process stays the DIRECT child of the (former-shell) pid that
`agent_manager.claude_pid()`'s `pgrep -P` correlation depends on.

Same posture as `config.worktree_disk_budget_bytes()` (CMX-164): `0`/unset means OFF,
nobody is forced onto a rail they haven't sized for their own box, and any failure —
`systemd-run` missing, no `systemd --user` session, no D-Bus, a stale daemon needing a
reload — degrades to launching **unwrapped** rather than ever blocking a launch. A
`Capability` (`memory_slice_budget`, dashboard + `chela doctor`) announces whether it is
actually enforcing right now, not just whether the knob is set.

**CMX-280: "the knob is off" is not "no bound is in force."** `chela-agents.slice` is
chela's own rail, but this personal wrapper's `memcap.slice` (or anything else sharing
this box's `systemd --user` session) still eats into the SAME machine's total RAM even
though chela never wrapped a single agent in it — `memcap.wrap_launch_cmd` only decides
whether an agent gets its OWN scope inside `chela-agents.slice`; it has no say over
what else is already running on the box. `memcap.live_bound()` scans every active
`systemd --user` slice (`systemctl --user show <unit> -p MemoryMax -p MemoryCurrent`,
the same property this page's own "Verify, don't assume" section checks by hand) and
surfaces the one with the least headroom, tagged `chela_owned` so an operator can tell
"chela's rail" from "something else already bounding this box" apart. `chela
doctor`/the dashboard now report that ceiling — occupancy included — even when
`CHELA_MEMORY_SLICE_BUDGET` is unset, which is exactly the state a `memcap.slice`
sized for an operator's OWN heavy jobs leaves the box in day to day.

**What this closes:** the "four correct per-job caps still authorised 24G on a 19G box"
failure mode above, for chela's own fleet specifically — the slice bounds the SUM of every
agent and judge chela itself launches. **What it does not close:** everything else on this
page. The shared slice is still a safety net, not a plan (`concurrency × working-set <
SLICECAP` still has to hold, see the counterintuitive-swap section above), `choom` biasing
and non-chela heavy jobs (backtests, test fans run by hand) are still the operator's own
`~/bin/memcap`-style wrapper, and chela's own daemon/dashboard/tmux are deliberately left
OUTSIDE `chela-agents.slice` — see below.

## What a judge actually costs (measured, not estimated)

Nobody had a number for what a single judge or dispatched agent actually peaks at —
`concurrency.max`/`JUDGE_MAX_CONCURRENT` were hand-picked without one. This PR does not
change either value (that decision needs the number below, made separately, on the
record), but it does put a real measurement in place of the guess.

**CMX-278 made that separate decision, using the number below.** `JUDGE_MAX_CONCURRENT`
was a hardcoded `1` with no knob at all — the single biggest throughput constraint in the
dispatch loop (every `awaiting_review` PR across every workflow queued behind ONE judge).
It is now `chela.config.judge_max_concurrent()`, a Dispatch-tab knob
(`CHELA_JUDGE_MAX_CONCURRENT`, per-workflow) — see its own docstring. The default stays
`1`: raising it is sizing decision against your OWN box, per the counterintuitive-swap
section above (`concurrency × working-set < SLICECAP`), not something the ~0.77G number
below licenses chela to pick for you.

**What was measured, and why not the judge/agent process directly:** a judge's dominant
paid-for step (`chela judge self-check`'s `self_check`) IS running this repo's own test
suite — `CHELA_REQUIRE_JS_TESTS=1 uv run pytest -q` (2826 tests, `-n 4 --dist loadfile`,
see `pyproject.toml`). A dispatched agent or judge is otherwise a Claude Code CLI process
talking to a remote API — its own footprint is comparatively small and hard to isolate
from this worktree without live API credentials and a real dispatch, which a rework agent
running *inside* a dispatched worktree does not have. The suite run is therefore reported
as an explicit **proxy for the dominant cost**, not a substitute for "judge alone / agent
alone / both concurrently."

**Method:** `systemd-run --user --scope --unit=<name> -- bash -c '...'`, then
`systemctl --user show <name>.scope -p MemoryPeak` — cgroup accounting across the WHOLE
process tree (the coordinating `pytest` process plus its 4 `xdist` workers), the same
mechanism `chela-agents.slice` itself uses, so this number is directly comparable to what
the shared slice would see. Cross-checked with `/usr/bin/time -v`, which undercounts
because it only reports the coordinating process, not its workers.

| Measurement | Peak |
|---|---|
| `systemd-run --scope` cgroup `MemoryPeak` (pytest + all 4 xdist workers) | 769,101,824 bytes (~733 MiB / 0.77 GB) |
| `/usr/bin/time -v` Maximum resident set size (coordinating process only) | 339,424 KiB (~331 MiB) — undercounts, see above |

**Default stays 0/off.** Even a handful of concurrent judges/agents at ~0.77G each sums
to a few GB — comfortably under the 19G box the 2026-07-14 incident OOM'd — but this
worktree's own headroom (dashboard, tmux, the daemon itself, none of which are in the
slice) isn't something this measurement can see, so an operator still has to pick a
budget against their own box rather than inherit one baked into chela; the `12G` example
above is consistent with (well above) this baseline for modest concurrency.

## Every suite run has its own ceiling (CMX-119)

The shared slice bounds the fleet's SUM. It does not stop ONE runaway suite run from eating
that whole budget. On 2026-10-13 two dispatched agents' `task-finished
--self-check-experiments` runs were stopped by Claude Code because "the machine was
critically low on memory". The cause was CMX-115's test branch, whose tests started real PM2
God Daemons that grew to ~4 GB each, outlived their test (reparented to PID 1) and ignored
SIGTERM. Three of them held 12 GB.

`chela.judge.run_suite` is the one place every judge battery, `judge sweep`,
`judge self-check` and `task-finished` suite run goes through. `chela/suite_guard.py` now
wraps each run in its own `systemd-run --user --scope`:

- `MemoryMax` = `CHELA_SUITE_MEMORY_MAX` (or `suite_memory_max` in `config.json`). The
  default is 3x the measured peak below (8.3G). `0` turns it off.
- `MemorySwapMax=0` makes a runaway get killed instead of thrashing swap, and
  `OOMPolicy=continue` makes the kernel kill the biggest process while the scope's own shell
  survives to record `memory.events`.
- The scope joins `chela-agents.slice` when the shared budget is on, so it still counts
  toward the fleet's sum.

A run whose scope logged an `oom_kill` comes back **CANNOT VERIFY**, with the message
`suite exceeded 8.3G (its memory ceiling, CMX-119)`. It is never read as a pass or a fail.

⛔ **Verify the guard, not the wrapper's exit.** The scope's first act is to write its own
`/proc/self/cgroup`. If that does not name `chela-suite-<id>.scope`, the run is refused with
"the memory ceiling was NOT applied". If no working `systemd --user` session exists (macOS, a
container), the suite runs unbounded, a warning is logged, and nothing claims a ceiling.

**Leaks are reaped.** A process reparented to PID 1 keeps its cgroup. After each run, chela
SIGKILLs whatever is left in the scope (`systemctl --user kill --signal=SIGKILL`). That is
exactly the shape of the 10-13 leak: the daemons ignored SIGTERM.

**Measured 2026-10-21** on a 20-core, 16 GB box, sampling the run's own cgroup
`memory.stat` every half second. One full `CHELA_REQUIRE_JS_TESTS=1 uv run pytest -q` run
(10,075 tests, `-n 4 --dist loadfile`):

| | anon peak | cgroup `memory.peak` | wall |
|---|---|---|---|
| Vitest at its default workers | 5.36 GiB | 6.62 GB | 10m11s |
| Vitest capped at 4 workers | **2.75 GiB** (2,954,940,416 B) | 4.69 GB | 9m36s |

`memory.peak` counts page cache, which the kernel reclaims before it OOM-kills anything, so
anon is the number that sizes the ceiling and the slots. `/usr/bin/time -v` reports a max RSS
of about 0.9 GB, but that is the coordinating process only. The 10-17 figure (1.81 GB) is out
of date: the React frontend's Vitest suite arrived since, and Vitest starts one jsdom worker
per core minus one. On 20 cores that was 19 workers and 4.3 GB on its own, so
`chela/dashboard/frontend/vite.config.ts` now caps it at 4 (1.2 GB, 33s instead of 17s).

## At most K suites at once (CMX-119)

A ceiling per run still lets N runs authorise N ceilings. On 10-20/21 Claude Code's memory
reaper killed six hand-in checks while three agents and the judges ran full suites at once.
So every `run_suite` first takes one of **K slots**, box-wide, judges and agents alike:
`flock` locks on `$CHELA_DIR/suite-slots/slot-<i>`. A lock dies with its holder, so a crashed
run never strands a slot.

- **K** = `CHELA_SUITE_SLOTS` > `suite_slots` in `config.json` > half the box's RAM over the
  measured anon peak, between 1 and 4. That is 2 on a 16 GB box. RAM that cannot be read is
  1. `0` turns admission control off.
- **Ranked, then first come, first served** (CMX-445). A free slot goes to a judge round
  first, then to hand-ins by their issue's priority (Urgent, High, Medium, Low, then no
  priority), then to the nightly sweep. Among equals, the oldest waiter goes first: a run
  that frees its slot and takes the next one a moment later waits behind a run already
  queued. A stopped waiter (a fleet pause) does not hold the line.
- **Visible.** The waiting run prints `⏳ waiting for a suite slot — all 2 busy: <worktree>
  (pid …, 4m), …`. A judge's `chela peek` badge says `⏳ waiting for a suite slot`.
- **Never stuck.** A queued run is waiting, not stuck. The judge watchdog holds a run while
  it waits and leaves the queued time out of its wall. `chela doctor` applies the same rule
  (CMX-445): a queued judge is never reported past its wall, and the time it spent queued is
  reported separately ("waited 73min for the suite slot"). The inbox sends no stuck or
  long-self-check notice while a run waits, and leaves queued time out of a self-check's
  running time (CMX-110). The self-check and sweep budgets (CMX-141) are not charged for it
  either.
- **Bounded.** A run waits at most 4 of its own suite timeouts. After that it is CANNOT
  VERIFY ("no suite slot came free in …"), never a pass.

Every `run_suite` call takes a slot, including a judge's targeted subset runs, which are
smaller than a full suite. That is the safe side of the trade.

### A battery holds one slot from start to finish (CMX-393)

Until CMX-393 a battery took a slot per suite run and freed it between mutations. With
`suite_slots: 1` that still ran one suite at a time, but the queue's fairness rule handed the
slot to the next battery after every mutation, so three agents' sweeps and self-checks ran
interleaved, all of them mid-flight at once. When memory ran low, Claude Code's reaper
killed all three (11-14: two runs; 11-17: three, at 16/51, 8/60 and 4/8).

Now each battery takes ONE slot for its whole run, and every `run_suite` inside it
re-enters that slot (`suite_guard.battery`; re-entry is per thread, so two judge threads in
one daemon still queue):

| battery | holds the slot across |
|---|---|
| `chela judge sweep` | provisioning, the baseline, every mutation's subset and confirmation run |
| `chela judge self-check` | the same, per experiment |
| a judge round (`run_experiments`) | the whole battery, the consistency re-runs included |
| `chela task-finished` | ALL its checks: self-check, sweep, standard-tier lint + suite, the base-move re-run |

A second agent's battery waits for the first one to finish, and its waiting line names the
holder. Its budget is not charged for the wait (`busy_clock`). It waits at most 12 suite
timeouts (`suite_slot_max_wait` overrides), then reports CANNOT VERIFY.

**A better-ranked waiter does not wait for the whole battery** (CMX-445). Between two
experiments, with no file mutated, a self-check or a hand-in's sweep checks the queue
(`suite_guard.yield_slot`). If a judge round or a higher-priority hand-in is waiting, it frees
the slot, queues again at its original claim time, and resumes once the slot comes back. That
re-queue has no time limit: every run ahead of it is bounded by its own budget, and a timeout
would throw away the experiments already measured. A waiting judge therefore gets the slot
within one experiment. On 11-19 a Medium self-check held the only slot for 2 h 08 m while an
Urgent judge waited 1 h 13 m. A judge round never yields, because nothing outranks it. The run
row stores the issue's priority at claim time (`runs.priority`). A standalone
`chela judge self-check` or `chela judge sweep` has no run row, so it ranks as a hand-in with
no priority.

**Stopped runs get one notice.** While a battery runs it keeps a record in
`$CHELA_DIR/suite-slots/batteries/`. A battery that ends, raises or exits removes the record.
One stopped by a signal or a SIGKILL leaves it. The daemon names every leftover record in
ONE inbox notice (`suite_runs_stopped`), once no slot is held and `MemAvailable` is at least
one suite's measured peak, so the orchestrator resumes them one at a time instead of
handling separate stops.

### The nightly sweep starts on idle and yields between mutations (CMX-400)

The nightly sweep's battery is the longest one (`sweep.budget_seconds`, up to 3 h). A fixed
start hour (`sweep.nightly_at`) collided with agents' hand-ins, which work around the clock.
With `sweep.mode: idle` (the default) the daemon starts it only when:

- no suite slot is held, so no hand-in, self-check, judge round or agent suite is running;
- nobody is waiting for a slot (`suite_guard.waiters`, read from the wait ledgers);
- no fleet pause is active;
- `MemAvailable` is at least one suite's measured peak (the CMX-393 resume floor). Memory
  that cannot be read counts as busy;
- the above has held without a break for `sweep.quiet_seconds` (default 600), so the sweep
  doesn't start between two hand-ins.

**Yield.** While the sweep holds the slot it checks, after each measured mutation (its file
already restored), whether another process is waiting for the slot. If one is, the sweep stops
there and releases the slot to it. The run ends as yielded, and every mutation it didn't reach
is owed (`owed.json`), so the next run starts with those. The first mutation always runs, so a
busy box still makes progress.

**Once a day.** A run that ends without yielding finishes the day. A yielded run may resume the
same day when the box is idle again, until the day's `sweep.budget_seconds` (a daily total) is
spent. The day record is `$CHELA_DIR/sweep-nightly/day.json`; a run that never reports back
ends its day, and an unreadable record starts nothing.

**Fallback.** If no idle window comes by `nightly_at` + `sweep.fallback_after_hours`
(default 3, so 05:00) and nothing has run that day, the sweep runs once anyway, on
`sweep.fallback_budget_seconds` (default 1800), still yielding. It fires only inside its own
window, `sweep.fallback_window_hours` (default 2, so 05:00–07:00): a day that missed it has no
fallback, rather than one that takes the slot from agents in working hours. The inbox notice
says "ran on fallback: the queue was never idle".

**Held off.** Two things keep the sweep from starting at all, and make a running one yield
between two mutations exactly as it does for an agent (the rest is owed):

- a dispatch pause (`chela dispatch --pause`), until it is released or its TTL runs out. The
  queue looks idle while it is paused, but the point of a pause is a quiet box. A hold file
  that can't be read counts as a pause;
- `sweep.blocked_hours`: a list of local `"HH:MM-HH:MM"` windows (one may wrap past midnight,
  `"23:00-01:00"`), default `["03:00-04:30"]`, the host's own cron window. `[]` turns it off;
  a list that doesn't parse is the default, said in the log. A fallback that falls inside a
  window moves to the window's end (02:00 + 2 h = 04:00 → 04:30).

The notice says which: "skipped — dispatch paused …", "skipped — inside blocked hours
03:00-04:30 …", "yielded: dispatch paused", "yielded: blocked hours 03:00-04:30". A hand run
(`chela judge nightly-sweep` without `--trigger`) is not held off: someone asked for it.

**Throughput.** The notice rates the battery (mutations/hour) only above a minimum sample, 10
mutations or 10 minutes; below it, it says "too few to rate".

`sweep.mode: fixed` keeps the old clock: one run a day from `nightly_at`, still yielding.

## Why chela cares (and does not fix all of it)

**chela's supervisor shares a failure domain with the workers it spawns.** The daemon, the
dashboard, and tmux itself are NOT put in `chela-agents.slice` alongside the agents/judges
they supervise — deliberately: the whole point of the shared slice is that the killer
never has to go global while it holds, and putting the supervisor in the same slice it
polices would mean a misbehaving agent's memory pressure could itself starve the daemon
that would otherwise notice and intervene. A dispatched agent runs `pytest`, `uv sync`, a
backtest — bounded now by the shared ceiling above, but a `chela-agents.slice`
over-subscribed past what fits in RAM still thrashes on swap for the reasons described
above, and sizing concurrency to the working set is still on the operator.

That remaining shape is a **known, accepted gap.** Stated, not built — so that when it
bites, it is a documented limit and not a mystery.
