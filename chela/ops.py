"""``chela ops scan`` (CMX-260) — a rule-based, READ-ONLY anomaly scan of the fleet.

Every fleet bug of the week of 2026-10-18 was found by the orchestrator noticing an oddity by
hand. Each rule below is one of those oddities, made automatic; each names its incident.

* **Never on the critical path.** The daemon only ever *spawns* ``chela ops scan --notify``
  (:func:`spawn`) — niced to 19, ionice idle, its own session — and never waits for it.
* **Read-only.** It reads ``events.jsonl`` (its tail), ``judge-heldout/*.jsonl``, the runs
  table (a ``mode=ro`` sqlite open) and ``/proc``. It never signals, merges or edits anything;
  the only writes are under ``$CHELA_DIR/ops/`` plus ONE inbox notice for NEW findings.
* **Cheap.** The rules run in one worker thread under a hard :data:`BUDGET_S`; a scan that
  overruns reports ``partial`` with what it finished.
* **Skips while a fleet pause** (``chela dispatch --pause --all``) **is active.**
"""
from __future__ import annotations

import calendar
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import threading
import time
from functools import cached_property
from pathlib import Path

from chela import config

BUDGET_S = 10.0
INTERVAL_S = 30 * 60
RENOTIFY_S = 6 * 3600
TAIL_BYTES = 2 * 1024 * 1024
HUNG_AFTER_S = 20 * 60
HUNG_CPU = 0.01
FLAKE_WINDOW_S = 24 * 3600
STORM_WINDOW_S = 3600
STORM_COUNT = 3
WHACK_ROUNDS = 3
OVER_STATUSES = ("done", "closed", "needs_human")
CI_UNSETTLED = ("QUEUED", "IN_PROGRESS", "PENDING", "WAITING", "REQUESTED", "EXPECTED")
TEST_RUNNERS = ("pytest", "py.test", "node", "vitest")
DETACHED_FLAG = "--detached-child"         # chela judge run --detach's own child
FLEET_PAUSE_FILE = "fleet-pause.json"     # CMX-214's `--pause --all` state

_TEST_ID_RE = re.compile(r"[\w./-]+::[^\s,;]+")
_JUDGE_FILE_RE = re.compile(r"\*\*File:\*\* `([^`]+)`\s*```diff\n- ([^\n]*)")


def ops_dir(chela_dir: Path | None = None) -> Path:
    return (chela_dir or config.CHELA_DIR) / "ops"


def _tail_jsonl(path: Path, limit: int | None = None) -> list[dict]:
    """The parseable records in the last ``limit`` bytes of ``path``; [] when unreadable."""
    limit = limit or TAIL_BYTES
    try:
        with open(path, "rb") as fh:
            size = fh.seek(0, os.SEEK_END)
            fh.seek(max(0, size - limit))
            data = fh.read()
    except OSError:
        return []
    lines = data.splitlines()
    if len(data) >= limit and lines:
        lines = lines[1:]                     # the first line was cut by the seek
    out = []
    for line in lines:
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        if isinstance(rec, dict):
            out.append(rec)
    return out


class Sources:
    """Everything the rules read. Tests build one over a fake CHELA_DIR and a fake /proc."""

    def __init__(self, chela_dir: Path, proc_root: Path = Path("/proc"),
                 events_path: Path | None = None, now: float | None = None):
        self.chela_dir, self.proc_root = Path(chela_dir), Path(proc_root)
        self.events_path = events_path or self.chela_dir / "events.jsonl"
        self.now = time.time() if now is None else now
        self.worktrees = self.chela_dir / "worktrees"

    @cached_property
    def events(self) -> list[dict]:
        return _tail_jsonl(self.events_path)

    @cached_property
    def heldout(self) -> list[dict]:
        out = []
        for path in sorted((self.chela_dir / "judge-heldout").glob("*.jsonl")):
            try:
                if path.stat().st_mtime < self.now - FLAKE_WINDOW_S:
                    continue
            except OSError:
                continue
            out.extend(_tail_jsonl(path, 256 * 1024))
        return out

    @cached_property
    def runs(self) -> dict[str, str]:
        """task id → status of its LATEST run, read through a read-only connection."""
        db = self.chela_dir / "scheduler.db"
        if not db.exists():
            return {}
        conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=2)
        try:
            rows = conn.execute("SELECT task_id, status FROM runs ORDER BY started_at").fetchall()
        finally:
            conn.close()
        return {task: status for task, status in rows}

    @cached_property
    def procs(self) -> list[dict]:
        hz = os.sysconf("SC_CLK_TCK")
        uptime = float((self.proc_root / "uptime").read_text().split()[0])
        out = []
        for entry in self.proc_root.iterdir():
            if not entry.name.isdigit() or int(entry.name) == os.getpid():
                continue
            try:
                stat = (entry / "stat").read_text()
                argv = (entry / "cmdline").read_bytes().split(b"\0")
                cwd = os.readlink(entry / "cwd")
            except OSError:
                continue                          # gone, or not ours to read
            rest = stat[stat.rindex(")") + 2:].split()
            out.append({"pid": int(entry.name), "ppid": int(rest[1]), "cwd": cwd,
                        "argv": [a.decode(errors="replace") for a in argv if a],
                        "start": int(rest[19]), "cpu_s": (int(rest[11]) + int(rest[12])) / hz,
                        "age_s": uptime - int(rest[19]) / hz})
        return out

    def worktree_of(self, proc: dict) -> tuple[str, str] | None:
        """``(task, "run"|"judge")`` when the process's cwd or argv is under a worktree."""
        for path in (proc["cwd"], *proc["argv"]):
            try:
                parts = Path(path).relative_to(self.worktrees).parts
            except ValueError:
                continue
            if len(parts) >= 2:
                name = parts[1]
                return (name[6:], "judge") if name.startswith("judge-") else (name, "run")
        return None


