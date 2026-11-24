"""🧠 CMX-119: every full-suite run (judge battery, ``judge sweep``, ``judge self-check``,
``task-finished --self-check-experiments``) goes through :func:`chela.judge.run_suite`, and
this is what that one choke point wraps it in.

1. **A memory ceiling per suite run** — a ``systemd-run --user --scope`` with ``MemoryMax``
   and no swap, so ONE runaway run is killed inside its own scope and reported as CANNOT
   VERIFY ("suite exceeded 8.3G"), not the machine. On 10-13 two self-checks were stopped by
   Claude Code for "critically low on memory" while leaked PM2 daemons (CMX-115) held 12 GB.
   ⛔ The guard is claimed only when the suite's own ``/proc/self/cgroup`` names the scope —
   a wrapper that exited 0 proves nothing about where the suite ran.
2. **Leak reaping** — after the run, whatever the suite left in its scope is SIGKILLed. A
   child reparented to PID 1 keeps its cgroup, so this reaches 10-13's leaked PM2 daemons.
3. **Admission control** — at most K suites run at once box-wide, judges AND agents alike:
   K ``flock`` slots in ``$CHELA_DIR/suite-slots``. A lock dies with its holder, so a crashed
   run never strands a slot. A run waiting for one says so, and its wait is credited back
   (:func:`busy_clock`, :func:`waits_in`) so the wait is never read as a stuck or slow run.
4. **A battery holds ONE slot start to finish** (CMX-393) — :func:`battery` wraps a whole
   sweep, self-check, judge battery or ``task-finished`` hand-in, and every ``run_suite``
   inside it re-enters that slot instead of taking and freeing one per mutation. Per-call
   slots let three agents' batteries interleave mutation by mutation: one suite at a time,
   but all three batteries mid-flight, so one low-memory reap killed all three (11-14,
   11-17). A battery killed anyway leaves its record, and the daemon names every such
   stopped run in ONE notice once the slot is free and memory is back
   (:func:`stopped_batteries`).
5. **Waiters are ranked, and a holder yields to a better one** (CMX-445) — a free slot goes
   to judge rounds first, then hand-ins by issue priority (Urgent, High, Medium, Low, none),
   then the nightly sweep; first come first served only among equals (:func:`queue_key`).
   The priority is the run's own, by worktree, for whichever command asks (:func:`run_rank`,
   CMX-494).
   A self-check or hand-in sweep gives its slot up between two experiments when a better
   waiter is queued, and takes it back after (:func:`yield_slot`). On 11-19 a Medium
   self-check held the only slot for 2 h 08 m while an Urgent judge waited 1 h 13 m.
6. **No slot inside blocked hours** (CMX-479) — the worktree's ``sweep.blocked_hours``
   (default 03:00-04:30, the host's nautilus crons). A waiter is told "blocked hours until
   04:30" and takes its turn, in queue order, when the window ends; a holder finishes its
   current experiment, then gives the slot up the same way (:func:`yield_slot`). On 11-23 the
   03:30 cron starved the box and the low-memory reaper killed three agents' WAITING runs.
   A battery's record is written before it waits, so a waiter killed anyway is found too
   (:func:`stopped_batteries`, ``state: "waiting"``) and its agent is asked to re-run it once.

⛔ Never fix an OOM by raising the ceiling blindly: a fatter ceiling is a fatter target.
"""
from __future__ import annotations

import contextlib
import fcntl
import json
import logging
import os
import shlex
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from collections.abc import Mapping
from datetime import datetime
from pathlib import Path

from chela import config, memcap

log = logging.getLogger(__name__)

# Peak anonymous memory of ONE full `CHELA_REQUIRE_JS_TESTS=1 uv run pytest -q` run (10,075
# tests: pytest + 4 xdist workers + node/jsdom/Vitest + browser tests), sampled from the run's
# own cgroup `memory.stat` on 2026-10-21 with Vitest capped at 4 workers: 2,954,940,416 bytes
# (cgroup memory.peak 4.69 GB, the rest reclaimable page cache). Sizes the defaults; it is
# not a limit.
SUITE_PEAK_BYTES = 2_954_940_416
DEFAULT_CEILING_BYTES = 3 * SUITE_PEAK_BYTES


def ceiling_bytes() -> int:
    """``CHELA_SUITE_MEMORY_MAX`` > ``suite_memory_max`` in config.json > 3x the measured
    peak. ``0`` turns the ceiling off."""
    return config.dashboard_setting("suite_memory_max", "CHELA_SUITE_MEMORY_MAX",
                                    DEFAULT_CEILING_BYTES, cast=config._cast_size)


def verdict(unit: str, cap: int, files: Path, returncode: int) -> str | None:
    """None when the run was bounded and stayed under ``cap``; otherwise why it CANNOT be
    read as a result. Reads what the scope itself wrote from inside — never the wrapper."""
    try:
        cgroup = (files / "cgroup").read_text()
    except OSError:
        cgroup = ""
    if f"/{unit}.scope" not in cgroup:
        return (f"the {config.human_size(cap)} memory ceiling was NOT applied — the suite's "
                f"/proc/self/cgroup does not name {unit}.scope, so chela refuses to read it")
    try:
        events = dict(line.split() for line in (files / "events").read_text().splitlines())
        oom = int(events.get("oom_kill", 0)) > 0
    except (OSError, ValueError):
        oom = returncode in (137, -9)     # the whole scope died before it could report
    if oom:
        return (f"suite exceeded {config.human_size(cap)} (its memory ceiling, CMX-119) and "
                f"was killed inside its own scope — CANNOT VERIFY, never a pass")
    return None


# The user-bus vars ``systemd-run --user`` needs to find the user's systemd. A suite's env may
# not carry them: ``judge._suite_env`` drops every var ``chela.env`` set, and a daemon started
# under ``env -i`` gets ``XDG_RUNTIME_DIR`` only from there. They name the user's systemd, not
# live chela state, so the WRAPPER gets the operator's values and the suite gets its own back.
BUS_VARS = ("XDG_RUNTIME_DIR", "DBUS_SESSION_BUS_ADDRESS")

_scope_ok: set[tuple] = set()  # launch envs a probe worked under, not re-probed by every run


def _bus_added(env: Mapping[str, str]) -> dict[str, str]:
    """The operator's user-bus vars that ``env`` lacks or sets differently."""
    return {k: os.environ[k] for k in BUS_VARS
            if os.environ.get(k) and env.get(k) != os.environ[k]}


