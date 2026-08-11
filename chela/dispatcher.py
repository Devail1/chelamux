from __future__ import annotations
import json
import logging
import os
import re
import sqlite3
import subprocess
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import NamedTuple

from chela import critic, epoch, event_log, hold, judge
from chela.config import (
    CHELA_DIR,
    TMUX_SESSION,
    dispatch_tick_interval,
    human_size,
    judge_max_unknown_retries,
    max_reworks,
    worktree_disk_budget_bytes,
)
from chela.messenger import messaging_socket_launch_arg, resend_enter, send_tmux
from chela.sources import Task, get_source
from chela.transcripts import agent_transcript_summary
from chela.tui_text import sanitize as tui_sanitize
from chela.workflow import (
    WorkflowDef,
    load_workflow,
    load_workflow_cached,
    poll_interval_seconds,
    render_prompt,
    resolve_workspace_root,
    workspace_escape,
)
from chela.worktree import (
    BranchGone,
    attach_worktree,
    detached_worktree,
    disk_usage_bytes,
    ensure_worktree,
    remove_worktree,
)

log = logging.getLogger(__name__)

DB_PATH = CHELA_DIR / "scheduler.db"
MAX_ATTEMPTS = 3
DONE_HISTORY_PER_WORKFLOW = 50

# --- the run states, and which of them mean what -----------------------------
#
# THE RUN ROW IS THE AUTHORITY ON WHETHER A PR PASSED REVIEW — not GitHub. This is
# measured, not a preference: `gh pr review --request-changes` HARD-ERRORS on a PR the
# calling account authored ("Can not request changes on your own pull request"), and the
# whole fleet is one account, so `reviewDecision` is null FOREVER and is useless as a
# trigger. The verdict is therefore written HERE, and the PR comment
# (`gh pr comment` — never `gh pr review`) is a human-readable PROJECTION of it. That is
# rule (a) of the runtime-truth registry: the process that ACTS publishes what it
# actually DID, and readers read that.
#
# ⛔ Every hard-coded status list is a hole waiting to happen — the new states must be
# added to each list they belong in (reconcile, the claim filter, the inbox's
# SETTLED_RUN_STATES). These constants exist so those lists are named, not spelled.

# Slot-holding states. `awaiting_review` is deliberately NOT one: a PR waiting on a human
# must not pin the fleet, which is why a new task is claimed the moment a PR opens. The
# consequence for the rework loop is the load-bearing one — a run re-entering `running`
# RE-CONSUMES a slot and must respect concurrency.max like any other work.
ACTIVE_STATUSES = ("claimed", "running")

# A PR is open and the verdict is out of the agent's hands. `changes_requested` is a
# rework the dispatcher will pick up; `needs_human` is a rework that hit the cap and
# stopped. Neither holds a concurrency slot, and neither may be re-claimed as a fresh
# task (they already own their branch, worktree and PR).
REVIEW_STATUSES = ("awaiting_review", "changes_requested", "needs_human")

# States a fresh claim must skip: already in flight, parked in review, or shipped.
NOT_CLAIMABLE = (*ACTIVE_STATUSES, *REVIEW_STATUSES, "done")

# Statuses whose PR can still merge OUT OF BAND (a hand `gh pr merge`, never through
# `chela merge`) with no chance for chela to have already seen it settle: the review
# states (a PR is open, waiting on a human or CI) AND `failed` — a fresh-dispatch retry
# gets its own PR per attempt, and a HUMAN merging attempt N's PR by hand while the row
# still reads `failed` (retries left) must stop the claim loop from spawning attempt
# N+1 for work that already shipped. `claimed`/`running` are excluded: neither has
# opened a PR yet, so `pr_state` cannot be `merged` for them.
RECONCILE_MERGE_STATUSES = (*REVIEW_STATUSES, "failed")

# Readiness poll (see _wait_for_ready) — how long to wait for the agent TUI to
# accept input before sending the prompt, and how often to re-check.
READY_TIMEOUT_SECONDS = 60
READY_POLL_INTERVAL = 1.0

# Reconcile watchdog (see tick) — a `running` row stuck at an idle, empty
# Claude prompt for this long is treated as a dropped-prompt strand: nudged
# once, then failed if it stays idle for another window of this length.
WATCHDOG_IDLE_MINUTES = 5

# Tracker strike (see _strike_merged_tasks) — the dispatcher marks a task done
# on base_branch once its PR merges. Local git is fast; fetch/push touch the
# network and must never hang the reconcile loop.
GIT_TIMEOUT_SECONDS = 30
GIT_NET_TIMEOUT_SECONDS = 60

# Seed-delivery verification (see _seed_landed / _send_seed). After pasting the
# prompt we confirm the agent actually picked it up — a late splash redraw (an
# MCP-auth notice, `gh auth login`, generically any startup redraw) can land after
# the paste and swallow its separately-sent Enter, stranding the prompt on the ❯
# line unsubmitted rather than dropping the paste itself. The agent flips
# "idle" → "busy" the moment it accepts a prompt, so poll that status for a
# short window; if it never flips, RE-SEND ENTER (capped) — never re-paste, since
# the text is already sitting there and a second paste would type it twice.
# These three are env-overridable so a real deploy can tune them for a slow box, and so
# the test suite can collapse them to ~0: no test has a real Claude TUI to flip "busy", so
# the confirm loop would otherwise burn its full real-time budget (5×8s ≈ 44s PER dispatch)
# doing nothing — which was ~80% of the suite's wall-clock. Defaults are unchanged for prod.
SEED_CONFIRM_TIMEOUT_SECONDS = float(os.environ.get("CHELA_SEED_CONFIRM_TIMEOUT_SECONDS") or 8.0)
SEED_CONFIRM_POLL_INTERVAL = float(os.environ.get("CHELA_SEED_CONFIRM_POLL_INTERVAL") or 1.0)
# 5 sends × ~8s each ≈ 40s of resend coverage — enough to outlast a slow startup
# notice (MCP-auth, `gh auth login`) whose redraw window can run 20-30s before the
# pane accepts keystrokes again. (Was 3 ≈ 24s, which gave up too early — CMX-133.)
SEED_MAX_SENDS = 5
SEED_RESEND_SETTLE_SECONDS = float(os.environ.get("CHELA_SEED_RESEND_SETTLE_SECONDS") or 1.0)

# Claude Code TUI ready indicators, matched against `tmux capture-pane -p`.
# The prompt glyph marks the (empty) input box and is present in every
# permission mode. The bypass-permissions footer only shows under
# `--permission-mode bypassPermissions`; it's a secondary ready hint (the
# default `--permission-mode auto` does not render it).
_READY_FOOTER = "bypass permissions"
_PROMPT_CHAR = "❯"  # ❯

# Active-work signature (see _pane_shows_activity). The Claude Code TUI draws its
# working spinner (a cycling star glyph) + a live "(<elapsed> · ↓ <n> tokens)" /
# "for <n>m <n>s" status line ABOVE the input box — and the input box below it is
# an EMPTY `❯` while it generates. So `_pane_idle_empty_prompt` returns True for an
# agent that is very much working; without this veto the watchdog false-fails it
# (cmx-158/159/160 were all marked `failed` mid-work, 2026-07-23). The spinner
# frames are version-dependent, so we also key off the token/elapsed counter, which
# only ever appears while Claude is actively generating.
_SPINNER_GLYPHS = "✢✳∗✻✽✶✦✷✸✹✺⋆✻●◐◓◑◒"
_WORK_LINE_RE = re.compile(
    r"↑|↓|\btokens\b|esc to interrupt|(?<!\w)for \d+m|(?<!\w)for \d+s|\(\d+m\b|\(\d+s\b"
)

_PR_NUMBER_RE = re.compile(r"/pull/(\d+)(?:[/?#]|$)")
_PR_REPO_RE = re.compile(r"github\.com/([^/]+)/([^/]+)/pull/\d+")

# --- CI: the one signal that needs no judgment -------------------------------
#
# GitHub OWNS whether a PR's checks passed. ⛔ We never infer it from a local test run —
# the agent that reported "tests pass" on PR #80 was telling the truth about ITS machine,
# and the PR was red on GitHub, and it got merged, and `dev` broke (hotfix 23664e2). That
# is rule (b) of the runtime-truth registry: a fact another system owns must be READ BACK
# FROM THAT SYSTEM. So `pr_checks` on the run row is a CACHE of GitHub's answer, refreshed
# every tick, and it is never written from anything but a `gh` read.
#
# The five states below are exhaustive on purpose, because the interesting ones are the
# three that are NOT "failing":
#   pending  — the checks have not settled. ⛔ A pending run is NOT a red one; sending an
#              agent back mid-CI is a loop fighting itself.
#   none     — the PR has no checks at all. No checks ≠ failing checks — but it is said out
#              loud rather than passed silently.
#   unknown  — gh is missing/offline/rate-limited/unauthenticated. ⛔ NEVER a pass. A check
#              state you could not read is CANNOT VERIFY, loudly (the doctor rule).
CI_PASSING = "passing"
CI_FAILING = "failing"
CI_PENDING = "pending"
CI_NONE = "none"
CI_UNKNOWN = "unknown"

# A check that is FINISHED and did not pass. GitHub reports these as CheckRun.conclusion
# (or StatusContext.state, which uses FAILURE/ERROR only).
CI_FAILED_CONCLUSIONS = (
    "FAILURE", "ERROR", "TIMED_OUT", "CANCELLED", "STARTUP_FAILURE", "ACTION_REQUIRED",
)
# A check that has NOT finished.
CI_UNSETTLED_STATUSES = ("QUEUED", "IN_PROGRESS", "PENDING", "WAITING", "REQUESTED", "EXPECTED")
# The ONLY conclusions that are a pass. Everything conclusive that is not here and not in
# CI_FAILED_CONCLUSIONS DID NOT RUN — see below.
CI_PASSED_CONCLUSIONS = ("SUCCESS",)
# ⛔ A check that finished WITHOUT RUNNING is not a pass. A `paths-ignore` filter, a skipped
# required job, an advisory NEUTRAL — none of them evaluated the code, and a rollup of
# nothing but these is `none` ("nothing ran, said out loud"), never `passing`. The doctor
# rule again: a thing you could not evaluate is never a pass.
CI_DID_NOT_RUN_CONCLUSIONS = ("SKIPPED", "NEUTRAL")

# The statuses whose checks we poll. ⛔ NOT `needs_human`: it is TERMINAL — a human owns
# that run now — so polling it would add a permanent `gh` call per tick, forever, for every
# run that ever escalated.
CI_POLL_STATUSES = ("awaiting_review", "changes_requested")

# A rollup that NEVER settles (a `WAITING` deployment gate with required reviewers, an app
# that registered a check and never reported) parks the PR forever: no verdict fires, the
# merge gate refuses, and nothing says why. It ages out into `needs_human` — the loop is
# allowed to give up, but never allowed to go quiet. Six hours is GitHub's own job ceiling,
# so anything past it is not "still running", it is stuck.
CI_PENDING_STALE_SECONDS = 6 * 3600

# A whole CI log does not fit in a prompt. The tail is where the failure is.
CI_LOG_TAIL_CHARS = 4000
_CI_RUN_ID_RE = re.compile(r"/actions/runs/(\d+)")

# --- ⚖️ the judge: the checks CI cannot run ----------------------------------
#
# CI proves the suite passes. It cannot prove the suite CAN FAIL — it runs the tests, and
# the tests are the thing that is broken (measured on 2026-07-14: four of five CI-green PRs
# had a guard that survived deliberate corruption). The judge is the pass that closes that
# hole, and everything about how a verdict of its becomes a FACT rather than an opinion
# lives in `chela.judge`. Here there is only the trigger.
#
# ⛔ Only a GREEN or a CHECK-LESS PR is judged. A red one is already going back through the
# CI gate (1c) — judging it would race a rework that is about to change the very sha we are
# judging — and a `pending` one has not finished telling us what it is yet.
JUDGE_TRIGGER_CHECKS = (CI_PASSING, CI_NONE)

# One judge at a time, per workflow. Each experiment re-runs the whole suite in its own
# worktree, so a fleet of judges is a fleet of test suites competing for the same box.
JUDGE_MAX_CONCURRENT = 1

# A judge that has not published a verdict in this long is not thinking, it is stuck. It is
# killed and its run becomes CANNOT VERIFY — which blocks nothing and approves nothing.
JUDGE_TIMEOUT_SECONDS = 60 * 60

# --- agent launch command ---------------------------------------------------
#
# The command that spawns an agent is a SHELL STRING, so nothing user-editable
# may ever reach it verbatim. What the dashboard may set is the permission mode
# and nothing else: a closed enum, validated here (server-side) on both write
# and read, with the rest of the command line fixed in code below. A value
# outside the enum fails closed to the built-in default — it never reaches a
# shell. Do NOT turn this into an editable command string: the dashboard is
# reachable over the tailnet, and a free-text `agent.cmd` field would be remote
# code execution by design.
#
# The enum is the mode list the installed CLI actually accepts
# (`claude --help` → --permission-mode choices). Note there is no "default"
# mode; omitting the flag is what "the CLI's own default" means.
PERMISSION_MODES = ("acceptEdits", "auto", "bypassPermissions", "manual", "dontAsk", "plan")

# Default to auto: a classifier auto-approves safe ops and gates dangerous ones.
# NOT bypassPermissions (reckless as an OSS default). Auto-mode is a CLI flag
# only (ignored from .claude/settings.json) and needs Opus/Sonnet 4.6+ on the
# Anthropic API.
DEFAULT_PERMISSION_MODE = "auto"

# The dashboard-writable key in ~/.chela/config.json (see chela.userconfig).
PERMISSION_MODE_KEY = "agent_permission_mode"

# CMX-131: `--strict-mcp-config` with no `--mcp-config` given means the launched CLI
# loads ZERO MCP servers — never the OPERATOR's own (`~/.claude.json` `mcpServers`,
# meant for an interactive human session: browser automation, docs search, ...). A
# dispatched window inherits that global config by default, and each server's connect
# handshake races the TUI's own startup: a late connect (or failed-connect) redraw can
# land AFTER the readiness glyph flips, swallowing the pasted seed prompt's Enter. The
# agent then sits idle forever with the prompt typed but never submitted — the
# "unattended dispatch hangs" bug this isolates. Applies to every dispatched window
# (coding agent, rework, judge) via this one constant; an explicit `agent.cmd` in
# WORKFLOW.md still bypasses it entirely (see resolve_agent_cmd's precedence).
AGENT_BASE_CMD = "claude --strict-mcp-config"

# --- coding-agent model (CMX-91) --------------------------------------------
#
# The MODEL the dispatched CODING agents run on — a Settings choice like the
# permission mode, and it rides the exact same rails (validated closed enum,
# fail-closed to the default, interpolated into a shell string). Default `sonnet`:
# cmx tasks rarely need Opus, and Sonnet is cheaper/faster. ("Sonnet 200k" is
# plain `--model sonnet`; the 1M-window variant is separate and not used here.)
#
# The enum is the alias set `claude --model` accepts. Members must be shell-inert
# for the same reason the permission modes are — they reach a shell verbatim.
AGENT_MODELS = ("sonnet", "opus", "haiku")
DEFAULT_AGENT_MODEL = "sonnet"

# The dashboard-writable key in ~/.chela/config.json (see chela.userconfig).
AGENT_MODEL_KEY = "agent_model"

# ⛔ THE JUDGE IS NOT DOWNGRADED. It is the adversarial safety net — the pass that
# caught the wiring gaps and the "proof that cannot fail" class CI cannot — so it
# runs on a fixed CAPABLE model, decoupled from the coding-agent Settings choice:
# a `sonnet`/`haiku` default set for the fleet must never reach it. This is a v1
# constant, deliberately NOT a user-facing dropdown (see resolve_agent_cmd's
# ``role``). It is not read from userconfig, so no Settings write can touch it.
DEFAULT_JUDGE_MODEL = "opus"

# CMX-115: the daemon carries this so PRODUCTION can push real phone notifications
# (~/.chela/chela.env), but a dispatched agent or judge window is spawned inside the
# same tmux session and inherits that same environment. Neither may ever hold it — a
# worker that runs inbox/notify code (or the judge's full pytest pass) would otherwise
# fire a REAL ntfy at the human's phone with whatever test fixture is lying around.
AGENT_ENV_STRIP = ("CHELA_NOTIFY_URL",)


def _strip_agent_env_cmd() -> str:
    """The shell line sent into a fresh agent/judge window before its agent command.

    A plain ``unset`` in THAT window's own shell — surgical (only the named vars,
    nothing else the agent needs) and scoped to this one pane, not the tmux
    session's stored environment, so it never touches any other window (including
    ones a human opens by hand) or the daemon's own process.
    """
    return " ".join(("unset", *AGENT_ENV_STRIP))


def settings_permission_mode() -> str | None:
    """The permission mode set from Settings, or None if unset/invalid.

    Read defensively: the dispatcher runs unattended under PM2, so a missing,
    corrupt, or hand-edited config.json must degrade to "unset" (→ built-in
    default), never crash the daemon loop and never yield an unvalidated string.
    """
    try:
        from chela import userconfig
        val = userconfig.get(PERMISSION_MODE_KEY)
    except Exception:  # unreadable config, import failure — fail closed
        return None
    return val if val in PERMISSION_MODES else None


def settings_agent_model() -> str | None:
    """The coding-agent model set from Settings, or None if unset/invalid.

    Read defensively, exactly like :func:`settings_permission_mode`: a missing,
    corrupt, or hand-edited config.json degrades to "unset" (→ built-in default),
    and a value outside :data:`AGENT_MODELS` is treated as unset rather than
    interpolated into the shell command — the model reaches a shell verbatim, so
    only the enum may.
    """
    try:
        from chela import userconfig
        val = userconfig.get(AGENT_MODEL_KEY)
    except Exception:  # unreadable config, import failure — fail closed
        return None
    return val if val in AGENT_MODELS else None


def agent_model_for(role: str = "coding") -> str:
    """The model an agent of this ``role`` launches on.

    ⛔ The one place the coding/judge split lives. The JUDGE gets the fixed
    :data:`DEFAULT_JUDGE_MODEL` and NEVER the coding-agent Settings model, so a
    `sonnet`/`haiku` default set for the fleet cannot downgrade the adversarial
    pass. Everything else is a coding agent → the Settings model, or the
    :data:`DEFAULT_AGENT_MODEL` default. Anything that is not the judge falls to
    the coding model on purpose: a mistaken role gives the human-owned coding
    default, never a silent judge downgrade.
    """
    if role == "judge":
        return DEFAULT_JUDGE_MODEL
    return settings_agent_model() or DEFAULT_AGENT_MODEL


def resolve_agent_cmd(wf: WorkflowDef, role: str = "coding") -> tuple[str, str]:
    """The command that launches an agent, and where it came from.

    PRECEDENCE (highest first) — the one place this is decided:

      1. ``agent.cmd`` in WORKFLOW.md  → source ``"workflow"``.
         An explicit per-workflow override stays authoritative: it is set by
         someone who can already write files in the repo, so it may be any
         command (including its own ``--model``), and it deliberately shadows
         Settings.
      2. the Settings permission mode → source ``"settings"``.
         ``agent_permission_mode`` in ~/.chela/config.json, written by the
         dashboard. Only ever one of :data:`PERMISSION_MODES`; anything else is
         treated as unset (fail closed) rather than interpolated.
      3. the built-in default        → source ``"default"``.
         ``claude --permission-mode auto``.

    Because (1) shadows (2), a workflow that pins ``agent.cmd`` makes the
    Settings control inert *for that workflow* — which is why chelamux's own
    WORKFLOW.md no longer sets it, and why the dashboard surfaces the winning
    source instead of implying the setting always applies.

    THE MODEL rides on top of (2)/(3) via ``--model`` (never (1) — a pinned
    ``agent.cmd`` already carries its own). ``role`` decides which model:
    ``"coding"`` → the Settings coding model (default `sonnet`); ``"judge"`` →
    the fixed capable :data:`DEFAULT_JUDGE_MODEL`, so the judge is decoupled from
    the fleet's coding-model choice. The ``source`` reported is the permission
    mode's origin, unchanged — the model does not have its own precedence chain.

    Returns ``(cmd, source)``.
    """
    cmd = wf.get("agent", "cmd", default=None)
    if isinstance(cmd, str) and cmd.strip():
        return cmd.strip(), "workflow"
    mode = settings_permission_mode()
    permission_mode = mode or DEFAULT_PERMISSION_MODE
    source = "settings" if mode else "default"
    model = agent_model_for(role)
    return f"{AGENT_BASE_CMD} --permission-mode {permission_mode} --model {model}", source


