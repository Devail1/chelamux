"""Configuration — **the environment is the single source of truth.**

Everything chela needs is discoverable from plain tmux; there is no external service to
point at. State lives under ``~/.chela`` (override with ``CHELA_DIR``).

One file, one authority: ``$CHELA_DIR/chela.env`` (``KEY=value`` lines) is *sourced* —
by :func:`load_env_file` here for anything importing chela, and by ``scripts/run-chela.sh``
for anything PM2 starts. A real environment variable always wins over the file, so an
override stays possible; what is NOT allowed any more is a second *place* config lives.
A PM2 ``env:`` block is exactly that second place, and it drifted: three copies of
``CHELA_TMUX_SESSION`` still said ``ccbot`` a day after the session was renamed.

The dashboard port is the case that proves the rule. It is baked into the Claude Code
hooks plugin as a literal (Claude Code does not expand env vars in a hook ``url``), and
``chela plugin`` renders that manifest from a **different process** than the dashboard —
so a port that only exists inside the dashboard's own process (``--port``) makes every
hook POST into a closed socket, silently. Hence :func:`publish_dashboard_port`: the
dashboard writes down the port it actually bound, and :func:`live_dashboard_port` — what
the plugin renders — reads it back. Nobody has to guess.
"""
import json
import os
import re
import subprocess
import time
from pathlib import Path

# Where chela keeps its own state (scheduler.db, dispatcher runs, context cache).
CHELA_DIR = Path(os.environ.get("CHELA_DIR", Path.home() / ".chela"))


# --- the env file: one place config lives -----------------------------------------

# What a shell will accept as a variable name. Anything else in the file is skipped
# rather than passed to os.environ, which raises on one (a truncated or binary file must
# not take the CLI down before it can tell you the file is broken).
_VALID_KEY = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")

def env_file_path() -> Path | None:
    """``$CHELA_DIR/chela.env``, or ``$CHELA_ENV_FILE`` when set. ``CHELA_ENV_FILE=""``
    disables the file entirely (what the test suite does — a developer's real
    ``~/.chela/chela.env`` must never leak into a unit test)."""
    raw = os.environ.get("CHELA_ENV_FILE")
    if raw is None:
        return CHELA_DIR / "chela.env"
    raw = raw.strip()
    return Path(raw).expanduser() if raw else None


def parse_env_file(path: Path) -> dict[str, str]:
    """``KEY=value`` lines → a dict. Blank lines and ``#`` comments are skipped, a
    leading ``export`` and one layer of surrounding quotes are stripped — the subset of
    shell syntax ``set -a; . file`` and this parser agree on. Anything unreadable or
    unparseable is skipped, never raised: a typo in a config file must not stop the CLI
    from starting (``chela doctor`` is where it becomes visible)."""
    try:
        text = Path(path).read_text(encoding="utf-8")
    except OSError:
        return {}
    out: dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[len("export "):].lstrip()
        key, _, value = line.partition("=")
        key = key.strip()
        if not _VALID_KEY.fullmatch(key):
            continue        # not a variable name — a stray line, or a corrupt file
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        out[key] = value
    return out


def load_env_file(path: Path | None = None) -> dict[str, str]:
    """Source the env file into ``os.environ``; return what it declared.

    ``setdefault``, not assignment: an explicitly exported variable outranks the file
    (that is how a one-off ``CHELA_TMUX_SESSION=other chela status`` still works, and how
    ``scripts/run-chela.sh`` sourcing the same file first is a no-op rather than a
    conflict). Child processes inherit it, so a tmux-spawned agent sees the same config.
    """
    path = path if path is not None else env_file_path()
    if path is None:
        return {}
    declared = parse_env_file(path)
    for key, value in declared.items():
        os.environ.setdefault(key, value)
    return declared


ENV_FILE: Path | None = env_file_path()
# At import, before anything below reads os.environ — so a plain `chela status` in a bare
# shell is configured identically to the PM2 daemon, with nothing exported by hand.
ENV_FILE_VARS: dict[str, str] = load_env_file(ENV_FILE)

