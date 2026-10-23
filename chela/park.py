"""A parked run: an agent held ON PURPOSE, which the stuck detector must not call hung.

10-22/23: the orchestrator held CMX-303's agent (waiting for a flaky-test fix, then for a
judge round to free memory) and the inbox sent six "looks STUCK" notices for it. A parked
agent and a hung one looked identical, so the notice became noise and a real hang would have
slipped through. Two things now park a run:

* **the orchestrator** (or the operator in a plain terminal): ``chela park <run> --reason
  "…" [--until <time|run:ID>]`` writes the ``parked_*`` columns on the run's row;
  ``chela unpark <run>`` clears them. The run keeps its slot and its window.
* **a suite-slot wait** (CMX-119 admission control): a run whose worktree holds an open
  wait in :mod:`chela.suite_guard`'s ledger is parked automatically — "waiting for a suite
  slot (holder: …)". Nothing is written for it; it is read from the ledger each time.

⛔ **An agent cannot park itself** (:func:`refusal`): parking is a way out of the stuck
detector, so a process inside the run's own worktree, or any Claude Code session that is
not the orchestrator, is refused.

A manual park is not forever: one ``run_park_overdue`` notice fires once it outlives its
``--until`` or :func:`chela.config.park_max_s` (:func:`overdue`). A park only counts while
the run is ``claimed``/``running``; a claim or a rework re-spawn clears it.
"""
from __future__ import annotations

import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

ACTIVE = ("claimed", "running")
TERMINAL = ("done", "closed", "failed")
MANUAL, SLOT = "manual", "slot"

_DURATION = re.compile(r"^(\d+)([smhd])$")
_RUN_EVENT = re.compile(r"^run:(\S+)$")
_UNIT_S = {"s": 1, "m": 60, "h": 3600, "d": 86400}


def parse_until(raw: str | None, now: datetime) -> str | None:
    """``--until`` as stored: an ISO-8601 UTC time, or ``run:<ID>`` (until that run ends).

    Accepts a duration (``90m``, ``2h``, ``1d``), an ISO-8601 time (a naive one is local
    time) or ``run:<ID>``. Anything else, or a time not in the future, is a ``ValueError``:
    a ``--until`` chela cannot check would silently mean "only the max"."""
    raw = (raw or "").strip()
    if not raw:
        return None
    if _RUN_EVENT.match(raw):
        return raw
    m = _DURATION.match(raw)
    if m:
        if int(m.group(1)) <= 0:
            raise ValueError(f"--until {raw!r} is not in the future")
        return (now + timedelta(seconds=int(m.group(1)) * _UNIT_S[m.group(2)])).isoformat()
    try:
        at = datetime.fromisoformat(raw)
    except ValueError:
        raise ValueError(f"--until {raw!r} is not a duration (90m, 2h, 1d), an ISO-8601 "
                         "time or run:<ID>") from None
    at = at.astimezone(timezone.utc) if at.tzinfo is None else at
    if at <= now:
        raise ValueError(f"--until {raw!r} is not in the future")
    return at.astimezone(timezone.utc).isoformat()


def _epoch(ts) -> float | None:
    if not ts:
        return None
    try:
        at = datetime.fromisoformat(str(ts))
    except ValueError:
        return None
    return (at if at.tzinfo else at.replace(tzinfo=timezone.utc)).timestamp()


def manual(run: dict) -> dict | None:
    """The orchestrator's park on ``run`` — ``{kind, reason, since, at, until, by}`` — or
    None. Only while the run is claimed/running: a row parked before it went to review is
    not a parked agent."""
    if run.get("status") not in ACTIVE or not run.get("parked_at"):
        return None
    return {"kind": MANUAL, "reason": run.get("parked_reason") or "",
            "since": _epoch(run.get("parked_at")), "at": run.get("parked_at"),
            "until": run.get("parked_until"), "by": run.get("parked_by")}


def slot_reason(holders: list[str]) -> str:
    return f"waiting for a suite slot (holder: {', '.join(holders) or 'unknown'})"


def slot(run: dict, wait: dict | None, holders: list[str]) -> dict | None:
    """A suite-slot wait as a park — or None when ``wait`` (``suite_guard.waits_in``) has no
    open wait."""
    since = (wait or {}).get("waiting_since")
    if run.get("status") not in ACTIVE or since is None:
        return None
    return {"kind": SLOT, "reason": slot_reason(holders), "since": float(since), "at": None,
            "until": None, "by": "suite slot"}


def state(run: dict) -> dict | None:
    """Why ``run`` is parked, read now: its manual park first, else an open suite-slot wait
    in its worktree. I/O (the slot ledger), so a tick calls it outside any lock."""
    park = manual(run)
    if park or run.get("status") not in ACTIVE or not run.get("worktree_path"):
        return park
    from chela import suite_guard
    wait = suite_guard.waits_in(run["worktree_path"])
    return slot(run, wait, suite_guard.holders()
                if (wait or {}).get("waiting_since") is not None else [])


def overdue(park: dict, now: float, runs_by_id: dict[str, dict], max_s: int) -> str | None:
    """Why a manual park has outlived itself — or None while it is still in date.

    Past its ``--until`` time; its ``--until run:<ID>`` run has ended (or is not a known
    run); or parked longer than ``max_s``. A park whose start or ``--until`` cannot be read
    is reported, never trusted: unknown is not in date."""
    since = park.get("since")
    if since is None:
        return f"its start ({park.get('at')!r}) cannot be read"
    until = park.get("until")
    if until:
        m = _RUN_EVENT.match(until)
        if m:
            other = runs_by_id.get(m.group(1))
            if other is None:
                return f"its --until run {m.group(1)} is not a known run"
            if other.get("status") in TERMINAL:
                return f"its --until run {m.group(1)} is {other.get('status')}"
        else:
            at = _epoch(until)
            if at is None:
                return f"its --until ({until!r}) cannot be read"
            if now >= at:
                return f"it is past its --until ({until})"
    if now - since >= max_s:
        return f"it is past the {max_s // 3600}h park max"
    return None


def refusal(run: dict, *, is_orchestrator: bool, env=None, cwd: str | None = None) -> str | None:
    """Why THIS process may not park ``run`` — or None if it may.

    A process inside the run's own worktree is that agent (or a shell it spawned); any other
    Claude Code session (``CLAUDECODE``) that is not the orchestrator is an agent too. Only
    the orchestrator, or the operator in a plain terminal, parks."""
    src = os.environ if env is None else env
    worktree = run.get("worktree_path")
    if worktree:
        try:
            here, root = Path(cwd or os.getcwd()).resolve(), Path(worktree).resolve()
        except OSError:
            return "cannot tell whether this process runs inside the run's own worktree"
        if here == root or root in here.parents:
            return (f"this process runs inside {run.get('task_id')}'s own worktree — an agent "
                    "cannot park itself (that is a way past the stuck detector)")
    if is_orchestrator:
        return None
    if src.get("CLAUDECODE"):
        return ("this process runs inside a Claude Code session that is not the orchestrator "
                "— only the orchestrator, or the operator in a plain terminal, parks a run")
    return None
