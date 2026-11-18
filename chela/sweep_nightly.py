"""⚖️🧹🌙 CMX-303 — the NIGHTLY sweep over what the STANDARD tier merged without a judge.

CMX-296 (gate redesign 1b-i) lets a standard-tier PR — no core path, see
:mod:`chela.risktier` — merge on green CI with no judge round. This is the other half (1b-ii,
CMX-299, landing in three slices): once a day nothing merged that way goes un-mutated for long.
Slice 1/3 is the scheduler (CMX-303); slice 2/3 (CMX-304) groups the survivors and finds
every same-shape site; slice 3/3 (CMX-305) files them in the tracker and tells the inbox.

* **When** (CMX-400). ``sweep.mode: idle`` (the default) starts the sweep when the box is
  IDLE (:func:`box_idle`: no suite slot held — so no hand-in, self-check, judge or agent suite
  running —, nobody waiting for one, no fleet pause, and at least one suite's peak of memory
  available) and has stayed idle for ``sweep.quiet_seconds`` (default 600), so it never starts
  between two hand-ins. At most ONE day's run per workflow: once it ends without yielding the
  day is done; a run that YIELDED may resume the same day when idle again, until the day's
  ``sweep.budget_seconds`` (a daily TOTAL) is spent. If no idle window came by the FALLBACK
  time — ``sweep.nightly_at`` (default ``02:00``) plus ``sweep.fallback_after_hours`` (default
  3: 05:00) — and nothing ran today, it runs anyway on ``sweep.fallback_budget_seconds``
  (default 1800), still yielding, and the notice says so — but only inside its window
  (``sweep.fallback_window_hours``, default 2: 05:00–07:00), never later in the day.
* **Held off** (CMX-436). Never started while ``chela dispatch --pause`` holds the queue
  (until its TTL), nor inside ``sweep.blocked_hours`` (local ``HH:MM-HH:MM``, wrapping past
  midnight allowed; default ``03:00-04:30``, the host's cron window); a fallback that falls
  inside a window moves to its end. A daemon-started run yields for either between two
  mutations, as for an agent, and the notice says which. ``sweep.mode: fixed`` is the old
  clock: once a day from ``nightly_at``. ``nightly_at: off`` (``never``, ``false``, or
  ``sweep.enabled: false``) turns either off. The daemon (:func:`daemon_tick`) spawns ``chela
  judge nightly-sweep`` DETACHED, one at a time (concurrency 1, and a lock file for a hand-run
  one), outside the dispatch slots, inside the shared memory slice the judge and the agents
  launch into (:func:`chela.memcap.wrap_launch_cmd`). A day is marked BEFORE the spawn, so a
  restart never sweeps the same day twice; a run that never reported back ends its day.
* **Yields** (CMX-400). The sweep holds the one suite slot for its whole battery (CMX-393).
  Between two mutations — the last one's file already restored — it asks who is waiting for
  the slot (:func:`chela.suite_guard.waiters`); anyone stops it there: the slot goes to them,
  the run ends as YIELDED, and every mutation it did not reach is OWED (below), so the next
  run starts there. Both modes yield.
* **What.** It fetches ``origin`` and reads ``origin/<workspace.base_branch>``. Every
  first-parent commit on it in the covered window whose files classify STANDARD (the same
  :func:`chela.risktier.classify` the merge gate uses — a core or always-human PR met the judge
  or a human already, a prose one has nothing to mutate). A root commit is diffed against the
  empty tree. The lines swept are the ones that STILL blame to one of those commits at the tip,
  so a line a later core PR rewrote is not swept twice. The checkout is provisioned first
  (:func:`chela.judge.provision_suite_env`: venv and node_modules — CMX-325). ONE battery, through
  :func:`chela.sweep.sweep` — the judge's own mechanics, ``judge.select_tests``, suite timeout
  and budget. A mutation the budget never reached is UNRUN and a file no mutator handles is
  UNKNOWN: a night with either and no survivor says so, never ``clean``.
* **Owed** (CMX-374). The budget is spent in the same order every night, so a mutation it never
  reached would hit the same wall again, and once the window moves past its PR nothing would
  ever measure its line. Each UNRUN mutation (file, line, kind, its line before and after, the
  PR its line blames to) is OWED, in ``$CHELA_DIR/sweep-nightly/owed.json``, and the next night
  measures the owed ones FIRST, inside the same budget — on a night with no new merge too. One
  leaves the set only once it ran (killed or survived; a survivor whose filing failed stays
  owed) or no line of its file reads as it did any more (``line_gone``) — the notice says
  which. An unreadable store is CANNOT VERIFY, never "nothing owed".
* **The covered window.** The last 24 h — or, after a night that could not look, back to the
  last run that did (at most :data:`MAX_CATCHUP_SECONDS`). An idle run (CMX-400) that comes
  more than 24 h after the last one is caught up the same way; a same-day resume measures its
  OWED mutations first, so the window it repeats only gets what budget is left. Only a run that looked and
  finished (not CANNOT VERIFY, no refused filing, every UNRUN mutation owed) moves it, in ``$CHELA_DIR/sweep-nightly/
  covered.json``.
* **Grouped.** One :class:`SurvivorGroup` per CATEGORY — mutation shape (its kind) plus target
  (the guard line's stripped text) — however many sites survived it. Each group lists EVERY
  same-shape site in the checkout: a FIXED-STRING ``git grep -F`` of the stripped line, so a
  regex metacharacter in it never drops a site. A group whose sites cannot be listed, or whose
  listing misses one of its own survivors, is not filed (fail closed; the next night retries).
* **Filed.** Each group goes to the :class:`Filer` unless EVERY survivor in it was filed
  before; a new site of a filed category files the group again. A survivor already filed is never
  filed again on its own: its key — workflow, file, kind, line text before and after, no line number — is
  kept in ``$CHELA_DIR/sweep-nightly/filed.json``, which outlives restarts and is written after
  EACH filing. The default filer is :class:`TrackerFiler`: ONE tracker issue per group, labelled
  :data:`LABELS` (``risk:normal``, ``sweep``), listing its survivors and its sites. A tracker that
  cannot create issues gets :class:`WouldFile`, which files nothing and records what it would
  have filed.
* **Told.** ONE inbox notice per run (:func:`summary`): what was filed, clean, or skipped and
  why, and the UNRUN / UNKNOWN counts. A run that filed nothing says so in that line. A night
  with no standard-tier merge (:data:`NO_MERGES`) is the one exception (CMX-371): nobody can
  act on it, so it is logged and appended to the event log, never queued to the inbox.
* **Recorded.** EVERY run — quiet or not — writes its time, status and reason to
  ``$CHELA_DIR/sweep-nightly/runs.json`` (:func:`record_run`). ``chela doctor``'s
  ``sweep.nightly_last_run`` reads it: a last run older than :data:`STALE_SECONDS` is red, so a
  dead scheduler cannot hide behind quiet nights.
* **A red baseline** (CMX-341) names every failing test (the first few, plus a count) with the
  first line of each, and keeps its JUnit report and output tail in
  ``$CHELA_DIR/sweep-nightly/<date>/<project>/`` (the newest :data:`KEEP_DAYS` dates) — the
  checkout is gone by morning. Its failing tests are re-run ONCE alone: green there is a
  ``baseline flake``, deselected from every mutation run, and the battery goes on.
* **Skipped** while a fleet pause (``chela dispatch --pause --all``) is active.
"""
from __future__ import annotations

import fcntl
import hashlib
import json
import logging
import os
import re
import shlex
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Protocol

from chela import config, judge, judge_timing, risktier, sweep
from chela.workflow import dispatch_enabled

log = logging.getLogger(__name__)

DEFAULT_AT = (2, 0)
WINDOW_SECONDS = 24 * 3600
# A night that could not look (a pause, a failed fetch, a red baseline) is caught up by the next
# one — from the last run that DID look, at most this far back.
MAX_CATCHUP_SECONDS = 7 * 24 * 3600
LABELS = ("risk:normal", "sweep")
MAX_SITES = 100           # same-shape sites listed in one issue; the rest are counted
INBOX_KIND = "nightly_sweep"
STATE_DIR_NAME = "sweep-nightly"
FLEET_PAUSE_FILE = "fleet-pause.json"      # chela.fleet.STATE_FILE_NAME (CMX-214)
GIT_TIMEOUT = 120
EMPTY_TREE = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"
KEEP_DAYS = 14            # dated red-baseline evidence dirs kept; older ones are pruned

SURVIVORS, CLEAN, SKIPPED, CANNOT_VERIFY = "survivors", "clean", "skipped", "cannot_verify"
# No survivor, but not clean either: the budget left mutations UNRUN, or a merged file is not
# mutatable (UNKNOWN). Neither is ever reported as clean.
UNRUN, UNKNOWN = "unrun", "unknown"
# CMX-371: why a run was SKIPPED, as a key — what the notice branches on, never the prose `why`.
FLEET_PAUSED, JUDGE_OFF, ALREADY_RUNNING, NO_MERGES = (
    "fleet_paused", "judge_off", "already_running", "no_merges")