# The tmux session chela orchestrates. Each agent lives in its own window of
# this session, and the window name IS the agent's display name. Override with
# CHELA_TMUX_SESSION; defaults to "chela".
#
# TMUX_SESSION is the import-time value (env override, else "chela") — kept as an
# importable shim for callers that always run with CHELA_TMUX_SESSION set (the
# pm2 daemon/dashboard). New code should prefer current_session() below, which
# also makes a bare `chela peek/read` zero-config for an orchestrator agent.
TMUX_SESSION = os.environ.get("CHELA_TMUX_SESSION", "chela")


def current_session() -> str:
    """The tmux session chela operates on, resolved LAZILY at call time.

    Precedence:
      1. explicit ``$CHELA_TMUX_SESSION`` — an override always wins;
      2. else the caller's OWN pane's session, via ``$TMUX_PANE`` (tmux sets it
         in every pane) — so an orchestrator agent living in whatever session the
         fleet actually uses (e.g. ``myteam``) gets zero-config discovery, mirroring
         how ``orchestrator.self_wid()`` derives the window from the same pane;
      3. else ``"chela"``.

    Resolved per-call (never at import) so it can't cache a stale value, and so a
    process with no tmux pane still imports cleanly. Grouped sessions share one
    window list, so deriving a mirror (``webterm_myteam__*``) lists the same real
    windows as its parent.
    """
    env = os.environ.get("CHELA_TMUX_SESSION")
    if env:
        return env
    pane = os.environ.get("TMUX_PANE")
    if pane:
        try:
            r = subprocess.run(
                ["tmux", "display-message", "-p", "-t", pane, "#{session_name}"],
                capture_output=True, text=True, timeout=5,
            )
            if r.returncode == 0 and r.stdout.strip():
                return r.stdout.strip()
        except (subprocess.TimeoutExpired, FileNotFoundError):
            pass
    return "chela"

# Window names to hide from discovery everywhere (dashboard, status, the ttyd
# supervisor). For placeholder / keep-alive windows that aren't agents — e.g. a
# pinned "__main__" remain-on-exit window kept so the tmux session survives when
# the last agent window exits; it's noise on the wall, not an agent.
# Comma-separated; default empty so a generic install shows every window.
IGNORE_WINDOWS = {
    w.strip() for w in os.environ.get("CHELA_IGNORE_WINDOWS", "").split(",") if w.strip()
}

DEFAULT_DASHBOARD_PORT = 5001


def dashboard_host() -> str:
    return os.environ.get("CHELA_DASH_HOST", "127.0.0.1")


def dashboard_port() -> int:
    """The port the dashboard is CONFIGURED to bind — ``CHELA_DASHBOARD_PORT`` (which the
    env file supplies), else the default.

    This is what the dashboard binds. It is *not* necessarily what it is listening on:
    ``chela dashboard --port`` can override it for a one-off run, and then the flag —
    not the env — is the truth. Anything that has to *reach* the dashboard from another
    process (the hooks plugin, above all) must ask :func:`live_dashboard_port`.

    Resolved per call, never at import: ``--port`` sets the env var after this module is
    imported, and a cached constant would ignore it.
    """
    try:
        return int(os.environ.get("CHELA_DASHBOARD_PORT", DEFAULT_DASHBOARD_PORT))
    except ValueError:
        return DEFAULT_DASHBOARD_PORT


def dashboard_port_file() -> Path:
    """Where the dashboard writes down the port it actually bound."""
    return CHELA_DIR / "dashboard.port"


def publish_dashboard_port(port: int, host: str = "127.0.0.1") -> None:
    """Record the port the dashboard REALLY bound, for every other process to read.

    Best-effort by design: a dashboard that cannot write this file still serves. What it
    loses is the guarantee that ``chela plugin`` renders a reachable URL — which
    ``chela doctor`` then reports, loudly, rather than the feature failing in silence.
    """
    try:
        CHELA_DIR.mkdir(parents=True, exist_ok=True)
        dashboard_port_file().write_text(
            json.dumps({"port": int(port), "host": host, "pid": os.getpid(),
                        "ts": time.time()}) + "\n",
            encoding="utf-8",
        )
    except OSError:
        pass


