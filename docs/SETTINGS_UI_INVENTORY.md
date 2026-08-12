# Settings UI inventory — the input the tabbed-modal decision was missing

Status: **input doc, not a design** (CMX-207). Liav proposed turning the Settings drawer
into a tabbed modal. Before picking a tab layout, this inventories every knob a tabbed
modal would actually have to hold, because "the drawer feels cramped" and "the drawer has
almost nothing in it" are both true at once, and only one of them is a container problem.

## The gap, measured

```bash
grep -rhoE 'os\.environ(\.get)?\(["'"'"']CHELA_[A-Z0-9_]+["'"'"']|os\.environ\[["'"'"']CHELA_[A-Z0-9_]+["'"'"']\]|os\.getenv\(["'"'"']CHELA_[A-Z0-9_]+["'"'"']' chela/ \
  | grep -oE 'CHELA_[A-Z0-9_]+' | sort -u | wc -l
```

**40** (was 58, then 49 after CMX-217 wired the 9-strong "Daemon loop intervals" group
below through `chela.config.dashboard_setting()`, its precedence layer — CMX-220 then
wired the 9-strong "Dispatch / judge / critic policy" group the same way (CMX-264 then
added a tenth member, `memory_slice_budget_bytes`, straight onto that same registry, so it
was never a literal read to begin with), so those 18 are no longer *literal*
`os.environ.get("CHELA_…")` call sites — see "WIRED" under Groups 2
and 3) — every literal `CHELA_*` name a Python module in `chela/` reads straight off
`os.environ`.
`tests/test_settings_inventory.py::test_inventory_matches_env_reads` re-runs this scan and
diffs it against the table below on every `pytest` run, so the count can't go stale the way
the README config table twice has (CMX-…, see `docs/CONFIG.md` history).

The Settings drawer (`chela/dashboard/static/js/nav.js`) writes exactly **two** of them
back to a server-side store: `chela.saveProjectsDir()` → `CHELA_PROJECTS_DIR` and
`chela.applyUpdate()` (a one-shot action, not a knob, but the nearest thing to
`CHELA_AUTO_UPDATE` the UI offers). Everything else in the drawer today — theme, terminal
font/size, collab display name, review-toast muting — is a **client-side `localStorage`
preference**, not a `CHELA_*` knob at all; `setAgentPermissionMode`/`setAgentModel` write to
`userconfig.py`'s `config.json`, which also isn't a `CHELA_*` env var.

**⛔ The container is not the bottleneck.** Two controls fit in a drawer with room to
spare; tabs would be empty rooms. The real question a redesign answers is which of the 58
are worth a control at all, and `chela/userconfig.py` already answers *half* of that
question in a doc-comment nobody reading the drawer's HTML would find:

> Distinct from `chela.config`, which reads env/CLI at process start and owns
> **trust-boundary settings** (bind host, terminal exposure, notify URL). This holds only
> **non-security preferences** the dashboard may write at runtime … the dashboard is
> loopback + no-auth, so it must never be able to rewrite the security boundary.

So this isn't "58 candidates for tabs." It's 58 knobs, most of which either (a) already
have a principled reason to stay env-file-only, or (b) nobody has looked at yet because
there was nowhere to put them.

## Method and scope

The 58 are **direct, literal `os.environ.get("CHELA_…")` / `os.environ["CHELA_…"]` /
`os.getenv("CHELA_…")` reads inside `chela/**/*.py`** — the exact set a contributor sees by
grepping the package. Three adjacent things are real knobs but sit outside that literal
scan, and matter to a redesign precisely because they're invisible to it:

- **`CHELA_ACTOR`** — read as `os.environ.get(config.ACTOR_ENV)` (`chela/contract.py:86`),
  an indirection through the `ACTOR_ENV` constant in `config.py`. Deliberately excluded
  from any settings surface: it's an actor stamp a launched orchestrator process exports
  into its own window, never a human preference (see `[[feedback_orchestrator...]]`-style
  guidance in `chela/config.py:345-352`).
- **Seven ttyd/terminal-wall knobs read only by `scripts/agent-terminals.sh`** (shell, not
  Python, so the Python-side grep never sees them): `CHELA_TERM_BASE`, `CHELA_TERM_POLL`,
  `CHELA_TERM_MAX_CLIENTS`, `CHELA_TERM_FONT`, `CHELA_TERM_FONTSIZE`, `CHELA_TERM_THEME`,
  `CHELA_TERM_BACKOFF_MAX`. A "Terminal wall" tab that only covers the 5 Python-side knobs
  (`CHELA_TERMINALS_ENABLED/EXPOSE/TERM_COLS/TERM_ROWS/WALL_TILE_DISPATCHED`) would still
  be missing the port range, font, theme, and client cap the supervisor itself reads.