# ⏳ CMX-436: what holds the sweep off (no start; a running one yields) — a dispatch pause
# (`chela dispatch --pause`) or one of `sweep.blocked_hours`. Also SKIPPED reasons.
DISPATCH_PAUSED, BLOCKED_HOURS = "dispatch_paused", "blocked_hours"
# ⏳ CMX-400: a SKIPPED run for one of these swept nothing and the cause passes — it does not
# end the idle day (the box reads busy meanwhile, so it is not retried in a loop).
RETRY_REASONS = frozenset({FLEET_PAUSED, ALREADY_RUNNING, DISPATCH_PAUSED, BLOCKED_HOURS})
# A SKIPPED reason no human can act on: recorded and logged, never queued to the inbox.
QUIET_REASONS = frozenset({NO_MERGES})
# `chela doctor` reads a last recorded run older than this as a dead scheduler (a day plus slack).
STALE_SECONDS = 36 * 3600

# ⏳ CMX-400: what started a run — the sweep.mode values and the fallback.
IDLE, FIXED, FALLBACK = "idle", "fixed", "fallback"
MODES = (IDLE, FIXED)
DEFAULT_QUIET_SECONDS = 600            # the box stays idle this long before an idle start
DEFAULT_FALLBACK_AFTER_HOURS = 3.0     # nightly_at + this: the fallback start (02:00 → 05:00)
DEFAULT_FALLBACK_WINDOW_HOURS = 2.0    # the fallback fires only this long after its start
DEFAULT_FALLBACK_BUDGET_SECONDS = 1800.0
BUDGET_SPENT = "budget_spent"
# ⏳ CMX-436: local hours the sweep never starts in (and yields at): the host's own cron load —
# ~25 memory-capped nautilus jobs between 03:03 and 04:17 on the reference box.
DEFAULT_BLOCKED_HOURS = ("03:00-04:30",)
DAY_MINUTES = 24 * 60
# ⚡ CMX-436: a mutations/hour figure only above this sample — n=1 in 0.0 h rates nothing.
MIN_RATE_MUTATIONS = 10
MIN_RATE_SECONDS = 600.0

_RE_AT = re.compile(r"^([01]?\d|2[0-3]):([0-5]\d)$")
_RE_SPAN = re.compile(r"^([01]?\d|2[0-3]):([0-5]\d)\s*-\s*([01]?\d|2[0-3]):([0-5]\d)\Z")
_RE_BLAME = re.compile(r"^([0-9a-f]{40}) \d+ (\d+)(?: \d+)?\Z")
_RE_DAY = re.compile(r"^\d{4}-\d{2}-\d{2}\Z")


class _Git(Exception):
    pass


def state_dir(chela_dir: Path | None = None) -> Path:
    return Path(chela_dir or config.CHELA_DIR) / STATE_DIR_NAME


# --- when ---------------------------------------------------------------------------------


def nightly_at(wf) -> tuple[int, int] | None:
    """``(hour, minute)`` from ``sweep.nightly_at``; ``None`` when the nightly sweep is off. An
    unset value is :data:`DEFAULT_AT`; so is a malformed one, said in the log — a typo must
    not silently stop the only thing that mutation-tests standard-tier merges."""
    if wf.get("sweep", "enabled", default=True) is False:
        return None
    raw = wf.get("sweep", "nightly_at", default=None)
    if raw is None:
        return DEFAULT_AT
    if raw is False or (isinstance(raw, str) and raw.strip().lower() in ("off", "never")):
        return None
    if isinstance(raw, int) and not isinstance(raw, bool) and 0 <= raw < 24 * 60:
        return divmod(raw, 60)     # YAML 1.1 reads an unquoted `2:30` as sexagesimal 150
    m = _RE_AT.match(str(raw).strip())
    if m:
        return int(m.group(1)), int(m.group(2))
    log.warning("%s: sweep.nightly_at %r is not HH:MM — using %02d:%02d", wf.path, raw,
                *DEFAULT_AT)
    return DEFAULT_AT


def _read_json(path: Path) -> dict:
    """``{}`` for a missing file. Raises ``ValueError`` for one that exists but cannot be read
    as a JSON object — the caller decides what an unknown means."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as e:
        raise ValueError(f"{path} is unreadable: {e}") from e
    if not isinstance(data, dict):
        raise ValueError(f"{path} does not hold a JSON object")
    return data


def _write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def _last_path(chela_dir: Path | None) -> Path:
    return state_dir(chela_dir) / "last.json"


def due(wf, now: datetime, chela_dir: Path | None = None) -> bool:
    """Is today's sweep of ``wf`` due at ``now`` (local)? Past ``nightly_at`` and not yet
    started today. An unreadable day record is NOT due — never a second sweep on a guess."""
    if not dispatch_enabled(wf):
        return False                  # CMX-348: a keeper-only workflow merged nothing
    at = nightly_at(wf)
    if at is None or (now.hour, now.minute) < at:
        return False
    try:
        last = _read_json(_last_path(chela_dir))
    except ValueError as e:
        log.warning("nightly sweep: %s — not sweeping until it is fixed or removed", e)
        return False
    return last.get(str(wf.path)) != now.date().isoformat()


def mark_started(wf, now: datetime, chela_dir: Path | None = None) -> None:
    path = _last_path(chela_dir)
    try:
        last = _read_json(path)
    except ValueError:
        last = {}
    last[str(wf.path)] = now.date().isoformat()
    _write_json(path, last)


def evidence_dir(wf, now: float, chela_dir: Path | None = None) -> Path:
    """Where tonight's red-baseline evidence for ``wf`` goes: ``<state>/<date>/<project>``."""
    day = datetime.fromtimestamp(now).date().isoformat()
    return state_dir(chela_dir) / day / (Path(wf.path).parent.name or "workflow")


def prune_evidence(chela_dir: Path | None = None, keep: int = KEEP_DAYS) -> list[str]:
    """Remove all but the newest ``keep`` dated evidence dirs; returns what went. A dir that
    cannot be removed is logged and left — pruning never fails a night."""
    try:
        days = sorted((d for d in state_dir(chela_dir).iterdir()
                       if d.is_dir() and _RE_DAY.match(d.name)), key=lambda d: d.name)
    except OSError:
        return []
    gone = []
    for d in days[:max(len(days) - keep, 0)]:
        try:
            shutil.rmtree(d)
        except OSError as e:
            log.warning("nightly sweep: could not prune %s: %s", d, e)
            continue
        gone.append(d.name)
    return gone


def _covered_path(chela_dir: Path | None) -> Path:
    return state_dir(chela_dir) / "covered.json"


def window_start(wf, now: float, chela_dir: Path | None = None) -> float:
    """The oldest merge time swept: the last 24 h — or, after a night that could not look,
    back to the last run that did (at most :data:`MAX_CATCHUP_SECONDS`). An unreadable record
    is the plain 24 h, said in the log."""
    since = now - WINDOW_SECONDS
    try:
        last = _read_json(_covered_path(chela_dir)).get(str(wf.path))
    except ValueError as e:
        log.warning("nightly sweep: %s — sweeping the last 24h only", e)
        return since
    if isinstance(last, (int, float)) and not isinstance(last, bool) and last < since:
        since = max(float(last), now - MAX_CATCHUP_SECONDS)
    return since


def _record_covered(wf, now: float, chela_dir: Path | None) -> None:
    path = _covered_path(chela_dir)
    try:
        covered = _read_json(path)
    except ValueError:
        covered = {}
    covered[str(wf.path)] = now
    _write_json(path, covered)


def spawn_argv(wf_path: Path, trigger: str | None = None) -> list[str]:
    return [sys.executable, "-m", "chela.main", "judge", "nightly-sweep", "--workflow",
            str(wf_path)] + (["--trigger", trigger] if trigger else [])


def spawn(wf_path: Path, chela_dir: Path | None = None,
          trigger: str | None = None) -> subprocess.Popen:
    """Start ``chela judge nightly-sweep`` detached, in the judge's shared memory slice."""
    from chela import memcap

    out = state_dir(chela_dir)
    out.mkdir(parents=True, exist_ok=True)
    cmd = memcap.wrap_launch_cmd(shlex.join(spawn_argv(wf_path, trigger)))
    with open(out / "nightly.log", "ab") as fh:
        return subprocess.Popen(["sh", "-c", cmd], stdin=subprocess.DEVNULL, stdout=fh,
                                stderr=subprocess.STDOUT, start_new_session=True,
                                close_fds=True)


# --- when, in idle mode (CMX-400) -----------------------------------------------------------


def _positive_number(wf, key: str, default: float) -> float:
    """``sweep.<key>`` as a positive number, else ``default`` (said in the log when set)."""
    raw = wf.get("sweep", key, default=None)
    if raw is None:
        return default
    n = sweep._positive(raw, float)
    if n is None:
        log.warning("%s: sweep.%s %r is not a positive number — using %g", wf.path, key, raw,
                    default)
        return default
    return n


def sweep_mode(wf) -> str:
    """``sweep.mode``: :data:`IDLE` (unset — the default) or :data:`FIXED`. Anything else is
    idle, said in the log."""
    raw = wf.get("sweep", "mode", default=None)
    if raw is None:
        return IDLE
    mode = str(raw).strip().lower()
    if mode in MODES:
        return mode
    log.warning("%s: sweep.mode %r is not idle|fixed — using idle", wf.path, raw)
    return IDLE


def quiet_seconds(wf) -> float:
    return _positive_number(wf, "quiet_seconds", DEFAULT_QUIET_SECONDS)


