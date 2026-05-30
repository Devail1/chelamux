from __future__ import annotations
import json
import logging
import re
import sqlite3
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

from chela.config import CHELA_DIR, TMUX_SESSION
from chela.messenger import send_tmux
from chela.sources import Task, get_source
from chela.transcripts import agent_transcript_summary
from chela.workflow import WorkflowDef, load_workflow, render_prompt, resolve_workspace_root
from chela.worktree import ensure_worktree

log = logging.getLogger(__name__)

DB_PATH = CHELA_DIR / "scheduler.db"
MAX_ATTEMPTS = 3
DONE_HISTORY_PER_WORKFLOW = 50

# Readiness poll (see _wait_for_ready) — how long to wait for the agent TUI to
# accept input before sending the prompt, and how often to re-check.
READY_TIMEOUT_SECONDS = 60
READY_POLL_INTERVAL = 1.0

# Reconcile watchdog (see tick) — a `running` row stuck at an idle, empty
# Claude prompt for this long is treated as a dropped-prompt strand: nudged
# once, then failed if it stays idle for another window of this length.
WATCHDOG_IDLE_MINUTES = 5

# Claude Code TUI ready indicators, matched against `tmux capture-pane -p`.
# The prompt glyph marks the (empty) input box and is present in every
# permission mode. The bypass-permissions footer only shows under
# `--permission-mode bypassPermissions`; it's a secondary ready hint (the
# default `--permission-mode auto` does not render it).
_READY_FOOTER = "bypass permissions"
_PROMPT_CHAR = "❯"  # ❯

_PR_NUMBER_RE = re.compile(r"/pull/(\d+)(?:[/?#]|$)")


def _task_file_relative(task_file: str, repo_path: Path) -> str:
    """Path of the source file relative to the repo, or "" for non-file sources.

    Filesystem sources (markdown) yield an absolute path under repo_path, which
    resolves to e.g. "TODO.md". Non-filesystem sources (gh_issues) set
    task.file == "", and an issue-backed task isn't under any repo path — so
    relative_to() would raise ValueError. Guard both cases by degrading to ""
    rather than crashing the tick; the markdown path is byte-for-byte unchanged.
    """
    if not task_file:
        return ""
    try:
        return str(Path(task_file).relative_to(repo_path))
    except ValueError:
        return ""