Neither group is in the 58, and neither should be added to the count above (that count is
useful precisely because it's grep-reproducible from one place); they're called out here so
a redesign doesn't quietly drop them.

## The 58, grouped for tab candidacy

Grouping is by what a person changing the value is trying to do, not by source file. Each
row: default, whether the drawer surfaces it **today** (all "No" except the one marked),
and a mutability class:

- **`hot`** — read per-call (not latched at import); config.py's own comments say so
  explicitly for most of these ("read per call, never latched at import"). Safe to write
  from a running dashboard with no restart.
- **`restart`** — read at import or binds a socket/process at startup; a UI control would
  need to write the file and say "takes effect after restart," same as the drawer's
  existing Update section already models.
- **`trust-boundary`** — `userconfig.py`'s own exclusion list, or the same shape (loosens
  what an unauthenticated-loopback dashboard process may do to itself or to what it merges/
  updates/exposes). Candidate for **staying env-file-only** even in a redesigned UI, not a
  gap to close.
- **`identity`** — injected into a specific process's environment by chela itself (spawn
  args, actor stamps); not a human preference, not a UI candidate.
- **`internal-path`** — overrides where chela's own state files live. Exists for tests and
  exotic multi-instance deploys; asking "where should chela.env live" from inside a UI that
  itself depends on `CHELA_DIR`/`CHELA_ENV_FILE` to exist is circular for two of these, and
  the rest are rarely-touched escape hatches, not settings a redesign needs to prioritize.

### 1. Bootstrap / identity (3) — `internal-path`, pre-UI by construction

| Variable | Default | Notes |
|---|---|---|
| `CHELA_DIR` | `~/.chela` | Where the env file, `config.json`, `scheduler.db`, worktrees live. The dashboard reads this to find itself; it can't also be edited by it. |
| `CHELA_ENV_FILE` | `$CHELA_DIR/chela.env` | Which file *is* the config. Empty disables the file entirely. |
| `CHELA_TMUX_SESSION` | `chela` | The tmux session orchestrated. Auto-derives from the caller's own pane if unset — a UI toggle would fight that fallback. |

### 2. Daemon loop intervals (9) — WIRED (CMX-217), the Timing tab