def fallback_budget_seconds(wf) -> float:
    return _positive_number(wf, "fallback_budget_seconds", DEFAULT_FALLBACK_BUDGET_SECONDS)


def fallback_at(wf) -> tuple[int, int] | None:
    """``(hour, minute)`` of the fallback start: ``nightly_at`` + ``sweep.fallback_after_hours``,
    at most 23:59 (a fallback never moves to the next day) — and, when that falls inside one of
    ``sweep.blocked_hours``, moved to the window's END (CMX-436). ``None`` when the sweep is
    off, or when the only end left is past midnight."""
    start = _fallback_minute(wf)
    if start is None:
        return None
    windows = blocked_hours(wf)
    for _ in range(len(windows) + 1):       # a window's end may sit inside another one
        hit = next((w for w in windows if _inside(w, start)), None)
        if hit is None:
            return divmod(start, 60)
        if hit[1] <= start:
            # Its end is tomorrow: a fallback never moves to the next day.
            log.warning("%s: the sweep fallback falls in blocked hours %s that end after "
                        "midnight — no fallback", wf.path, _span(hit))
            return None
        start = hit[1]
    return None


def fallback_window(wf) -> tuple[int, int] | None:
    """⏳ CMX-436: ``(start, end)`` minutes since midnight — the fallback fires ONLY in here,
    never "any time later on a day with no run" (11-18: a fallback fired at 16:35 and took the
    slot from agents in working hours). Start: :func:`fallback_at`; end:
    ``sweep.fallback_window_hours`` (default 2) later, at most midnight."""
    at = fallback_at(wf)
    if at is None:
        return None
    start = at[0] * 60 + at[1]
    hours = _positive_number(wf, "fallback_window_hours", DEFAULT_FALLBACK_WINDOW_HOURS)
    return start, min(start + int(hours * 60), DAY_MINUTES)


def _fallback_minute(wf) -> int | None:
    at = nightly_at(wf)
    if at is None:
        return None
    raw = wf.get("sweep", "fallback_after_hours", default=None)
    hours = DEFAULT_FALLBACK_AFTER_HOURS
    if raw is not None:
        # 0 is a valid choice here: the fallback at nightly_at itself.
        if isinstance(raw, bool) or not isinstance(raw, (int, float)) or raw < 0:
            log.warning("%s: sweep.fallback_after_hours %r is not a number >= 0 — using %g",
                        wf.path, raw, DEFAULT_FALLBACK_AFTER_HOURS)
        else:
            hours = float(raw)
    return min(at[0] * 60 + at[1] + int(hours * 60), DAY_MINUTES - 1)


def _span(window: tuple[int, int]) -> str:
    return "%02d:%02d-%02d:%02d" % (*divmod(window[0], 60), *divmod(window[1] % DAY_MINUTES, 60))


def _parse_span(raw: object) -> tuple[int, int] | None:
    m = _RE_SPAN.match(raw.strip()) if isinstance(raw, str) else None
    if not m:
        return None
    start = int(m.group(1)) * 60 + int(m.group(2))
    end = int(m.group(3)) * 60 + int(m.group(4))
    return (start, end) if start != end else None


def blocked_hours(wf) -> list[tuple[int, int]]:
    """⏳ CMX-436: ``sweep.blocked_hours`` — local ``"HH:MM-HH:MM"`` windows, as ``(start, end)``
    minutes since midnight; ``end < start`` wraps past midnight. Unset is
    :data:`DEFAULT_BLOCKED_HOURS`; ``[]`` (or ``off``) is none. A list with an entry that does
    not parse is the DEFAULT, said in the log — a typo must not quietly unblock the host's
    heaviest hours."""
    raw = wf.get("sweep", "blocked_hours", default=None)
    if raw is None:
        raw = list(DEFAULT_BLOCKED_HOURS)
    elif raw is False or (isinstance(raw, str) and raw.strip().lower() in ("off", "never")):
        return []
    elif isinstance(raw, str):
        raw = [raw]
    spans = [_parse_span(r) for r in raw] if isinstance(raw, list) else [None]
    if any(s is None for s in spans):
        log.warning("%s: sweep.blocked_hours %r is not a list of HH:MM-HH:MM — using %s",
                    wf.path, raw, list(DEFAULT_BLOCKED_HOURS))
        return [_parse_span(r) for r in DEFAULT_BLOCKED_HOURS]
    return spans


def _inside(window: tuple[int, int], minute: int) -> bool:
    start, end = window
    if start < end:
        return start <= minute < end
    return minute >= start or minute < end            # wraps past midnight


def blocked_window(wf, now: datetime) -> str:
    """The ``sweep.blocked_hours`` window ``now`` (local) is in, as ``"HH:MM-HH:MM"``; ``""``
    when none."""
    minute = now.hour * 60 + now.minute
    return next((_span(w) for w in blocked_hours(wf) if _inside(w, minute)), "")


def dispatch_paused(chela_dir: Path | None = None, now: float | None = None) -> bool:
    """⏳ CMX-436: is a ``chela dispatch --pause`` hold in force (until its TTL)? The operator
    paused the queue to keep the box QUIET, and a quiet queue must not read as an invitation.
    A hold file that cannot be read IS a pause here (fail closed, said in the log): unknown
    never reads as "go ahead"."""
    from chela import hold

    path = Path(chela_dir or config.CHELA_DIR) / hold.HOLD_FILE_NAME
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        expires = float(data["expires_at"])
    except FileNotFoundError:
        return False
    except (OSError, ValueError, TypeError, KeyError) as e:
        log.warning("nightly sweep: the dispatch hold %s is unreadable (%s) — holding off", path,
                    e)
        return True
    return expires > (time.time() if now is None else now)


def hold_off(wf, now: datetime, chela_dir: Path | None = None) -> tuple[str, str]:
    """⏳ CMX-436: ``(reason, what)`` — why the sweep must not run at ``now`` (no start; a
    running one yields): :data:`DISPATCH_PAUSED` or :data:`BLOCKED_HOURS`, with the words
    the notice uses. ``("", "")`` when nothing holds it off."""
    if dispatch_paused(chela_dir, now.timestamp()):
        return DISPATCH_PAUSED, "dispatch paused"
    window = blocked_window(wf, now)
    if window:
        return BLOCKED_HOURS, f"blocked hours {window}"
    return "", ""


def box_idle(chela_dir: Path | None = None) -> tuple[bool, str]:
    """``(idle, why not)``: is the box free for a sweep NOW? No suite slot held (no hand-in,
    self-check, judge or agent suite running), nobody waiting for one, no fleet or dispatch
    pause (CMX-436: a released pause starts the quiet period over), and at
    least one suite's measured peak of memory available (CMX-393's
    :data:`chela.suite_guard.RESUME_MEM_BYTES`). Memory that cannot be read is NOT idle."""
    from chela import suite_guard

    if paused(Path(chela_dir or config.CHELA_DIR)):
        return False, "a fleet pause is active"
    if dispatch_paused(chela_dir):
        return False, "dispatch is paused"
    held = suite_guard.holders()
    if held:
        return False, f"the suite slot is held by {', '.join(held)}"
    waiting = suite_guard.waiters()
    if waiting:
        return False, f"{', '.join(waiting)} waiting for the suite slot"
    avail = suite_guard.mem_available_bytes()
    if avail is None or avail < suite_guard.RESUME_MEM_BYTES:
        return False, "memory headroom is below one suite's peak"
    return True, ""


class IdleWatch:
    """How long the box has been idle without a break — the quiet period an idle start
    waits for. Any busy reading starts it over."""

    def __init__(self):
        self.since: float | None = None

    def observe(self, idle: bool, now: float) -> float:
        if not idle:
            self.since = None
            return 0.0
        if self.since is None:
            self.since = now
        return now - self.since

    def reset(self) -> None:
        self.since = None


_WATCH = IdleWatch()


def _day_path(chela_dir: Path | None) -> Path:
    return state_dir(chela_dir) / "day.json"


def read_day(wf, day: str, chela_dir: Path | None = None) -> dict:
    """``wf``'s record of ``day`` (``{used, runs, pending, done, …}``), or ``{}`` when it has
    none for that day. Raises ``ValueError`` for a store that cannot be read."""
    rec = _read_json(_day_path(chela_dir)).get(str(wf.path))
    if rec is not None and not isinstance(rec, dict):
        raise ValueError(f"{_day_path(chela_dir)} holds a malformed day for {wf.path}")
    return rec if rec and rec.get("date") == day else {}


def _pending(wf, chela_dir: Path | None) -> dict:
    """``wf``'s record while the daemon's spawn is pending, whatever its date — a run spawned
    at 23:59 that starts after midnight is still charged to the day it was started for.
    ``{}`` when none is pending; raises ``ValueError`` for an unreadable store."""
    rec = _read_json(_day_path(chela_dir)).get(str(wf.path))
    return rec if isinstance(rec, dict) and rec.get("pending") else {}


def _used(rec: dict) -> float:
    used = rec.get("used")
    return float(used) if isinstance(used, (int, float)) and not isinstance(used, bool) else 0.0


