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

import math
import time
from contextlib import contextmanager
from datetime import datetime, timezone

# The phases `chela judge timings` summarises, in the order a round runs them.
# `slot_wait` is the part of every other phase spent queued for a suite slot.
PHASES = ("setup", "reprovision", "provision", "baseline", "visible", "held_out",
          "consistency", "slot_wait", "run", "total")
EXPERIMENT_PHASE = "experiment"     # one row per experiment, whatever its kind


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


def phase_seconds(timings: dict) -> dict[str, float]:
    """``{phase: seconds}`` for one round's ``timings`` — every :data:`PHASES` name it has."""
    phases = _phases(timings)
    out: dict[str, float] = {}
    for name in PHASES:
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
              now: float | None = None) -> dict:
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
        for name, v in phase_seconds(timings).items():
            samples.setdefault(name, []).append(v)
        for e in timings.get("experiments") or []:
            if isinstance(e, dict) and isinstance(e.get("seconds"), (int, float)):
                samples.setdefault(EXPERIMENT_PHASE, []).append(float(e["seconds"]))
    order = [*PHASES, EXPERIMENT_PHASE]
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


def summary_lines(summary: dict, label: str) -> list[str]:
    """``chela judge timings``: one row per phase, the biggest median first."""
    lines = [f"⏱️ judge timings {label} — {summary['rounds']} timed round(s)"
             + (f", {summary['untimed']} untimed (before CMX-196)" if summary["untimed"]
                else "")]
    if not summary["phases"]:
        return lines
    lines.append(f"  {'phase':14} {'n':>4} {'median':>9} {'p90':>9} {'max':>9}")
    # The biggest phase first — what the next optimisation targets. `run`/`total` (the whole
    # round) and `experiment` (one experiment) are not phases of the same round, so they
    # close the table instead of topping it.
    whole = ("run", "total", EXPERIMENT_PHASE)
    ranked = sorted((kv for kv in summary["phases"].items() if kv[0] not in whole),
                    key=lambda kv: -kv[1]["median"])
    ranked += [(k, summary["phases"][k]) for k in whole if k in summary["phases"]]
    for name, s in ranked:
        lines.append(f"  {name:14} {s['n']:>4} {duration(s['median']):>9} "
                     f"{duration(s['p90']):>9} {duration(s['max']):>9}")
    return lines
