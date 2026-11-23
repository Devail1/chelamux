# tmux touchpoints: where chela types into or reads a pane

Every place chela types into a tmux pane (`send-keys`, `paste-buffer`) or reads one
(`capture-pane`), sorted by what it is for, with a recommendation for each: **keep** it on
tmux, **replace** it with a Claude Code native mechanism, or **remove** it. The goal is that
native peer messaging is the default way chela talks to an agent, and that tmux is used only
where nothing else can do the job.

This started as an audit (CMX-474) that changed no behaviour. The follow-up work is
written out as [slices](#slices) at the end; slice 1 is done (CMX-482), and the rows it
changed say so.

**The doc is kept complete by a test.** `tests/test_tmux_touchpoints.py` parses `chela/`
with `ast` and fails when a pane-touching function has no row here, when a row names a
function that no longer exists, or when a row has no bucket or recommendation. A function
counts as a site when it builds a `["tmux", "send-keys" | "capture-pane" | "paste-buffer", …]`
argv, passes one of those verbs to a tmux wrapper, calls a private (`_name`) site in the same
module (one hop: `dispatcher.tick` → `_capture_pane`), or calls or references one of
`chela.messenger`'s pane primitives (`send_tmux`, `resend_enter`, `send_key`, `send_escape`,
`capture_pane`, `_capture_pane`, `deliver_prompt`, `deliver_image`, `refuses_paste`,
`_read_draft`). The scanner also checks the shell scripts under `scripts/` and `plugin/`;
none of them types into or reads a pane today. A few rows are not scanner sites but
**consumers** of captured text or of the terminal stream (the gate watcher, the screenshot
renderer, the Wall, the collab bridge). They are listed so the doc covers everything that
depends on a pane, and the test only requires that they still exist.

## Why this matters

A message typed into a pane arrives as a bare **user turn**: no `from`, so the receiving
Claude Code session shows no cross-session banner, and text from another agent reads as if
the user wrote it. A native peer message arrives as `<cross-session-message from="…">`, and
the receiver's own `crossSessionInbound` setting decides whether to take it. The orchestrator
CLAUDE.md records the 08-25 incident this caused. `messenger.send_message` and its callers
used to try the peer socket first and fall back to `send_tmux` without telling anyone when
the socket couldn't be reached. Since slice 1 (CMX-482) they don't: an unreachable socket
returns `unreachable`, the message is held where a queue exists, a `peer_unreachable` event
is logged, and `chela msg` / `chela drive` exit non-zero. `chela doctor` names the windows in
that state as "unreachable: messages are held" (`runtime_truth.py`,
`messenger.peer_transport_kind`).

## Buckets

| # | Kind | Target |
|---|------|--------|
| 1 | **Message to an agent** from another agent or from chela itself (inbox, rooms, nudges, `chela drive`) | Native peer message only. If the socket can't be reached, hold and retry, or fail loudly to the orchestrator. Never fall back to tmux. |
| 2 | **Starting a dispatched agent and seeding its task** | The prompt goes in at launch (`claude … "<prompt>"`, `--append-system-prompt-file`) or as a native message, not as `send-keys` plus N retries. Typing the launch command into the window's own **shell** is part of this bucket and stays (see the note below). |
| 3 | **State read**: busy, idle, dialog showing, empty prompt, input mode, login expired | Native `claude agents --json` status, the transcript, hook events, the registry. Read the pane only where nothing native exists, and the row says what is missing. |
| 4 | **Slash command or TUI control key** (`/rename`, `/exit`, Escape) | A launch flag or native equivalent if there is one. Otherwise it stays on tmux, behind the CMX-434 gates: idle, no dialog, empty prompt. |
| 5 | **Human input**: Telegram inbound, dashboard typing, the user's own schedules | Stays on tmux. It is the user, so arriving as a user turn is correct. |
| 6 | **Display**: the Wall, `/screenshot`, the Telegram mirror | Stays on tmux. It is the terminal itself. |

