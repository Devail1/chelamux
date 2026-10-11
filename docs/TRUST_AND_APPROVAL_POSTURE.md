# Trust & approval posture

> Written to satisfy Symphony SPEC §1 / §10.5's MUST that an implementation "document its
> chosen approval, sandbox, and operator-confirmation posture" — see
> `docs/archive/SYMPHONY_CONFORMANCE_2026-09-13.md` G6. Nothing here is new behavior: every claim
> below already exists in code or in another doc; this page exists so a conformance
> reader (or an operator) has one place to point at instead of four.

chela dispatches autonomous coding agents into git worktrees on the operator's own
machine, using the operator's own credentials. That is real access, so the posture below
answers three separate questions: **who can start an agent, what can it do while running,
and what does its output need before it changes anything real.**

## 1. Who can trigger a dispatch

- **`markdown` tracker** (`TODO.md`): gated by repo write access — whoever can add a line
  to the file can queue a dispatch (`chela/sources/markdown.py`).
- **`gh_issues` tracker**: gated by `tracker.require_label`. It is REQUIRED, and an unset
  value fails closed and loud rather than defaulting to "no gate"
  (`chela/sources/gh_issues.py:20-97`). Applying a label needs repo write/triage
  permission, so the check is enforced by GitHub, not by convention — before this gate
  existed, the source dispatched on *every* open issue, so on a public repo anyone who
  could open an issue could run code on the operator's box. `require_label: false` is
  accepted as a recorded, deliberate opt-out, never a silent default. `trusted_authors` is
  optional defence in depth on top of the label. That "no work" and "refusing to look" must
  not be indistinguishable is true for the *operator* today (a `config_error` logs an ERROR
  and surfaces in `chela doctor`); the dispatcher's own `config_error` reconcile path still
  returns `[]`, so a failed read is indistinguishable from an empty queue there — tracked by
  G1 / cmx-363, in flight, not this doc's job to fix.

## 2. What a dispatched agent can do (execution / sandbox)

- **Default is a permission classifier, not blanket trust.** `resolve_agent_cmd` resolves,
  in order, an explicit per-workflow `agent.cmd` → the dashboard's Settings permission
  mode → the built-in default `claude --permission-mode auto`
  (`chela/dispatcher.py:405-443`). `auto` auto-approves safe operations and gates
  dangerous ones; `bypassPermissions` is a mode an operator can opt into but is never the
  shipped default — "reckless as an OSS default" (`dispatcher.py:291-297`).
- **No ambient MCP servers.** Every dispatched window (coding agent, rework, judge) runs
  with `--strict-mcp-config`, so it never inherits the operator's own interactive
  `~/.claude.json` `mcpServers` (`dispatcher.py:302-312`, `AGENT_BASE_CMD`).
