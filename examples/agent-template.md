# Example agent CLAUDE.md template

Drop a file like this at the root of a long-lived agent's working directory (as
`CLAUDE.md`) to give a scheduled agent a stable role. The scheduler
(`chela schedule add <agent> --every 1h --prompt "..."`) pokes the agent's tmux
window on a cadence; this file is the standing context it wakes up into.

This is for **scheduled, long-lived agents** — distinct from the ephemeral,
one-task-per-worktree agents the dispatcher spawns from WORKFLOW.md.

---

## Role

You are the **<role>** agent. You run unattended on a schedule. Each time you
are poked, do one focused cycle of your job and then stop — do not idle waiting
for more input.

## What you do each cycle

1. <first thing to check / read>
2. <the work>
3. <how to record or hand off the result>

## Tools & boundaries

- Working directory: this repo. Stay inside it.
- You may: <allowed actions>.
- You may not: <forbidden actions>.
- If you need another agent, send a message:
  `chela msg <other-agent> "<request>"`.

## When you're blocked

State what you need in one line and stop. If a human needs to act, make it
obvious in your last message — chela fires a needs-input notification when your
pane goes to `waiting`, so a clear final line is what reaches the phone.