def _finding(rule: str, subject: str, summary: str, **evidence) -> dict:
    return {"rule": rule, "subject": subject, "summary": summary, "evidence": evidence}


def _is_test_runner(argv: list[str]) -> bool:
    return bool(argv) and (Path(argv[0]).name in TEST_RUNNERS
                           or any(a == "-m" and b == "pytest" for a, b in zip(argv, argv[1:])))


def rule_hung_test(src: Sources, state: dict) -> list[dict]:
    """CMX-205: a test run under a worktree at ≤1% CPU for ≥20 min (75 min at 0% on 10-18).
    Measured since the previous scan's sample when there is one, else over its whole life."""
    out, prev = [], state.get("cpu") or {}
    for p in src.procs:
        wt = src.worktree_of(p)
        if not wt or not _is_test_runner(p["argv"]):
            continue
        seen = prev.get(f"{p['pid']}:{p['start']}")
        span, used = ((src.now - seen[0], p["cpu_s"] - seen[1]) if seen
                      else (p["age_s"], p["cpu_s"]))
        if span >= HUNG_AFTER_S and used <= HUNG_CPU * span:
            out.append(_finding(
                "hung_test", f"pid:{p['pid']}",
                f"{Path(p['argv'][0]).name} pid {p['pid']} under {wt[1]} {wt[0]} at "
                f"{100 * used / span:.1f}% CPU for {span / 60:.0f}m",
                pid=p["pid"], task=wt[0], argv=p["argv"][:6], cwd=p["cwd"],
                span_s=round(span), cpu_s=round(used, 2)))
    return out


def rule_leaked_proc(src: Sources, state: dict) -> list[dict]:
    """CMX-253: a process under a run/judge worktree whose run is over, or that was orphaned.
    A ``--detached-child`` judge is setsid'd ON PURPOSE, so ppid 1 alone is not a leak there."""
    out = []
    for p in src.procs:
        wt = src.worktree_of(p)
        if not wt:
            continue
        status = src.runs.get(wt[0])
        orphan = p["ppid"] == 1 and DETACHED_FLAG not in p["argv"]
        if status in OVER_STATUSES or orphan:
            why = f"its run is {status}" if status in OVER_STATUSES else "it is orphaned (ppid 1)"
            out.append(_finding(
                "leaked_proc", f"pid:{p['pid']}",
                f"pid {p['pid']} under {wt[1]} {wt[0]} is alive but {why}",
                pid=p["pid"], ppid=p["ppid"], task=wt[0], run_status=status,
                argv=p["argv"][:6], cwd=p["cwd"]))
    return out


def rule_ci_red_unsettled(src: Sources, state: dict) -> list[dict]:
    """CMX-256: a ``run_ci_red`` naming no job, or emitted while the head's checks ran."""
    out = []
    for e in src.events:
        if e.get("type") != "run_ci_red":
            continue
        pl = e.get("payload") or {}
        named = [f for f in (str(f).strip() for f in pl.get("failing") or []) if f]
        pending = [c.get("name") for c in pl.get("checks") or []
                   if isinstance(c, dict) and str(c.get("status", "")).upper() in CI_UNSETTLED]
        if named and not pending:
            continue
        why = "named no failing job" if not named else f"checks still running: {pending}"
        sha = str(pl.get("ci_sha") or pl.get("pr_head_sha") or "")[:7]
        out.append(_finding("ci_red_unsettled", f"{pl.get('task_id')}@{sha}",
                            f"run_ci_red for {pl.get('task_id')} on {sha} {why}",
                            seq=e.get("seq"), ts=e.get("ts"), task=pl.get("task_id")))
    return out


