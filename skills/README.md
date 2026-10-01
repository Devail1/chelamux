# chelamux skills

[Claude Code skills](https://docs.claude.com/en/docs/claude-code/skills) that ship with chelamux — onboarding plus a small curated set of agent-driven workflow habits (planning, elicitation, and session-continuity) that make a multi-agent setup productive.

## Install

Copy any skill directory into your skills folder:

```bash
# user-level (available everywhere)
cp -r skills/handoff ~/.claude/skills/

# or project-level
cp -r skills/handoff <your-project>/.claude/skills/
```

Then invoke it in Claude Code with `/handoff`, `/blindspot-pass`, etc.

## Setup

| Skill | What it does |
|-------|--------------|
| **chela-setup** | Install chela (extras, `chela.env`, the hooks plugin, the daemon) and wire its work-item dispatcher into a git repo — seed `WORKFLOW.md` + `TODO.md` so each `- [ ] task` becomes an agent → a judged PR. Use to onboard a repo to chela. |
| **telegram-setup** | Wire Telegram for chela — a bot from @BotFather, a private forum group with Topics, the chat id, the secrets in `~/.chela/secrets.env`, and the `chela telegram` bridge (one topic per agent window, two-way). |

## Orchestration

| Skill | What it does |
|-------|--------------|
| **orchestrate** | Act as the orchestrator over a fleet of agents and the dispatcher — observe (`peek`/`read`), queue tracker tasks, review PRs against the judge's verdict, merge only through `chela merge`, deploy, and surface decisions to the human. The operating manual for chela's agent-facing toolkit. |

## Agent workflow

| Skill | What it does |
|-------|--------------|
| **handoff** | Generate a structured handoff document so a future Claude session can resume a workstream cold — no shared context needed. Core to multi-session orchestration. |
| **blindspot-pass** | Surface the unknowns *before* doing the work: explore the territory, restate the plan, and report the questions you didn't know to ask. |
| **implementation-plan** | Produce an implementation plan ordered by likelihood-of-change / blast-radius, not chronology — load-bearing decisions first. |
| **interview-me** | Elicit requirements one question at a time, highest-impact first, then emit a paste-ready decision record. |

## Communication

| Skill | What it does |
|-------|--------------|
| **telegram-send** | Send a message or file to Telegram from any agent (Bot API, stdlib-only, env-configured) — push a result, chart, log, or a surfaced decision to your phone. Composes with `orchestrate` so the fleet can reach you proactively. |

## Credits

`blindspot-pass`, `implementation-plan`, and `interview-me` are derived from Thariq Shihipar's **"A Field Guide to Fable: Finding Your Unknowns."** Full credit to the original work; these are adaptations packaged as Claude Code skills.

`handoff`, `chela-setup`, `telegram-setup`, `orchestrate`, and `telegram-send` are original to this project.