def _scope_works(env: Mapping[str, str] | None = None) -> bool:
    """A harmless scope, launched for real — ``systemd-run`` on PATH is not a user session.
    ⛔ Probed under the SAME env the real launch gets (``env`` plus :func:`_bus_added`): a
    probe run under any other env can pass where the launch then fails (CMX-307).
    Only a success is remembered: a failed probe is retried by the next run."""
    env = os.environ if env is None else env
    launch = {**env, **_bus_added(env)}
    key = tuple(launch.get(k) for k in BUS_VARS)
    if key in _scope_ok:
        return True
    if not memcap.available():
        return False
    try:
        subprocess.run(["systemd-run", "--user", "--scope", "--collect", "-q", "--", "true"],
                       check=True, capture_output=True, timeout=10, env=launch)
    except (OSError, subprocess.SubprocessError):
        return False
    _scope_ok.add(key)
    return True


@contextlib.contextmanager
def bounded(test_cmd: str, env: Mapping[str, str] | None = None):
    """Yield ``(cmd, check)``: ``cmd`` runs ``test_cmd`` inside its own memory-capped scope
    and ``check(returncode)`` is :func:`verdict`. With the ceiling off, or no
    ``systemd-run``, ``cmd`` is ``test_cmd`` unchanged and ``check`` always passes.

    ``env`` is the env ``cmd`` will be run under (default ``os.environ``). ``systemd-run``
    alone gets the operator's user-bus vars on top (:data:`BUS_VARS`); ``test_cmd`` gets
    ``env``'s own values back, so the suite still sees exactly the env it was given."""
    env = os.environ if env is None else env
    cap = ceiling_bytes()
    if not cap or not _scope_works(env):
        if cap:
            log.warning("suite_guard: no working `systemd-run --user --scope` — this suite "
                        "runs WITHOUT a memory ceiling (CMX-119)")
        yield test_cmd, lambda _rc: None
        return
    added = _bus_added(env)
    q = shlex.quote
    outer = "".join(f"{k}={q(v)} " for k, v in added.items())
    # `env` reads options only before the first NAME=VALUE: every `-u` goes first.
    restore = ("".join(f" -u {k}" for k in added if k not in env)
               + "".join(f" {k}={q(env[k])}" for k in added if k in env))
    unit = f"chela-suite-{uuid.uuid4().hex[:12]}"
    in_slice = f" --slice={memcap.SLICE_NAME}" if config.memory_slice_budget_bytes() else ""
    with tempfile.TemporaryDirectory(prefix="chela-suite-guard-") as tmp:
        files = Path(tmp)
        inner = (f"cat /proc/self/cgroup > {q(str(files / 'cgroup'))}; sh -c {q(test_cmd)}; "
                 f"rc=$?; cg=/sys/fs/cgroup$(cut -d: -f3 /proc/self/cgroup); "
                 f"cat \"$cg/memory.events\" > {q(str(files / 'events'))}; exit $rc")
        cmd = (f"{'env ' + outer if outer else ''}"
               f"systemd-run --user --scope --collect -q --unit={unit}{in_slice} "
               f"-p MemoryMax={cap} -p MemorySwapMax=0 -p OOMPolicy=continue "
               f"-- {'env' + restore + ' ' if restore else ''}sh -c {q(inner)}")
        try:
            yield cmd, lambda rc: verdict(unit, cap, files, rc)
        finally:
            _reap(unit, {**env, **added})


def _reap(unit: str, env: Mapping[str, str] | None = None) -> None:
    """SIGKILL whatever the suite left in its scope. A child reparented to PID 1 keeps its
    cgroup, so this reaches what 10-13's leaked PM2 daemons escaped (they ignored SIGTERM).
    An already-empty scope is collected and the call simply fails — nothing to do.
    ``env`` is the env the scope was launched under, so this reaches the same user bus."""
    try:
        subprocess.run(["systemctl", "--user", "kill", "--signal=SIGKILL", f"{unit}.scope"],
                       capture_output=True, timeout=10, env=env)
    except (OSError, subprocess.SubprocessError):
        log.warning("suite_guard: could not reap %s.scope", unit)


# --- admission control: at most K suites at once, box-wide ------------------------------

SLOTS_DIRNAME = "suite-slots"
SLOT_POLL_SECONDS = 1.0
# A run waits at most this many of its own suite timeouts for a slot: every holder is itself
# bounded by one, so a queue that has not moved in that long is not a queue any more.
SLOT_WAIT_TIMEOUTS = 4
# A whole battery waits this many suite timeouts: each holder is bounded by its own budget
# (sweep/self-check ~ one suite timeout plus its baseline), so 12 covers a queue of several.
BATTERY_WAIT_TIMEOUTS = 12
# The share of RAM the default K lets full suites use at their measured peak. The rest is
# the agents, judges, tmux and the dashboard themselves.
SUITE_RAM_SHARE = 0.5
DEFAULT_SLOTS_MAX = 4

# ⏳ CMX-445: the queue order — LOWER goes first. A judge round, then hand-ins by tracker
# priority (Linear 1 Urgent … 4 Low; "No priority" or unknown after Low), then the sweep.
RANK_JUDGE = 0
RANK_URGENT, RANK_HIGH, RANK_MEDIUM, RANK_LOW = 1, 2, 3, 4
RANK_UNKNOWN = 5
RANK_SWEEP = 6
_KIND_RANKS = {"judge": RANK_JUDGE}


def handin_rank(priority) -> int:
    """⏳ CMX-445: a hand-in's queue rank from its tracker priority number — ``1``..``4``
    (Urgent..Low) keep their number; ``0`` (No priority), None and garbage rank after Low."""
    if isinstance(priority, bool) or not isinstance(priority, int):
        return RANK_UNKNOWN
    return priority if RANK_URGENT <= priority <= RANK_LOW else RANK_UNKNOWN


def _rank(raw) -> int:
    """A ledger's rank; a record without one (an older chela) ranks as an unknown hand-in."""
    if isinstance(raw, bool) or not isinstance(raw, int) or not RANK_JUDGE <= raw <= RANK_SWEEP:
        return RANK_UNKNOWN
    return raw


