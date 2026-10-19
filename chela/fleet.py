"""The fleet pause (CMX-214): ``chela dispatch --pause --all`` quiets every dispatched agent.

It takes the queue hold (:mod:`chela.hold`), then presses Escape in each dispatched agent's
window (never Ctrl+C: a second one at an idle prompt exits Claude Code) and sends one
message: start nothing until "resume". Only run rows are touched, never a human session. An
agent inside a command that mutates its worktree gets the message but NO Escape, so it
finishes that command first. The state is ``$CHELA_DIR/fleet-pause.json``; ``--resume``
claims it by an atomic rename, so two callers never both send "resume".
"""
from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path

from chela import config, inbox, messenger
from chela.hold import human_duration

log = logging.getLogger(__name__)

STATE_FILE_NAME = "fleet-pause.json"
SENDER = "chela"
# The brief's words, verbatim (Liav, 10-18). One message for both kinds of agent: one
# inside a mutating command gets it WITHOUT the Escape, finishes that command, then stops.
PAUSE_MESSAGE = ("Paused by the operator: start nothing (no tests, builds, installs, "
                 "hand-ins) until 'resume'. Note where you are.")
RESUME_MESSAGE = "resume: continue your plan"
# ``inbox._selfcheck_label`` labels that MUTATE the worktree (a plain pytest does not).
MUTATING = ("task-finished self-check", "judge sweep", "judge self-check")
# Every state may have stopped on our word, so each is owed a "resume".
INTERRUPTED, FINISHING, FAILED = "interrupted", "finishing", "failed"


def path() -> Path:
    return config.CHELA_DIR / STATE_FILE_NAME


def read(p: Path | None = None) -> dict | None:
    """``{"since", "by", "agents": {task_id: {"wid", "state", ...}}}``, or None."""
    p = p or path()
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as e:
        log.warning("fleet pause state at %s is unreadable (%s): send its agents 'resume' "
                    "by hand", p, e)
        return None
    if not isinstance(data, dict) or not isinstance(data.get("agents"), dict):
        log.warning("fleet pause state at %s is malformed: ignoring it", p)
        return None
    return data


def _active_runs() -> dict[str, dict]:
    from chela import dispatcher                     # lazy: dispatcher imports this module

    return {r["task_id"]: r for r in dispatcher.list_runs()
            if r.get("task_id") and r.get("status") in dispatcher.ACTIVE_STATUSES}


def _mutating(run: dict, procs: list) -> str | None:
    """The worktree-mutating command running in ``run``'s worktree, or None."""
    worktree = run.get("worktree_path")
    found = inbox._selfcheck_for(worktree, procs) if worktree else None
    what = (found or {}).get("what")
    return what if what in MUTATING else None


def _pause(wid: str, finishing: str | None) -> str:
    """Escape + the pause message, or the message alone for an agent ``finishing`` a
    worktree-mutating command. ``failed`` when any of it did not land."""
    ok = ((finishing or messenger.send_escape(wid))
          and messenger.send_message(SENDER, wid, PAUSE_MESSAGE))
    if not ok:
        log.warning("fleet pause: could not pause %s", wid)
    return (FINISHING if finishing else INTERRUPTED) if ok else FAILED


def pause_all(by: str = "") -> dict:
    """Pause every dispatched agent and record it. Call AFTER the hold is taken. Re-running
    it keeps the original ``since`` and does not message an agent already paused."""
    state = read() or {"since": time.time(), "by": by, "agents": {}}
    procs = inbox._selfcheck_scan()
    for task, run in _active_runs().items():
        if state["agents"].get(task, {}).get("state") in (INTERRUPTED, FINISHING):
            continue
        wid = inbox.run_wid(run)
        if not wid:
            continue                                 # no live window: nothing to pause
        what = _mutating(run, procs)
        state["agents"][task] = {"wid": wid, "state": _pause(wid, what),
                                 **({"finishing": what} if what else {})}
    config.CHELA_DIR.mkdir(parents=True, exist_ok=True)
    path().write_text(json.dumps(state) + "\n", encoding="utf-8")
    return state


def tick(held) -> list[str]:
    """The daemon's half, once per dispatch tick: with the hold gone (its TTL lapsed, or
    any surface released it), resume the fleet. Returns what it did, for the log."""
    if held or read() is None:
        return []
    resumed = resume_all()
    if resumed is None:
        return []
    sent = resumed["resumed"]
    failed = sorted(task for task, ok in sent.items() if not ok)
    lines = [f"fleet pause ENDED with the hold — sent 'resume' to "
             f"{len(sent) - len(failed)}/{len(sent)} agent(s)"]
    if failed:
        lines.append(f"fleet resume did NOT reach {', '.join(failed)}: send it 'resume' by hand")
    return lines


def resume_all() -> dict | None:
    """Send "resume" to every agent this pause stopped; returns ``{"since", "resumed":
    {task_id: delivered}}`` or None when no fleet pause was in force.

    The window is re-read from the run ROW: an agent whose run is no longer active is not
    messaged (its window may be someone else's now).
    """
    claimed = path().with_name(STATE_FILE_NAME + ".resuming")
    try:
        os.replace(path(), claimed)
    except FileNotFoundError:
        return None
    state = read(claimed)
    if state is None:
        # Kept: it is the only record of who was paused, for whoever resumes them by hand.
        log.warning("fleet pause state kept at %s", claimed)
        return None
    claimed.unlink(missing_ok=True)
    runs, resumed = _active_runs(), {}
    for task, agent in state["agents"].items():
        if task not in runs:
            continue
        wid = inbox.run_wid(runs[task])
        resumed[task] = bool(wid) and messenger.send_message(SENDER, wid, RESUME_MESSAGE)
    return {"since": state.get("since"), "resumed": resumed}


def status_lines(state: dict | None = None) -> list[str]:
    """What ``--hold-status`` prints about the fleet pause; empty when there is none."""
    state = read() if state is None else state
    if not state:
        return []
    since = state.get("since")
    ago = f" {human_duration(time.time() - since)} ago" if isinstance(since, float) else ""
    lines = [f"Fleet PAUSED{ago} by {state.get('by') or '?'} (--all): agents paused"]
    for task, agent in sorted(state["agents"].items()):
        what = f" {agent['finishing']}" if agent.get("finishing") else ""
        lines.append(f"  agent {task} @ {agent.get('wid')}: {agent.get('state')}{what}")
    return lines