**✅ Done.** These were the strongest tab candidate (all `hot`, no trust-boundary
concerns, no validation beyond "is it a number") — CMX-217 built the general
dashboard-setting precedence layer (`chela.config.dashboard_setting()`: the env var
beats userconfig.json beats the built-in default — the dashboard binds loopback with no
auth, so a value it wrote must never silently outrank an operator's explicit `export
CHELA_…`, same rule `projects_dir` / `agent_permission_mode` / `agent_model` now follow,
generalised into a registry — `chela.config.TIMING_KNOBS`) and proved it end to end on
this exact group: a "Timing" section in the Settings drawer, backed by `GET`/`POST
/api/config/timing`. A knob whose env var is set reports `source: "env"` and the drawer
disables that row rather than offering an edit the env value would silently override.

Because each is now read via `config.dashboard_setting()` (the env-var name is a runtime
argument, not a literal `os.environ.get("CHELA_…")` call), they no longer show up in the
raw-reads scan above — they are still real, settable env vars (unset falls through to
them exactly as before), just no longer *only* reachable that way. Not a table row here
on purpose, so `test_inventory_matches_env_reads` doesn't expect them back as literal
reads: `CHELA_SCHEDULER_POLL_INTERVAL` (daemon tick, s, default `30`),
`CHELA_CAPTURE_INTERVAL_SECONDS` (context-snapshot cadence, s, default `300`),
`CHELA_CACHE_STALE_SECONDS` (skip statusLine caches older than this, s, default `7200`),
`CHELA_CONTEXT_RETENTION_DAYS` (snapshot pruning window, days, default `30`),
`CHELA_DISPATCH_TICK_INTERVAL` (dispatcher tick fallback, s, default `60`),
`CHELA_STATUS_CMD_TIMEOUT_S` (`claude agents --json` timeout, s, default `45.0` — see
CMX-179; the dashboard write path rejects anything below `45.0`, the measured
cold-start floor), `CHELA_STATUS_TTL_S` (status-cache TTL,
s, default `30.0`), `CHELA_DOCTOR_CHECK_INTERVAL` (self-audit cadence, s, default
`3600`), `CHELA_DEFAULT_CONTEXT_WINDOW` (fallback context-window size, tokens, default
`200000`).

Two of the nine (`CHELA_STATUS_CMD_TIMEOUT_S` / `CHELA_STATUS_TTL_S`) are resolved once
at `chela/agent_manager.py` import, same as they always were — a dashboard write to
either needs that process restarted; the other seven are read per call and take effect
on the next tick/request with no restart.

### 3. Dispatch / judge / critic policy (10) — WIRED (CMX-220 + CMX-264), the Dispatch tab

**✅ Done.** The second-strongest tab candidate — unlike Group 2, not every member is
`hot`: four of the ten are latched at some module's own import (the judge/critic
fleet-wide kill switches, the dispatcher's own workflow list, the autonomous merge-base
fallback), so a dashboard write to one of those needs the daemon/dashboard restarted to
take effect, same shape `status_cmd_timeout_seconds`/`status_ttl_seconds` already modeled
in Group 2. CMX-220 built `chela.config.DISPATCH_KNOBS` on the same
`chela.config.dashboard_setting()` precedence layer Group 2 proved, generalised past
"every knob is a plain positive number" — this group mixes a bool pair, two free-text
knobs (one, `merge_base`, with an extra branch-name safety check since it feeds
`chela.contract`'s autonomous-merge fallback — the merge-safety gate itself,
`chela.contract.FORBIDDEN_BASES`/the NEVER-line check, is unconditional and untouched by
this), and a K/M/G/T-suffixed size pair (`worktree_disk_budget_bytes`, and CMX-264's
`memory_slice_budget_bytes`) — plus a "Dispatch" section in the Settings drawer, backed by
`GET`/`POST /api/config/dispatch`.

Because each is now read via `config.dashboard_setting()` (or, for the four latched
elsewhere, via `config.dispatch_value()`/a direct `dashboard_setting()` call at that
module's own import), they no longer show up in the raw-reads scan above — they are still
real, settable env vars (unset falls through to them exactly as before), just no longer
*only* reachable that way. Not a table row here on purpose, so
`test_inventory_matches_env_reads` doesn't expect them back as literal reads:
`CHELA_DISPATCH_WORKFLOWS` (colon-separated `WORKFLOW.md` paths, default empty — empty
means dispatcher off, deliberately, see `docs/CONFIG.md` "a config is not a capability";
**restart_required**), `CHELA_MAX_REWORKS` (rework cap before a run escalates to
`needs_human`, default `2`, `0` allowed — disables rework), `CHELA_JUDGE` (fleet-wide judge
kill switch, default `true`; **restart_required**), `CHELA_JUDGE_MAX_UNKNOWN_RETRIES`
(retries on a `cannot_verify` judge verdict, default `2`, `0` allowed), `CHELA_CRITIC`
(fleet-wide critic/advisory-review kill switch, default `true`; **restart_required**),
`CHELA_WORKTREE_DISK_BUDGET` (byte/size-suffixed disk ceiling per worktree root, default
unset/off), `CHELA_MEMORY_SLICE_BUDGET` (byte/size-suffixed ceiling on the SHARED cgroup
slice every dispatched agent and judge launches into — CMX-264, `chela/memcap.py`; a
per-job cap does not bound the box, only a shared one does, see
`docs/RESOURCE_ISOLATION.md`; default unset/off, and further gated on a working
`systemd --user` session regardless of the knob), `CHELA_MERGE_BASE` (fallback autonomous
base branch — per-workflow `base_branch` still wins; the NEVER-list is never overridable
by this, default `dev`; **restart_required**), `CHELA_GATE_WAIT_S` (how long a
`PermissionRequest` gate waits for a tap, default `90.0`s, `0` allowed — never wait),
`CHELA_GATE_MAX_WAITS` (concurrent gate-wait slots, default `8`, floor `1` — a
`BoundedSemaphore` cannot be sized `0`).

Four of the ten (`CHELA_DISPATCH_WORKFLOWS`/`CHELA_JUDGE`/`CHELA_CRITIC`/`CHELA_MERGE_BASE`)
are resolved once at their owning module's import (`chela/config.py` for the first three,
`chela/contract.py` for the fourth) — a dashboard write to any of those needs that process
restarted; the other six (`chela/config.py`'s `max_reworks()`/
`judge_max_unknown_retries()`/`worktree_disk_budget_bytes()`/`memory_slice_budget_bytes()`,
`chela/gateanswer.py`'s `wait_budget()`/`max_waits()`) are read per call and take effect on
the next tick/request.

### 4. Unattended-risk switches (3) — `trust-boundary`, keep env-file-only

| Variable | Default | Notes |
|---|---|---|
| `CHELA_AUTO_MERGE` | `false` | Fully-unattended merge sweep — opt-in risk, `docs/ESCALATION_CONTRACT.md` |
| `CHELA_AUTO_UPDATE` | `false` | Fully-unattended self-update sweep — opt-in risk, same doc |
| `CHELA_ORCHESTRATOR` | `false` | Auto-launches the embedded orchestrator persona, which holds `chela merge` authority |

These three are the ones `userconfig.py`'s doc-comment is warning about by name. A
checkbox is a worse UX for "I read the escalation contract and accept the risk" than a
one-line env edit — a checkbox implies casualness these three should not have. If a
redesign surfaces them at all, it should be **read-only status** (what the drawer's
Connections & Status section already does for other facts), not a write control.

### 5. Notifications / inbox (9) — mixed

| Variable | Default | Class | Notes |
|---|---|---|---|
| `CHELA_NOTIFY_URL` | empty | `trust-boundary`-adjacent | Per-install; an unauthenticated ntfy topic is a shared secret (`docs/CONFIG.md`) |
| `CHELA_NOTIFY_KIND` | auto | `hot` | Force ntfy/telegram/webhook |
| `CHELA_NOTIFY_CHAT_ID` | empty | `hot` | Telegram chat id if not in the URL |
| `CHELA_NOTIFY_INTERVAL` | `20` | `hot` | Pane-scan cadence (s) |
| `CHELA_NOTIFY_TITLE` | fixed string | `hot` | Notification title |
| `CHELA_INBOX_ENABLED` | `true` | `hot` | Decisions-inbox kill switch |
| `CHELA_INBOX_ALARM_GRACE_SECONDS` | `120` | `hot` | Grace before an undeliverable address pages |
| `CHELA_INBOX_FILE` | `$CHELA_DIR/inbox.json` | `internal-path` | |
| `CHELA_ORCHESTRATOR_WID` | empty | `identity` | Pins the inbox target window; env pin carries no epoch/session (see `chela/inbox.py`) |

### 6. Telegram relay behavior (4) — `hot`

| Variable | Default | Notes |
|---|---|---|
| `CHELA_SHOW_TOOL_CALLS` | `false` | Relay tool calls too (noisy) |
| `CHELA_STATUS_LINE` | `true` | Live self-editing "working…" status message per topic |
| `CHELA_TELEGRAM_BIND_DISPATCHED` | `false` | Give dispatcher-spawned agents a topic eagerly vs lazily |
| `CHELA_TELEGRAM_BINDINGS` | `$CHELA_DIR/telegram-bindings.json` | `internal-path` — override where bindings persist |

### 7. Dashboard bind (2) — `trust-boundary`, keep env-file-only

| Variable | Default | Notes |
|---|---|---|
| `CHELA_DASH_HOST` | `127.0.0.1` | Baked into the rendered hooks-plugin manifest as a literal URL — see `docs/CONFIG.md`'s port section for why this one is unusually load-bearing |
| `CHELA_DASHBOARD_PORT` | `5001` | Same |

`userconfig.py` names this pair explicitly as the reason it refuses to hold "the bind
host." A dashboard writing its own bind host is a process editing the boundary that makes
"loopback + no-auth" a safe default in the first place.

### 8. Terminal wall (5 of the 58; +7 shell-only, see above) — mixed

| Variable | Default | Class | Notes |
|---|---|---|---|
| `CHELA_TERMINALS_ENABLED` | `true` | `restart` | ttyd wall on/off |
| `CHELA_TERMINALS_EXPOSE` | `false` | `trust-boundary` | Serve the writable wall on a non-loopback bind — RCE risk, opt-in |
| `CHELA_TERM_COLS` | `120` | `hot` | Shared collab grid geometry (columns) |
| `CHELA_TERM_ROWS` | `30` | `hot` | Shared collab grid geometry (rows) |
| `CHELA_WALL_TILE_DISPATCHED` | `false` | `hot` | Give dispatcher-spawned agents a full tile eagerly vs minimized |

A "Terminal wall" tab is the clearest case where scoping to the Python-side 58 alone would
under-deliver: the shell-only 7 (font, theme, port base, poll interval, backoff, max
clients) are exactly the kind of thing a person opening a "Terminal wall" tab expects to
find there, and today they don't exist in any UI or even `docs/CONFIG.md`'s table.

### 9. Collaboration (2) — mixed

| Variable | Default | Class | Notes |
|---|---|---|---|
| `CHELA_COLLAB` | `true` | `hot` | Presence kill switch |
| `CHELA_COLLAB_RELAY` | empty | `hot` | The one relay-shaped value `docs/CONFIG.md` says **is** meant to be shared across installs, unlike `CHELA_NOTIFY_URL` |

### 10. Launcher / agent identity (4) — mixed

| Variable | Default | Class | Notes |
|---|---|---|---|
| `CHELA_AGENT_CMD` | `claude` | `restart` | Launch command for the dashboard Start button |
| `CHELA_PROJECTS_DIR` | `~/projects` | `hot` | **Already dashboard-writable today** — `userconfig.get("projects_dir")` wins over this, and it's the drawer's other live control |
| `CHELA_IGNORE_WINDOWS` | empty | `hot` | Comma-separated window names hidden from discovery |
| `CHELA_WID` | none | `identity` | Injected into every dispatched window's env; not a preference |

### 11. Internal state file paths (8) — `internal-path`, low priority

| Variable | Default |
|---|---|
| `CHELA_EVENTS_FILE` | `$CHELA_DIR/events.jsonl` |
| `CHELA_EVENTS_RING` | `2000` |
| `CHELA_EVENTS_MAX_BYTES` | `8 MiB` |
| `CHELA_EVENTS_KEEP` | `3` |
| `CHELA_ROOMS_FILE` | `$CHELA_DIR/rooms.json` |
| `CHELA_SEED_CONFIRM_TIMEOUT_SECONDS` | `8.0` |
| `CHELA_SEED_CONFIRM_POLL_INTERVAL` | `1.0` |
| `CHELA_SEED_RESEND_SETTLE_SECONDS` | `1.0` |

The three `SEED_*` knobs exist so the test suite can collapse them to ~0 (`chela/
dispatcher.py:115-125`) — they're a test-performance escape hatch, not an operator-facing
setting. The `EVENTS_*`/`ROOMS_FILE` group is path/sizing overrides for chela's own event
log, useful for an exotic deploy, not a redesign priority.

## What this means for the tab decision

Counting only groups where `hot`/`restart` knobs plausibly outnumber `trust-boundary`/
`internal-path`/`identity` ones — i.e. groups worth a **write** control, not just a status
readout:

| Candidate tab | Writable knobs | Today |
|---|---|---|
| Daemon intervals (Timing) | 9 | **9** (CMX-217) |
| Dispatch / judge / critic | 10 | **10** (CMX-220 + CMX-264) |
| Notifications | ~6 of 9 | 0 |
| Telegram | 3 of 4 | 0 |
| Terminal wall (+ the 7 shell-only) | ~9 of 12 | 0 |
| Collaboration | 2 | 0 |
| Launcher | 2 of 4 | **1** (`CHELA_PROJECTS_DIR`) |

That's roughly **40 legitimate write-candidates**, not 2 — the redesign's premise holds,
just not for the reason "the drawer is a cramped container." The other ~18 (bootstrap,
unattended-risk, bind host, most internal-paths, identity stamps) have a standing reason
in this codebase's own comments to stay env-file-only, and a redesign that turns *those*
into checkboxes would be quietly loosening a trust boundary `userconfig.py` was written to
protect. A tabbed modal is the right container for the ~40; it should render the other ~18
as read-only facts (extending the existing Connections & Status pattern) rather than skip
them or make them editable.

**CMX-217** built the precedence layer the whole ~40 count depends on
(`chela.config.dashboard_setting()` + a per-tab knob registry, e.g.
`chela.config.TIMING_KNOBS`) and proved it on the cheapest group — Daemon intervals,
renamed "Timing" in the UI. **CMX-220** reused it for the second-cheapest group —
Dispatch/judge/critic, "Dispatch" in the UI (`chela.config.DISPATCH_KNOBS`) — and
generalised the registry past "every knob is a plain positive number" (a bool pair, free
text, a K/M/G/T size) and past "every knob lives in `config.py`" (`chela.contract`'s
`AUTONOMOUS_BASE`, `chela.gateanswer`'s `wait_budget()`/`max_waits()`). The other ~22
(Notifications, Telegram, Terminal wall, Collaboration, the remaining Launcher knobs) are
now each a registry entry + an API route + a drawer section away, not a new precedence
design — that was the actual gap this doc originally measured.