def _task_rank(rank: int, raw) -> int:
    """⏳ CMX-494: the task's own rank behind a queue rank. A judge queues at
    :data:`RANK_JUDGE`, so its task's rank is a separate field (it orders judge rounds among
    themselves); for everyone else the queue rank IS the task's."""
    return _rank(raw) if rank == RANK_JUDGE else rank


RANK_LABELS = {RANK_JUDGE: "judge", RANK_URGENT: "Urgent", RANK_HIGH: "High",
               RANK_MEDIUM: "Medium", RANK_LOW: "Low", RANK_UNKNOWN: "no priority",
               RANK_SWEEP: "nightly sweep"}


def rank_label(rank: int, task_rank: int) -> str:
    """``"Urgent"``, or ``"judge, Urgent"`` for a judge round of an Urgent task."""
    if rank == RANK_JUDGE:
        return f"{RANK_LABELS[RANK_JUDGE]}, {RANK_LABELS[task_rank]}"
    return RANK_LABELS[rank]


JUDGE_DIR_PREFIX = "judge-"       # ``judge.judge_worktree_path``: ``<root>/judge-<task_id>``
_last_rank: dict[str, int] = {}   # per cwd: the last rank the run DB gave


def _run_row(cwd: str | Path) -> dict | None:
    """The run whose worktree holds ``cwd`` — its own worktree, or its judge's checkout beside
    it — as ``{task_id, priority}``, or None when no run does. A LOCAL read of the run DB (the
    dispatcher keeps ``runs.priority`` current), never a tracker call. Raises ``sqlite3.Error``
    / ``OSError`` when the DB cannot be read (or is not there): that is not "no run"."""
    db = config.CHELA_DIR / "scheduler.db"
    dirs: list[Path] = []             # the deepest first
    for here in (Path(cwd).absolute(), Path(cwd).resolve()):
        dirs += [d for d in (here, *here.parents) if d not in dirs]
    judged = {d.name.removeprefix(JUDGE_DIR_PREFIX): d for d in dirs
              if d.name.startswith(JUDGE_DIR_PREFIX)}
    marks = ",".join("?" * len(dirs))
    task_marks = ",".join("?" * len(judged))
    with contextlib.closing(sqlite3.connect(f"{db.resolve().as_uri()}?mode=ro", uri=True,
                                            timeout=1.0)) as conn:
        rows = conn.execute(
            "SELECT task_id, worktree_path, priority FROM runs WHERE worktree_path IN "
            f"({marks}) OR task_id IN ({task_marks}) ORDER BY started_at DESC",
            [*map(str, dirs), *judged]).fetchall()
    best = None
    for task_id, worktree, priority in rows:
        path = Path(str(worktree))
        if path not in dirs:
            path = judged.get(task_id)
            if path is None or Path(str(worktree)).parent != path.parent:
                continue              # a directory that only LOOKS like this run's judge
        depth = len(path.parts)
        if best is None or depth > best[0]:
            best = (depth, {"task_id": task_id, "priority": priority})
    return best[1] if best else None


def run_rank(cwd: str | Path) -> int:
    """⏳ CMX-494: where the dispatched run that owns ``cwd`` queues, by its issue's priority
    as the run DB has it NOW. A directory no run owns is :data:`RANK_UNKNOWN`; a DB that
    cannot be read keeps the last rank it gave for ``cwd`` (else unknown) — a moment's lock
    must not drop an Urgent waiter to the back of the queue."""
    key = str(cwd)
    try:
        row = _run_row(cwd)
    except (sqlite3.Error, OSError):
        log.debug("suite_guard: could not read the run's priority", exc_info=True)
        return _last_rank.get(key, RANK_UNKNOWN)
    _last_rank[key] = handin_rank(row["priority"]) if row else RANK_UNKNOWN
    return _last_rank[key]


def _ranks(cwd: str, rank: int | None) -> tuple[int, int]:
    """``(queue rank, task rank)`` of an acquirer in ``cwd``, read now. ``rank`` None is the
    run's own; an explicit one (the nightly sweep) is kept as given."""
    if rank is None:
        mine = run_rank(cwd)
        return mine, mine
    return rank, run_rank(cwd) if rank == RANK_JUDGE else rank


_waited = 0.0                    # seconds THIS process has spent waiting for a slot
_held = threading.local()         # CMX-393: ``depth`` of this thread's hold — a nested slot re-enters
_waited_lock = threading.Lock()
_thread_waited: dict[tuple[int, str], float] = {}  # per (thread, cwd): what a ledger publishes


def _cast_slots(raw) -> int:
    """A whole number >= 0; anything else raises, so the next precedence level decides."""
    if isinstance(raw, (bool, float)):
        raise ValueError("not a slot count")
    n = int(raw.strip() if isinstance(raw, str) else raw)
    if n < 0:
        raise ValueError("not a slot count")
    return n


def _mem_total_bytes(meminfo: Path = Path("/proc/meminfo")) -> int | None:
    try:
        for line in meminfo.read_text().splitlines():
            if line.startswith("MemTotal:"):
                return int(line.split()[1]) * 1024
    except (OSError, ValueError, IndexError):
        pass
    return None


