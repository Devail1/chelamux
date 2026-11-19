# Trust & approval posture

> Written to satisfy Symphony SPEC §1 / §10.5's MUST that an implementation "document its
> chosen approval, sandbox, and operator-confirmation posture"; see
> `docs/archive/SYMPHONY_CONFORMANCE_2026-09-13.md` G6. Nothing here is new behavior: every claim
> below already exists in code or in another doc; this page exists so a conformance
> reader (or an operator) has one place to point at instead of four.

chela dispatches autonomous coding agents into git worktrees on the operator's own
machine, using the operator's own credentials. That is real access, so the posture below
answers three separate questions: **who can start an agent, what can it do while running,
and what does its output need before it changes anything real.**

## 1. Who can trigger a dispatch

- **`markdown` tracker** (`TODO.md`): gated by repo write access: whoever can add a line
  to the file can queue a dispatch (`chela/sources/markdown.py`).
- **`gh_issues` tracker**: gated by `tracker.require_label`. It is REQUIRED, and an unset
  value fails closed and loud rather than defaulting to "no gate"
  (`chela/sources/gh_issues.py:20-97`). Applying a label needs repo write/triage
  permission, so the check is enforced by GitHub, not by convention; before this gate
  existed, the source dispatched on *every* open issue, so on a public repo anyone who
  could open an issue could run code on the operator's box. `require_label: false` is
  accepted as a recorded, deliberate opt-out, never a silent default. `trusted_authors` is
  optional defence in depth on top of the label. That "no work" and "refusing to look" must
  not be indistinguishable is true for the *operator* today (a `config_error` logs an ERROR
  and surfaces in `chela doctor`); the dispatcher's own `config_error` reconcile path still
  returns `[]`, so a failed read is indistinguishable from an empty queue there. Tracked by
  G1 / cmx-363, in flight, not this doc's job to fix.

## 2. What a dispatched agent can do (execution / sandbox)

- **Default is a permission classifier, not blanket trust.** `resolve_agent_cmd` resolves,
  in order, an explicit per-workflow `agent.cmd` → the dashboard's Settings permission
  mode → the built-in default `claude --permission-mode auto`
  (`chela/dispatcher.py:405-443`). `auto` auto-approves safe operations and gates
  dangerous ones; `bypassPermissions` is a mode an operator can opt into but is never the
  shipped default: "reckless as an OSS default" (`dispatcher.py:291-297`).
- **No ambient MCP servers.** Every dispatched window (coding agent, rework, judge) runs
  with `--strict-mcp-config`, so it never inherits the operator's own interactive
  `~/.claude.json` `mcpServers` (`dispatcher.py:302-312`, `AGENT_BASE_CMD`).
- **The judge's model is a fixed floor, not a fleet setting.** `DEFAULT_JUDGE_MODEL` is
  pinned to a capable model and never follows the coding-agent Settings model, so a
  cheaper fleet default cannot quietly weaken the adversarial pass
  (`dispatcher.py:330-402`).
