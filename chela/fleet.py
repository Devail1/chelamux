"""The fleet pause (CMX-214): ``chela dispatch --pause --all`` quiets every dispatched agent.

It takes the queue hold (:mod:`chela.hold`), then presses Escape in each dispatched agent's
window (never Ctrl+C: a second one at an idle prompt exits Claude Code) and sends one
message: start nothing until "resume". Only run rows are touched, never a human session. An
agent inside a command that mutates its worktree gets the message but NO Escape, so it
finishes that command first. The state is ``$CHELA_DIR/fleet-pause.json``; ``--resume``
claims it by an atomic rename, so two callers never both send "resume".

A running judge is FROZEN instead (SIGSTOP to its agent's and run's trees, SIGCONT on resume),
recorded first in its ledger so neither the watchdog nor the suite deadline counts frozen time.
"""
from __future__ import annotations

import json
import logging
import os
import signal
import time
from pathlib import Path

from chela import config, inbox, judge, messenger, procguard
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
    try:
        _freeze_judges(state)
    finally:                                       # every tree it stopped is on record
        config.CHELA_DIR.mkdir(parents=True, exist_ok=True)
        path().write_text(json.dumps(state) + "\n", encoding="utf-8")
    return state


def _signal(pids: list[int], sig: int) -> list[int]:
    """The pids ``sig`` reached; one that is gone is skipped, never fatal."""
    sent = []
    for pid in pids:
        try:
            os.kill(pid, sig)
        except OSError:
            continue
        sent.append(pid)
    return sent


def _freeze_judges(state: dict) -> None:
    """SIGSTOP every running judge's tree (one already frozen is left as it is)."""
    from chela import agent_manager, dispatcher

    judges = state.setdefault("judges", {})
    for run in dispatcher.list_runs():
        task = run.get("task_id")
        if not task or run.get("judge_state") != judge.J_RUNNING or task in judges:
            continue
        wid = run.get("judge_window_id")      # never "": tmux reads that as the ACTIVE pane
        roots = [agent_manager.claude_pid(wid) if wid else None,
                 (judge.live_judge_run(task) or {}).get("pid")]
        pids = procguard.process_tree([p for p in roots if isinstance(p, int)])
        if not pids:
            continue
        now = time.time()
        rnd, rec = run.get("judge_started_at"), judge.read_freeze(task)
        old = rec["intervals"] if rec.get("round") == rnd else []
        judge.write_freeze(task, {"round": rnd, "intervals": [*old, [now, None]]})  # then stop
        judges[task] = {"pids": _signal(pids, signal.SIGSTOP), "frozen_at": now}


def _thaw_judges(state: dict) -> dict[str, bool]:
    """Close each freeze's record, THEN SIGCONT: every frozen judge (a tree left in T leaks)."""
    thawed = {}
    for task, frozen in state.get("judges", {}).items():
        rec = judge.read_freeze(task)
        if rec.get("intervals") and rec["intervals"][-1][1] is None:
            rec["intervals"][-1][1] = time.time()
            rec["retry"] = 1                     # CMX-214: one model-call retry per thaw
            judge.write_freeze(task, rec)
        pids = frozen.get("pids") or []
        thawed[task] = _signal(pids, signal.SIGCONT) == pids
    return thawed


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
    lines += [f"fleet resume: judge {task} thawed" if ok else
              f"fleet resume: judge {task} NOT fully thawed: check its tree for state T"
              for task, ok in sorted(resumed["thawed"].items())]
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
    thawed = _thaw_judges(state)                   # before the record of who is frozen goes
    claimed.unlink(missing_ok=True)
    runs, resumed = _active_runs(), {}
    for task, agent in state["agents"].items():
        if task not in runs:
            continue
        wid = inbox.run_wid(runs[task])
        resumed[task] = bool(wid) and messenger.send_message(SENDER, wid, RESUME_MESSAGE)
    return {"since": state.get("since"), "resumed": resumed, "thawed": thawed}


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
    for task, frozen in sorted(state.get("judges", {}).items()):
        lines.append(f"  judge {task}: suspended {human_duration(time.time() - frozen['frozen_at'])}"
                     f" (SIGSTOP, {len(frozen.get('pids') or [])} process(es))")
    return lines