def clear_dashboard_port() -> None:
    """Drop the published port on a clean shutdown (a crash leaves it; the pid check
    below is what makes a stale file harmless either way)."""
    try:
        dashboard_port_file().unlink()
    except OSError:
        pass


def live_dashboard() -> dict | None:
    """What the running dashboard published, or None if none is running.

    A file whose ``pid`` is gone is stale — the dashboard died — and is treated as no
    dashboard at all, so a crashed instance can't keep pointing hooks at a closed socket.
    """
    try:
        data = json.loads(dashboard_port_file().read_text(encoding="utf-8"))
        port = int(data["port"])
        pid = int(data.get("pid") or 0)
    except (OSError, ValueError, TypeError, KeyError):
        return None
    if pid > 0:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return None
        except PermissionError:
            pass                     # alive, owned by someone else — still listening
        except OSError:
            return None
    return {"port": port, "host": str(data.get("host") or "127.0.0.1"), "pid": pid}


def live_dashboard_port() -> int:
    """The port the dashboard is ACTUALLY listening on — what a hook must POST to.

    Prefers what the running dashboard published over what the config says, because a
    ``--port`` flag makes those two differ and the *plugin* has to be right regardless.
    Falls back to the configured port when no dashboard is running (rendering a plugin
    before starting one is legitimate). ``chela doctor`` flags the disagreement.
    """
    live = live_dashboard()
    return live["port"] if live else dashboard_port()


# Context-window status-line cache, written by scripts/cache-statusline.sh after
# each assistant turn. The daemon reads these to track per-agent context usage.
CONTEXT_CACHE_DIR = CHELA_DIR / "context"
CACHE_STALE_SECONDS = int(os.environ.get("CHELA_CACHE_STALE_SECONDS", "7200"))  # skip files older than 2h

# Cost history: how often the daemon calls `context.capture_all()` to accrue
# `context_snapshots` rows — the foundation the Cost tab's Today/7d/30d windows sum
# over. Cheap (one row per live statusLine cache file), so a 5-minute default is
# plenty of resolution without writing on every daemon tick.
CAPTURE_INTERVAL_SECONDS = int(os.environ.get("CHELA_CAPTURE_INTERVAL_SECONDS", "300"))
# How long accrued snapshots are kept before `context.prune_snapshots` deletes them —
# an always-on daemon would otherwise grow scheduler.db without bound.
CONTEXT_SNAPSHOT_RETENTION_DAYS = int(os.environ.get("CHELA_CONTEXT_RETENTION_DAYS", "30"))

# Daemon loop intervals (seconds).
SCHEDULER_POLL_INTERVAL = int(os.environ.get("CHELA_SCHEDULER_POLL_INTERVAL", "30"))

# Work-item dispatcher inside the daemon. Colon-separated list of WORKFLOW.md
# paths (~ and $VAR are expanded). Empty = dispatcher off in the daemon; the
# `chela dispatch <workflow>` CLI still works regardless.
DISPATCH_TICK_INTERVAL = int(os.environ.get("CHELA_DISPATCH_TICK_INTERVAL", "60"))

# CMX-179: `claude agents --json` (the native busy/idle status feed) cold-starts ~12s and
# warm-starts 17-18s on measured hardware (chela/agent_manager.py's diagnostic comment has
# the raw numbers) — the cost is CLI STARTUP, not payload. A timeout below the warm-start
# floor times out on EVERY call, silently: this shipped as 10.0s from 2026-07-14 to
# 2026-07-26 and produced 17,411 identical timeout WARNINGs (~250/hour) before anyone
# noticed. Give real headroom above the measured worst case — do not "tidy" this back down.
STATUS_CMD_TIMEOUT_S = float(os.environ.get("CHELA_STATUS_CMD_TIMEOUT_S", "45.0"))
# How long the background refresher (agent_manager.start_background_refresh) trusts a
# successful fetch before asking again. Deliberately NOT how long a request blocks — an
# ordinary request only ever reads the cache; only this periodic thread pays the subprocess
# cost, off the request path.
STATUS_TTL_S = float(os.environ.get("CHELA_STATUS_TTL_S", "30.0"))


