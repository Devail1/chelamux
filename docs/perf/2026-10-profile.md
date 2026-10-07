# chela services under normal load: a profile (CMX-15, 2026-10-06)

This is a measurement report. It changes no production code. It follows CMX-14, which
capped the glibc malloc arenas and so took memory off the table.

**In short:** the dashboard spends most of its CPU *starting processes*. About 90% of its
subprocess spawns come from two endpoints, `/api/agents` and `/api/agents/context`. Each
call runs 2–4 `tmux`/`pgrep` processes **per window**, and every open Wall tab calls both
every 4 s. The cost grows linearly with window count. One phone tab on the Wall costs 7% of
a core at 5 windows and 73% at 40. The batched replacement already exists
(`chela.sessions.panes()`: one `tmux` call plus `/proc`). In a microbenchmark it is 17×
cheaper at 5 windows and 67× cheaper at 40. Swapping the server (waitress) changes nothing
measurable, and memory is flat.

## 1. Method

| What | How |
|---|---|
| Isolation | A COPY of `$CHELA_DIR` (no secrets, sockets or worktrees; scheduled tasks disabled; dispatch, notify, relay and restore-resume off). A **private** tmux server (`tmux -L chela-perf` on every call — never `TMUX_TMPDIR`, which `$TMUX` overrides; see CMX-21) with N fake agent windows (`fake_claude.py`, which matches `pgrep -f claude` and prints like an agent). Spare ports. Egress to a dead proxy, so the harness Telegram bot only ever hits `Connection refused`. The live services and the live tmux socket were never touched. |
| Services | `dashboard`, `run` (daemon), `telegram --no-inbound --auto-topics` (fake token, every window bound), plus `scripts/agent-terminals.sh` for real ttyds behind the Wall. Each runs under `py-spy record` at 50 Hz. |
| Instrumentation | `prof_run.py` wraps the real entry point without editing it. It logs every request's wall time, handler-thread CPU, bytes, body hash, and the spawns made inside it. It also counts every `subprocess.Popen` by command, thread and endpoint. |
| Load | Real headless Chromium (Playwright), 3 tabs: **wall** (1600×1000, default Wall view, 10 live ttyd tiles), **work** (1440×900, Work view) and **phone** (iPhone 13 profile, Wall view). Normal UI cadences, no synthetic hammering. |
| Process metrics | `/proc/<pid>/stat` (self CPU, plus reaped-children CPU = the cost of every spawned tmux/pgrep/claude), RSS, threads and fds, sampled every 10 s. |
| Live services | The same `/proc` sampler, read-only, over 10 min. Nothing was attached, signalled or requested. |

Runs: **main** = 10 windows, 3 tabs, 22 min. **scale** = 5 / 20 / 40 windows, 1 phone
tab, 4 min each. **SSE** = 0/1/3/6/12 idle `/api/events` clients, 90 s each.
**A/B** = werkzeug vs waitress, replaying the main run's recorded HTTP mix, 6 min each.
The harness is committed under [`2026-10-harness/`](2026-10-harness/) so each optimization
below can be re-measured the same way (see §9).

## 2. Live services (read-only, 10 min, ~5 agent windows, 4 open dashboard connections)

| Service | CPU (self + children) | RSS | Threads | fds |
|---|---|---|---|---|
| chela-dashboard | **10.0%** of a core (4.9% + 5.1%) | 143 → 152 MB | 20–29 | 32–42 |
| chela-telegram | 3.0% (1.2% + 1.8%) | 119 → 122 MB | 6 | 10–13 |
| chela-daemon | 1.5% (0.1% + 1.4%) | 78 MB | 2 | 9 |
| chela-collab | ~0% | 42 MB | 2 | 7 |

Half of the live dashboard's CPU is spent in child processes. The live daemon used 0.5 s
of CPU in this window (minutes 1–11 after start) and 37 s by minute 32, consistent with the
doctor burst in §5.2. The harness's 5-window, 1-tab run measured 7.3% for the dashboard, in line with the
live 10%.

