"""⏱️ CMX-196: where a judge round's hour goes.

A judge round took 60-250 minutes in October 2026 and nothing said which part: the judge
stored one ``battery_seconds`` figure and no breakdown. :class:`RoundTimer` measures every
phase of one round — setup, provisioning, the baseline suite, each visible and held-out
experiment (with its selected test count), the consistency re-runs — and, inside each, the
time spent WAITING for a suite slot (``suite_slots``, CMX-119) rather than running tests.
:func:`chela.judge.record_private` writes it as the round record's ``timings`` object;
``chela judge show`` prints it and ``chela judge timings`` summarises it across tasks.

⛔ Measure only. Nothing here changes what a judge runs or what a verdict means, and a
round record without ``timings`` (written before CMX-196) still renders.
"""
from __future__ import annotations

import json
import logging
import math
import os
import re
import time
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger(__name__)

# The phases `chela judge timings` summarises, in the order a round runs them.
# `slot_wait` is the part of every other phase spent queued for a suite slot.
PHASES = ("setup", "reprovision", "provision", "baseline", "visible", "held_out",
          "consistency", "slot_wait", "run", "total")
EXPERIMENT_PHASE = "experiment"     # one row per experiment, whatever its kind

# ⏱️ CMX-397: the phases of a `chela task-finished` hand-in, in the order it runs them. A
# dotted name is a phase INSIDE the one before the dot (`sweep.baseline` is the sweep's own
# full-suite run; `….flake_rerun` is the re-run of a red baseline's failed tests).
HAND_IN_PHASES = ("lint", "suite",
                  "self_check", "self_check.baseline", "self_check.baseline.flake_rerun",
                  "self_check.experiments",
                  "sweep", "sweep.baseline", "sweep.baseline.flake_rerun", "sweep.mutations",
                  "remerge", "slot_wait", "run", "total")
# ⏱️ CMX-397: the phases of one nightly sweep (`chela.sweep_nightly`).
NIGHTLY_PHASES = ("provision", "sweep", "sweep.baseline", "sweep.baseline.flake_rerun",
                  "sweep.mutations", "slot_wait", "run", "total")
TASK_TOTAL = "task_total"           # a task's hand-in attempts, summed (`summarize_hand_ins`)
HAND_IN_DIRNAME = "hand-in-timings"
NIGHTLY_TIMINGS_FILENAME = "timings.jsonl"


class RoundTimer:
    """Wall-clock per phase and per experiment for ONE judge round. ``clock`` is injectable
    (a test drives it by hand); it must be monotonic seconds, as :func:`time.monotonic` is."""

    def __init__(self, clock=time.monotonic):
        self._clock = clock
        self._started = clock()
        self._phases: dict[str, dict] = {}
        self._experiments: list[dict] = []
        self._open: list[dict] = []          # every entry being timed right now, outermost first
        self._stage: list[str] = []          # names of the open phases
        self._slot_wait = 0.0
        self.load: dict | None = None
        self.meta: dict = {}                 # ⏱️ CMX-397: a hand-in's tier and checks

    def now(self) -> float:
        return self._clock()

    def record(self, name: str, seconds: float | None) -> None:
        """A phase measured elsewhere (``setup``: the judge launched before this process)."""
        if seconds is not None:
            entry = self._phases.setdefault(name, {"seconds": 0.0, "slot_wait": 0.0})
            entry["seconds"] += max(0.0, float(seconds))

    @contextmanager
    def phase(self, name: str):
        entry = self._phases.setdefault(name, {"seconds": 0.0, "slot_wait": 0.0})
        self._stage.append(name)
        try:
            with self._timing(entry):
                yield entry
        finally:
            self._stage.pop()

    @contextmanager
    def experiment(self, *, held_out: bool):
        """One experiment's row. ``stage`` is the phase it ran in — ``experiments`` for the
        first pass, ``consistency`` for a re-run."""
        entry = {"stage": self._stage[-1] if self._stage else "", "held_out": bool(held_out),
                 "seconds": 0.0, "slot_wait": 0.0, "selected": None,
                 "confirmed_full": False, "verdict": ""}
        self._experiments.append(entry)
        with self._timing(entry):
            yield entry

    @contextmanager
    def _timing(self, entry: dict):
        self._open.append(entry)
        t0 = self._clock()
        try:
            yield
        finally:
            entry["seconds"] += self._clock() - t0
            self._open = [e for e in self._open if e is not entry]

    def stage(self) -> str:
        """The innermost phase open now ("" outside every phase)."""
        return self._stage[-1] if self._stage else ""

    def slot_wait(self, seconds: float) -> None:
        """Credit a suite-slot wait to every entry open now (an experiment AND its phase)."""
        seconds = max(0.0, float(seconds))
        self._slot_wait += seconds
        for entry in self._open:
            entry["slot_wait"] += seconds

    def as_dict(self, total: float | None = None) -> dict:
        """The record's ``timings`` object. ``visible`` / ``held_out`` are the first-pass
        experiments of that kind, summed; ``run`` is this process's own wall-clock and
        ``total`` the whole round's (``setup`` included) when the caller knows it."""
        phases = {k: _rounded(v) for k, v in self._phases.items()}
        phases.pop("experiments", None)       # the sum of `visible` + `held_out` below
        for kind, held in (("visible", False), ("held_out", True)):
            rows = [e for e in self._experiments
                    if e["stage"] != "consistency" and e["held_out"] is held]
            if rows:
                phases[kind] = {"seconds": round(sum(e["seconds"] for e in rows), 1),
                                "slot_wait": round(sum(e["slot_wait"] for e in rows), 1),
                                "count": len(rows)}
        if "consistency" in phases:
            phases["consistency"]["count"] = sum(
                1 for e in self._experiments if e["stage"] == "consistency")
        run = self._clock() - self._started
        return {
            "phases": phases,
            "experiments": [_rounded(e) for e in self._experiments],
            "slot_wait": round(self._slot_wait, 1),
            "run": round(run, 1),
            "total": round(total if total is not None else run, 1),
            "load": self.load,
        }