def max_reworks() -> int:
    """How many times a PR that FAILED REVIEW may be sent back to its agent.

    Past the cap the run escalates to ``needs_human`` instead of going round again —
    a bounded loop that surfaces rather than spins (``rooms.MAX_HOPS`` is the same
    idea). ``0`` disables rework entirely: the first ``changes_requested`` escalates.

    Read per call, never latched at import: it is a policy knob an operator turns on a
    daemon that is already running, and a garbage value must degrade to the default
    rather than crash the tick.
    """
    try:
        return max(0, int(os.environ.get("CHELA_MAX_REWORKS", "2")))
    except ValueError:
        return 2


def judge_max_unknown_retries() -> int:
    """How many times a judge that came back CANNOT VERIFY may be RE-RUN on the SAME commit.

    ⚖️ CMX-81. ``cannot_verify`` is an UNKNOWN — a flake, a ``gh`` timeout, a worktree that
    would not check out, a judge window that died — NOT a verdict. An unknown must cost a
    BOUNDED retry, never permanently retire the commit from judgment: without this a single
    transient failure lets a green PR merge UNJUDGED, silently defeating the judge on any
    flake. This bounds the retries beyond the first attempt; a new head sha is a fresh
    judgement and resets the count. Past the cap the run is left in ``awaiting_review`` for a
    human, exactly where a settled cannot-verify leaves it — the loop surfaces rather than
    spins (``max_reworks`` / ``rooms.MAX_HOPS`` are the same idea). ``0`` disables the retry:
    the first cannot-verify is final.

    Read per call, never latched at import: a policy knob an operator turns on a running
    daemon, and a garbage value degrades to the default rather than crashing the tick.
    """
    try:
        return max(0, int(os.environ.get("CHELA_JUDGE_MAX_UNKNOWN_RETRIES", "2")))
    except ValueError:
        return 2
# ⚖️ The judge (see chela.judge) — the adversarial pass on a PR that reached
# awaiting_review. The fleet-wide kill switch; a workflow turns it off for itself with
# `judge: {enabled: false}`, and it is off anyway for any workflow with no `judge.test_cmd`
# (there is nothing to run a mutation against). It spawns one extra agent per PR head, so
# an operator who wants that stopped needs one env var, not an edit to every WORKFLOW.md.
JUDGE_ENABLED = os.environ.get("CHELA_JUDGE", "true").strip().lower() not in (
    "false", "0", "no", "off",
)

# 🧑‍⚖️ The critic (see chela.critic) — the persona pattern's third instance: an ADVISORY
# brief-review at the moment a task is picked for dispatch ("plan review is the new linter").
# The fleet-wide kill switch; a workflow turns it off for itself with `critic: {enabled:
# false}`. Unlike the judge it is advisory-only (it never blocks/delays/changes a dispatch)
# and needs no `test_cmd`, so it defaults ON — "on" costs nothing it can get wrong.
CRITIC_ENABLED = os.environ.get("CHELA_CRITIC", "true").strip().lower() not in (
    "false", "0", "no", "off",
)

# 🎭🤖 The orchestrator auto-launch (see chela.personas.autolaunch) — the persona pattern's
# harness instance: chela launches the embedded orchestrator persona itself, inbox-woken and
# gated by a human's attended-lease (CMX-90). ⛔ Defaults OFF, unlike the judge/critic: this one
# spawns an agent that holds `chela merge` authority, so it fires only when an operator has
# explicitly armed it AND is actively attending (`chela orchestrator attend`).
ORCHESTRATOR_ENABLED = os.environ.get("CHELA_ORCHESTRATOR", "false").strip().lower() in (
    "true", "1", "yes", "on",
)