def start_reason(wf, now: datetime, quiet_for: float, chela_dir: Path | None = None,
                 ) -> str | None:
    """Why ``wf``'s sweep starts at ``now`` — :data:`IDLE`, :data:`FALLBACK` or
    :data:`FIXED` — or ``None``. ``quiet_for``: how long the box has been idle. An
    unreadable day record starts nothing — never a second sweep on a guess. CMX-436: nothing
    starts while :func:`hold_off` (a dispatch pause, blocked hours), and the fallback fires
    only inside :func:`fallback_window`."""
    if sweep_mode(wf) == FIXED:
        # CMX-436: a held-off fixed start waits (still due) until the hold passes.
        return FIXED if due(wf, now, chela_dir) and not hold_off(wf, now, chela_dir)[0] \
            else None
    if not dispatch_enabled(wf) or nightly_at(wf) is None:
        return None
    try:
        rec = read_day(wf, now.date().isoformat(), chela_dir)
    except ValueError as e:
        log.warning("nightly sweep: %s — not sweeping until it is fixed or removed", e)
        return None
    # A run still pending (or one that never reported back) and a day that ran to its end
    # are both the day's one run; a yielded day goes on until its budget is spent.
    if rec.get("pending") or rec.get("done") or \
            (rec and _used(rec) >= nightly_budget(wf).seconds):
        return None
    if hold_off(wf, now, chela_dir)[0]:
        return None             # CMX-436: a dispatch pause or blocked hours — no start at all
    if quiet_for >= quiet_seconds(wf):
        return IDLE
    win = fallback_window(wf)
    if not rec and win is not None and win[0] <= now.hour * 60 + now.minute < win[1]:
        return FALLBACK
    return None


def mark_day_started(wf, now: datetime, trigger: str, chela_dir: Path | None = None) -> None:
    """Record, BEFORE the spawn, that today's run of ``wf`` started (``pending`` until the
    run reports back in :func:`finish_day`). An unreadable store is rewritten."""
    path = _day_path(chela_dir)
    try:
        store = _read_json(path)
        rec = read_day(wf, now.date().isoformat(), chela_dir)
    except ValueError:
        store, rec = {}, {}
    store[str(wf.path)] = {"date": now.date().isoformat(), "used": _used(rec),
                           "runs": int(rec.get("runs") or 0) + 1, "pending": True,
                           "done": False, "trigger": trigger}
    _write_json(path, store)


def finish_day(wf, result: dict, chela_dir: Path | None = None) -> None:
    """The run's report back to today's record: the seconds it used join the day's total, and
    the day is DONE unless it yielded or skipped for a passing cause (:data:`RETRY_REASONS`).
    Never raises: the record is not the work."""
    path = _day_path(chela_dir)
    try:
        try:
            store = _read_json(path)
        except ValueError:
            store = {}
        rec = store.get(str(wf.path))
        if not isinstance(rec, dict) or not rec.get("pending"):
            rec = {"date": datetime.fromtimestamp(result["at"]).date().isoformat(),
                   "runs": 1}
        store[str(wf.path)] = {**rec, "used": _used(rec) + float(result.get("sweep_seconds")
                                                                  or 0.0),
                               "pending": False,
                               "done": not (result.get("yielded_to")
                                            or result.get("reason") in RETRY_REASONS),
                               "trigger": result.get("trigger"), "status": result["status"]}
        _write_json(path, store)
    except Exception:  # noqa: BLE001 — logged; a pending record ends the day, never repeats it
        log.exception("nightly sweep: could not record the day in %s", path)


def daemon_tick(workflows, prev: subprocess.Popen | None, now: datetime | None = None,
                chela_dir: Path | None = None, probe=None,
                watch: IdleWatch | None = None) -> subprocess.Popen | None:
    """The daemon's half, once per loop: spawn the first workflow whose sweep should start
    (:func:`start_reason`) — never a second while one is still running (concurrency 1).
    ``probe`` reads whether the box is idle (:func:`box_idle`); ``watch`` keeps how long it
    has been. Returns the child to pass back next time."""
    watch = watch if watch is not None else _WATCH
    if prev is not None and prev.poll() is None:
        watch.reset()               # our own sweep holds the slot: not a quiet box
        return prev
    from chela.workflow import load_workflow_cached

    now = now or datetime.now()
    idle, why = (probe or box_idle)(chela_dir)
    quiet_for = watch.observe(idle, now.timestamp())
    for wf_path in workflows:
        status = load_workflow_cached(wf_path)
        if not status.ok:
            continue
        trigger = start_reason(status.workflow, now, quiet_for, chela_dir)
        if trigger is None:
            continue
        if trigger == FIXED:
            mark_started(status.workflow, now, chela_dir)
        else:
            mark_day_started(status.workflow, now, trigger, chela_dir)
        watch.reset()
        log.info("nightly sweep: starting for %s (%s%s)", wf_path, trigger,
                 f"; box busy: {why}" if why else "")
        return spawn(Path(wf_path), chela_dir, trigger)
    return prev


# --- which lines ----------------------------------------------------------------------------


def _git(cwd: Path, *args: str, ok=(0,)) -> str:
    try:
        out = subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True,
                             errors="replace", timeout=GIT_TIMEOUT)
    except (OSError, subprocess.TimeoutExpired) as e:
        raise _Git(f"`git {' '.join(args[:3])}` could not run: {e}") from e
    if out.returncode not in ok:
        raise _Git(f"`git {' '.join(args[:3])}` failed (exit {out.returncode}): "
                   f"{(out.stderr or '').strip()[:200]}")
    return out.stdout


def merged_commits(repo: Path, ref: str, since: float, wf) -> list[dict]:
    """Each first-parent commit on ``ref`` committed at or after ``since``: ``{sha, subject,
    tier, files, introduced}`` — ``introduced`` is every commit it brought in (itself, or a
    merge's whole side branch). Raises :class:`_Git` on any read that fails."""
    out = []
    log_out = _git(repo, "log", "--first-parent", f"--since=@{int(since)}",
                   "--format=%H%x1f%ct%x1f%P%x1f%s", ref, "--")
    for raw in log_out.splitlines():
        sha, ct, parents, subject = (raw.split("\x1f") + ["", "", ""])[:4]
        if not ct.isdigit() or int(ct) < since:
            continue
        first = parents.split()[0] if parents.split() else EMPTY_TREE
        files = [f for f in _git(repo, "diff", "--name-only", "-z", "--no-renames", first, sha,
                                 "--").split("\0") if f]
        introduced = {sha}
        if len(parents.split()) > 1:
            introduced |= set(_git(repo, "rev-list", f"{first}..{sha}").split())
        tier = risktier.classify(files, risktier.always_human(wf), risktier.core(wf))[0]
        out.append({"sha": sha, "subject": subject, "tier": tier, "files": files,
                    "introduced": sorted(introduced)})
    return out


def blamed_lines(worktree: Path, commits: list[dict]) -> tuple[dict[str, set[int]],
                                                                dict[tuple[str, int], str]]:
    """``(lines, origin)``: the lines of the checkout at HEAD that still blame to one of
    ``commits``, and which of them each came from. Only source the sweep can judge — a
    mutatable file, or an unsupported one (reported UNKNOWN). Raises :class:`_Git`."""
    owner = {s: c["sha"] for c in commits for s in c["introduced"]}
    files = sorted({f for c in commits for f in c["files"]
                    if (sweep._sweepable(f) or sweep._unsupported(f))
                    and (worktree / f).is_file()})
    lines: dict[str, set[int]] = {}
    origin: dict[tuple[str, int], str] = {}
    for rel in files:
        for raw in _git(worktree, "blame", "--porcelain", "HEAD", "--", rel).splitlines():
            m = _RE_BLAME.match(raw)
            if m and m.group(1) in owner:
                ln = int(m.group(2))
                lines.setdefault(rel, set()).add(ln)
                origin[(rel, ln)] = owner[m.group(1)]
    return lines, origin


# --- the filing seam ------------------------------------------------------------------------


@dataclass(frozen=True)
class FileContext:
    """What a :class:`Filer` gets beside the surviving group: the run it came from."""
    workflow: Path
    worktree: Path           # the checkout at ``head`` — still there while the filer runs
    head: str
    ref: str
    merged_in: str           # "`<sha12>` <subject>" of the PR the guard line blames to, or ""
    chela_dir: Path


@dataclass(frozen=True)
class SurvivorGroup:
    """One CATEGORY of surviving guard: mutation shape (``mutation.kind``) plus target (the
    stripped line, :func:`category`), with every same-shape site of it in the checkout."""
    file: str                           # where the first survivor of the category is
    mutation: sweep.Mutation            # that survivor — the sweep's priority order
    survivors: tuple[sweep.Mutation, ...]   # every surviving mutation in the category
    sites: tuple[str, ...]              # EVERY "file:line" whose stripped text is the target
    key: str                            # :func:`category_key` — no file, no line number

    def as_dict(self) -> dict:
        return {"file": self.file, "key": self.key, "mutation": self.mutation.describe(),
                "kind": self.mutation.kind, "target": self.mutation.before.strip(),
                "survived": [m.key for m in self.survivors], "sites": list(self.sites)}


class Filer(Protocol):
    """Files ONE :class:`SurvivorGroup`. Returns the tracker identifier, or ``None`` when
    nothing was filed (the keys are then NOT recorded, so a later run files it). Raising
    refuses this filing only."""

    def __call__(self, group: SurvivorGroup, ctx: FileContext) -> str | None: ...


