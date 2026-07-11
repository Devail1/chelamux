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
| **chela-setup** | Install chela and wire its work-item dispatcher into a git repo — scaffold a starter `WORKFLOW.md` + `TODO.md` so each `- [ ] task` becomes an agent → PR. Use to onboard a repo to chela. |

## Agent workflow

| Skill | What it does |
|-------|--------------|
| **handoff** | Generate a structured handoff document so a future Claude session can resume a workstream cold — no shared context needed. Core to multi-session orchestration. |
| **blindspot-pass** | Surface the unknowns *before* doing the work: explore the territory, restate the plan, and report the questions you didn't know to ask. |
| **implementation-plan** | Produce an implementation plan ordered by likelihood-of-change / blast-radius, not chronology — load-bearing decisions first. |
| **interview-me** | Elicit requirements one question at a time, highest-impact first, then emit a paste-ready decision record. |

## Credits

`blindspot-pass`, `implementation-plan`, and `interview-me` are derived from Thariq Shihipar's **"A Field Guide to Fable: Finding Your Unknowns."** Full credit to the original work; these are adaptations packaged as Claude Code skills.

`handoff` and `chela-setup` are original to this project.
