# chela — the hooks plugin

Feeds a [chela](https://github.com/Devail1/chelamux) fleet's **event log** from Claude
Code hooks. Every tool call, prompt, permission request and session end is POSTed to the
chela daemon you are already running, and lands as a typed, durable record.

The point is *timing*: Claude Code writes an interactive tool's `tool_use` to the
transcript only when it is **answered**, so a pending `AskUserQuestion` or permission gate
is invisible to anything reading the transcript. A hook fires **before** the fact — so the
question reaches the log, with every option's label and description, while the agent is
still waiting on it.

## Observe-only

This plugin **watches**. It never answers a prompt, approves a tool, or returns a
permission decision — the receiving endpoint replies with an empty object, deliberately.
It cannot make a decision on your behalf.

## What it needs

A chela dashboard/daemon listening on **`127.0.0.1:5001`** (chela's default). Running it
on another port? A hook URL is a literal — Claude Code does not expand environment
variables in it — so render your own copy of this plugin with the right port baked in:

```bash
chela plugin --dir ~/.chela/plugin      # then: claude --plugin-dir ~/.chela/plugin
```

`chela plugin` takes the port from the **running** dashboard (which publishes what it
actually bound to `$CHELA_DIR/dashboard.port`), not from whatever this shell's environment
says — the two disagreeing is how the manifest once came to name a port nobody served, and
a hook that cannot connect fails open, silently. `chela doctor` re-checks it afterwards;
re-render whenever the dashboard moves.

If the daemon is down, the hooks simply fail open: Claude Code logs a warning, the event
is lost, and **your agent carries on**. It will never wedge a session.

MIT.
