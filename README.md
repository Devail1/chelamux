# Chelamux

**A tiny control plane for a fleet of Claude Code agents on tmux.**

Schedule long-lived agents to keep working on a cadence, dispatch ephemeral ones
into isolated git worktrees that open PRs, and watch the whole fleet from a
mission-control wall — all on your Max plan, no API tokens, remote over Tailscale.

> clawmux and ccmux are for *talking to* your agents. Chelamux is for *putting
> them to work.*

It's tmux-native: every agent is a real terminal in a tmux window, so any agent
CLI that runs in a terminal works, and nothing reimplements a session runtime.

## Status

🚧 Early — porting the core in. `chela status` (tmux-native window discovery)
works today; `chela run` (persona scheduler) and `chela dispatch` (work-item
dispatcher) are landing next.

```bash
chela status          # list the agent windows in your tmux session
```

Point it at your session with `CHELA_TMUX_SESSION` (defaults to `chela`).

## Credits

The work-item dispatcher is an adaptation of
[openai/symphony](https://github.com/openai/symphony)'s spec — Claude Code on the
Max plan in place of Codex, a markdown task list in place of Linear. _(Attribution
to be finalized against their LICENSE.)_

## License

MIT