**Shell launch note (bucket 2).** `dispatcher._launch_agent`, `personas/autolaunch` and
`spawn._send` type into the window's **shell** before Claude Code exists, using the two-step
pattern (`new-window`, then `send-keys 'claude …'`). It stays because `claude` has to be a
child of the pane shell, or `agent_manager.claude_pid` (`pgrep -P`) never finds it and the
window gets no Telegram topic. What changes is only the seed: it moves into that launch line.

## Call sites

Columns: **site** (`file:function`), **what it sends or reads, and why**, **arrives as**
(for a write: a user turn, or a native message with `from`), **bucket**, **recommendation**.

### Messages to agents (bucket 1)

Since slice 1 (CMX-482) none of these reaches a pane: each goes through
`messenger.deliver_peer` (or `send_peer` / `send_peer_to_pid` in the inbox) and nothing
else. `tests/test_tmux_touchpoints.py` fails if any bucket-1 row becomes a pane site again.
The fleet pause moved to bucket 4: its message is native, and only its Escape touches the
pane.

| Site | What and why | Arrives as | Bucket | Rec |
|------|--------------|------------|--------|-----|
| `chela/messenger.py:send_message` | `chela msg` / `broadcast` / `fleet` / dashboard trigger: `[from] text` to an agent, over `deliver_peer`. Returns `delivered`, `unreachable`, `offline` or the adverse receipt; `chela msg` / `broadcast` exit non-zero on anything but `delivered`. **Done (CMX-482):** no `send_tmux` fallback. | Native with `from`; held (not sent) when unreachable | 1 | remove |
| `chela/rooms.py:post` | A room post to each member. An unreachable socket **parks** the delivery (`unreachable` in the result, `chela room post` exits non-zero). **Done (CMX-482).** | Native, or parked | 1 | remove |
| `chela/rooms.py:flush_pending` | Retries a parked room delivery over the socket; still unreachable leaves it queued until `PENDING_TTL_SECONDS`. **Done (CMX-482).** | Native, or still parked | 1 | remove |
| `chela/inbox.py:deliver` | Decision-inbox events to the orchestrator, over the socket only. A failed send HOLDS the event in the durable queue and records `last_delivery_failure`. **Done (CMX-482):** the `tmux_fallback` is gone. | Native, or held | 1 | remove |
| `chela/inbox.py:send_resumes` | The resume prompt sent to each agent after a usage outage ends, over `deliver_peer`. A failed send re-queues the outage. **Done (CMX-482).** | Native, or re-queued | 1 | remove |
| `chela/main.py:cmd_drive` | `chela drive @N msg`: the orchestrator drives a sibling with `[sender] msg`, now a native peer message (`deliver_peer`, sender = the driving window). Exits non-zero naming the window and reason when not delivered. **Done (CMX-482):** it used to be tmux-only. | Native with `from` | 1 | remove |

### Starting and seeding agents (bucket 2)

| Site | What and why | Arrives as | Bucket | Rec |
|------|--------------|------------|--------|-----|
| `chela/dispatcher.py:_launch_agent` | Four `send-keys` into the new window's **shell**: the env-allowlist `unset`, `export CHELA_RUN…`, `export CHELA_WID`, then the `claude …` command. Then `_wait_for_ready` and `_send_seed`. | Shell input (no Claude Code yet) | 2 | keep |
| `chela/dispatcher.py:_send_seed` | Pastes the task prompt with `send_tmux`, then re-sends Enter only (`resend_enter`) up to `SEED_MAX_SENDS` times until native status reads `busy`. Also used for the watchdog's idle re-nudge (`tick`) and the judge retry (`_judge_watchdog`). | **A user turn** | 2 | replace |
| `chela/dispatcher.py:_wait_for_ready` | Polls `_capture_pane` for the `❯` prompt glyph before seeding. Only needed because the seed is typed. | Read | 2 | remove |
| `chela/personas/autolaunch.py:_spawn_orchestrator_window` | `send-keys` the `export CHELA_WID … CHELA_ACTOR` line, then `claude --append-system-prompt "$(cat persona)" …` into the shell. The persona already goes in at launch. | Shell input | 2 | keep |
| `chela/spawn.py:_send` | `send-keys -l` + Enter into a new window's shell: the setup line, then the launch command (`claude --remote-control <name> --session-id …`). Used by `spawn_window` / `spawn_sandbox_window`. | Shell input | 2 | keep |
| `chela/agent_manager.py:start_agent` | Types `cd <dir> && CHELA_WID=… claude` into an agent window's shell through `send_tmux` (whose Claude-specific input-mode check means nothing on a shell). | Shell input | 2 | keep |
| `chela/dispatcher.py:_spawn` | First dispatch: calls `_launch_agent`, so it inherits the shell typing and the typed seed. | Shell input + a user turn (seed) | 2 | replace |
| `chela/dispatcher.py:_respawn_rework` | Rework re-spawn: same `_launch_agent` path, with the rework prompt as the seed. | Shell input + a user turn (seed) | 2 | replace |
| `chela/dispatcher.py:_spawn_judge` | Judge spawn: same `_launch_agent` path, with the judge prompt as the seed. | Shell input + a user turn (seed) | 2 | replace |
| `chela/personas/autolaunch.py:wake` | Caller of `_spawn_orchestrator_window`. | Shell input | 2 | keep |
| `chela/spawn.py:spawn_window` | Caller of `_send`: the dashboard, `/new` and restore launches. | Shell input | 2 | keep |

