# Getting started

The fastest path from a clone to your **first autonomously-dispatched agent** that
opens a PR on its own. Roughly 10 minutes. For the full reference, see the
[README](../README.md); this is the happy path.

## 0. Prerequisites

- **Python ≥ 3.11**, **`tmux`**, **`git`**
- [`uv`](https://docs.astral.sh/uv/) — the package manager chela uses
- the **`claude` CLI** on your `PATH` ([Claude Code](https://claude.com/claude-code))
- **`gh`** (GitHub CLI) — only needed for the dispatcher's PR flow

> **macOS works.** chela is developed on Linux but runs on macOS (a POSIX
> `/proc` fallback covers the process/window facts). If TUI glyphs render as
> boxes (tofu), install a symbol font — see [the fonts note below](#fonts-macos).

## 1. Install

```bash
git clone https://github.com/Devail1/chelamux && cd chelamux
uv sync                       # core only (two small deps, no Flask)
uv run chela status
```

Want the dashboard + live terminal wall too? It's a separate, optional install
so the CLI stays lean:

```bash
uv sync --extra dashboard
```

The Telegram bridge (`chela telegram`) is its own extra as well:

```bash
uv sync --extra telegram
```

> ⚠️ **Ask for every extra you want in ONE command.** `uv sync --extra X` *replaces*
> the environment rather than adding to it, so running the two lines above in sequence
> leaves you with telegram and **no dashboard**. For both:
> `uv sync --extra dashboard --extra telegram` (or `uv sync --all-extras`).

## 2. Authenticate Claude once

chela never handles credentials — it drives the `claude` CLI inside your tmux
windows, so every agent reuses one cached login:

```bash
claude          # then /login   (or: claude setup-token, for a headless token)
```

The whole fleet runs as **one Claude account** and shares its rate limits.

## 3. See what chela sees

Make a tmux session whose **windows are your agents** (the window name is the
agent's display name):

```bash
tmux new-session -d -s chela -n researcher
uv run chela status            # lists the windows chela can drive
```

> The session is named `chela` by default; override with `CHELA_TMUX_SESSION`.

## 4. Start the daemon — this is the engine

Nothing autonomous happens without it. Schedules don't fire, dispatched TODOs
never become PRs, completion never wakes the orchestrator. Run it under a process
manager in production (see [`examples/ecosystem.config.js`](../examples/ecosystem.config.js)),
but for a first look, one terminal is fine:

```bash
uv run chela run               # leave this running
```

## 5. Your first dispatched agent

This is the headline feature: a `- [ ] task` line becomes a git worktree → an
agent that implements it → a PR, reviewed by an adversarial judge.

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

On the next dispatch pass the daemon claims the task, spins up a worktree, runs
the agent, strikes the `- [ ]` line, and opens a PR. Watch it on the dashboard
(`uv run chela dashboard`, then <http://127.0.0.1:5001>) or with `uv run chela
dispatch-runs`. **Auto-merge is off by default** — the PR waits for you to merge.

> **Guard discipline is the contract.** Every guard must go **RED** when the thing
> it protects is broken — the judge corrupts each guard in a throwaway worktree and
> blocks any PR whose guard survives its own corruption. See
> [CONTRIBUTING.md](../CONTRIBUTING.md).

> **Prefer an agent set it up?** Copy [`skills/chela-setup`](../skills/chela-setup/SKILL.md)
> into `~/.claude/skills/` and a Claude Code agent will install chela and seed a
> starter `WORKFLOW.md` + `TODO.md` for the current repo.

## 6. Optional polish

- **Exact context / rate-limit numbers** — `uv run chela install-statusline --write`
  installs the statusLine hook the dashboard reads.
- **Phone push when an agent blocks** — set `CHELA_NOTIFY_URL` (ntfy / Telegram /
  webhook); see [README → Needs-input notifications](../README.md#needs-input-notifications).
- **Remote access** — the dashboard binds `127.0.0.1` with no built-in auth by
  design. Put it behind Tailscale or an SSH tunnel — the tailnet is the trust
  boundary. See [README → Remote access & security](../README.md#remote-access--security).

## Fonts (macOS)

The TUI markers (`⏺ ❌ ✅`, the working spinner) and Nerd icons need a font that
covers those codepoints. If any surface shows boxes, install a symbol font such
as [Symbola](https://dn-works.com/ufas/) and let the surface's font-fallback pick
it up. chela wires a coverage fallback on each render surface (dashboard, telegram
`/screenshot`, collab viewer) — a missing glyph means the underlying font stack
lacks it, not chela.

## Where to go next

- [README](../README.md) — the full feature tour
- [docs/CONFIG.md](CONFIG.md) — the environment, precedence, and PM2 migration
- [docs/HOOKS.md](HOOKS.md) · [docs/EVENTS.md](EVENTS.md) — how the plugin and event log work
- [WORKFLOW.md](../WORKFLOW.md) — the dispatch prompt agents run against
- [CONTRIBUTING.md](../CONTRIBUTING.md) — the dev loop and guard discipline