## 3. Where the dashboard's CPU and time go (main run: 10 windows, 3 tabs, 22 min)

Dashboard: **28.6% of a core**. 9.3% is its own Python and **19.4% is child processes**.
It spawns **3,260 processes/min**. RSS is flat at 145 MB and threads peak at 94 (§4).
Telegram uses 4.7% (774 spawns/min) and the daemon 2.4%.

**Spawns by origin (73,639 in 22 min):**

| Origin | Share | What it runs |
|---|---|---|
| `/api/agents/context` | 49.6% | per window: `list-windows` (name→id via `get_window_cwd`), `display-message`, `pgrep -P` |
| `/api/agents` | 40.0% | per window: `display-message` + `pgrep -P` (`claude_pid`), `display-message` (`pane_command`) |
| SSE threads | 5.1% | `list-windows` once per second **per open tab** |
| `/api/orchestrator/status` | 4.1% | the same per-window probe |
| everything else | 1.2% | |

**Per endpoint** (thread CPU excludes child processes. "same" = the response was
byte-identical to the same client's previous poll):

| Endpoint | req/min | p50 ms | p95 ms | CPU ms/req | spawns/req | KB/req | same |
|---|---|---|---|---|---|---|---|
| `/api/agents` | 33.7 | 175 | 331 | 41.7 | 38.6 | 4.5 | 100% |
| `/api/agents/context` | 39.3 | 167 | 247 | 26.0 | 41.0 | 0.0* | 100% |
| `/api/restore` | 5.7 | 163 | 383 | **172.6** | 1.1 | 1.0 | 100% |
| `/api/dispatcher` | 5.7 | 28 | 236 | 24.0 | 1.0 | **115.8** | 100% |
| `/api/orchestrator/status` | 5.7 | 119 | 168 | 14.9 | 22.0 | 0.9 | 100% |
| `/api/rooms` | 14.0 | 4 | 6 | 1.0 | 1.0 | 0.4 | 100% |
| `/api/resources` | 5.7 | 101† | 106 | 0.8 | 0 | 0.1 | 0% |
| `/api/summary`, `/api/log`, `/api/launcher`, `/api/share-requests`, `/api/version`, `/api/agents/status_health` | ~6 each | ≤5 | ≤10 | ≤2.6 | ≤1 | ≤1.1 | — |

\* The fake windows have no statusLine cache, so `/context` returns `[]` here. Live, it
carries one row per agent, and its cost is the same probes. † A deliberate 100 ms
`/proc/stat` sampling gap, not work.

**Poll rates per client.** A tab showing the Wall (desktop *or* phone) calls `/api/agents`
and `/api/agents/context` **every ~4 s** (`terminals.js` `TERM_REFRESH_MS`), about 16/min
each. A tab on any other view calls them about 2–4/min. API requests per minute: wall 67,
phone 52, work 25. So **the Wall's 4-second tick is the load**, and a phone left on the
Wall costs as much as a desktop.

**py-spy (dashboard Python, inclusive):** `api_agents` 29%, `_sse_stream` 22%,
`list_runs` 19% (12 points of it via the SSE loop), `api_restore` 17%, `api_agents_context`
17%, `subprocess._execute_child` 12% self. `epoch.current()` adds 6%: a `tmux` spawn whose
answer is constant for the tmux server's lifetime, reached per window through
`sessionids.session_id_for`. `/api/restore` spends its time in `sessions.wid_for_session`,
which **rescans the event-log ring once per dead-epoch row**. That is O(rows × ring), and
`chela doctor` reports 301 such rows on this host.

### Scaling with window count (1 phone tab on the Wall)

| Windows | dashboard CPU | `/api/agents` p50 / p95 | spawns/min (dash / tg / daemon) | telegram CPU |
|---|---|---|---|---|
| 5 | 7.3% | 89 / 119 ms | 860 / 490 / 64 | 2.9% |
| 10 (3 tabs) | 28.6% | 175 / 331 ms | 3,260 / 774 / 58 | 4.7% |
| 20 | 27.4% | 436 / 679 ms | 2,940 / 1,610 / 134 | 10.6% |
| 40 | **72.6%** | **1,161 / 1,451 ms** | 5,860 / 3,110 / 206 | **25.0%** |

**The probe in isolation** (`bench_panes.py`, CPU per call including children). It
compares what `/api/agents` does today with the existing batched `sessions.panes(force=True)`:

| Windows | per-window probe (today) | `sessions.panes()` | ratio |
|---|---|---|---|
| 5 | 40 ms | 2.4 ms | 17× |
| 20 | 199 ms | 5.0 ms | 40× |
| 40 | 448 ms | 6.7 ms | 67× |

## 4. Server model, threads and SSE

**Werkzeug `threaded=True` vs waitress (16 threads)**, replaying the identical recorded mix:

| | CPU | client p50 / p95 / p99 | RSS | threads |
|---|---|---|---|---|
| werkzeug | 30.9% | 199 / 483 / 952 ms | 99.6 MB | 3–8 |
| waitress | 30.5% | 199 / 460 / 944 ms | 98.1 MB | 19 (fixed) |

There is no measurable difference, because the time goes into the handlers' subprocesses,
not into the server. Waitress also cannot host the Wall's `flask-sock` websocket proxy
(`/term/<wid>/ws`), and every open SSE tab permanently occupies one of its workers. With
the default 4 threads and 3 SSE tabs, one worker serves everything, and `/api/log` p95 was
192 ms vs 24 ms on werkzeug under the same 2-minute load. The overall tails of those two
short runs were too noisy to rank.

**SSE per open tab (idle, 10 windows):**

| Open `/api/events` | CPU per 90 s (self + children) | threads | RSS |
|---|---|---|---|
| 0 | 0.78 s (0.9%) | 3 | 54 MB |
| 1 | 1.41 s | 4 | 59 MB |
| 3 | 2.99 s | 6 | 69 MB |
| 6 | 5.09 s | 9 | 75 MB |
| 12 | 11.39 s (12.7%) | 15 | 105 MB |

Each tab costs **~0.7–1% of a core, one thread and ~4 MB**. Each stream independently
re-runs every snapshot each second: `list-windows`, `list_runs()` (`SELECT *` of 68 rows ×
51 columns, about 1.1 MB of row data, ~1.7 ms), the event-log tip, the inbox and the
terminals map. Nothing is shared between tabs. Threads themselves are cheap: the main run
peaked at 94 (each Wall tile = 1 handler + 1 upstream pump thread, plus the SSE streams),
and RSS stayed flat at 145 MB under the CMX-14 arena cap.

## 5. Daemon and Telegram

### 5.1 Telegram

Every `--interval` (2 s), for **every bound window**, the bridge spawns:

- `capture-pane` (gate watcher): 290/min at 10 windows.
- `display-message` from `epoch.current()`: the outbound monitor reaches it through
  `sessions.resolve_window` → `sessionids.session_id_for`.
- More per-window probes.

It also re-parses `session-ids.json` per window per poll, which is why `json` decoding is
11% of its own Python samples. Its cost scales linearly: 2.9% / 4.7% / 10.6% / 25% of a
core at 5 / 10 / 20 / 40 windows. Most windows are idle most of the time, yet each is
captured every 2 s anyway.

### 5.2 Daemon: a 36-second CPU burst in `chela doctor`

`runtime_truth.collected_js_suites()` builds its result like this:

```python
found = {suite for line in proc.stdout.splitlines()
               for suite in _js_suites_on_disk()   # re-walks the repo for EVERY line
               if suite in line}
```

`pytest --collect-only -q` prints 5,883 lines, so the repo is walked 5,883 times. Measured:
**35.6 s of CPU** per read (+3.7 s for pytest itself). With the walk hoisted out of the
comprehension, the same work takes **~33 ms** (6 ms walk + 27 ms matching). This runs at
every daemon start, every hour (`CHELA_DOCTOR_CHECK_INTERVAL`) and on every interactive
`chela doctor`, where it is ~37 s of the command's wall time. It is consistent with the
live daemon's 37 s of CPU in its first 32 minutes. Outside it, the daemon is ~1.5%, mostly
`claude agents --json` (~0.28 s CPU per call, shared with the dashboard's background
refresh).

## 6. Recommendations, ranked by measured gain against effort and risk

1. **Batch the per-window pane probes.** *Gain: large, and it grows with N.* Effort S–M,
   risk low–medium.
   `/api/agents`, `/api/agents/context` and `/api/orchestrator/status` should take
   `claude_pid` and the pane command from `sessions.panes()`. That is one `tmux list-windows`
   plus `/proc`, behind a 1 s TTL that is *shared* across concurrent requests and tabs.
   `/context` should resolve name→window once per request instead of once per agent. These
   call sites produce 94% of dashboard spawns, and child processes are 68% of dashboard
   CPU. Expected effect (projected from the spawn share, not measured): the main-run dashboard
   drops from ~29% to about 10% of a core, and the 40-window case from 73% to well under
   20%. `/api/agents` latency at 40 windows (p50 1.2 s, nearly all of it spawn wall time)
   should fall roughly in proportion.
   Risk: `sessions._looks_like_claude` already mirrors `pgrep -f claude` (CMX-160), so
   attribution should not change. Keep `claude_running` and `window_type` identical, and
   re-run `bench_panes.py`. Do the same for `epoch.current()`: cache it for one tick, or
   read `#{pid}-#{start_time}` from the same `list-windows` call. It is one spawn per
   window per poll in Telegram and 6% of dashboard Python.

2. **Hoist `_js_suites_on_disk()` out of the comprehension in `collected_js_suites`.**
   *Gain: 35.6 s → 0.03 s per doctor read.* Effort XS (one line), risk ~0.
   Averaged over an hour this is only ~1% of a core. Each read, though, is a full-core
   burst of ~36 s, and it makes every `chela doctor` invocation ~37 s slower. A test can
   pin it by counting walks for a multi-line collector output.

3. **Telegram: capture only the panes that changed.** *Gain: about 85% of Telegram's spawns
   in a mostly idle fleet. 25% → a few % of a core at 40 windows.* Effort S, risk low–medium.
   One `tmux list-windows -F '#{window_id} #{window_activity}'` per tick, then
   `capture-pane` only for windows whose activity stamp moved since their last capture
   (gate prompts render as pane output, so they move it). Pair it with recommendation 1's
   `epoch.current()` cache, and an mtime-keyed cache for `sessionids._load()`.
   Risk: a gate that appears without bumping `window_activity` would be missed. Keep a slow
   full sweep (e.g. every 30 s) as a backstop until that is measured.

**Worth doing after the top 3 (smaller, measured):**

- **SSE: one snapshot producer, many subscribers.** One thread computes the snapshots per
  second and fans the diffs out to the streams. That removes the per-tab ~0.7–1% of a core
  and the per-tab 1.1 MB `list_runs()` materialisation that CMX-14's tracemalloc flagged.
  Effort M.
- **`/api/restore`: index the event-log ring once per call** (`session_id → [wid]`) instead
  of rescanning it per row. It is 173 ms CPU per call (≈1.6% of a core across 3 tabs), and
  it scales with dead-epoch rows (301 on this host; `chela restore` clears them). Effort S.

## 7. Not worth it (measured)

- **Replacing Werkzeug with a pooled WSGI server (waitress).** It changed CPU, latency and
  RSS by nothing measurable (§4). It would break the Wall's websocket proxy and let SSE
  tabs starve the pool. The "thread per request" model is not where the time goes.
- **ETag / 304 on the polled endpoints.** In the harness every polled body was 100%
  identical poll-to-poll. Live, `/api/agents` changes more often, so treat that as an upper
  bound. But the cost is in *computing* the response (the subprocesses), not in sending it:
  an ETag computed after the work saves only bytes. And bytes are small: ~300 KB/min per tab,
  ~220 KB/min of which is `/api/dispatcher` (116 KB, compresses 4.6× with gzip). If phone
  data ever matters, `encode zstd gzip` in the fronting reverse proxy is a one-line
  deployment change with no code. Push-over-SSE for `/api/agents` only pays off after
  recommendation 1 makes the snapshot cheap, and then it barely matters.
- **Lengthening the client poll intervals.** This would hide the per-window cost rather than
  remove it, and the 4 s Wall tick is what keeps tiles live. After recommendation 1, a 4 s
  tick at 40 windows costs a few ms per call.
- **Memory work.** RSS was flat for 22 minutes at 94 threads (145 MB dashboard, 112 MB
  daemon, 122 MB telegram). CMX-14 settled this.
- **`list_runs()` SQL tuning (indexes, column pruning for CPU).** It is 1.7 ms per call. It
  only matters because SSE multiplies it per tab per second, so fix the multiplication
  (above) instead.
- **The collab service.** ~0% CPU and no spawns.

## 8. macOS differences (not measured here; derived from the code paths)

- **No glibc.** CMX-14's `mallopt(M_ARENA_MAX)` is a no-op there, and libmalloc has its own
  zone behaviour. The RSS figures above are Linux, post-cap.
- **Spawns cost more on the parent side.** On Linux, CPython 3.13 spawns with `vfork`. On
  macOS, the default `close_fds=True` path uses a real `fork()` of a ~150 MB, 30–90-thread
  process. Each of the 3,260 spawns/min therefore costs more, and recommendation 1 matters
  *more*. The tmux and pgrep work in the child is the same.
- **`sessions.panes()` is not one spawn on macOS.** With no `/proc`, its per-pid helpers
  (`_children`, `_comm`, `_cmdline_argv`, the cwd and start-time reads) fall back to
  `pgrep`/`ps`/`lsof`, about 4–6 spawns per window. That is *more* per call than today's
  probe. On macOS the only win is the 1 s TTL sharing across requests and tabs.
  Recommendation 1 should build the process table there from **one**
  `ps -axo pid=,ppid=,comm=,args=` per refresh. That works on both platforms.
- `/api/resources` returns `null` for CPU/RAM on a host without `/proc` (by design).
  Telegram's and the daemon's tmux costs are identical.

## 9. Reproducing (and measuring an optimization)

```bash
cd docs/perf/2026-10-harness
export PERF_HOME=/some/scratch/dir SPY_BIN=$(command -v py-spy)   # py-spy: `uvx py-spy`
bash mkcopy.sh                         # copy ~/.chela → $PERF_HOME/chela, scrubbed
bash main_run.sh main 10 22            # 10 windows, wall+work+phone tabs, 22 min → tables
bash main_run.sh s40 40 4 phone        # scaling point
python3 bench_panes.py                 # (after `. env.sh; bash mkwin.sh N`, from the repo root)
python3 spy.py $PERF_HOME/runs/main/dash.raw    # py-spy hot functions
```

`ab_run.sh` (server A/B via `PERF_SERVER=waitress`, with waitress on `PYTHONPATH`) and
`sse_exp.sh` reproduce §4. The harness never reads the live tmux socket. It does run the
daemon's normal read-only probes (`pm2 jlist`, `systemctl --user`, `git`) like any daemon
start would. Node needs `playwright` (a dev dependency) with its Chromium.

Caveats: fake panes never change state, so "same" percentages are upper bounds. Headless
tabs are never throttled; real background tabs are, so live load per open tab is at most
what was measured here. Short scaling runs include the daemon's startup doctor burst, so
daemon figures in §3's scaling table are inflated. Use the main run's.