### State reads (bucket 3)

| Site | What and why | Arrives as | Bucket | Rec |
|------|--------------|------------|--------|-----|
| `chela/dispatcher.py:_capture_pane` | The dispatcher's own `capture-pane` (optionally `-e`) primitive. | Read | 3 | keep |
| `chela/dispatcher.py:tick` | Idle watchdog: `-e` capture, ghost suggestion dropped, then `_pane_idle_empty_prompt` plus an activity veto, combined with native `_agent_status`. Catches a **stranded seed** (idle at an empty prompt) and re-nudges it through `_send_seed`. Native `waiting` already drives the dialog branch. Once the seed goes in at launch (slice 2), a native `idle` for N minutes plus a transcript whose last entry is a finished assistant turn answers the same question. Native gap: none, once the stranding can't happen. | Read | 3 | replace |
| `chela/dispatcher.py:_judge_watchdog` | Reads a judge pane for the "login expired" banner (CMX-282). Native gap: `claude agents --json` doesn't report per-session auth errors. `claude auth status --json` exists (2.1.330, binary) but is host-wide and starts a heavy process. Kept until slice 3 measures it. | Read | 3 | keep |
| `chela/messenger.py:_capture_pane` | messenger's private plain capture: used by `refuses_paste` and `send_tmux`'s paste-chip check. | Read | 3 | keep |
| `chela/messenger.py:capture_pane` | The public capture (`-e` on request), used by every reader below and by `/screenshot`. | Read | 3 | keep |
| `chela/messenger.py:refuses_paste` | Reads the input box's first glyph (`❯`/`!`/`#`) so nothing is typed into a pane in bash or memory mode (CMX-79). Native gap: **no native API exposes the prompt's input mode.** Stays as long as anything types into a pane. | Read | 3 | keep |
| `chela/messenger.py:_read_draft` | `-e` capture → `input_draft` (ghost-aware) for `deliver_prompt` / `deliver_image`. Native gap: **no native API exposes an unsent draft.** | Read | 3 | keep |
| `chela/restart.py:_draft` | Same draft read, as `chela restart`'s "empty prompt" gate before `/exit`. Native gap: as above. | Read | 3 | keep |
| `chela/main.py:cmd_telegram` | Wires `capture_pane` (and `ActivityGatedCapture(capture_pane)`) into the Telegram gate watcher. The watcher matches a permission or AskUserQuestion selector on screen against the hook log's pending gate, and renders the mirror. Hooks (`hookgate.pending_gate`) say what a gate asks. Native gap: **only the pane shows that the selector is on screen right now** and which option is highlighted. | Read | 3 | keep |
| `chela/telegram/gatewatch.py:PermissionGateWatcher._poll_window` | Consumer: one capture per bound window per tick, shared by the AskUserQuestion, plan and permission detectors. Native gap: as above. | Read | 3 | keep |
| `chela/telegram/panecache.py:ActivityGatedCapture` | Consumer: re-captures only windows whose `#{window_activity}` moved (CMX-18 cost fix). | Read | 3 | keep |
| `chela/telegram/reconcile.py:blocked_on_human` | Auto-topic: an unbound dispatched window gets a Telegram topic when it is blocked on a human. Checks the hook gate first, then `detect_dialog(capture_pane(wid))` for dialogs that fire no hook. Native gap: dialogs with no hook event. | Read | 3 | keep |