class WouldFile:
    """The :class:`Filer` for a tracker that cannot create issues: files NOTHING, and appends
    what it would have filed to ``$CHELA_DIR/sweep-nightly/would-file.jsonl``."""

    def __call__(self, group: SurvivorGroup, ctx: FileContext) -> None:
        path = state_dir(ctx.chela_dir) / "would-file.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        rec = {"at": time.time(), "workflow": str(ctx.workflow), "site": group.mutation.key,
               "file": group.file, "key": group.key, "sites": list(group.sites),
               "head": ctx.head, "merged_in": ctx.merged_in,
               "mutations": [{"site": m.key, "kind": m.kind, "before": m.before.strip(),
                              "after": m.after.strip()} for m in group.survivors]}
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, sort_keys=True) + "\n")
        return None


def _code(text: str) -> str:
    """``text`` as one inline code span, fenced past any backtick run inside it."""
    ticks = "`" * (max((len(r) for r in re.findall(r"`+", text)), default=0) + 1)
    pad = " " if text.startswith("`") or text.endswith("`") else ""
    return f"{ticks}{pad}{text}{pad}{ticks}"


def render_issue(group: SurvivorGroup, ctx: FileContext) -> tuple[str, str]:
    """``(title, description)`` of the ONE issue for ``group``."""
    first = group.mutation
    text = first.before.strip()
    more = len(group.survivors) - 1
    title = f"Nightly sweep: {first.kind} survived at {first.key}" + (
        f" and {more} more site(s)" if more else "")
    lines = [
        "⚖️🧹 The nightly mutation sweep found a guard on the base branch that no test can "
        "tell apart from its corruption. It merged from a STANDARD-tier PR, which gets no "
        "judge round.", "",
        f"**Category:** `{first.kind}` on {_code(text)}",
        f"**Swept:** `{ctx.ref}` at `{ctx.head[:12]}`",
    ]
    if ctx.merged_in:
        lines.append(f"**Merged in:** {ctx.merged_in}")
    lines += ["", "**Mutations that survived:**", ""]
    lines += [f"- `{m.key}`: {_code(m.before.strip())} → {_code(m.after.strip())}"
              for m in group.survivors]
    lines += ["", f"**Same-shape sites** (every source line that reads exactly {_code(text)}):",
              ""]
    survived = {m.key for m in group.survivors}
    lines += [f"- `{s}`" + (" ← survived" if s in survived else "")
              for s in group.sites[:MAX_SITES]]
    if len(group.sites) > MAX_SITES:
        lines.append(f"- … and {len(group.sites) - MAX_SITES} more (`git grep -nF` the line)")
    lines += ["", "Add a test that goes RED under each mutation above — not a weaker "
                  "assertion — and check every same-shape site for the same gap."]
    return title, "\n".join(lines)


class TrackerFiler:
    """The tracker's :class:`Filer`: ONE issue per group through ``create_issue``, labelled
    :data:`LABELS`. A label the tracker could not attach is kept in :attr:`warnings` — the
    issue itself is filed."""

    def __init__(self, create):
        self.create = create
        self.warnings: list[str] = []

    def __call__(self, group: SurvivorGroup, ctx: FileContext) -> str:
        title, body = render_issue(group, ctx)
        out = self.create(title, body, labels=LABELS)
        ident = out["identifier"]
        self.warnings += [f"{ident}: {w}" for w in out.get("warnings") or ()]
        return ident


def default_filer(wf) -> Filer:
    """:class:`TrackerFiler` over the workflow's tracker, or :class:`WouldFile` when its kind
    cannot create issues."""
    from chela.sources import get_source

    create = getattr(get_source(wf), "create_issue", None)
    return TrackerFiler(create) if create is not None else WouldFile()


def survivor_key(wf_path: str, mut: sweep.Mutation) -> str:
    """Stable across nights and line shifts: no line number, no sha — what the guard IS."""
    raw = "\0".join((str(wf_path), mut.file, mut.kind, mut.before.strip(), mut.after.strip()))
    return hashlib.sha256(raw.encode()).hexdigest()[:24]


def category(mut: sweep.Mutation) -> tuple[str, str]:
    """``(shape, target)``: the mutation's kind and the stripped text of the line it hit."""
    return mut.kind, mut.before.strip()


def category_key(wf_path: str, mut: sweep.Mutation) -> str:
    """The group's identity: workflow + :func:`category` — no file, no line number."""
    raw = "\0".join((str(wf_path), *category(mut)))
    return hashlib.sha256(raw.encode()).hexdigest()[:24]


def group_survivors(survivors: list[sweep.Mutation]) -> list[list[sweep.Mutation]]:
    """One group per CATEGORY (:func:`category`) — every survivor of it, wherever it is — in
    the sweep's priority order."""
    groups: dict[tuple[str, str], list[sweep.Mutation]] = {}
    for m in survivors:
        groups.setdefault(category(m), []).append(m)
    return list(groups.values())


def parse_grep(out: str, text: str) -> list[str]:
    """``git grep -n -z`` output (``path NUL line NUL body``) → each ``"path:line"`` whose
    stripped body is EXACTLY ``text``, in mutatable source. A longer line merely containing
    ``text`` is not the same shape."""
    sites = []
    for raw in out.split("\n"):
        parts = raw.split("\0", 2)
        if len(parts) != 3 or not parts[1].isdigit():
            continue
        rel, ln, body = parts
        if body.strip() == text and sweep._sweepable(rel):
            sites.append(f"{rel}:{ln}")
    return sites


def same_shape_sites(worktree: Path, text: str) -> list[str] | None:
    """Every ``file:line`` of mutatable source in ``worktree`` whose stripped text is exactly
    ``text`` — a FIXED-STRING grep (``-F``): ``.*[(`` is text, not a pattern. ``None`` when
    ``git grep`` could not say, never "no other site"."""
    if not text:
        return None
    try:
        out = _git(worktree, "grep", "-n", "-z", "-I", "--full-name", "--no-color",
                   "--no-column", "-F", "-e", text, "--", ok=(0, 1))
    except _Git as e:
        log.warning("nightly sweep: same-shape sites of %r: %s", text, e)
        return None
    return parse_grep(out, text)


def survivor_groups(wf_path: str, worktree: Path, survivors: list[sweep.Mutation],
                    ) -> tuple[list[SurvivorGroup], list[str]]:
    """``(groups, failed)``: one :class:`SurvivorGroup` per category, with its sites. A
    category whose sites cannot be listed — or whose listing misses one of its own survivors
    — is in ``failed`` instead (``"<site>: <why>"``): unknown sites are never "only these"."""
    groups, failed = [], []
    for members in group_survivors(survivors):
        first = members[0]
        sites = same_shape_sites(worktree, first.before.strip())
        if sites is None:
            failed.append(f"{first.key}: its same-shape sites could not be listed")
            continue
        missing = [m.key for m in members if m.key not in sites]
        if missing:
            failed.append(f"{first.key}: the same-shape listing misses {', '.join(missing)}")
            continue
        groups.append(SurvivorGroup(file=first.file, mutation=first, survivors=tuple(members),
                                    sites=tuple(sites), key=category_key(wf_path, first)))
    return groups, failed


# --- what is owed (CMX-374) -----------------------------------------------------------------

OWED_FIELDS = {"file": str, "line": int, "kind": str, "before": str, "after": str, "pr": str}
# Why an owed mutation left the set: it was measured, or its line is no longer on the base.
RAN, LINE_GONE = "ran", "line_gone"
RAN_STATUSES = (judge.KILLED, judge.SURVIVED)


def _owed_path(chela_dir: Path | None) -> Path:
    return state_dir(chela_dir) / "owed.json"


def _valid_owed(entry: object) -> bool:
    return isinstance(entry, dict) and all(
        type(entry.get(k)) is t for k, t in OWED_FIELDS.items()) and entry["line"] > 0


def read_owed(wf, chela_dir: Path | None = None) -> list[dict]:
    """``wf``'s owed mutations, oldest first. Raises ``ValueError`` for a store that exists but
    cannot be read, or holds an entry that is not one: unknown is never "nothing owed"."""
    path = _owed_path(chela_dir)
    entries = _read_json(path).get(str(wf.path), [])
    if not isinstance(entries, list) or not all(_valid_owed(e) for e in entries):
        raise ValueError(f"{path} holds a malformed owed list for {wf.path}")
    return entries


def write_owed(wf, entries: list[dict], chela_dir: Path | None = None) -> None:
    """Replace ``wf``'s owed list, keeping every other workflow's."""
    path = _owed_path(chela_dir)
    store = _read_json(path)
    store[str(wf.path)] = entries
    _write_json(path, store)


def mutation_id(m: sweep.Mutation) -> tuple[str, int, str, str]:
    return m.file, m.line, m.kind, m.after


def _entry_id(e: dict, line: int) -> tuple[str, int, str, str]:
    return e["file"], line, e["kind"], e["after"]


