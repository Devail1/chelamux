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

**What this closes:** the "four correct per-job caps still authorised 24G on a 19G box"
failure mode above, for chela's own fleet specifically — the slice bounds the SUM of every
agent and judge chela itself launches. **What it does not close:** everything else on this
page. The shared slice is still a safety net, not a plan (`concurrency × working-set <
SLICECAP` still has to hold, see the counterintuitive-swap section above), `choom` biasing
and non-chela heavy jobs (backtests, test fans run by hand) are still the operator's own
`~/bin/memcap`-style wrapper, and chela's own daemon/dashboard/tmux are deliberately left
OUTSIDE `chela-agents.slice` — see below.

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