# 🎭🔑 The ACTOR stamp — how a chela session names itself to the contract gate (chela.contract).
# The auto-launched orchestrator exports CHELA_ACTOR=auto-orchestrator into its window (alongside
# CHELA_WID) so that `chela merge`, run from THAT session, requires a live attended-lease of it:
# it is auto-*launched*, and the lease keeps its autonomous ACTIONS attended too, not just its
# start. A human's own `chela merge` carries no such stamp, so the lease gate never applies to a
# human — the human's presence IS the attendance. Read live at merge-time, never latched here.
ACTOR_ENV = "CHELA_ACTOR"
AUTO_ORCHESTRATOR_ACTOR = "auto-orchestrator"

# 🔀⚠️ AUTO-MERGE (see chela.automerge) — the FULLY-UNATTENDED merge sweep (CMX-138). Today
# every autonomous merge path — a human's own `chela merge`, and the auto-launched orchestrator
# (which needs a human's attended-lease, see ACTOR_ENV above) — has a human either doing it or
# watching it. This is the one path that does not: on every daemon tick it hands each
# judge-`clean` `awaiting_review` run straight to `contract.merge`, the SAME gate (base/CI/judge/
# mergeable, still no `--force`, still no NEVER-line override) a human or the lease-gated
# orchestrator would use — it just does not wait for either to be watching.
#
# ⛔ Defaults OFF, hard: a fresh/external install must never autonomously merge anything with
# nobody attending. This is a deliberate, narrow loosening of "no fully-unattended auto-merge"
# (docs/ESCALATION_CONTRACT.md) that only an operator who has explicitly read the risk and set
# this env var opts into — never a default, never inferred, never widened by any other flag.
AUTO_MERGE_ENABLED = os.environ.get("CHELA_AUTO_MERGE", "false").strip().lower() in (
    "true", "1", "yes", "on",
)
# The actor stamp `chela.automerge` passes to `contract.merge` — deliberately NOT
# AUTO_ORCHESTRATOR_ACTOR. That actor string is what makes `contract.merge` require a live
# attended-lease (its clause 2); auto-merge is meant to run with nobody attending at all, so it
# needs its own name — one that never accidentally satisfies (or is satisfied by) the
# orchestrator's lease gate, and that the event log can tell apart from a human or the
# orchestrator when someone later asks "why did this merge itself."
AUTO_MERGE_ACTOR = "auto-merge"

# ⬆️⚠️ AUTO-UPDATE (see chela.update.auto_apply_sweep) — the FULLY-UNATTENDED half of
# self-update (CMX-148, part 2 of CMX-142). Part 1 (`chela update`, `update.check_and_notify`)
# only ever INFORMS a human that the checkout fell behind — nothing there pulls code on its
# own. This is the opt-in sweep that lets the daemon actually pull, `uv sync`, and restart its
# own `chela-*` services (including itself) on its own hourly tick, mirroring
# CHELA_AUTO_MERGE's contract exactly: the exact same safety rail underneath
# (`update.apply()`'s dirty-tree / diverged-branch refusal, unchanged and unloosened by this
# flag) — the only thing this removes is a human watching it happen.
#
# ⛔ Defaults OFF, hard: a fresh/external install must never autonomously rewrite its own
# code and restart its own services with nobody attending. This is a deliberate, narrow
# opt-in an operator makes for themselves — never a default, never inferred, and never
# widened by any other flag (CHELA_AUTO_MERGE included — the two are independent knobs; an
# operator can trust their judge to merge PRs without trusting every dependency in `dev` to
# auto-deploy onto their own machine, and vice versa).
AUTO_UPDATE_ENABLED = os.environ.get("CHELA_AUTO_UPDATE", "false").strip().lower() in (
    "true", "1", "yes", "on",
)

_dispatch_raw = os.environ.get("CHELA_DISPATCH_WORKFLOWS", "")
DISPATCH_WORKFLOWS = [
    Path(os.path.expandvars(os.path.expanduser(p))).resolve()
    for p in _dispatch_raw.split(":") if p.strip()
]

