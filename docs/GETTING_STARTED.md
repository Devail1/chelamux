# Getting started

The fastest path from nothing installed to your **first autonomously-dispatched agent** that
opens a PR on its own. Roughly 10 minutes. For the full reference, see the
[README](../README.md); this is the happy path.

## 0. Prerequisites

- **`git`**, **`tmux`**
- [`uv`](https://docs.astral.sh/uv/), the package manager chela uses. It downloads
  Python ≥ 3.11 for you if the machine has none.
- the **`claude` CLI** on your `PATH` ([Claude Code](https://claude.com/claude-code))

That is the whole list for a first run. **`gh`** (GitHub CLI, logged in with
`gh auth login`) is needed once you dispatch work in step 4, since the agents push branches
and open PRs with it. Telegram, Linear and pm2 are optional follow-ups.

> **macOS works.** chela is developed on Linux but runs on macOS (a POSIX
> `/proc` fallback covers the process/window facts). If TUI glyphs render as
> boxes (tofu), install a symbol font (see [the fonts note below](#fonts-macos)).

## 1. Install

```bash
uv tool install "chelamux[dashboard] @ git+https://github.com/Devail1/chelamux"
tmux new-session -d -s chela -n researcher     # a window = an agent
chela run > ~/chela-run.log 2>&1 &             # the daemon (the engine)
chela dashboard                                # the wall: http://127.0.0.1:5001
```

That is the one install path, and the wall is up when the last command prints its URL.
[`scripts/check_fresh_install.sh`](../scripts/check_fresh_install.sh) runs exactly this
block on a clean Ubuntu container (only the prerequisites above, a stub `claude`) and
checks that the dashboard answers and `chela doctor` passes; a test keeps the two in sync.
If your shell does not find `chela`, run `uv tool update-shell` and open a new shell.

For a first look without installing anything, `uvx chelamux dashboard` runs the wall from
PyPI (every `chela …` command works as `uvx chelamux …`). Install as above once you want
the daemon running.

- **tmux windows are agents.** The window name is the agent's short, stable key, which
  `chela peek`/`msg` and the Telegram bindings use. The session is named `chela` by
  default (override with `CHELA_TMUX_SESSION`). Windows chela opens for you (the
  dashboard launcher, Telegram `/new`, `chela spawn <dir>`) are named after their folder.
  How window names line up with Remote Control on claude.ai is described in
  [DASHBOARD.md](DASHBOARD.md#window-names-and-remote-control).
- **`chela run` is the engine.** Without it schedules don't fire, dispatched TODOs never
  become PRs, and completion never wakes the orchestrator. For a long-lived setup run it
  under a process manager ([`examples/ecosystem.config.js`](../examples/ecosystem.config.js)
  is a pm2 example); for a first look, the background job above is fine.
- **The Telegram bridge** is an extra: `uv tool install --force
  "chelamux[dashboard,telegram]"` installs the latest tagged release from PyPI. To stay on
  the default branch like the block above, use `uv tool install --force
  "chelamux[dashboard,telegram] @ git+https://github.com/Devail1/chelamux"`.

Then, **inside Claude Code**, add the hooks plugin. It powers blocked-agent handling,
answering from your phone, and the merge gate that stops an agent from running
`gh pr merge` itself ([HOOKS.md](HOOKS.md)):

```
/plugin marketplace add Devail1/chelamux
/plugin install chela@chela
```

Configuration is optional: chela runs on built-in defaults. When you want to change
something, copy [`examples/chela.env`](../examples/chela.env) to `~/.chela/chela.env` and
edit it ([CONFIG.md](CONFIG.md)).

## 2. Authenticate Claude once

chela never handles credentials; it drives the `claude` CLI inside your tmux
windows, so every agent reuses one cached login:

```bash
claude          # then /login   (or: claude setup-token, for a headless token)
```

The whole fleet runs as **one Claude account** and shares its rate limits.

## 3. Check what chela sees

```bash
chela status                   # lists the windows chela can drive
chela doctor                   # exits 1 only when a REQUIRED check fails
```

`chela doctor` marks the integrations a first run does not need (Telegram, Linear) as
*optional*: their absence reads "not configured (optional)", and even an error from one is
printed without failing the run.

## 4. Your first dispatched agent

This is the headline feature: a task becomes a git worktree → an agent that implements
it → a PR, reviewed by an adversarial judge. The queue can be a markdown `TODO.md` (shown
here, nothing to sign up for), GitHub issues, or a Linear team. For Linear, follow
[LINEAR_TRACKER.md](LINEAR_TRACKER.md) instead of copying `TODO.md`.

```bash
# In a repo you own (with a GitHub remote), seed the two files from the examples:
for f in WORKFLOW.md TODO.md; do
  curl -fsSLo "$f" "https://raw.githubusercontent.com/Devail1/chelamux/main/examples/$f"
done

# Point the daemon at that workflow (or set CHELA_DISPATCH_WORKFLOWS):
chela dispatch "$PWD/WORKFLOW.md" --once     # one pass; drop --once to poll
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
agent, and pushes and opens the PR for it. Watch it on the wall
(<http://127.0.0.1:5001>) or with `chela dispatch-runs`. **Auto-merge is off by default.** When the judge passes the PR, merge it
with `chela merge <run>`; the dispatcher then strikes the `- [ ]` line on your base
branch. What to do when a run stops for a human (retry, reopen, override, close) is in
[RUNS.md](RUNS.md).

> **Guard discipline is the contract.** Every guard must go **RED** when the thing
> it protects is broken: the judge corrupts each guard in a throwaway worktree and
> blocks any PR whose guard survives its own corruption. See
> [CONTRIBUTING.md](../CONTRIBUTING.md). To see where your own tests stand first, run
> `chela judge sweep --cwd /path/to/your/repo --base origin/main` on a branch: it lists
> every changed check no test catches ([RUNS.md](RUNS.md#the-sweep-on-its-own)).

> **Prefer an agent set it up?** Copy [`skills/chela-setup`](../skills/chela-setup/SKILL.md)
> into `~/.claude/skills/` and a Claude Code agent will install chela and seed a
> starter `WORKFLOW.md` + `TODO.md` for the current repo.

## 5. Optional polish

- **Exact context / rate-limit numbers**: `chela install-statusline --write`
  installs the statusLine hook the dashboard reads. This is the default and works on
  every Claude Code chela supports. On Claude Code 2.1.301 or later you can instead
  opt in to the [`chela-hooks` Mod](../mods/chela-hooks/README.md), which feeds the same
  cache and draws chela's status line without editing `settings.json`.
- **Phone push when an agent blocks**: set `CHELA_NOTIFY_URL` (ntfy / Telegram /
  webhook); see [Needs-input notifications](https://chela.pages.dev/docs#needs-input).
- **Remote access**: the dashboard binds `127.0.0.1` with no built-in auth by
  design. Put it behind Tailscale or an SSH tunnel; the tailnet is the trust
  boundary. See [Remote access & security](https://chela.pages.dev/docs#security).

## Recommended skills

chela ships only the skills that are specific to it ([`skills/`](../skills/README.md)). For
planning and everyday engineering habits, install Matt Pocock's
[`mattpocock/skills`](https://github.com/mattpocock/skills) (MIT) as a Claude Code plugin
(follow the install steps in its README). It adds no hooks, so it does not interfere with
chela's merge gate. The skills that fit chela's flow:

- **`grill-me`** and **`to-spec`** before you file a task: settle the open questions, then
  turn the answers into a spec that becomes the task's brief.
- **`tdd`** while an agent implements: the judge blocks any guard that survives its own
  corruption, so tests written first pay off.
- **`diagnosing-bugs`** when a run fails for a reason nobody understands yet.
- **`code-review`** for a second read of a PR before you merge it.

chela itself covers the rest of the loop: a git worktree per task, dispatch, the merge gate
(`chela merge`), and verification by the judge. You do not need a separate skill pack for
those.

## Fonts (macOS)

The TUI markers (`⏺ ❌ ✅`, the working spinner) and Nerd icons need a font that
covers those codepoints. If any surface shows boxes, install a symbol font such
as [Symbola](https://dn-works.com/ufas/) and let the surface's font-fallback pick
it up. chela wires a coverage fallback on each render surface (dashboard, telegram
`/screenshot`, collab viewer), so a missing glyph means the underlying font stack
lacks it, not chela.

## Where to go next

- [README](../README.md): what's new, and the full documentation map
- [docs/RUNS.md](RUNS.md): a dispatched run from claim to merge, and every command on the way
- [docs/DASHBOARD.md](DASHBOARD.md): the Wall, the sidebar, the Work board and the Usage view
- [docs/CONFIG.md](CONFIG.md): the environment, precedence, and PM2 migration
- [docs/HOOKS.md](HOOKS.md) · [docs/EVENTS.md](EVENTS.md): how the plugin and event log work
- [WORKFLOW.md](../WORKFLOW.md): the dispatch prompt agents run against
- [CONTRIBUTING.md](../CONTRIBUTING.md): the dev loop and guard discipline
