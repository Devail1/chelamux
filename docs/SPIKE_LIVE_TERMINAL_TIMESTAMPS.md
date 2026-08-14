# Spike: timestamps on messages in the live terminal, via the plugin's hooks

**Question:** can chela stamp individual messages (user prompt / assistant reply) with a
timestamp *inside* the live terminal wall — the same view a browser tile shows — using
only the Claude Code plugin's hooks (`plugin/hooks/hooks.json` → `chela/hooks.py`)?

**Verdict: yes.** The original version of this document said no, and that verdict was
wrong. Below is the correction: the actual write path, why the original's "no path into
the pty" claim doesn't hold, and what is still unverified before this ships.

## ⛔ Correction (CMX-274) — the original verdict rested on the wrong mechanism

The original spike (CMX-270) tested one specific route — chela's own code reaching into
`ttyd`'s served page and touching xterm.js's buffer directly — found it closed on three
independent grounds, and generalized that closure to "the hook channel cannot write into
the pty stream at all." That generalization is false. It conflated two different
questions:

1. *Can chelamux's own JavaScript reach into `ttyd`'s rendered page and decorate a
   specific row?* No — confirmed below, this part of the original spike was measured
   correctly and stands.
2. *Can a hook cause the **Claude Code CLI process itself** — which is the thing actually
   drawing content into the pty, before `ttyd` ever sees a byte — to print a line?* Yes.
   Claude Code's hook JSON output schema has a field whose entire purpose is exactly
   this, and it is universal across every hook event, including the `http` transport
   chela uses for every hook except `SessionStart`.

The original spike never asked question 2. It answered question 1, thoroughly, and then
wrote the answer to question 1 as if it settled question 2.

## What the live terminal wall actually is (unchanged, and still the reason route 1 is closed)

Confirmed against a running tile (`ps aux | grep ttyd`, then `curl` its own page):

- Each wall tile is a same-origin reverse-proxied **`ttyd`** instance
  (`chela/dashboard/app.py`, "Terminal wall: same-origin ttyd reverse proxy" — `term_http`
  forwards `/term/<wid>/*` verbatim to `127.0.0.1:<port>`). `ttyd` attaches its own tmux
  client directly to the agent's pane and streams the pty bytes over its own WebSocket.
- `ttyd`'s entire frontend — including xterm.js — ships as **one minified webpack IIFE**
  inlined in the page. Nothing is attached to `window`: no global `term`, no `Terminal`
  handle, no exported module registry.
- xterm.js is running with **`CanvasAddon`/`WebglAddon`** (`rendererType:"webgl"` present
  in the bundle) — it paints to a `<canvas>`, not per-character DOM nodes.

Chelamux does inject `<script>` shims into `ttyd`'s served HTML today — paste-image,
Ctrl+V, the palette hotkey, touch-scroll, collab presence (`chela/dashboard/app.py`,
`_TERM_PASTE_SHIM` / `_TERM_PASTE_KEY_SHIM` / `_TERM_PALETTE_KEY_SHIM` and friends,
`term_http`). Proof a script *can* run in that page — but every one of them listens to
DOM/keyboard/paste events; none of them, nor anything else chelamux could inject, can
reach xterm's buffer to decorate a specific row (no handle, and a canvas has no DOM to
mutate). **This route is genuinely closed, and none of the above is what changed.** It's
also, per the correction, not the route that matters: chelamux doesn't need to write into
`ttyd`'s page at all if the process one layer upstream will write the line for it.

## The real write path: `systemMessage`, from Claude Code's own hook output schema

Per the current hook reference (`code.claude.com/docs/en/hooks`), every hook response —
`command` or `http` alike — is parsed against one shared JSON output schema, and one of
its universal fields is:

> `systemMessage` — "Warning message shown to the user."

And for the `http` transport specifically (which is what every chela hook except
`SessionStart` uses, per `plugin/hooks/hooks.json`):