def locate_owed(worktree: Path, entry: dict, taken: set) -> int | None:
    """The line ``entry``'s mutation sits on at the swept tip: one that reads exactly its
    ``before``, nearest its recorded line (a later merge shifts it), not ``taken`` by another
    owed entry. ``None`` when no line reads so — deleted, rewritten, or the file is gone.
    Raises ``OSError``/``UnicodeDecodeError`` for a file that exists but cannot be read."""
    try:
        src = (worktree / entry["file"]).read_text().split("\n")   # the mutators' line split
    except (FileNotFoundError, NotADirectoryError):
        return None
    hits = sorted((abs(i - entry["line"]), i) for i, text in enumerate(src, 1)
                  if text == entry["before"] and _entry_id(entry, i) not in taken)
    return hits[0][1] if hits else None


def owed_first(muts: list[sweep.Mutation], owed: list[tuple], blamed: dict[str, set[int]],
               ) -> list[sweep.Mutation]:
    """The order tonight's battery measures in: every OWED mutation first (oldest first), then
    tonight's merges' in the sweep's own priority. Another mutation of a line swept only
    because it is owed is left out — an earlier night measured it."""
    by_id = {mutation_id(m): m for m in muts}
    first = [by_id[i] for i in owed if i in by_id]
    owed_ids = set(owed)
    rest = [m for m in muts
            if mutation_id(m) not in owed_ids and m.line in blamed.get(m.file, ())]
    return first + rest


def settle_owed(report: sweep.SweepReport, located: list[tuple[dict, int]], gone: list[dict],
                pr_of: dict[tuple[str, int], str], now: float, keep_survivors: bool,
                ) -> tuple[list[dict], list[dict]]:
    """``(owed, dropped)`` after the battery. An owed mutation that ran (killed or survived)
    is dropped — unless it survived and ``keep_survivors`` (its filing failed); one whose line
    is gone, or no longer yields it, is dropped as :data:`LINE_GONE`; any other stays owed at
    its new line. Then tonight's UNRUN mutations join, each once."""
    status = {mutation_id(r.mutation): r.status for r in report.results}
    keep: list[dict] = []
    dropped = [{**e, "why": LINE_GONE} for e in gone]
    for e, ln in located:
        st = status.get(_entry_id(e, ln))
        if st == judge.SURVIVED and keep_survivors:
            keep.append({**e, "line": ln})
        elif st in RAN_STATUSES:
            dropped.append({**e, "line": ln, "why": RAN, "status": st})
        elif st is None:
            dropped.append({**e, "line": ln, "why": LINE_GONE})
        else:
            keep.append({**e, "line": ln})
    known = {_entry_id(e, e["line"]) for e in keep}
    for r in report._with(sweep.UNRUN):
        m = r.mutation
        if mutation_id(m) not in known:
            known.add(mutation_id(m))
            keep.append({"file": m.file, "line": m.line, "kind": m.kind, "before": m.before,
                         "after": m.after, "pr": pr_of.get((m.file, m.line), ""), "since": now})
    return keep, dropped


# --- the run --------------------------------------------------------------------------------


def paused(chela_dir: Path) -> bool:
    """A fleet pause file that exists — even one we cannot parse — means paused."""
    return os.path.lexists(Path(chela_dir) / FLEET_PAUSE_FILE)


def base_ref(wf) -> str:
    return f"origin/{wf.get('workspace', 'base_branch', default='dev')}"


def run_budget(wf, trigger: str | None, now: float,
               chela_dir: Path | None = None) -> sweep.SweepBudget:
    """⏳ CMX-400: this run's budget. An idle or fallback run gets what is LEFT of the day's
    ``sweep.budget_seconds`` (a yielded run may resume the same day); a fallback run at most
    ``sweep.fallback_budget_seconds`` of it. A fixed or hand run gets the whole budget.
    Raises ``ValueError`` for a day record that cannot be read."""
    budget = nightly_budget(wf)
    if trigger not in (IDLE, FALLBACK):
        return budget
    rec = _pending(wf, chela_dir) or read_day(wf, datetime.fromtimestamp(now).date().isoformat(),
                                              chela_dir)
    seconds = budget.seconds - _used(rec)
    if trigger == FALLBACK:
        seconds = min(seconds, fallback_budget_seconds(wf))
    return sweep.SweepBudget(seconds=seconds, max_mutations=budget.max_mutations)


def slot_wanted() -> str:
    """Who, besides this process, is waiting for the suite slot (``""``: nobody) — the
    sweep's yield check between two mutations."""
    from chela import suite_guard

    return ", ".join(suite_guard.waiters(exclude_pid=os.getpid()))


class YieldCheck:
    """⏳ CMX-436: a daemon-started sweep's yield check between two mutations — a dispatch
    pause or blocked hours (:func:`hold_off`) first, then anyone waiting for the suite slot
    (:func:`slot_wanted`). Returns what it yields to (``""``: go on); :attr:`reason` keeps
    WHY — :data:`DISPATCH_PAUSED`, :data:`BLOCKED_HOURS`, or ``""`` for the slot."""

    def __init__(self, wf, chela_dir: Path | None = None, clock=datetime.now):
        self.wf, self.chela_dir, self.clock = wf, chela_dir, clock
        self.reason = ""

    def __call__(self) -> str:
        self.reason, what = hold_off(self.wf, self.clock(), self.chela_dir)
        return what or slot_wanted()


def run(wf, *, now: float | None = None, chela_dir: Path | None = None,
        filer: Filer | None = None, trigger: str | None = None) -> dict:
    """One nightly sweep of ``wf``, then ONE inbox notice. ``filer`` is the filing seam
    (:func:`default_filer` when None). ``trigger`` (CMX-400): what started it — the daemon's
    :data:`IDLE` / :data:`FALLBACK` / :data:`FIXED`, ``None`` for a hand run. Never raises an
    ``Exception``: a crash is a CANNOT VERIFY result."""
    chela_dir = Path(chela_dir or config.CHELA_DIR)
    now = time.time() if now is None else now
    result = {"workflow": str(wf.path), "at": now, "filed": [], "would_file": [],
              "already_filed": 0, "failed": [], "survivors": 0, "prs": [], "groups": [],
              "warnings": [], "trigger": trigger}
    try:
        budget = run_budget(wf, trigger, now, chela_dir)
    except ValueError as e:
        # ⛔ Unknown is not "nothing used today": a guess could overspend the day.
        budget = sweep.SweepBudget(seconds=0.0)
        result.update(status=CANNOT_VERIFY, why=f"the day record: {e}")
    if result.get("status"):
        pass
    elif budget.seconds <= 0:
        result.update(status=SKIPPED, reason=BUDGET_SPENT,
                      why="today's sweep budget is already spent")
    elif paused(chela_dir):
        result.update(status=SKIPPED, reason=FLEET_PAUSED,
                      why="a fleet pause (`chela dispatch --pause --all`) is active")
    elif trigger in (IDLE, FALLBACK, FIXED) and \
            (held := hold_off(wf, datetime.fromtimestamp(now), chela_dir))[0]:
        # CMX-436: the daemon checked before the spawn; this is the race in between.
        result.update(status=SKIPPED, reason=held[0], why=_held_why(*held))
    elif not judge.judge_enabled(wf):
        result.update(status=SKIPPED, reason=JUDGE_OFF,
                      why="the judge is off for this workflow — nothing to mutate against")
    else:
        lock_path = state_dir(chela_dir) / "lock"
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        with open(lock_path, "a") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                result.update(status=SKIPPED, reason=ALREADY_RUNNING,
                              why="another nightly sweep is already running")
            else:
                timer = judge_timing.RoundTimer()
                try:
                    with judge_timing.activate(timer):     # ⏱️ CMX-397: measure only
                        _run(wf, now, chela_dir, filer, result, budget)
                except Exception as e:  # noqa: BLE001 — a crash is still ONE result, said
                    log.exception("nightly sweep crashed")
                    result.update(status=CANNOT_VERIFY,
                                  why=f"the sweep crashed: {type(e).__name__}: {e}")
                # ⛔ A night that could not verify (even after it read the commits: a red
                # baseline, a torn key store) or could not file must not move the window.
                # CMX-374: a budget that left mutations UNRUN does move it — every one is OWED
                # by then (or the night is CANNOT VERIFY), and runs first next night.
                if result.get("covered") and result["status"] != CANNOT_VERIFY \
                        and not result["failed"]:
                    _record_covered(wf, now, chela_dir)
                record_timings(wf, result, timer, chela_dir)
    log.info("nightly sweep (%s): %s%s", wf.path, result["status"],
             f" — {result['why']}" if result.get("why") else "")
    if trigger in (IDLE, FALLBACK):
        finish_day(wf, result, chela_dir)
    if quiet(result):
        log_quiet(result)
    else:
        notify(result)
    record_run(wf, result, chela_dir)
    return result


