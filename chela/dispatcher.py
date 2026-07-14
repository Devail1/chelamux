from __future__ import annotations
import json
import logging
import re
import sqlite3
import subprocess
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import NamedTuple

from chela import hold
from chela.config import CHELA_DIR, DISPATCH_TICK_INTERVAL, TMUX_SESSION, max_reworks
from chela.messenger import send_tmux
from chela.sources import Task, get_source
from chela.transcripts import agent_transcript_summary
from chela.workflow import (
    WorkflowDef,
    load_workflow,
    load_workflow_cached,
    poll_interval_seconds,
    render_prompt,
    resolve_workspace_root,
)
from chela.worktree import BranchGone, attach_worktree, ensure_worktree

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
# prompt we confirm the agent actually picked it up — a late splash redraw can
# swallow the paste, leaving the agent idle with no task. The agent flips
# "idle" → "busy" the moment it accepts a prompt, so poll that status for a
# short window; if it never flips, re-send (capped) instead of hoping.
SEED_CONFIRM_TIMEOUT_SECONDS = 8.0
SEED_CONFIRM_POLL_INTERVAL = 1.0
SEED_MAX_SENDS = 3
SEED_RESEND_SETTLE_SECONDS = 1.0

# Claude Code TUI ready indicators, matched against `tmux capture-pane -p`.
# The prompt glyph marks the (empty) input box and is present in every
# permission mode. The bypass-permissions footer only shows under
# `--permission-mode bypassPermissions`; it's a secondary ready hint (the
# default `--permission-mode auto` does not render it).
_READY_FOOTER = "bypass permissions"
_PROMPT_CHAR = "❯"  # ❯

_PR_NUMBER_RE = re.compile(r"/pull/(\d+)(?:[/?#]|$)")

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
# A check that has NOT finished. NEUTRAL/SKIPPED/SUCCESS are finished-and-not-failing.
CI_UNSETTLED_STATUSES = ("QUEUED", "IN_PROGRESS", "PENDING", "WAITING", "REQUESTED", "EXPECTED")

# A whole CI log does not fit in a prompt. The tail is where the failure is.
CI_LOG_TAIL_CHARS = 4000
_CI_RUN_ID_RE = re.compile(r"/actions/runs/(\d+)")

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

AGENT_BASE_CMD = "claude"


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


