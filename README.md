<p align="center">
  <img src="chela/dashboard/static/img/banner.svg" alt="chela" width="560">
</p>

<p align="center">
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-AGPL%20v3-blue.svg" alt="License: AGPL v3"></a>
  <img src="https://img.shields.io/badge/python-3.11%2B-blue.svg" alt="Python 3.11+">
  <img src="https://img.shields.io/badge/deps-uv-261230.svg" alt="uv-managed">
  <img src="https://img.shields.io/badge/tmux-native-1bb91f.svg" alt="tmux-native">
</p>

# chelamux

**A tiny control plane that puts a fleet of Claude Code agents to work — unattended.**

Most tmux + Claude Code tools help you *talk to and supervise* agents. chela is for
**handing them work and walking away**. It runs as a small daemon over a single tmux
session and does three things:

- **Schedules** long-lived agents — poke an agent's pane on an interval or cron
  (`every 1h`, `0 */8 * * *`, a one-shot timestamp).
- **Dispatches** work — turn a markdown `TODO.md` (or GitHub issues) into one
  **git worktree per task**, spawn an agent in it, and let it **open a PR** — which an
  adversarial judge re-reviews before it reaches you.
- **Closes the loop** — an orchestrator is just another agent, and it can only act when
  something messages it, so an agent *finishing* is invisible to it. chela pushes
  completions, blocks and deaths back into the orchestrator's own session.

