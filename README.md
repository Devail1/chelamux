# chelamux

**A tiny control plane that puts a fleet of Claude Code agents to work — unattended.**

chela runs as a small daemon over a single tmux session. It does two things:

- **Schedules** long-lived agents — poke an agent's pane on an interval or cron
  (`every 1h`, `0 */8 * * *`, a one-shot timestamp).
- **Dispatches** work — turn a markdown `TODO.md` (or GitHub issues) into one
  **git worktree per task**, spawn an agent in it, and let it **open a PR**.

Where [clawmux](https://github.com/zeulewan/clawmux) and
[ccmux](https://github.com/skzv/ccmux) help you *talk to and supervise* agents,
chela is for **putting them to work** and walking away. You watch them however
you already watch tmux — `tmux attach`, [Mosh](https://mosh.org/), or the
optional web dashboard (think *ccmux, weblized*).

> Status: early. Core (scheduler + dispatcher + messaging) is solid and tested;
> the dashboard is an optional extra; the embedded terminal **wall** streams
> live ttyd sessions and is opt-in (off by default — it spawns writable shells,
> so enable it only behind loopback/Tailscale).

---

## Install

chela uses [`uv`](https://docs.astral.sh/uv/). The core has two small deps
(`croniter`, `pyyaml`); the dashboard is an **optional extra**.

```bash
git clone https://github.com/<you>/chelamux && cd chelamux
uv sync                       # core only — no Flask
uv run chela status

# optional web dashboard:
uv sync --extra dashboard
```

Requirements: Python ≥ 3.11, `tmux`, `git`, the `claude` CLI on `PATH`
(plus `gh` for the dispatcher's PR flow).

---

## Run

```bash
# 1. Make a tmux session whose windows are your agents (the window name is the
#    agent's display name). Override the session name with CHELA_TMUX_SESSION.
tmux new-session -d -s chela -n researcher

# 2. See what chela can see:
uv run chela status

# 3. Schedule an agent:
uv run chela schedule add researcher --every 1h --prompt "Run your research cycle."

# 4. Start the daemon (scheduler + optional dispatcher + needs-input notify):
uv run chela run

# 5. Dispatch work items from a repo's WORKFLOW.md (see examples/):
uv run chela dispatch /path/to/repo/WORKFLOW.md --once    # one pass
uv run chela dispatch /path/to/repo/WORKFLOW.md           # poll
```

The **dispatcher** is the headline feature. Drop a `WORKFLOW.md` + `TODO.md` in
a repo (copy `examples/`), and each `- [ ] task` becomes: a worktree on a fresh
branch → an agent that implements it, strikes the line, and opens a PR → a run
that flips to `done` when you merge. See [`examples/WORKFLOW.md`](examples/WORKFLOW.md).

---

## CLI

| Command | What it does |
|---|---|
| `chela status` | List the agent windows chela sees in the tmux session |
| `chela run` | The daemon loop: scheduler tick + dispatcher + needs-input notify |
| `chela schedule add <agent> --every/--cron/--once --prompt ...` | Add a scheduled poke |
| `chela schedule list` / `remove <id>` | Manage scheduled tasks |
| `chela dispatch <WORKFLOW.md> [--once] [--interval N] [--dry-run]` | Run the work-item dispatcher |
| `chela dispatch-runs` | List dispatcher runs and their status |
| `chela task-finished <task_id>` | (agent uses this) mark a run awaiting-review + kill its window |
| `chela msg <agent> <text> [--from] [--priority]` | Message a live agent over tmux |
| `chela broadcast <text>` | Message every other live agent |
| `chela dashboard [--host] [--port]` | Launch the optional web UI (needs `[dashboard]` extra) |

---

## Config (environment)

| Variable | Default | Purpose |
|---|---|---|
| `CHELA_TMUX_SESSION` | `chela` | tmux session chela orchestrates |
| `CHELA_DIR` | `~/.chela` | State dir (scheduler.db, worktrees, context) |
| `CHELA_SCHEDULER_POLL_INTERVAL` | `30` | Daemon loop interval (s) |
| `CHELA_DISPATCH_WORKFLOWS` | — | Colon-separated WORKFLOW.md paths the daemon dispatches |
| `CHELA_DISPATCH_TICK_INTERVAL` | `60` | Dispatcher tick interval in the daemon (s) |
| `CHELA_AGENT_CMD` | `claude` | Launch command for the dashboard Start button |
| `CHELA_NOTIFY_URL` | — | Needs-input notification target (ntfy / Telegram / webhook) |
| `CHELA_NOTIFY_KIND` | auto | Force `ntfy` \| `telegram` \| `webhook` |
| `CHELA_NOTIFY_CHAT_ID` | — | Telegram chat id (if not in the URL) |
| `CHELA_NOTIFY_INTERVAL` | `20` | Pane-state scan interval (s) |
| `CHELA_DASH_HOST` / `CHELA_DASHBOARD_PORT` | `127.0.0.1` / `5001` | Dashboard bind |
| `CHELA_TERMINALS_ENABLED` | `false` | Embedded ttyd terminal wall (opt-in; streams live) |

---

## Needs-input notifications

When an agent's pane enters the `waiting` state (blocked on a permission prompt
or a question), chela fires a one-shot notification — so you don't have to babysit.

```bash
export CHELA_NOTIFY_URL=https://ntfy.sh/my-chela-topic              # ntfy
export CHELA_NOTIFY_URL="https://api.telegram.org/bot<token>/sendMessage?chat_id=<id>"  # Telegram
export CHELA_NOTIFY_URL=https://example.com/hook                    # generic JSON webhook
```

Transport is auto-detected from the URL; it's edge-triggered (one ping per
entry into `waiting`, not per tick). Pair it with ntfy on your phone for a
push-to-pocket "your agent needs you" alert.

---

## Remote access & security

**chela ships with zero built-in auth, by design.** The dashboard and the ttyd
terminals bind `127.0.0.1`. The terminal wall is a *writable shell* — exposing
it on an untrusted network is remote code execution. **The tailnet is the trust
boundary**, not a password.

For remote access, put the loopback dashboard behind one of:

- **[Tailscale](https://tailscale.com/)** — `tailscale serve 5001` gives you TLS
  + tailnet ACLs for free (recommended).
- An **SSH tunnel** — `ssh -L 5001:127.0.0.1:5001 host`.
- A reverse proxy **with your own auth**.

**Watch from your phone** without the web UI at all: SSH/Mosh into the box from
a mobile terminal — [Blink](https://blink.sh/) (iOS), [Termius](https://termius.com/),
or any Mosh-capable client — then `tmux attach -t chela` and you've got the live
panes. A QR of the connect string makes this one tap.

---

## Dashboard (optional)

`uv sync --extra dashboard && uv run chela dashboard` serves a web UI on
`127.0.0.1:5001` with tabs for **agents** (liveness from `claude agents --json`:
alive / waiting / offline), **schedules**, the **dispatcher**, and a **Kanban**
of runs. Liveness is derived live from the native session status — no heartbeat
daemon. An embedded ttyd **terminal wall** (a ccmux-style multi-pane view that
streams the live panes) is opt-in — off by default; enable it with
`CHELA_TERMINALS_ENABLED=true`.

### HTTP API (selected)

| Route | Returns |
|---|---|
| `GET /api/agents` | Per-window liveness/health, session status, context, schedules |
| `GET /api/summary` | Header counts (agents online, schedules) |
| `GET /api/schedules` · `POST` · `DELETE/PATCH /<id>` | Scheduled tasks CRUD |
| `GET /api/dispatcher` | Open tasks + active/awaiting/recent runs per workflow |
| `POST /api/dispatcher/runs/<id>/merge` · `/merge-all` | Squash-merge PRs + clean up |
| `POST /api/agents/{start,stop,restart,msg,broadcast,trigger}` | Agent controls |
| `GET /api/events` | Server-Sent Events stream (reactive UI accelerator) |

---

## How it works

- **Discovery is tmux-native.** `tmux list-windows` + `pane_current_path` are
  the single source of truth — no external state file, no daemon to coordinate
  with. tmux never lies about what's live right now.
- **The dispatcher** keys each task by a stable SHA of its source line, creates
  `~/.chela/worktrees/<...>/` per task on branch `<project_key>-<n>`, and tracks
  runs in `~/.chela/scheduler.db`. Dispatched agents default to
  `claude --permission-mode auto` (a classifier auto-approves safe ops and gates
  dangerous ones); set `agent.cmd: claude --permission-mode bypassPermissions`
  in `WORKFLOW.md` for zero-hang autonomy on a repo you trust.

---

## Credits

The work-item dispatcher is an adaptation of OpenAI's **Symphony** pattern
(task-list → isolated git worktree → autonomous agent → PR) — chela does not
claim novelty for that shape. The needs-input notification idea is borrowed from
**[ccmux](https://github.com/skzv/ccmux)**; the positioning contrast is with
ccmux and **[clawmux](https://github.com/zeulewan/clawmux)**.

## License

MIT — see [LICENSE](LICENSE).