> "Claude Code sends the hook's JSON input as the POST request body... The response body
> uses the same JSON output format as command hooks." A 2xx JSON body is "parsed using
> the same JSON output schema as command hooks."

This is not a theoretical schema field. Two real, reproduced reports against
`anthropics/claude-code` confirm it renders, live, as a persistent line in the terminal
transcript — the same pty stream `ttyd` mirrors byte-for-byte into the wall tile:

- **[#50542](https://github.com/anthropics/claude-code/issues/50542)** — a plugin-dispatched
  `Stop` hook returning `{"systemMessage": "✦ 30 memories woven into the palace"}` was
  observed rendering as "a visible single-line `<Line>` element via `AttachmentMessage.tsx`
  handling a `hook_system_message` attachment," displayed as `Stop says: <message>`. The
  reporter's own repro thread narrows the reliable shape: a **bare** `{"systemMessage":
  "…"}` renders and is then discarded within about a second (visible, but easy to miss);
  pairing it with the full schema — `{"continue": true, "suppressOutput": false,
  "systemMessage": "…"}` — made the line "persist as expected," consistently, across
  repeated fires.
- **[#40380](https://github.com/anthropics/claude-code/issues/40380)** — the same field on
  `PreToolUse`/`PostToolUse` is silently dropped **unless** `hookSpecificOutput` is also
  present (e.g. `hookSpecificOutput.permissionDecision: "allow"` alongside
  `additionalContext`); with it, both the terminal render and the model-context injection
  work. The report also names `#32624` as confirming the same field renders for
  `SessionStart`.

So the field is real, it is universal, `http` hooks carry it exactly like `command` hooks,
and the working shape is event-dependent: `Stop` wants `continue`+`suppressOutput`
alongside it, tool events want `hookSpecificOutput` alongside it. Bare `systemMessage` on
its own is the one shape that's unreliable.

## Why the "no hook→row mapping" objection dissolves

The original spike's second, deeper objection — that xterm's decoration API needs a
buffer marker, and there is no reliable hook→row mapping because the TUI repaints live —
was a real problem for the *ttyd-buffer-decoration* idea. It is not a problem here,
because this mechanism isn't decorating an existing row at all. Claude Code's own
process — the one already deciding, this instant, where the next line of its own
transcript goes — is the one inserting the line, synchronously, at the exact moment the
(blocking) hook call returns. There is no async timestamp to reconcile against a
scrolled, reflowed buffer from the outside: the write happens from the inside, in order,
by the same renderer that owns "what row is this."

## What's already wired in chela — zero new hook registration needed

