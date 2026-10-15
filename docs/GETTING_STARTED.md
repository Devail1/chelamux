# Getting started

The fastest path from a clone to your **first autonomously-dispatched agent** that
opens a PR on its own. Roughly 10 minutes. For the full reference, see the
[README](../README.md); this is the happy path.

## 0. Prerequisites

- **Python ≥ 3.11**, **`tmux`**, **`git`**
- [`uv`](https://docs.astral.sh/uv/) — the package manager chela uses
- the **`claude` CLI** on your `PATH` ([Claude Code](https://claude.com/claude-code))
- **`gh`** (GitHub CLI), logged in (`gh auth login`) — the daemon uses it to push branches
  and open PRs

> **macOS works.** chela is developed on Linux but runs on macOS (a POSIX
> `/proc` fallback covers the process/window facts). If TUI glyphs render as
> boxes (tofu), install a symbol font — see [the fonts note below](#fonts-macos).

## 1. Install

```bash
git clone https://github.com/Devail1/chelamux && cd chelamux
uv sync --all-extras          # core + dashboard + Telegram bridge
uv run chela status
```

That is the one install path. `--all-extras` installs everything in one command, which
matters: `uv sync --extra X` *replaces* the environment instead of adding to it, so naming
extras one at a time leaves you with only the last one. (A headless box that will never run
the dashboard or the Telegram bridge can use plain `uv sync`, which installs only the core.)

Then, **inside Claude Code**, add the hooks plugin. It powers blocked-agent handling,
answering from your phone, and the merge gate that stops an agent from running
`gh pr merge` itself ([HOOKS.md](HOOKS.md)):

```
/plugin marketplace add Devail1/chelamux
/plugin install chela@chela
```

Configuration is optional: chela runs on built-in defaults. When you want to change
something, copy [`examples/chela.env`](../examples/chela.env) to `~/.chela/chela.env` and
edit it ([CONFIG.md](CONFIG.md)); `uv run chela doctor` checks that what is running matches
it.

## 2. Authenticate Claude once

chela never handles credentials — it drives the `claude` CLI inside your tmux
windows, so every agent reuses one cached login:

```bash
claude          # then /login   (or: claude setup-token, for a headless token)
```

The whole fleet runs as **one Claude account** and shares its rate limits.

## 3. See what chela sees

Make a tmux session whose **windows are your agents** (the window name is the
agent's short, stable key — what `chela peek`/`msg` and the Telegram bindings use):

```bash
tmux new-session -d -s chela -n researcher
uv run chela status            # lists the windows chela can drive
```

> The session is named `chela` by default; override with `CHELA_TMUX_SESSION`.
> Windows chela opens for you (the dashboard launcher, Telegram `/new`, `chela spawn <dir>`)
> are named after their folder.

> **Window names** and how they line up with Remote Control on claude.ai are described in
> [DASHBOARD.md](DASHBOARD.md#window-names-and-remote-control).

## 4. Start the daemon — this is the engine

Nothing autonomous happens without it. Schedules don't fire, dispatched TODOs
never become PRs, completion never wakes the orchestrator. Run it under a process
manager in production (see [`examples/ecosystem.config.js`](../examples/ecosystem.config.js)),
but for a first look, one terminal is fine:

```bash
uv run chela run               # leave this running
```

## 5. Your first dispatched agent

This is the headline feature: a task becomes a git worktree → an agent that implements
it → a PR, reviewed by an adversarial judge. The queue can be a markdown `TODO.md` (shown
here, nothing to sign up for), GitHub issues, or a Linear team — for Linear, follow
[LINEAR_TRACKER.md](LINEAR_TRACKER.md) instead of copying `TODO.md`.

```bash
# In a repo you own (with a GitHub remote), seed the two files from the examples:
cp /path/to/chelamux/examples/WORKFLOW.md ./WORKFLOW.md
cp /path/to/chelamux/examples/TODO.md ./TODO.md

# Point the daemon at that workflow (or set CHELA_DISPATCH_WORKFLOWS):
uv run chela dispatch "$PWD/WORKFLOW.md" --once     # one pass; drop --once to poll
```

Add a task to `TODO.md` under `## Open`, using the four-field brief the judge
enforces:

```markdown
## Open

- [ ] **Add a --version flag to the CLI.**
  **OBJECTIVE.** Print the package version and exit 0.
  **BOUNDARIES.** The CLI entrypoint + its test only. PR → your default branch.
  **GUARDS.** A test asserting `--version` prints the version and exits 0;
  break the flag → the test goes RED.
  **VERIFY.** `mytool --version` prints the version.
```

On the next dispatch pass the daemon claims the task, spins up a worktree, runs the
agent, and pushes and opens the PR for it. Watch it on the dashboard
(`uv run chela dashboard`, then <http://127.0.0.1:5001>) or with `uv run chela
dispatch-runs`. **Auto-merge is off by default.** When the judge passes the PR, merge it
with `uv run chela merge <run>`; the dispatcher then strikes the `- [ ]` line on your base
branch. What to do when a run stops for a human — retry, reopen, override, close — is in
[RUNS.md](RUNS.md).

> **Guard discipline is the contract.** Every guard must go **RED** when the thing
> it protects is broken — the judge corrupts each guard in a throwaway worktree and
> blocks any PR whose guard survives its own corruption. See
> [CONTRIBUTING.md](../CONTRIBUTING.md). To see where your own tests stand first, run
> `uv run chela judge sweep --cwd /path/to/your/repo --base origin/main` on a branch: it lists
> every changed check no test catches ([RUNS.md](RUNS.md#the-sweep-on-its-own)).

> **Prefer an agent set it up?** Copy [`skills/chela-setup`](../skills/chela-setup/SKILL.md)
> into `~/.claude/skills/` and a Claude Code agent will install chela and seed a
> starter `WORKFLOW.md` + `TODO.md` for the current repo.

## 6. Optional polish

- **Exact context / rate-limit numbers** — `uv run chela install-statusline --write`
  installs the statusLine hook the dashboard reads.
- **Phone push when an agent blocks** — set `CHELA_NOTIFY_URL` (ntfy / Telegram /
  webhook); see [Needs-input notifications](https://chela.pages.dev/docs#needs-input).
- **Remote access** — the dashboard binds `127.0.0.1` with no built-in auth by
  design. Put it behind Tailscale or an SSH tunnel — the tailnet is the trust
  boundary. See [Remote access & security](https://chela.pages.dev/docs#security).

## Fonts (macOS)

The TUI markers (`⏺ ❌ ✅`, the working spinner) and Nerd icons need a font that
covers those codepoints. If any surface shows boxes, install a symbol font such
as [Symbola](https://dn-works.com/ufas/) and let the surface's font-fallback pick
it up. chela wires a coverage fallback on each render surface (dashboard, telegram
`/screenshot`, collab viewer) — a missing glyph means the underlying font stack
lacks it, not chela.

## Where to go next

- [README](../README.md) — what's new, and the full documentation map
- [docs/RUNS.md](RUNS.md) — a dispatched run from claim to merge, and every command on the way
- [docs/DASHBOARD.md](DASHBOARD.md) — the Wall, the sidebar, the Work board and the Usage view
- [docs/CONFIG.md](CONFIG.md) — the environment, precedence, and PM2 migration
- [docs/HOOKS.md](HOOKS.md) · [docs/EVENTS.md](EVENTS.md) — how the plugin and event log work
- [WORKFLOW.md](../WORKFLOW.md) — the dispatch prompt agents run against
- [CONTRIBUTING.md](../CONTRIBUTING.md) — the dev loop and guard discipline