### Slash commands and control keys (bucket 4)

| Site | What and why | Arrives as | Bucket | Rec |
|------|--------------|------------|--------|-----|
| `chela/restart.py:_send_keys` | `chela restart`: Escape, `/exit`, Enter, after native `idle` and an empty-draft read (`_draft`). No native "exit this session" exists. | A slash command | 4 | keep |
| `chela/rc_rename.py:deliver` | `/rename <window name>` keeps a Remote Control session's claude.ai and desktop name in step with the tmux name (the daemon retries the queue every tick via `flush_pending`). Launch-time naming is already native (`--remote-control <name>`, 2.1.280+, CMX-460). **No native way to rename a running session**, so `/rename` stays, gated: not `waiting`, readable status, an `-e` capture showing an empty prompt, `interrupt=False`. | Read + writes `/rename` | 4 | keep |
| `chela/suggest_names.py:_deliver` | Same for a suggested title: native `idle`, no queued rc rename, `-e` capture, `dialog_showing`, empty prompt (the CMX-434 gates). | Read + writes `/rename` | 4 | keep |
| `chela/suggest_names.py:deliver_title` | The locked public entry that calls `_deliver`. | Read + writes `/rename` | 4 | keep |
| `chela/agent_manager.py:stop_agent` | Types `/exit` through `send_tmux` with `interrupt=True`, so Escape **cancels a turn in progress**. Checks nothing first: no status, no draft (a draft gets `/exit` appended and submitted). | A slash command | 4 | replace |
| `chela/dispatcher.py:_dismiss_input_block` | Sends Escape to a dispatched agent stuck on an AskUserQuestion with no human there, so the model falls back to its own default. Since CMX-478 a permission prompt is never Escaped (Escape denies the call); it alerts the inbox. A `PreToolUse` hook that refuses AskUserQuestion when `CHELA_RUN` is set would mean the dialog never opens. chela already ships gate hooks. | A control key | 4 | replace |
| `chela/messenger.py:send_escape` | One Escape keypress (`/esc` on Telegram, fleet pause). | A control key | 4 | keep |
| `chela/fleet.py:_pause` | Fleet pause: Escape, then the pause message through `send_message` (native since CMX-482, bucket 1). The Escape stays, because no native message can interrupt a turn in progress. | A control key (+ a native message) | 4 | keep |
| `chela/fleet.py:pause_all` | Caller of `_pause` for every dispatched run (`chela dispatch --pause --all`). | A control key (+ a native message) | 4 | keep |
| `chela/messenger.py:send_key` | One tmux key name (`Up`, `C-c`, `Enter`…). Primitive behind the Telegram keyboard and the AskUserQuestion answer keys. | A control key | 4 | keep |
| `chela/messenger.py:resend_enter` | A bare Enter for a typed prompt the TUI didn't submit (seed retries, image chip). Not needed for the seed after slice 2; `deliver_image` still uses it. | A control key | 4 | keep |

### Human input (bucket 5)

