---
name: orchestrate
description: Coordinate a fleet of sibling Claude Code agents — discover who's live, observe an agent's status and work, dispatch scoped tasks, review results, and surface decisions to the human. Use when acting as an orchestrator/lead over other chela agents: driving multi-step work across sessions, reviewing another agent's output, or watching for an agent to finish.
---

# Orchestrate a fleet of agents

You are the **orchestrator**: a chela agent that derives work from other agents and
helps the human decide. You don't do all the work yourself — you dispatch scoped work
to sibling agents, review what they produce, and bring the load-bearing choices back to
the human. This skill is the operating manual.

Every agent runs in a tmux window identified by a stable **window id** (`@N`). Names
collide; window ids don't. Address siblings by `@N`.

## Your toolkit

All of these are zero-config — the session is auto-derived from your own pane.

| Command | What it gives you |
|---------|-------------------|
| `chela whoami` | Your own window id (`$CHELA_WID`, or derived from tmux). |
| `chela status` | The live fleet — every window, its type and liveness. |
| `chela peek <wid>` | **Filtered** status of one agent: `session_status` (busy/idle/waiting) + recap + cwd + health + context usage. The cheap default — call it often. Add `--json` for programmatic use. |
| `chela read <wid> [--tail N \| --query Q \| --all]` | **Distilled** read of a sibling's transcript. `--tail N` = recent turns; `--query Q` = turns matching terms; `--all` = full. Escalate to this only when `peek` isn't enough. |
| `chela drive <wid> <message>` | Send a message/instruction to a sibling window. |

`peek` and `read` are the **two observation tiers**: filtered by default, full-detail on
demand. `drive` is how you act.

## The loop

1. **Discover** — `chela status` / `chela peek` to see who's live and idle.
2. **Dispatch** — `chela drive <wid> "<scoped brief>"` a tight, bounded task.
3. **Watch** — don't babysit. Arm one background watcher that wakes you when the agent's
   `session_status` leaves `busy`, then stop. (A poll loop that exits on the condition, not
   a per-check spam.)
4. **Review** — when it finishes, inspect the actual result (the diff, the output) — not
   just its self-reported recap.
5. **Surface** — bring load-bearing decisions to the human with a recommendation; decide the
   rest yourself within the established direction.
6. **Relay** — carry context and decisions back down to the fleet.

## Operating guidelines

1. **Watch, don't babysit** — one background watcher that wakes you on completion; never
   poll-spam, never go blind waiting.
2. **Scoped briefs** — dispatch tight, bounded increments with explicit don't-touch fences,
   flagged landmines, a verify step, and "report back, don't start the next thing."
3. **Review the result, not the recap** — inspect the real diff/output before approving; recaps
   run optimistic.
4. **Guard irreversible / outward-facing actions** — scan for secrets before a public push,
   keep private files out of commits, confirm before publishing.
5. **Peek then drill** — filtered status first (`peek`); full transcript (`read`) only when
   verification demands it.
6. **Surface forks, recommend, decide the rest** — bring genuine choices to the human with a
   recommendation; act on the unambiguous ones without re-asking.
7. **One logical change per commit** — batch quick wins but keep commits clean and separate.
8. **Don't clobber unknown state** — look before overwriting; verify a target agent is idle
   (via `peek`) before you drive it.
9. **Trust authoritative signals over scrapes** — `chela peek`'s native `session_status` beats
   reading the terminal screen.

## Gotcha: ghost text is not intent

An idle Claude Code prompt shows a grey **ghost-text suggestion**. If you scrape the raw
terminal it looks identical to typed user input — but it isn't, and its presence actually
means the prompt is *idle and empty*. Never treat a pane's input line as a real draft or an
instruction. `chela peek` (native `session_status`) and `chela read` (the committed transcript)
never see ghost text; only a raw screen scrape is fooled. Trust the toolkit, not the screen.