def _run(wf, now: float, chela_dir: Path, filer: Filer | None, result: dict,
         budget: sweep.SweepBudget | None = None) -> None:
    from chela.workflow import resolve_workspace_root
    from chela.worktree import detached_worktree, remove_worktree

    repo = wf.path.parent
    ref = base_ref(wf)
    try:
        _git(repo, "fetch", "--quiet", "origin", ref.split("/", 1)[1])
        head = _git(repo, "rev-parse", "--verify", f"{ref}^{{commit}}").strip()
        since = window_start(wf, now, chela_dir)
        commits = merged_commits(repo, head, since, wf)
    except _Git as e:
        result.update(status=CANNOT_VERIFY, why=f"could not read {ref}: {e}")
        return
    standard = [c for c in commits if c["tier"] == risktier.STANDARD]
    try:
        owed = read_owed(wf, chela_dir)
    except ValueError as e:
        # ⛔ Unknown is not "nothing owed": a night that went on would overwrite what is.
        result.update(status=CANNOT_VERIFY, why=f"the owed-mutation store: {e}")
        return
    result["owed_carried"] = len(owed)
    result["covered"] = True
    window = ("in the last 24h" if since >= now - WINDOW_SECONDS
              else f"since {datetime.fromtimestamp(since).isoformat(timespec='minutes')}")
    result["head"] = head
    result["prs"] = [f"{c['sha'][:12]} {c['subject']}" for c in standard]
    result["not_swept"] = {t: sum(1 for c in commits if c["tier"] == t)
                           for t in (risktier.CORE, risktier.HUMAN, risktier.PROSE)}
    if not standard and not owed:
        result.update(status=SKIPPED, reason=NO_MERGES,
                      why=f"no standard-tier PR merged to {ref} {window}")
        return
    root = resolve_workspace_root(wf)
    worktree = (root / "nightly-sweep").resolve()
    try:
        detached_worktree(repo, head, worktree, root)
    except Exception as e:  # noqa: BLE001 — any checkout failure is a CANNOT VERIFY, said
        result.update(status=CANNOT_VERIFY, why=f"could not check out {head[:12]}: {e}")
        return
    try:
        _sweep_and_file(wf, worktree, head, ref, standard, chela_dir, filer, result, owed,
                        budget)
    finally:
        try:
            remove_worktree(repo, worktree, root)
        except Exception:  # noqa: BLE001 — a leftover checkout is logged, never fatal
            log.exception("nightly sweep: could not remove %s", worktree)


def nightly_budget(wf) -> sweep.SweepBudget:
    """⏳ CMX-382: the nightly battery's budget — ``sweep.budget_seconds`` /
    ``sweep.max_mutations`` (nightly-only) over ``judge.sweep_*`` (the per-PR budget) over
    the defaults, each field on its own (:func:`sweep.resolve_budget`)."""
    return sweep.resolve_budget(wf, seconds=wf.get("sweep", "budget_seconds"),
                                max_mutations=wf.get("sweep", "max_mutations"))


def _sweep_and_file(wf, worktree: Path, head: str, ref: str, standard: list[dict],
                    chela_dir: Path, filer: Filer | None, result: dict,
                    owed: list[dict], budget: sweep.SweepBudget | None = None) -> None:
    try:
        lines, origin = blamed_lines(worktree, standard)
    except _Git as e:
        result.update(status=CANNOT_VERIFY, why=f"could not blame the merged lines: {e}")
        return
    # CMX-374: where each owed mutation sits tonight — or that its line is gone.
    located, gone, taken = [], [], set()
    for e in owed:
        try:
            ln = locate_owed(worktree, e, taken)
        except (OSError, UnicodeDecodeError) as err:
            result.update(status=CANNOT_VERIFY,
                          why=f"could not read {e['file']} to find an owed mutation: {err}")
            return
        if ln is None:
            gone.append(e)
        else:
            taken.add(_entry_id(e, ln))
            located.append((e, ln))
    swept = {f: set(ns) for f, ns in lines.items()}
    for e, ln in located:
        swept.setdefault(e["file"], set()).add(ln)
    first = [_entry_id(e, ln) for e, ln in located]
    # ⛔ CMX-325: this checkout is never launched through an agent, so no `hooks.before_run`
    # built it — no venv, no node_modules. Provision it HERE, before the battery: the sweep's
    # own provisioning runs only after it has collected the mutations, and collecting a
    # .ts/.tsx file's already needs `typescript` (the first night was CANNOT VERIFY on that).
    with judge_timing.within("provision"):
        env_problem = judge.provision_suite_env(worktree)
    if env_problem:
        result.update(status=CANNOT_VERIFY,
                      why=f"the nightly worktree could not be PROVISIONED: {env_problem}")
        return
    prune_evidence(chela_dir)
    cfg = judge.judge_suite_config(wf)
    budget = budget if budget is not None else nightly_budget(wf)
    # ⏳ CMX-400: an agent waiting for the suite slot stops the battery between mutations —
    # and (CMX-436) so does a dispatch pause or blocked hours, for a daemon-started run.
    check = YieldCheck(wf, chela_dir) if result.get("trigger") in (IDLE, FALLBACK, FIXED) \
        else None
    with judge_timing.within("sweep"):
        report = sweep.sweep(worktree, f"{ref}@{head[:12]} (standard-tier lines)",
                             cfg.test_cmd, timeout=cfg.suite_timeout_seconds,
                             select_tests=cfg.select_tests, provision=False, budget=budget,
                             lines=swept, keep=evidence_dir(wf, result["at"], chela_dir),
                             order=lambda muts: owed_first(muts, first, lines),
                             should_yield=check or slot_wanted)
    unrun = [r.mutation.key for r in report._with(sweep.UNRUN)]
    result.update(yielded_to=report.yielded, sweep_seconds=report.seconds,
                  budget_seconds=budget.seconds,
                  yield_reason=check.reason if (check and report.yielded) else "")
    result.update(mutations=len(report.results), unrun=len(unrun),
                  measured=report.measured, battery_seconds=report.battery_seconds,
                  per_hour=report.per_hour,
                  unrun_sites=sorted(set(unrun)), unsupported=list(report.unsupported),
                  baseline_failures=list(report.baseline_failures),
                  baseline_flakes=list(report.baseline_flakes),
                  baseline_rerun=report.baseline_rerun, kept=report.kept)
    if report.cannot_verify:
        # The owed set stays as it was: a battery that could not verify settled nothing.
        result.update(status=CANNOT_VERIFY, why=report.cannot_verify)
        return
    subjects = {c["sha"]: f"`{c['sha'][:12]}` {c['subject']}" for c in standard}
    pr_of = {**{(e["file"], ln): e["pr"] for e, ln in located},
             **{k: subjects.get(sha, "") for k, sha in origin.items()}}
    survivors = [r.mutation for r in report.survivors]
    result["survivors"] = len(survivors)
    if survivors:
        _file(wf, worktree, head, ref, survivors, pr_of, chela_dir, filer, result)
    # After the filing: an owed survivor that could not be filed stays owed — its PR may be
    # out of the window, so nothing else would measure it again.
    unfiled = result.get("status") == CANNOT_VERIFY or bool(result["failed"])
    owed_now, dropped = settle_owed(report, located, gone, pr_of, result["at"], unfiled)
    write_owed(wf, owed_now, chela_dir)
    result.update(owed=len(owed_now),
                  owed_ran=sum(1 for d in dropped if d["why"] == RAN),
                  owed_dropped=[f"{d['file']}:{d['line']} {d['kind']} — {d['why']}"
                                for d in dropped if d["why"] != RAN])
    if result.get("status") is None:
        result.update(status=_quiet_status(result), why=_quiet_why(result))


def _quiet_status(result: dict) -> str:
    """A night with nothing (left) to file: CLEAN only when every mutation ran and every
    merged file was mutatable."""
    if result.get("unrun"):
        return UNRUN
    if result.get("unsupported"):
        return UNKNOWN
    return CLEAN


def _held_why(reason: str, what: str) -> str:
    """CMX-436: a held-off skip, saying which hold."""
    return {DISPATCH_PAUSED: "dispatch paused (`chela dispatch --pause`) — the box stays quiet",
            BLOCKED_HOURS: f"inside {what} (`sweep.blocked_hours`)"}.get(reason, what)


def _yielded(result: dict) -> str:
    """``yielded: dispatch paused`` / ``yielded: blocked hours HH:MM-HH:MM`` (CMX-436), or
    ``yielded the suite slot to CMX-12``."""
    if result.get("yield_reason") in (DISPATCH_PAUSED, BLOCKED_HOURS):
        return f"yielded: {result['yielded_to']}"
    return f"yielded the suite slot to {result['yielded_to']}"


def _quiet_why(result: dict) -> str:
    if result.get("yielded_to"):
        return (f"{_yielded(result)}; {result.get('unrun', 0)} "
                "mutation(s) it did not reach are owed to the next run")
    if result.get("unrun"):
        return (f"{result['unrun']} mutation(s) the budget never reached are UNRUN — CANNOT "
                "VERIFY for their lines, not clean")
    if result.get("unsupported"):
        return (f"{', '.join(result['unsupported'])} UNKNOWN — no mutator handles them, not "
                "clean")
    return ""