def _at(rec: dict) -> float:
    try:
        return calendar.timegm(time.strptime(rec.get("at") or "", "%Y-%m-%dT%H:%M:%SZ"))
    except ValueError:
        return 0.0


def rule_repeat_flake(src: Sources, state: dict) -> list[dict]:
    """CMX-195/257: one test id red in the judge baselines of ≥2 TASKS within 24 h. Distinct
    tasks, because a test one branch turned red repeats on every round of that branch."""
    seen: dict[str, dict[str, float]] = {}
    for rec in src.heldout:
        at = _at(rec)
        if at < src.now - FLAKE_WINDOW_S:
            continue
        ids = set(rec.get("baseline_flakes") or [])
        ids |= {m.rstrip(".,;)") for m in _TEST_ID_RE.findall(rec.get("cannot_verify") or "")}
        for tid in ids:
            seen.setdefault(tid, {})[str(rec.get("task_id"))] = at
    return [_finding("repeat_flake", tid, f"{tid} failed judge baselines of {len(tasks)} "
                     f"tasks in 24h: {', '.join(sorted(tasks))}", test=tid, tasks=tasks)
            for tid, tasks in sorted(seen.items()) if len(tasks) >= 2]


def rule_judge_whack_a_mole(src: Sources, state: dict) -> list[dict]:
    """CMX-176/259: ≥3 judge rounds whose survivors move to a new file/line every round."""
    latest: dict[str, dict] = {}
    for e in src.events:
        if e.get("type") == "run_changes_requested":
            latest[str((e.get("payload") or {}).get("task_id"))] = e
    out = []
    for task, e in sorted(latest.items()):
        rounds = [set(_JUDGE_FILE_RE.findall(r.get("body") or ""))
                  for r in (e.get("payload") or {}).get("reviews") or [] if isinstance(r, dict)]
        rounds = [r for r in rounds if r]
        if len(rounds) >= WHACK_ROUNDS and all(not (a & b) for a, b in zip(rounds, rounds[1:])):
            files = [sorted({f for f, _ in r}) for r in rounds]
            out.append(_finding("judge_whack_a_mole", task,
                                f"{task}: {len(rounds)} judge rounds, survivors moved every "
                                f"round: {files}", seq=e.get("seq"), task=task, rounds=files))
    return out


def rule_notice_storm(src: Sources, state: dict) -> list[dict]:
    """CMX-250: the same notice kind for the same run ≥3 times within 1 h."""
    by: dict[tuple, list] = {}
    for e in src.events:
        task = (e.get("payload") or {}).get("task_id")
        if str(e.get("type", "")).startswith("run_") and task and e.get("ts"):
            by.setdefault((e["type"], task), []).append(e)
    out = []
    for (kind, task), evs in sorted(by.items()):
        evs.sort(key=lambda e: e["ts"])
        for i in range(len(evs) - STORM_COUNT + 1):
            burst = evs[i:i + STORM_COUNT]
            if burst[-1]["ts"] - burst[0]["ts"] <= STORM_WINDOW_S:
                out.append(_finding("notice_storm", f"{kind}:{task}",
                                    f"{kind} for {task} {STORM_COUNT}x within "
                                    f"{(burst[-1]['ts'] - burst[0]['ts']) / 60:.0f}m",
                                    seqs=[e.get("seq") for e in burst], task=task))
                break
    return out


RULES = (rule_hung_test, rule_leaked_proc, rule_ci_red_unsettled, rule_repeat_flake,
         rule_judge_whack_a_mole, rule_notice_storm)


def scan(src: Sources, state: dict | None = None, budget: float = BUDGET_S,
         rules=RULES) -> dict:
    """Run every rule within ``budget`` seconds. A rule that raises is reported, never fatal;
    rules the budget never reached make the result ``partial``."""
    state, findings, done, errors = state or {}, [], [], {}

    def work():
        for rule in rules:
            name = rule.__name__.removeprefix("rule_")
            try:
                findings.extend(rule(src, state))
            except Exception as e:  # noqa: BLE001 — one broken source must not hide the rest
                errors[name] = f"{type(e).__name__}: {e}"
            done.append(name)

    worker = threading.Thread(target=work, daemon=True, name="ops-scan")
    worker.start()
    worker.join(budget)
    finished = list(done)
    return {"ts": src.now, "findings": list(findings), "partial": len(finished) < len(rules),
            "rules_done": finished, "errors": dict(errors)}


