"""Keeper schedule — the daemon runs ``chela keeper run`` per keeper workflow (CMX-348).

Keeper mode (CMX-345), slice 3. A workflow with ``keeper: {enabled: true, every: 45m}`` gets
one ``chela keeper run <WORKFLOW.md>`` every ``every``, as a detached child the loop never
waits on. A run is SKIPPED, and stays owed, while:

* the queue is held (:func:`chela.hold.active`) — a hold means "change nothing";
* a run for that workflow is still in flight — at most one at a time;
* no session in the repo was active since the last run — no prompt and no edit from a live
  window launched in it (:func:`chela.keeper.live_windows`), so there is nothing to align.
  Re-checked at most every :data:`ACTIVITY_RECHECK_SECONDS`.

When each workflow last ran is ``$CHELA_DIR/keeper/schedule.json``. An unreadable one runs
NOTHING: a guess could start a second run while one is going.
"""
from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import time
from pathlib import Path

from chela import hold, keeper, keeper_run
from chela.workflow import keeper_every_seconds, load_workflow_cached

log = logging.getLogger(__name__)

ACTIVITY_RECHECK_SECONDS = 300


def state_path() -> Path:
    return keeper_run.keeper_dir() / "schedule.json"


def _read_state() -> dict:
    """``{workflow path: last run ts}``. Missing ⇒ ``{}``; unreadable raises ValueError."""
    try:
        data = json.loads(state_path().read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as e:
        raise ValueError(f"{state_path()} is unreadable: {e}") from e
    if not isinstance(data, dict):
        raise ValueError(f"{state_path()} is not a JSON object")
    return data


def _mark_ran(wf_path: str, now: float) -> None:
    state = _read_state()
    state[wf_path] = now
    path = state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(state), encoding="utf-8")
    os.replace(tmp, path)


def active_since(wf, since_ts: float) -> bool:
    """Whether a live session in ``wf``'s repo typed a prompt or edited a file since then."""
    _, roots = keeper.repo_roots(wf)
    events = keeper.read_events(None, since_ts)["events"]
    for window in keeper.live_windows(roots):
        prompts, paths, _ = keeper._activity(window, events, roots)
        if prompts or paths:
            return True
    return False


def spawn_argv(wf_path: Path) -> list[str]:
    return [sys.executable, "-m", "chela.main", "keeper", "run", str(wf_path)]


def spawn(wf_path: Path) -> subprocess.Popen:
    out = keeper_run.keeper_dir()
    out.mkdir(parents=True, exist_ok=True)
    with open(out / "run.log", "ab") as fh:
        return subprocess.Popen(spawn_argv(wf_path), stdin=subprocess.DEVNULL, stdout=fh,
                                stderr=subprocess.STDOUT, start_new_session=True,
                                close_fds=True)


class Scheduler:
    """The daemon loop's keeper half. One instance per daemon; ``tick`` once per pass."""

    def __init__(self, spawner=spawn, active=active_since):
        self.children: dict[str, subprocess.Popen] = {}
        self.next_check: dict[str, float] = {}     # inactive: when to look again
        self._spawn, self._active = spawner, active

    def tick(self, workflows, now: float | None = None) -> list[str]:
        """Start each keeper run that is due. Returns the workflow paths started."""
        now = time.time() if now is None else now
        if hold.active(now) is not None:
            return []
        started = []
        for wf_path in workflows:
            key = str(wf_path)
            wf = load_workflow_cached(wf_path).workflow
            every = keeper_every_seconds(wf) if wf is not None else None
            if every is None:
                continue
            child = self.children.get(key)
            if child is not None and child.poll() is None:
                continue
            if now < self.next_check.get(key, 0.0):
                continue
            try:
                last = _read_state().get(key)
            except ValueError as e:
                log.warning("keeper schedule: %s — no keeper run until it is fixed", e)
                return started
            if last is not None and not isinstance(last, (int, float)):
                log.warning("keeper schedule: %s holds %r for %s — not running it",
                            state_path(), last, key)
                continue
            if last is not None and now - last < every:
                continue
            since = now - every if last is None else last
            if not self._active(wf, since):
                self.next_check[key] = now + ACTIVITY_RECHECK_SECONDS
                continue
            self.next_check.pop(key, None)
            _mark_ran(key, now)
            log.info("keeper: starting a run for %s", key)
            self.children[key] = self._spawn(Path(wf_path))
            started.append(key)
        return started