# Needs-input notification: when an agent's pane enters the `waiting` state
# (blocked on a permission prompt or a question), fire a notification once per
# edge. Empty = off. The kind is auto-detected from the URL (ntfy / Telegram /
# generic webhook); override with CHELA_NOTIFY_KIND. CHELA_NOTIFY_INTERVAL is
# how often (seconds) the daemon scans pane states.
NOTIFY_URL = os.environ.get("CHELA_NOTIFY_URL", "").strip()
NOTIFY_KIND = os.environ.get("CHELA_NOTIFY_KIND", "").strip().lower()  # "", ntfy, telegram, webhook
NOTIFY_TITLE = os.environ.get("CHELA_NOTIFY_TITLE", "chela: agent needs input")
NOTIFY_INTERVAL = int(os.environ.get("CHELA_NOTIFY_INTERVAL", "20"))

# CMX-187: how often (seconds) the daemon runs `chela doctor`'s full audit and pushes any
# ERROR-level finding through the same notify channel as the needs-input check above.
# `chela doctor` diagnosing a dead relay perfectly and nobody seeing it for hours (nothing
# runs doctor unless a human does) is exactly the shape this closes.
#
# ⚠️ Hourly, matching UPDATE_CHECK_INTERVAL_SECONDS — NOT the 20s needs-input cadence. A
# full audit is not "a bit more than scanning pane states": measured twice on a live box it
# took 28.0s and 32.2s, because it shells out to `git` and `gh` per parked run and PR, runs
# a `pytest --collect-only` for the JS-suite fact, and asks `claude agents --json` fresh
# (12-18s on its own — CMX-179). At the 300s this shipped with, that is a ~10% permanent
# duty cycle of subprocess churn on every install, forever, to re-derive a set that is
# edge-triggered and so almost always unchanged. Raise the cadence only with a fresh
# measurement of what an audit costs on the box in question.
DOCTOR_CHECK_INTERVAL = int(os.environ.get("CHELA_DOCTOR_CHECK_INTERVAL", "3600"))

# Outbound Telegram relay: post every tool_use/tool_result event as its own
# message (🔧 Bash / ✅ Bash result). That's a firehose on a phone, so it is OFF
# by default — the relay then sends only text/thinking/user turns plus the
# interactive prompts that need a human (AskUserQuestion / ExitPlanMode). Set
# CHELA_SHOW_TOOL_CALLS=true for the full stream. Ported from ccbot's
# CCBOT_SHOW_TOOL_CALLS (which defaulted ON).
SHOW_TOOL_CALLS = os.environ.get("CHELA_SHOW_TOOL_CALLS", "false").strip().lower() not in (
    "false", "0", "no", "off",
)

# Auto-topics: give a DISPATCHER-SPAWNED agent (a worktree worker the dispatcher
# owns — identified from the `runs` table, never from its window name) a Telegram
# topic like any other agent. OFF by default: a fleet of short-lived workers, each
# churning a topic on spawn and archiving it on exit, turns a human's forum inbox
# into a changelog. With it off, a dispatched agent is bound LAZILY — no topic while
# it works, a topic the moment it BLOCKS on a permission gate / question (see
# chela.telegram.reconcile.blocked_on_human — the hook log OR the pane, because a
# PERMISSION gate is never in the log at all), so the forum shows only the agents
# that want a human. Human-driven sessions (orchestrator, project sessions) are
# unaffected either way. Set CHELA_TELEGRAM_BIND_DISPATCHED=true for the old
# bind-everything behaviour.
BIND_DISPATCHED = os.environ.get("CHELA_TELEGRAM_BIND_DISPATCHED", "false").strip().lower() in (
    "true", "1", "yes", "on",
)

# Ephemeral status line: relay Claude Code's live "working" verb (and background
# shell count) to each bound topic as a single self-deleting message that edits in
# place while the agent works and is poofed when the turn ends. It is what lets a
# phone tell a *thinking* agent from a *dead* one, so it is ON by default; set
# CHELA_STATUS_LINE=false to turn it off (it costs a Telegram edit every few
# seconds per WORKING window — no extra tmux calls, and idle windows cost nothing).
STATUS_LINE = os.environ.get("CHELA_STATUS_LINE", "true").strip().lower() not in (
    "false", "0", "no", "off",
)

