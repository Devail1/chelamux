"""🧵 CMX-287 — ``chela task show [<id>]``: a dispatched agent reads its own Linear issue.

The read half of Symphony's tracker tools (openai/symphony SPEC §11.5): this CLI runs with
chela's own Linear key, read from ``chela.env`` in THIS process, and the agent gets only the
printed result — the key never enters the agent's environment, and there is no write here.

What it prints: the issue's CURRENT title and description, then its comments oldest first
(author, timestamp), so an operator can steer a run with a comment instead of an edit to a
brief that was persisted at claim time. Only trusted comments are shown — the key's own
user plus the workflow's ``linear.comment_authors`` allowlist, never a bot or integration —
and the output says how many it dropped. A full page says TRUNCATED, and a failed read says
"could not fetch" and exits non-zero: unknown must never read as "nothing new".
"""
from __future__ import annotations

import os
import sqlite3
from pathlib import Path


def own_task_id(conn: sqlite3.Connection, cwd: Path, wid: str | None) -> str | None:
    """The run this process belongs to: the newest run whose worktree contains ``cwd``,
    else the newest run on window ``wid`` (``$CHELA_WID``). None when neither matches."""
    rows = conn.execute("SELECT task_id, worktree_path, window_id FROM runs "
                        "ORDER BY started_at DESC").fetchall()
    here = cwd.resolve()
    for row in rows:
        wt = row["worktree_path"]
        if wt and (here == Path(wt).resolve() or Path(wt).resolve() in here.parents):
            return row["task_id"]
    return next((row["task_id"] for row in rows if wid and row["window_id"] == wid), None)


def render(thread: dict) -> str:
    """The text ``chela task show`` prints for one :meth:`LinearSource.issue_thread`."""
    out = [f"{thread['identifier']}: {thread['title']}"]
    if thread.get("url"):
        out.append(thread["url"])
    out += ["", "## Description", "", thread["description"].strip() or "(empty)", "",
            f"## Comments ({len(thread['comments'])}, oldest first — the newest is the "
            "operator's latest steer)"]
    for c in thread["comments"]:
        user = c.get("user") or {}
        who = user.get("displayName") or user.get("name") or user.get("id") or "?"
        out += ["", f"--- {who} · {c.get('createdAt') or '?'}", str(c.get("body") or "").strip()]
    if not thread["comments"]:
        out += ["", "(none)"]
    if thread["dropped"]:
        out += ["", f"({thread['dropped']} comment(s) dropped: not from the API key's own user "
                "or `linear.comment_authors`, or posted by a bot/integration)"]
    if thread["truncated"]:
        out += ["", "⚠ TRUNCATED: Linear returned a full page of comments — some are not "
                "shown. Ask the orchestrator for the rest."]
    return "\n".join(out)


def show(task_id: str | None, *, cwd: Path | None = None, wid: str | None = None,
         source=None) -> tuple[int, str]:
    """``(exit code, text)`` for ``chela task show``; every failed read is exit 1."""
    from chela import dispatcher
    from chela.sources.linear import LinearError, LinearSource
    from chela.workflow import load_workflow

    try:
        with dispatcher._db() as conn:
            if not task_id:
                task_id = own_task_id(conn, cwd or Path.cwd(), wid or os.environ.get("CHELA_WID"))
            row = conn.execute("SELECT task_id, workflow_path FROM runs WHERE task_id=? "
                               "COLLATE NOCASE", (task_id.strip(),)).fetchone() if task_id else None
    except (sqlite3.Error, OSError) as e:
        return 1, f"task show: could not read the runs table: {e}"
    if not task_id:
        return 1, ("task show: no run owns this directory or $CHELA_WID — pass the task id "
                   "(`chela task show CMX-N`).")
    if row is None:
        return 1, f"task show: no run {task_id}."
    task_id = row["task_id"]
    try:
        wf = load_workflow(row["workflow_path"])
    except Exception as e:                   # an unreadable workflow is a failed read too
        return 1, f"⚠ could not fetch {task_id}: workflow unreadable ({type(e).__name__})"
    if wf.get("tracker", "kind", default=None) != "linear":
        return 1, f"task show: {task_id}'s tracker is not Linear — nothing to show."
    source = source or LinearSource(wf)
    if source.config_error:
        return 1, f"⚠ could not fetch {task_id}: {source.config_error}"
    raw = wf.get("linear", "comment_authors", default=None)
    trusted = [raw] if isinstance(raw, str) else raw if isinstance(raw, list) else []
    try:
        thread = source.issue_thread(task_id, frozenset(str(t) for t in trusted))
    except LinearError as e:
        return 1, (f"⚠ could not fetch {task_id} from Linear ({e}). The issue's current "
                   "description and comments are UNKNOWN — not empty.")
    return 0, render(thread)