def _rounded(entry: dict) -> dict:
    return {k: round(v, 1) if isinstance(v, float) else v for k, v in entry.items()}


def _phases(timings: dict) -> dict:
    phases = timings.get("phases")
    return phases if isinstance(phases, dict) else {}


def phase_seconds(timings: dict, names: tuple[str, ...] = PHASES) -> dict[str, float]:
    """``{phase: seconds}`` for one round's ``timings`` — every name in ``names`` it has."""
    phases = _phases(timings)
    out: dict[str, float] = {}
    for name in names:
        if name in ("slot_wait", "run", "total"):
            v = timings.get(name)
        else:
            entry = phases.get(name)
            v = entry.get("seconds") if isinstance(entry, dict) else None
        if isinstance(v, (int, float)):
            out[name] = float(v)
    return out


def percentile(values: list[float], q: float) -> float:
    """Nearest-rank percentile (``q`` in 0..100) — p50 of [1, 2, 3, 4] is 2, p90 of 1..10 is 9."""
    ordered = sorted(values)
    rank = max(1, math.ceil(q / 100 * len(ordered)))
    return ordered[rank - 1]


def _record_time(rec: dict) -> float | None:
    try:
        return datetime.strptime(str(rec.get("at")), "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=timezone.utc).timestamp()
    except ValueError:
        return None


def summarize(records: list[dict], since: float | None = None,
              now: float | None = None, names: tuple[str, ...] = PHASES) -> dict:
    """Median / p90 / max per phase across every non-stale round with ``timings``, recorded
    within ``since`` seconds of ``now`` (all of them when ``since`` is None). A round with
    no ``timings`` — older than CMX-196 — is counted in ``untimed``, never as zero."""
    now = time.time() if now is None else now
    samples: dict[str, list[float]] = {}
    rounds = untimed = 0
    for rec in records:
        if rec.get("stale"):
            continue
        if since is not None:
            at = _record_time(rec)
            if at is None or now - at > since:
                continue
        timings = rec.get("timings")
        if not isinstance(timings, dict):
            untimed += 1
            continue
        rounds += 1
        for name, v in phase_seconds(timings, names).items():
            samples.setdefault(name, []).append(v)
        for e in timings.get("experiments") or []:
            if isinstance(e, dict) and isinstance(e.get("seconds"), (int, float)):
                samples.setdefault(EXPERIMENT_PHASE, []).append(float(e["seconds"]))
    order = [*names, EXPERIMENT_PHASE]
    phases = {
        name: {"n": len(vs), "median": percentile(vs, 50), "p90": percentile(vs, 90),
               "max": max(vs)}
        for name in order if (vs := samples.get(name))
    }
    return {"rounds": rounds, "untimed": untimed, "phases": phases}


def duration(seconds: float | None) -> str:
    if seconds is None:
        return "?"
    seconds = max(0, int(round(seconds)))
    h, rest = divmod(seconds, 3600)
    m, s = divmod(rest, 60)
    if h:
        return f"{h}h {m:02d}m"
    return f"{m}m {s:02d}s" if m else f"{s}s"


def _with_wait(entry: dict) -> str:
    text = duration(entry.get("seconds"))
    wait = entry.get("slot_wait") or 0
    return f"{text} ({duration(wait)} slot wait)" if wait >= 1 else text


def round_lines(timings: dict | None) -> list[str]:
    """``chela judge show``'s per-round breakdown. ``None`` (a pre-CMX-196 round) says so."""
    if not isinstance(timings, dict):
        return ["⏱️ no timings recorded (a round from before CMX-196)"]
    phases = _phases(timings)
    parts = [f"total {duration(timings.get('total'))}"]
    for name in ("setup", "reprovision", "provision", "baseline", "visible", "held_out",
                 "consistency"):
        entry = phases.get(name)
        if isinstance(entry, dict):
            count = f" {entry['count']}×" if entry.get("count") else ""
            parts.append(f"{name.replace('_', '-')}{count} {_with_wait(entry)}")
    parts.append(f"slot wait {duration(timings.get('slot_wait'))}")
    lines = ["⏱️ " + " · ".join(parts)]
    load = timings.get("load")
    if isinstance(load, dict):
        lines.append(f"   load at start: {load.get('judges', '?')} judge(s) · "
                     f"{load.get('agents', '?')} agent(s) running (this judge included)")
    for n, e in enumerate(timings.get("experiments") or [], 1):
        if not isinstance(e, dict):
            continue
        kind = "held-out" if e.get("held_out") else "visible"
        if e.get("stage") == "consistency":
            kind += " re-run"
        tests = ("full suite" if e.get("selected") is None
                 else f"{e['selected']} selected test(s)"
                 + (" → full suite" if e.get("confirmed_full") else ""))
        lines.append(f"   exp {n:>2} {kind:16} {str(e.get('verdict') or '?'):9} {tests}, "
                     f"{_with_wait(e)}")
    return lines


def summary_lines(summary: dict, label: str, title: str = "judge timings",
                  unit: str = "round") -> list[str]:
    """``chela judge timings``: one row per phase, the biggest median first."""
    lines = [f"⏱️ {title} {label} — {summary['rounds']} timed {unit}(s)"
             + (f", {summary['untimed']} untimed (before CMX-196)" if summary["untimed"]
                else "")]
    outcomes = summary.get("outcomes")
    if outcomes:
        lines.append("  outcomes: " + ", ".join(f"{k} {v}" for k, v in sorted(outcomes.items()))
                     + f" — over {summary.get('tasks', '?')} task(s)")
    if not summary["phases"]:
        return lines
    lines.append(f"  {'phase':14} {'n':>4} {'median':>9} {'p90':>9} {'max':>9}")
    # The biggest phase first — what the next optimisation targets. `run`/`total` (the whole
    # round) and `experiment` (one experiment) are not phases of the same round, so they
    # close the table instead of topping it.
    whole = ("run", "total", EXPERIMENT_PHASE, TASK_TOTAL)
    ranked = sorted((kv for kv in summary["phases"].items() if kv[0] not in whole),
                    key=lambda kv: -kv[1]["median"])
    ranked += [(k, summary["phases"][k]) for k in whole if k in summary["phases"]]
    for name, s in ranked:
        lines.append(f"  {name:14} {s['n']:>4} {duration(s['median']):>9} "
                     f"{duration(s['p90']):>9} {duration(s['max']):>9}")
    return lines


# --- ⏱️ CMX-397: the same timing for `chela task-finished` and the nightly sweep ---------
#
# ⛔ Measure only, as above: nothing here changes what a hand-in or a sweep runs or decides.
# A timer is ACTIVE for one hand-in (or one nightly sweep) in this process; the code those
# share with the judge (`judge.run_baseline`, `sweep._sweep`, `judge._self_check`) opens a
# phase on it through :func:`within`, which does nothing when no timer is active — the judge
# never activates one, so its own rounds and records are exactly as before.

_ACTIVE: ContextVar["RoundTimer | None"] = ContextVar("chela_active_timer", default=None)


def _waited() -> float:
    from chela import suite_guard

    return suite_guard.waited_seconds()


def active() -> "RoundTimer | None":
    return _ACTIVE.get()


@contextmanager
def activate(timer: RoundTimer):
    """Make ``timer`` the active one for the block. Every suite-slot wait in the block is
    credited to it (the process-wide wait counter, so a wait inside a nested battery
    counts too)."""
    token = _ACTIVE.set(timer)
    w0 = _waited()
    try:
        yield timer
    finally:
        timer.slot_wait(_waited() - w0)
        _ACTIVE.reset(token)


@contextmanager
def within(name: str):
    """A phase on the active timer, named under the phase open now (``sweep`` then
    ``baseline`` is ``sweep.baseline``); its slot wait is what the block waited. Without an
    active timer it yields None and measures nothing."""
    timer = _ACTIVE.get()
    if timer is None:
        yield None
        return
    full = f"{timer.stage()}.{name}" if timer.stage() else name
    w0 = _waited()
    with timer.phase(full) as entry:
        try:
            yield entry
        finally:
            entry["slot_wait"] += max(0.0, _waited() - w0)


def note(**fields) -> None:
    """Attach ``fields`` (a tier, the checks run) to the active timer's record."""
    timer = _ACTIVE.get()
    if timer is not None:
        timer.meta.update(fields)


def utc_stamp(now: float | None = None) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() if now is None else now))