# Embedded ttyd terminal wall on/off (read by the dashboard and the ttyd
# supervisor in scripts/agent-terminals.sh). The wall — the flagship feature —
# is ON by default, but it serves writable shells, so the dashboard gates it on
# the bind host: a loopback bind (the documented model — fronted by a tailnet /
# SSH tunnel) serves the wall; a non-loopback bind refuses to unless you opt in
# with CHELA_TERMINALS_EXPOSE=true. Set CHELA_TERMINALS_ENABLED=false to turn
# the wall off entirely.
TERMINALS_ENABLED = os.environ.get("CHELA_TERMINALS_ENABLED", "true").strip().lower() not in ("false", "0", "no", "off")

# The Wall's half of the same rule as BIND_DISPATCHED above, on the second surface a
# dispatched worker can occupy: give it a full-size TILE on spawn, like a human's
# session. OFF by default — a dispatched worker opens MINIMIZED (a chip in the dock,
# its terminal live and scrollable, just not holding a slot on the wall) and POPS OUT
# the moment it blocks on a human. ⛔ The principle is one principle: a worker should
# not occupy human attention surface — a topic OR a tile — until it needs a human.
# The wall is where you WATCH the fleet; five workers grinding through a backlog crowd
# out the one session you are actually in. Set CHELA_WALL_TILE_DISPATCHED=true for the
# old tile-everything behaviour. (This gates the BEHAVIOUR only: /api/agents always
# reports `dispatched` + `needs_human` honestly — they are facts about the window.)
WALL_TILE_DISPATCHED = os.environ.get("CHELA_WALL_TILE_DISPATCHED", "false").strip().lower() in (
    "true", "1", "yes", "on",
)

# Decisions inbox (chela/inbox.py): push agent/run events into the orchestrator's
# session when it is idle, so a finished agent stops being invisible to it. Inert
# until a session registers itself as the orchestrator (`chela watch`), so this
# defaults ON safely; set CHELA_INBOX_ENABLED=false to disable it outright.
INBOX_ENABLED = os.environ.get("CHELA_INBOX_ENABLED", "true").strip().lower() not in ("false", "0", "no", "off")

# How long an undeliverable orchestrator address must stay dead before the inbox buzzes
# the phone about it (chela/inbox.py `_undeliverable`). A reboot / tmux-restart / handoff
# makes the address dangle for a few ticks and then SELF-HEALS (CMX-82) the moment the next
# session runs `chela watch` (or any dispatch) — that is expected and fixes itself, so
# buzzing the phone for it is noise (CMX-113). The durable event / log ERROR / red doctor
# still fire the INSTANT the address is seen dead, unconditionally: those are the surfaces a
# human checks WHILE DEBUGGING (CMX-77's whole point), and this grace window only delays the
# proactive push, never the record.
INBOX_ALARM_GRACE_SECONDS = int(os.environ.get("CHELA_INBOX_ALARM_GRACE_SECONDS", "120"))

# Explicit opt-in to serve the writable terminal wall on a NON-loopback bind
# (e.g. --host 0.0.0.0 or a LAN/tailnet IP). Off by default: a public bind would
# otherwise hand out unauthenticated remote shells (RCE). Loopback binds, fronted
# by a tailnet or SSH tunnel (the recommended setup), never need this.
TERMINALS_EXPOSE = os.environ.get("CHELA_TERMINALS_EXPOSE", "false").strip().lower() not in ("false", "0", "no", "off")

