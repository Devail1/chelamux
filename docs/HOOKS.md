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

## This slice is OBSERVE-ONLY

It **ingests**. It answers nothing. The endpoint returns `{}` — no `permissionDecision`,
no `hookSpecificOutput`, ever — because a decision in that response would silently start
answering the user's permission prompts on their behalf. The pane-scraped gates
(`chela/telegram/{panescan,gatewatch,interactive}.py`) are still what answers a gate, and
they stay: **hooks are read at agent startup**, so a fleet that is already running has
none, and a fleet member launched without the plugin never will. Hooks are the better
channel; they are not yet the *only* channel, and nothing here assumes they are.

## The log is what a relayed gate is RENDERED from

Still observe-only — the endpoint answers nothing — but the payload is no longer written
and forgotten. When the Telegram relay sees an `AskUserQuestion` selector on a window's
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

Answering is still keystroke injection, so the tap-to-answer buttons are attached only
where their ordinal mapping can be *proven* (one question, single-select, options the
scraper could count). For a multi-question or `multiSelect` gate the card renders in full,
offers the nav keys, and says outright that it must be answered in the terminal — a button
that silently picks the wrong option is worse than no button.

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
   `#{pane_current_path}` of a `claude` pane *is* that pane's origin directory, and
   encoding it yields the same slug.

So the lookup stays a single `tmux list-windows` call (~5 ms, cached ~1 s, keyed by slug)
plus a dict hit. The session→slug half is cached for the life of the process — a session's
origin never changes. A payload with no `transcript_path` falls back to one glob of
`~/.claude/projects/*/<session_id>.jsonl`, on a cache miss only.

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

Two of those do not deliver, and are documented rather than quietly missing:

| event | measured behaviour |
|---|---|
| `SessionStart` | **Never fires over the `http` transport** — it fires as a `command` hook, and `SessionEnd`/`Stop` fire over http, so this is the transport, not the config. chela does not spawn a `curl` for it: a session announces itself with its first `UserPromptSubmit` or `PreToolUse` anyway, and a `SessionStart` command-hook's **stdout is injected into the agent's context** — a strictly worse failure than a missing marker. It stays registered, so it starts working if Claude Code starts delivering it. |
| `PermissionDenied` | Does not fire when a human denies a gate interactively (neither `Esc` nor picking "No"). It appears to be for rule-based denials. Registered; simply rare. |

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