def resolve_agent_cmd(wf: WorkflowDef) -> tuple[str, str]:
    """The command that launches an agent, and where it came from.

    PRECEDENCE (highest first) — the one place this is decided:

      1. ``agent.cmd`` in WORKFLOW.md  → source ``"workflow"``.
         An explicit per-workflow override stays authoritative: it is set by
         someone who can already write files in the repo, so it may be any
         command, and it deliberately shadows Settings.
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

    Returns ``(cmd, source)``.
    """
    cmd = wf.get("agent", "cmd", default=None)
    if isinstance(cmd, str) and cmd.strip():
        return cmd.strip(), "workflow"
    mode = settings_permission_mode()
    if mode:
        return f"{AGENT_BASE_CMD} --permission-mode {mode}", "settings"
    return f"{AGENT_BASE_CMD} --permission-mode {DEFAULT_PERMISSION_MODE}", "default"


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
    """
    tasks_from_text = getattr(source, "tasks_from_text", None)
    closed_ids_from_text = getattr(source, "closed_ids_from_text", None)
    tracker = getattr(source, "path", None)
    if tasks_from_text is None or closed_ids_from_text is None or tracker is None:
        return on_disk

    repo = wf.path.parent
    base = wf.get("workspace", "base_branch", default="master")
    try:
        rel = str(Path(tracker).relative_to(repo))
    except ValueError:
        return on_disk
    if not _git_out(_git(repo, "remote")):
        return on_disk
    if not _git_ok(_git(repo, "fetch", "origin", base, timeout=GIT_NET_TIMEOUT_SECONDS)):
        log.warning(
            "claim: could not fetch origin/%s — claiming from the on-disk tracker, which "
            "may be stale", base,
        )
        return on_disk
    show = _git(repo, "show", f"FETCH_HEAD:{rel}")
    if not _git_ok(show):
        log.warning("claim: %s is not on origin/%s — claiming from the on-disk tracker", rel, base)
        return on_disk

    text = show.stdout
    remote_tasks = tasks_from_text(text)
    known = {t.id for t in remote_tasks} | closed_ids_from_text(text)
    unpushed = [t for t in on_disk if t.id not in known]
    if unpushed:
        log.debug(
            "claim: %d task(s) on disk are not on origin/%s yet; queued after the pushed ones",
            len(unpushed), base,
        )
    return remote_tasks + unpushed


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

    This runs unattended under PM2, so every step FAILS CLOSED: it writes only
    when it is on base_branch, only when the tracker file is clean, and only
    after a fast-forward to the remote. It never force-pushes, never commits a
    path other than the tracker, and rolls its own commit back if the push is
    rejected. A missed checkbox is cosmetic and self-heals — the pending set is
    recomputed from the runs table on every tick, not remembered — whereas a
    mangled base branch is not.

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

    # Only ever write the branch we were told to write.
    head = _git_out(_git(repo, "rev-parse", "--abbrev-ref", "HEAD"))
    if head != base:
        log.warning(
            "tracker strike skipped: %s is on %r, not %r", repo, head or "?", base
        )
        return 0

    # Never sweep a human's in-progress edit of the tracker into our commit.
    status = _git(repo, "status", "--porcelain", "--", rel)
    if not _git_ok(status):
        return 0
    if status.stdout.strip():
        log.warning("tracker strike skipped: %s has uncommitted changes", rel)
        return 0

    # Get level with the remote first, fast-forward only: a diverged base branch
    # is a skip-and-retry, never a rebase we weren't asked for and never a force.
    remote = _git_out(_git(repo, "remote"))
    if remote:
        if not _git_ok(_git(repo, "fetch", "origin", base, timeout=GIT_NET_TIMEOUT_SECONDS)):
            log.warning("tracker strike skipped: could not fetch origin/%s", base)
            return 0
        if not _git_ok(_git(repo, "merge", "--ff-only", "FETCH_HEAD")):
            log.warning(
                "tracker strike skipped: %s has diverged from origin/%s — "
                "leaving it for a human", base, base,
            )
            return 0

    try:
        results = close_tasks(task_ids)
    except OSError as e:
        log.warning("tracker strike failed to write %s: %s", rel, e)
        _git(repo, "checkout", "--", rel)
        return 0

    for tid, outcome in sorted(results.items()):
        if outcome == "missing":
            log.warning(
                "tracker strike: no line matches task %s — a human edited or "
                "removed it; not guessing", tid,
            )
        elif outcome == "already":
            log.info("tracker strike: task %s was already struck", tid)
    struck = sorted(t for t, outcome in results.items() if outcome == "struck")
    if not struck:
        return 0

    parent = _git_out(_git(repo, "rev-parse", "HEAD"))
    subject = f"chore({rel}): strike {len(struck)} merged task" + ("s" if len(struck) > 1 else "")
    body = "\n".join(f"- {tid}" for tid in struck)
    # Pathspec form: commits ONLY the tracker, ignoring whatever else a human
    # may have staged in this checkout.
    if not _git_ok(_git(repo, "commit", "-m", subject, "-m", body, "--", rel)):
        log.warning("tracker strike: commit failed; restoring %s", rel)
        _git(repo, "checkout", "--", rel)
        return 0

    if remote:
        mine = _git_out(_git(repo, "rev-parse", "HEAD"))
        if not _git_ok(_git(repo, "push", "origin", f"HEAD:{base}", timeout=GIT_NET_TIMEOUT_SECONDS)):
            # Someone pushed between our fetch and our push. Roll our own commit
            # back — but only if HEAD is still exactly it — and retry next tick.
            if parent and mine and _git_out(_git(repo, "rev-parse", "HEAD")) == mine:
                _git(repo, "reset", "--soft", parent)
                _git(repo, "checkout", "HEAD", "--", rel)
            log.warning(
                "tracker strike: push to %s rejected — rolled back, retrying next tick", base
            )
            return 0

    log.info("tracker strike: marked %d task(s) done on %s: %s", len(struck), base, ", ".join(struck))
    return len(struck)


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
        ("pr_checks", "ALTER TABLE runs ADD COLUMN pr_checks TEXT"),
        ("pr_head_sha", "ALTER TABLE runs ADD COLUMN pr_head_sha TEXT"),
        ("ci_failed_sha", "ALTER TABLE runs ADD COLUMN ci_failed_sha TEXT"),
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
) -> bool | None:
    """Did the seed prompt actually reach the agent?

    True once the agent flips to "busy" (it only does that with a prompt in
    hand), False if it is still "idle" when the window closes (the paste was
    swallowed — a splash redraw landing after the ready glyph does exactly
    that), and None when the status is unreadable, in which case delivery is
    unverifiable and the caller should fail open rather than re-send blindly.

    A freshly-booted agent reads "idle" until the seed makes it busy, so this
    watches for the idle → busy transition, not for "is it idle right now".
    """
    deadline = time.monotonic() + timeout
    while True:
        status = _agent_status(window_id)
        if status is None:
            return None
        if status == "busy":
            return True
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return False
        time.sleep(min(poll, remaining))


def _send_seed(window_id: str, prompt: str, task_id: str) -> bool:
    """Send the seed prompt and confirm it landed, re-sending if it didn't.

    Returns False only when the send itself fails. An unconfirmed seed after
    SEED_MAX_SENDS is logged and left to the reconcile watchdog rather than
    retried forever — a genuinely broken agent must fail cleanly.
    """
    for send in range(1, SEED_MAX_SENDS + 1):
        if not send_tmux(window_id, prompt):
            return False
        landed = _seed_landed(window_id)
        if landed is None:
            log.debug(
                "Task %s: agent status unreadable on %s; assuming the seed landed",
                task_id, window_id,
            )
            return True
        if landed:
            if send > 1:
                log.info("Task %s: seed landed on %s after %d sends", task_id, window_id, send)
            return True
        if send < SEED_MAX_SENDS:
            log.warning(
                "Task %s: agent on %s still idle %.0fs after the seed (paste dropped); "
                "re-sending (%d/%d)",
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
    -status API, with a single ``state`` + ``context``). A rollup we cannot recognise at all
    is not a pass — it is simply not failing, and the caller says so.

    ⛔ UNSETTLED WINS OVER FAILING, deliberately. A rollup with one red job and one still
    running is ``pending``, not ``failing``: acting on it would send the agent back into a
    branch whose CI is still writing its own verdict, and the second half of that run could
    just as well fail too — a second, different red on the SAME sha, which the once-per-sha
    guard would then swallow. The checks settle in a minute; the loop can wait a tick.
    """
    if not nodes:
        return CI_NONE, (), ()
    unsettled = False
    failing: list[str] = []
    run_ids: list[str] = []
    for node in nodes:
        if not isinstance(node, dict):
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
    if unsettled:
        return CI_PENDING, (), ()
    if failing:
        # dict.fromkeys: dedupe, keep order — a matrix job can fail in several shards.
        return CI_FAILING, tuple(dict.fromkeys(failing)), tuple(dict.fromkeys(run_ids))
    return CI_PASSING, (), ()


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
            cwd=repo_dir, capture_output=True, text=True, timeout=20,
        )
    except FileNotFoundError:
        return CIStatus(CI_UNKNOWN, detail="gh is not installed — nothing can read the checks")
    except subprocess.TimeoutExpired:
        return CIStatus(CI_UNKNOWN, detail="gh timed out reading the checks")
    if out.returncode != 0:
        return CIStatus(CI_UNKNOWN, detail=(out.stderr or out.stdout or "gh failed").strip()[:200])
    try:
        data = json.loads(out.stdout)
    except (json.JSONDecodeError, ValueError):
        return CIStatus(CI_UNKNOWN, detail="gh returned something that is not JSON")
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
    """
    if not repo_dir or not run_ids:
        return ""
    try:
        out = subprocess.run(
            ["gh", "run", "view", run_ids[0], "--log-failed"],
            cwd=repo_dir, capture_output=True, text=True, timeout=60,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as e:
        return f"(could not fetch the CI log: {e})"
    if out.returncode != 0:
        return f"(could not fetch the CI log: {(out.stderr or out.stdout or '').strip()[:200]})"
    text = (out.stdout or "").strip()
    if len(text) <= CI_LOG_TAIL_CHARS:
        return text
    return "… (log truncated — this is the tail)\n" + text[-CI_LOG_TAIL_CHARS:]


def _ci_verdict_body(ci: CIStatus, log_tail: str, pr_url: str | None) -> str:
    """The verdict a red CI writes — a FACT, stated as one.

    It is deliberately not a review: it makes no judgment about the code, it reports what
    GitHub said. That is why this loop needs no reviewer and no LLM, and why a wrong verdict
    is not a risk here the way it would be for a judge.
    """
    jobs = "\n".join(f"- `{name}`" for name in ci.failing) or "- (the rollup named no job)"
    log_block = f"\n```\n{log_tail}\n```\n" if log_tail else "\n_(no log tail available)_\n"
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
    reviews = reviews_of(run)
    return str(reviews[-1].get("body") or "") if reviews else ""


def _pr_number(pr_url: str | None) -> str | None:
    m = _PR_NUMBER_RE.search(str(pr_url or ""))
    return m.group(1) if m else None


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


def request_changes(ident: str, body: str) -> dict:
    """FAIL a PR under review: the run goes to ``changes_requested`` and the loop turns.

    (a) writes the verdict + the status on the run row — THE authority — and (b) posts the
    body as a PR comment, which is the human-readable projection and the record the
    reworking agent reads back. In that order: if the comment fails, the loop still runs.

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
    posted, detail = _post_pr_comment(run.get("pr_url"), repo_dir, body)
    if not posted:
        log.warning(
            "review: %s is changes_requested, but the PR comment did not post (%s). The "
            "run row is the authority, so the rework still spawns — with the verdict in "
            "its prompt but nothing on the PR to read back.", task_id, detail,
        )
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


