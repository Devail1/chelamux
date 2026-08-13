# Upstream feature request — per-message timestamps in the TUI transcript

Paste into <https://github.com/anthropics/claude-code/issues>. Written 2026-08-13 after
`docs/SPIKE_LIVE_TERMINAL_TIMESTAMPS.md` established this cannot be built downstream.

---

**Title:** Optional per-message timestamps in the terminal transcript

### What I want

A setting that renders a timestamp against each message in the transcript — my prompts and
Claude's replies — something like:

```
[14:52]  ▸ should we run the shadow rebalance now?
[14:53]  ● Checking the bar-guard behaviour before asserting it.
[15:07]  ● Done — 27 passed.
```

A `settings.json` flag would be ideal, e.g. `"transcriptTimestamps": "time" | "elapsed" | "off"`,
default `"off"` so nothing changes for anyone who doesn't want it.

### Why

I run several long-lived agent sessions in parallel and step away from them for stretches at
a time. When I come back to a session, the transcript tells me *what* happened in perfect
detail and gives me no way at all to tell *when*. Concretely:

- I can't tell whether a run has been stuck for two minutes or forty.
- I can't tell whether the reply above my last message came before or after something I
  saw elsewhere.
- When several sessions are working at once, I can't reconstruct the order events happened
  in across them.

Every one of those is a "when" question, and the transcript is the only place I'm reading.

### Why it can't be done outside Claude Code

I maintain a tmux-based multi-agent orchestrator that mirrors agent panes into a browser
wall, and I spent a spike establishing that this is not solvable downstream. Three
independent blockers, each checked against a running session rather than read from source:

1. **The hook channel has no write path into the pty.** Plugin hooks are HTTP callbacks
   fired synchronously inside the CLI, out-of-band from the terminal stream. A hook knows
   the timestamp and cannot put a byte on screen.
2. **The terminal frontend is opaque.** My wall proxies `ttyd`, whose entire frontend ships
   as one minified webpack closure with nothing attached to `window` — there's no
   `Terminal` handle to reach — and it renders through WebGL to a canvas, so there are no
   DOM nodes to decorate either.
3. **Even with instance access, there's no event→row mapping.** xterm.js's decoration API
   needs a buffer marker: a specific row. Claude Code's TUI is a live-redrawing renderer
   over the raw pty — spinners, streamed tokens, collapsing tool blocks — so an event at
   time *T* cannot say which row that message will occupy once the pane reflows and
   scrolls.

Blocker 3 is the one that matters: it isn't about plugins or about `ttyd`. Only the
renderer that owns the layout can attach a timestamp to a message, because only it knows
where the message ended up. That's Claude Code.

### Notes

- The data already exists — session transcript JSONL carries a per-entry ISO `timestamp`.
  Tooling that reads the JSONL after the fact can and does show times; it's only the live
  view that can't.
- The docs confirm there's no current setting for this (the settings reference lists
  `outputStyle`, `axScreenReader`, `autoScrollEnabled`, `editorMode` and similar, none of
  them timestamp-related), which is what prompted filing rather than configuring.
- `axScreenReader` suggests the renderer already supports alternate line treatments, so a
  timestamp gutter may fit an existing seam.
- Elapsed-since-previous would be as useful as wall-clock for the stuck-run case, possibly
  more.