def default_slots() -> int:
    """``SUITE_RAM_SHARE`` of RAM over the measured peak, in [1, ``DEFAULT_SLOTS_MAX``].
    RAM that cannot be read is ONE slot — unknown never buys more concurrency."""
    total = _mem_total_bytes()
    if not total:
        return 1
    return max(1, min(DEFAULT_SLOTS_MAX, int(total * SUITE_RAM_SHARE) // SUITE_PEAK_BYTES))


def slots() -> int:
    """``CHELA_SUITE_SLOTS`` > ``suite_slots`` in config.json > :func:`default_slots`.
    ``0`` turns admission control off."""
    n = config.dashboard_setting("suite_slots", "CHELA_SUITE_SLOTS", None, cast=_cast_slots)
    return default_slots() if n is None else n


def max_wait(timeout: float) -> float:
    """How long a run with this suite ``timeout`` waits for a slot:
    ``CHELA_SUITE_SLOT_MAX_WAIT`` > ``suite_slot_max_wait`` in config.json (seconds, > 0) >
    ``SLOT_WAIT_TIMEOUTS`` x ``timeout``."""
    override = config.dashboard_setting("suite_slot_max_wait", "CHELA_SUITE_SLOT_MAX_WAIT",
                                        0.0, cast=float)
    return override if override > 0 else SLOT_WAIT_TIMEOUTS * timeout


def battery_max_wait(timeout: float) -> float:
    """How long a whole BATTERY waits for a slot (CMX-393): ``CHELA_SUITE_SLOT_MAX_WAIT`` >
    ``suite_slot_max_wait`` in config.json (seconds, > 0) > ``BATTERY_WAIT_TIMEOUTS`` x
    ``timeout``. Longer than one suite's :func:`max_wait`: the holder now keeps its slot for
    its whole battery, so a queue of two or three batteries is hours, not minutes."""
    override = config.dashboard_setting("suite_slot_max_wait", "CHELA_SUITE_SLOT_MAX_WAIT",
                                        0.0, cast=float)
    return override if override > 0 else BATTERY_WAIT_TIMEOUTS * timeout


def waited_seconds() -> float:
    """⏱️ CMX-397: seconds THIS process has spent waiting for a suite slot so far."""
    return _waited


def busy_clock() -> float:
    """``time.monotonic()`` minus what this process spent waiting for a slot — the clock a
    wall-clock BUDGET reads, so a queued run is not charged for the queue."""
    return time.monotonic() - _waited


def _proc_stat(pid: int) -> tuple[str, str] | None:
    """``(state, starttime)`` from ``/proc/<pid>/stat``, or None when it is gone."""
    try:
        fields = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()
        return fields[0], fields[19]
    except (OSError, IndexError):
        return None


def _waits_dir() -> Path:
    return config.CHELA_DIR / SLOTS_DIRNAME / "waits"


class _Ledger:
    """One waiter's public record, ``waits/<pid>-<tid>.json``: where it waits, since when
    (``None`` once it holds a slot) and how long it has waited in all. It is what orders the
    queue (:func:`_must_queue`) and what the stuck probes credit (:func:`waits_in`)."""

    def __init__(self, cwd: str, rank: int = RANK_UNKNOWN, task_rank: int | None = None):
        self.path = _waits_dir() / f"{os.getpid()}-{threading.get_ident()}.json"
        self.cwd, self.waited = cwd, _thread_waited.get((threading.get_ident(), cwd), 0.0)
        self.rank, self.task_rank = rank, _task_rank(rank, task_rank)
        # When THIS wait began: ``waiting_since`` is the CLAIM time, which for a battery that
        # yielded its slot is before it first held it.
        self.opened = time.time()
        self.blocked_until: str | None = None    # CMX-479: "04:30" while blocked hours hold it
        stat = _proc_stat(os.getpid())
        self.start = stat[1] if stat else None

    def write(self, waiting_since: float | None) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps({"pid": os.getpid(), "start": self.start,
                                       "cwd": self.cwd, "waiting_since": waiting_since,
                                       "waited": self.waited, "rank": self.rank,
                                       "task_rank": self.task_rank,
                                       "opened": self.opened,
                                       "blocked_until": self.blocked_until}))
            tmp.replace(self.path)
        except OSError:
            log.debug("suite_guard: could not publish the slot wait", exc_info=True)

    def re_rank(self, rank: int | None, since: float) -> None:
        """CMX-494: publish the rank the acquirer has NOW, when it changed. Also while blocked
        hours hold it (CMX-479): the line must be right the moment they end."""
        now_ranked = _ranks(self.cwd, rank)
        if now_ranked != (self.rank, self.task_rank):
            self.rank, self.task_rank = now_ranked
            self.write(since)


def _ledgers() -> list[tuple[Path, dict]]:
    """Every LIVE waiter's record. One whose process is gone, or whose pid was reused (the
    ``/proc`` start time differs), is removed — never credited, never queued behind."""
    try:
        files = list(_waits_dir().glob("*.json"))
    except OSError:
        return []
    out = []
    for path in files:
        try:
            rec = json.loads(path.read_text())
            stat = _proc_stat(int(rec["pid"]))
        except (OSError, ValueError, KeyError, TypeError):
            continue
        if stat is None or stat[1] != rec.get("start"):
            path.unlink(missing_ok=True)
            continue
        out.append((path, {**rec, "state": stat[0]}))
    return out


def queue_key(rank: int, since: float, name: str, task_rank=None) -> tuple:
    """⏳ CMX-445: where a waiter stands in the queue — rank first (:data:`RANK_JUDGE` …
    :data:`RANK_SWEEP`), then the task's own rank (CMX-494: among judge rounds), then age
    (first come first served among equals), then its ledger name so two waiters never tie."""
    return (rank, _task_rank(rank, task_rank), since, name)


# A waiter whose blocked hours ended this recently is back in line even if it has not polled
# since: whoever polls first at 04:30 must not pass a better-ranked one by seconds (CMX-479).
BLOCKED_STALE_MINUTES = 5


def _blocked(rec: dict) -> bool:
    """Is this waiter's record held by its own blocked hours NOW? One whose ``blocked_until``
    has just passed is not; one that is no time at all still is."""
    until = rec.get("blocked_until")
    if not until:
        return False
    try:
        hours, minutes = str(until).split(":")
        ends = int(hours) * 60 + int(minutes)
    except ValueError:
        return True
    now = _local_now()
    return (now.hour * 60 + now.minute - ends) % (24 * 60) >= BLOCKED_STALE_MINUTES


def _must_queue(me: Path, since: float, rank: int = RANK_UNKNOWN, task_rank=None) -> bool:
    """Is a waiter AHEAD of us (:func:`queue_key`) still queued? Then it goes first: a
    battery that frees its slot and takes the next one a millisecond later must not starve a
    run already waiting, and a Medium hand-in must not take the slot an Urgent judge is
    queued for (CMX-445). A stopped waiter (a fleet pause, ``T``) does not hold the line, nor
    one its own blocked hours hold (:func:`_blocked`, CMX-479): another repo may have none."""
    mine = queue_key(rank, since, me.name, task_rank)
    for path, rec in _ledgers():
        opened = rec.get("waiting_since")
        if path == me or opened is None or rec["state"] in ("T", "t") or _blocked(rec):
            continue
        try:
            theirs = queue_key(_rank(rec.get("rank")), float(opened), path.name,
                               rec.get("task_rank"))
        except (TypeError, ValueError):
            continue
        if theirs < mine:
            return True
    return False


def outranking(rank: int, exclude_pid: int | None = None) -> list[str]:
    """⏳ CMX-445: the worktree names of every live, unstopped waiter whose rank is strictly
    BETTER than ``rank`` — who a holder of that rank yields its slot to (best first)."""
    out = []
    for _path, rec in _ledgers():
        opened = rec.get("waiting_since")
        if opened is None or rec["state"] in ("T", "t") or rec.get("pid") == exclude_pid:
            continue
        if _blocked(rec):
            continue         # CMX-479: its own blocked hours hold it — it cannot take a slot
        theirs = _rank(rec.get("rank"))
        if theirs >= rank:
            continue
        try:
            out.append((theirs, float(opened), Path(str(rec.get("cwd") or "?")).name))
        except (TypeError, ValueError):
            continue
    return [name for _r, _s, name in sorted(out)]


def waits_in(worktree: str | Path) -> dict | None:
    """``{"waiting_since": epoch|None, "waited": seconds, "blocked_until": "HH:MM"|None}``
    over every live process that waited for a suite slot inside ``worktree`` (the earliest
    open wait; the summed total; CMX-479: when an open wait is held by blocked hours, when
    they end), or None when none did."""
    try:
        root = Path(worktree).resolve()
    except OSError:
        return None
    found, since, hit, until = 0.0, None, False, None
    for _path, rec in _ledgers():
        try:
            cwd, waited = Path(rec["cwd"]), float(rec.get("waited") or 0.0)
            opened = rec.get("waiting_since")
            opened = float(opened) if opened is not None else None
        except (KeyError, TypeError, ValueError):
            continue
        if cwd != root and root not in cwd.parents:
            continue
        hit, found = True, found + waited
        if opened is not None:
            since = opened if since is None else min(since, opened)
            if isinstance(rec.get("blocked_until"), str):
                until = rec["blocked_until"]
    return {"waiting_since": since, "waited": found, "blocked_until": until} if hit else None


def waiters(exclude_pid: int | None = None) -> list[str]:
    """⏳ CMX-400: who is asking for a suite slot RIGHT NOW — the worktree name of every live
    process with an open wait (``waiting_since`` set), oldest first, except ``exclude_pid``.
    A stopped waiter (a fleet pause, ``T``) is not asking, as in :func:`_must_queue`. What the
    idle sweep reads to start only on a quiet box, and to yield the slot between mutations."""
    out = []
    for _path, rec in _ledgers():
        opened = rec.get("waiting_since")
        if opened is None or rec["state"] in ("T", "t") or rec.get("pid") == exclude_pid:
            continue
        try:
            out.append((float(opened), Path(str(rec.get("cwd") or "?")).name))
        except (TypeError, ValueError):
            continue
    return [name for _since, name in sorted(out)]


def queued() -> list[dict]:
    """⏳ CMX-494: every open slot wait in the order it will be served — ``{name, cwd, pid,
    rank, task_rank, since, stopped, blocked_until}``, ``since`` being when THIS wait began (a
    yielded battery's earlier hold is not a wait). ``stopped``: a paused waiter, which does not
    hold the line; nor does one with ``blocked_until`` (CMX-479: when its blocked hours end)."""
    out = []
    for path, rec in _ledgers():
        rank = _rank(rec.get("rank"))
        try:
            claimed = float(rec.get("waiting_since"))
            since = float(rec.get("opened") or claimed)
        except (TypeError, ValueError):
            continue              # None: it holds a slot now, it is not asking
        cwd = str(rec.get("cwd") or "?")
        out.append((queue_key(rank, claimed, path.name, rec.get("task_rank")), {
            "name": Path(cwd).name, "cwd": cwd, "pid": rec.get("pid"), "rank": rank,
            "task_rank": _task_rank(rank, rec.get("task_rank")), "since": since,
            "stopped": rec["state"] in ("T", "t"),
            "blocked_until": rec["blocked_until"] if _blocked(rec) else None}))
    return [w for _key, w in sorted(out, key=lambda kw: kw[0])]


def wait_rank(worktree: str | Path) -> tuple[int, int] | None:
    """``(rank, task_rank)`` of the first-served open wait inside ``worktree``, or None."""
    root = Path(worktree).resolve()
    for w in queued():
        if root in (Path(w["cwd"]), *Path(w["cwd"]).parents):
            return w["rank"], w["task_rank"]
    return None


def _take(d: Path, k: int) -> int | None:
    """An fd holding one of the ``k`` slot locks, or None when every one is held."""
    for i in range(k):
        fd = os.open(d / f"slot-{i}", os.O_RDWR | os.O_CREAT, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            os.close(fd)
            continue
        except BaseException:
            os.close(fd)
            raise
        return fd
    return None


def _holders(d: Path, k: int) -> str:
    """Who holds the slots, for the waiting line: ``"CMX-12-r1 (pid 7, 4m)"``…"""
    out = []
    for i in range(k):
        try:
            rec = json.loads((d / f"slot-{i}").read_text() or "{}")
            age = int(time.time() - float(rec["since"])) // 60
            out.append(f"{Path(rec['cwd']).name} (pid {rec['pid']}, {age}m)")
        except (OSError, ValueError, KeyError, TypeError):
            continue
    return ", ".join(out) or "holders unknown"


def _flocked(locks: Path = Path("/proc/locks")) -> set[tuple[str, int]] | None:
    """``{(dev, inode)}`` of every file someone holds a ``flock`` on, read from
    ``/proc/locks`` (``dev`` as it prints it, ``"08:30"``) — or None when it cannot be read.
    Reading it takes no lock, so it never makes a free slot look busy to ``_take``."""
    try:
        lines = locks.read_text().splitlines()
    except OSError:
        return None
    out = set()
    for line in lines:
        f = line.split()
        if len(f) < 6 or f[1] != "FLOCK":
            continue
        try:
            maj, mnr, ino = f[5].split(":")
            out.add((f"{maj}:{mnr}", int(ino)))
        except ValueError:
            continue
    return out


def holders(locks: Path = Path("/proc/locks")) -> list[str]:
    """The worktree names holding a suite slot now (``["CMX-12-r1"]``) — what a parked
    run's "waiting for a suite slot (holder: …)" names (CMX-318). A slot file whose lock is
    free holds nobody, whatever its stale record says. When ``/proc/locks`` cannot be read,
    a record whose process is still alive is named instead."""
    return [h["name"] for h in held(locks)]


def held(locks: Path = Path("/proc/locks")) -> list[dict]:
    """:func:`holders`, with each one's record: ``{name, cwd, pid, since, rank, task_rank}``
    (CMX-494). What a record does not say is None; its rank then reads as unknown."""
    d = config.CHELA_DIR / SLOTS_DIRNAME
    locked = _flocked(locks)
    out = []
    for i in range(slots()):
        path = d / f"slot-{i}"
        try:
            st = path.stat()
        except OSError:
            continue
        try:
            rec = json.loads(path.read_text() or "{}")
        except (OSError, ValueError):
            rec = {}
        if locked is not None:
            dev = f"{os.major(st.st_dev):02x}:{os.minor(st.st_dev):02x}"
            if (dev, st.st_ino) not in locked:
                continue
        elif not isinstance(rec, dict) or _proc_stat(_int(rec.get("pid"))) is None:
            continue
        rec = rec if isinstance(rec, dict) else {}
        cwd, rank = rec.get("cwd"), _rank(rec.get("rank"))
        out.append({"name": Path(cwd).name if isinstance(cwd, str) else "unknown", "cwd": cwd,
                    "pid": rec.get("pid"), "since": rec.get("since"), "rank": rank,
                    "task_rank": _task_rank(rank, rec.get("task_rank"))})
    return out


def _int(v) -> int:
    try:
        return int(v)
    except (TypeError, ValueError):
        return -1


class SlotTimeout(TimeoutError):
    """No suite slot came free in time. An ``OSError``, so ``run_suite`` reports it as a
    suite that could not be run — CANNOT VERIFY, never a pass."""


# --- blocked hours: no slot is granted inside them (CMX-479) ------------------------------

WORKFLOW_FILE = "WORKFLOW.md"


def _local_now() -> datetime:
    """The local wall clock blocked hours are read against — what a test fakes."""
    return datetime.now()


def blocked_windows(cwd: str | Path) -> list[tuple[int, int]]:
    """⏳ CMX-479: the ``sweep.blocked_hours`` that hold the suite slot for a run in ``cwd`` —
    the windows of the ``WORKFLOW.md`` at ``cwd`` (the worktree root), as ``(start, end)``
    minutes since midnight. No WORKFLOW.md there is the default (03:00-04:30): the window is
    the HOST's (its nautilus crons), not one repo's. A WORKFLOW.md that cannot be read is the
    default too, said in the log — a broken file must not unblock the box's heaviest hours."""
    from chela import sweep_nightly
    from chela.workflow import load_workflow

    default = [sweep_nightly._parse_span(w) for w in sweep_nightly.DEFAULT_BLOCKED_HOURS]
    path = Path(cwd) / WORKFLOW_FILE
    try:
        wf = load_workflow(path)
    except FileNotFoundError:
        return default
    except Exception as e:          # a WORKFLOW.md that does not parse
        log.warning("suite_guard: %s could not be read (%s) — the suite slot keeps the default "
                    "blocked hours %s", path, e, list(sweep_nightly.DEFAULT_BLOCKED_HOURS))
        return default
    return sweep_nightly.blocked_hours(wf)


def blocked_until(windows: list[tuple[int, int]], now: datetime | None = None) -> str:
    """``"HH:MM"`` — when the blocked window ``now`` (local; default :func:`_local_now`) is
    in ends; ``""`` when ``now`` is in none of ``windows``."""
    from chela import sweep_nightly

    now = _local_now() if now is None else now
    minute = now.hour * 60 + now.minute
    for w in windows:
        if sweep_nightly._inside(w, minute):
            return "%02d:%02d" % divmod(w[1] % sweep_nightly.DAY_MINUTES, 60)
    return ""


def _blocked_line(until: str, verb: str) -> str:
    return (f"⏳ blocked hours until {until} — {verb} (`sweep.blocked_hours`, CMX-479); "
            "it starts by itself, in queue order, when they end")


def _acquire(d: Path, k: int, cwd: str, rank: int | None, on_wait, max_wait: float | None,
             poll: float, since: float | None = None,
             windows: list[tuple[int, int]] | None = None) -> int:
    """Wait (visibly, in :func:`queue_key` order) for one of the ``k`` slots and return its
    fd, at most ``max_wait`` seconds (:class:`SlotTimeout`); the wait is credited. ``since``
    is where it stands among equals (default: now). ``rank`` None is the run's own
    (:func:`run_rank`), re-read every poll: raising the issue's priority re-ranks a waiter
    that is already queued (CMX-494).

    ⏳ CMX-479: no slot is taken while the local time is inside one of ``windows`` (blocked
    hours): the ledger says until when, and that time does not count toward ``max_wait`` —
    a run queued at 03:10 must not time out because the box was held on purpose."""
    global _waited
    ledger = _Ledger(cwd, *_ranks(cwd, rank))
    opened = ledger.opened
    since = opened if since is None else since
    started, fd = None, None
    blocked_at, blocked_total = None, 0.0
    try:
        while True:
            until = blocked_until(windows) if windows else ""
            if until:
                if blocked_at is None:
                    blocked_at = time.monotonic()
                if ledger.blocked_until != until:
                    ledger.blocked_until = until
                    ledger.write(since)
                    print(_blocked_line(until, "the suite slot is not granted inside them"),
                          file=sys.stderr, flush=True)
                    if started is None:
                        started = time.monotonic()
                        if on_wait is not None:
                            on_wait(since)
                time.sleep(poll)
                ledger.re_rank(rank, since)
                continue
            if blocked_at is not None:
                blocked_total += time.monotonic() - blocked_at
                ledger.opened = opened + blocked_total    # held on purpose is not overdue
                blocked_at = None
                ledger.blocked_until = None
                ledger.write(since)
            if (not _must_queue(ledger.path, since, ledger.rank, ledger.task_rank)
                    and (fd := _take(d, k)) is not None):
                break
            if started is None:
                started = time.monotonic()
                ledger.write(since)
                print(f"⏳ waiting for a suite slot — all {k} busy: {_holders(d, k)} "
                      f"(CMX-119; CHELA_SUITE_SLOTS)", file=sys.stderr, flush=True)
                if on_wait is not None:
                    on_wait(since)
            elif (max_wait is not None
                  and time.time() - opened - blocked_total >= max_wait):
                raise SlotTimeout(f"no suite slot came free in {max_wait:.0f}s "
                                  f"(all {k} busy: {_holders(d, k)})")
            time.sleep(poll)
            ledger.re_rank(rank, since)
    finally:
        if started is not None:
            spent = time.monotonic() - started
            with _waited_lock:
                _waited += spent
            ledger.waited += spent
            _thread_waited[(threading.get_ident(), ledger.cwd)] = ledger.waited
            ledger.blocked_until = None
            ledger.write(None)      # kept: what the stuck probes credit, while we live
            if on_wait is not None:
                on_wait(None)
    try:
        os.ftruncate(fd, 0)
        os.pwrite(fd, json.dumps({"pid": os.getpid(), "cwd": cwd, "since": time.time(),
                                  "rank": ledger.rank,
                                  "task_rank": ledger.task_rank}).encode(), 0)
    except BaseException:
        os.close(fd)
        raise
    return fd


@contextlib.contextmanager
def slot(cwd: str | Path, on_wait=None, max_wait: float | None = None,
         poll: float | None = None, rank: int | None = None):
    """Hold one of the K suite slots for the ``with`` body; wait (visibly, in
    :func:`queue_key` order — ``rank`` first, then first come first served) for one first,
    ``rank`` None being that of the run whose worktree ``cwd`` is (:func:`run_rank`), at most ``max_wait`` seconds (:class:`SlotTimeout`).

    ``on_wait(since)`` is told when a wait begins (``since`` = its wall-clock start) and
    ends (``None``). With ``K == 0`` the body runs at once. A slot dir that cannot be made
    raises ``OSError`` — :func:`chela.judge.run_suite` reports that as CANNOT VERIFY.
    The body may hand the slot over and take it back (:func:`yield_slot`)."""
    if getattr(_held, "depth", 0):
        # 🧠 CMX-393: this thread already holds a slot (its battery's) — a suite run inside
        # the battery re-enters it. Taking a SECOND one would deadlock at K=1, and freeing
        # the battery's between mutations is exactly how batteries interleaved.
        _held.depth += 1
        try:
            yield
        finally:
            _held.depth -= 1
        return
    poll = SLOT_POLL_SECONDS if poll is None else poll
    k = slots()
    if k == 0:
        _held.depth = 1
        try:
            yield
        finally:
            _held.depth = 0
        return
    d = config.CHELA_DIR / SLOTS_DIRNAME
    d.mkdir(parents=True, exist_ok=True)
    claimed = time.time()
    windows = blocked_windows(cwd)
    _held.fd = _acquire(d, k, str(cwd), rank, on_wait, max_wait, poll, claimed, windows)
    _held.hold = (d, k, str(cwd), rank, on_wait, claimed, poll, windows)
    try:
        _held.depth = 1
        yield
    finally:
        _held.depth = 0
        fd, _held.fd, _held.hold = getattr(_held, "fd", None), None, None
        if fd is not None:
            os.close(fd)


def yield_slot() -> str:
    """⏳ CMX-445: call BETWEEN two suite runs of a battery (no file mutated). When a waiter
    outranks this thread's hold (:func:`outranking`) — an Urgent judge queued behind a Medium
    self-check — free the slot, queue for it again at this hold's own rank, and return once
    it is back. Returns who it yielded to (``""``: nobody outranked it, the slot was kept).

    ⏳ CMX-479: inside blocked hours it yields too, whoever is waiting, and returns
    ``"blocked hours until HH:MM"`` once the window is over and the slot is back.

    It queues again at its ORIGINAL claim time, so only a strictly better rank — never an
    equal that came after it — goes ahead, and it waits for the slot without a bound: every
    run ahead of it is bounded by its own budget, and a timeout here would throw away the
    experiments this battery already measured. Not holding a slot (``K == 0``, or outside a
    battery) is a no-op."""
    hold, fd = getattr(_held, "hold", None), getattr(_held, "fd", None)
    if hold is None or fd is None:
        return ""
    d, k, cwd, rank, on_wait, claimed, poll, windows = hold
    until = blocked_until(windows) if windows else ""
    # By its rank NOW (CMX-494): a raised issue stays put.
    better = [] if until else outranking(_ranks(cwd, rank)[0])
    if until:
        names = f"blocked hours until {until}"
        print(_blocked_line(until, "yielding the suite slot after this experiment"),
              file=sys.stderr, flush=True)
    elif better:
        names = ", ".join(better)
        print(f"⏳ yielding the suite slot to {names} — it outranks this run (CMX-445); "
              "resuming once it is free again", file=sys.stderr, flush=True)
    else:
        return ""
    # ⏳ CMX-479: between two experiments no file is mutated — killed in THIS wait, the
    # battery is a reaped waiter like any other.
    _set_battery_state(WAITING)
    _held.fd = None
    os.close(fd)
    _held.fd = _acquire(d, k, cwd, rank, on_wait, None, poll, claimed, windows)
    _set_battery_state(RUNNING)
    return names


# --- whole batteries: one slot for the battery, a record while it runs (CMX-393) ----------

BATTERIES_DIRNAME = "batteries"
# Memory that must be available before stopped runs are announced as resumable: one suite's
# measured peak — less, and a resume would only be reaped again.
RESUME_MEM_BYTES = SUITE_PEAK_BYTES


def _batteries_dir() -> Path:
    return config.CHELA_DIR / SLOTS_DIRNAME / BATTERIES_DIRNAME


@contextlib.contextmanager
def battery(cwd: str | Path, kind: str, on_wait=None, max_wait: float | None = None,
            poll: float | None = None, rank: int | None = None):
    """Hold ONE suite slot for a whole battery (``kind``: ``"sweep"``, ``"self-check"``,
    ``"judge"``, ``"task-finished"``), and keep a record of it while it runs.

    Every :func:`slot` taken inside — each ``run_suite`` of each mutation — re-enters this
    one, so no other battery can run a suite between two of ours. A nested ``battery`` (the
    sweep inside ``task-finished``) re-enters too and keeps the outer record.

    The record (``batteries/<pid>-<tid>.json``) is written BEFORE the slot wait (``state``
    ``"waiting"``, then ``"running"`` once the slot is held — CMX-479), removed when the
    battery ends — by returning, raising, or ``sys.exit`` — and KEPT when a signal stops it (an exception that
    is not an ``Exception``/``SystemExit``, e.g. the sweep's own ``_Interrupted``) or nothing
    runs at all (SIGKILL). A record whose process is gone is a stopped run:
    :func:`stopped_batteries`.

    ⏳ CMX-445: ``rank`` is where it queues (:func:`queue_key`); None ranks it by ``kind`` —
    a ``"judge"`` first, anything else by the priority of the run whose worktree ``cwd`` is
    (CMX-494, :func:`run_rank`), whichever command started it."""
    nested = bool(getattr(_held, "depth", 0))
    rank = _KIND_RANKS.get(kind) if rank is None else rank
    if nested:
        with slot(cwd, on_wait=on_wait, max_wait=max_wait, poll=poll, rank=rank):
            yield
        return
    # ⏳ CMX-479: recorded BEFORE the wait — on 11-23 the reaper killed three runs that were
    # only waiting, and a record written once the slot was held never saw them.
    rec = _battery_record(cwd, kind, WAITING)
    _held.battery = (cwd, kind, rec)
    try:
        with slot(cwd, on_wait=on_wait, max_wait=max_wait, poll=poll, rank=rank):
            _set_battery_state(RUNNING)
            yield
    except (Exception, SystemExit):
        _drop(rec)
        raise
    except BaseException:
        raise              # stopped by a signal — the record says so once we are gone
    finally:
        _held.battery = None
    _drop(rec)


# A battery record's ``state``: queued for the slot (no file mutated), or holding it.
WAITING, RUNNING = "waiting", "running"


def _set_battery_state(state: str) -> None:
    """Rewrite this thread's battery record as ``state``. One that cannot be rewritten is
    dropped: a stale ``"waiting"`` would call a run killed mid-mutation untouched."""
    cwd, kind, rec = getattr(_held, "battery", None) or (None, None, None)
    if rec is not None and _battery_record(cwd, kind, state) is None:
        _drop(rec)


def live_battery_in(worktree: str | Path) -> bool:
    """Is a live battery recorded in ``worktree`` now (waiting or running)?"""
    dead = {path for path, _rec in stopped_batteries()}
    try:
        root, files = Path(worktree).resolve(), list(_batteries_dir().glob("*.json"))
    except OSError:
        return False
    for path in files:
        if path in dead:
            continue
        try:
            here = Path(json.loads(path.read_text())["cwd"]).resolve()
        except (OSError, ValueError, KeyError, TypeError):
            continue
        if here == root or root in here.parents:
            return True
    return False


def _battery_record(cwd: str | Path, kind: str, state: str = RUNNING) -> Path | None:
    stat = _proc_stat(os.getpid())
    path = _batteries_dir() / f"{os.getpid()}-{threading.get_ident()}.json"
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"pid": os.getpid(), "start": stat[1] if stat else None,
                                   "cwd": str(cwd), "kind": kind, "state": state,
                                   "since": time.time()}))
        tmp.replace(path)
    except OSError:
        log.debug("suite_guard: could not record the battery", exc_info=True)
        return None
    return path