- **A dispatched agent inherits an environment ALLOWLIST, not the daemon's env** (CMX-45).
  An agent's pane starts from the tmux server's global environment, which is the daemon's
  world: an audit by name on 2026-10-11 found the daemon's own env plus `chela.env`
  (`CHELA_NOTIFY_URL`, the dashboard/collab/terminal knobs), ~40 pm2 bookkeeping vars
  (`args`, `env`, `status`, `cwd`, `axm_*`, `pm_*`, a var named after the pm2 process —
  some of them not even valid shell identifiers, so no `unset` can remove them),
  `NODE_CHANNEL_*`, WSL interop (`WSL_INTEROP`, `WSLENV`), shell-prompt state, and the
  operator's Claude session markers. `_launch_agent` therefore runs the agent command
  (coding, rework and judge alike — one launch path) under `env -i` with only these names
  passed through, each only if set (`chela/envutil.py::AGENT_ENV_ALLOW`,
  `agent_env_cmd`):

  | Group | Names |
  |---|---|
  | who / where | `HOME` `USER` `LOGNAME` `SHELL` `PATH` `PWD` `TMPDIR` `TZ` |
  | terminal + locale | `TERM` `COLORTERM` `LANG` `LANGUAGE` `LC_ALL` `LC_CTYPE` `LC_MESSAGES` `LC_COLLATE` `LC_NUMERIC` `LC_TIME` |
  | per-user runtime | `XDG_RUNTIME_DIR` `XDG_CONFIG_HOME` `XDG_CACHE_HOME` `XDG_DATA_HOME` `XDG_STATE_HOME` `XDG_DATA_DIRS` `XDG_CONFIG_DIRS` `DBUS_SESSION_BUS_ADDRESS` |
  | tmux self-location | `TMUX` `TMUX_PANE` |
  | chela self-identity | `CHELA_WID` `CHELA_DIR` `CHELA_TMUX_SESSION` `CHELA_ENV_FILE` |
  | node (nvm) | `NVM_DIR` `NVM_BIN` `NVM_INC` |
  | Claude Code config | `CLAUDE_CONFIG_DIR` `ANTHROPIC_BASE_URL` `ANTHROPIC_MODEL` |
  | display | `DISPLAY` `WAYLAND_DISPLAY` |
  | credentials it cannot work without | `ANTHROPIC_API_KEY` `ANTHROPIC_AUTH_TOKEN` `CLAUDE_CODE_OAUTH_TOKEN` `GH_TOKEN` `GITHUB_TOKEN` (`FORWARDED_SECRET_VARS`) |

  Everything else is out by default — including proxies, `SSH_AUTH_SOCK`, CA-bundle
  overrides and editor vars. An operator who needs one adds it by name to
  `CHELA_CHILD_ENV_FORWARD` (the same list hooks use); a name that is not a plain identifier
  is dropped, since it is interpolated into a shell line. The values are expanded by the
  pane's own shell (`${NAME+"NAME=$NAME"}`), so none is typed into the pane or logged.
  This replaces CMX-115's strip-list (`unset CHELA_NOTIFY_URL`), which removed one known
  name and passed every unknown one. It complements, and does not replace, CMX-425/CMX-21's
  scrub of the tmux server's global table (`envutil.scrub_tmux_secrets`), which protects
  every *other* window. Limits: the allowlist governs what the agent process *inherits*;
  a `chela` subcommand the agent runs still re-sources `chela.env` at import (the config
  contract), and the agent can read any file its uid can — env hygiene is not a sandbox
  (see the sandbox bullet below). Guarded by `tests/test_agent_env_strip.py`, which types
  the exact launch lines into a real `bash`/`dash` and checks a sentinel daemon var is gone
  while `CHELA_WID` and `PATH` remain.
- **The judge's model is a fixed floor, not a fleet setting.** `DEFAULT_JUDGE_MODEL` is
  pinned to a capable model and never follows the coding-agent Settings model, so a
  cheaper fleet default cannot quietly weaken the adversarial pass
  (`dispatcher.py:330-402`).
- **A worktree cannot escape into a workspace it doesn't own.** `workspace_escape` refuses
  to dispatch — loudly, not by silently relocating — when a non-default `CHELA_DIR`
  process's configured `workspace.root` resolves outside that `CHELA_DIR`
  (`chela/workflow.py:250-282`).
- **A shared memory ceiling bounds the box, not just one job** — `chela/memcap.py`
  (CMX-264); see `docs/RESOURCE_ISOLATION.md` for the incident that made a per-job cap
  provably insufficient.
- **A per-agent sandbox boundary is implemented and measured, but OFF for chelamux's own
  dispatch — so in practice its agents still run unsandboxed.** `chela/sandbox.py`
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
  those tests are socket-free — `WORKFLOW.md`'s `agent:` block records why. What was
  measured, and what the sandbox does and does not cover, is in
  [SANDBOX_BOUNDARY.md](SANDBOX_BOUNDARY.md). Isolating *shared panes* is a separate, still
  open blocker on running the orchestrator unattended — see "Shared-pane isolation" in
  `docs/ESCALATION_CONTRACT.md`.

## 3. What a dispatched agent's output needs before it changes anything real (approval)

Three independent layers, each narrower than the last:

**a. CI** — the mechanical baseline every PR must clear regardless of anything below.

**b. The judge** (`chela/judge.py`) — an adversarial pass CI cannot run. It never merges
and never approves by opinion; "cannot verify" counts as neither pass nor fail. It
proposes mutations to the guards a PR claims to add, and chela itself applies each one,
proves the file changed, proves it still parses, re-runs the repo's own suite, and
restores it — a guard that survives its own corruption is a fact, not a judgment (see the
module docstring). Findings that are taste rather than a mechanical fact become a PR
comment and can never send a run back.