# Collaborative-terminal presence (P3 agent-as-peer). When on, the dashboard
# publishes a Yjs *awareness* frame per running-claude window into that window's
# relay room, so a browser viewing a *shared* /term/<wid>/ sees a "claude" pill
# for the live agent. Purely additive and opaque: it only reaches browsers that
# have joined the room, i.e. a window the host has shared.
#
# COLLAB_RELAY is the dumb fan-out relay (chela/collab-relay). It defaults to
# EMPTY: presence is OFF until you point CHELA_COLLAB_RELAY at a relay you own, so
# the repo ships no phone-home to anyone's personal infrastructure. Self-hosting
# is a one-liner: `wrangler deploy` in chela/collab-relay/ (free plan), then export
# the printed wss:// URL. With no relay, collab.start() no-ops and the injected
# shim disables presence.js. CHELA_COLLAB=false also stops it.
COLLAB_PRESENCE = os.environ.get("CHELA_COLLAB", "true").strip().lower() not in ("false", "0", "no", "off")
COLLAB_RELAY = os.environ.get("CHELA_COLLAB_RELAY", "").strip().rstrip("/")

# Shared fixed grid (cols x rows) that a shared terminal snaps to when 2+ human
# peers are present (see the adaptive sizing in static/collab/presence.js): while
# collaborating, every viewer sees an identical, complete grid (tmux window-size
# manual) and letterbox-scales it to their viewport; solo, the pane fits the
# viewport dynamically as usual.
TERM_COLS = int(os.environ.get("CHELA_TERM_COLS", "120"))
TERM_ROWS = int(os.environ.get("CHELA_TERM_ROWS", "30"))


_SIZE_SUFFIXES = {"": 1, "K": 1024, "M": 1024**2, "G": 1024**3, "T": 1024**4}


def _parse_size_bytes(raw: str) -> int | None:
    """``"20G"`` → bytes. A bare integer is bytes; a trailing K/M/G/T (case-insensitive,
    1024-based) is a size suffix. ``None`` for anything that doesn't parse — the caller
    treats that as budget-off rather than crashing a tick on a typo."""
    raw = raw.strip().upper()
    if not raw:
        return None
    suffix = raw[-1]
    number, mult = (raw[:-1], _SIZE_SUFFIXES[suffix]) if suffix in _SIZE_SUFFIXES and suffix else (raw, 1)
    try:
        return int(float(number) * mult)
    except ValueError:
        return None


def worktree_disk_budget_bytes() -> int:
    """The disk ceiling for a workflow's worktree root — the ``memcap`` analog for disk
    (CMX-164). A dispatch tick that finds the root's measured size over this refuses to
    claim a fresh task rather than let an adopter's heavier repo (a Rust ``target/``, an
    ML venv, a Node monorepo — 1-10 GB *per worktree*) run the box out of disk, which is
    worse than an OOM: it can take git, sqlite, tmux and the daemon down together.

    ``CHELA_WORKTREE_DISK_BUDGET`` accepts a bare byte count or a K/M/G/T-suffixed size
    (``"20G"``, ``"500M"``, ...). ``0``, unset, or anything that fails to parse means OFF
    — no adopter is forced to opt into a rail they haven't sized for their own repo, and a
    garbage value degrades to the safe default rather than crashing the tick.

    Read per call, never latched at import: a policy knob an operator turns on a daemon
    that is already running.
    """
    raw = os.environ.get("CHELA_WORKTREE_DISK_BUDGET", "").strip()
    if not raw:
        return 0
    parsed = _parse_size_bytes(raw)
    return parsed if parsed and parsed > 0 else 0


def human_size(n: int) -> str:
    """``21474836480`` → ``"20.0G"``. Only ever fed a non-negative byte count — the
    disk-budget rail's shared formatter, for the log line and the capability detail to
    agree on the same units."""
    size = float(n)
    for unit in ("B", "K", "M", "G", "T"):
        if size < 1024 or unit == "T":
            return f"{int(size)}{unit}" if unit == "B" else f"{size:.1f}{unit}"
        size /= 1024
    return f"{size:.1f}T"


def is_loopback_host(host: str) -> bool:
    """True when the dashboard bind host is the local loopback (the safe case
    for serving the writable terminal wall)."""
    return (host or "").strip().lower() in ("127.0.0.1", "::1", "localhost", "")