def append_record(path: Path, rec: dict) -> bool:
    """Append one JSON line to ``path`` (0600, its directory 0700). Never raises — a timing
    that could not be written is logged; the work it measured stands."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        with os.fdopen(fd, "a") as fh:
            fh.write(json.dumps(rec) + "\n")
        return True
    except Exception:  # noqa: BLE001 — the record is not the work
        log.exception("timings: could not append to %s", path)
        return False


def read_records(path: Path) -> list[dict]:
    """Every JSON-object line of ``path``; a missing file is no records, a torn line is
    skipped."""
    try:
        lines = path.read_text().splitlines()
    except OSError:
        return []
    out = []
    for line in lines:
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        if isinstance(rec, dict):
            out.append(rec)
    return out


def hand_in_dir(chela_dir: Path | None = None) -> Path:
    from chela import config

    return Path(chela_dir or config.CHELA_DIR) / HAND_IN_DIRNAME


def hand_in_path(task_id: str, chela_dir: Path | None = None) -> Path:
    safe = re.sub(r"[^A-Za-z0-9_.-]", "_", task_id) or "_"
    return hand_in_dir(chela_dir) / f"{safe}.jsonl"


def record_hand_in(task_id: str, timer: RoundTimer, outcome: str,
                   chela_dir: Path | None = None, now: float | None = None) -> bool:
    """Append one ``chela task-finished`` attempt — handed in or refused, a refusal costs
    the slot too — to ``hand-in-timings/<task>.jsonl``. Never raises."""
    try:
        rec = {"task_id": task_id, "at": utc_stamp(now), "outcome": outcome,
               **timer.meta, "timings": timer.as_dict()}
    except Exception:  # noqa: BLE001 — the record is not the work
        log.exception("timings: could not build the hand-in record for %s", task_id)
        return False
    return append_record(hand_in_path(task_id, chela_dir), rec)


def load_hand_ins(chela_dir: Path | None = None) -> list[dict]:
    out: list[dict] = []
    for path in sorted(hand_in_dir(chela_dir).glob("*.jsonl")):
        out.extend(read_records(path))
    return out


def summarize_hand_ins(records: list[dict], since: float | None = None,
                       now: float | None = None) -> dict:
    """:func:`summarize` over hand-in attempts, plus how they ended and ``task_total`` — the
    slot time of ALL of one task's attempts, refusals included: what a flake that restarts a
    hand-in really costs."""
    summary = summarize(records, since, now, HAND_IN_PHASES)
    kept = [r for r in records if not r.get("stale") and isinstance(r.get("timings"), dict)
            and (since is None or _within_since(r, since, now))]
    outcomes: dict[str, int] = {}
    per_task: dict[str, float] = {}
    for r in kept:
        outcomes[str(r.get("outcome") or "?")] = outcomes.get(str(r.get("outcome") or "?"), 0) + 1
        total = r["timings"].get("total")
        if isinstance(total, (int, float)):
            key = str(r.get("task_id") or "?")
            per_task[key] = per_task.get(key, 0.0) + float(total)
    summary["outcomes"] = outcomes
    summary["tasks"] = len(per_task)
    if per_task:
        vs = list(per_task.values())
        summary["phases"][TASK_TOTAL] = {"n": len(vs), "median": percentile(vs, 50),
                                         "p90": percentile(vs, 90), "max": max(vs)}
    return summary


def _within_since(rec: dict, since: float, now: float | None) -> bool:
    at = _record_time(rec)
    return at is not None and (time.time() if now is None else now) - at <= since


def hand_in_line(timings: dict, outcome: str) -> str:
    """The one line ``chela task-finished`` prints at its end: total, slot wait, each phase."""
    phases = _phases(timings)
    parts = [f"total {duration(timings.get('total'))}",
             f"slot wait {duration(timings.get('slot_wait'))}"]
    for name in HAND_IN_PHASES:
        entry = phases.get(name)
        if isinstance(entry, dict):
            parts.append(f"{name} {_with_wait(entry)}")
    return f"⏱️ hand-in ({outcome}): " + " · ".join(parts)
