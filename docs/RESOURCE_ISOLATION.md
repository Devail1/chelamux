# Resource isolation — a per-job memory ceiling does not bound the box

> **A per-job memory ceiling does not bound the box. Only a shared one does.**

That sentence is the whole page. Everything below is the incident that proves it, the fix
that holds, and the way it fails when you push it — which is *not* the way you expect.

Nothing here is implemented in chela. This is a **known, accepted gap** (see
[Why chela cares](#why-chela-cares-and-does-not-fix-it)) plus the mitigation that runs
outside it.

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

The mitigation lives in a personal `~/bin/memcap` wrapper — **not in chela** — and it is
three guards, in the order that matters:

**1. A shared cgroup slice — this is the one that actually bounds the box.** Every job,
however many you fan out, is launched *into the same slice*, so the ceiling applies to
their **sum**:

```ini
# ~/.config/systemd/user/memcap.slice   (survives reboot)
[Unit]
Description=Memory-capped batch jobs

[Slice]
MemoryMax=12G
```

```bash
systemctl --user daemon-reload
systemd-run --user --slice=memcap.slice --scope -p MemoryMax=6G -- python heavy_thing.py
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

Four agents at a measured 4.5 GB each is 18 GB. Under a 12 G slice that does not crash —
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

## Why chela cares (and does not fix it)

**chela's supervisor shares a failure domain with the workers it spawns.** The daemon, the
dashboard, tmux, and every dispatched agent live in the same memory. A dispatched agent
runs `pytest`, `uv sync`, a backtest — with **nothing bounding it** — and when the box goes
down it takes the orchestrator with it. The component that would have *noticed* is the
component that dies.

That is a **known, accepted gap.** chela does not put agents in cgroups, and this task
deliberately did not add it: the mitigation is to run heavy work under a shared-slice
wrapper like the one above, outside chela.

Stated, not built — so that when it bites, it is a documented limit and not a mystery.