| Site | What and why | Arrives as | Bucket | Rec |
|------|--------------|------------|--------|-----|
| `chela/messenger.py:send_tmux` | The primitive that types text into a pane: refuses unsafe input modes, Escape before a slash command, bracketed paste for multi-line text, a second Enter for a paste chip. Stays for human input and gated slash commands. Since slice 1 (CMX-482) it is reachable from no bucket-1 path. | A user turn | 5 | keep |
| `chela/messenger.py:deliver_prompt` | `send_tmux` that refuses to append to an existing draft (CMX-117). Telegram text and documents. | A user turn | 5 | keep |
| `chela/messenger.py:deliver_image` | Telegram photo: path pasted, submit checked via the UserPromptSubmit hook, then a draft read. | A user turn | 5 | keep |
| `chela/dashboard/app.py:api_agents_msg` | Dashboard "message agent": a `/command` goes through `send_tmux`, anything else through `deliver_prompt` (409 when the prompt holds an unsent draft). The person typing is the **user**, so a user turn is correct here; since CMX-482 it says so explicitly and no longer goes through `send_message`. | A user turn | 5 | keep |
| `chela/telegram/inbound.py:RegistryRouter.__init__` | Default sender `messenger.deliver_prompt` for a bound topic's text. | A user turn | 5 | keep |
| `chela/telegram/inbound.py:TopicRouter.__init__` | Same for the legacy topic router. | A user turn | 5 | keep |
| `chela/telegram/inbound.py:build_application` | Wires the defaults `capture_pane` (`/screenshot`), `send_escape` (`/esc`) and `send_key` (control keyboard). | User keys | 5 | keep |
| `chela/telegram/inbound.py:build_application._on_photo` | A photo: `deliver_image`. | A user turn | 5 | keep |
| `chela/telegram/inbound.py:build_application._on_document` | A document: saved, then the path is delivered with `deliver_prompt`. | A user turn | 5 | keep |
| `chela/telegram/inbound.py:build_application._on_esc` | `/esc`: `send_escape`. | A user key | 5 | keep |
| `chela/telegram/inbound.py:build_application._on_key` | The `/screenshot` control keyboard: one tmux key per tap. | A user key | 5 | keep |
| `chela/telegram/inbound.py:build_application._on_mirror` | Mirror D-pad taps: key presses, then a re-render. | A user key | 5 | keep |
| `chela/telegram/inbound.py:build_application._on_qa` | AskUserQuestion answered from Telegram: Down/Up presses + Enter to pick option *i*. | User keys | 5 | keep |
| `chela/dashboard/app.py:_term_keyargv` | Mobile key bar: whitelisted tmux key names, punctuation with `-l`, `copy-mode` exit. | User keys | 5 | keep |
| `chela/dashboard/app.py:api_term_key` | The `/api/term/key` route that runs `_term_keyargv`'s argv. | User keys | 5 | keep |
| `chela/dashboard/app.py:api_term_paste` | Browser clipboard → `load-buffer` + `paste-buffer -p -d`. | User input | 5 | keep |
| `chela/dashboard/app.py:api_term_upload` | A dropped file is saved, then `@uploads/<name> ` is typed (`-l`, no Enter) so the user can finish the prompt. | User input (unsent) | 5 | keep |
| `chela/scheduler.py:tick` | A user-authored schedule's prompt typed into the named agent at its time. It is the **user's own** deferred prompt, so a user turn is right. Sending it as a native message would change how the agent treats it: it would arrive bannered as "from another session". | A user turn | 5 | keep |

### Display (bucket 6)

| Site | What and why | Arrives as | Bucket | Rec |
|------|--------------|------------|--------|-----|
| `chela/telegram/screenshot.py:text_to_image` | Renders an `-e` capture (from `capture_pane`) to the `/screenshot` PNG. | Read | 6 | keep |
| `chela/telegram/gatewatch.py:PermissionGateWatcher._redraw_mirror` | Re-captures and re-renders the live Telegram mirror after a tap. | Read | 6 | keep |
| `scripts/agent-terminals.sh` | The Wall: ttyd attached to each window. Reads the terminal stream, not `capture-pane`. | Read (stream) | 6 | keep |
| `chela/collab_stream.py:Bridge` | Collab terminal: relays ttyd frames. A `capture-pane` snapshot was tried and dropped (it loses terminal state). | Read (stream) | 6 | keep |

### Not sites

Grepping for `send-keys` / `capture-pane` also finds these. None of them types into or reads
a pane:

* `chela/mergegate.py` / `plugin/hooks/mergegate.py`: `send-keys` is in `_TMUX_SPAWNS`, the
  list of tmux subcommands the Bash hook **denies** inside a dispatched run.