def fleet_paused(chela_dir: Path) -> bool:
    """A fleet pause file that exists — even one we cannot parse — means paused."""
    return os.path.lexists(chela_dir / FLEET_PAUSE_FILE)


def record(chela_dir: Path, result: dict, state: dict, src: Sources) -> list[dict]:
    """Append the findings to ``ops/findings.jsonl``, update the dedupe state, and return the
    ones to notify: never notified, or last notified ≥ :data:`RENOTIFY_S` ago. A finding that
    is gone from a rule that COMPLETED is forgotten, so its recurrence is new again."""
    notified, now = dict(state.get("notified") or {}), result["ts"]
    keys = {f"{f['rule']}:{f['subject']}" for f in result["findings"]}
    for key in list(notified):
        if key not in keys and key.split(":", 1)[0] in result["rules_done"]:
            del notified[key]
    fresh = []
    for f in result["findings"]:
        key = f"{f['rule']}:{f['subject']}"
        if now - notified.get(key, float("-inf")) >= RENOTIFY_S:
            notified[key] = now
            fresh.append(f)
    out = ops_dir(chela_dir)
    out.mkdir(parents=True, exist_ok=True)
    with open(out / "findings.jsonl", "a", encoding="utf-8") as fh:
        for f in result["findings"]:
            fh.write(json.dumps(dict(f, ts=now, new=f in fresh)) + "\n")
    cpu = {f"{p['pid']}:{p['start']}": [now, p["cpu_s"]] for p in src.procs} \
        if "hung_test" in result["rules_done"] else state.get("cpu") or {}
    (out / "state.json").write_text(json.dumps({"notified": notified, "cpu": cpu}))
    return fresh


def load_state(chela_dir: Path) -> dict:
    try:
        state = json.loads((ops_dir(chela_dir) / "state.json").read_text())
    except (OSError, ValueError):
        return {}
    return state if isinstance(state, dict) else {}


def scheduled(chela_dir: Path | None = None, proc_root: Path = Path("/proc")) -> dict:
    """What ``chela ops scan --notify`` (the daemon's child) does: scan, record, and queue ONE
    inbox notice for the orchestrator when there are new findings."""
    from chela import event_log, inbox

    chela_dir = chela_dir or config.CHELA_DIR
    if fleet_paused(chela_dir):
        return {"skipped": "a fleet pause (--pause --all) is active", "findings": []}
    state = load_state(chela_dir)
    src = Sources(chela_dir, proc_root, events_path=event_log.log_path())
    result = scan(src, state)
    fresh = record(chela_dir, result, state, src)
    if fresh:
        head = "; ".join(f["summary"] for f in fresh[:3])
        more = f" (+{len(fresh) - 3} more)" if len(fresh) > 3 else ""
        inbox.enqueue("ops_findings",
                      f"🔎 ops scan: {len(fresh)} new finding(s) — {head}{more} — chela ops scan",
                      {"findings": fresh, "partial": result["partial"]})
    return dict(result, notified=len(fresh))


def spawn_argv() -> list[str]:
    nice = ["nice", "-n", "19"] if shutil.which("nice") else []
    ionice = ["ionice", "-c", "3"] if shutil.which("ionice") else []
    return [*nice, *ionice, sys.executable, "-m", "chela.main", "ops", "scan", "--notify"]


def spawn(prev: subprocess.Popen | None = None) -> subprocess.Popen:
    """Start the scheduled scan detached and return at once. The daemon never waits on it;
    a previous scan that is still running is left alone instead of doubled."""
    if prev is not None and prev.poll() is None:
        return prev
    out = ops_dir()
    out.mkdir(parents=True, exist_ok=True)
    with open(out / "scan.log", "ab") as log:
        return subprocess.Popen(spawn_argv(), stdin=subprocess.DEVNULL, stdout=log,
                                stderr=subprocess.STDOUT, start_new_session=True,
                                close_fds=True)


def render(result: dict) -> str:
    if result.get("skipped"):
        return f"ops scan: skipped — {result['skipped']}"
    lines = [f"ops scan: {len(result['findings'])} finding(s)"
             + (f" — PARTIAL (finished: {', '.join(result['rules_done']) or 'none'})"
                if result["partial"] else "")]
    lines += [f"  rule {k} raised {v}" for k, v in result["errors"].items()]
    for f in result["findings"]:
        lines.append(f"  [{f['rule']}] {f['summary']}\n      {json.dumps(f['evidence'])}")
    return "\n".join(lines)