def _db() -> sqlite3.Connection:
    CHELA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("""
        CREATE TABLE IF NOT EXISTS runs (
            task_id TEXT PRIMARY KEY,
            workflow_path TEXT NOT NULL,
            title TEXT NOT NULL,
            status TEXT NOT NULL,
            window_name TEXT,
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
    # Idempotent migrations for pre-existing DBs.
    for column, ddl in (
        ("pr_url", "ALTER TABLE runs ADD COLUMN pr_url TEXT"),
        ("pr_state", "ALTER TABLE runs ADD COLUMN pr_state TEXT"),
        ("pr_mergeable", "ALTER TABLE runs ADD COLUMN pr_mergeable TEXT"),
        ("task_number", "ALTER TABLE runs ADD COLUMN task_number INTEGER"),
        ("idle_nudged_at", "ALTER TABLE runs ADD COLUMN idle_nudged_at TEXT"),
    ):
        try:
            conn.execute(ddl)
        except sqlite3.OperationalError:
            pass  # column already exists
    conn.commit()
    return conn


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
        "task_file_relative": _task_file_relative(task.file, repo_path),
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
    out = subprocess.run(
        ["tmux", "new-window", "-t", TMUX_SESSION, "-n", window_name,
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


def tick(workflow_path: str | Path) -> dict:
    """One dispatcher pass. Returns a dict summary for logging."""
    wf = load_workflow(workflow_path)
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
            "SELECT task_id, pr_url, workflow_path FROM runs "
            "WHERE pr_url IS NOT NULL AND (pr_state IS NULL OR pr_state='open')"
        ).fetchall()
        for pr_row in pr_rows:
            wf_path = pr_row["workflow_path"]
            repo_dir = str(Path(wf_path).parent) if wf_path else None
            state, mergeable = _read_pr_status(pr_row["pr_url"], repo_dir)
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
        # claimed/running can disappear from source (legacy direct master-strike
        # flow) or have their tmux window die; awaiting_review rows just wait
        # for the TODO line to disappear from master (PR merge with --rebase).
        rows = conn.execute(
            "SELECT * FROM runs WHERE status IN ('claimed', 'running', 'awaiting_review')"
        ).fetchall()
        for row in rows:
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
                # Preserve the original ended_at on awaiting_review → done so
                # the timestamp reflects when the agent finished, not when the
                # human merged the PR. claimed/running rows have no ended_at
                # yet, so stamp it now.
                if row["status"] == "awaiting_review":
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
                if idle_age_ok and _pane_idle_empty_prompt(_capture_pane(row["window_name"])):
                    nudged = _parse_ts(row["idle_nudged_at"])
                    task = tasks_by_id.get(row["task_id"])
                    if nudged is not None:
                        # Already nudged. Give the nudge a full window to take
                        # effect before declaring it dead, to avoid failing an
                        # agent that's merely between steps.
                        if (datetime.now(timezone.utc) - nudged).total_seconds() >= WATCHDOG_IDLE_MINUTES * 60:
                            conn.execute(
                                "UPDATE runs SET status='failed', ended_at=?, last_error=? WHERE task_id=?",
                                (_now(), "agent idle at empty prompt after re-nudge", row["task_id"]),
                            )
                            summary["reconciled_failed"] += 1
                            log.warning("Task %s failed (idle at empty prompt after re-nudge)", row["task_id"])
                    elif task is not None:
                        base_branch = wf.get("workspace", "base_branch", default="master")
                        prompt = render_prompt(
                            wf.prompt_template,
                            _prompt_vars(
                                wf, task, row["worktree_path"], row["branch_name"],
                                base_branch, row["task_number"],
                            ),
                        )
                        send_tmux(row["window_name"], prompt)
                        conn.execute(
                            "UPDATE runs SET idle_nudged_at=? WHERE task_id=?",
                            (_now(), row["task_id"]),
                        )
                        summary["watchdog_renudged"] += 1
                        log.warning(
                            "Task %s idle at empty prompt; re-sent prompt to %s",
                            row["task_id"], row["window_name"],
                        )

        # 2. Keep done rows for the "recent runs" view; just cap history per workflow.
        _prune_done_rows(conn, str(wf.path))
        conn.commit()

        # 2b. Fire after_done hook (post-commit so the row state is durable in
        # case the hook restarts this very process — picoclaw self-dogfood
        # runs `pm2 restart picoclaw`). Detached via start_new_session=True so
        # the restart doesn't take down the current reconcile.
        if merged_in_tick:
            _fire_after_done(wf)

        # 3. Dispatch
        max_concurrent = wf.get("concurrency", "max", default=1) or 1
        active = conn.execute(
            "SELECT COUNT(*) FROM runs WHERE status IN ('claimed', 'running')"
        ).fetchone()[0]

        for task in open_tasks:
            if active >= max_concurrent:
                break
            existing = conn.execute(
                "SELECT status, attempt FROM runs WHERE task_id=?", (task.id,)
            ).fetchone()
            if existing:
                # Fresh tasks only: anything already in flight (claimed/running),
                # waiting on a human (awaiting_review), or already shipped (done)
                # is excluded. Only `failed` rows are eligible for a retry, and
                # only until MAX_ATTEMPTS.
                if existing["status"] in ("claimed", "running", "awaiting_review", "done"):
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

    # after_create hook — fires once, only when ensure_worktree freshly created
    # the worktree (skipped on idempotent re-dispatch into a reused worktree),
    # and BEFORE before_run. This is the seam for seeding a per-worktree
    # `.claude/settings.local.json` so an agent can launch *without*
    # --dangerously-skip-permissions. Unlike after_done (best-effort, detached),
    # a failure here is a hard dispatch abort: a missing settings file would
    # leave the agent hanging on its first permission prompt, so we'd rather
    # fail loudly. The command sees the same {{...}} template vars as the prompt
    # (e.g. {{workspace_path}}) so it can target the worktree.
    if created:
        after_create = wf.get("hooks", "after_create")
        if after_create:
            rendered = render_prompt(
                after_create,
                _prompt_vars(wf, task, str(worktree), branch, base_branch, task_number),
            )
            log.info("Running after_create hook for %s", task.id)
            subprocess.run(rendered, shell=True, cwd=worktree, check=True)

    # before_run hook
    before = wf.get("hooks", "before_run")
    if before:
        log.info("Running before_run hook for %s", task.id)
        subprocess.run(before, shell=True, cwd=worktree, check=True)

    # Claim the row before touching tmux so failure leaves a trace.
    conn.execute(
        """INSERT INTO runs (task_id, workflow_path, title, status, window_name, worktree_path, branch_name, started_at, attempt, task_number)
           VALUES (?, ?, ?, 'claimed', ?, ?, ?, ?, ?, ?)
           ON CONFLICT(task_id) DO UPDATE SET
             status='claimed', window_name=excluded.window_name,
             worktree_path=excluded.worktree_path, branch_name=excluded.branch_name,
             started_at=excluded.started_at, attempt=excluded.attempt, last_error=NULL,
             task_number=excluded.task_number, idle_nudged_at=NULL""",
        (task.id, str(wf.path), task.title, window_name, str(worktree), branch, _now(), attempt, task_number),
    )
    conn.commit()

    # Spawn tmux window. Kill any pre-existing window(s) of this name first so a
    # retry starts clean — tmux allows duplicate names, and a stacked second
    # window makes the by-name target ambiguous (the orphan-window bug).
    _kill_windows_named(window_name)

    # Create the window and capture its @id; target THIS spawn by id (not name)
    # for the agent-cmd, readiness poll, and prompt send so no residual same-name
    # window can make send-keys ambiguous and exit non-zero.
    target_id = _new_window(window_name, str(worktree))

    # Default to --permission-mode auto: a classifier auto-approves safe ops and
    # gates dangerous ones. NOT --dangerously-skip-permissions (reckless as an
    # OSS default). Override per-workflow via agent.cmd in WORKFLOW.md — a
    # power-user on a trusted repo can opt into `--permission-mode
    # bypassPermissions` for zero-hang autonomy. Auto-mode is a CLI flag only
    # (ignored from .claude/settings.json) and needs Opus/Sonnet 4.6+ on the
    # Anthropic API.
    agent_cmd = wf.get("agent", "cmd", default="claude --permission-mode auto")
    subprocess.run(
        ["tmux", "send-keys", "-t", f"{TMUX_SESSION}:{target_id}", agent_cmd, "Enter"],
        check=True, capture_output=True,
    )

    # Wait for the Claude Code TUI to be ready before sending the prompt. A
    # fixed sleep loses the prompt to the startup splash under load (the
    # send-keys lands mid-splash and is silently dropped), so poll the pane for
    # the ready indicator instead — honoring startup_delay_seconds only as a
    # minimum initial wait.
    min_wait = int(wf.get("agent", "startup_delay_seconds", default=4) or 4)
    ready_timeout = int(
        wf.get("agent", "ready_timeout_seconds", default=READY_TIMEOUT_SECONDS)
        or READY_TIMEOUT_SECONDS
    )
    if not _wait_for_ready(target_id, min_wait, ready_timeout):
        log.warning(
            "Task %s: window %s (%s) not ready after %ds; sending prompt anyway",
            task.id, window_name, target_id, ready_timeout,
        )

    prompt = render_prompt(
        wf.prompt_template,
        _prompt_vars(wf, task, str(worktree), branch, base_branch, task_number),
    )

    # Target by the captured @id (not the bare name) so a duplicate-named
    # window can't make the prompt send-keys ambiguous.
    if not send_tmux(target_id, prompt):
        raise RuntimeError(f"failed to send prompt to {window_name} ({target_id})")

    conn.execute(
        "UPDATE runs SET status='running' WHERE task_id=?", (task.id,)
    )
    conn.commit()
    log.info("Dispatched task %s → %s (attempt %d)", task.id, window_name, attempt)
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
    branch, prompt. The worktree path is the would-be path — no git worktree
    is created.

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
            "task_file_relative": _task_file_relative(task.file, repo_path),
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
        })
    return plans