- **An agent's pane inherits an allowlisted environment, not the daemon's** (CMX-45).
  A dispatched window (coding agent, rework, judge) is opened inside the daemon's tmux
  session, and a tmux window inherits the server's *global* environment; on a pm2 install it is
  the daemon's own. Audited by name on the reference install (2026-10-11): about 60 names
  in the daemon env and the tmux global table: every `CHELA_*` setting `chela.env` sources
  (dashboard port, collab relay, notify URL, dispatch workflows, …), pm2's process
  description (`name`, `status`, `args`, `env`, `pm_*`, and a key named after the app such
  as `chela-daemon`), Node's IPC markers, the operator's prompt/theme variables and the
  desktop plumbing (`DISPLAY`, `DBUS_SESSION_BUS_ADDRESS`, WSL interop). Before the agent
  command is typed, `_launch_agent` sends one `unset` line into that pane naming **every**
  inherited name (the daemon's env plus the tmux global and session tables) that is not
  on `envutil.AGENT_ENV_ALLOW`:
  - kept: the shell basics (`PATH`, `HOME`, `USER`, `SHELL`, `LANG`, `LC_*`, `TZ`, `TMPDIR`),
    the terminal (`TERM`, `COLORTERM`, `TMUX`, `TMUX_PANE`), session plumbing
    (`XDG_*`, `DBUS_SESSION_BUS_ADDRESS` (`systemd-run --user` needs them for the memory
    slice), `DISPLAY`, `WAYLAND_DISPLAY`, `WSL*`), editors, tool switches (`COREPACK_*`,
    `UV_*`), and chela's own identity: `CHELA_DIR`, `CHELA_TMUX_SESSION`, `CHELA_WID`
    (exported after the strip). Claude Code's and `gh`'s credentials pass as the
    forwarded secrets (`envutil.FORWARDED_SECRET_VARS`), plus `CLAUDE_CONFIG_DIR` and
    `ANTHROPIC_BASE_URL`.
  - dropped: everything else, including every other `CHELA_*` setting. A name on the
    allowlist is still dropped when it is secret-shaped (`*_TOKEN`, …), a Claude session
    marker, or a pm2/Node leak. `CHELA_NOTIFY_URL` is dropped even when forwarded (CMX-115).
  - extending it: an operator names extra variables in `CHELA_CHILD_ENV_FORWARD`, the same
    knob that forwards a secret to a hook ([CONFIG.md](CONFIG.md)).

  Where this sits next to the other strips: `envutil.child_env` (CMX-390/425/21) is what a
  hook or a suite chela runs *itself* receives: the daemon env minus leaks, secrets and
  session markers, everything else passed. `envutil.scrub_tmux_secrets` unsets secrets,
  markers, server hazards (proxies, `TMUX_TMPDIR`) and, since CMX-45, every name that is
  not a valid shell variable (pm2's `chela-daemon`-shaped keys, which bash passes to children
  and cannot `unset`) from the tmux global table before any window opens. The allowlist is
  the narrowest of the three and applies to the agent pane only. Not covered: what the
  operator's own shell rc sets when the pane's shell starts, and `chela` subcommands run
  inside the pane, which re-read `$CHELA_DIR/chela.env` at import (so a `chela` process there
  still sees the operator's settings, including the notify URL; its notify calls are what
  CMX-115's test fence covers). Pinned by `tests/test_agent_env_strip.py`, which replays the
  exact typed lines through a real `bash`: a sentinel set in the daemon env is absent in the
  pane while `CHELA_WID` and `PATH` survive.
- **A worktree cannot escape into a workspace it doesn't own.** `workspace_escape` refuses
  to dispatch, loudly and not by silently relocating, when a non-default `CHELA_DIR`
  process's configured `workspace.root` resolves outside that `CHELA_DIR`
  (`chela/workflow.py:250-282`).
- **A shared memory ceiling bounds the box, not just one job**: `chela/memcap.py`
  (CMX-264); see `docs/RESOURCE_ISOLATION.md` for the incident that made a per-job cap
  provably insufficient.
- **A per-agent sandbox boundary is implemented and measured, but OFF for chelamux's own
  dispatch, so in practice its agents still run unsandboxed.** `chela/sandbox.py`
  (CMX-369, #518) launches an agent with a chela-owned Claude Code sandbox settings file:
  writes outside the worktree denied except the `uv` cache (so `~/.chela` is out of reach),
  a strict network allowlist (GitHub, PyPI, npm), no unsandboxed-command escape hatch, and
  the `gh` token file (`~/.config/gh/hosts.yml`) denied. The push and the PR open moved to
  the daemon (`chela request-push`) so a sandboxed agent never needs the token. It is
  **opt-in per workflow** (`agent.sandbox: true` in `WORKFLOW.md`, default off) and applies
  to the **coding and rework roles only**; the judge is never sandboxed
  (`sandbox_launch_arg`). The trial on chelamux (2026-09-15) proved the boundary holds but
  was reverted: about 110 tests need a real tmux socket and `api.telegram.org`, which the
  boundary denies, so every sandboxed run parked before `task-finished`. It stays off until
  those tests are socket-free; `WORKFLOW.md`'s `agent:` block records why. What was
  measured, and what the sandbox does and does not cover, is in
  [SANDBOX_BOUNDARY.md](SANDBOX_BOUNDARY.md). Isolating *shared panes* is a separate, still
  open blocker on running the orchestrator unattended; see "Shared-pane isolation" in
  `docs/ESCALATION_CONTRACT.md`.

## 3. What a dispatched agent's output needs before it changes anything real (approval)

Three independent layers, each narrower than the last. Not every PR meets all three before
merge: which ones it meets depends on its **risk tier** (`chela/risktier.py`, read from the PR's live file list; the full table is
"Risk tiers" in `docs/ESCALATION_CONTRACT.md`):

| Tier | Which PRs | What `chela merge` needs |
|---|---|---|
| **always-human** | any file on an ALWAYS-HUMAN path (`WORKFLOW.md`, `.github/workflows/**`, `plugin/**`, the merge gate's own modules, `scripts/release*`) | the operator's approved `--override`, even with a clean judge verdict |
| **core** | any core path (the dispatcher, the judge, the workflow loader, the sources, the merge and containment machinery) or an agent-instruction file (`CLAUDE.md`, `AGENTS.md`) | green CI and a clean judge verdict on the current head |
| **standard** | everything else: product and UI work | green CI; no per-PR judge. The hand-in runs `hand_in.lint_cmd` (skipped when unset) and one full suite, and the nightly sweep mutates what merged |
| **prose** | every file is docs, a changelog fragment or media | green CI |

A workflow can add paths to the always-human and core sets (`merge.always_human`,
`merge.core`), never remove a default. An unreadable or empty file list is never prose or
standard: it takes the core path.

**a. CI**: the mechanical baseline every PR must clear regardless of anything below.

**b. The judge** (`chela/judge.py`), an adversarial pass CI cannot run. It never merges
and never approves by opinion; "cannot verify" counts as neither pass nor fail. It
proposes mutations to the guards a PR claims to add, and chela itself applies each one,
proves the file changed, proves it still parses, re-runs the repo's own suite, and
restores it, so a guard that survives its own corruption is a fact, not a judgment (see the
module docstring). Findings that are taste rather than a mechanical fact become a PR
comment and can never send a run back.

The judge meets every **core** PR before merge. A **standard** PR meets the same mechanics
after merge instead: once a day the nightly sweep (`chela/sweep_nightly.py`) mutates the lines
standard-tier PRs landed on the base branch and files one tracker issue per surviving guard
category, so a weak guard there is filed after merge, not blocked before it. The sweep is a
backstop, not a gate: `sweep.enabled: false` turns it off, a night that ends CANNOT VERIFY or
with mutations UNRUN does not advance its covered window (the next night looks again), and a
line a later core PR rewrote is not swept twice. A **prose** PR has nothing to mutate.

**c. The merge gate** (`chela/contract.py::merge`), the only function allowed to run
`gh pr merge`, a pure function of `(run row, live GitHub facts)` with no `--force`:

1. the run exists and is `awaiting_review`, the only clause that reads the run row
   rather than live GitHub;
2. the base branch is the run's own dispatching workflow's declared
   `workspace.base_branch` (`dev` for chelamux itself), and is never
   `main`/`master`/`production`/… unless that workflow explicitly committed to exactly
   that branch;
3. the judge said `clean` against the PR's *current* head commit, read live from GitHub. A
   standard or prose PR carries the no-judge verdict the dispatcher stamped instead
   (`standard_no_judge`, `prose_only`), and the gate re-classifies the live diff to check it
   still fits that tier. A `clean` verdict is carried across a head move that touches none of
   the PR's own files, such as a merge from the base branch: `judge_sha` is re-stamped to the
   new head, not re-judged (`chela/verdictcarry.py`);
4. CI is green;
5. GitHub reports the PR open and `MERGEABLE`;
6. **if the actor is the auto-launched orchestrator** (`$CHELA_ACTOR ==
   auto-orchestrator`), a human's attended-lease must be live. A human's own `chela merge`
   carries no actor stamp and needs no lease, because the human *is* the attendance
   (`chela/personas/lease.py`);
7. the PR's live file list is readable and touches no always-human path.

Any miss refuses, naming the exact clause. Every merge is logged with its justification
to the event log.

**Escalation is the fail-closed default, not an edge case.**
`docs/ESCALATION_CONTRACT.md` sorts every orchestrator decision into AUTONOMOUS / ESCALATE
/ NEVER, and ambiguity between tiers resolves upward: "unknown" is a `cannot_verify`, and a
`cannot_verify` always goes to a human (`chela/contract.py::escalate`, `chela escalate`).
Merging to `main`, making the repo public, and executing agent-authored text as a command
are all in the NEVER tier; no standing grant reaches them.

**Two requests reach past the gate, and only a human's tap completes either.**
`chela merge --override --reason …` (skip the judge clause; every other clause is
re-checked) and `chela release X.Y.Z` (push the release commit to `main`, its tag and `dev`)
each file a request bound to what they were cut from: the PR's head, or the `origin/dev`
commit. Nothing moves until an operator approves it on Telegram, on the dashboard or with
`chela merge-approve`; a new push expires it. Approval is human-only: `merge-approve` refuses
inside a Claude Code session or the orchestrator's process, and the merge-gate hook denies a
session's `python` call into the approval path (`chela/overrides.py`, `chela/release.py`).
See `docs/RUNS.md` ("Overriding the judge", "Releasing").

**A dashboard decision must prove the operator's browser, not loopback** (CMX-392). Every
dispatched agent runs on the same machine as the dashboard, so "the request came from
`127.0.0.1`" proves nothing. Before CMX-392 an agent could approve its own override, and so
merge its own always-human PR, with one `urllib` POST: no Origin header read as same-origin,
the merge-gate hook never saw a `curl`, and the dashboard channel skipped the agent check.
Now (`chela/dashboard/humanproof.py`):

- a page the browser loads (`/`, `/override/<id>`) sets an `HttpOnly`, `SameSite=Lax`
  session cookie and embeds that session's token, an HMAC under a key that exists only in
  the dashboard process's memory;
- a decision must carry the cookie, the matching token (`X-Chela-Session-Token`, or the
  confirm form's `session_token` field) and an `Origin` that is exactly the dashboard's
  `host:port` (over `http` or `https`, since the tailnet proxy terminates TLS). A missing
  Origin is refused, since every browser sends one on a POST, and a `Sec-Fetch-Site` other
  than `same-origin` is refused too. Any miss is a 403 and nothing is decided;
- `chela.overrides.decide` no longer exempts the `dashboard` channel by name: only a call
  the dashboard verified (`browser_proven=True`) skips the agent check. An unproven dashboard
  caller is refused outright. Telegram is unchanged: a button tap is its own proof.

Why a token rather than a mandatory Telegram confirm: Telegram is optional, and a
dashboard-only operator (CMX-103) would be left with no way to approve.

⚠️ **What this does not stop.** A process running as the operator's Unix user can still
emulate a browser on purpose: GET `/`, keep the cookie it sets, read the token from the
page, forge the Origin. That is two plain HTTP requests, not a hard barrier. Nothing on
loopback can tell that apart from a real browser; only an OS boundary can (the agent sandbox
in §2, whose strict network allowlist does not include loopback). The token closes the blind single POST, and makes what
remains an explicit impersonation of the operator's browser rather than an API call that
happens to work. The same holds in-process: `browser_proven` is an argument any Python caller
can pass. Until chelamux's own agents run sandboxed, or approval moves to a channel an agent
cannot drive (a Telegram tap), that is the honest ceiling.

**The merge-gate hook is a speed bump, never the gate.** `chela/mergegate.py` denies a
session's `chela merge-approve` and `chela share-requests approve`, a `curl`/`wget`/`http`/`xh`
to `/override/` or `/api/share-requests/…/approve`, and a `python` call into
`chela.overrides.decide`. It matches strings in argv, so any HTTP client it does not name
(`python -c 'urllib…'`, `node -e 'fetch(…)'`, a script file) walks past it, and it does not
look at the card's Merge route at all. It stays because it catches the obvious attempt and
says why; the server-side proof above is what refuses.

**Which dashboard routes need the proof** (audit, CMX-392). Every mutating route in
`chela/dashboard/app.py` was checked for the same shape: does it decide something on the
operator's behalf that an agent is refused elsewhere?

| Route | Status |
|---|---|
| `POST /api/override/<id>/approve\|deny`, `POST /override/<id>` (the confirm page) | **proof required.** Approving an override merges a PR past the judge, or ships a release |
| `POST /api/share-requests/<id>/approve\|deny\|revoke` | **proof required.** An approval grants a guest access to a terminal |
| `POST /api/dispatcher/runs/<id>/merge` (the card's Merge) | **proof required.** It runs `gh pr merge` itself, which the merge-gate hook denies an agent; without the proof an agent could merge its own PR through it |
| `POST /api/config` (agent permission mode, model), `/api/config/dispatch`, `/api/config/concurrency`, `/api/config/timing` | **should get it next.** Setting the permission mode loosens every future dispatch |
| `POST /api/update/apply` | **should get it next.** It pulls and restarts every chela service |
| `POST /api/schedules`, `PATCH`/`DELETE /api/schedules/<id>` | **should get it next.** A schedule runs a prompt on a timer |
| `POST /api/dispatcher/backlog/promote`, `/api/dispatcher/init`, `/api/dispatcher/linear/issue` | **should get it next.** Each writes to a repo or tracker as the operator |
| `POST /api/term/<wid>/share`, `/api/term/<wid>/share-mode` | **should get it next.** The unsandboxed mode's typed-window-name confirm is readable by an agent |
| `POST /api/dispatcher/pause\|resume`, `/api/dispatcher/runs/<id>/close`, `/api/dispatcher/delete`, `/api/orchestrator/subscribe\|release` | lower risk: disruptive, but no gate is skipped |
| terminal input (`/term/<wid>/…`, `/api/term/key`, `/paste`, `/paste-image`, `/upload`, `/watch`, `/grid`, `/api/agents/msg`, `/broadcast`, `/trigger`) | **no added power:** an agent on the box can already type into any pane with `tmux` or `chela msg` |
| agent lifecycle (`/api/agents/start`, `stop`, `restart`, `rediscover`, `spawn`, `spawn-sandboxed`, `kill`, `<wid>/rename`, `<wid>/title`, `suggest-names`) | **no added power:** the same `tmux` and `chela` CLI calls are open to an agent |
| sidebar archive (`/api/sidebar/archive`, `unarchive`, `archive/migrate`) and layout (`/api/sidebar/layout`), launcher pins (`/api/launcher/pin`, `unpin`, `forget`), restore (`/api/restore/dismiss`, `undismiss`, `resume`), rooms (`/api/rooms/join\|leave`), knowledge export (`/api/knowledge/export`) | **no added power:** each edits the operator's own view or files an agent can already write |
| `POST /hooks/<event>`, `POST /api/inbox/denial` | **agent-posted by design:** agents' own hooks post these, so no browser proof is possible |

The "should get it next" rows are tracked as one follow-up issue; until it lands, treat each
as reachable by any process on the box.

**Two unattended paths exist, and both are strictly opt-in, off by default, and loud when
on** (`capabilities.py`'s `warn_when_on` logs a WARNING, not an INFO, on every boot they
are enabled): `CHELA_AUTO_MERGE` runs the *same* `contract.merge` gate above on a timer
with nobody attending (`chela/config.py:870-893`); `CHELA_AUTO_UPDATE` does the equivalent
for `chela update`'s already safety-railed `apply()` (`chela/config.py:896-905`). Neither
loosens the gate it rides on; both require an operator who read the tradeoff to turn them
on for themselves.

## 4. What becomes public

On a public repo, a dispatched brief, its PR, and (for a `gh_issues` tracker) the
triggering issue's body are all public by construction. The `require_label` gate in §1 is
what actually stands between a stranger opening an issue and code executing on the
operator's machine; nothing downstream of dispatch is a substitute for it. See
`docs/archive/SYMPHONY_CONFORMANCE_2026-09-13.md` G2 for the fuller tradeoff if a workflow ever
migrates its tracker from `markdown` to `gh_issues`.

## Where each of these is enforced, for a conformance reader

| Posture question | Enforced in | Documented in |
|---|---|---|
| Who may trigger a dispatch | `chela/sources/gh_issues.py`, `chela/sources/markdown.py` | this doc §1 |
| What a running agent may do | `chela/dispatcher.py::resolve_agent_cmd`, `chela/dispatcher.py::_strip_agent_env_cmd` + `chela/envutil.py::AGENT_ENV_ALLOW` (env allowlist), `chela/workflow.py::workspace_escape`, `chela/memcap.py`, `chela/sandbox.py` (opt-in, off for chelamux) | this doc §2, `RESOURCE_ISOLATION.md`, `SANDBOX_BOUNDARY.md` |
| What approves a merge | `chela/risktier.py` (tier), `chela/judge.py` (core, before merge), `chela/sweep_nightly.py` (standard, after merge), `chela/verdictcarry.py` (carry), `chela/contract.py::merge` | this doc §3, `ESCALATION_CONTRACT.md` |
| That a dashboard decision is a human's | `chela/dashboard/humanproof.py`, `chela/overrides.py::decide` (`browser_proven`); `chela/mergegate.py` is advisory only | this doc §3 |
| What a human must decide instead of the orchestrator | `chela/contract.py::escalate` | `ESCALATION_CONTRACT.md`'s decision taxonomy |

This is chela's answer to **SPEC 10.5** ("Each implementation MUST document its chosen
approval, sandbox, and operator-confirmation posture") and to **SPEC 1**'s parallel
requirement to document a trust and safety posture explicitly. Symphony itself takes no
position on approval, sandbox, or review behavior; chela's chosen position is fail-closed
escalation, a mechanical (not opinion-based) merge gate, and unattended paths that are
off by default and loud when turned on.