def _git(repo: Path, *args: str, timeout: float = GIT_TIMEOUT_SECONDS):
    """Run a git command in `repo`. Returns None if git is missing or hung."""
    try:
        return subprocess.run(
            ["git", "-C", str(repo), *args],
            capture_output=True, text=True, timeout=timeout,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as e:
        log.warning("git %s failed in %s: %s", " ".join(args), repo, e)
        return None


def _git_ok(cp) -> bool:
    return cp is not None and cp.returncode == 0


def _git_out(cp) -> str:
    return cp.stdout.strip() if _git_ok(cp) else ""


def _claim_order(wf: WorkflowDef, source, on_disk: list[Task]) -> list[Task]:
    """The queue AS OF THE INSTANT OF CLAIMING — re-read from ``origin/<base_branch>``.

    FETCH-THEN-CLAIM. The tick's own parse happens before reconciliation, PR polling and
    the tracker strike, all of which touch the network; by the time a slot is actually
    free that parse can be seconds to minutes old, and the orchestrator may have pushed a
    reordered queue in the meantime. So the queue is re-read here, at the last possible
    moment, from the branch the orchestrator actually pushes to — not from a parse we made
    earlier in the tick, and not from a working tree that may be mid-edit.

    ⛔ **This is necessary and it is NOT sufficient. It does not fix the race.** The race
    that hurts is: a PR merges → reconciliation frees the only slot → the orchestrator
    starts *writing* the next task, which takes MINUTES → our tick fires long before the
    push lands and claims the old top item. The edit genuinely does not exist yet at claim
    time, and no amount of freshness can read a file that has not been written. The fix
    for that is the queue HOLD (see :mod:`chela.hold`), which the orchestrator takes
    *before* it starts rewriting. Fetch-then-claim closes the tail of the window for
    free — an edit pushed while this tick was busy IS honoured — and nothing more.

    Degrades, never blocks: no remote, no network, a tracker that is not a file
    (``gh_issues`` reads the live API on every call, so it is already claim-fresh), or any
    git failure → the on-disk order stands, exactly as before. A dispatcher that refuses
    to work offline is a worse bug than the one being fixed here.

    Tasks that exist on disk but not on ``origin`` (an item the orchestrator has written
    but not yet pushed) are kept, appended AFTER origin's — they are real work and must
    not vanish from a local-only checkout — but they never outrank what was actually
    pushed. Tasks ``origin`` has already STRUCK are dropped, even if this checkout has not
    pulled the strike yet.

    Whatever order comes out of the above is then run through :func:`_ready`, which drops
    any task whose declared ``depends`` (the markdown tracker's blocking edges, see
    :mod:`chela.sources.markdown`) are not yet all struck done — a task that has not merged
    yet cannot be the base a same-day follow-up forks its worktree from.
    """
    tasks_from_text = getattr(source, "tasks_from_text", None)
    closed_ids_from_text = getattr(source, "closed_ids_from_text", None)
    tracker = getattr(source, "path", None)
    if tasks_from_text is None or closed_ids_from_text is None or tracker is None:
        return _ready(on_disk, set())

    repo = wf.path.parent
    base = wf.get("workspace", "base_branch", default="master")
    try:
        rel = str(Path(tracker).relative_to(repo))
    except ValueError:
        return _ready(on_disk, _local_closed_ids(closed_ids_from_text, tracker))
    if not _git_out(_git(repo, "remote")):
        return _ready(on_disk, _local_closed_ids(closed_ids_from_text, tracker))
    if not _git_ok(_git(repo, "fetch", "origin", base, timeout=GIT_NET_TIMEOUT_SECONDS)):
        log.warning(
            "claim: could not fetch origin/%s — claiming from the on-disk tracker, which "
            "may be stale", base,
        )
        return _ready(on_disk, _local_closed_ids(closed_ids_from_text, tracker))
    show = _git(repo, "show", f"FETCH_HEAD:{rel}")
    if not _git_ok(show):
        log.warning("claim: %s is not on origin/%s — claiming from the on-disk tracker", rel, base)
        return _ready(on_disk, _local_closed_ids(closed_ids_from_text, tracker))

    text = show.stdout
    remote_tasks = tasks_from_text(text)
    closed_ids = closed_ids_from_text(text)
    known = {t.id for t in remote_tasks} | closed_ids
    unpushed = [t for t in on_disk if t.id not in known]
    if unpushed:
        log.debug(
            "claim: %d task(s) on disk are not on origin/%s yet; queued after the pushed ones",
            len(unpushed), base,
        )
    return _ready(remote_tasks + unpushed, closed_ids)


def _local_closed_ids(closed_ids_from_text, tracker: Path) -> set[str]:
    """`closed_ids_from_text` applied to the LOCAL on-disk tracker — the best a claim
    that could not read ``origin`` (no remote, a failed fetch, a tracker not yet
    pushed) can still do for :func:`_ready`. Never raises: a tracker that has gone
    missing out from under a running dispatcher degrades to "nothing is closed",
    same as the rest of this function's offline fallbacks.
    """
    try:
        text = Path(tracker).read_text()
    except OSError:
        return set()
    return closed_ids_from_text(text)


def _ready(tasks: list[Task], closed_ids: set[str]) -> list[Task]:
    """Drop tasks whose declared dependencies (``Task.depends`` — the markdown
    tracker's ``<!-- depends: ... -->`` marker) have not merged yet.

    A dependency counts as satisfied ONLY once its own task is struck ``[x]`` in
    the tracker — an id that is merely open (claimed, running, still queued) or
    not recognised at all (a stale reference: a typo, a retitled or deleted
    task) both fail CLOSED. The alternative — treating an unresolved reference as
    satisfied — would let one bad edit in the tracker silently defeat the entire
    feature; a permanently blocked task at least stays visible in the queue and
    fails loud, which is exactly the frontier this exists to enforce: a task with
    an unmet dependency is not the next thing to claim, full stop, regardless of
    its position in the file.

    ``tasks`` is always the FULL universe of currently open candidates (every
    caller passes its complete on-disk/remote task list, never a pre-filtered
    subset — see ``_claim_order``), so ``{t.id for t in tasks} | closed_ids`` is
    every id that could ever legitimately be named. A dependency reference
    outside that set can never resolve — a typo, or a retitled/deleted blocker,
    since edges are keyed on title text (``_title_id``) — and is a TRACKER BUG,
    not an ordinary wait: it warrants ``log.warning``. A reference that DOES
    resolve but whose task simply hasn't been struck yet is the normal, expected
    case and only warrants ``log.info`` — warning on every unmet dependency would
    drown the one case that actually needs a human's attention.
    """
    known_ids = {t.id for t in tasks} | closed_ids
    ready = []
    for t in tasks:
        unmet = set(t.depends) - closed_ids
        if unmet:
            unresolved = unmet - known_ids
            if unresolved:
                log.warning(
                    "claim: task %s (%s) held back — depends on %d unresolved reference(s) "
                    "(no open or closed task matches — a typo, or a retitled/deleted "
                    "dependency): %s",
                    t.id, t.title, len(unresolved), ", ".join(sorted(unresolved)),
                )
            else:
                log.info(
                    "claim: task %s (%s) held back — depends on %d task(s) not yet merged: %s",
                    t.id, t.title, len(unmet), ", ".join(sorted(unmet)),
                )
            continue
        ready.append(t)
    return ready


def _tracker_is_versioned(repo: Path, rel: str) -> bool:
    """Whether `rel` is eligible to ever land on `origin/<base>` — i.e. NOT excluded by
    `repo`'s own `.gitignore` — used by :func:`_strike_merged_tasks` and
    :func:`_write_trial_ledger` to decide whether an unattended write may go through the
    isolated base-write worktree at all.

    Deliberately `git check-ignore`, NOT `git ls-files --error-unmatch`: both a
    gitignored path AND a path that is simply new — never yet committed by anyone, e.g.
    a `trial_ledger:` on its very first write, still untracked because nothing has
    written it yet — return the same "unknown to git" result from `ls-files
    --error-unmatch`, so that check cannot tell "deliberately local-only" apart from
    "not created yet". `check-ignore` can: it matches gitignore *patterns* against a
    pathname regardless of whether the path exists or has ever been committed, so a
    brand-new, non-ignored path still reads as versioned (eligible), while
    chelamux's own gitignored `TODO.md` reads as not.

    A git failure (missing binary, timeout) or an ambiguous `check-ignore` result
    (exit 128) both default to `True` — the safe direction is to keep using the
    isolated worktree machinery, not to silently fall back to writing `repo` in place.
    """
    cp = _git(repo, "check-ignore", "-q", "--", rel)
    return cp is None or cp.returncode != 0


BASE_WRITE_DIRNAME = "_base-write"


def _base_write_worktree(repo: Path, base: str, root: Path, what: str) -> Path | None:
    """The isolated, chela-owned checkout every unattended ``base_branch`` writer — the
    tracker strike and the trial ledger (see :func:`_strike_merged_tasks` and
    :func:`_write_trial_ledger`) — actually reads, edits, commits and pushes from.
    NEVER ``repo`` itself.

    ``repo`` is the human's interactive checkout. It can be on any branch, mid-rebase,
    with edits staged, at whatever moment a tick happens to fire — and none of that has
    any bearing on whether chela is allowed to advance ``base_branch``. The old precheck
    read ``repo``'s HEAD and working tree and skipped (silently, forever, until a human
    noticed the warning log) the instant either didn't match what an unattended writer
    needs. A DETACHED worktree at ``root/_base-write`` — reused across ticks, one per
    workspace root — sidesteps the whole class of failure: nothing but this function ever
    touches it, so its own staleness or dirt is never a human's to have caused, and every
    precondition below is a self-heal, not a "leave it for a human" skip. Detached, not a
    branch checkout, on purpose: ``repo`` (or a rework worktree) very often already has
    ``base`` checked out, and ``git worktree add <branch>`` refuses a branch that is live
    anywhere else — detached HEAD has no such conflict, and ``git push HEAD:base`` does
    not care whether HEAD is a branch or a bare commit.

    :func:`worktree.detached_worktree` does the self-heal for free: reusing an existing
    worktree resets it (``checkout --detach --force``) and cleans it (``clean -fdx``)
    before handing it back, discarding anything left over from an interrupted previous
    write — which is always safe here, since nothing legitimate is ever left uncommitted
    in this worktree between calls.

    FAILS CLOSED only on what is genuinely not ours to fix: no remote to write through,
    or a fetch that fails. Returns ``None`` when the caller should write nothing this
    tick and simply retry the next one; otherwise the worktree path, fetched to
    ``origin/<base>`` and clean.
    """
    remote = _git_out(_git(repo, "remote"))
    if not remote:
        log.warning("%s skipped: %s has no remote to write %s through", what, repo, base)
        return None
    if not _git_ok(_git(repo, "fetch", "origin", base, timeout=GIT_NET_TIMEOUT_SECONDS)):
        log.warning("%s skipped: could not fetch origin/%s", what, base)
        return None

    # Resolve FETCH_HEAD to a concrete sha IN `repo`'s context, right here — FETCH_HEAD
    # is a per-worktree pseudo-ref (`repo/.git/FETCH_HEAD`, not the shared common dir), so
    # handing the literal string to `detached_worktree` works for a brand NEW worktree
    # (created via `git -C repo worktree add`, still `repo`'s context) but silently fails
    # to resolve on REUSE (`git -C wt checkout`, the isolated worktree's own context) —
    # not a hard failure, just a `BranchGone` neither raises: `checkout` errors out, the
    # fallback path deletes and recreates the whole worktree instead of the fast reset
    # this function's docstring promises, every single tick. A concrete sha resolves
    # identically from any worktree sharing this repo's object store.
    sha = _git_out(_git(repo, "rev-parse", "FETCH_HEAD"))
    if not sha:
        log.warning("%s skipped: FETCH_HEAD did not resolve after fetching origin/%s", what, base)
        return None

    wt_path = (root / BASE_WRITE_DIRNAME).resolve()
    try:
        wt_path, _ = detached_worktree(repo, sha, wt_path)
    except BranchGone:
        log.warning("%s skipped: origin/%s did not resolve after fetch", what, base)
        return None
    except subprocess.CalledProcessError as e:
        log.warning("%s skipped: could not prepare the base-write worktree: %s", what, e)
        return None
    return wt_path


def _base_write_commit_push(wt: Path, base: str, rel: str, subject: str, body: str, what: str) -> bool:
    """Commit ``rel`` (already rewritten on disk by the caller, in the isolated worktree
    ``wt`` — see :func:`_base_write_worktree` — possibly a BRAND NEW path, e.g. the trial
    ledger's first-ever write) and push to ``base_branch``, rolling the local commit back
    if the push is rejected — someone else pushed between our fetch and our push, so this
    retries next tick rather than force-pushing. Never commits a path other than ``rel``.

    ``git commit -- rel`` alone (no prior ``git add``) only picks up changes to
    a path git already tracks; a never-before-committed ``rel`` is silently
    left out — so this always stages it first, and its rollback path has to
    know the difference: a path that existed at ``HEAD`` is restored FROM
    ``HEAD`` (``checkout HEAD -- rel``), one that did not is unstaged and
    deleted — ``checkout HEAD -- rel`` on a path HEAD has never heard of
    errors out and would leave it staged-but-uncommitted forever, failing
    every future tick's "clean tree" precheck.
    """
    parent = _git_out(_git(wt, "rev-parse", "HEAD"))
    existed_before = bool(parent) and _git_ok(_git(wt, "cat-file", "-e", f"{parent}:{rel}"))

    def _restore() -> None:
        if existed_before:
            _git(wt, "checkout", "HEAD", "--", rel)
        else:
            _git(wt, "reset", "--", rel)
            (wt / rel).unlink(missing_ok=True)

    if not _git_ok(_git(wt, "add", "--", rel)):
        log.warning("%s: git add failed for %s", what, rel)
        return False
    if not _git_ok(_git(wt, "commit", "-m", subject, "-m", body, "--", rel)):
        log.warning("%s: commit failed; restoring %s", what, rel)
        _restore()
        return False

    mine = _git_out(_git(wt, "rev-parse", "HEAD"))
    if not _git_ok(_git(wt, "push", "origin", f"HEAD:{base}", timeout=GIT_NET_TIMEOUT_SECONDS)):
        if parent and mine and _git_out(_git(wt, "rev-parse", "HEAD")) == mine:
            _git(wt, "reset", "--soft", parent)
            _restore()
        log.warning("%s: push to %s rejected — rolled back, retrying next tick", what, base)
        return False
    return True


def _strike_merged_tasks(wf: WorkflowDef, source, task_ids: list[str]) -> int:
    """Mark merged tasks `- [x]` in the tracker, on base_branch, in ONE commit.

    The dispatcher is the tracker's *only* writer. Agents used to strike their
    own line inside their branch while the orchestrator kept appending items to
    base_branch behind them — two writers, both editing the top of one file, so
    every single dispatched PR conflicted on it. Now the agent never touches the
    tracker and the strike happens here, once the PR has actually merged. That
    also makes the checkbox mean *merged* rather than *the agent believed it was
    finished*, which is strictly more truthful.

    Tasks are matched by their stable task id (the hash of the line's title), not
    by fuzzy text, and the strike is idempotent — see markdown.strike_lines.

    This runs unattended under PM2, so every step FAILS CLOSED — see
    :func:`_base_write_worktree` / :func:`_base_write_commit_push`, which this
    shares with :func:`_write_trial_ledger`. It writes through the isolated
    base-write worktree, never ``wf.path.parent`` (the human's interactive
    checkout) — see :func:`_base_write_worktree` for why. A missed checkbox is
    cosmetic and self-heals — the pending set is recomputed from the runs
    table on every tick, not remembered — whereas a mangled base branch is
    not.

    Not every tracker is even eligible for this at all: a gitignored tracker (chelamux's
    own `TODO.md` — a deliberate, local, per-install queue, never committed) does not
    exist in `origin/<base>`'s tree and never will, so the isolated base-write worktree
    (see :func:`_base_write_worktree`) can never see it either — routing the strike
    through it there would silently and permanently no-op the strike (a regression
    caught in review, CMX-174 round 1). :func:`_tracker_is_versioned` branches on this
    BEFORE any worktree/fetch/commit/push machinery: an ineligible tracker is struck
    directly, in place, on `repo`'s own tracker file — no isolation needed, because
    nothing there is ever pushed anywhere. This is the normal, intended path for a
    local-only queue, not a degraded fallback, so it never warns.

    Returns the number of lines actually struck.
    """
    close_tasks = getattr(source, "close_tasks", None)
    tracker = getattr(source, "path", None)
    if close_tasks is None or tracker is None:
        # A non-file tracker (gh_issues) closes itself: the merged PR closes the
        # issue, so the task leaves list_open_tasks with no write from us.
        return 0

    repo = wf.path.parent
    base = wf.get("workspace", "base_branch", default="master")
    try:
        rel = str(Path(tracker).relative_to(repo))
    except ValueError:
        log.warning("tracker strike skipped: %s is outside the repo %s", tracker, repo)
        return 0

    if not _tracker_is_versioned(repo, rel):
        struck = _log_and_collect_struck(close_tasks(task_ids), "tracker strike")
        if struck:
            log.info("tracker strike: marked %d task(s) done in %s: %s", len(struck), rel, ", ".join(struck))
        return len(struck)

    root = resolve_workspace_root(wf)
    wt = _base_write_worktree(repo, base, root, "tracker strike")
    if wt is None:
        return 0

    try:
        results = close_tasks(task_ids, at=wt / rel)
    except OSError as e:
        log.warning("tracker strike failed to write %s: %s", rel, e)
        _git(wt, "checkout", "--", rel)
        return 0

    struck = _log_and_collect_struck(results, "tracker strike")
    if not struck:
        return 0

    subject = f"chore({rel}): strike {len(struck)} merged task" + ("s" if len(struck) > 1 else "")
    body = "\n".join(f"- {tid}" for tid in struck)
    if not _base_write_commit_push(wt, base, rel, subject, body, "tracker strike"):
        return 0

    log.info("tracker strike: marked %d task(s) done on %s: %s", len(struck), base, ", ".join(struck))
    return len(struck)


def _log_and_collect_struck(results: dict[str, str], what: str) -> list[str]:
    """Shared between the two `_strike_merged_tasks` paths (in-place and base-write):
    log the missing/already-struck outcomes and return the sorted ids actually struck."""
    for tid, outcome in sorted(results.items()):
        if outcome == "missing":
            log.warning(
                "%s: no line matches task %s — a human edited or removed it; not guessing",
                what, tid,
            )
        elif outcome == "already":
            log.info("%s: task %s was already struck", what, tid)
    return sorted(t for t, outcome in results.items() if outcome == "struck")


# --- the trial ledger (CMX-105) ---------------------------------------------
#
# lean-alpha's honesty harness deflates a probe's Sharpe by N = number of trials run
# ("run 100, keep the best" is the classic false-positive machine — the bar must rise
# with N). A hand-maintained N, written by the probe agent into its own repo, is exactly
# what a fan-out would game: dispatch 20 probes, register only the 1 winner, N stays low,
# every OTHER probe's bar stays low too. To be honest under fan-out, N must be owned by
# something the agent cannot undercount, and it must count trials that never merged — a
# died/abandoned probe leaves no PR and no row in the repo's own tracker, but it WAS a
# trial. chela already has the ground truth (the `runs` table records every dispatched
# run); this projects it into a committed, git-visible artifact a repo's own guards can
# read — including in a throwaway judge worktree and in GitHub CI, neither of which has
# `~/.chela/scheduler.db`.
#
# GENERIC and opt-in on purpose: chela does not know what PROBES.md or a DSR guard is,
# and must not — the layering is "chela counts dispatched trials, the repo decides the
# statistical use". A workflow that never sets `trial_ledger:` gets no ledger, no extra
# git writes, byte-unchanged behavior.

def _trial_ledger_rel(wf: WorkflowDef) -> str | None:
    """This workflow's trial-ledger path, relative to its repo — or None when it has
    not opted in (no `trial_ledger:` key in the WORKFLOW.md front matter)."""
    raw = wf.get("trial_ledger")
    if not raw:
        return None
    repo = wf.path.parent
    p = (repo / raw).resolve()
    try:
        return str(p.relative_to(repo))
    except ValueError:
        log.warning("trial ledger skipped: %s is outside the repo %s", raw, repo)
        return None


def _parse_trial_ledger(text: str) -> list[dict]:
    """One JSON object per non-blank line — the file's on-disk form."""
    return [json.loads(line) for line in text.splitlines() if line.strip()]


def _format_trial_ledger(entries: list[dict]) -> str:
    return "".join(json.dumps(e, sort_keys=True) + "\n" for e in entries)


def _run_trial_outcome(row: sqlite3.Row) -> str | None:
    """The terminal outcome for a `runs` row, or None while it is still pending — in
    flight, or a `failed` row with retries left (the claim loop may still re-dispatch
    the SAME task_id, and that is the same trial, not a new one — see
    :func:`reconcile_trial_ledger`).

    `died`: a `failed` row at MAX_ATTEMPTS — the exact same "no more retries" test the
    claim loop itself uses to refuse a re-claim, so a row this function calls terminal is
    a row that will never move again.
    `abandoned`: `done` without a merged PR — the task left the tracker (a human edit, a
    re-hash, a manually-closed PR) with no completion evidence. The trial ran and was
    walked away from, not shipped.
    `merged`: the PR landed. Checked first — `pr_state` can be `merged` on a row whose
    `status` is not yet `done` for one tick (phase-0 PR-state refresh runs before the
    reconcile step that flips `status`), and a trial that merged is never "abandoned".
    """
    if row["pr_state"] == "merged":
        return "merged"
    if row["status"] == "failed" and (row["attempt"] or 1) >= MAX_ATTEMPTS:
        return "died"
    if row["status"] == "done":
        return "abandoned"
    return None


def reconcile_trial_ledger(existing_text: str, rows: list[sqlite3.Row]) -> tuple[str, list[str], list[str]]:
    """Pure merge of the ledger's current text with `rows` (this workflow's `runs`
    table). Returns ``(new_text, appended_ids, resolved_ids)``.

    * A `task_id` with no line yet gets exactly ONE appended, at the end — its outcome
      is the row's current terminal outcome, or `"pending"` while still in flight. The
      line exists the moment a `runs` row exists, before any PR and before any merge:
      THAT is the dispatcher-owned trial count.
    * A `"pending"` line whose row has since resolved gets its `outcome` field updated
      IN PLACE — same position, same `task_id` — never a second line. Re-dispatch and
      rework of the same `task_id` is the same trial, so this is the only way an
      existing line ever changes.
    * Every OTHER existing line survives untouched — including one whose `task_id` no
      longer appears in `rows` (pruned by `_prune_done_rows`, or simply not the current
      workflow_path). The ledger only ever grows or resolves, never shrinks: a
      died/abandoned trial keeps its line forever, so a fan-out cannot game N by letting
      its losers age out of the `runs` table.
    """
    entries = _parse_trial_ledger(existing_text)
    by_id = {e["task_id"]: e for e in entries}
    appended: list[str] = []
    resolved: list[str] = []
    for row in rows:
        tid = row["task_id"]
        outcome = _run_trial_outcome(row)
        entry = by_id.get(tid)
        if entry is None:
            entry = {
                "task_id": tid,
                "dispatched_at": row["started_at"] or "",
                "outcome": outcome or "pending",
            }
            entries.append(entry)
            by_id[tid] = entry
            appended.append(tid)
        elif entry.get("outcome") == "pending" and outcome:
            entry["outcome"] = outcome
            resolved.append(tid)
    if not appended and not resolved:
        return existing_text, [], []
    return _format_trial_ledger(entries), appended, resolved


def _write_trial_ledger(wf: WorkflowDef, conn: sqlite3.Connection) -> int:
    """Project this workflow's `runs` table onto its trial ledger, on base_branch, in
    ONE commit — see :func:`reconcile_trial_ledger` for the merge and
    :func:`_base_write_worktree` / :func:`_base_write_commit_push` for the unattended-
    write discipline this shares with :func:`_strike_merged_tasks`. Writes through the
    isolated base-write worktree, never `wf.path.parent` (the human's interactive
    checkout) — see :func:`_base_write_worktree` for why.

    Opt-in via `trial_ledger: <path>` (CMX-105) — a workflow without the key returns 0
    immediately, no git touched, no behavior change. Scoped to `workflow_path`, same as
    every other per-workflow query here: a shared repo with two workflows must not have
    one's trials counted on the other's ledger.

    A `trial_ledger:` opt-in is a committed artifact BY DEFINITION — the whole point is
    a git-visible count a repo's own guards can read, including from a throwaway judge
    worktree that never sees `~/.chela/scheduler.db`. If the configured path is
    gitignored (see :func:`_tracker_is_versioned`), this warns and writes nothing rather
    than silently keeping an honesty ledger that only ever exists on one box — worse
    than no ledger at all, since a repo's guard would trust it.

    Returns the number of NEW trial lines appended this call.
    """
    rel = _trial_ledger_rel(wf)
    if rel is None:
        return 0

    repo = wf.path.parent
    if not _tracker_is_versioned(repo, rel):
        log.warning(
            "trial ledger skipped: %s is gitignored in %s — a trial_ledger must be a "
            "committed artifact; point it at a path that is not excluded", rel, repo,
        )
        return 0

    rows = conn.execute(
        "SELECT task_id, started_at, status, attempt, pr_state FROM runs WHERE workflow_path=?",
        (str(wf.path),),
    ).fetchall()

    def _reconcile(text: str):
        # A ledger this process never wrote invalid JSON into can only be
        # invalid because a human hand-edited it mid-save — degrade, don't die
        # (workflow.py's rule for a bad WORKFLOW.md applies here too): skip
        # this tick rather than crash the whole reconcile loop over one file.
        try:
            return reconcile_trial_ledger(text, rows)
        except (ValueError, KeyError) as e:
            log.warning("trial ledger: %s is not valid — skipping this tick: %s", rel, e)
            return None

    base = wf.get("workspace", "base_branch", default="master")
    root = resolve_workspace_root(wf)

    # Cheap check first — no git touched when nothing would change, same as the
    # tracker strike's caller only invoking it `if pending_strikes:`. Reads whatever
    # this workflow's isolated base-write worktree last saw (possibly stale — another
    # writer may have moved base_branch since; that is fine, the real write below
    # re-reads fresh) rather than `wf.path.parent`, which the human checkout may have
    # moved to an unrelated branch entirely.
    cached = (root / BASE_WRITE_DIRNAME / rel)
    merged = _reconcile(cached.read_text() if cached.exists() else "")
    if merged is None:
        return 0
    _, appended, resolved = merged
    if not appended and not resolved:
        return 0

    wt = _base_write_worktree(repo, base, root, "trial ledger")
    if wt is None:
        return 0

    # Re-read AFTER the worktree is fetched and self-healed, and re-reconcile against
    # whatever is on base_branch NOW, in case it moved since the cheap read above —
    # the same "recompute, don't trust the pre-network read" discipline
    # `_strike_merged_tasks` gets from `close_tasks` reading the tracker itself.
    path = wt / rel
    merged = _reconcile(path.read_text() if path.exists() else "")
    if merged is None:
        return 0
    new_text, appended, resolved = merged
    if not appended and not resolved:
        return 0
    path.write_text(new_text)

    subject = f"chore({rel}): trial ledger — {len(appended)} new, {len(resolved)} resolved"
    body = "\n".join(f"+ {tid}" for tid in appended) + ("\n" if appended and resolved else "") + \
        "\n".join(f"~ {tid}" for tid in resolved)
    if not _base_write_commit_push(wt, base, rel, subject, body, "trial ledger"):
        return 0

    log.info(
        "trial ledger: %d new trial(s), %d resolved on %s (%s)",
        len(appended), len(resolved), base, rel,
    )
    return len(appended)


def ensure_schema(conn: sqlite3.Connection) -> sqlite3.Connection:
    """Create/migrate the ``runs`` table on ``conn``. Idempotent.

    Public because the tests build their run rows against it: three test modules
    each carried a hand-copied CREATE TABLE, and every new column had to be added
    to all four by hand — which is how a schema change turns into four failing
    tests instead of one. One definition, no drift.
    """
    conn.execute("""
        CREATE TABLE IF NOT EXISTS runs (
            task_id TEXT PRIMARY KEY,
            workflow_path TEXT NOT NULL,
            title TEXT NOT NULL,
            status TEXT NOT NULL,
            window_name TEXT,
            window_id TEXT,
            worktree_path TEXT,
            branch_name TEXT,
            started_at TEXT,
            ended_at TEXT,
            attempt INTEGER NOT NULL DEFAULT 1,
            last_error TEXT,
            pr_url TEXT,
            pr_state TEXT
        )
    """)
    # Idempotent migrations for pre-existing DBs. A row written before a column
    # existed simply reads NULL there — never a crash: this runs unattended.
    for _column, ddl in (
        ("pr_url", "ALTER TABLE runs ADD COLUMN pr_url TEXT"),
        ("pr_state", "ALTER TABLE runs ADD COLUMN pr_state TEXT"),
        ("pr_mergeable", "ALTER TABLE runs ADD COLUMN pr_mergeable TEXT"),
        ("task_number", "ALTER TABLE runs ADD COLUMN task_number INTEGER"),
        ("idle_nudged_at", "ALTER TABLE runs ADD COLUMN idle_nudged_at TEXT"),
        # The tmux @id of the window this run was spawned into. Recorded at spawn
        # because that is the only lossless moment: the agent kills its own window
        # (`chela task-finished`) BEFORE the run reconciles to awaiting_review, so a
        # live-tmux lookup at event time is always too late and every run_review
        # landed ownerless. A pre-existing row simply has NULL here and falls back
        # to the by-name lookup (inbox.run_wid).
        ("window_id", "ALTER TABLE runs ADD COLUMN window_id TEXT"),
        # ...and the tmux server that ISSUED that id (CMX-77). `@3` is an ADDRESS, not an
        # identity: tmux numbers windows per server, so after a restart (an OOM took ours on
        # 2026-07-14) the same `@3` names a different agent entirely. A recorded id with no
        # epoch beside it cannot be told apart from one that is still good, and acting on it
        # files a dead run's events under a LIVE agent's lane — a wrong wid is worse than no
        # wid (CMX-48). Readers (inbox.run_wid, runtime_truth) drop the id when this stamp is
        # not the epoch running now. A pre-existing row reads NULL and is treated as
        # unverifiable, not as current.
        ("window_epoch", "ALTER TABLE runs ADD COLUMN window_epoch TEXT"),
        # The rework loop (CMX-68). `rework_count` is the bounded-loop counter — it is
        # incremented when a rework is actually SPAWNED, not when the verdict is written,
        # so a verdict that never gets a slot cannot burn a round. `review_history` is
        # every verdict this run has ever received, as a JSON list (see reviews_of): the
        # escalation to needs_human carries the history of what was tried, not just the
        # last thing anyone said.
        ("rework_count", "ALTER TABLE runs ADD COLUMN rework_count INTEGER DEFAULT 0"),
        ("review_history", "ALTER TABLE runs ADD COLUMN review_history TEXT"),
        # CI (CMX-69). `pr_checks` is our CACHE of GitHub's rollup (one of CI_*), refreshed
        # from `gh` every tick — the UI and the merge gate read it. `pr_head_sha` is the
        # commit that state belongs to. `ci_failed_sha` is the commit whose red CI has
        # ALREADY been turned into a verdict: it is what makes each red fire exactly ONCE.
        # ⛔ Without it a run that fails CI, gets reworked, and comes back on an unchanged
        # SHA would burn the entire CHELA_MAX_REWORKS budget in three ticks having done
        # nothing. A NEW push that is also red is a new SHA, and a new verdict.
        # `ci_pending_since` is when the CURRENT unsettled spell began (cleared the moment
        # the checks settle, restarted by a new head sha). A rollup that never settles would
        # otherwise park the run forever — no verdict, no merge, no exit; it ages out into
        # needs_human instead. The loop may give up. It may not go quiet.
        ("pr_checks", "ALTER TABLE runs ADD COLUMN pr_checks TEXT"),
        ("pr_head_sha", "ALTER TABLE runs ADD COLUMN pr_head_sha TEXT"),
        ("ci_failed_sha", "ALTER TABLE runs ADD COLUMN ci_failed_sha TEXT"),
        ("ci_pending_since", "ALTER TABLE runs ADD COLUMN ci_pending_since TEXT"),
        # ⚖️ The judge (CMX-75). `judge_sha` is the head commit a judge was LAUNCHED on —
        # the same once-per-sha guard as `ci_failed_sha`, and for the same reason: without
        # it every tick would spawn another judge agent onto the same unchanged PR. A rework
        # pushes a NEW sha, which IS judged again — bounded by CHELA_MAX_REWORKS, because a
        # judge block spends a rework round like any other verdict. `judge_state` is one of
        # judge.J_* and `judge_detail` says why (it is the CANNOT VERIFY reason, and an
        # unknown must never be silent). ⚖️ CMX-81 loosened "once per sha" for exactly one
        # state: a `cannot_verify` on an unchanged sha is RE-tried (bounded — see
        # `judge_cannot_verify_tries` below), because an unknown is not a judgement.
        ("judge_sha", "ALTER TABLE runs ADD COLUMN judge_sha TEXT"),
        ("judge_state", "ALTER TABLE runs ADD COLUMN judge_state TEXT"),
        ("judge_started_at", "ALTER TABLE runs ADD COLUMN judge_started_at TEXT"),
        ("judge_detail", "ALTER TABLE runs ADD COLUMN judge_detail TEXT"),
        # ⚖️ CMX-81. How many times the judge has been RE-RUN on the CURRENT `judge_sha` after
        # coming back CANNOT VERIFY. `cannot_verify` is an UNKNOWN (a flake, a gh timeout, a
        # worktree that would not check out, a dead window), not a verdict — so it must cost a
        # BOUNDED retry, never permanently retire the commit from judgment the way a bare
        # `judge_sha == pr_head_sha` guard did (it let a green PR merge UNJUDGED on any flake).
        # `_spawn_judge` owns this count: it zeroes on a new head sha (a fresh judgement) and
        # bumps it each time it re-launches on the same unknown one, up to
        # `judge_max_unknown_retries`.
        ("judge_cannot_verify_tries",
         "ALTER TABLE runs ADD COLUMN judge_cannot_verify_tries INTEGER"),
        # 🤫 CMX-97. The judge's OWN tmux window — `_spawn_judge` calls `_launch_agent` with
        # `judge_window=True` (the run's `window_id` must stay the RUN's window, not a
        # judge that will be gone in twenty minutes; see `_launch_agent`'s docstring), which
        # means it is otherwise invisible to `dispatched_window_ids` — the SAME "is this a
        # dispatched worker?" read the forum-topic bridge (CMX-73) and the Wall tile (CMX-76)
        # both use. Invisible there, the judge looked like a HUMAN window: a Telegram topic
        # for a headless agent nobody is meant to talk to, and a full-size pop on the Wall
        # instead of docking minimized like every other worker. These two columns are the
        # judge's OWN `window_id`/`window_epoch` pair — same epoch-safety shape as the run's,
        # recorded separately so the two identities can never collide.
        ("judge_window_id", "ALTER TABLE runs ADD COLUMN judge_window_id TEXT"),
        ("judge_window_epoch", "ALTER TABLE runs ADD COLUMN judge_window_epoch TEXT"),
        # 🧑‍⚖️ The critic (CMX-88) — the persona pattern's ADVISORY brief-review, run once at
        # dispatch. `critic_notes` is the advisory it produced ("" ⇒ it ran and had nothing to
        # add; NULL ⇒ it never ran — a different fact, and not to be shown as either an
        # approval or a complaint); `critic_reviewed_at` is when. ⛔ Advisory-only by design:
        # nothing in the dispatch path ever READS these back, so a wrong note — or a crashed
        # critic — can never block, delay, or change a dispatch. A pre-migration row reads NULL
        # in both, which is exactly "the critic never ran".
        ("critic_notes", "ALTER TABLE runs ADD COLUMN critic_notes TEXT"),
        ("critic_reviewed_at", "ALTER TABLE runs ADD COLUMN critic_reviewed_at TEXT"),
        # 📋 Task-detail modal (Work-view redesign). `brief` is the task's full
        # multi-line brief (`Task.body` — the markdown source's title + its dedented
        # OBJECTIVE/BOUNDARIES/GUARDS/VERIFY continuation, when it captured one;
        # else `Task.raw`/title — see `_task_brief`, below) copied onto the run row
        # at CLAIM time (`_spawn`) so the modal still has the brief to show once the
        # task has left `open_tasks` (which only lists UNCLAIMED tasks). Additive and
        # read-only downstream: nothing in the dispatch/rework/judge path reads this
        # column back. A pre-migration row simply reads NULL — the modal degrades to
        # "no brief recorded", never a crash.
        ("brief", "ALTER TABLE runs ADD COLUMN brief TEXT"),
        # 🔁🛑 CMX-198. `CHELA_MAX_REWORKS` bounds the dispatcher's AUTOMATIC rework loop —
        # it does NOT bound `reopen` (deliberately: the human-takeover path must never
        # refuse). Measured 2026-07-31: cmx-197 was reopened 14 times, `rework_count`
        # printing `1/2` on every single one, because nothing counted the human's OWN
        # loop. `reopen_count` is that counter — incremented once per successful
        # `reopen()`, never touched anywhere else (mirrors `rework_count`'s "spent budget
        # stays spent" discipline, just for the human loop instead of the agent's).
        # `first_reopen_head_sha` is the PR's head commit at the FIRST reopen — the fixed
        # baseline the no-production-change nudge diffs every later reopen's head
        # against. It is set once (on `reopen_count` 0→1) and never overwritten, so the
        # comparison always spans the FULL reopen history, not just the latest round (a
        # per-round diff is trivially non-empty and would never fire).
        ("reopen_count", "ALTER TABLE runs ADD COLUMN reopen_count INTEGER DEFAULT 0"),
        ("first_reopen_head_sha", "ALTER TABLE runs ADD COLUMN first_reopen_head_sha TEXT"),
        # 🔁🚪 CMX-237. `reopen` covers ONE human intent — "I fixed it myself, re-verify the
        # new head" — and refuses everything else via its new-commit gate. It has no answer
        # for the other intent a `needs_human` verdict provokes just as often: "don't merge
        # it, don't fix it for it, just let the loop have another automatic swing at the SAME
        # head" (hit live on CMX-231, twice, with no in-contract way to say it). `retry_count`
        # is that second counter — how many extra automatic rounds a human has explicitly
        # granted via `chela retry`. It is added to `CHELA_MAX_REWORKS` at the escalation cap
        # check (see the `changes_requested` scan below) rather than folded into
        # `rework_count` itself, for the same reason `reopen_count` stays separate from it:
        # `rework_count` is the AUTOMATIC loop's own spent-budget ledger, and conflating a
        # human's grant with the agent's spend would make either counter lie about what it
        # measures.
        ("retry_count", "ALTER TABLE runs ADD COLUMN retry_count INTEGER DEFAULT 0"),
    ):
        try:
            conn.execute(ddl)
        except sqlite3.OperationalError:
            pass  # column already exists
    conn.commit()
    return conn


@contextmanager
def _db():
    """Open a WAL-mode connection, ensure schema, and ALWAYS close it.

    Used as ``with _db() as conn:`` — commits on clean exit, closes in every
    case. The previous version returned a bare Connection; a plain
    ``with conn:`` only manages the transaction (commit/rollback) and never
    closes it, so every call (``list_runs`` runs on each SSE poll) leaked a
    file descriptor and left short-lived writers on scheduler.db.
    """
    CHELA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    ensure_schema(conn)
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_ts(ts: str | None) -> datetime | None:
    if not ts:
        return None
    try:
        dt = datetime.fromisoformat(ts)
    except (ValueError, TypeError):
        return None
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt


def _capture_pane(window_name: str) -> str:
    """Return the visible text of a tmux window's pane (empty string on error)."""
    out = subprocess.run(
        ["tmux", "capture-pane", "-p", "-t", f"{TMUX_SESSION}:{window_name}"],
        capture_output=True, text=True,
    )
    return out.stdout if out.returncode == 0 else ""


def _pane_ready(pane: str) -> bool:
    """True once the Claude Code TUI is up enough to accept a prompt.

    The bypass-permissions footer appears only after the startup splash; the
    prompt glyph marks the input box. Either is sufficient evidence of ready.
    """
    return _READY_FOOTER in pane or _PROMPT_CHAR in pane


def _pane_idle_empty_prompt(pane: str) -> bool:
    """True when the pane shows a ready prompt whose input line is empty.

    This is the signature of the startup-race strand: the prompt was dropped
    during the splash, so the agent sits at a bare `❯` and never does any work.
    An agent that actually received its prompt is either mid-response (no bare
    empty prompt visible) or shows the queued text on the input line, so both
    fail this check.

    Gates on the prompt glyph rather than the bypass-permissions footer so this
    works under any agent.cmd — the default `--permission-mode auto` does not
    render that footer. Safe because the watchdog only runs WATCHDOG_IDLE_MINUTES
    after launch, well past the splash, so the glyph is a reliable ready signal.
    """
    if _PROMPT_CHAR not in pane:
        return False
    for line in pane.splitlines():
        if _PROMPT_CHAR in line:
            after = line.split(_PROMPT_CHAR, 1)[1].strip().strip("│").strip()
            return after == ""
    return False


def _pane_shows_activity(pane: str) -> bool:
    """True when the pane shows Claude Code actively generating/running a tool.

    The TUI paints its working spinner + a live "(<elapsed> · ↓ <n> tokens)" status
    ABOVE the input box while the box below stays an empty `❯` — so a working agent
    trips :func:`_pane_idle_empty_prompt`. This is the veto that keeps the watchdog
    from nudging or failing an agent that is plainly still working: a spinner-star
    line OR a token/elapsed counter is positive evidence of work. Erring toward "is
    working" is deliberate — a false veto merely declines to fail a run (safe), while
    a false idle-verdict kills a working agent's PR (the bug this fixes).
    """
    for line in pane.splitlines():
        s = line.strip()
        if s and s[0] in _SPINNER_GLYPHS:
            return True
        if _WORK_LINE_RE.search(s):
            return True
    return False


def _dismiss_input_block(window_id: str) -> None:
    """Send Escape to a window whose agent is blocked on an input dialog.

    A dispatched agent has no human to answer an ``AskUserQuestion`` (or a gated
    permission prompt); left alone it hangs until MAX_ATTEMPTS. Escape cancels the
    dialog so the model falls back to its own default and keeps working. Best-effort
    and never raises — a failed send is caught by the next watchdog tick.
    """
    try:
        subprocess.run(
            ["tmux", "send-keys", "-t", f"{TMUX_SESSION}:{window_id}", "Escape"],
            capture_output=True, text=True, timeout=5,
        )
    except (OSError, subprocess.SubprocessError) as e:
        log.warning("Task dialog-dismiss on %s failed: %s", window_id, e)


def _agent_status(window_id: str) -> str | None:
    """Native session status of the agent in a window ("busy"/"idle"), or None.

    Reads the same `claude agents --json` authority the dashboard uses, keyed by
    the window's claude pid. None means "can't tell" (no claude child, session
    not listed, command unavailable) — callers must treat that as unverifiable
    rather than as evidence of idleness.
    """
    from chela import agent_manager

    pid = agent_manager.claude_pid(window_id)
    if pid is None:
        return None
    # force=True: the map is cached for a couple of seconds, and we are polling
    # for a transition that happens within that window.
    return agent_manager.session_status_map(force=True)["by_pid"].get(pid)


def _seed_landed(
    window_id: str,
    timeout: float = SEED_CONFIRM_TIMEOUT_SECONDS,
    poll: float = SEED_CONFIRM_POLL_INTERVAL,
) -> bool:
    """Did the seed prompt actually reach the agent?

    True once the agent flips to "busy" (it only does that with a prompt in hand);
    False if it never goes busy within `timeout` — the paste's Enter was swallowed
    (a splash redraw landing after the ready glyph does exactly that) and needs a
    resend. Transient unreadable reads (None from `_agent_status`, a window
    mid-redraw) are treated as "not yet" and polled through, NOT reported as a
    verdict — bailing on the first one burned the resend budget before the notice
    cleared (the CMX-133 hang).

    A freshly-booted agent reads "idle" until the seed makes it busy, so this
    watches for the → busy transition, not for "is it idle right now".
    """
    deadline = time.monotonic() + timeout
    while True:
        if _agent_status(window_id) == "busy":
            return True
        # Anything other than "busy" — "idle", "waiting", or None (status UNREADABLE,
        # exactly what a window mid-redraw returns while a startup notice eats
        # keystrokes) — means "not landed yet, keep waiting". Bailing on the first None
        # (the old behaviour) made the caller's resend loop burn its whole budget in ~2s,
        # before the notice cleared — the CMX-133 hang. Poll the full window for the
        # → busy transition instead; a genuinely unaddressable window is caught by the
        # send itself failing (send_tmux/resend_enter return False), not here.
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return False
        time.sleep(min(poll, remaining))


def _send_seed(window_id: str, prompt: str, task_id: str) -> bool:
    """Send the seed prompt and confirm it landed, re-sending if it didn't.

    Only the FIRST send is a full :func:`send_tmux` (paste + Enter). If the agent
    is still idle afterwards, the paste itself landed — what got swallowed is the
    separately-sent Enter, eaten by a late startup redraw (an MCP-auth notice,
    `gh auth login`, or any other splash that redraws after the paste). Retrying
    with another full send_tmux would type the prompt a SECOND time on top of the
    still-unsubmitted text, so every retry after the first re-sends Enter ONLY
    (:func:`chela.messenger.resend_enter`).

    Returns False only when a send itself fails. An unconfirmed seed after
    SEED_MAX_SENDS is logged and left to the reconcile watchdog rather than
    retried forever — a genuinely broken agent must fail cleanly.
    """
    for send in range(1, SEED_MAX_SENDS + 1):
        ok = send_tmux(window_id, prompt) if send == 1 else resend_enter(window_id)
        if not ok:
            return False
        landed = _seed_landed(window_id)
        # `landed is None` means the status was UNREADABLE — which is exactly what a
        # window mid-redraw returns, the very moment a startup notice (MCP auth, gh
        # auth, any splash) ate the Enter. The old code FAILED OPEN here ("assuming
        # the seed landed") and stranded the seed unsubmitted. Treat None like an idle
        # (False) result instead: fall through and re-send Enter. SEED_MAX_SENDS still
        # bounds it — a genuinely dead window drops to the reconcile watchdog below.
        if landed is None:
            log.debug(
                "Task %s: agent status unreadable on %s; treating as not-yet-landed, re-sending Enter",
                task_id, window_id,
            )
        if landed:
            if send > 1:
                log.info("Task %s: seed landed on %s after %d sends", task_id, window_id, send)
            return True
        if send < SEED_MAX_SENDS:
            log.warning(
                "Task %s: agent on %s still idle %.0fs after the seed (Enter dropped); "
                "re-sending Enter (%d/%d)",
                task_id, window_id, SEED_CONFIRM_TIMEOUT_SECONDS, send + 1, SEED_MAX_SENDS,
            )
            time.sleep(SEED_RESEND_SETTLE_SECONDS)
    log.warning(
        "Task %s: agent on %s never went busy after %d seed sends; leaving it to the watchdog",
        task_id, window_id, SEED_MAX_SENDS,
    )
    return True


def _wait_for_ready(
    window_name: str,
    min_wait: float,
    timeout: float,
    poll: float = READY_POLL_INTERVAL,
) -> bool:
    """Block until the agent TUI is ready to accept a prompt.

    Honors `min_wait` (agent.startup_delay_seconds) as a minimum initial sleep
    for backward compat, then polls capture-pane every `poll` seconds, up to a
    total of `timeout` seconds from launch, for the ready indicator. Returns
    True as soon as the pane looks ready; returns False if the cap is hit
    (caller sends the prompt anyway, degrading rather than hanging).
    """
    if min_wait > 0:
        time.sleep(min_wait)
    deadline = time.monotonic() + max(0.0, timeout - min_wait)
    while True:
        if _pane_ready(_capture_pane(window_name)):
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(poll)


def _prompt_vars(
    wf: WorkflowDef,
    task: Task,
    worktree_path: str,
    branch: str,
    base_branch: str,
    task_number: int,
) -> dict:
    """Build the render_prompt variable map shared by _spawn and the watchdog."""
    repo_path = wf.path.parent
    return {
        "task_id": task.id,
        "task_title": task.title,
        "task_file": task.file,
        "task_line_number": task.line_number,
        "workspace_path": str(worktree_path),
        "branch_name": branch,
        "base_branch": base_branch,
        "repo_path": str(repo_path),
        "project_key": wf.project_key,
        "task_number": task_number,
    }


def _read_pr_status(pr_url: str | None, repo_dir: str | None) -> tuple[str | None, str | None]:
    """Return (state, mergeable) from `gh pr view <n> --json state,mergeable`.

    `state` is lowercased to match `pr_state in ('open','merged','closed')`.
    `mergeable` is GitHub's MERGEABLE/CONFLICTING/UNKNOWN, kept uppercase. Both
    fields are advisory — the UI gates the Merge / Merge-all buttons on them —
    so each element is None on any failure, leaving the previous value in place
    rather than erasing it. The two come from one `gh` call to keep the
    phase-0 refresh cheap.
    """
    if not pr_url or not repo_dir:
        return None, None
    m = _PR_NUMBER_RE.search(str(pr_url))
    if not m:
        return None, None
    pr_number = m.group(1)
    try:
        out = subprocess.run(
            ["gh", "pr", "view", pr_number, "--json", "state,mergeable"],
            cwd=repo_dir, capture_output=True, text=True, timeout=15,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None, None
    if out.returncode != 0:
        return None, None
    try:
        data = json.loads(out.stdout)
    except (json.JSONDecodeError, ValueError):
        return None, None
    state = (data.get("state") or "").strip().lower() or None
    mergeable = (data.get("mergeable") or "").strip().upper() or None
    return state, mergeable


class CIStatus(NamedTuple):
    """What GitHub says about a PR's checks, right now.

    ``state`` is one of the ``CI_*`` constants and is the only thing anything gates on.
    ``head_sha`` is the commit the state belongs to (the dedupe key for the verdict);
    ``failing`` names the jobs that failed and ``run_ids`` the Actions runs they came from
    (the log is fetched from those, once, on the transition into red). ``detail`` says WHY
    when the state is ``unknown`` — an unreadable owner is reported, never swallowed.
    """
    state: str
    head_sha: str | None = None
    failing: tuple[str, ...] = ()
    run_ids: tuple[str, ...] = ()
    detail: str = ""


def _rollup_state(nodes: list) -> tuple[str, tuple[str, ...], tuple[str, ...]]:
    """Reduce GitHub's ``statusCheckRollup`` to (state, failing job names, Actions run ids).

    Two node shapes ride in that field and both must be handled: a **CheckRun** (Actions,
    with ``status`` + ``conclusion`` + ``name``) and a **StatusContext** (the legacy commit
    -status API, with a single ``state`` + ``context``).

    ⛔ A NODE WE CANNOT RECOGNISE IS UNSETTLED, NOT GREEN. The first cut of this said in its
    docstring that an unrecognised rollup "is not a pass" and then returned exactly that: a
    node with none of status/conclusion/state fell through every branch, left `failing`
    empty, and came out ``passing``. A shape GitHub adds tomorrow would have read as green
    and merged. Nothing reaches ``passing`` here that was not SEEN to succeed.

    ⛔ AND A CHECK THAT DID NOT RUN IS NOT A CHECK THAT PASSED. A rollup of nothing but
    SKIPPED/NEUTRAL nodes (a `paths-ignore` filter, a skipped required job) is ``none`` —
    the same "nothing ran" as an empty rollup, and said just as loudly. It is only a pass
    when at least one check actually ran and succeeded.

    ⛔ UNSETTLED WINS OVER FAILING, deliberately. A rollup with one red job and one still
    running is ``pending``, not ``failing``: acting on it would send the agent back into a
    branch whose CI is still writing its own verdict, and the second half of that run could
    just as well fail too — a second, different red on the SAME sha, which the once-per-sha
    guard would then swallow. The checks settle in a minute; the loop can wait a tick.
    """
    if not nodes:
        return CI_NONE, (), ()
    unsettled = False
    passed = 0
    failing: list[str] = []
    run_ids: list[str] = []
    for node in nodes:
        if not isinstance(node, dict):
            unsettled = True       # a node we cannot even read is not a node that passed
            continue
        status = str(node.get("status") or "").upper()
        conclusion = str(node.get("conclusion") or "").upper()
        # StatusContext carries neither: its `state` is both at once.
        context_state = str(node.get("state") or "").upper()
        name = str(
            node.get("name") or node.get("context") or node.get("workflowName") or "check"
        )
        if context_state and not status and not conclusion:
            if context_state in CI_UNSETTLED_STATUSES:
                unsettled = True
            elif context_state in CI_FAILED_CONCLUSIONS:
                failing.append(name)
            elif context_state in CI_PASSED_CONCLUSIONS:
                passed += 1
            elif context_state not in CI_DID_NOT_RUN_CONCLUSIONS:
                unsettled = True   # a state we do not know is not one we may call green
            continue
        if status and status != "COMPLETED":
            unsettled = True
            continue
        if conclusion in CI_FAILED_CONCLUSIONS:
            workflow = str(node.get("workflowName") or "")
            failing.append(f"{workflow} / {name}" if workflow and workflow != name else name)
            m = _CI_RUN_ID_RE.search(str(node.get("detailsUrl") or ""))
            if m:
                run_ids.append(m.group(1))
        elif conclusion in CI_PASSED_CONCLUSIONS:
            passed += 1
        elif conclusion not in CI_DID_NOT_RUN_CONCLUSIONS:
            # No conclusion at all (a node carrying none of the three fields lands here), or
            # one GitHub has not taught us yet. Either way: not evaluated ⇒ not a pass.
            unsettled = True
    if unsettled:
        return CI_PENDING, (), ()
    if failing:
        # dict.fromkeys: dedupe, keep order — a matrix job can fail in several shards.
        return CI_FAILING, tuple(dict.fromkeys(failing)), tuple(dict.fromkeys(run_ids))
    if passed:
        return CI_PASSING, (), ()
    return CI_NONE, (), ()   # every node skipped: nothing ran, and nothing passed


def _read_pr_checks(pr_url: str | None, repo_dir: str | None) -> CIStatus:
    """Ask GITHUB whether this PR's checks passed. Never infer, never assume.

    ⛔ Every failure path here returns ``CI_UNKNOWN`` — gh missing, gh unauthenticated, a
    timeout, a rate limit, a PR url we cannot parse. An unknown is NOT a pass: it blocks the
    merge gate and it says why. That is the whole point of the fact — the alternative
    (treating "I could not look" as green) is the exact hole that let PR #80 merge red.
    """
    number = _pr_number(pr_url)
    if not number or not repo_dir:
        return CIStatus(CI_UNKNOWN, detail="the run row carries no PR number to ask about")
    try:
        out = subprocess.run(
            ["gh", "pr", "view", number, "--json", "statusCheckRollup,headRefOid"],
            cwd=repo_dir, capture_output=True, text=True, errors="replace", timeout=20,
        )
    # OSError, not FileNotFoundError: a gh that is present but not executable raises
    # PermissionError, and THIS function's whole contract is that it never raises — a tick
    # that dies here stops the loop, when the honest answer was CANNOT VERIFY.
    except OSError as e:
        return CIStatus(CI_UNKNOWN, detail=f"gh could not be run ({e}) — nothing read the checks")
    except subprocess.TimeoutExpired:
        return CIStatus(CI_UNKNOWN, detail="gh timed out reading the checks")
    if out.returncode != 0:
        return CIStatus(CI_UNKNOWN, detail=(out.stderr or out.stdout or "gh failed").strip()[:200])
    try:
        data = json.loads(out.stdout)
    except (json.JSONDecodeError, ValueError):
        return CIStatus(CI_UNKNOWN, detail="gh returned something that is not JSON")
    if not isinstance(data, dict):
        # Valid JSON, wrong shape (`null`, a list, a bare string). `.get` on it would be an
        # AttributeError out of a function that must only ever return.
        return CIStatus(CI_UNKNOWN, detail="gh returned JSON that is not an object")
    sha = (data.get("headRefOid") or "").strip() or None
    rollup = data.get("statusCheckRollup")
    state, failing, run_ids = _rollup_state(rollup if isinstance(rollup, list) else [])
    return CIStatus(state, sha, failing, run_ids)


def _failing_log_tail(repo_dir: str | None, run_ids: tuple[str, ...]) -> str:
    """The tail of the failing CI log — fetched ONCE, on the transition into red.

    ⛔ Not per tick: `gh run view --log-failed` downloads the whole log archive, and a poll
    that does that every 60s for every open PR is a bad neighbour. The once-per-sha guard in
    :func:`tick` is what makes this affordable, and it is the same guard that makes the
    verdict fire once.

    Best-effort: a log we cannot fetch costs the agent the tail, not the verdict — the
    failing job NAMES came from the rollup and are already in hand.

    ⛔ THE LOG IS NOT TEXT UNTIL WE MAKE IT TEXT. Two things about it are hostile:

    * It is RAW. `--log-failed` keeps whatever the job printed, ANSI and all (`##[group]`
      markers, the `\\x1b[36;1m` of the setup actions, anything running under FORCE_COLOR or
      `pytest --color=yes`). This string ends up inside the rework prompt, which chela PASTES
      into the agent's tmux pane — where an escape is a keypress, not a character, and a
      `\\x03` is a Ctrl-C. So it goes through the same sanitizer a room body does.
    * It is BYTES. A test that prints an invalid UTF-8 byte would make `text=True` raise
      ``UnicodeDecodeError`` — an exception this function never promised and `tick` does not
      catch, killing the whole pass. ``errors="replace"`` makes a mangled byte cost a
      character, not the loop.
    """
    if not repo_dir or not run_ids:
        return ""
    try:
        out = subprocess.run(
            ["gh", "run", "view", run_ids[0], "--log-failed"],
            cwd=repo_dir, capture_output=True, text=True, errors="replace", timeout=60,
        )
    except (OSError, subprocess.TimeoutExpired) as e:
        return f"(could not fetch the CI log: {e})"
    if out.returncode != 0:
        return f"(could not fetch the CI log: {(out.stderr or out.stdout or '').strip()[:200]})"
    text = tui_sanitize(out.stdout or "")
    if len(text) <= CI_LOG_TAIL_CHARS:
        return text
    return "… (log truncated — this is the tail)\n" + text[-CI_LOG_TAIL_CHARS:]


def _ci_verdict_body(ci: CIStatus, log_tail: str, pr_url: str | None) -> str:
    """The verdict a red CI writes — a FACT, stated as one.

    It is deliberately not a review: it makes no judgment about the code, it reports what
    GitHub said. That is why this loop needs no reviewer and no LLM, and why a wrong verdict
    is not a risk here the way it would be for a judge.

    The body is BOTH a PR comment and (through the rework prompt) something pasted into an
    agent's terminal. `_failing_log_tail` already stripped the escapes for the terminal; the
    fence below is widened past the longest backtick run in the log for the markdown — a CI
    log that prints ``` would otherwise close the block early and spill the rest of itself
    into the comment as prose.
    """
    jobs = "\n".join(f"- `{name}`" for name in ci.failing) or "- (the rollup named no job)"
    longest_run = max((len(m) for m in re.findall(r"`+", log_tail)), default=0)
    fence = "`" * max(3, longest_run + 1)
    log_block = (
        f"\n{fence}\n{log_tail}\n{fence}\n" if log_tail else "\n_(no log tail available)_\n"
    )
    return f"""## 🚦 CI is RED on this PR — sent back automatically

GitHub's checks are the authority on whether this can ship, and they are **failing** on
`{(ci.head_sha or "?")[:12]}` — the commit currently at the head of this branch. No human
reviewed this; a failing check is a fact, not a judgment.

**Failing check(s):**
{jobs}

**Tail of the failing job's log:**
{log_block}
### What to do

1. Reproduce it **as CI runs it** — read `.github/workflows/*` and run the same commands
   (a green local suite is exactly what was reported last time this happened, and the PR
   was still red).
2. Fix the failure, commit, and push to this same branch — the PR updates itself.
3. Confirm with GitHub, not with your own test run: `gh pr checks` on this PR must be
   green before you finish. ⛔ **A check you did not read back from GitHub is not a pass.**
4. Then run `chela task-finished <task-id>` as usual.

_PR: {pr_url or "(no url on the run row)"} — posted by the dispatcher's CI gate._
"""


def _read_pr_url(window_name: str | None) -> str | None:
    """Best-effort read of the latest pr-link URL from the agent's transcript.

    Returns None on any failure — pr_url is optional; a missing URL leaves the
    Done card unlinked rather than blocking the run from being marked done.
    """
    if not window_name:
        return None
    try:
        summary = agent_transcript_summary(window_name)
    except Exception:
        log.exception("Failed to resolve PR URL for window %s", window_name)
        return None
    pr = summary.get("pr") if isinstance(summary, dict) else None
    if isinstance(pr, dict):
        url = pr.get("url")
        if isinstance(url, str) and url:
            return url
    return None


def _kill_window(window_name: str) -> None:
    subprocess.run(
        ["tmux", "kill-window", "-t", f"{TMUX_SESSION}:{window_name}"],
        capture_output=True,
    )


def _kill_windows_named(window_name: str) -> None:
    """Kill every existing tmux window in TMUX_SESSION whose name == window_name.

    A retry (failed → re-dispatch) re-enters _spawn with the same window_name;
    tmux happily creates a *second* window of that name, after which the by-name
    target `<session>:<name>` is ambiguous and send-keys can exit non-zero —
    stacking duplicate windows that flap a run between `failed` states.
    Enumerating by id and killing each match before the new-window guarantees a
    retry starts from a clean slate. Best-effort: a missing session or "no such
    window" is ignored (nothing to clean up).
    """
    out = subprocess.run(
        ["tmux", "list-windows", "-t", TMUX_SESSION, "-F", "#{window_id} #{window_name}"],
        capture_output=True, text=True,
    )
    if out.returncode != 0 or not isinstance(out.stdout, str):
        return
    for line in out.stdout.splitlines():
        wid, _, name = line.partition(" ")
        if name == window_name and wid:
            subprocess.run(
                ["tmux", "kill-window", "-t", f"{TMUX_SESSION}:{wid}"],
                capture_output=True,
            )


def _new_window(window_name: str, cwd: str) -> str:
    """Create a fresh tmux window and return its @id (e.g. "@7").

    Uses `-P -F '#{window_id}'` to capture the new window's id so the caller can
    target THIS spawn by id rather than by name. By-id targeting is immune to
    duplicate-name ambiguity, so a residual same-name window can never make the
    agent-cmd send-keys / readiness poll / prompt send-keys land on the wrong
    pane. Falls back to the bare window_name if the id can't be parsed (e.g.
    under a subprocess mock that returns no stdout).
    """
    # Trailing ':' forces session resolution; a bare session name is ambiguous
    # to tmux when a window shares that name, making it target that window's
    # index and fail with "index N in use".
    out = subprocess.run(
        ["tmux", "new-window", "-t", f"{TMUX_SESSION}:", "-n", window_name,
         "-c", cwd, "-P", "-F", "#{window_id}"],
        check=True, capture_output=True, text=True,
    )
    wid = out.stdout.strip() if isinstance(out.stdout, str) else ""
    return wid if re.fullmatch(r"@\d+", wid) else window_name


def _fire_after_done(wf: WorkflowDef) -> None:
    """Best-effort: fire hooks.after_done (detached) when a merge completes.

    Runs from the workflow's repo dir (`wf.path.parent`) with shell=True so
    workflow authors can write `git pull && pm2 restart …`. start_new_session
    detaches from the daemon's process group — required because the hook may
    legitimately `pm2 restart` the daemon itself, and we don't want SIGHUP
    propagation to kill the current reconcile mid-execution. We don't wait on
    the exit code (the daemon may not be around to read it); only Popen-start
    failures are logged.
    """
    cmd = wf.get("hooks", "after_done")
    if not cmd:
        return
    log.info("Firing after_done hook in %s", wf.path.parent)
    try:
        subprocess.Popen(
            cmd,
            shell=True,
            cwd=str(wf.path.parent),
            start_new_session=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except Exception:
        log.exception("after_done hook failed to start")


def mark_awaiting_review(task_id: str) -> dict:
    """Transition a run from running → awaiting_review and kill its tmux window.

    Called by the agent as its final step (via `chela task-finished <id>`)
    once the PR is open and the in-branch strike is committed. Reads pr_url
    from the agent's transcript *before* killing the window, since the
    cwd/session-id mapping disappears with the window.

    Returns a dict summary of what changed (used by the CLI for stdout).
    """
    with _db() as conn:
        row = conn.execute(
            "SELECT * FROM runs WHERE task_id=?", (task_id,)
        ).fetchone()
        if row is None:
            return {"ok": False, "error": f"no run found for task_id {task_id}"}
        if row["status"] not in ("claimed", "running"):
            return {
                "ok": False,
                "error": f"run is in status {row['status']!r}, refusing to transition",
                "task_id": task_id,
            }
        pr_url = _read_pr_url(row["window_name"])
        window_name = row["window_name"]
        effective_pr_url = pr_url or row["pr_url"]
        wf_path = row["workflow_path"]
        repo_dir = str(Path(wf_path).parent) if wf_path else None
        pr_state, pr_mergeable = _read_pr_status(effective_pr_url, repo_dir)
        conn.execute(
            "UPDATE runs SET status='awaiting_review', ended_at=?, "
            "pr_url=COALESCE(?, pr_url), pr_state=COALESCE(?, pr_state), "
            "pr_mergeable=COALESCE(?, pr_mergeable) WHERE task_id=?",
            (_now(), pr_url, pr_state, pr_mergeable, task_id),
        )
        conn.commit()
        if window_name:
            _kill_window(window_name)
        return {
            "ok": True,
            "task_id": task_id,
            "window_name": window_name,
            "pr_url": pr_url,
            "pr_state": pr_state,
            "pr_mergeable": pr_mergeable,
        }


# --- the review verdict: the carrier of the rework loop ----------------------
#
# A PR that FAILED review used to have nowhere to go. The dispatcher could claim a task
# and reconcile a merged PR, but `awaiting_review` was terminal unless a human climbed
# into the worktree and hand-spawned a fix agent (which is literally what happened on
# 2026-07-14, reviewing PR #80). These two functions are the carrier — NOT the judge:
# the reviewer is still whoever calls them.


def reviews_of(run: dict) -> list[dict]:
    """Every verdict this run has received, oldest first. Never raises.

    The column is JSON written by :func:`request_changes`; a legacy row (or a hand-edited
    one) reads as an empty list rather than taking down the daemon that reads it.
    """
    raw = run.get("review_history")
    if not raw:
        return []
    try:
        parsed = json.loads(raw)
    except (json.JSONDecodeError, TypeError, ValueError):
        return []
    return [r for r in parsed if isinstance(r, dict)] if isinstance(parsed, list) else []


def latest_verdict(run: dict) -> str:
    """The most recent SUBSTANTIVE verdict body — what a rework prompt tells the agent to fix.

    🔁🚪 CMX-237. ``retry()`` appends a meta entry ("keep going") to ``review_history``
    without anyone re-reviewing the code — it exists to grant the automatic loop another
    round, not to replace what that round should fix. Were it not skipped here, the very
    next rework prompt would render the human's "keep going" note as THE verdict, in place
    of the real defect description the previous round actually failed on — the agent would
    be told to fix nothing in particular. Skipped, not deleted: the entry stays in the
    history for the audit trail, it is just never mistaken for a review.
    """
    reviews = reviews_of(run)
    for r in reversed(reviews):
        if r.get("verdict") == "retry":
            continue
        return str(r.get("body") or "")
    return ""


def _pr_number(pr_url: str | None) -> str | None:
    m = _PR_NUMBER_RE.search(str(pr_url or ""))
    return m.group(1) if m else None


def _pr_owner_repo(pr_url: str | None) -> tuple[str, str] | None:
    m = _PR_REPO_RE.search(str(pr_url or ""))
    return (m.group(1), m.group(2)) if m else None


def _production_files_changed(
    pr_url: str | None, repo_dir: str | None, base_sha: str, head_sha: str,
) -> tuple[bool | None, str]:
    """Did anything under ``chela/**`` (excluding ``tests/**``) change between two commits?

    Reads GitHub's own compare, live — the same "never trust a local checkout" discipline
    as :func:`_read_pr_checks`: ``repo_dir`` is wherever the dispatcher happens to be
    running from, not guaranteed to have fetched these exact objects, so this asks GitHub
    instead of running ``git diff`` on disk.

    Returns ``(None, detail)`` on anything unreadable — no ``gh``, no network, an
    unparsable PR url. This backs a NUDGE, not a gate: an unknown here must say nothing,
    never guess "no change" (which would fire the nudge on missing data) or "changed"
    (which would silently suppress a nudge that should have fired).
    """
    if base_sha == head_sha:
        return False, "the head has not moved since the first reopen"
    owner_repo = _pr_owner_repo(pr_url)
    if not owner_repo:
        return None, "could not parse an owner/repo out of the PR url"
    owner, repo = owner_repo
    try:
        out = subprocess.run(
            ["gh", "api", f"repos/{owner}/{repo}/compare/{base_sha}...{head_sha}",
             "--jq", ".files[].filename"],
            cwd=repo_dir, capture_output=True, text=True, errors="replace", timeout=20,
        )
    except subprocess.TimeoutExpired:
        return None, "gh timed out reading the compare"
    except Exception as e:  # noqa: BLE001 — see below; the contract is DEGRADE, never raise
        # ⛔ Deliberately broad. This function's contract is "(None, detail) on anything
        # unreadable", and it backs a NUDGE, not a gate: the worst case of swallowing is a
        # nudge that stays silent, while the worst case of RAISING is an unhandled
        # exception in `reopen()` — the human-takeover path, which a stuck run depends on.
        #
        # Naming individual exception types is what failed here: `except (OSError,
        # TimeoutExpired)` looked exhaustive and was not. `check=True` (CalledProcessError)
        # and a malformed argument (ValueError) both escaped it, and neither is reachable
        # by reading the call — they arrive through a KEYWORD, so the next one added is
        # invisible again. Catching the behaviour covers every keyword at once.
        return None, f"gh could not be run ({e})"
    if out.returncode != 0:
        return None, (out.stderr or out.stdout or "gh api compare failed").strip()[:200]
    files = [f for f in (out.stdout or "").splitlines() if f.strip()]
    production = [f for f in files if f.startswith("chela/") and not f.startswith("tests/")]
    return (
        len(production) > 0,
        f"{len(files)} file(s) changed since the first reopen, {len(production)} under chela/",
    )


def _post_pr_comment(pr_url: str | None, repo_dir: str | None, body: str) -> tuple[bool, str]:
    """Post the verdict on the PR — with ``gh pr comment``, NEVER ``gh pr review``.

    Measured on 2026-07-14: ``gh pr review --request-changes`` REFUSES a PR authored by
    the calling account ("Can not request changes on your own pull request"), and every
    agent in the fleet pushes as the same account. So a review is impossible to record on
    GitHub, ``reviewDecision`` stays null forever, and anything built on it is built on
    sand. A comment always works — and it is the durable record the reworking agent reads
    back with ``gh pr view <n> --comments``.

    The comment is a PROJECTION. The run row is the authority, and it is written first,
    so a `gh` that is missing, unauthenticated or offline degrades to "the loop still
    runs, the human-readable copy is missing" — reported, never fatal.
    """
    number = _pr_number(pr_url)
    if not number or not repo_dir:
        return False, "no PR number on the run row"
    try:
        out = subprocess.run(
            ["gh", "pr", "comment", number, "--body-file", "-"],
            cwd=repo_dir, input=body, capture_output=True, text=True, timeout=30,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as e:
        return False, f"gh pr comment failed: {e}"
    if out.returncode != 0:
        return False, (out.stderr or out.stdout or "gh pr comment failed").strip()
    return True, (out.stdout or "").strip()


def resolve_run(ident: str) -> dict | None:
    """Find a run by task id, branch name, or window name. Never guesses.

    The orchestrator reviews a PR titled ``CMX-68`` on branch ``cmx-68`` — it does not
    have the task id in hand, and making it look one up is how a verdict ends up on the
    wrong run. An ambiguous identifier resolves to None (CMX-48: a wrong id is worse than
    no id); the caller says so.
    """
    ident = (ident or "").strip()
    if not ident:
        return None
    runs = list_runs()
    exact = [r for r in runs if r.get("task_id") == ident]
    if exact:
        return exact[0]
    low = ident.lower()
    named = [
        r for r in runs
        if (r.get("branch_name") or "").lower() == low
        or (r.get("window_name") or "").lower() == low
    ]
    return named[0] if len(named) == 1 else None


def request_changes(ident: str, body: str, *, post_comment: bool = True) -> dict:
    """FAIL a PR under review: the run goes to ``changes_requested`` and the loop turns.

    (a) writes the verdict + the status on the run row — THE authority — and (b), when
    ``post_comment`` is true (the default), posts the body as a PR comment, the
    human-readable projection and the record the reworking agent reads back. In that
    order: if the comment fails, the loop still runs.

    ``post_comment=False`` is for a caller that already posted this exact ``body`` to the
    PR itself, unconditionally, before calling here — ⚖️🕳️ CMX-228, the judge. Two early
    exits below (an already-moved ``status``, and the compare-and-swap) return before this
    function would ever reach the comment, so a caller that relied on THIS function to
    publish would lose the comment on exactly the runs a human or CI reached first — the
    finding that most needed to reach the PR was the one most likely not to. Set this false
    only when the comment has independently already been posted; every other caller keeps
    the default and this behaves exactly as before.

    It increments nothing. ``rework_count`` is spent when the dispatcher actually spawns
    the rework (:func:`_respawn_rework`) — a verdict that never gets a concurrency slot
    must not burn a round of the cap.
    """
    body = (body or "").strip()
    if not body:
        return {"ok": False, "error": "a verdict with no body is not a verdict"}
    run = resolve_run(ident)
    if run is None:
        return {"ok": False, "error": f"no run matches {ident!r} (task id, branch, or window name)"}
    task_id = run["task_id"]
    if run["status"] != "awaiting_review":
        return {
            "ok": False, "task_id": task_id,
            "error": f"run is in status {run['status']!r}, not 'awaiting_review' — "
                     "only a run that is actually under review can fail review",
        }

    reviews = reviews_of(run)
    reviews.append({"round": len(reviews) + 1, "at": _now(), "body": body,
                    "verdict": "changes_requested"})
    with _db() as conn:
        # COMPARE-AND-SWAP on the status we READ. ⛔ Not a formality: `resolve_run` above is
        # a separate connection and a separate moment, and a dispatcher tick runs every 60s
        # in another process. If it reconciles this row to `done` (the human merged the PR
        # while the reviewer was typing) in between, an unconditional UPDATE would RESURRECT
        # a merged run — and the next tick would dutifully re-spawn an agent onto a branch
        # whose PR is closed. The row moves only if it is still the row we judged.
        cur = conn.execute(
            "UPDATE runs SET status='changes_requested', review_history=? "
            "WHERE task_id=? AND status='awaiting_review'",
            (json.dumps(reviews), task_id),
        )
        conn.commit()
        if cur.rowcount == 0:
            now = conn.execute(
                "SELECT status FROM runs WHERE task_id=?", (task_id,)
            ).fetchone()
            current = now["status"] if now else "gone"
            log.warning("review: %s moved to %r under the verdict — nothing written",
                        task_id, current)
            return {
                "ok": False, "task_id": task_id,
                "error": f"run moved to {current!r} while the verdict was being written "
                         "(a tick reconciled it, or another reviewer got there first) — "
                         "nothing was changed. Re-read it and decide again.",
            }

    wf_path = run.get("workflow_path")
    repo_dir = str(Path(wf_path).parent) if wf_path else None
    if post_comment:
        posted, detail = _post_pr_comment(run.get("pr_url"), repo_dir, body)
        if not posted:
            log.warning(
                "review: %s is changes_requested, but the PR comment did not post (%s). The "
                "run row is the authority, so the rework still spawns — with the verdict in "
                "its prompt but nothing on the PR to read back.", task_id, detail,
            )
    else:
        posted, detail = None, "posted by the caller before this call"
    log.info("review: %s → changes_requested (round %d)", task_id, len(reviews))
    return {
        "ok": True, "task_id": task_id, "status": "changes_requested",
        "branch_name": run.get("branch_name"), "pr_url": run.get("pr_url"),
        "round": len(reviews), "rework_count": run.get("rework_count") or 0,
        "max_reworks": max_reworks(), "comment_posted": posted, "comment_detail": detail,
        # The CLI checks that SOMETHING will actually come and pick this run up before it
        # tells the reviewer so (main._rework_prospects). It needs the workflow to check.
        "workflow_path": wf_path,
    }


def approve(ident: str, body: str = "", force: bool = False) -> dict:
    """PASS a PR under review — unless GITHUB says its checks failed.

    The run STAYS in ``awaiting_review`` and the merge stays a human's call: this is the
    carrier, not the judge, and nothing here merges anything. An approval with a body
    posts it as a PR comment; without one it is a no-op that just confirms the state.

    ⛔ **A red PR is REFUSED**, and so is one whose checks could not be read. On 2026-07-14
    the orchestrator approved and merged PR #80 while its CI was red — it read the code and
    never looked at the artifact that governs whether the thing can ship — and `dev` broke.
    So the gate is not advisory and it is not a warning: an approval that would have been
    wrong now costs an error message. The checks are read back LIVE from GitHub here, not
    from the run row: the row is a cache refreshed on a 60s tick, and an approval is exactly
    the moment to ask the owner rather than trust a copy.

    ``force=True`` overrides it (a human may know the failure is unrelated) and the result
    says so, loudly, so the override is visible in the PR comment and the CLI output.
    """
    run = resolve_run(ident)
    if run is None:
        return {"ok": False, "error": f"no run matches {ident!r} (task id, branch, or window name)"}
    if run["status"] != "awaiting_review":
        return {
            "ok": False, "task_id": run["task_id"],
            "error": f"run is in status {run['status']!r}, not 'awaiting_review'",
        }
    wf_path = run.get("workflow_path")
    repo_dir = str(Path(wf_path).parent) if wf_path else None
    ci = _read_pr_checks(run.get("pr_url"), repo_dir)
    if ci.state in (CI_FAILING, CI_UNKNOWN) and not force:
        reason = (
            f"CI is RED on this PR — failing: {', '.join(ci.failing) or 'unnamed check(s)'}"
            if ci.state == CI_FAILING else
            f"the checks on this PR CANNOT BE READ ({ci.detail}) — and a check state nobody "
            "could read is never a pass"
        )
        return {
            "ok": False, "task_id": run["task_id"], "pr_checks": ci.state,
            "error": f"{reason}. Refusing to approve it. The dispatcher sends a red PR back "
                     "to its agent on the next tick; if you believe the failure is unrelated "
                     "to this PR, re-run with --force and say why in the body.",
        }
    posted, detail = (False, "no body given")
    if (body or "").strip():
        posted, detail = _post_pr_comment(run.get("pr_url"), repo_dir, body.strip())
    notes = {
        CI_PASSING: "checks are green",
        CI_PENDING: "⚠ the checks have NOT settled yet — approving is fine, merging is not",
        CI_NONE: "⚠ this PR has NO checks at all (no checks is not the same as passing)",
        CI_FAILING: "⛔ CI is RED and this approval was FORCED",
        CI_UNKNOWN: "⛔ the checks COULD NOT BE READ and this approval was FORCED",
    }
    return {
        "ok": True, "task_id": run["task_id"], "status": "awaiting_review",
        "branch_name": run.get("branch_name"), "pr_url": run.get("pr_url"),
        "comment_posted": posted, "comment_detail": detail,
        "pr_checks": ci.state, "ci_note": notes.get(ci.state, ci.state),
        "forced": bool(force and ci.state in (CI_FAILING, CI_UNKNOWN)),
        "note": "approved — the run stays awaiting_review; merging is still a human's call",
    }


def reopen(ident: str, reason: str = "") -> dict:
    """Put a ``needs_human`` run BACK under review — the human-takeover re-entry.

    ``needs_human`` is terminal everywhere else in this file: the rework loop gave up on
    it (CMX-68), and ``request_changes``/``approve`` both refuse anything that is not
    ``awaiting_review``. That left a human who fixes the branch themselves and pushes a
    new commit with no in-contract way back — the only escape was a raw ``gh pr merge``,
    which never re-verifies the fixed head and skips the judge entirely (a self-review
    hole). This is the missing edge: flip the row back to ``awaiting_review`` so the
    EXISTING ``judge run`` / ``review`` / ``merge`` path picks the fixed head up exactly
    as it would a fresh PR. It does not touch the branch, the worktree, or the PR — those
    were already preserved by the escalation this reverses.

    ``rework_count`` is left exactly as it was. That is deliberate, not an oversight: if
    the judge blocks the "fixed" head again, ``request_changes`` sends it to
    ``changes_requested`` and the very next tick's cap check (the row's ``rework_count``
    is still at the cap) escalates it straight back to ``needs_human`` — no wasted
    automatic rework attempt, just the loop correctly refusing to spend a budget it has
    already spent. A human fix that actually passes the judge never spends the budget at
    all: it rides ``awaiting_review`` → ``merge`` like any other clean run.

    ⛔ THE NEW-COMMIT GATE. Refuses to reopen unless the branch's CURRENT head (re-read
    from ``gh``, right here — never trusted stale off the row) differs from ``judge_sha``,
    the commit the judge last ruled on. Without this, reopening an unchanged head (no fix
    pushed, wrong branch, fat-fingered) would flip the row to ``awaiting_review`` carrying
    its OLD failing verdict — and the dispatcher judges once per head commit, so the judge
    would never re-run to catch it. That stale, already-rejected head would then be
    reachable by ``review --approve`` → ``merge``: the "reopen the same failing code" hole.

    🔁🛑 CMX-198. ``reopen_count`` is bumped every successful call — the counter
    ``CHELA_MAX_REWORKS`` does NOT cover, because this is the human-takeover path, not the
    dispatcher's automatic one. Past the 3rd reopen, if nothing under ``chela/`` has
    changed since the FIRST reopen (a diff of the two head shas, read live from GitHub —
    see :func:`_production_files_changed`), the return carries a ``nudge``: an
    informed-consent signal that the judge may be hardening its own proof rather than
    fixing the feature, not a refusal. It never blocks; a human who really is still fixing
    something gets the same ``ok: True`` either way.
    """
    run = resolve_run(ident)
    if run is None:
        return {"ok": False, "error": f"no run matches {ident!r} (task id, branch, or window name)"}
    task_id = run["task_id"]
    if run["status"] != "needs_human":
        return {
            "ok": False, "task_id": task_id,
            "error": f"run is in status {run['status']!r}, not 'needs_human' — only a run "
                     "the rework loop actually gave up on can be reopened",
        }

    # ⛔ GUARD: the new-commit gate. The dispatcher judges ONE PASS PER HEAD COMMIT
    # (`pr_head_sha` vs `judge_sha` — see the cap check around line 2200). Reopening an
    # UNCHANGED head would flip the row to `awaiting_review` carrying its old failing
    # verdict, reachable by `review --approve` → `merge`, with the judge never re-running
    # to catch it — the exact "reopen the same failing code" loop/merge hole this call
    # exists to close. So: refresh the head sha from `gh` (the same read the poller does)
    # and refuse unless it has actually moved past the last-judged commit.
    wf_path = run.get("workflow_path")
    repo_dir = str(Path(wf_path).parent) if wf_path else None
    ci = _read_pr_checks(run.get("pr_url"), repo_dir)
    if ci.head_sha is None:
        return {
            "ok": False, "task_id": task_id,
            "error": f"could not read the PR's current head commit from GitHub ({ci.detail or 'no detail'}) "
                     "— refusing to reopen without knowing whether the head actually changed. Push a fix, "
                     "make sure `gh` can reach this PR, and try again.",
        }
    judge_sha = run.get("judge_sha")
    if judge_sha and ci.head_sha == judge_sha:
        return {
            "ok": False, "task_id": task_id,
            "error": f"the PR's head ({ci.head_sha[:12]}) is the SAME commit the judge already reviewed "
                     "and rejected — reopening it would send an unchanged head back into `awaiting_review`, "
                     "where the judge does not re-run (one pass per head commit) and the old failing "
                     "verdict would still be reachable by review → merge. Push a fix to the branch first, "
                     "then reopen.",
        }

    reviews = reviews_of(run)
    note = (reason or "").strip() or "reopened for review — a human fixed the branch"
    reviews.append({"round": len(reviews) + 1, "at": _now(), "body": note, "verdict": "reopened"})

    # 🔁🛑 CMX-198. `reopen_count` is THIS loop's counter — separate from `rework_count`
    # (the dispatcher's automatic one) on purpose; conflating them is the exact bug this
    # exists to fix. `first_reopen_head_sha` is set ONCE, on the 0→1 transition, and never
    # touched again: it is the fixed baseline every later reopen's head gets diffed
    # against, so the no-production-change check always spans the WHOLE reopen history.
    prior_reopen_count = run.get("reopen_count") or 0
    new_reopen_count = prior_reopen_count + 1
    first_reopen_sha = run.get("first_reopen_head_sha") or ci.head_sha

    with _db() as conn:
        # Same COMPARE-AND-SWAP discipline as request_changes: the row must still be the
        # needs_human row this call read, or a concurrent reconcile (a human merged the
        # stale PR directly, in the gap between the read above and this write) would be
        # resurrected out of `done`.
        cur = conn.execute(
            "UPDATE runs SET status='awaiting_review', review_history=?, last_error=NULL, "
            "pr_head_sha=?, reopen_count=?, first_reopen_head_sha=? "
            "WHERE task_id=? AND status='needs_human'",
            (json.dumps(reviews), ci.head_sha, new_reopen_count, first_reopen_sha, task_id),
        )
        conn.commit()
        if cur.rowcount == 0:
            now = conn.execute(
                "SELECT status FROM runs WHERE task_id=?", (task_id,)
            ).fetchone()
            current = now["status"] if now else "gone"
            log.warning("reopen: %s moved to %r before it could be reopened", task_id, current)
            return {
                "ok": False, "task_id": task_id,
                "error": f"run moved to {current!r} while this was being written (a tick "
                         "reconciled it, or someone else reopened it first) — nothing was "
                         "changed. Re-read it and decide again.",
            }

    wf_path = run.get("workflow_path")
    repo_dir = str(Path(wf_path).parent) if wf_path else None
    posted, detail = _post_pr_comment(
        run.get("pr_url"), repo_dir,
        f"🔓 Reopened for review by a human: {note}\n\nBack in `awaiting_review` — the "
        "judge and the merge gate will re-verify the fixed head before anything ships.",
    )
    if not posted:
        log.warning("reopen: %s is awaiting_review again, but the PR comment did not post "
                    "(%s)", task_id, detail)
    log.info("reopen: %s (needs_human) → awaiting_review (reopen %d)", task_id, new_reopen_count)

    # ⭐ THE NUDGE. Advisory only — see the docstring. Only worth asking GitHub about past
    # the 3rd reopen (rounds 1-2 are never enough signal, and every round below that would
    # be an extra `gh api` call for nothing).
    nudge = None
    if new_reopen_count >= 3 and first_reopen_sha:
        touched, diff_detail = _production_files_changed(
            run.get("pr_url"), repo_dir, first_reopen_sha, ci.head_sha,
        )
        if touched is False:
            nudge = (
                f"{new_reopen_count} rounds, no production change since the first reopen "
                f"({diff_detail}) — the judge is hardening the proof, not the feature. "
                "Merging is a defensible call."
            )
            event_log.append(
                "reopen_nudge", f"{task_id}: {nudge}",
                payload={
                    "task_id": task_id, "pr_url": run.get("pr_url"),
                    "reopen_count": new_reopen_count,
                    "first_reopen_head_sha": first_reopen_sha, "head_sha": ci.head_sha,
                },
            )

    result = {
        "ok": True, "task_id": task_id, "status": "awaiting_review",
        "branch_name": run.get("branch_name"), "pr_url": run.get("pr_url"),
        "rework_count": run.get("rework_count") or 0, "max_reworks": max_reworks(),
        "reopen_count": new_reopen_count,
        "comment_posted": posted, "comment_detail": detail,
    }
    if nudge:
        result["nudge"] = nudge
    return result


def retry(ident: str, reason: str = "") -> dict:
    """🔁🚪 Give a ``needs_human`` run ONE MORE automatic rework round — "keep going".

    ``reopen`` answers "I fixed it myself, re-verify the new head" and REFUSES an
    unchanged one (the new-commit gate) — on purpose, because flipping straight to
    ``awaiting_review`` would let a stale, already-rejected head reach ``merge``
    unjudged. That refusal is correct for what ``reopen`` is for, and it leaves the
    OTHER intent a ``needs_human`` verdict just as often provokes with no in-contract
    exit: a human who read the verdict, does not want to fix it by hand, and does not
    want to merge past it either — just wants the SAME automatic loop to have another
    swing at the SAME head. Hit live on CMX-231, twice, with the only escape being to
    edit the runs DB by hand. This is that exit.

    Unlike ``reopen`` this does not touch ``awaiting_review`` or the judge/merge path at
    all — it sends the run back to ``changes_requested``, the automatic rework loop's own
    carrier, exactly where a failing verdict already puts it. The very next dispatcher
    tick re-spawns the agent in its own worktree with the SAME rework prompt (the latest
    verdict, unchanged) it would have gotten on a normal round — this is not a new code
    path for the agent, only a human-granted extension of the one that already exists.

    ``rework_count`` is left exactly as ``reopen`` leaves it: untouched. Bumping it here
    would spend the human's grant before the agent ever got a slot, and a launch that
    then fails (:func:`_rework_failed`) would double-charge the round. Instead
    ``retry_count`` — a SEPARATE counter, never read by the automatic loop's spend path —
    records how many extra rounds a human has granted, and the escalation cap check
    (the ``changes_requested`` scan above, in :func:`dispatch_tick`) adds it to
    ``CHELA_MAX_REWORKS`` before comparing. A run granted zero retries escalates at
    EXACTLY the automatic cap, same as before this existed.

    Same compare-and-swap discipline as ``reopen``/``request_changes``: refuses unless
    the row is STILL ``needs_human`` at write time, so a concurrent reconcile (a human
    merged the stale PR directly, in the gap between the read and this write) cannot be
    resurrected out of ``done``.
    """
    run = resolve_run(ident)
    if run is None:
        return {"ok": False, "error": f"no run matches {ident!r} (task id, branch, or window name)"}
    task_id = run["task_id"]
    if run["status"] != "needs_human":
        return {
            "ok": False, "task_id": task_id,
            "error": f"run is in status {run['status']!r}, not 'needs_human' — only a run "
                     "the rework loop actually gave up on can be retried",
        }

    reviews = reviews_of(run)
    note = (reason or "").strip() or "asked to keep going — one more automatic round, same head"
    reviews.append({"round": len(reviews) + 1, "at": _now(), "body": note, "verdict": "retry"})
    new_retry_count = (run.get("retry_count") or 0) + 1

    with _db() as conn:
        cur = conn.execute(
            "UPDATE runs SET status='changes_requested', review_history=?, last_error=NULL, "
            "retry_count=? WHERE task_id=? AND status='needs_human'",
            (json.dumps(reviews), new_retry_count, task_id),
        )
        conn.commit()
        if cur.rowcount == 0:
            now = conn.execute(
                "SELECT status FROM runs WHERE task_id=?", (task_id,)
            ).fetchone()
            current = now["status"] if now else "gone"
            log.warning("retry: %s moved to %r before it could be retried", task_id, current)
            return {
                "ok": False, "task_id": task_id,
                "error": f"run moved to {current!r} while this was being written (a tick "
                         "reconciled it, or someone else acted on it first) — nothing was "
                         "changed. Re-read it and decide again.",
            }

    wf_path = run.get("workflow_path")
    repo_dir = str(Path(wf_path).parent) if wf_path else None
    posted, detail = _post_pr_comment(
        run.get("pr_url"), repo_dir,
        f"🔁 Asked to keep going by a human: {note}\n\nBack in the automatic rework loop "
        "for one more round on the same head — the next dispatcher tick re-spawns it.",
    )
    if not posted:
        log.warning("retry: %s is changes_requested again, but the PR comment did not post "
                    "(%s)", task_id, detail)
    cap = max_reworks()
    log.info(
        "retry: %s (needs_human) → changes_requested (rework %d/%d, retry #%d)",
        task_id, run.get("rework_count") or 0, cap + new_retry_count, new_retry_count,
    )

    return {
        "ok": True, "task_id": task_id, "status": "changes_requested",
        "branch_name": run.get("branch_name"), "pr_url": run.get("pr_url"),
        "rework_count": run.get("rework_count") or 0, "max_reworks": cap,
        "retry_count": new_retry_count,
        "comment_posted": posted, "comment_detail": detail,
    }


def set_judge_state(task_id: str, state: str, detail: str = "", *, sha: str | None = None) -> None:
    """Record what the judge concluded on this run. ⛔ It writes NOTHING ELSE (besides ``sha``).

    The judge's only way to change a run's STATUS is :func:`request_changes` — the one
    carrier, shared with the CI gate and with a human reviewer. This column is a report, not
    a lever: ``clean`` does not approve, does not merge, and does not move the row out of
    ``awaiting_review``, where the orchestrator will find it. ``cannot_verify`` is the same
    non-answer the CI gate's ``unknown`` is, and it is recorded rather than swallowed
    precisely because an unknown that goes quiet is indistinguishable from a pass.

    ``sha``, when given, also stamps ``judge_sha`` — for the ONE caller that judges a commit
    without having gone through ``_spawn_judge`` first (a re-run that rebuilt its own reaped
    worktree, CMX-201): that caller's judgment would otherwise leave ``judge_sha`` pointing at
    a stale commit, and the per-sha trigger would immediately re-spawn a redundant judge on
    the very sha this call just verified. Every normal caller passes nothing and this column
    is untouched, exactly as before.
    """
    with _db() as conn:
        if sha:
            conn.execute(
                "UPDATE runs SET judge_state=?, judge_detail=?, judge_sha=? WHERE task_id=?",
                (state, (detail or "")[:2000], sha, task_id),
            )
        else:
            conn.execute(
                "UPDATE runs SET judge_state=?, judge_detail=? WHERE task_id=?",
                (state, (detail or "")[:2000], task_id),
            )
        conn.commit()


def _cleanup_worktree_on_done(repo_path: Path, row: sqlite3.Row) -> None:
    """Free a finished run's worktree the moment its row goes `done` — not later.

    `done` is terminal and NOT_CLAIMABLE: nothing ever reworks a `done` row, so nothing
    ever needs its worktree again once this fires. Previously nothing removed it at all
    — the worktree.py:35 comment on `ensure_worktree`'s collision handling made it sound
    like `_prune_done_rows` was the thing freeing this disk, but that function only ever
    dropped the DB row; a run's checkout + `.venv`/`node_modules` sat on disk until
    someone noticed and hand-pruned it (51 orphaned worktrees / 2.6 GB, 2026-07-22).
    Removing it here, right as the row commits to `done`, closes that gap.

    Only the worktree DIRECTORY goes — the branch is left alone on purpose:
    `_max_existing_task_number` scans branches (local + remote) to avoid handing out a
    task_number that's still spoken for, and a live branch with no worktree is exactly
    the state it already expects to see for a finished run.

    Best-effort and silent on an already-gone worktree (hand-cleaned, or this task never
    got past `claimed`): `remove_worktree` itself only warns on failure, it never raises.
    """
    worktree_path = row["worktree_path"]
    if not worktree_path:
        return
    wt_path = Path(worktree_path)
    if not wt_path.is_dir():
        return
    remove_worktree(repo_path, wt_path)


def _prune_done_rows(
    conn: sqlite3.Connection,
    workflow_path: str,
    open_ids: frozenset[str] = frozenset(),
    keep: int = DONE_HISTORY_PER_WORKFLOW,
) -> int:
    """Keep at most `keep` most-recent done rows for a workflow, drop the older ones.

    Ordering uses ended_at (falling back to started_at) so rows without timestamps
    sort last and get pruned first.

    A `done` row whose `task_id` is still in `open_ids` — its tracker line has not
    been struck yet (an offline strike, a diverged base, or simply a strike still
    pending this same tick) — is NEVER pruned, retention window or not. It is the
    only thing left standing between the claim loop and a duplicate dispatch of a
    task whose PR already merged (the out-of-band-merge guard): prune it and the
    next claim finds `existing is None` and spawns fresh, with no surviving row to
    stop it.
    """
    guard_clause = ""
    params: list = [workflow_path]
    if open_ids:
        guard_clause = f"AND task_id NOT IN ({','.join('?' * len(open_ids))})"
        params.extend(open_ids)
    params.extend([workflow_path, keep])
    cursor = conn.execute(
        f"""DELETE FROM runs
           WHERE status='done'
             AND workflow_path=?
             {guard_clause}
             AND task_id NOT IN (
               SELECT task_id FROM runs
               WHERE status='done' AND workflow_path=?
               ORDER BY COALESCE(ended_at, started_at, '') DESC
               LIMIT ?
             )""",
        params,
    )
    return cursor.rowcount


def _tmux_windows() -> set[str]:
    out = subprocess.run(
        ["tmux", "list-windows", "-t", TMUX_SESSION, "-F", "#W"],
        capture_output=True, text=True,
    )
    if out.returncode != 0:
        return set()
    return set(line.strip() for line in out.stdout.splitlines() if line.strip())


def poll_interval(workflow_path: str | Path, default: float | None = None) -> float:
    """The effective seconds between ticks for this workflow.

    Reads `polling.interval_ms` from the (hot-reloaded) front matter, falling
    back to ``default`` / :func:`chela.config.dispatch_tick_interval` (itself a
    Timing-tab, dashboard-writable knob — CMX-217). Called from the daemon loop
    on every pass so an edited interval takes effect without a restart; it is
    stat-gated, so an unchanged file costs one ``stat``.
    """
    base = dispatch_tick_interval() if default is None else default
    return poll_interval_seconds(load_workflow_cached(workflow_path).workflow, base)


def _refused(error: str | None, refused: bool = False) -> dict:
    """A tick that did NOTHING, and says why. Same shape every caller already reads:
    `blocked` is what the daemon loop edge-triggers on and what the Settings drawer
    renders, so a refusal surfaces through the plumbing a broken workflow already uses.

    `refused` distinguishes the two. A BLOCKED workflow (unparseable) still reconciles on
    its last known-good config; a REFUSED one does not reconcile either — it does nothing
    at all — and the daemon must not tell the operator otherwise.
    """
    return {
        "open": 0, "reconciled_done": 0, "reconciled_failed": 0, "dispatched": 0,
        "pr_state_refreshed": 0, "watchdog_renudged": 0, "tracker_struck": 0,
        "reworked": 0, "escalated": 0, "ci_failed": 0, "judged": 0, "judge_lost": 0,
        "trial_ledger": 0, "disk_budget_exceeded": False,
        "blocked": True, "error": error, "held": False, "hold_expired": False,
        "refused": refused,
    }


# Workflows currently refused by the workspace fence — so the ERROR is logged on the
# EDGE, not once per tick forever (a 60s drumbeat is how an operator learns to skip logs).
_escaped: set[str] = set()


def tick(workflow_path: str | Path) -> dict:
    """One dispatcher pass. Returns a dict summary for logging.

    The workflow is re-read at this tick boundary (never mid-dispatch): a change
    is picked up here and governs everything this pass does, while an agent
    already in flight keeps the config it launched with.

    If the file is currently unparseable, the LAST KNOWN-GOOD config stays in
    force: reconciliation (PR states, done/failed, tracker strikes) keeps
    running, but new dispatches are BLOCKED until it parses again, and the
    summary carries ``blocked`` + ``error`` so the daemon and the Settings
    drawer can say why (Symphony SPEC 6.2/6.3).

    Two things gate the CLAIM specifically, and neither touches reconciliation:

    * a **queue hold** (:mod:`chela.hold`) — the orchestrator's "claim nothing, I
      am rewriting the queue". Taken and released at this tick boundary, never
      mid-``_spawn``. ``held`` in the summary.
    * **fetch-then-claim** (:func:`_claim_order`) — the queue is re-read from
      ``origin/<base_branch>`` at the instant of claiming, not from the parse at
      the top of this tick.

    Dispatch itself has TWO sources of work, in this order: the runs a reviewer sent back
    (``changes_requested`` → :func:`_respawn_rework`, capped by ``CHELA_MAX_REWORKS`` and
    then escalated to ``needs_human``), and only then the fresh queue. Both draw from the
    SAME ``concurrency.max`` slots — a rework is finishing work, not free work.
    """
    status = load_workflow_cached(workflow_path)
    wf = status.workflow
    if wf is None:
        # Nothing has ever parsed — there is no last-good config to reconcile
        # against. Report, don't raise: a broken file must not kill the loop.
        return _refused(status.error)
    # THE WORKSPACE FENCE, and it comes before EVERYTHING this tick would touch — the
    # claim, the reconcile, the tracker strike, the spawn. `CHELA_DIR` isolates state,
    # never the workspace (see workflow.workspace_escape), so a daemon on a scratch state
    # dir would otherwise git-push, strike the tracker and launch agents in the REAL
    # install's worktrees. There is nothing this tick can safely do; it does nothing.
    escape = workspace_escape(wf)
    if escape:
        if str(workflow_path) not in _escaped:      # edge-triggered: LOUD once, not a drumbeat
            _escaped.add(str(workflow_path))
            log.error("Dispatch REFUSED — %s", escape)
        return _refused(escape, refused=True)
    _escaped.discard(str(workflow_path))
    blocked = status.error is not None
    source = get_source(wf)
    open_tasks = source.list_open_tasks()
    open_ids = {t.id for t in open_tasks}
    tasks_by_id = {t.id: t for t in open_tasks}

    summary = {
        "open": len(open_tasks),
        "reconciled_done": 0,
        "reconciled_failed": 0,
        "dispatched": 0,
        "pr_state_refreshed": 0,
        "watchdog_renudged": 0,
        "watchdog_unblocked": 0,
        "tracker_struck": 0,
        "reworked": 0,
        "escalated": 0,
        "ci_failed": 0,
        "judged": 0,
        "judge_lost": 0,
        "trial_ledger": 0,
        "disk_budget_exceeded": False,
        "blocked": blocked,
        "error": status.error,
        "held": False,
        "hold_expired": False,
    }
    merged_in_tick = 0  # awaiting_review → done transitions; fires hooks.after_done

    with _db() as conn:
        # 0. Refresh pr_state + pr_mergeable for any row whose PR could still
        # change. Skips rows whose pr_state is already terminal
        # ('merged'/'closed') — gh's GraphQL is cheap but not free, and the
        # Kanban only gates the Merge button on `pr_state === 'open'`, so once
        # we've seen a terminal state there's nothing to recheck. mergeable can
        # flip CONFLICTING/MERGEABLE as sibling PRs land, so refreshing it on
        # every tick (for still-open PRs) keeps the Merge-all count honest.
        # Runs against rows of any status that carry a pr_url (done cards still
        # surface a Merge button in the UI).
        #
        # ⛔ SCOPED TO THIS WORKFLOW. `tick` runs once per workflow, so an unscoped query
        # here asked GitHub about every PR in the fleet, once per workflow — W×P `gh` spawns
        # a cycle, and the reward for hitting the rate limit is CI_UNKNOWN on everything,
        # which (correctly, and catastrophically) refuses every merge at once.
        pr_rows = conn.execute(
            "SELECT task_id, status, pr_url, workflow_path, pr_head_sha, ci_pending_since "
            "FROM runs WHERE workflow_path=? AND pr_url IS NOT NULL "
            "AND (pr_state IS NULL OR pr_state='open')",
            (str(wf.path),),
        ).fetchall()

        # ⛔ EVERY `gh` CALL HAPPENS HERE, WITH NO WRITE TRANSACTION OPEN. The first UPDATE
        # takes SQLite's write lock and holds it until the commit — so doing the network
        # inside that loop meant 20 PRs × a couple of gh round-trips of lock, and every
        # concurrent writer (a `chela review`, a dashboard merge, a `task-finished`) got
        # `database is locked` back. Read first, write second: the loop below touches
        # nothing but memory.
        ci_now: dict[str, CIStatus] = {}   # task_id → what GitHub said THIS tick
        reads: list[tuple] = []
        for pr_row in pr_rows:
            wf_path = pr_row["workflow_path"]
            repo_dir = str(Path(wf_path).parent) if wf_path else None
            state, mergeable = _read_pr_status(pr_row["pr_url"], repo_dir)
            # 0b. THE CHECKS — read from their owner, for the runs parked in review.
            #
            # Only the LIVE review states (CI_POLL_STATUSES): they are the ones a merge gate
            # protects and the only ones a red CI can send back. A `running` row's PR is
            # still being pushed to, and asking GitHub about a moving target every 60s buys
            # nothing; a `needs_human` row is terminal, and polling it forever buys less.
            # ⛔ Rows whose pr_state is already terminal never reach here at all (the WHERE
            # above) — which is also the "no resurrection" rule holding: a merged run's red
            # CI does NOTHING.
            ci = None
            if pr_row["status"] in CI_POLL_STATUSES:
                ci = _read_pr_checks(pr_row["pr_url"], repo_dir)
                ci_now[pr_row["task_id"]] = ci
                if ci.state == CI_UNKNOWN:
                    # ⛔ Loud, and never a pass: a check state nobody could read is the
                    # doctor rule's CANNOT VERIFY. The merge gate refuses it downstream.
                    log.warning(
                        "CI: could not read the checks on %s (%s) — recorded as UNKNOWN, "
                        "which is NOT a pass: nothing will merge it and nothing sends it "
                        "back until GitHub can be asked.",
                        pr_row["task_id"], ci.detail,
                    )
            reads.append((pr_row, state, mergeable, ci))

        for pr_row, state, mergeable, ci in reads:
            if ci is not None:
                # The unsettled clock: started when a pending spell begins, restarted when a
                # new head sha begins its own, cleared the moment the checks settle. Only a
                # spell that outlives CI_PENDING_STALE_SECONDS is stuck (see 1c′).
                pending_since = None
                if ci.state == CI_PENDING:
                    same_sha = ci.head_sha is None or ci.head_sha == pr_row["pr_head_sha"]
                    pending_since = (pr_row["ci_pending_since"] if same_sha else None) or _now()
                conn.execute(
                    "UPDATE runs SET pr_checks=?, pr_head_sha=COALESCE(?, pr_head_sha), "
                    "ci_pending_since=? WHERE task_id=?",
                    (ci.state, ci.head_sha, pending_since, pr_row["task_id"]),
                )
            if state is None and mergeable is None:
                continue
            # COALESCE so a partial read (e.g. mergeable still UNKNOWN right
            # after PR creation, resolving to MERGEABLE on a later tick) never
            # clobbers a known value with None.
            conn.execute(
                "UPDATE runs SET pr_state=COALESCE(?, pr_state), "
                "pr_mergeable=COALESCE(?, pr_mergeable) WHERE task_id=?",
                (state, mergeable, pr_row["task_id"]),
            )
            summary["pr_state_refreshed"] += 1
        conn.commit()

        # 1. Reconcile
        live_windows = _tmux_windows()
        # A run finishes when its PR merges. The task ALSO leaving the source is
        # still honoured (a human striking the line by hand, an issue closing
        # itself, or the legacy flow where the agent struck its own line), but it
        # is no longer the signal we rely on: agents don't touch the tracker any
        # more, so an awaiting_review row would otherwise sit there forever
        # waiting for a line that only we will ever strike (_strike_merged_tasks).
        #
        # The review states are ALL reconcilable, not just awaiting_review: a human can
        # merge a PR that is sitting in `changes_requested` (they decided the verdict was
        # wrong) or in `needs_human` (they fixed it themselves), and a merged PR is done no
        # matter which state the loop left the row in. A status list that only knew
        # awaiting_review would strand those rows forever — and the run would keep its
        # branch, its worktree and its tracker line, unstruck.
        #
        # ⛔ SCOPED TO THIS WORKFLOW, same reason as the PR-refresh query above.
        # `open_ids` is only THIS workflow's open tasks, so an unscoped query here
        # would pull every OTHER workflow's active/review runs too, find their
        # task_id missing from open_ids, and mark them `done` ("removed from
        # source, window killed") — killing a live sibling workflow's run within
        # one tick of dispatch.
        #
        # `failed` is included alongside the review states (RECONCILE_MERGE_STATUSES)
        # so a run whose PR merged OUT OF BAND while it still reads `failed` (retries
        # left) reconciles to `done` here too — otherwise the claim loop below would
        # re-dispatch a task whose work already shipped (the out-of-band-merge guard).
        rows = conn.execute(
            "SELECT * FROM runs WHERE workflow_path=? AND status IN ({})".format(
                ",".join("?" * len(ACTIVE_STATUSES + RECONCILE_MERGE_STATUSES))
            ),
            (str(wf.path), *ACTIVE_STATUSES, *RECONCILE_MERGE_STATUSES),
        ).fetchall()
        for row in rows:
            if row["status"] in RECONCILE_MERGE_STATUSES and row["pr_state"] == "merged":
                # pr_state was refreshed in phase 0 above, so we see the merge on
                # the very tick it lands. The tracker strike happens in 1b, after
                # this transition is durable — if it fails, this row stays `done`
                # and the strike is simply retried on the next tick.
                if row["window_name"]:
                    _kill_window(row["window_name"])
                conn.execute(
                    "UPDATE runs SET status='done' WHERE task_id=?", (row["task_id"],)
                )
                conn.commit()
                _cleanup_worktree_on_done(wf.path.parent, row)
                merged_in_tick += 1
                summary["reconciled_done"] += 1
                log.info("Task %s done (PR merged)", row["task_id"])
                continue
            if row["task_id"] not in open_ids and row["status"] in REVIEW_STATUSES:
                # Read the agent's transcript *before* killing the window —
                # transcript resolution maps window_name → cwd → transcript via
                # the live tmux pane, and that mapping disappears once tmux drops
                # the window. For awaiting_review rows the window is already dead
                # (the task-finished CLI kills it), so the transcript read is a
                # no-op fallback for the rare case where it wasn't killed.
                #
                # A REVIEW-status row already carries its own completion evidence
                # (the agent opened a PR and reached a review state) — the task
                # leaving the tracker (a human striking the line on merge, or the
                # legacy self-strike) is legitimate done-evidence here. Preserve
                # the original ended_at so the timestamp reflects when the agent
                # finished, not when the human merged the PR.
                pr_url = _read_pr_url(row["window_name"])
                if row["window_name"]:
                    _kill_window(row["window_name"])
                conn.execute(
                    "UPDATE runs SET status='done', pr_url=COALESCE(?, pr_url) WHERE task_id=?",
                    (pr_url, row["task_id"]),
                )
                conn.commit()
                _cleanup_worktree_on_done(wf.path.parent, row)
                merged_in_tick += 1
                summary["reconciled_done"] += 1
                log.info("Task %s done (removed from source, window killed)", row["task_id"])
                continue
            if row["task_id"] not in open_ids:
                # claimed/running, no review state: "left the tracker" is NOT proof of a
                # merge (cmx-100 — an orphaned agent whose window died before ever opening a
                # PR still leaves the tracker for unrelated reasons: a human edit, a re-hash,
                # uncommitted-tracker churn). Gate `done` on completion evidence — a merged
                # PR — same as _read_pr_url already fetches for the review-status branch
                # above. No evidence means this row is NOT done here: it falls through to
                # the window-gone→failed check below (reused, not duplicated) if its window
                # is dead, or is left alone — still running/claimed — for the watchdog if
                # its window is still alive. Don't kill a window we haven't proven finished.
                pr_url = _read_pr_url(row["window_name"])
                effective_pr_url = pr_url or row["pr_url"]
                wf_path = row["workflow_path"]
                repo_dir = str(Path(wf_path).parent) if wf_path else None
                pr_state = row["pr_state"]
                if pr_state != "merged" and effective_pr_url:
                    pr_state, _mergeable = _read_pr_status(effective_pr_url, repo_dir)
                if pr_state == "merged":
                    if row["window_name"]:
                        _kill_window(row["window_name"])
                    conn.execute(
                        "UPDATE runs SET status='done', ended_at=?, pr_url=COALESCE(?, pr_url), "
                        "pr_state=COALESCE(?, pr_state) WHERE task_id=?",
                        (_now(), pr_url, pr_state, row["task_id"]),
                    )
                    conn.commit()
                    _cleanup_worktree_on_done(wf.path.parent, row)
                    summary["reconciled_done"] += 1
                    log.info("Task %s done (removed from source, merged PR found)", row["task_id"])
                    continue
                # No completion evidence — fall through (no `continue`): the window-gone→
                # failed check right below catches a dead window; a live window (or a
                # claimed row with none yet) is left untouched for the watchdog.
            if row["status"] == "running" and row["window_name"] and row["window_name"] not in live_windows:
                # A dead REWORK re-enters the rework loop; only a dead first dispatch is a
                # `failed` the claim loop may retry from scratch (_rework_failed).
                if _is_rework(row):
                    _rework_failed(conn, row, "rework agent's tmux window disappeared")
                    continue
                attempt = row["attempt"]
                conn.execute(
                    "UPDATE runs SET status='failed', ended_at=?, last_error=? WHERE task_id=?",
                    (_now(), "tmux window disappeared", row["task_id"]),
                )
                summary["reconciled_failed"] += 1
                log.warning("Task %s failed (window gone, attempt %d)", row["task_id"], attempt)
                continue

            # Watchdog: a running row whose window is alive but stuck — either the
            # startup race dropped its prompt (it sits at an idle, empty `❯`) or it
            # blocked on an input dialog no dispatched agent has a human to answer.
            # Re-send / unblock once, then fail into the attempt-capped re-dispatch
            # path only on affirmative evidence it is not working.
            if (
                row["status"] == "running"
                and row["window_name"]
                and row["window_name"] in live_windows
            ):
                started = _parse_ts(row["started_at"])
                idle_age_ok = (
                    started is not None
                    and (datetime.now(timezone.utc) - started).total_seconds()
                    >= WATCHDOG_IDLE_MINUTES * 60
                )
                if idle_age_ok:
                    window = row["window_name"]
                    pane = _capture_pane(window)
                    status = _agent_status(window)
                    task = tasks_by_id.get(row["task_id"])
                    nudged = _parse_ts(row["idle_nudged_at"])
                    nudge_expired = (
                        nudged is not None
                        and (datetime.now(timezone.utc) - nudged).total_seconds()
                        >= WATCHDOG_IDLE_MINUTES * 60
                    )

                    # (a) Blocked on an input dialog — `AskUserQuestion` or a gated
                    # permission prompt: the native status is "waiting" and the pane
                    # shows a picker, not a bare `❯`, so the idle-empty check below
                    # never fires and a dispatched agent (no human) hangs to
                    # MAX_ATTEMPTS. Escape it so the model falls back to its own
                    # default — but only after a full grace, so a human answering via
                    # Telegram still wins. If it is STILL waiting a grace after the
                    # Escape, it is not recovering: fail it into re-dispatch.
                    if status == "waiting":
                        if nudged is not None and nudge_expired:
                            if _is_rework(row):
                                _rework_failed(
                                    conn, row,
                                    "rework agent blocked on an input dialog with no human",
                                )
                                continue
                            conn.execute(
                                "UPDATE runs SET status='failed', ended_at=?, last_error=? WHERE task_id=?",
                                (_now(), "agent blocked on an input dialog with no human", row["task_id"]),
                            )
                            summary["reconciled_failed"] += 1
                            log.warning("Task %s failed (input-dialog block, no human to answer)", row["task_id"])
                        elif nudged is None:
                            _dismiss_input_block(window)
                            conn.execute(
                                "UPDATE runs SET idle_nudged_at=? WHERE task_id=?",
                                (_now(), row["task_id"]),
                            )
                            summary["watchdog_unblocked"] += 1
                            log.warning(
                                "Task %s blocked on an input dialog (AskUserQuestion?); "
                                "sent Escape to %s (no human present)",
                                row["task_id"], window,
                            )
                        continue

                    # (b) Idle-empty-prompt stall (the dropped-prompt strand) — but
                    # NEVER while the pane shows active work. The TUI paints its
                    # spinner + a live token/elapsed counter ABOVE an EMPTY input box,
                    # so a working agent trips `_pane_idle_empty_prompt`; the activity
                    # veto is what stops the watchdog nudging/failing it mid-work
                    # (cmx-158/159/160 were false-failed this way, 2026-07-23).
                    stuck = (
                        _pane_idle_empty_prompt(pane)
                        and not _pane_shows_activity(pane)
                        and status != "busy"
                    )
                    if not stuck:
                        continue
                    if nudged is not None:
                        if not nudge_expired:
                            continue          # nudged recently; give it the full window
                        # A terminal FAIL needs AFFIRMATIVE idleness ("idle"), never a
                        # merely-not-busy read: an UNREADABLE status (None) is not
                        # evidence of idleness, and failing on it is exactly what killed
                        # working agents. On an unreadable status, re-nudge instead — a
                        # genuinely stuck agent reads "idle" and fails correctly.
                        if status == "idle":
                            if _is_rework(row):
                                _rework_failed(
                                    conn, row,
                                    "rework agent idle at empty prompt after re-nudge",
                                )
                                continue
                            conn.execute(
                                "UPDATE runs SET status='failed', ended_at=?, last_error=? WHERE task_id=?",
                                (_now(), "agent idle at empty prompt after re-nudge", row["task_id"]),
                            )
                            summary["reconciled_failed"] += 1
                            log.warning("Task %s failed (idle at empty prompt after re-nudge)", row["task_id"])
                            continue
                        # status unreadable → fall through and re-nudge (no false fail).
                    # First nudge (nudged is None), or a re-nudge after an unreadable
                    # status at the fail step.
                    # ⛔ A stuck REWORK is re-nudged with its REWORK prompt, not the
                    # first-dispatch one: the two say opposite things ("branch and open a
                    # PR" vs "you are already on your branch, your PR is open, here is
                    # the verdict"). Re-seeding the wrong one is the same lost verdict as
                    # a `failed` rework, just delivered by hand.
                    prompt = _renudge_prompt(wf, row, task)
                    if prompt is None:
                        continue          # task gone from the tracker; nothing to re-send
                    _send_seed(window, prompt, row["task_id"])
                    conn.execute(
                        "UPDATE runs SET idle_nudged_at=? WHERE task_id=?",
                        (_now(), row["task_id"]),
                    )
                    summary["watchdog_renudged"] += 1
                    log.warning(
                        "Task %s idle at empty prompt; re-sent prompt to %s",
                        row["task_id"], window,
                    )

        # 1b. Strike the merged tasks in the tracker — we are its only writer.
        # Commit the reconcile first so the `done` rows are durable: if the
        # strike (or this process) dies, the pending set below is recomputed
        # from the runs table next tick and the strike simply retries. It is
        # derived state, never remembered — which is also what makes it
        # self-healing and what batches several runs merged in the same tick,
        # plus any strike a previous tick couldn't land, into ONE commit.
        conn.commit()
        pending_strikes = [
            r["task_id"]
            for r in conn.execute(
                "SELECT task_id FROM runs "
                "WHERE workflow_path=? AND status='done' AND pr_state='merged'",
                (str(wf.path),),
            ).fetchall()
            if r["task_id"] in open_ids  # still unstruck in the tracker
        ]
        if pending_strikes:
            summary["tracker_struck"] = _strike_merged_tasks(wf, source, pending_strikes)

        # 1c. A RED CI SENDS THE PR BACK — automatically, with no reviewer.
        #
        # A failing check is a FACT, not a judgment: GitHub owns it, we read it back, and
        # nothing here decides anything. That is why this is the loop's first automatic
        # driver and why it needs no judge — a wrong `changes_requested` would burn a whole
        # rework round, and CI cannot be wrong about whether it went red.
        #
        # It reuses `request_changes` — THE way back into the loop (CMX-68), never a second
        # path — so everything that already holds keeps holding: the compare-and-swap (a run
        # a human merged under us moves to `done` and this writes NOTHING — no resurrection),
        # the verdict history, the PR comment, the re-spawn into the ORIGINAL worktree.
        #
        # ⛔ ABOVE 1d, deliberately: 3b re-spawns every `changes_requested` row WITHOUT
        # re-checking the cap, because it trusts 1c to have escalated the spent ones already.
        # A verdict written after 1c would be re-spawned this same tick, one round over
        # budget. Written here, a run whose last round this was escalates below, in 1d, on
        # the tick it happens.
        conn.commit()   # release the write lock: request_changes opens its own connection
        for row in conn.execute(
            "SELECT * FROM runs WHERE workflow_path=? AND status='awaiting_review' "
            "AND pr_checks=?",
            (str(wf.path), CI_FAILING),
        ).fetchall():
            task_id = row["task_id"]
            ci = ci_now.get(task_id)
            sha = (ci.head_sha if ci else None) or row["pr_head_sha"]
            if not sha:
                log.warning("CI: %s is red but GitHub named no head commit — not firing a "
                            "verdict that could not be fired exactly once", task_id)
                continue
            if row["ci_failed_sha"] == sha:
                continue        # this red has already been delivered. Each red fires ONCE.
            wf_dir = str(wf.path.parent)
            # The heavy read (a whole log archive) happens HERE and nowhere else: once, on
            # the transition into red — never on the poll. ⛔ BEFORE the sha is burned: the
            # first cut committed `ci_failed_sha` first, so anything that went wrong while
            # FETCHING the log (and the fetch is a subprocess reading a stranger's bytes)
            # took the verdict with it — the red was marked delivered and never fired again,
            # and the run sat red in awaiting_review until a human happened to look.
            log_tail = _failing_log_tail(wf_dir, ci.run_ids if ci else ())
            body = _ci_verdict_body(ci or CIStatus(CI_FAILING, sha), log_tail, row["pr_url"])
            # ⛔ NOW record the fired sha — after the tail is in hand, before the one step
            # that cannot be taken back. The failure modes are not symmetric: a crash after
            # this line costs at most ONE missed verdict (a human still sees the red PR),
            # while a crash before it would re-fire the same red every tick and burn the
            # whole rework budget on a single commit — the very bug this guard exists for.
            conn.execute("UPDATE runs SET ci_failed_sha=? WHERE task_id=?", (sha, task_id))
            conn.commit()
            try:
                result = request_changes(task_id, body)
            except sqlite3.Error as e:
                # request_changes opens its OWN connection, and a busy DB can refuse it. Two
                # things must not happen: the tick must not die (it still has merges to
                # reconcile and a queue to dispatch), and the red must not be lost. A refused
                # write wrote NOTHING, so the sha it was about is un-burned and this red fires
                # again next tick — the once-per-sha guard is a guard against DOUBLE firing,
                # never a licence to fire zero times.
                log.error("CI: %s is red but the verdict could not be written: %s — the sha is "
                          "un-burned and this red will be retried next tick", task_id, e)
                try:
                    conn.execute("UPDATE runs SET ci_failed_sha=? WHERE task_id=?",
                                 (row["ci_failed_sha"], task_id))
                    conn.commit()
                except sqlite3.Error:
                    pass   # the DB is the thing that is broken; next tick re-reads the red
                continue
            if not result.get("ok"):
                # The CAS refused it — the row moved (a human merged it, or a reviewer got
                # there first). Nothing was written, and nothing should be.
                log.info("CI: %s is red, but the verdict was not written: %s",
                         task_id, result.get("error"))
                continue
            summary["ci_failed"] += 1
            log.warning(
                "CI is RED on %s (%s) — run %s sent back for rework (round %s, cap %s): %s",
                row["pr_url"] or "?", sha[:12], task_id, result.get("round"),
                result.get("max_reworks"), ", ".join(ci.failing) if ci else "?",
            )

        # 1c′. A CHECK THAT NEVER SETTLES IS NOT A CHECK WE ARE STILL WAITING FOR.
        #
        # `pending` is the state with no exit of its own: no verdict fires on it (correctly —
        # unsettled is not red), every merge gate refuses it (correctly — nobody knows yet
        # what merging it means), and the Kanban card does not even render a Merge button.
        # A rollup that stays there — a `WAITING` deployment gate with required reviewers, a
        # check an app registered and never reported — parks the run FOREVER, silently. That
        # is the one thing the loop is not allowed to do. Past GitHub's own job ceiling it is
        # not running any more, it is stuck, and it goes to a human WITH THE REASON.
        now = _parse_ts(_now())
        for row in conn.execute(
            "SELECT * FROM runs WHERE workflow_path=? AND status='awaiting_review' "
            "AND pr_checks=? AND ci_pending_since IS NOT NULL",
            (str(wf.path), CI_PENDING),
        ).fetchall():
            since = _parse_ts(row["ci_pending_since"])
            if not since or not now or (now - since).total_seconds() < CI_PENDING_STALE_SECONDS:
                continue
            hours = int((now - since).total_seconds() // 3600)
            _escalate(
                conn, row,
                f"the checks on this PR have not settled in {hours}h — they are not running, "
                "they are STUCK (a deployment gate awaiting approval, or a check an app "
                "registered and never reported). Nothing can merge a PR whose checks never "
                "answer, and nothing can send it back either: pending is not red. Branch, "
                "worktree and PR are preserved.",
            )
            summary["escalated"] += 1

        # 1d. ESCALATE the runs that have spent their rework budget.
        #
        # ⛔ Deliberately HERE — with reconciliation, ABOVE the `blocked` and `hold` returns
        # — and not with the re-spawn in 3b. Escalation is not a claim: it takes no slot, it
        # starts no agent, and it is the only thing that makes `changes_requested` a state a
        # run can LEAVE without a human. Gating it behind the hold (as the first cut did)
        # meant a paused queue also paused the ONE transition that says "the loop gave up,
        # come look" — and a hold that is forgotten is exactly when you need that said. A
        # broken WORKFLOW.md must not silence it either: `needs_human` is how it gets fixed.
        cap = max_reworks()
        for row in conn.execute(
            "SELECT * FROM runs WHERE workflow_path=? AND status='changes_requested'",
            (str(wf.path),),
        ).fetchall():
            # 🔁🚪 CMX-237. A `chela retry` grants extra rounds ON TOP of the automatic
            # budget — never folded into `cap` itself, so a run with no grant escalates at
            # EXACTLY `cap`, same as before this existed.
            effective_cap = cap + (row["retry_count"] or 0)
            if (row["rework_count"] or 0) >= effective_cap:
                _escalate(
                    conn, row,
                    f"rework cap reached ({row['rework_count'] or 0}/{effective_cap}) — the "
                    "PR still fails review. Branch, worktree and PR are preserved; every "
                    "verdict is on the run row (review_history).",
                )
                summary["escalated"] += 1

        # 1e. ⚖️ THE JUDGE'S SILENCE — a judge that stopped without publishing a verdict.
        #
        # With reconciliation, ABOVE the `blocked` and `hold` returns, for the same reason
        # 1d is: it takes no slot, starts nothing, and it is the transition that stops a run
        # from being stuck believing it is under review. ⛔ A judge that died is CANNOT
        # VERIFY — it does NOT block and it does NOT approve; the run stays exactly where it
        # was, and a human is told why.
        summary["judge_lost"] = _judge_watchdog(conn, wf, live_windows)

        # 1f. 📊 THE TRIAL LEDGER (CMX-105) — opt-in, and BEFORE the prune below: a died
        # or abandoned row must get its ledger line while it still exists in `runs`, or
        # a row pruned this very tick would never resolve. See `_write_trial_ledger`.
        summary["trial_ledger"] = _write_trial_ledger(wf, conn)

        # 2. Keep done rows for the "recent runs" view; just cap history per workflow.
        # `open_ids` guards a done-but-unstruck row from being pruned out from under
        # the claim-time out-of-band-merge check below (3c).
        _prune_done_rows(conn, str(wf.path), open_ids)
        conn.commit()

        # 2b. Fire after_done hook (post-commit so the row state is durable in
        # case the hook restarts this very process — e.g. an after_done that
        # redeploys the daemon itself). Detached via start_new_session=True so
        # the restart doesn't take down the current reconcile.
        if merged_in_tick:
            _fire_after_done(wf)

        # 3. Dispatch — BLOCKED while the workflow file does not parse.
        # Everything above (PR-state refresh, done/failed reconcile, the tracker
        # strike) has already run on the last known-good config, which is the
        # point: a YAML typo must not strand in-flight runs. Starting a NEW run
        # from a config the operator is visibly in the middle of breaking is the
        # part that would be wrong, so that is the only part we stop.
        if blocked:
            log.warning("Dispatch paused for %s — %s. Reconciliation continues on "
                        "the last known-good config.", wf.path, status.error)
            return summary

        # 3a. The queue HOLD — "claim nothing, I am rewriting the queue" (chela.hold).
        # Taken at this tick boundary and nowhere else: a hold must never land in the
        # middle of a _spawn, exactly as the hot-reloaded config is applied here and not
        # mid-dispatch. Everything above this line has already run — the hold pauses
        # CLAIMS, not reconciliation, because a hold that stopped merged PRs from freeing
        # their slots would jam the very slot the orchestrator is holding the queue to
        # fill.
        expired = hold.expire_if_stale()
        if expired:
            summary["hold_expired"] = True
            # Loud, unconditionally: somebody paused the queue and never came back, so
            # the queue we are about to claim from is probably not the one they meant.
            log.warning(
                "Dispatch hold EXPIRED and was released automatically — it was taken %s "
                "ago by %s%s. Dispatch RESUMES now; if the queue was never rewritten, the "
                "top item may not be the one that was intended.",
                hold.human_duration(expired.age()), expired.by or "?",
                f" ({expired.reason})" if expired.reason else "",
            )
        held = hold.active()
        if held:
            summary["held"] = True
            summary["hold"] = held.as_dict()
            # Not logged per tick here — a 60s drumbeat is how an operator learns to
            # ignore a log. The daemon loop edge-triggers it (cmd_run), `chela doctor`
            # and /api/settings show it live, and the startup capability line announces
            # it: a paused dispatcher is a disabled subsystem, and CMX-53's rule applies.
            return summary

        max_concurrent = wf.get("concurrency", "max", default=1) or 1
        # ⛔ ONLY claimed/running. `awaiting_review` does NOT hold a slot — that is why a
        # new task is claimed the moment a PR opens — and neither do `changes_requested`
        # (waiting for a slot) or `needs_human` (stopped, and it must never pin the queue
        # behind it). The consequence for the rework loop is the load-bearing one: a run
        # re-entering `running` RE-CONSUMES a slot, so it is capped like any other work.
        active = conn.execute(
            "SELECT COUNT(*) FROM runs WHERE status IN ({})".format(
                ",".join("?" * len(ACTIVE_STATUSES))
            ),
            ACTIVE_STATUSES,
        ).fetchone()[0]

        # 3a′. ⚖️ THE JUDGE — the adversarial pass CI cannot run.
        #
        # A PR reaches here GREEN and unreviewed. CI proved the suite passes; nothing has
        # proved the suite CAN FAIL, and on 2026-07-14 four of five CI-green PRs shipped a
        # guard that survived deliberate corruption. One judge is spawned per PR HEAD (the
        # `judge_sha` guard, exactly the once-per-sha guard the CI gate uses), it works in a
        # throwaway detached worktree, and its blocking verdicts go back through
        # `request_changes` — the one carrier — so a judge round IS a rework round and the
        # existing cap bounds the whole thing. See chela/judge.py.
        #
        # ⚖️ CMX-81: the ONE exception to once-per-sha. A `cannot_verify` is an UNKNOWN, not a
        # judgement — a flake, a gh timeout, a worktree that would not check out, a judge that
        # died. So the SAME head is re-judged while it last came back `cannot_verify` and under
        # `judge_max_unknown_retries` (`_spawn_judge` counts the retries in
        # `judge_cannot_verify_tries`). Without this a single transient failure retired the
        # commit from judgment for good and it merged UNJUDGED. `clean`/`blocked` are real
        # verdicts and never re-fire; a new sha resets the count and is judged afresh.
        #
        # Below the hold/blocked gates, deliberately: a judge is an AGENT on this box, and an
        # operator who paused the queue (or whose WORKFLOW.md does not parse) has not asked
        # for new agents. It resumes when they do — nothing is lost, the PR simply waits.
        if judge.judge_enabled(wf):
            judging = conn.execute(
                "SELECT COUNT(*) FROM runs WHERE workflow_path=? AND judge_state=?",
                (str(wf.path), judge.J_RUNNING),
            ).fetchone()[0]
            for row in conn.execute(
                "SELECT * FROM runs WHERE workflow_path=? AND status='awaiting_review' "
                "AND pr_state='open' AND pr_head_sha IS NOT NULL "
                "AND pr_checks IN ({}) AND ("
                "  judge_sha IS NULL OR judge_sha != pr_head_sha"
                "  OR (judge_state=? AND COALESCE(judge_cannot_verify_tries, 0) < ?)"
                ")"
                .format(",".join("?" * len(JUDGE_TRIGGER_CHECKS))),
                (str(wf.path), *JUDGE_TRIGGER_CHECKS,
                 judge.J_CANNOT_VERIFY, judge_max_unknown_retries()),
            ).fetchall():
                if judging >= JUDGE_MAX_CONCURRENT:
                    break        # it waits a tick; each judge re-runs a whole test suite
                if _spawn_judge(wf, row, row["pr_head_sha"], conn):
                    judging += 1
                    summary["judged"] += 1

        # 3b. REWORK — the runs the reviewer sent back (chela review --request-changes).
        #
        # Before the fresh claims, deliberately: a run that already has a branch, a
        # worktree, an open PR and a verdict against it is further along than anything in
        # the queue, and finishing work beats starting more of it. It is not a privilege,
        # though — a rework takes a slot on exactly the same terms as a fresh dispatch:
        #
        #   ⛔ it NEVER exceeds concurrency.max, and it NEVER preempts a claimed/running
        #      run (chela.hold: preemption is deliberately NO, and that stands). With every
        #      slot busy the rework simply waits its turn, like everything else.
        #
        # The over-cap runs are already gone by now — step 1d escalated them with the rest of
        # reconciliation, because escalation is not a claim and must not wait on a hold, on a
        # free slot, or on a WORKFLOW.md that parses. Everything still here has budget left.
        for row in conn.execute(
            "SELECT * FROM runs WHERE workflow_path=? AND status='changes_requested' "
            "ORDER BY COALESCE(started_at, '')",
            (str(wf.path),),
        ).fetchall():
            if active >= max_concurrent:
                continue                     # waits its turn — it does not jump the queue
            try:
                if _respawn_rework(wf, row, conn):
                    active += 1
                    summary["reworked"] += 1
            except Exception as e:
                # ⛔ NOT `failed`. See _rework_failed: `failed` is the FRESH-dispatch retry
                # state, and a rework that fell into it would be re-claimed as a new task,
                # losing the verdict and bypassing the cap. It goes back where it came from,
                # one round poorer.
                log.exception("Rework re-spawn failed for task %s", row["task_id"])
                _rework_failed(conn, row, f"rework re-spawn failed: {e}")

        # 3b′. 🧹💽 THE DISK-BUDGET RAIL (CMX-164) — the `memcap` analog for disk. A fresh
        # claim forks a BRAND-NEW worktree (`ensure_worktree`), and `~/.chela/worktrees/` is
        # the largest per-agent resource chela consumes — the one that scales with agent
        # count. An adopter with a heavier repo (a Rust `target/`, an ML venv, a Node
        # monorepo: 1-10 GB *per worktree*) can otherwise run the box out of disk, which is
        # worse than an OOM: it takes git, sqlite, tmux and the daemon down together, and
        # there is no rail for it today. Only measured when a slot is actually free —
        # walking the tree to learn there is nothing to claim anyway would be the same
        # wasted work the fetch skip below avoids. Rework/judge worktrees are NOT gated
        # here: both attach to or reuse a worktree already on disk, never fork a fresh one.
        over_budget = False
        budget = worktree_disk_budget_bytes()
        if budget and active < max_concurrent:
            used = disk_usage_bytes(resolve_workspace_root(wf))
            if used > budget:
                over_budget = True
                summary["disk_budget_exceeded"] = True
                log.warning(
                    "Dispatch REFUSED for %s — the worktree root is %s, over the %s "
                    "CHELA_WORKTREE_DISK_BUDGET. No new task will be claimed until disk is "
                    "freed (a stuck/orphaned worktree, or space made elsewhere).",
                    wf.path, human_size(used), human_size(budget),
                )

        # 3c. FETCH-THEN-CLAIM: re-read the queue from origin/<base_branch> right now,
        # rather than trusting the parse made at the top of this tick (before the PR
        # polling and the tracker strike, both of which touch the network). Necessary,
        # NOT sufficient — see _claim_order. Skipped entirely when every slot is busy, OR
        # when the disk budget above refused the claim: there is nothing to claim, and a
        # network fetch to learn that is a waste either way.
        queue = _claim_order(wf, source, open_tasks) if (active < max_concurrent and not over_budget) else []

        for task in queue:
            if active >= max_concurrent:
                break
            existing = conn.execute(
                "SELECT status, attempt, pr_state FROM runs WHERE task_id=?", (task.id,)
            ).fetchone()
            if existing:
                # Fresh tasks only: anything already in flight (claimed/running), parked in
                # review (awaiting_review / changes_requested / needs_human — each of which
                # already owns a branch, a worktree and a PR), or already shipped (done) is
                # excluded. ⛔ A `changes_requested` row claimed here as a fresh task would
                # fork a NEW worktree off the base branch and abandon the PR under review.
                # Only `failed` rows are eligible for a retry, and only until MAX_ATTEMPTS.
                if existing["status"] in NOT_CLAIMABLE:
                    continue
                # Belt-and-suspenders out-of-band-merge guard: whatever status this row
                # reads THIS tick, a `pr_state='merged'` row is never re-dispatched — the
                # reconcile pass above (1.) should already have flipped it to `done` before
                # we ever got here, but a surviving stale status (a race, a schema this
                # tick's reconcile didn't cover) must not fall through to a duplicate spawn.
                if existing["pr_state"] == "merged":
                    continue
                if existing["status"] == "failed" and existing["attempt"] >= MAX_ATTEMPTS:
                    continue
                attempt = existing["attempt"] + 1
            else:
                attempt = 1

            try:
                spawned = _spawn(wf, task, attempt, conn)
            except Exception as e:
                log.exception("Dispatch failed for task %s", task.id)
                conn.execute(
                    """INSERT INTO runs (task_id, workflow_path, title, status, attempt, last_error, started_at, brief)
                       VALUES (?, ?, ?, 'failed', ?, ?, ?, ?)
                       ON CONFLICT(task_id) DO UPDATE SET
                         status='failed', attempt=excluded.attempt, last_error=excluded.last_error,
                         brief=excluded.brief""",
                    (task.id, str(wf.path), task.title, attempt, str(e), _now(), _task_brief(task)),
                )
                conn.commit()
                continue
            if spawned:
                active += 1
                summary["dispatched"] += 1

    return summary


def _max_existing_task_number(repo_path: Path, project_key: str) -> int:
    """Highest N already spoken for by a `{project_key.lower()}-<n>` branch — local
    or on `origin` — regardless of what the `runs` table remembers.

    `runs.task_number` alone is not the whole story: `_prune_done_rows` and
    `delete_run` drop rows while leaving the branch (local and/or remote) behind, and a
    dispatch that only looked at `MAX(task_number) FROM runs` would happily hand a fresh
    task_id that same, still-occupied number. Two real incidents from reusing it: two
    open PRs both titled the same `CMX-<n>` (the row for the first was gone by the time
    the second was minted), and a `git push -u origin {branch}` rejected non-fast-forward
    because a same-named remote branch from an out-of-band-merged PR was still there
    (`contract._squash_merge` deletes the remote branch on ITS merges, but a PR merged by
    hand via `gh` bypasses that). Scanning branches closes both: a slot with no live
    `runs` row but a surviving branch is never handed out again.
    """
    prefix = f"{project_key.lower()}-"
    out = subprocess.run(
        ["git", "-C", str(repo_path), "for-each-ref",
         "--format=%(refname:short)", "refs/heads/", "refs/remotes/origin/"],
        capture_output=True, text=True, check=False,
    )
    if out.returncode != 0:
        return 0
    best = 0
    for line in out.stdout.splitlines():
        name = line.strip()
        if name.startswith("origin/"):
            name = name[len("origin/"):]
        if not name.startswith(prefix):
            continue
        suffix = name[len(prefix):]
        if suffix.isdigit():
            best = max(best, int(suffix))
    return best


def _task_brief(task: Task) -> str | None:
    """What to persist onto `runs.brief` at claim time — the task-detail modal's
    left pane. `task.body` (the markdown source's full title + dedented
    OBJECTIVE/BOUNDARIES/GUARDS/VERIFY continuation) wins when the source
    captured one; a bare one-line task, or a source with no notion of a
    continuation (gh_issues), falls back to `task.raw` (the bullet line / issue
    URL), and — belt-and-suspenders, should raw itself ever be empty — `task.title`.
    """
    return task.body or task.raw or task.title


def _spawn(wf: WorkflowDef, task: Task, attempt: int, conn: sqlite3.Connection) -> bool:
    repo_path = wf.path.parent
    base_branch = wf.get("workspace", "base_branch", default="master")
    project_key = wf.project_key
    if wf.get("workspace", "branch_prefix") is not None:
        log.warning(
            "%s: workspace.branch_prefix is obsolete and ignored; "
            "branches now use {project_key.lower()}-{task_number}",
            wf.path,
        )
    if wf.get("agent", "window_name_prefix") is not None:
        log.warning(
            "%s: agent.window_name_prefix is obsolete and ignored; "
            "tmux windows now use {project_key.lower()}-{task_number}",
            wf.path,
        )
    root = resolve_workspace_root(wf)

    # Reuse an existing task_number when retrying (failed → re-dispatch); otherwise
    # mint a fresh one scoped per workflow_path. task_id is the stable identity;
    # task_number is an additive display layer.
    existing = conn.execute(
        "SELECT task_number FROM runs WHERE task_id=?", (task.id,)
    ).fetchone()
    if existing and existing["task_number"] is not None:
        task_number = existing["task_number"]
    else:
        row = conn.execute(
            "SELECT COALESCE(MAX(task_number), 0) + 1 AS n FROM runs WHERE workflow_path=?",
            (str(wf.path),),
        ).fetchone()
        task_number = max(int(row["n"]), _max_existing_task_number(repo_path, project_key) + 1)

    worktree, created = ensure_worktree(repo_path, task.id, base_branch, project_key, task_number, root)
    branch = f"{project_key.lower()}-{task_number}"
    window_name = branch
    hook_vars = _prompt_vars(wf, task, str(worktree), branch, base_branch, task_number)

    # Claim the row before touching tmux so failure leaves a trace. A retry re-enters
    # here with the same task_id and gets a NEW window, so window_id is cleared on
    # conflict: leaving attempt 1's id would point the next run_review at a corpse
    # (or, worse, at whatever window tmux later recycled that id onto).
    conn.execute(
        """INSERT INTO runs (task_id, workflow_path, title, status, window_name, worktree_path, branch_name, started_at, attempt, task_number, brief)
           VALUES (?, ?, ?, 'claimed', ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(task_id) DO UPDATE SET
             status='claimed', window_name=excluded.window_name,
             worktree_path=excluded.worktree_path, branch_name=excluded.branch_name,
             started_at=excluded.started_at, attempt=excluded.attempt, last_error=NULL,
             task_number=excluded.task_number, idle_nudged_at=NULL, window_id=NULL,
             window_epoch=NULL, brief=excluded.brief""",
        (task.id, str(wf.path), task.title, window_name, str(worktree), branch, _now(), attempt, task_number, _task_brief(task)),
    )
    conn.commit()

    prompt = render_prompt(wf.prompt_template, hook_vars)
    _launch_agent(
        wf, task.id, window_name, worktree, prompt, conn,
        hook_vars=hook_vars, fresh_worktree=created,
    )

    conn.execute(
        "UPDATE runs SET status='running' WHERE task_id=?", (task.id,)
    )
    conn.commit()
    log.info("Dispatched task %s → %s (attempt %d)", task.id, window_name, attempt)

    # 🧑‍⚖️ THE CRITIC (CMX-88) — advisory brief-review, AFTER the dispatch is already done.
    # ⛔ Deliberately the last thing _spawn touches and swallowing every failure: the agent is
    # launched, the row is 'running', the dispatch has HAPPENED — so nothing this call does or
    # fails to do can block, delay, or change it. See _run_critic.
    _run_critic(wf, task, conn)
    return True


# The run statuses that mean "still in flight" for the critic's coupling check — a run that
# owns a worktree and may be editing its target files right now. ``claimed``/``running`` are
# the brief's "dispatched"/"running"; ``awaiting_review``/``changes_requested`` still hold the
# worktree (the PR is open, a rework may re-spawn). ``done``/``failed``/``needs_human`` are
# terminal — their files are no longer contested — so they are excluded.
_CRITIC_INFLIGHT_STATUSES = ("claimed", "running", "awaiting_review", "changes_requested")


def _run_critic(wf: WorkflowDef, task: Task, conn: sqlite3.Connection) -> None:
    """🧑‍⚖️ Advisory brief-review at dispatch (persona-pattern step 3, ``chela.critic``).

    ⛔ ADVISORY-ONLY, and it is ENFORCED here, not promised. This runs *after* the agent is
    launched and the run row is already ``running``; every failure is SWALLOWED; and nothing
    in the dispatch path ever reads ``critic_notes`` back. So a wrong opinion — or an outright
    crash — costs at most a missing note, never a dispatch. This is the critic's version of the
    judge's founding property ("the reviewing agent decides NOTHING"): a wrong critic cannot do
    the one thing v1 forbids, because the code never gives its output a way to.

    It reviews the **task-specific brief** — the TODO item the human actually wrote
    (``task.title`` / ``task.body`` or ``task.raw``), NOT the rendered WORKFLOW.md prompt.
    The template is boilerplate identical on every dispatch and already carries every
    field-signal, so reviewing it would report "complete" for every task and the critic
    would never say anything. The text that varies per task is the only text worth
    reviewing. ``task.body`` (chela.sources.markdown._task_body — title + the bullet's
    dedented OBJECTIVE/BOUNDARIES/GUARDS/VERIFY continuation, when the source captured one)
    is what the human actually wrote past the title; using only ``task.raw`` (the bare
    bullet line) starved the four-field detector of everything after the first line, so it
    fired "no explicit objective/boundaries/verify" on briefs that named all three further
    down. Falls back to ``task.raw`` for a bare one-line task or a source with no notion of
    a continuation (gh_issues) — same fallback ``_task_brief`` uses for ``runs.brief``.

    Writes ``critic_notes`` ("" ⇒ ran, nothing to add) and ``critic_reviewed_at`` when the
    critic is on; a disabled critic writes NOTHING, leaving both NULL — "the critic never ran",
    which is a different fact from "ran and clean".
    """
    try:
        if not critic.critic_enabled(wf):
            return
        brief_text = f"{task.title}\n{task.body or task.raw}"
        review = critic.review_brief(brief_text)
        files = critic.target_files(brief_text)
        inflight = _inflight_target_files(conn, task.id)
        note = critic.compose_advisory(review, files, inflight)
        conn.execute(
            "UPDATE runs SET critic_notes=?, critic_reviewed_at=? WHERE task_id=?",
            (note, _now(), task.id),
        )
        conn.commit()
        if review.missing:
            log.info("critic: %s brief names no explicit %s (advisory only — dispatch "
                     "unaffected)", task.id, ", ".join(review.missing))
    except Exception:
        # ⛔ The whole point. A broken critic must never surface as a broken dispatch.
        log.warning("critic: advisory brief-review failed for %s — dispatch is unaffected",
                    task.id, exc_info=True)


def _inflight_target_files(
    conn: sqlite3.Connection, exclude_task_id: str
) -> list[tuple[str, frozenset[str]]]:
    """The target files of every run still in flight, keyed by short run id.

    Excludes ``exclude_task_id`` — the run being dispatched right now, whose own row is already
    ``running`` and must not be reported as colliding with itself. Each run's target files come
    from its stored ``title`` (the TODO line), the same task-specific text the current brief is
    parsed from, so the two sides couple on the same basis.
    """
    placeholders = ",".join("?" for _ in _CRITIC_INFLIGHT_STATUSES)
    rows = conn.execute(
        f"SELECT task_id, title FROM runs WHERE status IN ({placeholders}) AND task_id != ?",
        (*_CRITIC_INFLIGHT_STATUSES, exclude_task_id),
    ).fetchall()
    out: list[tuple[str, frozenset[str]]] = []
    for row in rows:
        files = critic.target_files(row["title"] or "")
        if files:
            out.append((str(row["task_id"]), files))
    return out


def _launch_agent(
    wf: WorkflowDef,
    task_id: str,
    window_name: str,
    worktree: Path | str,
    prompt: str,
    conn: sqlite3.Connection,
    *,
    hook_vars: dict,
    fresh_worktree: bool,
    judge_window: bool = False,
    role: str = "coding",
) -> str:
    """PREPARE the worktree, then put an agent in it. THE spawn path — the only one.

    Shared by the first dispatch (:func:`_spawn`) and the rework re-spawn
    (:func:`_respawn_rework`) precisely so there is no second one. It has TWO halves and
    the environment half is the one a refactor forgets — CMX-68's first cut extracted the
    tmux half alone and shipped a rework path that ran NO hooks:

    ENVIRONMENT (this must never move back out — a worktree an agent cannot work in is
    worse than no agent, because the agent will believe what it sees there):

    * ``after_create`` — fires only when the worktree is FRESH (``fresh_worktree``), which
      on the rework path means ``attach_worktree`` had to RE-CREATE a directory that was
      cleaned up. That directory is as bare as a first-dispatch one: no
      ``.claude/settings.local.json``, so the agent hangs on its first permission prompt.
      A non-zero exit is a HARD ABORT for exactly that reason.
    * ``before_run`` — fires on EVERY launch, fresh worktree or not, because it is the hook
      that makes the venv real (``uv sync --all-extras``, the CMX-21 trap). ⛔ Skipping it
      on a rework hands the agent phantom test failures — and the rework prompt orders it
      to re-run the CI gates and believe them.
    * :data:`AGENT_ENV_STRIP` (CMX-115) — sent via ``unset`` into the window's own shell
      before anything else, so this agent (and the judge, which calls this same function)
      never holds ``CHELA_NOTIFY_URL``. Both run notify.py-touching code (inbox repros, the
      judge's full pytest pass) that would otherwise push a REAL ntfy to the human's phone.

    TMUX (every rule below was paid for once already; a hand-rolled copy relearns them):

    * the TWO-STEP window pattern — ``new-window`` and THEN ``send-keys 'claude …'``.
      ⛔ Never ``tmux new-window '<cmd>'``: claude then *is* the pane process,
      ``agent_manager.claude_pid()`` (``pgrep -P``) never correlates it, and the agent
      never gets a Telegram topic.
    * kill any pre-existing same-name window first (tmux allows duplicates, and a stacked
      second window makes the by-name target ambiguous — the orphan-window bug), then
      target THIS window by the captured ``@id`` for everything that follows.
    * record the ``@id`` on the run row NOW, the one lossless moment (CMX-62): the agent
      kills its own window on ``chela task-finished``, so a later live-tmux lookup always
      misses and the run's events land ownerless.
    * confirm the paste actually landed (:func:`_send_seed`) rather than trusting the
      readiness glyph — a late splash redraw still swallows prompts.

    ``hook_vars`` is the same ``{{...}}`` map the prompt is rendered from, so a hook can
    target the worktree it is preparing (``{{workspace_path}}``).

    ``judge_window=True`` for an agent that is working ON a run rather than AS it (the
    judge): stamps ``judge_window_id``/``judge_window_epoch`` instead of ``runs.window_id`` —
    the run's OWN window column, which the Feed keys its lane on and ``run_review`` addresses,
    and must stay the RUN's window, never a short-lived judge's. ⛔ It is stamped at the SAME
    lossless moment as the run's own id (right after ``_new_window()``, below) — CMX-136:
    stamping a judge's id only after this whole function returns (past the ready-wait and
    ``_send_seed``, several seconds later) left its tmux window live but invisible to
    ``dispatched_window_ids`` for that whole gap, so the Wall drew it full-size like a human's
    window instead of docking it minimized from the first poll.

    ``role`` (``"coding"`` | ``"judge"``) picks the MODEL the launch command runs on (see
    :func:`resolve_agent_cmd`). ⛔ The judge passes ``role="judge"`` so its command stays on
    the capable :data:`DEFAULT_JUDGE_MODEL` and never inherits the fleet's coding-model
    Settings choice — a `sonnet` default must not downgrade the adversarial pass.

    Returns the window target (the ``@id``, or the bare name if the id was unreadable).
    """
    if fresh_worktree:
        after_create = wf.get("hooks", "after_create")
        if after_create:
            log.info("Running after_create hook for %s", task_id)
            subprocess.run(
                render_prompt(after_create, hook_vars),
                shell=True, cwd=worktree, check=True,
            )

    before = wf.get("hooks", "before_run")
    if before:
        log.info("Running before_run hook for %s", task_id)
        subprocess.run(before, shell=True, cwd=worktree, check=True)

    _kill_windows_named(window_name)
    target_id = _new_window(window_name, str(worktree))

    # Only a real @id is stored — _new_window degrades to the bare name when the id can't
    # be parsed, and a name in this column would be a lie the Feed keys a lane on. The id is
    # stamped with the tmux epoch that ISSUED it (CMX-77): this is the same lossless moment,
    # and the id is worthless to a later reader without it. CMX-136: the judge gets that SAME
    # lossless moment into ITS OWN columns — stamping it any later (e.g. after the ready-wait
    # and _send_seed below) reopens the stamp race this branch exists to close.
    if re.fullmatch(r"@\d+", target_id):
        if judge_window:
            conn.execute(
                "UPDATE runs SET judge_window_id=?, judge_window_epoch=? WHERE task_id=?",
                (target_id, epoch.current(), task_id))
        else:
            conn.execute(
                "UPDATE runs SET window_id=?, window_epoch=? WHERE task_id=?",
                (target_id, epoch.current(), task_id))
        conn.commit()

    # WORKFLOW.md's agent.cmd → the Settings permission mode → the built-in default (see
    # resolve_agent_cmd). The mode AND model are fixed at spawn: changing either in Settings
    # affects the NEXT dispatch, never an agent already running. ``role`` keeps the judge on
    # its capable model regardless of the coding-agent Settings choice.
    agent_cmd, cmd_source = resolve_agent_cmd(wf, role)
    # CMX-223: a chela-owned, deterministic peer-messaging socket path — lets
    # messenger.send_peer address THIS window without guessing it from our own
    # env. Appended (never baked into resolve_agent_cmd/agent.cmd — that command
    # may not even be `claude`) and only once the real @id is known; None (path
    # would overflow the sun_path ceiling) just launches without the flag.
    if re.fullmatch(r"@\d+", target_id):
        socket_arg = messaging_socket_launch_arg(target_id)
        if socket_arg:
            agent_cmd = f"{agent_cmd} {socket_arg}"
    log.info("Launching %s with %r (source: %s)", task_id, agent_cmd, cmd_source)
    # CMX-115: strip daemon-only secrets from THIS window's shell before anything else
    # runs in it — must land before the CHELA_WID export and the agent command below,
    # so nothing launched in this pane (the agent itself, a repro, `chela` subcommands)
    # ever sees them.
    subprocess.run(
        ["tmux", "send-keys", "-t", f"{TMUX_SESSION}:{target_id}",
         _strip_agent_env_cmd(), "Enter"],
        check=True, capture_output=True,
    )
    # Export CHELA_WID first so the worktree agent knows its own window id (self-identity
    # for peek/read/drive), then launch.
    if re.fullmatch(r"@\d+", target_id):
        subprocess.run(
            ["tmux", "send-keys", "-t", f"{TMUX_SESSION}:{target_id}",
             f"export CHELA_WID={target_id}", "Enter"],
            check=True, capture_output=True,
        )
    subprocess.run(
        ["tmux", "send-keys", "-t", f"{TMUX_SESSION}:{target_id}", agent_cmd, "Enter"],
        check=True, capture_output=True,
    )

    min_wait = int(wf.get("agent", "startup_delay_seconds", default=4) or 4)
    ready_timeout = int(
        wf.get("agent", "ready_timeout_seconds", default=READY_TIMEOUT_SECONDS)
        or READY_TIMEOUT_SECONDS
    )
    if not _wait_for_ready(target_id, min_wait, ready_timeout):
        log.warning(
            "Task %s: window %s (%s) not ready after %ds; sending prompt anyway",
            task_id, window_name, target_id, ready_timeout,
        )

    if not _send_seed(target_id, prompt, task_id):
        raise RuntimeError(f"failed to send prompt to {window_name} ({target_id})")
    return target_id


# --- the rework re-spawn -----------------------------------------------------

# The prompt a reworking agent wakes up to. Generic on purpose — it says nothing about
# any one project's test commands — and overridable per workflow with `agent.rework_prompt`
# in WORKFLOW.md, which sees the same {{...}} vars.
#
# It does two things the first-dispatch prompt cannot: it tells the agent it is BACK in
# its own worktree on its own branch with its PR already open (so it pushes instead of
# forking anything), and it hands it the verdict — while pointing it at the PR comments as
# the durable record, because the comment thread is what a human will have added to.
REWORK_PROMPT = """\
🔁 **REWORK — your PR failed review.** This is round {{rework_round}} of {{max_reworks}}.

You are back in your ORIGINAL worktree (`{{workspace_path}}`) on your ORIGINAL branch
(`{{branch_name}}`), and your PR is ALREADY OPEN: {{pr_url}}

⛔ Do NOT open a second PR and do NOT branch again — push to `{{branch_name}}` and the
existing PR updates itself.

## The verdict

{{verdict}}

## Do this, in order

1. **Read the PR thread yourself** — `{{pr_comments_cmd}}`. The comment is the durable
   record, and a human may have added to it since the verdict above was written.
2. Fix every defect it names, in this worktree.
3. Re-run the SAME validation your original task told you to run (this repo's CI gates are
   not optional).
4. Stage only what you changed (`git add <paths>` — never `git add -A`), commit, and
   `git push`.
5. Run `chela task-finished {{task_id}}` as your last step — it puts the run back in
   `awaiting_review` and wakes the reviewer.

**Do NOT touch the tracker file.** If the verdict is wrong, or you cannot fix it, say so
plainly in your final message and stop — do not push a half-fix. There are only
{{max_reworks}} rounds; after that the run escalates to a human.
"""


def _rework_vars(
    wf: WorkflowDef, row: sqlite3.Row, worktree: Path | str, verdict: str, rework_round: int
) -> dict:
    """The ``{{...}}`` map a rework renders from — the prompt AND the worktree hooks.

    Deliberately a superset of :func:`_prompt_vars`' keys where they overlap
    (``workspace_path``, ``branch_name``, ``repo_path``, …): the same ``after_create`` /
    ``before_run`` command has to render in BOTH paths, and it can only do that if it sees
    the same names. ``base_branch`` is here for the same reason and nothing else — a rework
    never forks from it.
    """
    number = _pr_number(row["pr_url"])
    return {
        "task_id": row["task_id"],
        "task_title": row["title"] or "",
        "branch_name": row["branch_name"] or "",
        "workspace_path": str(worktree),
        "base_branch": wf.get("workspace", "base_branch", default="master"),
        "repo_path": str(wf.path.parent),
        "project_key": wf.project_key,
        "task_number": row["task_number"],
        "pr_url": row["pr_url"] or "(no PR link on the run row)",
        "pr_comments_cmd": (
            f"gh pr view {number} --comments" if number
            else "gh pr view --comments   # this run has no PR url recorded"
        ),
        "verdict": verdict,
        "rework_round": rework_round,
        "max_reworks": max_reworks(),
    }


def _escalate(conn: sqlite3.Connection, row: sqlite3.Row, reason: str) -> None:
    """Stop the loop and hand the run to a human — keeping EVERYTHING.

    The branch, the worktree and the PR all stay exactly where they are: a run that
    defeated the loop is the one case where throwing work away is least forgivable. The
    concurrency slot is freed simply by not being in :data:`ACTIVE_STATUSES` — a stuck run
    must never pin the queue behind it.

    The orchestrator finds out through the decisions inbox, which is edge-triggered on the
    runs DB (``inbox.run_events``) and carries the whole review history in the payload —
    every verdict, not just the last. That is also why nothing is pushed from here: the
    row IS the notification, and a second publisher would be a second source of truth.
    """
    conn.execute(
        "UPDATE runs SET status='needs_human', ended_at=?, last_error=? WHERE task_id=?",
        (_now(), reason, row["task_id"]),
    )
    conn.commit()
    log.warning(
        "Task %s → needs_human: %s (branch %s and worktree %s preserved; slot freed)",
        row["task_id"], reason, row["branch_name"], row["worktree_path"],
    )


def _renudge_prompt(wf: WorkflowDef, row: sqlite3.Row, task: Task | None) -> str | None:
    """The prompt the watchdog re-sends to a run stuck at an empty prompt — ITS OWN.

    A rework gets the rework prompt (rebuilt from the row: the same verdict, the same round
    it is already spending), never the first-dispatch one. None when there is nothing to
    re-send: a first dispatch whose task has left the tracker.
    """
    if _is_rework(row):
        return render_prompt(
            wf.get("agent", "rework_prompt", default=None) or REWORK_PROMPT,
            _rework_vars(
                wf, row, row["worktree_path"] or "",
                latest_verdict(dict(row)), row["rework_count"] or 0,
            ),
        )
    if task is None:
        return None
    return render_prompt(
        wf.prompt_template,
        _prompt_vars(
            wf, task, row["worktree_path"], row["branch_name"],
            wf.get("workspace", "base_branch", default="master"), row["task_number"],
        ),
    )


def _rework_failed(conn: sqlite3.Connection, row: sqlite3.Row, error: str) -> None:
    """A rework that DIED goes back to ``changes_requested`` — never to ``failed``.

    ⛔ ``failed`` is not in :data:`NOT_CLAIMABLE`, deliberately: it is the retry state for a
    FRESH dispatch. Dropping a rework into it hands the run to the claim loop, which knows
    nothing about reworks — it would call :func:`_spawn` with the ORIGINAL first-dispatch
    prompt (telling an agent to branch and open a PR that is already open), bump ``attempt``,
    and never look at ``rework_count``. The verdict would be silently lost and the cap
    silently bypassed. So a dead rework re-enters the loop it was already in.

    The round is SPENT even though the agent never worked, which is what bounds this: a
    rework that cannot be launched at all (no tmux, a hook that always fails) retries once
    per tick, burns its rounds, and escalates to ``needs_human`` — where a human sees it.
    Refunding the round would spin forever instead.
    """
    task_id = row["task_id"]
    # What round did we just lose? The snapshot's OWN status answers it, and the two answers
    # differ by one:
    #   `running`           — _respawn_rework already wrote rework_count = the round in
    #                         flight. It is spent. Charging another would let a single dead
    #                         tmux window burn the entire budget.
    #   `changes_requested` — it never got out of the gate, and the attempt STILL costs a
    #                         round: a launch that always fails (no tmux, a hook that always
    #                         exits non-zero) would otherwise retry every tick forever. This
    #                         is what makes the failure path terminate — in `needs_human`,
    #                         where a person sees it.
    spent = (row["rework_count"] or 0)
    if row["status"] != "running":
        spent += 1
    conn.execute(
        "UPDATE runs SET status='changes_requested', rework_count=?, last_error=?, "
        "window_id=NULL, window_epoch=NULL WHERE task_id=?",
        (spent, error, task_id),
    )
    conn.commit()
    log.warning(
        "Task %s: rework round %d FAILED (%s) — back to changes_requested, verdict intact "
        "(cap %d)", task_id, spent, error, max_reworks(),
    )


def _is_rework(row: sqlite3.Row) -> bool:
    """Is this ``running`` row a rework in flight (rather than a first dispatch)?

    ``rework_count`` is only ever spent by :func:`_respawn_rework`, so a running row that
    has one is an agent working on a verdict. It is the flag that keeps a dead rework out
    of the fresh-dispatch retry path (:func:`_rework_failed`).
    """
    return (row["rework_count"] or 0) > 0


def _respawn_rework(wf: WorkflowDef, row: sqlite3.Row, conn: sqlite3.Connection) -> bool:
    """Re-spawn a ``changes_requested`` run IN ITS OWN WORKTREE, ON ITS OWN BRANCH.

    The branch history, the open PR and the agent's own work are all preserved — which is
    exactly what the human did by hand on 2026-07-14, and it worked. ⛔ It must never fork
    a fresh worktree from the base branch: that would abandon the commits the PR points at.

    Worktree gone (cleaned up) → re-attached from the branch. BRANCH gone → there is
    nothing to rework and :func:`_escalate` hands it to a human. The caller has already
    checked the concurrency slot and the rework cap.
    """
    task_id = row["task_id"]
    repo_path = wf.path.parent
    branch = row["branch_name"]
    if not branch:
        _escalate(conn, row, "rework: the run row has no branch — nothing to re-enter")
        return False

    root = resolve_workspace_root(wf)
    want = Path(row["worktree_path"]) if row["worktree_path"] else (root / task_id)
    try:
        worktree, attached = attach_worktree(repo_path, branch, want)
    except BranchGone as e:
        _escalate(conn, row, f"rework: {e} — the work it points at is unreachable")
        return False
    except subprocess.CalledProcessError as e:
        stderr = (e.stderr or b"").decode() if isinstance(e.stderr, bytes) else (e.stderr or "")
        _escalate(conn, row, f"rework: could not attach a worktree for {branch}: {stderr.strip()}")
        return False
    if attached:
        log.info("Task %s: worktree was gone; re-attached %s from branch %s",
                 task_id, worktree, branch)

    rework_round = (row["rework_count"] or 0) + 1
    verdict = latest_verdict(dict(row))
    hook_vars = _rework_vars(wf, row, worktree, verdict, rework_round)
    prompt = render_prompt(
        wf.get("agent", "rework_prompt", default=None) or REWORK_PROMPT, hook_vars
    )
    window_name = branch

    # Spend the round and take the slot BEFORE touching tmux, exactly as _spawn claims its
    # row first: a crash mid-launch must leave a trace, and must not be free to retry
    # forever. A launch that then fails reconciles like any other running run.
    conn.execute(
        "UPDATE runs SET status='running', rework_count=?, started_at=?, ended_at=NULL, "
        "last_error=NULL, idle_nudged_at=NULL, window_id=NULL, window_epoch=NULL, "
        "worktree_path=? "
        "WHERE task_id=?",
        (rework_round, _now(), str(worktree), task_id),
    )
    conn.commit()

    # ⛔ fresh_worktree=attached: a RE-CREATED worktree is a fresh one. It has the branch's
    # tracked files and nothing else — no .claude/settings.local.json, no venv — so it needs
    # after_create exactly like a first dispatch. (before_run fires either way.)
    _launch_agent(
        wf, task_id, window_name, worktree, prompt, conn,
        hook_vars=hook_vars, fresh_worktree=attached,
    )
    log.info(
        "Task %s: rework round %d/%d spawned in %s on %s (PR %s)",
        task_id, rework_round, max_reworks(), worktree, branch, row["pr_url"] or "?",
    )
    return True


# --- ⚖️ the judge: spawn, and the watchdog that makes its silence mean something ---------

# What the judge agent is told. ⛔ Read it as the CONTRACT it is: this agent DECIDES NOTHING.
# It proposes experiments; `chela judge run` applies them, proves they applied, proves the
# code still parses, runs the repo's OWN suite and adjudicates. That is the whole reason an
# autonomous judge is safe to put in a blocking position — see chela/judge.py's module
# docstring, which is the design and not a summary of it.
#
# Overridable per workflow with `agent.judge_prompt` in WORKFLOW.md (same {{...}} vars).
JUDGE_PROMPT = """\
⚖️ **You are the JUDGE for PR {{pr_url}}** (branch `{{branch_name}}`, run `{{task_id}}`).
Its CI is green and it is waiting for review.

⛔ **YOU DECIDE NOTHING, AND YOU REPORT NO RESULTS.** You propose EXPERIMENTS. `chela` runs
them itself — it applies each mutation, reads the file back to prove it changed, checks it
still parses, runs the suite, restores the file, and writes the verdict. Your opinion never
reaches the blocking path; only a suite chela ran does. **Do not run the test suite yourself
and do not report whether something passed** — your results are not evidence.

⛔ **Do not commit, push, or edit the PR's branch.** `{{workspace_path}}` is a THROWAWAY
detached checkout that is deleted when you finish.

## What you are looking for — and it is NOT "is the code good?"

Every feature in the last five PRs worked. Four were still sent back, because **the thing
meant to PROVE the feature works could not fail**: a guard that stayed green with the
guarded state folded back in, a colourblind cue whose glyph could be emptied with 0
failures, a whole production wiring that could be REVERTED with 1112 passed.

**A guard that survives deliberate corruption is not a guard.** That is what you hunt.

## Do this, in order

1. Read what this PR claims: `{{diff_cmd}}` and `{{pr_view_cmd}}`.
2. For **each guard/invariant it adds** (each new or changed test, each "must never…"
   comment, each accessibility cue), design ONE **minimal, live, syntactically valid**
   mutation to the **production** code that a real guard MUST catch:
   - ✅ `if (false && <cond>)`, invert a comparison, empty a returned value, blank a string.
   - ⛔ **Never delete a line** that would unbalance braces or indentation. A mutation that
     breaks the parse makes the suite red for the WRONG reason, proves nothing, and chela
     will throw it out as INVALID.
   - **WIRING** (`"kind": "wiring"`): the smallest edit that makes the feature NEVER RUN —
     revert the production call-site. If the suite is still green, the tests never exercise
     what actually runs.
3. Write `{{experiments_path}}` — JSON, exactly this shape:

```json
{
  "experiments": [
    {"guard": "sidebar state must never reach _termSig",
     "kind": "mutation",
     "file": "chela/dashboard/static/terminals.js",
     "before": "<the EXACT text to replace, copied verbatim, occurring EXACTLY ONCE>",
     "after":  "<what to replace it with>"}
  ],
  "notes": [
    {"title": "style/design opinion", "body": "posted as a comment; blocks nothing"}
  ]
}
```

   - `before` is matched **literally** and must occur **exactly once** in the file — chela
     REFUSES an ambiguous anchor and refuses one it cannot find, because a mutation that
     never applied leaves the suite green and would send a GOOD PR back.
   - **`notes` are for everything that is a judgment** — style, taste, "I'd have done it
     differently". They are posted as a comment and can never send a PR back. ⛔ Do not
     smuggle an opinion into an experiment: **you are allowed to be useless. You are not
     allowed to be wrong.**

4. Run **`{{judge_cmd}}`** — your last step. It publishes the verdict, cleans up, and closes
   this window.

If you genuinely cannot find a guard to corrupt, say so in `notes` and still run the
command with `"experiments": []` — that is recorded as **CANNOT VERIFY**, not as a pass.
"""


def _judge_vars(wf: WorkflowDef, row: sqlite3.Row, worktree: Path, sha: str) -> dict:
    number = _pr_number(row["pr_url"])
    base = wf.get("workspace", "base_branch", default="master")
    exp_path = judge.experiments_path(worktree)
    return {
        "task_id": row["task_id"],
        "task_title": row["title"] or "",
        "branch_name": row["branch_name"] or "",
        "base_branch": base,
        "workspace_path": str(worktree),
        "repo_path": str(wf.path.parent),
        "project_key": wf.project_key,
        "task_number": row["task_number"],
        "pr_url": row["pr_url"] or "(no PR link on the run row)",
        "head_sha": sha,
        "experiments_path": str(exp_path),
        "judge_cmd": f"chela judge run {row['task_id']} --experiments {exp_path}",
        "test_cmd": judge.judge_test_cmd(wf) or "(none)",
        "diff_cmd": f"git diff origin/{base}...HEAD",
        "pr_view_cmd": (f"gh pr view {number} --comments" if number
                        else "gh pr view --comments   # no PR url on the run row"),
    }


def _refresh_judge_worktree(repo: Path, worktree: Path, base: str) -> str:
    """Merge fresh ``origin/<base>`` into the judge's throwaway worktree before it is judged.

    ⛔ CMX-176. A dispatched run's branch is cut at claim time and then sits — through
    review, rework rounds, and CI — while ``base_branch`` moves on underneath it. Judging
    that stale tip means a fix that landed on base after the claim is absent from the
    mutation baseline, so the pre-mutation suite goes red for reasons that have nothing to
    do with this PR, and the judge correctly (and uselessly) refuses every experiment:
    cannot_verify. **Observed live 2026-07-25 on cmx-174 (#222):** the branch was 14 commits
    behind ``dev``, its merge-base predating the ``_isolate_claude_projects`` conftest fix,
    so 3-4 telegram tests failed from a leak already closed on base. A human ran
    ``gh pr update-branch`` by hand and the baseline went to 1830 passed / 0 failed. This is
    that same catch-up, done automatically, right before the baseline is ever measured.

    The merge lands in the judge's OWN throwaway detached copy — never the run's real
    worktree, never pushed, never touching the actual PR branch on GitHub — so
    ``JUDGE_PROMPT``'s "do not commit, push, or edit the PR's branch" stays true no matter
    what this does. A real commit (not ``--no-commit``) is required: ``run_experiments``'s
    ``_git_dirty`` check demands a clean tree before a single mutation runs, and a merge
    left staged-but-uncommitted would trip that guard on every judged PR.

    ⛔ DEGRADES, NEVER BLOCKS on anything short of a REAL conflict: no ``origin`` remote, no
    network, a repo this is not a git repo at all (a unit test's fake worktree) — every one
    of those leaves the worktree exactly as :func:`detached_worktree` left it, and judging
    proceeds on the un-refreshed tip, same as before this existed. Only a genuine merge
    conflict is reported back — the one case where "proceed anyway" would judge a tree that
    does not even resolve, and the caller turns it into a NAMED cannot_verify instead of a
    judge agent wasted on it.
    """
    if not _git_ok(_git(repo, "fetch", "origin", base, timeout=GIT_NET_TIMEOUT_SECONDS)):
        return ""              # no remote / no network / not a git repo — nothing to refresh
    target = _git_out(_git(repo, "rev-parse", f"origin/{base}"))
    if not target:
        return ""              # base_branch does not resolve on origin — nothing to merge in
    behind = _git_out(_git(worktree, "rev-list", "--count", f"HEAD..{target}"))
    if not behind or behind == "0":
        return ""              # already caught up (or the count could not be read)
    merge = _git(worktree, "merge", "--no-edit", "--quiet", target)
    if merge is not None and merge.returncode == 0:
        log.info("judge: refreshed worktree %s from origin/%s (was %s commit(s) behind)",
                  worktree, base, behind)
        return ""
    _git(worktree, "merge", "--abort")
    detail = (merge.stderr or merge.stdout or "").strip()[:300] if merge else "the merge could not be run"
    return (
        f"this run's branch is {behind} commit(s) behind origin/{base} and could not be "
        f"refreshed automatically ({detail or 'merge conflict'}) — rebase or merge "
        f"origin/{base} into it by hand before it can be judged"
    )


def _spawn_judge(wf: WorkflowDef, row: sqlite3.Row, sha: str, conn: sqlite3.Connection) -> bool:
    """Put a judge on this PR's head — in a throwaway worktree, on a detached HEAD.

    ⛔ The sha is burned FIRST, before tmux is touched. A judge that fails to launch must not
    be retried every 60s forever: the run is marked CANNOT VERIFY on this commit and left in
    ``awaiting_review``. ⚖️ CMX-81: that CANNOT VERIFY is not the end of the road, though — it
    is an UNKNOWN, and the trigger gate re-fires this same commit a BOUNDED number of times
    (``judge_max_unknown_retries``), so a one-off launch failure or flake costs a retry, not
    the whole adversarial pass. Only once the budget is spent does the unknown settle to a
    human — a loop that surfaces rather than spins.
    """
    task_id, branch = row["task_id"], row["branch_name"] or ""
    worktree = judge.judge_worktree_path(wf, task_id)
    # ⚖️ CMX-81: the CANNOT VERIFY retry budget belongs to a COMMIT, and this is its ONLY
    # writer. A new head is a fresh judgement → the count starts at 0. Re-launching on the
    # SAME head that last came back `cannot_verify` IS a retry → bump it (the trigger gate
    # already proved the bump stays under `judge_max_unknown_retries`). Any other same-head
    # re-launch keeps the running total. Counting retries HERE, not on the verdict, keeps one
    # writer whatever ended the judge — the watchdog, a launch failure, or `chela judge run`.
    same_sha = row["judge_sha"] == sha
    prior = (row["judge_cannot_verify_tries"] or 0) if same_sha else 0
    tries = prior + 1 if (same_sha and row["judge_state"] == judge.J_CANNOT_VERIFY) else prior
    conn.execute(
        "UPDATE runs SET judge_sha=?, judge_state=?, judge_started_at=?, judge_detail=?, "
        "judge_cannot_verify_tries=? WHERE task_id=?",
        (sha, judge.J_RUNNING, _now(), "", tries, task_id),
    )
    conn.commit()

    try:
        created = detached_worktree(wf.path.parent, sha, worktree)[1]
    except (BranchGone, subprocess.CalledProcessError) as e:
        detail = getattr(e, "stderr", None) or str(e)
        if isinstance(detail, bytes):
            detail = detail.decode(errors="replace")
        set_judge_state(task_id, judge.J_CANNOT_VERIFY,
                        f"the judge worktree could not be created: {str(detail).strip()[:300]}")
        log.warning("judge: %s: could not check out %s: %s", task_id, sha[:12], detail)
        return False

    # ⛔ CMX-176: refresh BEFORE the agent ever sees this tree — a stale worktree measured a
    # baseline that had nothing to do with the PR (see _refresh_judge_worktree). This never
    # blocks on its own (no remote/no network just proceeds unrefreshed); only a REAL merge
    # conflict against base_branch stops the spawn, and it stops it with a named diagnosis
    # instead of a wasted judge agent and a mystery red baseline.
    base = wf.get("workspace", "base_branch", default="master")
    stale = _refresh_judge_worktree(wf.path.parent, worktree, base)
    if stale:
        set_judge_state(task_id, judge.J_CANNOT_VERIFY, stale)
        log.warning("judge: %s: %s", task_id, stale)
        return False

    prompt = render_prompt(
        wf.get("agent", "judge_prompt", default=None) or JUDGE_PROMPT,
        _judge_vars(wf, row, worktree, sha),
    )
    try:
        _launch_agent(
            wf, task_id, judge.judge_window_name(branch), worktree, prompt, conn,
            hook_vars=_judge_vars(wf, row, worktree, sha),
            fresh_worktree=created,
            # 🤫 CMX-97 (race closed CMX-136). The judge is NOT this run's agent — `window_id`
            # is the run's own window (the Feed keys its lane on it, the inbox addresses
            # run_review to it) and pointing it at a judge that will be gone in twenty minutes
            # would misattribute both. `judge_window=True` stamps `judge_window_id`/
            # `judge_window_epoch` instead, INSIDE `_launch_agent` at the same lossless moment
            # as the run's own id — right after the tmux window is created, before the
            # ready-wait/`_send_seed` that can run for several seconds. Stamping it out HERE,
            # after this call returns, left the judge's window live-but-unstamped for that
            # whole gap: invisible to `dispatched_window_ids`, so the Wall drew it full-size
            # like a human's window instead of docking it minimized from the first poll.
            judge_window=True,
            # ⛔ The judge runs on the fixed capable model, NEVER the coding-agent Settings
            # model — a `sonnet` default set for the fleet must not downgrade the safety net.
            role="judge",
        )
    except Exception as e:
        log.exception("judge: %s: the judge agent failed to launch", task_id)
        set_judge_state(task_id, judge.J_CANNOT_VERIFY, f"the judge agent failed to launch: {e}")
        return False
    log.info("judge: %s: judging %s on %s in %s", task_id, row["pr_url"] or "?", sha[:12], worktree)
    return True


def _judge_watchdog(conn: sqlite3.Connection, wf: WorkflowDef, live_windows: set[str]) -> int:
    """A judge that stopped without a verdict is CANNOT VERIFY — never a pass, never a fail.

    Two silences mean the same thing, and neither may be mistaken for a clean bill of health:
    the window is gone but no verdict was published (the agent died, or a human killed it),
    or it has been running past :data:`JUDGE_TIMEOUT_SECONDS` (it is stuck, not thinking).
    ⛔ `chela judge run` writes the state BEFORE it kills its own window, so "window gone,
    state still running" is unambiguous — it did not finish.

    Returns how many runs were handed back to a human this way.
    """
    now = _parse_ts(_now())
    handed_over = 0
    for row in conn.execute(
        "SELECT * FROM runs WHERE workflow_path=? AND judge_state=?",
        (str(wf.path), judge.J_RUNNING),
    ).fetchall():
        window = judge.judge_window_name(row["branch_name"] or "")
        started = _parse_ts(row["judge_started_at"])
        timed_out = (
            started is not None and now is not None
            and (now - started).total_seconds() >= JUDGE_TIMEOUT_SECONDS
        )
        alive = window in live_windows
        if alive and not timed_out:
            continue
        # ⚖️🕳️ CMX-229 Objective 2: `alive` is ONE signal (this tick's tmux snapshot) and
        # it can be wrong — measured live on CMX-227, a judge SIGKILLed (exit 137) mid-
        # `chela judge run` because the watchdog reaped its worktree/window on exactly
        # this kind of miss. `judge.judge_lock_live` is a SECOND, independent signal (the
        # judge's own claim file: pid + `/proc` start time, CMX-219) — cross-check it
        # before tearing anything down. ⛔ BOUNDED, not a second timeout: once `timed_out`
        # is True the lock is never consulted and this always reaps, exactly as before —
        # a live owner past JUDGE_TIMEOUT_SECONDS is "stuck, not thinking" regardless of
        # what its own lock claims, so a hold can never outlive that bound.
        if not timed_out and judge.judge_lock_live(judge.judge_worktree_path(wf, row["task_id"])):
            log.info(
                "judge watchdog: %s: window %s missing from this tick's tmux snapshot, but "
                "the judge lock says its owner is still alive — holding teardown", row["task_id"],
                window,
            )
            continue
        reason = (
            f"the judge did not finish in {JUDGE_TIMEOUT_SECONDS // 60}min — it is stuck, "
            "not thinking" if timed_out else
            "the judge's window disappeared before it published a verdict"
        )
        conn.execute(
            "UPDATE runs SET judge_state=?, judge_detail=? WHERE task_id=?",
            (judge.J_CANNOT_VERIFY, reason, row["task_id"]),
        )
        conn.commit()
        if alive:
            _kill_windows_named(window)
        remove_worktree(wf.path.parent, judge.judge_worktree_path(wf, row["task_id"]))
        handed_over += 1
        # ⛔ Loud. The run stays exactly where it was (`awaiting_review`), which is the ONLY
        # safe answer — but a judge that silently never ran is indistinguishable from a judge
        # that found nothing, and that is precisely the confusion this whole feature exists
        # to end.
        log.warning(
            "judge: %s → CANNOT VERIFY: %s. The PR was NOT reviewed adversarially and it was "
            "NOT sent back; it is a human's now.", row["task_id"], reason,
        )
    return handed_over


def list_runs() -> list[dict]:
    with _db() as conn:
        rows = conn.execute("SELECT * FROM runs ORDER BY started_at DESC").fetchall()
        return [dict(r) for r in rows]


def delete_run(task_id: str) -> dict:
    """Drop a run row; if it's still in flight, abort + clean up first.

    Idempotent: deleting a non-existent row succeeds as a no-op so the UI's
    inline-confirm can fire without distinguishing "already gone" from "just
    gone". Mirrors the user-visible delete affordance on Kanban cards.

    - done / failed / awaiting_review → just delete the row (PRs are left
      alone; user closes on GitHub if needed).
    - claimed / running → kill the tmux window, `git worktree remove --force`
      the worktree, then delete the row.
    """
    with _db() as conn:
        row = conn.execute(
            "SELECT * FROM runs WHERE task_id=?", (task_id,)
        ).fetchone()
        if row is None:
            return {"ok": True, "deleted": False, "reason": "no such run"}

        status = row["status"]
        cleanup_errors: list[str] = []
        if status in ("claimed", "running"):
            if row["window_name"]:
                try:
                    _kill_window(row["window_name"])
                except Exception as e:
                    cleanup_errors.append(f"kill_window: {e}")
            worktree_path = row["worktree_path"]
            wf_path = row["workflow_path"] or ""
            repo_dir = Path(wf_path).parent if wf_path else None
            if worktree_path and repo_dir and repo_dir.is_dir():
                try:
                    wt = subprocess.run(
                        ["git", "worktree", "remove", "--force", worktree_path],
                        cwd=str(repo_dir), capture_output=True, text=True, timeout=30,
                    )
                    if wt.returncode != 0:
                        cleanup_errors.append(
                            (wt.stderr or wt.stdout or "git worktree remove failed").strip()
                        )
                except (FileNotFoundError, subprocess.TimeoutExpired) as e:
                    cleanup_errors.append(f"worktree remove: {e}")

        conn.execute("DELETE FROM runs WHERE task_id=?", (task_id,))
        conn.commit()
        return {
            "ok": True,
            "deleted": True,
            "task_id": task_id,
            "prior_status": status,
            "cleanup_errors": cleanup_errors,
        }


def dry_run(workflow_path: str | Path) -> list[dict]:
    """Compute what `tick` would dispatch, without touching tmux, hooks, or the DB.

    Returns one dict per open task with keys: task_id, title, worktree_path,
    branch, prompt, agent_cmd, agent_cmd_source. The worktree path is the
    would-be path — no git worktree is created; agent_cmd is the command that
    live dispatch WOULD run (see resolve_agent_cmd), previewed but not executed.

    Reuses any task_number already assigned to a known task_id; for unknown
    tasks, projects a synthetic sequence above the current MAX(task_number) so
    the dry-run preview reflects what live dispatch would produce.
    """
    wf = load_workflow(workflow_path)
    source = get_source(wf)
    open_tasks = source.list_open_tasks()

    base_branch = wf.get("workspace", "base_branch", default="master")
    project_key = wf.project_key
    root = resolve_workspace_root(wf)
    repo_path = wf.path.parent
    agent_cmd, agent_cmd_source = resolve_agent_cmd(wf)

    with _db() as conn:
        max_row = conn.execute(
            "SELECT COALESCE(MAX(task_number), 0) AS m FROM runs WHERE workflow_path=?",
            (str(wf.path),),
        ).fetchone()
        next_number = int(max_row["m"]) + 1
        existing_numbers = {
            r["task_id"]: r["task_number"]
            for r in conn.execute(
                "SELECT task_id, task_number FROM runs WHERE workflow_path=?",
                (str(wf.path),),
            ).fetchall()
        }

    plans: list[dict] = []
    for task in open_tasks:
        tn = existing_numbers.get(task.id)
        if tn is None:
            tn = next_number
            next_number += 1
        worktree = (root / task.id).resolve()
        branch = f"{project_key.lower()}-{tn}"
        prompt = render_prompt(wf.prompt_template, {
            "task_id": task.id,
            "task_title": task.title,
            "task_file": task.file,
            "task_line_number": task.line_number,
            "workspace_path": str(worktree),
            "branch_name": branch,
            "base_branch": base_branch,
            "repo_path": str(repo_path),
            "project_key": project_key,
            "task_number": tn,
        })
        plans.append({
            "task_id": task.id,
            "task_number": tn,
            "project_key": project_key,
            "title": task.title,
            "worktree_path": str(worktree),
            "branch": branch,
            "prompt": prompt,
            "agent_cmd": agent_cmd,
            "agent_cmd_source": agent_cmd_source,
        })
    return plans