def _prune_done_rows(
    conn: sqlite3.Connection,
    workflow_path: str,
    keep: int = DONE_HISTORY_PER_WORKFLOW,
) -> int:
    """Keep at most `keep` most-recent done rows for a workflow, drop the older ones.

    Ordering uses ended_at (falling back to started_at) so rows without timestamps
    sort last and get pruned first.
    """
    cursor = conn.execute(
        """DELETE FROM runs
           WHERE status='done'
             AND workflow_path=?
             AND task_id NOT IN (
               SELECT task_id FROM runs
               WHERE status='done' AND workflow_path=?
               ORDER BY COALESCE(ended_at, started_at, '') DESC
               LIMIT ?
             )""",
        (workflow_path, workflow_path, keep),
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
    back to ``default`` / ``CHELA_DISPATCH_TICK_INTERVAL``. Called from the
    daemon loop on every pass so an edited interval takes effect without a
    restart; it is stat-gated, so an unchanged file costs one ``stat``.
    """
    base = DISPATCH_TICK_INTERVAL if default is None else default
    return poll_interval_seconds(load_workflow_cached(workflow_path).workflow, base)


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
        return {
            "open": 0, "reconciled_done": 0, "reconciled_failed": 0, "dispatched": 0,
            "pr_state_refreshed": 0, "watchdog_renudged": 0, "tracker_struck": 0,
            "reworked": 0, "escalated": 0, "ci_failed": 0,
            "blocked": True, "error": status.error, "held": False, "hold_expired": False,
        }
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
        "tracker_struck": 0,
        "reworked": 0,
        "escalated": 0,
        "ci_failed": 0,
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
        pr_rows = conn.execute(
            "SELECT task_id, status, pr_url, workflow_path FROM runs "
            "WHERE pr_url IS NOT NULL AND (pr_state IS NULL OR pr_state='open')"
        ).fetchall()
        ci_now: dict[str, CIStatus] = {}   # task_id → what GitHub said THIS tick
        for pr_row in pr_rows:
            wf_path = pr_row["workflow_path"]
            repo_dir = str(Path(wf_path).parent) if wf_path else None
            state, mergeable = _read_pr_status(pr_row["pr_url"], repo_dir)
            # 0b. THE CHECKS — read from their owner, for the runs parked in review.
            #
            # Only those: they are the ones a merge gate protects and the only ones a red CI
            # can send back. A `running` row's PR is still being pushed to, and asking GitHub
            # about a moving target every 60s buys nothing. ⛔ Rows whose pr_state is already
            # terminal never reach here at all (the WHERE above) — which is also the
            # "no resurrection" rule holding: a merged run's red CI does NOTHING.
            if pr_row["status"] in REVIEW_STATUSES:
                ci = _read_pr_checks(pr_row["pr_url"], repo_dir)
                ci_now[pr_row["task_id"]] = ci
                conn.execute(
                    "UPDATE runs SET pr_checks=?, pr_head_sha=COALESCE(?, pr_head_sha) "
                    "WHERE task_id=?",
                    (ci.state, ci.head_sha, pr_row["task_id"]),
                )
                if ci.state == CI_UNKNOWN:
                    # ⛔ Loud, and never a pass: a check state nobody could read is the
                    # doctor rule's CANNOT VERIFY. The merge gate refuses it downstream.
                    log.warning(
                        "CI: could not read the checks on %s (%s) — recorded as UNKNOWN, "
                        "which is NOT a pass: nothing will merge it and nothing sends it "
                        "back until GitHub can be asked.",
                        pr_row["task_id"], ci.detail,
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
        rows = conn.execute(
            "SELECT * FROM runs WHERE status IN ({})".format(
                ",".join("?" * len(ACTIVE_STATUSES + REVIEW_STATUSES))
            ),
            ACTIVE_STATUSES + REVIEW_STATUSES,
        ).fetchall()
        for row in rows:
            if row["status"] in REVIEW_STATUSES and row["pr_state"] == "merged":
                # pr_state was refreshed in phase 0 above, so we see the merge on
                # the very tick it lands. The tracker strike happens in 1b, after
                # this transition is durable — if it fails, this row stays `done`
                # and the strike is simply retried on the next tick.
                if row["window_name"]:
                    _kill_window(row["window_name"])
                conn.execute(
                    "UPDATE runs SET status='done' WHERE task_id=?", (row["task_id"],)
                )
                merged_in_tick += 1
                summary["reconciled_done"] += 1
                log.info("Task %s done (PR merged)", row["task_id"])
                continue
            if row["task_id"] not in open_ids:
                # Read the agent's transcript *before* killing the window —
                # transcript resolution maps window_name → cwd → transcript via
                # the live tmux pane, and that mapping disappears once tmux drops
                # the window. For awaiting_review rows the window is already dead
                # (the task-finished CLI kills it), so the transcript read is a
                # no-op fallback for the rare case where it wasn't killed.
                pr_url = _read_pr_url(row["window_name"])
                if row["window_name"]:
                    _kill_window(row["window_name"])
                # Preserve the original ended_at on a review-state → done so
                # the timestamp reflects when the agent finished, not when the
                # human merged the PR. claimed/running rows have no ended_at
                # yet, so stamp it now.
                if row["status"] in REVIEW_STATUSES:
                    conn.execute(
                        "UPDATE runs SET status='done', pr_url=COALESCE(?, pr_url) WHERE task_id=?",
                        (pr_url, row["task_id"]),
                    )
                    merged_in_tick += 1
                else:
                    conn.execute(
                        "UPDATE runs SET status='done', ended_at=?, pr_url=COALESCE(?, pr_url) WHERE task_id=?",
                        (_now(), pr_url, row["task_id"]),
                    )
                summary["reconciled_done"] += 1
                log.info("Task %s done (removed from source, window killed)", row["task_id"])
                continue
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

            # Watchdog: a running row whose window is alive but stuck at an
            # idle, empty Claude prompt means the startup race dropped the
            # prompt (the startup-race strand). Re-send the prompt once; if it
            # stays idle for another WATCHDOG_IDLE_MINUTES, fail it so the
            # attempt-capped re-dispatch path takes over.
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
                # Cross-check the pane signature against the native busy status
                # (the same authority the seed-delivery check uses) so an agent
                # that is actually working never gets nudged; an unreadable
                # status falls back to the pane alone.
                stuck = (
                    idle_age_ok
                    and _pane_idle_empty_prompt(_capture_pane(row["window_name"]))
                    and _agent_status(row["window_name"]) != "busy"
                )
                if stuck:
                    nudged = _parse_ts(row["idle_nudged_at"])
                    task = tasks_by_id.get(row["task_id"])
                    if nudged is not None:
                        # Already nudged. Give the nudge a full window to take
                        # effect before declaring it dead, to avoid failing an
                        # agent that's merely between steps.
                        if (datetime.now(timezone.utc) - nudged).total_seconds() >= WATCHDOG_IDLE_MINUTES * 60:
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
                    else:
                        # ⛔ A stuck REWORK is re-nudged with its REWORK prompt, not the
                        # first-dispatch one: the two say opposite things ("branch and open a
                        # PR" vs "you are already on your branch, your PR is open, here is
                        # the verdict"). Re-seeding the wrong one is the same lost verdict as
                        # a `failed` rework, just delivered by hand.
                        prompt = _renudge_prompt(wf, row, task)
                        if prompt is None:
                            continue          # task gone from the tracker; nothing to re-send
                        _send_seed(row["window_name"], prompt, row["task_id"])
                        conn.execute(
                            "UPDATE runs SET idle_nudged_at=? WHERE task_id=?",
                            (_now(), row["task_id"]),
                        )
                        summary["watchdog_renudged"] += 1
                        log.warning(
                            "Task %s idle at empty prompt; re-sent prompt to %s",
                            row["task_id"], row["window_name"],
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
            # ⛔ Record the fired sha BEFORE firing. The failure modes are not symmetric: a
            # crash after this line costs at most ONE missed verdict (a human still sees the
            # red PR), while a crash before it would re-fire the same red every tick and burn
            # the whole rework budget on a single commit — which is the bug this guard exists
            # to prevent, arriving by the back door.
            conn.execute("UPDATE runs SET ci_failed_sha=? WHERE task_id=?", (sha, task_id))
            conn.commit()
            wf_dir = str(wf.path.parent)
            # The heavy read (a whole log archive) happens HERE and nowhere else: once, on
            # the transition into red — never on the poll.
            log_tail = _failing_log_tail(wf_dir, ci.run_ids if ci else ())
            result = request_changes(
                task_id,
                _ci_verdict_body(ci or CIStatus(CI_FAILING, sha), log_tail, row["pr_url"]),
            )
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
            if (row["rework_count"] or 0) >= cap:
                _escalate(
                    conn, row,
                    f"rework cap reached ({row['rework_count'] or 0}/{cap}) — the PR still "
                    "fails review. Branch, worktree and PR are preserved; every verdict is "
                    "on the run row (review_history).",
                )
                summary["escalated"] += 1

        # 2. Keep done rows for the "recent runs" view; just cap history per workflow.
        _prune_done_rows(conn, str(wf.path))
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

        # 3c. FETCH-THEN-CLAIM: re-read the queue from origin/<base_branch> right now,
        # rather than trusting the parse made at the top of this tick (before the PR
        # polling and the tracker strike, both of which touch the network). Necessary,
        # NOT sufficient — see _claim_order. Skipped entirely when every slot is busy:
        # there is nothing to claim, and a network fetch to learn that is a waste.
        queue = _claim_order(wf, source, open_tasks) if active < max_concurrent else []

        for task in queue:
            if active >= max_concurrent:
                break
            existing = conn.execute(
                "SELECT status, attempt FROM runs WHERE task_id=?", (task.id,)
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
                    """INSERT INTO runs (task_id, workflow_path, title, status, attempt, last_error, started_at)
                       VALUES (?, ?, ?, 'failed', ?, ?, ?)
                       ON CONFLICT(task_id) DO UPDATE SET
                         status='failed', attempt=excluded.attempt, last_error=excluded.last_error""",
                    (task.id, str(wf.path), task.title, attempt, str(e), _now()),
                )
                conn.commit()
                continue
            if spawned:
                active += 1
                summary["dispatched"] += 1

    return summary


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
        task_number = int(row["n"])

    worktree, created = ensure_worktree(repo_path, task.id, base_branch, project_key, task_number, root)
    branch = f"{project_key.lower()}-{task_number}"
    window_name = branch
    hook_vars = _prompt_vars(wf, task, str(worktree), branch, base_branch, task_number)

    # Claim the row before touching tmux so failure leaves a trace. A retry re-enters
    # here with the same task_id and gets a NEW window, so window_id is cleared on
    # conflict: leaving attempt 1's id would point the next run_review at a corpse
    # (or, worse, at whatever window tmux later recycled that id onto).
    conn.execute(
        """INSERT INTO runs (task_id, workflow_path, title, status, window_name, worktree_path, branch_name, started_at, attempt, task_number)
           VALUES (?, ?, ?, 'claimed', ?, ?, ?, ?, ?, ?)
           ON CONFLICT(task_id) DO UPDATE SET
             status='claimed', window_name=excluded.window_name,
             worktree_path=excluded.worktree_path, branch_name=excluded.branch_name,
             started_at=excluded.started_at, attempt=excluded.attempt, last_error=NULL,
             task_number=excluded.task_number, idle_nudged_at=NULL, window_id=NULL""",
        (task.id, str(wf.path), task.title, window_name, str(worktree), branch, _now(), attempt, task_number),
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
    return True


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
    # be parsed, and a name in this column would be a lie the Feed keys a lane on.
    if re.fullmatch(r"@\d+", target_id):
        conn.execute("UPDATE runs SET window_id=? WHERE task_id=?", (target_id, task_id))
        conn.commit()

    # WORKFLOW.md's agent.cmd → the Settings permission mode → the built-in default (see
    # resolve_agent_cmd). The mode is fixed at spawn: changing it in Settings affects the
    # NEXT dispatch, never an agent already running.
    agent_cmd, cmd_source = resolve_agent_cmd(wf)
    log.info("Launching %s with %r (source: %s)", task_id, agent_cmd, cmd_source)
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
        "window_id=NULL WHERE task_id=?",
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
        "last_error=NULL, idle_nudged_at=NULL, window_id=NULL, worktree_path=? "
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