You watch them however you already watch tmux — `tmux attach`, [Mosh](https://mosh.org/),
or the **live terminal-wall dashboard**.

<table align="center">
  <tr>
    <td align="center" valign="top">
      <img src="docs/img/chela-demo-desktop.gif" alt="chela on desktop — the live terminal wall plus the Dispatch board, Kanban and Schedules" width="600"><br>
      <sub>Desktop — the live wall, Dispatch, Kanban &amp; Schedules</sub>
    </td>
    <td align="center" valign="top">
      <img src="docs/img/chela-demo-mobile.gif" alt="chela on a phone — single-pane wall, agent pill switcher and keybar" width="150"><br>
      <sub>Phone — single-pane, pill switcher, keybar</sub>
    </td>
  </tr>
</table>

<p align="center"><strong><a href="https://chela.pages.dev/docs">📖 Full documentation →</a></strong></p>

---

## Install

Requires Python ≥ 3.11, `tmux`, `git`, the `claude` CLI on `PATH` (plus `gh` for the
dispatcher's PR flow). chela uses [`uv`](https://docs.astral.sh/uv/).

```bash
git clone https://github.com/Devail1/chelamux && cd chelamux
uv sync --all-extras          # core + dashboard + Telegram bridge
uv run chela status
```

The core has two small deps; the **dashboard + live terminal wall** and the **Telegram
bridge** are optional extras. ⚠️ `uv sync --extra X` *replaces* the environment — it drops
any extra you don't name, so ask for every one you want in a single command.

**Authenticate Claude once.** chela never touches credentials — it drives the `claude` CLI
inside your tmux windows, and every agent reuses the cached `~/.claude` login. The whole
fleet therefore runs as **one Claude account** and shares its 5h / 7d rate limits.

**Recommended:** add the hooks plugin — one line inside Claude Code
(`/plugin marketplace add Devail1/chelamux`). It is what makes blocked-agent handling,
answering from your phone, and the live Feed work well.

## Quickstart

```bash
# 1. A tmux session whose windows are your agents (window name = agent name)
tmux new-session -d -s chela -n researcher

# 2. See what chela can see
uv run chela status

# 3. Schedule one
uv run chela schedule add researcher --every 1h --prompt "Run your research cycle."

# 4. Start the daemon — NOT optional (see below)
uv run chela run

# 5. Dispatch a TODO list into pull requests
uv run chela dispatch /path/to/repo/WORKFLOW.md
```

> **`chela run` is the engine, not a helper.** Everything autonomous lives in its loop —
> the scheduler, the dispatcher, the needs-input scan, the decisions inbox. Kill it and
> nothing *looks* broken: the dashboard still works and agents still run, the fleet just
> quietly stops doing anything by itself. If schedules seem stuck, check it is alive first.

New here? [**docs/GETTING_STARTED.md**](docs/GETTING_STARTED.md) is a ~10-minute
clone-to-first-dispatched-agent walkthrough. Prefer to have an agent set it up? Copy
[`skills/chela-setup`](skills/chela-setup/SKILL.md) into `~/.claude/skills/`.

## Documentation

Everything lives at **[chela.pages.dev/docs](https://chela.pages.dev/docs)**:

| | |
|---|---|
| [Quickstart](https://chela.pages.dev/docs#quickstart) · [Concepts](https://chela.pages.dev/docs#concepts) | tmux as the source of truth, one window per agent |
| [Scheduling](https://chela.pages.dev/docs#scheduling) | intervals, cron, standing context |
| [Dispatching work](https://chela.pages.dev/docs#dispatch) | `TODO.md` → worktree → agent → PR, and the judge |
| [The orchestration loop](https://chela.pages.dev/docs#orchestration) | the decisions inbox, and the rules that make writing into a live session safe |
| [Agent rooms](https://chela.pages.dev/docs#rooms) | agents that can actually talk to each other |
| [Agent autonomy](https://chela.pages.dev/docs#autonomy) | permission modes, and the resource-isolation gap |
| [Dashboard &amp; the wall](https://chela.pages.dev/docs#dashboard) | live terminals, context bars, rate-limit pills |
| [Collaborative terminals](https://chela.pages.dev/docs#collab) | share a session, end-to-end encrypted |
| [Command reference](https://chela.pages.dev/docs#cli) · [Configuration](https://chela.pages.dev/docs#config) | every command, every env var |
| [Remote access &amp; security](https://chela.pages.dev/docs#security) | the loopback guard and how to front it |
| [How it works](https://chela.pages.dev/docs#internals) | internals, the HTTP API, and why not a structured agent protocol |

In-repo references: [HOOKS.md](docs/HOOKS.md) · [EVENTS.md](docs/EVENTS.md) ·
[CONFIG.md](docs/CONFIG.md) · [RESOURCE_ISOLATION.md](docs/RESOURCE_ISOLATION.md) ·
[AGENT_IDENTITY.md](docs/AGENT_IDENTITY.md) · [ESCALATION_CONTRACT.md](docs/ESCALATION_CONTRACT.md) ·
[design notes](docs/README.md#design-records--context-not-instructions).

## Status

Early. Core (scheduler + dispatcher + messaging) is solid and tested. The dashboard and
live terminal wall are first-class but ship as a separate install to keep the core lean
for headless use. The wall serves **writable shells**, so it is on by default but
**loopback-guarded** — a non-loopback bind refuses it unless you set
`CHELA_TERMINALS_EXPOSE=true`. Front it with a tailnet or an SSH tunnel.

[Changelog](CHANGELOG.md) · [Contributing](CONTRIBUTING.md) · [Security policy](SECURITY.md) ·
[Code of Conduct](CODE_OF_CONDUCT.md)

## Credits

The work-item dispatcher is an adaptation of OpenAI's **Symphony** pattern (task-list →
isolated git worktree → autonomous agent → PR) — chela does not claim novelty for that shape.

## License

Copyright © 2026 Liav Edry. **GNU Affero General Public License v3.0 or later** — see
[LICENSE](LICENSE).

AGPL rather than GPL because chela is a long-running daemon with a web dashboard: plain
GPL obligations trigger on *distribution*, so running a modified chela as a hosted service
would carry none. In plain terms — use it freely, privately or commercially, and modify it
as you like; if you distribute a modified version **or run one as a network service**,
publish your source under the same licence.

Versions up to and including **0.3.0 were released under MIT** and remain available under
it. Third-party components keep their own licences — see [NOTICE](NOTICE).
