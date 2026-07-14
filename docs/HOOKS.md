# Hooks — feeding the event log from Claude Code

Everything chela knows about a *blocked* agent today, it learned by scraping a tmux pane.
That is not an aesthetic choice. Claude Code writes an interactive tool's `tool_use`
record to the transcript **when the human answers it** — measured, not assumed — so at the
exact moment an agent is stuck on an `AskUserQuestion` or a permission gate, the
structured channel is *empty*. Reading characters off a terminal was the only thing left.

**Hooks are the channel that was missing.** They are typed, they carry the whole
`tool_input`, and they arrive *before* the fact. Where a pane scrape gives you a
truncated button caption, a hook gives you this, while the agent is still waiting:

```json
{"seq": 4, "type": "hook.permission_request", "wid": "@29",
 "summary": "permission asked — AskUserQuestion: Which storage backend should we use?",
 "payload": {"tool_name": "AskUserQuestion", "permission_mode": "auto",
   "tool_input": {"questions": [{"question": "Which storage backend should we use?",
     "options": [{"label": "SQLite",   "description": "One file, no server, transactional"},
                 {"label": "Postgres", "description": "A server to run, but concurrent writers"}]}]}}}
```

## What the endpoint answers — and everything it does not

It **ingests**, and it returns `{}`. One event is the exception, deliberately: a
`PermissionRequest` for an **`AskUserQuestion`** can come back carrying the human's answer
(see [Answering a question with zero keypresses](#answering-a-question-with-zero-keypresses)),
and only ever an answer a human actually tapped. Nothing else in this response decides
anything — no `permissionDecision`, no `hookSpecificOutput` on any other event — because a
decision in that body is chela answering someone's prompts on their behalf, and that is a
thing you should have to read about, not discover.

The pane-scraped gates (`chela/telegram/{panescan,gatewatch,interactive}.py`) stay:
**hooks are read at agent startup**, so a fleet that is already running has none, and a
fleet member launched without the plugin never will. Hooks are the better channel; they
are not the *only* channel, and nothing here assumes they are.

## The log is what a relayed gate is RENDERED from

The payload is not written and forgotten. When the Telegram relay sees an
`AskUserQuestion` selector on a window's
pane, the *content* it posts comes from that window's pending `hook.pre_tool_use`
(`chela/telegram/hookgate.py`), not from the scrape: every question, every option's
`label`, `description` **and** `preview`, one Telegram message per question.

That matters because the scrape is lossy in a way that has no fix. Its option patterns
were measured against *one* selector shape; a **multi-question** selector draws a tab
strip, and an option carrying a **`preview`** re-lays the TUI out side-by-side so the
option rows no longer start their line. Either one alone parses as "unparseable", and the
question lands on the phone with **no options at all** — which is exactly what happened
live on 2026-07-14, while the log already held the whole payload. The scraper will keep
meeting shapes it was not measured against; the hook is handed the structure.

The split, then:

* **the hook payload is the CONTENT** — a `pre_tool_use` for an interactive tool
  (`AskUserQuestion` / `ExitPlanMode`) with no `post_tool_use` bearing the same
  `tool_use_id` is *pending*. `PreToolUse` and not `PermissionRequest`, because only
  `PreToolUse` carries a `tool_use_id` — without one there is nothing to pair a resolution
  against;
* **the pane is the LIVENESS** — a pending call with no result could equally mean the
  agent *died* at the gate, so nothing is posted for a window whose pane is not showing a
  selector right now. It also stays the whole content source for a **pre-plugin** agent,
  which emits no hooks at all.

## Answering a question with zero keypresses

Every gate chela ever answered, it answered by *typing at the terminal*: find the `❯`
cursor, inject that many arrow presses, wait, send Enter. That substrate cannot be made
safe for the shapes we actually send. It has already produced a **silent mis-answer** — a
tap on option 3 selected option 2, because Enter raced the arrow moves (CMX-32) — and a
multi-question or `multiSelect` picker has no cursor semantics to inject against at all: an
answer to "which extras?" is a *set*, and "press Down twice" cannot express one.

**A `PermissionRequest` hook can just return the answer.** Measured on Claude Code 2.1.209,
against a real 3-question `AskUserQuestion` whose options carried previews:

```json
{"hookSpecificOutput": {"hookEventName": "PermissionRequest",
  "decision": {"behavior": "allow",
    "updatedInput": {"questions": [...],
      "answers": {"Which store?": "Postgres",
                  "Which extras?": ["Metrics", "Profiling"],
                  "Deploy when?": "Later"}}}}}
```

The agent's transcript then reads: *Your questions have been answered: "Which store?"=
"Postgres", "Which extras?"="Metrics,Profiling", "Deploy when?"="Later"* — and **not one
keystroke went near the pane**. It works in every permission mode, `auto` included. So a
held gate carries real answer buttons for **every** shape: one tap for a single-select,
`☐`/`☑` toggles plus **✅ Send** for a `multiSelect`, and a multi-question run answered
question by question.

**A gate is ONE Telegram message.** The mirrored pane is the body — the `❯` cursor, the
ticked boxes, the tab strip, re-drawn in place after every key — with the answer buttons
above a nine-key D-pad on the same keyboard. Watch the cursor and press `⏎`, or just tap the
answer. What the pane cannot show is a *comparison*: the TUI draws one option's `preview` at
a time (the one under the cursor), and it clips a long one. So the message also carries a
**📖** button that swaps its body for the whole payload — every question, every option's
`label`, `description` and `preview`, together — and **🎛️** swaps back. It is a toggle on
the one message, and the answer buttons stay put while it is open: expanding to compare must
never mean collapsing to answer.

The mechanics, and the three things that make them safe to run at a live fleet:

**The wait is bounded, and it fails OPEN.** The hook runs *synchronously inside the agent's
process*: the moment this endpoint blocks on a human, a live agent is frozen. So it waits
at most `CHELA_GATE_WAIT_S` (default 90s) and then gives up and answers nothing. Giving up
is **not a deny** — the picker is exactly where it was, still on the pane, still answerable
in tmux, and the run is no worse off than before the feature existed. (An auto-deny would
destroy work because a human was slow. Do not "harden" it into one.) `CHELA_GATE_MAX_WAITS`
caps how many gates may be held at once; past the bound the next one is not held at all.

**The budget can never outlive the hook that would deliver it.** `PermissionRequest` alone
declares a long `timeout` (120s) in the plugin manifest; every other event keeps the 2s one,
because `PreToolUse`/`PostToolUse` are ~78% of the log's volume. That ceiling is *measured*,
not taken from the docs — a hook that never replies, timed against a 4.5s baseline:

| declared `timeout` | how long the turn actually blocked |
|---|---|
| 10s | 10.2s |
| 65s | ~66s |
| 130s | ~133s |

Honoured **verbatim — there is no 60s clamp** — and on expiry Claude Code fails open by
itself. So a wait longer than the timeout is a wait that can never deliver, and the budget
is clamped strictly below it.

**A stale answer is refused.** A gate is identified by its `tool_use_id` — which
`PermissionRequest` does not carry, so it is correlated to the `PreToolUse` that fired for
the same call — and every button names it. A tap that lands after the gate resolved, timed
out, or belongs to a different question finds no open gate and is **dropped and reported**,
never applied to whatever is on screen by then. And a gate is only held for a window with a
**bound Telegram topic**: an agent nobody is watching is never frozen on a human who was
never shown the question.

**The answers map must be complete.** Measured: Claude Code accepts a *partial* map without
complaint and silently drops the unanswered question — the agent proceeds believing it
asked, and never re-asks. So chela holds the answer until every question has one (each tap
toasts how many are outstanding) and refuses anything less, along with any label the asker
never offered.

Keystroke injection survives for exactly one case: a **pre-plugin** agent, whose gate no
hook ever announced. There the old rules still apply — buttons only where their ordinal
mapping can be *proven*, the D-pad otherwise, and no 📖 at all: with no payload there is
nothing to expand into, and a button that opened an empty page would be a lie.

## Install

chela ships the hooks as a **plugin**, and never by writing your `settings.json` — that
file holds your hand-curated permission rules and chela has no business opening it.
Plugin hooks *merge* additively with your own (at the lowest precedence, firing last),
which is exactly right for something that only watches.

```
/plugin marketplace add Devail1/chelamux
/plugin install chela@chela
```

**Not on the default dashboard port?** Then you need your own copy of the manifest. A hook
`url` is a literal — Claude Code does **not** expand environment variables in it (measured
on 2.1.207) — so the port is baked in when the plugin is rendered:

```bash
chela plugin --dir ~/.chela/plugin      # bakes in the port the dashboard is actually on
claude --plugin-dir ~/.chela/plugin     # one session
# ...or /plugin marketplace add ~/.chela/plugin   (it is a one-plugin marketplace too)
```

"Actually on" is meant literally, and it is the difference between this feature working
and doing nothing at all: the dashboard **publishes the port it bound** to
`$CHELA_DIR/dashboard.port`, and `chela plugin` — a different process, which sees none of
the dashboard's own environment — renders *that*. When it was left to read
`CHELA_DASHBOARD_PORT` instead, a dashboard started with `--port 5005` produced a manifest
aimed at 5001; every hook POSTed into a closed socket, failed open exactly as designed, and
said nothing. Run `chela doctor` if you suspect it (see [CONFIG.md](CONFIG.md)).

Restart an agent to pick the hooks up. A running one will not.

## The manifest you render is not the manifest that runs

`/plugin install` **copies** the plugin into Claude Code's own cache
(`~/.claude/plugins/cache/<marketplace>/<plugin>/<version>/`), and **that copy is what
every agent loads at startup**. Re-rendering `~/.chela/plugin` changes nothing until you
reinstall it — and for a day it changed nothing while every check said otherwise: the
rendered manifest raised the `PermissionRequest` timeout to 120s, the installed one still
said `2`, so every gate hook was killed after two seconds, no gate was ever *held*, and the
phone's answer buttons never appeared. `chela doctor` was green throughout, because it read
the file chela **writes**.

So both commands now read the copy that runs:

* `chela plugin` renders the manifest, then reads the installed copy back and tells you, by
  name, if it is stale — with the reinstall commands.
* `chela doctor` compares the **installed** manifest to the one the code renders. A drift is
  an **ERROR** (the feature is dead, not degraded). So is an installed copy it cannot find
  or cannot read: Claude Code's cache is an implementation detail and may change shape
  between releases, and the only honest answer then is a loud *"I cannot verify this"* — a
  silent green here is the very bug being caught, one level up.

The install path is **discovered**, never constructed: Claude Code records it in
`~/.claude/plugins/installed_plugins.json`, and it contains the plugin *version*, so a
hardcoded path would quietly check a directory that no longer exists the day `plugin.json`
is bumped. (If that registry is unreadable, chela falls back to scanning the cache.)

chela does **not** write into that cache. It is Claude Code's — keyed by version and
recorded in Claude Code's own bookkeeping — and a reinstall would overwrite anything chela
put there, leaving Claude Code describing a copy it never installed. Detect and instruct:

```
chela plugin                      # re-render (picks up the live port + the current spec)
/plugin uninstall chela@chela     # in Claude Code — refresh the copy agents read
/plugin install chela@chela
```

Then **restart the agents**. Hooks are read at startup; a running fleet keeps the manifest
it booted with.

## How an event gets in

Each hook is an **`http`** hook posting to the daemon the dashboard is already running:

```json
{"type": "http", "url": "http://127.0.0.1:5001/hooks/PreToolUse", "timeout": 2}
```

(5001 is chela's default port; the rendered manifest carries whatever yours is on.)

No shell script, no process spawn per tool call, no PATH assumption — and no way for a
chatty `.bashrc` to put stray stdout into the JSON contract a `command` hook has to
honour. The endpoint appends via [the event log](EVENTS.md) and returns.

**A hook runs synchronously inside a live agent**, so every choice here is made against
one constraint: *a slow or crashing hook stalls or breaks somebody's session.*

* the timeout is short (2s) and the receiver only appends;
* `hooks.ingest()` and `event_log.append()` both refuse to raise;
* a malformed body is dropped, a huge one is clipped, and either way the answer is `200`;
* **the daemon being down fails OPEN** — the connection is refused, Claude Code logs a
  warning and carries on. The event is lost. A lost event is a bug; a wedged agent is an
  outage, and this trade is made deliberately and in that direction.

## Correlating an event to a window, without the pane

A hook payload carries `cwd`, `session_id` and `transcript_path`. It does not carry a tmux
window, and going back to the pane to find one would reinvent the thing this replaces.

**The key is the session's ORIGIN directory — the directory `claude` was launched in.**
⛔ **It is emphatically not `cwd`.** `cwd` is the session's *current* directory: it moves
the moment an agent `cd`s, which is completely normal, and the pane's `#{pane_current_path}`
moves too. Keying on it produced not ambiguity but a **confidently wrong answer** — the
orchestrator (window `@0`, launched in `~`) `cd`-ed into the chelamux repo to work, and
every one of its events was then filed against `@1`, the window of a *different* agent who
genuinely lives there. A per-window timeline built on that key would simply lie.

The origin directory is immutable on both sides, and three measured facts (Claude Code
2.1.207) make it free to read:

1. a session's transcript lives at `~/.claude/projects/<slug>/<session_id>.jsonl`, and
   `<slug>` is derived from the origin directory **once, at session start**. It does not
   follow a `cd` — the orchestrator's session still writes to the `~` slug while its
   payloads report the repo;
2. **every payload carries `transcript_path`**, so the slug is already in the event — no
   filesystem access, no `/proc` walk, no `pgrep`;
3. Claude Code **never `chdir`s its own process** (it tracks the working directory
   internally — which is exactly why the payload `cwd` and the process cwd disagree). So
   the *process* cwd of a pane's `claude` **is** that pane's origin directory, and encoding
   it yields the same slug.

So the lookup stays a single `tmux list-windows` call (~5 ms, cached ~1 s) plus a couple of
`/proc` reads and a dict hit — still no `pgrep`, no `capture-pane`, no `claude agents
--json`. The session→slug half is cached for the life of the process — a session's origin
never changes. A payload with no `transcript_path` falls back to one glob of
`~/.claude/projects/*/<session_id>.jsonl`, on a cache miss only.

### `--resume` is the exception, and it is checked first (CMX-70)

A session **resumed from a different directory** breaks fact 1's *other* half: the
transcript stays in the project dir the session was **born** in, so its slug names a
directory no pane is sitting in. Origin matching then resolves it to `None` — or, worse, to
an unrelated agent who genuinely lives in that birth directory.

The pane's own command line settles it outright: **`claude --resume <session-id>` is that
window claiming that session, by construction**, and it is consulted *before* the slug.
`chela/sessions.py` owns both signals (and is also what the outbound relay resolves a
window's transcript through — same question, one answer).

**Ambiguity resolves to `None`, never to a guess.** Two agents launched in one directory
cannot be told apart, and an unknown session resolves to `None` — **never** to the window
whose `cwd` happens to match, because that fallback *is* the bug. An event filed against
the *wrong* window is worse than one filed against no window; the `session_id`, `cwd` and
`transcript_path` are in the payload regardless, so nothing is lost but the shortcut.

A **subagent**'s hooks carry its parent's `session_id`, so they resolve to the parent's
window. That is the right answer rather than a near-miss: the subagent runs inside that
agent, in that window, and has no window of its own.

> ⚠️ **`hook.*` records written before this fix (CMX-48) have unreliable `wid`s** — any
> event from an agent that had `cd`-ed away from its origin was filed against whichever
> window happened to sit in that directory. They are left in `events.jsonl` as written:
> the log is append-only, and the pane table that produced them is gone, so a rewrite
> would only be a *different* guess. Their `session_id` and `transcript_path` are intact,
> so a reader can re-derive the truth — but if you are about to build a per-window UI on
> the log, drop the old hook lines rather than render them.

## What actually fires (measured on Claude Code 2.1.207)

`AskUserQuestion` and `ExitPlanMode` are **not** hook events — they are **tools**, and
arrive as `PreToolUse` / `PermissionRequest` carrying `tool_name` and the full
`tool_input`. `PermissionRequest` fires for `AskUserQuestion` **in every permission mode,
`auto` included**: auto does not auto-answer a question, it genuinely blocks on the picker.

Registered: `PreToolUse`, `PostToolUse`, `PermissionRequest`, `PermissionDenied`,
`UserPromptSubmit`, `SessionStart`, `SessionEnd`, `Stop`, `SubagentStart`, `SubagentStop`,
`Notification`, `PreCompact`, `PostCompact`, `Elicitation`.

One of those does not deliver over http, and one barely delivers at all. Both are
documented rather than quietly missing:

| event | measured behaviour |
|---|---|
| `SessionStart` | **Never fires over the `http` transport** — it fires as a `command` hook, and `SessionEnd`/`Stop` fire over http, so this is the transport, not the config. It is therefore the ONE hook chela ships as a `command`, and a command hook's **stdout is injected into the agent's context** — which is not a hazard here, it is the delivery mechanism. See [The room recap](#the-room-recap-sessionstart). |
| `PermissionDenied` | Does not fire when a human denies a gate interactively (neither `Esc` nor picking "No"). It appears to be for rule-based denials. Registered; simply rare. |

## The room recap (`SessionStart`)

**Hooks are read at agent startup, and an agent's context does not survive its process.**
Everything a [room](../README.md#agent-rooms--agents-that-can-actually-talk-to-each-other)
ever told an agent — the handoff, the question, the blocker — was injected into a
*session*, and a dispatched agent is a fresh session every run. Restart it and the shared
context is gone: the ledger still holds every post, and the only reader who needed them
has forgotten they exist. `SessionStart` is the one moment we can hand them back.

The hook is a `curl` into the same endpoint every other hook POSTs to:

```json
{"type": "command",
 "command": "curl -s --fail --max-time 3 -X POST -H 'Content-Type: application/json' --data-binary @- http://127.0.0.1:5001/hooks/SessionStart 2>/dev/null || true",
 "timeout": 5}
```

Not a `chela` spawn: `chela` is not on an agent's PATH (it is a `uv run` inside the repo),
so `chela room recap` would be `command not found` in most fleets — and it would fail
*invisibly*, because a hook that prints nothing is indistinguishable from an agent with no
rooms. The daemon already holds the tmux table, `rooms.json` and the log; a curl is a ~5 ms
spawn against ~90–250 ms of Python that would re-read all three, and **the agent blocks on
it**. It fails open by construction: `--fail` so an HTTP error body can never be injected
as context, stderr to `/dev/null`, and `|| true` so a missing curl or a dead daemon exits 0
having printed nothing at all — which is exactly "no recap".

The daemon replies with `hookSpecificOutput.additionalContext` (honoured for `SessionStart`
— Claude Code collects `additionalContexts` from its SessionStart hooks), built by
`rooms.recap()`:

```
[chela room] shared context recap — you are @3. Your room(s) below: …
room "wire" — peers: @5 (cmx-64)
  #128 question from @5 (cmx-64) → YOU: does the retry live in the parser or the client?
  #121 status from @5 (cmx-64): pushed the schema migration
Answer one: chela room post <room> --kind handoff --from @3 --to @N --reply-to <seq> -- "<your answer>"
```

Four rules, and each one is load-bearing:

* **An agent in no room gets NOTHING** — not a header, not "no shared context". The stdout
  of this hook is the agent's context; most agents are in no room; boilerplate in all of
  them for the benefit of none is a tax on every run in the fleet. The response body is
  empty, so `curl` prints zero bytes.
* **It is bounded** — the last `RECAP_POSTS` posts per room, one line each, newest-first,
  capped at `RECAP_MAX_CHARS`. It rides in every fresh context, forever.
* **It is sanitised** — those are other agents' words going into a context window, so every
  line goes back through `rooms.sanitize()`, and the recap opens with the `[chela room]`
  header, which makes it unpostable: `rooms.is_relay_text()` refuses a body that starts
  with one (the echo guard, for free).
* **The window is resolved off the session's ORIGIN, never `cwd`** (the CMX-48 rule, same
  as every other hook). A session that cannot be correlated gets nothing rather than
  somebody else's rooms.

`chela room recap [--wid @N]` prints exactly what the hook would inject — including
printing nothing at all.

> ⚠️ **A new hook means the INSTALLED copy is stale.** The plugin manifest is *copied* into
> Claude Code's cache at install time, keyed by the plugin version, so a fleet installed
> before this change still runs a manifest with no recap hook in it. `chela doctor` catches
> exactly that (it compares every declared hook — `type`, `url`, `command`, `timeout` — not
> merely the first one), and the fix is the usual one: `chela plugin`, then
> `/plugin uninstall chela@chela` + `/plugin install chela@chela`. Hooks are read at agent
> **startup**: a running agent keeps the old set until it restarts.

## Event types in the log

A hook event is namespaced — `hook.pre_tool_use`, `hook.permission_request` — so *an agent
told us this* is distinguishable at a glance from chela's own bookkeeping (`run_review`,
`died`, `daemon_start`). Everything else is [the event log's ordinary record](EVENTS.md):
`seq`, `boot_id`, `ts`, `wid`, `session_id`, a one-line `summary`, and the payload.

```bash
chela events --type hook.permission_request --follow    # every gate, live, fleet-wide
chela events --wid @3 --type hook.pre_tool_use          # what @3 is doing
```

A big payload is **clipped, never dropped** — a `Write` of a 200 KB file carries that file
in `tool_input.content`, and the log is a line-per-event JSONL a human tails. Strings are
bounded, and a payload past the ceiling degrades to a stub. What the bound is written to
protect is the per-option `label`/`description` of an `AskUserQuestion` and a Bash
`command`: the things a decision is actually made from.