def _drop(path: Path | None) -> None:
    if path is not None:
        with contextlib.suppress(OSError):
            path.unlink(missing_ok=True)


def stopped_batteries() -> list[tuple[Path, dict]]:
    """Every battery record whose process is gone (or whose pid was reused): a sweep,
    self-check or hand-in that was killed before it could end — oldest first."""
    try:
        files = sorted(_batteries_dir().glob("*.json"))
    except OSError:
        return []
    out = []
    for path in files:
        try:
            rec = json.loads(path.read_text())
            pid = int(rec["pid"])
        except (OSError, ValueError, KeyError, TypeError):
            continue
        stat = _proc_stat(pid)
        if stat is not None and stat[0] != "Z" and stat[1] == rec.get("start"):
            continue                                   # still running
        out.append((path, rec))
    out.sort(key=lambda pr: float(pr[1].get("since") or 0))
    return out


def mem_available_bytes(meminfo: Path = Path("/proc/meminfo")) -> int | None:
    """``MemAvailable`` in bytes, or None when it cannot be read."""
    try:
        for line in meminfo.read_text().splitlines():
            if line.startswith("MemAvailable:"):
                return int(line.split()[1]) * 1024
    except (OSError, ValueError, IndexError):
        pass
    return None


def resumable(locks: Path = Path("/proc/locks"),
              meminfo: Path = Path("/proc/meminfo")) -> bool:
    """Is it safe to resume a stopped run now? No slot held, and at least one suite's peak
    of memory available. Memory that cannot be read is NOT safe — unknown never resumes."""
    avail = mem_available_bytes(meminfo)
    return not holders(locks) and avail is not None and avail >= RESUME_MEM_BYTES