* `chela/orchestrator.py`, `chela/sessions.py`, `chela/inbox.py`, `chela/restore.py`,
  `chela/runtime_truth.py`: docstrings and messages only. `sessions.py` says it uses no
  `capture-pane` on purpose.
* `chela/e2e.py`: `send_key` there is an AES-GCM key, not a keypress. The scanner only counts
  bare names imported from `chela.messenger`, for exactly this reason.

The brief counted 18 modules for send-keys and 13 for capture-pane. Those numbers came from a
text grep, which also matches docstrings. The real sites are the rows above.

## Native features relied on

Checked against the installed Claude Code **2.1.330**. **[bin]** below means that bundle,
string-searched; running `claude` from a dispatched run is refused by chela's own hook.
Also checked against the version notes
already in the code. A minimum marked *not established* means the feature is present in
2.1.330 but the first version that had it was never recorded. Before a slice depends on such
a feature, it should check `claude --version` at runtime against 2.1.330, or measure an older
version.

| Feature | Used for | Minimum | Evidence |
|---------|----------|---------|----------|
| Peer-messaging Unix socket (`<cross-session-message>`, receiver's `crossSessionInbound`) | Bucket 1 transport | 2.1.224 | `messenger.py` module docstring (measured); `crossSessionInbound` and `cross-session-message` present in [bin] |
| Named arrival (`from-name` in the envelope) | The sender shows as `chela`, not anonymous | 2.1.283 | `messenger.py` CMX-380 note (measured) |
| `--messaging-socket-path <path>` | A deterministic, chela-owned socket per window | not established (used since CMX-223) | in [bin]'s flag list |
| `claude agents --json` | Native busy / idle / waiting (`agent_manager.session_status_map`) | not established | `command("agents")` in [bin]; chela calls it in production every tick |
| Interactive `claude "<prompt>"` (positional `[prompt]`) | Slice 2: the seed goes in at launch | not established | `.argument("[prompt]","Your prompt")` on the root command in [bin] |
| `--append-system-prompt` / `--append-system-prompt-file` | Persona at launch (already used); a seed file | not established | both in [bin] |
| `--remote-control <name>` | Launch-time RC name (CMX-460) | 2.1.280 | `config.py` note; in [bin] |
| `-n, --name <name>` | Slice 4: display name at launch ("shown in the prompt box, /resume picker, and terminal title") | not established | help text in [bin] |
| `--session-id <uuid>` | Pins the session for transcript lookup (already used by `spawn`) | not established | in [bin] |
| `UserPromptSubmit` hook | Proof that a typed prompt was submitted (`deliver_image`, slice 2) | 2.1.207 | `hooks.py` (measured) |
| `claude auth status --json` | Possible native "login expired" check (slice 3) | not established | `command("status")…option("--json")` under `auth` in [bin] |

**Gaps with no native equivalent** (so the pane read stays): the prompt's input mode
(`❯`/`!`/`#`), an unsent draft, whether a selector is on screen right now and which option is
highlighted, dialogs that fire no hook, renaming a running session, and an "exit this
session" command.

## Slices

These are recommended in this order. Each one is about 300 lines or less, and each comes with
a **negative control**: a test that is red on the old tmux path and green on the new one.
Dispatched agents have no Linear access, so the orchestrator files these on merge and adds
the issue IDs here.

### Slice 1: no silent tmux fallback for agent messages

**Done: CMX-482.** One PR (1a and 1b together). The rows above are updated.

* **Scope:** `messenger.send_message`, `rooms.post`, `rooms.flush_pending`, `inbox.deliver`,
  `inbox.send_resumes`, plus `main.cmd_drive`, which is different: it is tmux-only today and
never tries the socket, so it needs a **new** peer path (`send_peer` with `from` = the
driving window), not a removed fallback. When the socket can't be
  reached, return a distinct "unreachable" result, keep the item queued where a queue exists
  (rooms parking, inbox store, the resume outage), and say so loudly: an `event_log` entry and
  a non-zero `chela msg`/`chela drive` exit naming the window and the reason. Never call
  `send_tmux`. `dashboard.api_agents_msg` sends non-slash text as `deliver_prompt` (it is the
  user) and no longer relies on the fallback.
* **Negative control:** stub `send_peer` to return `handed_off=False` and stub `send_tmux`
  to record its calls. Assert it is **never** called and the result is "unreachable". On the
  old path the same test records one `send_tmux` call. For `cmd_drive`: assert `send_peer`
  is called and `send_tmux` is not; the old path calls `send_tmux` and never `send_peer`.
* **Acceptance:** `send_tmux` is reachable from no bucket-1 row in this doc (update the rows
  to `remove`/done). The `chela doctor` fallback warning becomes "unreachable: messages are
  held". If one PR would exceed 300 lines, split it into 1a (messenger, rooms, fleet) and
  1b (inbox, and `cmd_drive`'s new peer path).

### Slice 2: seed dispatched agents at launch

* **Scope:** `dispatcher._launch_agent` writes the rendered prompt to a run-private file
  under `$CHELA_DIR` (never the worktree) and launches `claude … "$(cat <file>)"`. Remove
  `_wait_for_ready` and the first-dispatch use of `_send_seed`. Confirm with the existing
  native `busy` check (`_seed_landed`). The watchdog re-nudge and `JUDGE_RETRY_MESSAGE` go as
  native peer messages (bucket 1). Guard the argument size: past a cap (`MAX_ARG_STRLEN` is
  128 KiB on Linux), send the seed as a native message once status reads `idle`, never with
  `send-keys`.
* **Negative control:** in a test of `_launch_agent` with tmux stubbed, assert no
  `send-keys` / `paste-buffer` argv carries the prompt text, and the launch argv carries the
  file reference. The old path pastes the prompt through `send_tmux`.
* **Acceptance:** `_wait_for_ready` is gone; its row and `_send_seed`'s row are updated; a
  real dispatch on the dogfood box goes `busy` without a single seed `send-keys`.

### Slice 3: native state reads in the watchdog

* **Scope:** after slice 2, `dispatcher.tick`'s idle-empty-prompt check becomes native:
  `idle` for `WATCHDOG_IDLE_MINUTES` and a transcript whose last entry is a finished assistant
  turn with no pending tool call. Drop `_pane_idle_empty_prompt` / `_pane_shows_activity`
  from that path. Measure `claude auth status --json` as the `_judge_watchdog` login check.
  Adopt it only if it costs less than the capture it replaces; otherwise record why the pane
  read stays.
* **Negative control:** an agent whose pane shows `❯` with an empty prompt but whose native
  status is `busy` (mid tool call) is not nudged, and the test asserts `_capture_pane` is not
  called on that path. The old path captures the pane.
* **Acceptance:** `dispatcher.tick`'s row changes to native. Every remaining bucket-3 row
  names its native gap.

### Slice 4: slash commands with a native equivalent

* **Scope:** (a) `agent_manager.stop_agent` stops typing an ungated `/exit` with Escape. It
  goes through `restart`'s gates (`idle`, empty draft, `interrupt` off) or signals the Claude
  process. (b) Launch every chela-started session that isn't Remote Control with
  `-n <window name>`, so its name is right from the start. First measure whether `--name`
  also reaches the Remote Control / desktop name. If it does, `rc_rename`'s launch-time
  marker can use it too.
* **Negative control:** (a) `stop_agent` on a `busy` window (or one with a draft) sends no
  keys. The old path sends Escape and `/exit`. (b) The launch argv carries `-n`; the old
  path has none.
* **Acceptance:** `stop_agent`'s row moves to gated `keep`. `rc_rename` / `suggest_names`
  stay as the only runtime `/rename` typers, behind their gates.

### Slice 5 (optional): dispatched agents never open a dialog no one can answer

* **Scope:** a `PreToolUse` hook that refuses `AskUserQuestion` inside a dispatched run
  (`CHELA_RUN` set), with a reason telling the agent to pick its own default. It replaces
  `dispatcher._dismiss_input_block`'s Escape. A permission prompt is out of scope: since
  CMX-478 it is never Escaped and alerts the inbox.
* **Negative control:** a dispatched-run AskUserQuestion is refused by the hook, and the
  watchdog sends no Escape. The old path sends Escape.