def _file(wf, worktree: Path, head: str, ref: str, survivors: list[sweep.Mutation],
          pr_of: dict[tuple[str, int], str], chela_dir: Path, filer: Filer | None,
          result: dict) -> None:
    keys_path = state_dir(chela_dir) / "filed.json"
    try:
        store = _read_json(keys_path)
    except ValueError as e:
        # ⛔ Unknown is not "nothing filed yet": filing on a guess files duplicates.
        result.update(status=CANNOT_VERIFY, why=f"the filed-key store {e} — nothing filed")
        return
    filed = store.setdefault("keys", {})
    merged = {f"{f}:{ln}": pr for (f, ln), pr in pr_of.items()}
    filer = filer if filer is not None else default_filer(wf)
    groups, unlisted = survivor_groups(str(wf.path), worktree, survivors)
    result["failed"].extend(unlisted)
    result["groups"] = [g.as_dict() for g in groups]
    for group in groups:
        site = group.mutation.key
        keys = [survivor_key(str(wf.path), m) for m in group.survivors]
        # Per MUTATION: a new survivor category on a guard line filed before is filed again.
        if all(k in filed for k in keys):
            result["already_filed"] += 1
            continue
        ctx = FileContext(workflow=wf.path, worktree=worktree, head=head, ref=ref,
                          merged_in=merged.get(site, ""), chela_dir=chela_dir)
        try:
            ident = filer(group, ctx)
        except Exception as e:  # noqa: BLE001 — one refused filing never loses the others
            result["failed"].append(f"{site}: {e}")
            continue
        if ident is None:
            result["would_file"].append(site)
            continue
        result["filed"].append(ident)      # before the write: an issue made is reported
        for k in keys:
            filed[k] = {"issue": ident, "at": result["at"], "site": site}
        _write_json(keys_path, store)       # per issue: a crash never files one twice
    result["warnings"].extend(getattr(filer, "warnings", ()))
    if result["filed"] or result["failed"] or result["would_file"]:
        # A night with survivors AND unmeasured lines says both: the count alone hides it.
        result.update(status=SURVIVORS, why=_quiet_why(result))


# --- the notice -----------------------------------------------------------------------------


def summary(result: dict) -> str:
    """The ONE inbox line for a run: its outcome (and why), what was filed — or that nothing
    was — and the battery's counts, UNRUN and UNKNOWN included."""
    name = Path(result["workflow"]).parent.name or result["workflow"]
    status = result["status"]
    outcome = {SKIPPED: f"skipped — {result.get('why')}",
               CANNOT_VERIFY: f"CANNOT VERIFY — {result.get('why')}",
               SURVIVORS: f"{result['survivors']} survivor(s) in "
                          f"{len(result['groups'])} group(s)",
               CLEAN: "clean", UNRUN: "UNRUN, not clean", UNKNOWN: "UNKNOWN, not clean",
               }.get(status, status)
    filing = [f"filed {', '.join(result['filed'])}" if result["filed"] else "nothing filed"]
    if result["already_filed"]:
        filing.append(f"{result['already_filed']} group(s) already filed")
    if result["failed"]:
        filing.append(f"{len(result['failed'])} NOT filed ({result['failed'][0]})")
    if result["would_file"]:
        filing.append(f"{len(result['would_file'])} would be filed (the tracker cannot file "
                      "issues; see would-file.jsonl)")
    line = f"🧹🌙 nightly sweep ({name}){started(result)}: {outcome} — {'; '.join(filing)}"
    if "mutations" in result:
        line += (f" — {len(result['prs'])} standard-tier PR(s), {result['mutations']} "
                 f"mutation(s), {result.get('unrun', 0)} UNRUN")
        if result.get("unsupported"):
            line += f", {len(result['unsupported'])} file(s) UNKNOWN (no mutator)"
        line += throughput(result)
    if result.get("owed_carried"):
        line += f" — {result['owed_carried']} owed from earlier nights"
        if "owed_ran" in result:
            line += f" ({result['owed_ran']} ran"
            if result["owed_dropped"]:
                line += (f"; {len(result['owed_dropped'])} dropped, line gone: "
                         f"{', '.join(result['owed_dropped'])}")
            line += ")"
    if "owed" in result:
        line += f" — {result['owed']} owed to the next night"
    if result.get("yielded_to"):
        line += f" — ⏳ {_yielded(result)}"
    if result.get("trigger") and result.get("sweep_seconds"):
        line += f" — {result['sweep_seconds'] / 60:.0f} min used"
        if result.get("budget_seconds"):
            line += f" of {result['budget_seconds'] / 60:.0f}"
    if result.get("baseline_flakes"):
        line += (f" — ⚠️ baseline flake: {', '.join(result['baseline_flakes'])} (red once, green "
                 "alone; deselected from the battery)"
                 + (f" — evidence in {result['kept']}" if result.get("kept") else ""))
    if result["warnings"]:
        line += f" — ⚠️ {len(result['warnings'])} warning(s): {result['warnings'][0]}"
    return line


def started(result: dict) -> str:
    """⏳ CMX-400: `` — started HH:MM on idle`` / `` — ran on fallback: the queue was never
    idle`` / `` — started HH:MM at the fixed time``; ``""`` for a hand run."""
    trigger = result.get("trigger")
    if trigger not in (IDLE, FALLBACK, FIXED):
        return ""
    at = datetime.fromtimestamp(result["at"]).strftime("%H:%M")
    return {IDLE: f", started {at} on idle (queue and suite slot quiet)",
            FALLBACK: f", started {at} — ran on fallback: the queue was never idle",
            FIXED: f", started {at} at the fixed time (sweep.mode: fixed)",
            }[trigger]


def throughput(result: dict) -> str:
    """⚡ CMX-383: `` — N measured in H h: R mutations/hour`` — the night's rate, so a
    selection regression shows up (11-16 ran 25 in 3.2 h: 7.8/h). ``""`` when the battery
    measured nothing or recorded no time. CMX-436: below :data:`MIN_RATE_MUTATIONS` measured
    AND :data:`MIN_RATE_SECONDS` of battery, `` — N measured: too few to rate`` (11-18: "1
    measured in 0.0 h: 22885 mutations/hour")."""
    rate = result.get("per_hour")
    if not result.get("measured") or not rate:
        return ""
    seconds = result.get("battery_seconds") or 0
    if result["measured"] < MIN_RATE_MUTATIONS and seconds < MIN_RATE_SECONDS:
        return f" — {result['measured']} measured: too few to rate"
    hours = seconds / 3600
    return f" — {result['measured']} measured in {hours:.1f} h: {rate:g} mutations/hour"


def _render(result: dict) -> None:
    try:
        result["summary"] = summary(result)
    except Exception as e:  # noqa: BLE001 — run() never raises: a plain line instead
        log.exception("nightly sweep: the notice could not be rendered")
        result["summary"] = (f"🧹🌙 nightly sweep ({result.get('workflow')}): "
                             f"{result.get('status')} — the notice failed: {e!r}")


def quiet(result: dict) -> bool:
    """CMX-371: a run nobody needs to hear about — SKIPPED for a :data:`QUIET_REASONS` key."""
    return result["status"] == SKIPPED and result.get("reason") in QUIET_REASONS


def log_quiet(result: dict) -> None:
    """A quiet run's record instead of a notice: the same line, logged and appended to the
    event log (never raises), so the night stays auditable."""
    from chela import event_log

    _render(result)
    log.info("%s (not sent to the inbox)", result["summary"])
    event_log.append(INBOX_KIND, result["summary"],
                     {k: v for k, v in result.items() if k != "summary"})


def _runs_path(chela_dir: Path | None) -> Path:
    return state_dir(chela_dir) / "runs.json"


def read_runs(chela_dir: Path | None = None) -> dict:
    """``{workflow path: {at, status, reason}}`` — each workflow's last recorded run. Raises
    ``ValueError`` for a record that exists but cannot be read."""
    return _read_json(_runs_path(chela_dir))


def record_run(wf, result: dict, chela_dir: Path | None = None) -> None:
    """Record ``result`` as ``wf``'s last run, quiet or not — what ``chela doctor`` reads to
    tell quiet nights from a dead scheduler. Never raises: the record is not the work."""
    path = _runs_path(chela_dir)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        # Its own lock, not the sweep's: a run that skipped because another holds THAT one
        # records too, and two writers must not drop each other's workflow.
        with open(path.with_name("runs.lock"), "a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            try:
                runs = _read_json(path)
            except ValueError:
                runs = {}
            runs[str(wf.path)] = {"at": result["at"], "status": result["status"],
                                  "reason": result.get("reason")}
            _write_json(path, runs)
    except Exception:  # noqa: BLE001 — logged; doctor then reads the last run as stale
        log.exception("nightly sweep: could not record the run in %s", path)


def timings_path(chela_dir: Path | None = None) -> Path:
    return state_dir(chela_dir) / judge_timing.NIGHTLY_TIMINGS_FILENAME


def record_timings(wf, result: dict, timer: judge_timing.RoundTimer,
                   chela_dir: Path | None = None) -> bool:
    """⏱️ CMX-397: append this night's per-phase wall-clock (slot wait included) — what
    ``chela judge timings --nightly`` summarises. Never raises: the record is not the work."""
    try:
        rec = {"workflow": str(wf.path), "at": judge_timing.utc_stamp(result["at"]),
               "status": result.get("status"), "timings": timer.as_dict()}
    except Exception:  # noqa: BLE001 — the record is not the work
        log.exception("nightly sweep: could not build the timings record")
        return False
    return judge_timing.append_record(timings_path(chela_dir), rec)


def notify(result: dict) -> None:
    """Queue the run's ONE inbox notice. A failing inbox is logged: the notice is the report,
    never the work."""
    from chela import inbox

    _render(result)
    log.info("%s", result["summary"])
    try:
        inbox.enqueue(INBOX_KIND, result["summary"],
                      {k: v for k, v in result.items() if k != "summary"})
    except Exception:  # noqa: BLE001 — the notice is the report, not the work
        log.exception("nightly sweep: the inbox notice failed")