# --- a waiter the reaper killed is re-queued ONCE (CMX-479) -------------------------------

REQUEUED_FILENAME = "requeued.json"
# A worktree re-queued within this long is not re-queued again — its next kill is announced
# instead. Longer than any one night's blocked window plus a queue, shorter than a day.
REQUEUE_ONCE_SECONDS = 12 * 3600


def _requeued_path() -> Path:
    return config.CHELA_DIR / SLOTS_DIRNAME / REQUEUED_FILENAME


def _requeued() -> dict:
    try:
        data = json.loads(_requeued_path().read_text())
    except FileNotFoundError:
        return {}
    except (OSError, ValueError):
        return {"?": time.time()}     # unreadable: treat as "already re-queued" (below)
    return data if isinstance(data, dict) else {"?": time.time()}


def requeued_recently(cwd: str | Path, now: float | None = None) -> bool:
    """Was a reaped waiter in ``cwd`` already re-queued within :data:`REQUEUE_ONCE_SECONDS`?
    A record that cannot be read says YES — unknown never buys a second re-queue."""
    now = time.time() if now is None else now
    data = _requeued()
    if "?" in data:
        return True
    at = data.get(str(Path(cwd).resolve()))
    if at is None:
        return False
    if isinstance(at, bool) or not isinstance(at, (int, float)):
        return True
    return now - at < REQUEUE_ONCE_SECONDS


def mark_requeued(cwd: str | Path, now: float | None = None) -> None:
    """Remember that ``cwd``'s reaped waiter was re-queued (:func:`requeued_recently`);
    entries older than :data:`REQUEUE_ONCE_SECONDS` are dropped on the way."""
    now = time.time() if now is None else now
    data = {k: v for k, v in _requeued().items()
            if k != "?" and isinstance(v, (int, float)) and now - v < REQUEUE_ONCE_SECONDS}
    data[str(Path(cwd).resolve())] = now
    path = _requeued_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data))
        tmp.replace(path)
    except OSError:
        log.warning("suite_guard: could not record the re-queue of %s", cwd, exc_info=True)
