<p align="center">
  <img src="chela/dashboard/static/img/banner.svg" alt="chela" width="560">
</p>

<table align="center">
  <tr>
    <td align="center" valign="top">
      <img src="docs/img/chela-demo-desktop.gif" alt="chela on desktop — the live terminal Wall with one agent in each state beside the sidebar (folder groups, Needs you, the orchestrator badge); hold Alt to number the rows, archive a finished session and unarchive it, then the Work view's board, runs and schedules, and Settings opened with Ctrl+," width="600"><br>
      <sub>Desktop — the Wall, Work (board, runs, schedules) &amp; Settings on <kbd>Ctrl</kbd>+<kbd>,</kbd></sub>
    </td>
    <td align="center" valign="top">
      <img src="docs/img/chela-demo-mobile.gif" alt="chela on a phone — single-pane wall, agent pill switcher, the + New session menu and the keybar" width="150"><br>
      <sub>Phone — pill switcher, “+” New session, keybar</sub>
    </td>
  </tr>
</table>

<p align="center"><sub>Recorded from a synthetic demo fleet (<a href="scripts/demo/"><code>scripts/demo/</code></a>) — re-record with <code>scripts/demo/record.sh</code>.</sub></p>

<p align="center">
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-AGPL%20v3-blue.svg" alt="License: AGPL v3"></a>
  <img src="https://img.shields.io/badge/python-3.11%2B-blue.svg" alt="Python 3.11+">
  <img src="https://img.shields.io/badge/deps-uv-261230.svg" alt="uv-managed">
  <img src="https://img.shields.io/badge/tmux-native-1bb91f.svg" alt="tmux-native">
</p>

# chelamux

**Run Claude Code agents unattended — chela only merges a PR once it has proved the tests
catch broken code.**

- **Tracker-driven dispatch.** A ready task in Linear, GitHub issues or a `TODO.md` becomes
  one git worktree, one agent and one PR.
- **A mutation-testing judge.** Before a merge, the judge corrupts the code the PR guards and
  checks the tests turn red; its verdict is bound to the PR's head commit, so a new push
  voids it.
- **A merge gate with one-tap approval.** `chela merge` refuses without a clean verdict on
  the current head and green CI; `chela merge --override --reason …` asks you instead, and
  one Telegram tap (or the dashboard) completes it — audited.

And you can watch it all live: a terminal-wall dashboard, a Telegram bridge to every
session, and schedules that poke long-lived agents (`--every 1h`, cron, or once).

> **Trust note.** Dispatched agents run **unsandboxed by default** — as your user, with your
> tools and network. A measured sandbox exists and is **opt-in per workflow**
> (`agent.sandbox: true` in `WORKFLOW.md`); see
> [docs/TRUST_AND_APPROVAL_POSTURE.md](docs/TRUST_AND_APPROVAL_POSTURE.md).

## Install

Requires Linux, WSL or macOS · Python ≥ 3.11 · `tmux` · `git` · [`uv`](https://docs.astral.sh/uv/) ·
the `claude` CLI · `gh` (for the PR flow).

```bash
git clone https://github.com/Devail1/chelamux && cd chelamux
uv sync --all-extras          # core + dashboard + Telegram bridge, in one command
uv run chela status           # what chela sees
```

Full walkthrough, clone to a first agent-opened PR:
[**docs/GETTING_STARTED.md**](docs/GETTING_STARTED.md).

chela never touches credentials: it drives the `claude` CLI in your tmux windows (run
`claude` and `/login` once), so the whole fleet runs as **one Claude account** and shares
its 5h / 7d limits. Inside Claude Code, add the hooks plugin too —
`/plugin marketplace add Devail1/chelamux`, then `/plugin install chela@chela` — it powers
blocked-agent handling, answering from your phone, and the merge gate
([docs/HOOKS.md](docs/HOOKS.md)).

## First run

```bash
tmux new-session -d -s chela -n researcher        # a window = an agent
uv run chela status                               # what chela sees
uv run chela schedule add researcher --every 1h --prompt "Run your research cycle."
uv run chela run                                  # the daemon — leave it running
uv run chela dashboard                            # http://127.0.0.1:5001
```

> **`chela run` is the engine, not a helper.** The scheduler, the dispatcher, the
> needs-input scan and the decisions inbox all live in its loop. Kill it and nothing *looks*
> broken — the dashboard still works and agents still run — but the fleet stops doing
> anything by itself.

To dispatch work, follow [**docs/GETTING_STARTED.md**](docs/GETTING_STARTED.md) (about
10 minutes, clone to a first agent-opened PR). Prefer an agent to set it up? Copy
[`skills/chela-setup`](skills/chela-setup/SKILL.md) into `~/.claude/skills/`.

## What's new in 0.15

- **Linear is a first-class queue.** `tracker: kind: linear` reads a team's issues (ready
  state `Todo`, `risk:high|normal|low` labels), the Work board mirrors the team's workflow
  states, and "New task" creates the issue — [docs/LINEAR_TRACKER.md](docs/LINEAR_TRACKER.md).
- **Override is one tap.** `chela merge --override --reason …` files a request and returns;
  approving it (a Telegram button, the dashboard, or `chela merge-approve`) completes the
  merge after re-checking head, CI and mergeability — [docs/RUNS.md](docs/RUNS.md).
- **A closed task can come back.** `chela close <run> --requeue` re-dispatches it on a fresh
  branch, and `chela doctor` plus a Work-board **Requeue** button flag a closed run that
  stalls a ready task.
- **A sidebar like the Claude desktop app's** — folder groups, one row per dispatched run
  with live judge progress, a VIEW menu, an orchestrator badge, and Archive that closes a
  session (Unarchive resumes it) — [docs/DASHBOARD.md](docs/DASHBOARD.md).
- **Usage view** (in Settings, beside spend): 5h / 7d limit bars, burn rate, and the top
  token consumers across every transcript.
- **Window names match Remote Control.** A new window is named after its folder and launches
  with `--remote-control <that name>`; a dashboard rename types `/rename` into the session.

Full list: [CHANGELOG.md](CHANGELOG.md).

## Documentation

**[chela.pages.dev/docs](https://chela.pages.dev/docs)** is the user guide — concepts,
scheduling, dispatching, the orchestration loop, agent rooms, permission modes, the
dashboard, collaborative terminals, the command reference and configuration.

In-repo references ([index](docs/README.md)): [GETTING_STARTED.md](docs/GETTING_STARTED.md) ·
[RUNS.md](docs/RUNS.md) · [DASHBOARD.md](docs/DASHBOARD.md) · [CONFIG.md](docs/CONFIG.md) ·
[LINEAR_TRACKER.md](docs/LINEAR_TRACKER.md) · [RISK_LEVELS.md](docs/RISK_LEVELS.md) ·
[HOOKS.md](docs/HOOKS.md) · [EVENTS.md](docs/EVENTS.md) ·
[TRUST_AND_APPROVAL_POSTURE.md](docs/TRUST_AND_APPROVAL_POSTURE.md) ·
[ESCALATION_CONTRACT.md](docs/ESCALATION_CONTRACT.md) ·
[RESOURCE_ISOLATION.md](docs/RESOURCE_ISOLATION.md).

## Status

Early. Core (scheduler + dispatcher + messaging) is solid and tested; CI runs the suite on
Python 3.11 and 3.13. The dashboard and live terminal wall are first-class but ship as an
optional extra, so a headless install can skip them (`uv sync` alone is core only). The
wall serves **writable shells**, so it is on by default but
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
