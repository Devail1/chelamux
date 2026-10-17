# chelamux skills

[Claude Code skills](https://docs.claude.com/en/docs/claude-code/skills) that ship with chelamux. Only what is specific to chela lives here: onboarding, orchestration, chela's handoff layout and Telegram. For general planning and engineering habits, see [Recommended skills](../docs/GETTING_STARTED.md#recommended-skills).

## Install

Copy any skill directory into your skills folder:

```bash
# user-level (available everywhere)
cp -r skills/handoff ~/.claude/skills/

# or project-level
cp -r skills/handoff <your-project>/.claude/skills/
```

Then invoke it in Claude Code with `/handoff`, `/orchestrate`, etc.

## Setup

| Skill | What it does |
|-------|--------------|
| **chela-setup** | Install chela (extras, `chela.env`, the hooks plugin, the daemon) and wire its work-item dispatcher into a git repo, then seed `WORKFLOW.md` + `TODO.md` so each `- [ ] task` becomes an agent → a judged PR. Use to onboard a repo to chela. |
| **telegram-setup** | Wire Telegram for chela: a bot from @BotFather, a private forum group with Topics, the chat id, the secrets in `~/.chela/secrets.env`, and the `chela telegram` bridge (one topic per agent window, two-way). |

## Orchestration

| Skill | What it does |
|-------|--------------|
| **orchestrate** | Act as the orchestrator over a fleet of agents and the dispatcher: observe (`peek`/`read`), file tracker tasks with their blocking edges, review PRs against the judge's verdict, merge only through `chela merge`, deploy, and surface decisions to the human. The operating manual for chela's agent-facing toolkit. |
| **handoff** | Generate a structured handoff document in chela's memory layout so a future Claude session can resume a workstream cold, with no shared context needed. Core to multi-session orchestration. |

## Communication

| Skill | What it does |
|-------|--------------|
| **telegram-send** | Send a message or file to Telegram from any agent (Bot API, stdlib-only, env-configured). Push a result, chart, log, or a surfaced decision to your phone. Composes with `orchestrate` so the fleet can reach you proactively. |

## Credits

All five skills are original to this project. The blocking-edges rule in `orchestrate` is adapted from the `to-tickets` skill in Matt Pocock's [`mattpocock/skills`](https://github.com/mattpocock/skills) (MIT).
