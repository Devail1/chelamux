"""Read Claude Code's per-session task list and join it to a dispatcher run — read-only
(issue #462: "surface it on the wall").

Every Claude Code session writes its structured TODO list to
``<claude config dir>/tasks/<session-id>/N.json`` — one small JSON file per task
(``{id, subject, description, status, blocks, blockedBy}``). Nothing in chela reads that
directory today; the dashboard's kanban knows a dispatched run only as one opaque unit.
This module is the reader, plus the wid→session join that lets a run's row find its own
list — never a write, rename, or delete: those files belong to Claude Code, not chela.

**Forward-compatible on purpose.** Agent teams use the identical schema at
``<claude config dir>/tasks/{team-name}/`` — only the directory KEY differs (a team name
instead of a session id), so :func:`read_tasks` is the reader a future team integration
needs too; it takes whatever key names the directory and does not care which kind it is.

**Why join on session id, and not the run's ``window_id`` directly.** tmux only ever
reissues a small ``@N`` after its SERVER restarts (:mod:`chela.epoch`) — i.e. at exactly
the moment a fresh window's ``session-ids.json`` entry overwrites the stale one sitting at
that address. A naive ``session-ids.json[wid]`` lookup would happily return the NEW
occupant's session id for an old run's row, because that entry's own recorded epoch now
matches the running server. What stops the misattribution is comparing the RUN's own
``window_epoch`` (stamped by the dispatcher the moment it wrote ``window_id`` — see
``dispatcher.py``) against the epoch running right now: a run dispatched under a dead
epoch never resolves a session id here again, no matter what a reused wid currently
points at. See :func:`resolve_session_id`.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

from chela.transcripts import claude_config_dir

log = logging.getLogger(__name__)

TASKS_DIR = claude_config_dir() / "tasks"


def read_tasks(key: str, base: Path | None = None) -> list[dict] | None:
    """Every valid task record under ``<base>/<key>/*.json``, in filename order.

    ``None`` when the directory does not exist at all — most Claude Code sessions never
    create one (issue #462's counterweight guard: 15 stale directories on the host that
    first measured this belong to long-dead runs), and this is the caller's signal to
    render exactly as if no task data had ever been asked for. An EMPTY directory (a
    session that has started but not yet written its first task) reads as ``[]``, not
    ``None`` — a real, if empty, list.

    A malformed or partially-written ``N.json`` is skipped, not fatal: these files are
    written live by Claude Code while a dispatched agent works, so a torn read here is
    normal, not exceptional, and must never blank the rest of the list.
    """
    root = (base or TASKS_DIR) / key
    if not root.is_dir():
        return None
    tasks: list[dict] = []
    for path in sorted(root.glob("*.json"), key=lambda p: p.name):
        try:
            obj = json.loads(path.read_text())
        except (OSError, ValueError):
            continue
        if not isinstance(obj, dict):
            continue
        task_id = obj.get("id")
        subject = obj.get("subject")
        if not isinstance(task_id, str) or not isinstance(subject, str) or not subject:
            continue
        status = obj.get("status")
        blocked_by = obj.get("blockedBy")
        tasks.append({
            "id": task_id,
            "subject": subject,
            "status": status if isinstance(status, str) else None,
            "blocked_by": [b for b in blocked_by if isinstance(b, str)] if isinstance(blocked_by, list) else [],
        })
    return tasks


def summarize(tasks: list[dict] | None) -> dict | None:
    """``{total, done, in_progress: {id, subject} | None, blocked: [...]}`` or ``None``.

    ``None`` for both "no directory" (``tasks is None``) and "a directory with no
    (valid) task files yet" (``tasks == []``) — a run must read identically in either
    case, which is the counterweight guard's whole point: no task data is no task data,
    never "0/0".

    ``blocked`` lists every task that still names an unmet ``blocked_by`` id, regardless
    of that task's own status — a caller wanting only the currently-actionable blockers
    can filter further, but this is the raw relationship Claude Code recorded.
    """
    if not tasks:
        return None
    done = sum(1 for t in tasks if t["status"] == "completed")
    in_progress = next((t for t in tasks if t["status"] == "in_progress"), None)
    blocked = [
        {"id": t["id"], "subject": t["subject"], "blocked_by": t["blocked_by"]}
        for t in tasks if t["blocked_by"]
    ]
    return {
        "total": len(tasks),
        "done": done,
        "in_progress": {"id": in_progress["id"], "subject": in_progress["subject"]} if in_progress else None,
        "blocked": blocked,
    }


def resolve_session_id(
    window_id: str | None,
    window_epoch: str | None,
    session_entries: dict,
    current_epoch: str | None,
) -> str | None:
    """The session id CURRENTLY live at ``window_id`` — but only when this run's own
    ``window_epoch`` (recorded the moment the dispatcher stamped ``window_id`` onto the
    run) still matches ``current_epoch``. See the module docstring for why this extra
    check is the join, not an optional belt-and-braces.

    ``session_entries`` is :func:`chela.sessionids.entries` — the caller fetches it (and
    ``current_epoch``) ONCE per request rather than this function doing its own tmux
    round trip per run.
    """
    if not window_id or not window_epoch or not current_epoch:
        return None
    if window_epoch != current_epoch:
        return None
    entry = session_entries.get(str(window_id))
    if not entry or entry.get("epoch") != current_epoch:
        return None
    session_id = entry.get("session_id")
    return session_id if isinstance(session_id, str) and session_id else None


def progress_for_run(
    run: dict,
    session_entries: dict,
    current_epoch: str | None,
    base: Path | None = None,
) -> dict | None:
    """:func:`summarize` of :func:`read_tasks` for a dispatcher run row, joined via
    :func:`resolve_session_id` — ``None`` whenever the run can't (or shouldn't) be
    joined to a task list: no window recorded yet, a dead epoch, or a session with no
    task directory. Exactly the "render as today" case the counterweight guard requires.
    """
    session_id = resolve_session_id(
        run.get("window_id"), run.get("window_epoch"), session_entries, current_epoch,
    )
    if not session_id:
        return None
    return summarize(read_tasks(session_id, base=base))
