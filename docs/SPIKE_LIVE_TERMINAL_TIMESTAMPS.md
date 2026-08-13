# Spike: timestamps on messages in the live terminal, via the plugin's hooks

**Question:** can chela stamp individual messages (user prompt / assistant reply) with a
timestamp *inside* the live terminal wall — the same view a browser tile shows — using
only the Claude Code plugin's hooks (`plugin/hooks/hooks.json` → `chela/hooks.py`)?

**Verdict: no.** Not as scoped ("via the hooks"). The hook channel and the terminal-wall
render path are two separate systems with no write edge between them, and the one real
injection point chela already has into that render path cannot reach message boundaries
either. Below is the evidence, and what *is* buildable instead.

## What the live terminal wall actually is

Confirmed against a running tile (`ps aux | grep ttyd`, then `curl` its own page):

- Each wall tile is a same-origin reverse-proxied **`ttyd`** instance
  (`chela/dashboard/app.py`, "Terminal wall: same-origin ttyd reverse proxy" — `term_http`
  forwards `/term/<wid>/*` verbatim to `127.0.0.1:<port>`). `ttyd` attaches its own tmux
  client directly to the agent's pane and streams the pty bytes over its own WebSocket.
- `ttyd`'s entire frontend — including xterm.js — ships as **one minified webpack IIFE**
  inlined in the page (`(()=>{"use strict"; ... (0,e.render)((0,e.h)(t.App,null),
  document.body)})()`). Nothing is attached to `window`: no global `term`, no `Terminal`
  handle, no exported module registry. Verified live: `curl 127.0.0.1:<port>/term/@N/` and
  grep the response — no `src="..."` script tags, no globals, just the closure.
- xterm.js is running with **`CanvasAddon`/`WebglAddon`** (`rendererType:"webgl"` present
  in the bundle) — it paints to a `<canvas>`, not per-character DOM nodes. There is nothing
  a `MutationObserver` could read.

So "the live terminal" is pixels painted by a process chelamux does not own, delivered
through a proxy chelamux does not control the *content* of — only the transport.

## What the hook channel actually is

`chela/hooks.py`'s own module docstring is explicit: *"This module turns a hook POST into
an `event_log` record. It does nothing else: it answers no gate and decides no
permission."* Concretely:

- `plugin/hooks/hooks.json` registers HTTP callbacks (`http://127.0.0.1:5001/hooks/*`)
  that Claude Code's CLI fires **synchronously inside itself**, out-of-band from the pty.
- `chela/event_log.append()` stamps every event with `ts = time.time()` and appends it to
  a JSONL log — a side channel chela reads for the dashboard Feed, Telegram relays, and
  gate-answering. It has no return path into the pty: it can't write a byte to the tmux
  pane, and it isn't asked to.
- The one hook that *does* act (`PermissionRequest` → `chela.gateanswer`) answers Claude
  Code's own JSON response body for that tool call — still not terminal content.

A hook firing tells chela "an event happened, here's `ts`" on a completely separate wire
from whatever `ttyd` is streaming to the browser at that moment.

## The one real injection point — and why it doesn't close the gap

Chelamux *does* inject `<script>` shims into `ttyd`'s served HTML today — paste-image,
Ctrl+V, the palette hotkey, touch-scroll, collab presence (`chela/dashboard/app.py`,
`_TERM_PASTE_SHIM` / `_TERM_PASTE_KEY_SHIM` / `_TERM_PALETTE_KEY_SHIM` and friends,
`term_http`). This is proof a script *can* run in that page. It doesn't help here, because:

- Every existing shim listens to **DOM/keyboard/paste events** on `document` — it never
  touches xterm's buffer, and (per above) there's no handle to touch: the `Terminal`
  instance lives inside the anonymous webpack closure.
- Even granting instance access (e.g. by vendoring/patching `ttyd`'s bundle instead of
  using the system binary), xterm.js's line-decoration API (`registerDecoration`,
  the mechanism VS Code's terminal actually uses for inline command timestamps) needs a
  **buffer marker** — a specific row. There is no reliable hook→row mapping: Claude
  Code's TUI is a live-redrawing renderer (spinners, streamed tokens, collapsed tool
  blocks) over the raw pty, not an append-only log. A `UserPromptSubmit` firing at `ts=T`
  doesn't say which on-screen row that prompt will end up occupying once the pane
  reflows, and that row keeps moving as output streams and the view scrolls.

## The closest thing to an in-pane surface (rejected)

tmux itself can label a pane from the *server* side — `tmux select-pane -t <wid> -T
"<text>"` (pane title) plus `pane-border-status`/`pane-border-format`, refreshed on every
hook. Chela's own hook handler could plausibly run that. But it doesn't answer the
question asked:

- It's **one line per pane** (last event only), not a per-message stamp in the transcript.
- `scripts/agent-terminals.sh` explicitly runs each `ttyd`'s tmux session with
  `set-option status off` and never turns on pane borders (a single-pane session has none
  by default) — wiring this up means adding visible chrome to every tile, a UX change well
  outside a hooks-feasibility spike.

## What's actually buildable, if the underlying need is "see when things happened"

The hook `ts` is already flowing into a place designed to show exactly this, just not
inside the raw pty mirror:

- **The dashboard Feed** (`chela/dashboard/static/js/feed.js` + `feedmodel.js`) already
  renders every hook-derived event, per agent, with a timestamp — this *is* "timestamped
  messages," it's the structured view next to the wall instead of burned into it.
- A cheap, real addition within the existing shim mechanism: a small floating badge over
  each wall tile ("last activity HH:MM:SS"), sourced from `event_log`'s `ts` over the
  dashboard's existing SSE channel (`sse.js`). This sits in chela's own DOM around the
  iframe — not inside `ttyd`'s page — so none of the constraints above apply. It's a
  per-agent status ticker, not a per-message inline stamp, which is a smaller thing than
  what was asked for; flagging that gap explicitly rather than quietly substituting it.

## Bottom line

"Timestamps on messages in the live terminal, via the hooks" is not feasible as scoped:
the hook channel cannot write into the pty stream at all, and the terminal wall's own
render path (an unmodified system `ttyd` binary bundling an opaque xterm.js) doesn't
expose a surface to attach per-message decorations to even if something *did* have a
write path in. Closing the gap for real would mean vendoring/patching `ttyd` (or replacing
it with a chela-owned xterm.js frontend) to get buffer-marker access, and *still* solving
the harder, hook-independent problem of mapping an async event timestamp to a specific
row in a TUI that repaints live — a materially bigger project than "via the hooks," and
one this task explicitly scoped out by asking for a spike first.