`plugin/hooks/hooks.json` already registers `UserPromptSubmit` (fires at the user-message
boundary) and `Stop` (fires at the assistant-reply-finished boundary) against
`http://127.0.0.1:5001/hooks/*` — exactly the two events "user prompt / assistant reply"
in the original question maps to. `chela/hooks.py`'s `ingest()` currently returns only
the log record; the Flask routes for every event except `PermissionRequest` and
`SessionStart` reply with `{}` (see `chela/dashboard/app.py`). Making this real is a
response-body change on those two routes — attaching a timestamp-bearing `systemMessage`
(plus its event's required companion fields) to what's already returned — not new plugin
wiring.

## What remains unverified — the concrete next step before shipping

This correction is grounded in the current official schema docs and two reproduced,
external bug reports — not, unlike Part 1 above, a live `ps`/`curl`-style measurement
against chela's own daemon and a running tile in this repo. That's the standard this file
sets for itself, and it hasn't been met yet for this specific field. Before wiring it in
for real:

1. Point `chela/hooks.py`'s `UserPromptSubmit` and `Stop` routes at a response carrying
   `systemMessage` (with each event's required companion fields, per above) with a
   formatted timestamp.
2. Watch a real tile (`curl 127.0.0.1:<port>/term/@N/`, or the browser wall) across
   several fires and confirm the line renders, persists, and lands in the right temporal
   order — not just once, given `#50542`'s own account of intermittency on some CLI
   versions.
3. Confirm the currently-pinned Claude Code version behaves at least as well as the
   schema promises; the flakiness reports above are from `2.1.114`, and chela's own hook
   comments already track version-specific behavior elsewhere (`chela/hooks.py`'s
   `GATE_TIMEOUT` measurements).

## `terminalSequence` — a narrower, adjacent primitive

The same schema also exposes `terminalSequence`: "a terminal escape sequence for Claude
Code to emit on your behalf... Restricted to OSC `0`/`1`/`2`/`9`/`99`/`777` and BEL." That
can move a timestamp into the pane/tab **title** (a live "last activity" signal, closer to
the status-ticker idea this document previously proposed as a fallback), but it cannot
carry an arbitrary line of text into the transcript itself the way `systemMessage` can.
Worth knowing about; not the mechanism this correction is about.

## Bottom line

"Timestamps on messages in the live terminal, via the hooks" is feasible, via
`systemMessage` (with its event-appropriate companion fields) in the response body of the
`UserPromptSubmit` and `Stop` hooks chela already registers — both firing exactly at the
message boundaries asked about, both already wired to chela's own daemon, requiring a
response-body change rather than new plumbing. The original verdict's fatal claim — "the
hook channel... can't write a byte to the tmux pane" — was true of chelamux's own code and
false of Claude Code's, which owns the pty and exposes a documented field for precisely
this. What's still open is a live measurement of reliability against this repo's own
pinned Claude Code version, not feasibility.

## ⛔ Correction (CMX-285) — `systemMessage` is the wrong field, `MessageDisplay` is the right event

CMX-277 shipped the `systemMessage` mechanism above, and Liav's verdict on it: "i see the
timestamps now, but it doesn't seem to be presented like it does for
zoharbabin/claude-code-message-timestamps." He's right, and this document's own framing
is why: it asked "can a hook write *a line* into the transcript" and answered that
question well — but a reference plugin doing this same feature answers a different
question, "can a hook change how *this exact message* is displayed," and the two render
completely differently.

`systemMessage` is a **warning-message field**, universal across every hook event. Claude
Code renders it as its own `<Line>` — a `Stop says: …`-style attachment, separate from and
outside the message it's attached to. That is a real, working mechanism, and it does
"stamp the terminal with a timestamp" by any literal reading of that phrase. It is just
not what "timestamp on the message" means to someone who has used the reference plugin:
there, the clock reads `[14:32:05] Sure, I'll do that…` — the stamp is the first few
characters of the assistant's own reply text, not a line above or below it.

That shape comes from a **different, newer hook event** this repo's own schema dump
(`chela/hooks.py`'s comment trail never spotted it — the original spike measured against
`code.claude.com/docs/en/hooks`' prose, not a live binary's embedded schema) had all along:
**`MessageDisplay`**, fired once per streamed batch of an assistant message
(`{index, delta, final, …}`), whose `hookSpecificOutput.displayContent` response
"replaces the delta on screen without changing the stored message" — display-only, by
design, so it can never confuse the model the way editing `systemMessage` content would.
Stamping `index == 0`'s `delta` with a `"🕐 HH:MM:SS "` prefix is what makes the marker
part of the message's own first line, verified against `zoharbabin/claude-code-message-
timestamps`' own `hooks/scripts/timestamp-display.sh` (fetched via `gh api`), which does
exactly this. `MessageDisplay` needs Claude Code 2.1.152+; chela's install is pinned
above that, and an older pin simply never fires the hook — no marker, never a broken one.

This also fixes a volume problem CMX-277 didn't have to think about: `UserPromptSubmit`/
`Stop` fire once per message and were safe to log through the ordinary event-log path.
`MessageDisplay` fires once per streamed **batch** — order of magnitude more calls per
turn — so `chela/dashboard/app.py`'s endpoint answers it ahead of `hooks.ingest` entirely
and never logs it, keeping the feature off the event log's hot path whether or not
`CHELA_TERMINAL_TIMESTAMPS` is even on.

## Measured (CMX-285 rework round 1) — fire count and added blocking latency

The open question after the first review round: hooks *block* the agent, and the round-trip
cost measured against the live dashboard (n=40: median 6.8ms, p90 10.0ms, max 66.6ms) only
turns into a real number once multiplied by how many times `MessageDisplay` actually fires
for one reply — a number nothing in the repo had measured yet.

**Why `-p`/print mode can't answer this.** The first attempt used `claude -p "<prompt>"`
against an isolated dashboard instance (own `CHELA_DIR`, scratch port, so this never touched
the real orchestrator's state) — a 1460-word reply fired `MessageDisplay` exactly **once**.
Non-interactive print mode doesn't do the incremental TUI-repaint streaming this hook exists
for; it's effectively one batch regardless of reply length. The measurement has to run
through a real interactive session.

**Setup.** Isolated `chela dashboard` instance from this branch (own `CHELA_DIR`/scratch
port, no auth needed — `require_auth` is a no-op, the boundary is loopback bind), driven by
three separate real interactive Claude Code 2.1.232 sessions (`claude --settings
'{"hooks":{"MessageDisplay":[...],"Stop":[...]}}'`, additive so it never touched the
marketplace-installed plugin) in throwaway tmux sessions, each given the identical prompt
("write a 1500-word essay on the history of the printing press, no tool use"). Fire count =
`grep -c "POST /hooks/MessageDisplay"` in the isolated instance's own access log; wall-clock
= Claude Code's own built-in turn timer (`Baked/Crunched/Brewed for Xs`, shown after every
reply).

| run | `CHELA_TERMINAL_TIMESTAMPS` | `MessageDisplay` fires | wall-clock |
|---|---|---|---|
| A | on | 18 | 63s ("Baked for 1m 3s") |
| B | off | 20 | 58s ("Crunched for 58s") |
| C (control, hook not registered at all) | n/a | 0 | 59s ("Brewed for 59s") |

**Fire count for a ~1500-word reply: 18–20** — roughly one `MessageDisplay` batch per
75–85 words, at a cadence of about one every 3 seconds of streaming. At the PR's own measured
round-trip, that's ≈122–200ms of added blocking latency (median-to-p90) across the whole
turn — 0.2–0.3% of the ~60s the turn actually took, and smaller than the 5s (~8%) spread
already present between runs A/B/C from ordinary generation-speed variance. No systematic
difference is distinguishable between "on", "off", and "hook not registered at all."

**A finding beyond what was asked:** `CHELA_TERMINAL_TIMESTAMPS` does not gate whether the
round-trip happens. The hook is registered statically in the installed plugin manifest, so
Claude Code fires the HTTP request to `/hooks/MessageDisplay` on every batch regardless of
the flag; the flag only changes what `chela/dashboard/app.py` puts in the response body
(stamped delta vs. `{}`, both computed in-process, not the network hop). Run C — hook not
registered at all — is the only one of the three that actually skips the round-trip, and its
wall-clock (59s) is indistinguishable from A/B's (58–63s), which is itself the strongest
evidence that the round-trip cost is not observable against generation-speed noise.

**Verdict: `http` stays.** Fire count is bounded (tens, not hundreds, per reply — Claude
Code's own batching cadence, not something this repo controls or needs to), the added
latency is a fraction of a percent of total turn time, and it isn't measurably distinguishable
from run-to-run noise. No transport change, no first-batch logic change, no version-bump
change — the PR's implementation was already correct; what was missing was this number, not
a different design. Side note for future spikes here: the *first* MessageDisplay batch in
each run only landed 3-5s after `Enter` was actually submitted, not the network round-trip —
that gap is Claude Code's own first-token latency before streaming starts, unrelated to this
hook.