**c. The merge gate** (`chela/contract.py::merge`) — the only function allowed to run
`gh pr merge`, a pure function of `(run row, live GitHub facts)` with no `--force`:

1. the run exists and is `awaiting_review` — the only clause that reads the run row
   rather than live GitHub;
2. the base branch is the run's own dispatching workflow's declared
   `workspace.base_branch` (`dev` for chelamux itself), and is never
   `main`/`master`/`production`/… unless that workflow explicitly committed to exactly
   that branch;
3. the judge said `clean` against the PR's *current* head commit, read live from GitHub;
4. CI is green;
5. GitHub reports the PR open and `MERGEABLE`;
6. **if the actor is the auto-launched orchestrator** (`$CHELA_ACTOR ==
   auto-orchestrator`), a human's attended-lease must be live. A human's own `chela merge`
   carries no actor stamp and needs no lease — the human *is* the attendance
   (`chela/personas/lease.py`).

Any miss refuses, naming the exact clause. Every merge is logged with its justification
to the event log.

**Escalation is the fail-closed default, not an edge case.**
`docs/ESCALATION_CONTRACT.md` sorts every orchestrator decision into AUTONOMOUS / ESCALATE
/ NEVER, and ambiguity between tiers resolves upward: "unknown" is a `cannot_verify`, and a
`cannot_verify` always goes to a human (`chela/contract.py::escalate`, `chela escalate`).
Merging to `main`, making the repo public, and executing agent-authored text as a command
are all in the NEVER tier — no standing grant reaches them.

**Two unattended paths exist, and both are strictly opt-in, off by default, and loud when
on** (`capabilities.py`'s `warn_when_on` logs a WARNING, not an INFO, on every boot they
are enabled): `CHELA_AUTO_MERGE` runs the *same* `contract.merge` gate above on a timer
with nobody attending (`chela/config.py:870-893`); `CHELA_AUTO_UPDATE` does the equivalent
for `chela update`'s already safety-railed `apply()` (`chela/config.py:896-905`). Neither
loosens the gate it rides on; both require an operator who read the tradeoff to turn them
on for themselves.

## 4. What becomes public

On a public repo, a dispatched brief, its PR, and — for a `gh_issues` tracker — the
triggering issue's body are all public by construction. The `require_label` gate in §1 is
what actually stands between a stranger opening an issue and code executing on the
operator's machine; nothing downstream of dispatch is a substitute for it. See
`docs/archive/SYMPHONY_CONFORMANCE_2026-09-13.md` G2 for the fuller tradeoff if a workflow ever
migrates its tracker from `markdown` to `gh_issues`.

## Where each of these is enforced, for a conformance reader

| Posture question | Enforced in | Documented in |
|---|---|---|
| Who may trigger a dispatch | `chela/sources/gh_issues.py`, `chela/sources/markdown.py` | this doc §1 |
| What a running agent may do | `chela/dispatcher.py::resolve_agent_cmd`, `chela/envutil.py::agent_env_cmd`, `chela/workflow.py::workspace_escape`, `chela/memcap.py`, `chela/sandbox.py` (opt-in, off for chelamux) | this doc §2, `RESOURCE_ISOLATION.md`, `SANDBOX_BOUNDARY.md` |
| What approves a merge | `chela/judge.py`, `chela/contract.py::merge` | this doc §3, `ESCALATION_CONTRACT.md` |
| What a human must decide instead of the orchestrator | `chela/contract.py::escalate` | `ESCALATION_CONTRACT.md`'s decision taxonomy |

This is chela's answer to **SPEC 10.5** ("Each implementation MUST document its chosen
approval, sandbox, and operator-confirmation posture") and to **SPEC 1**'s parallel
requirement to document a trust and safety posture explicitly. Symphony itself takes no
position on approval, sandbox, or review behavior; chela's chosen position is fail-closed
escalation, a mechanical (not opinion-based) merge gate, and unattended paths that are
off by default and loud when turned on.
