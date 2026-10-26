"""``chela ops scan`` (CMX-260) — a rule-based, READ-ONLY anomaly scan of the fleet.

Every fleet bug of the week of 2026-10-18 was found by the orchestrator noticing an oddity by
hand. Each rule below is one of those oddities, made automatic; each names its incident.

* **Never on the critical path.** The daemon only ever *spawns* ``chela ops scan --notify``
  (:func:`spawn`) — niced to 19, ionice idle, its own session — and never waits for it.
* **Read-only.** It reads ``events-core.jsonl`` (its tail: chela's own events and never a
  ``hook.*`` one, so it reaches back days, not minutes — CMX-328), ``judge-heldout/*.jsonl``,
  the runs table (a ``mode=ro`` sqlite open) and ``/proc``. It never signals, merges or edits anything;
  the only writes are under ``$CHELA_DIR/ops/`` plus ONE inbox notice for NEW findings.
* **Cheap.** The rules run in one worker thread under a hard :data:`BUDGET_S`; a scan that
  overruns reports ``partial`` with what it finished.
* **Skips while a fleet pause** (``chela dispatch --pause --all``) **is active.**
"""
from __future__ import annotations

import ast
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
from datetime import datetime
from functools import cached_property
from pathlib import Path

from chela import config, fleet

BUDGET_S = 10.0
INTERVAL_S = 30 * 60
RENOTIFY_S = 6 * 3600
TAIL_BYTES = 2 * 1024 * 1024
HUNG_AFTER_S = 20 * 60
HUNG_CPU = 0.01
FLAKE_WINDOW_S = 24 * 3600
QUARANTINE_WINDOW_S = 48 * 3600
GIT_TIMEOUT_S = 5
STORM_WINDOW_S = 3600
STORM_COUNT = 3
WHACK_ROUNDS = 3
OVER_STATUSES = ("done", "closed", "needs_human")
TEST_RUNNERS = ("pytest", "py.test", "vitest")
DETACHED_FLAG = "--detached-child"         # chela judge run --detach's own child
DAEMON_MARKERS = ("God Daemon (",)         # argv[0] of a self-daemonizing proc: ppid 1 by design
TMUX_ARG_FLAGS = ("-c", "-f", "-S", "-T")    # tmux's other global flags that take a value
TEST_TMUX_SOCKET_RE = re.compile(r"chelatest-(\d+)-")  # a test's `tmux -L chelatest-<its pid>-…`
SUITE_SCOPE_RE = re.compile(r"/(chela-suite-[0-9a-f]+)\.scope$")  # suite_guard.bounded's unit
DAEMON_PERSIST_SCANS = 2                   # CMX-353: a daemon is a leak on its 2nd scan in a row
DAEMON_DO_NOT_KILL = "do not kill while a suite runs"
FLEET_PAUSE_FILE = fleet.STATE_FILE_NAME  # CMX-214's `--pause --all` state

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
        self.events_path = events_path or self.chela_dir / "events-core.jsonl"
        self.now = time.time() if now is None else now
        self.worktrees = self.chela_dir / "worktrees"
        self._environ: dict[int, dict[str, str] | None] = {}
        self._scopes: dict[int, str | None] = {}
        self.daemon_suspects: set[str] = set()   # CMX-353: this scan's, kept by record()
        self._fixes: dict[tuple, tuple[Path, str] | None] = {}
        self._trees: dict[tuple, set[str] | None] = {}

    @cached_property
    def events(self) -> list[dict]:
        # Right after the core log rolls, the current file holds almost nothing: top the
        # tail up from the rolled ``.1`` (CMX-328).
        try:
            size = self.events_path.stat().st_size
        except OSError:
            size = 0
        older: list[dict] = []
        if size < TAIL_BYTES:
            rolled = self.events_path.with_name(self.events_path.name + ".1")
            older = _tail_jsonl(rolled, TAIL_BYTES - size)
        return older + _tail_jsonl(self.events_path)

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
    def ci_flakes(self) -> list[tuple[str, dict]]:
        """CMX-282: ``(task id, flake)`` for every CMX-255 flake record — a red re-run GREEN
        on the same sha, so a failure the code did not cause."""
        db = self.chela_dir / "scheduler.db"
        if not db.exists():
            return []
        conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=2)
        try:
            try:
                rows = conn.execute("SELECT task_id, ci_flakes, workflow_path FROM runs "
                                    "WHERE ci_flakes IS NOT NULL").fetchall()
            except sqlite3.OperationalError:      # no workflow_path: no repo to ask git
                rows = conn.execute("SELECT task_id, ci_flakes, NULL FROM runs "
                                    "WHERE ci_flakes IS NOT NULL").fetchall()
        except sqlite3.OperationalError:          # a runs table older than CMX-255
            return []
        finally:
            conn.close()
        out = []
        for task, raw, wf in rows:
            try:
                flakes = json.loads(raw)
            except ValueError:
                continue
            out += [(task, dict(f, workflow=wf)) for f in flakes
                    if isinstance(f, dict) and "::" in str(f.get("test"))]
        return out

    def _git(self, repo: Path, *args: str) -> subprocess.CompletedProcess | None:
        try:
            return subprocess.run(["git", "-C", str(repo), *args], capture_output=True,
                                  text=True, errors="replace", timeout=GIT_TIMEOUT_S)
        except (OSError, subprocess.SubprocessError):
            return None

    def last_fix(self, workflow_path: str | None, test_file: str) -> tuple[Path, str] | None:
        """CMX-298: ``(repo, sha)`` of the latest commit on ``origin/<base_branch>`` that
        touched ``test_file`` or a repo module it imports; None when any of it is unreadable."""
        if (workflow_path, test_file) not in self._fixes:
            self._fixes[(workflow_path, test_file)] = self._last_fix(workflow_path, test_file)
        return self._fixes[(workflow_path, test_file)]

    def _last_fix(self, workflow_path: str | None, test_file: str) -> tuple[Path, str] | None:
        from chela import workflow

        if not workflow_path:
            return None
        try:
            wf = workflow.load_workflow(workflow_path)
        except Exception:  # noqa: BLE001 — unreadable or unparseable: no base to read
            return None
        repo, ref = wf.path.parent, f"origin/{wf.get('workspace', 'base_branch', default='master')}"
        show = self._git(repo, "show", f"{ref}:{test_file}")
        tree = self._tree(repo, ref)
        if not show or show.returncode or tree is None:
            return None
        try:
            mods = _imported_paths(show.stdout, test_file)
        except (SyntaxError, ValueError):
            mods = set()
        paths = [test_file, *sorted(mods & tree)]
        log = self._git(repo, "log", "-1", "--format=%H", ref, "--", *paths)
        sha = log.stdout.strip() if log and not log.returncode else ""
        return (repo, sha) if sha else None

    def _tree(self, repo: Path, ref: str) -> set[str] | None:
        """Every file path at ``ref``, listed once per scan; None when unreadable."""
        if (repo, ref) not in self._trees:
            got = self._git(repo, "ls-tree", "-r", "--name-only", ref)
            self._trees[(repo, ref)] = (set(got.stdout.splitlines())
                                        if got and not got.returncode else None)
        return self._trees[(repo, ref)]

    def predates_fix(self, flake: dict) -> bool:
        """CMX-298: True only when git PROVES the flake's head lacks :meth:`last_fix` — a
        flake from before the test (or what it imports) was last changed. Unknown ⇒ False:
        the flake still counts."""
        fix = self.last_fix(flake.get("workflow"), str(flake["test"]).split("::", 1)[0])
        sha = str(flake.get("sha") or "")
        if fix is None or not sha:
            return False
        got = self._git(fix[0], "merge-base", "--is-ancestor", fix[1], sha)
        return got is not None and got.returncode == 1

    @cached_property
    def procs(self) -> list[dict]:
        """Every readable process, in pid order: a /proc listing comes back in whatever order
        the filesystem keeps, and the rules emit findings in this list's order (CMX-285)."""
        hz = os.sysconf("SC_CLK_TCK")
        uptime = float((self.proc_root / "uptime").read_text().split()[0])
        out = []
        pids = sorted(int(e.name) for e in self.proc_root.iterdir() if e.name.isdigit())
        for pid in pids:                          # iterdir order is the filesystem's, not ours
            if pid == os.getpid():
                continue
            entry = self.proc_root / str(pid)
            try:
                stat = (entry / "stat").read_text()
                argv = (entry / "cmdline").read_bytes().split(b"\0")
                cwd = os.readlink(entry / "cwd")
            except OSError:
                continue                          # gone, or not ours to read
            rest = stat[stat.rindex(")") + 2:].split()
            out.append({"pid": pid, "ppid": int(rest[1]), "cwd": cwd,
                        "argv": [a.decode(errors="replace") for a in argv if a],
                        "start": int(rest[19]), "cpu_s": (int(rest[11]) + int(rest[12])) / hz,
                        "age_s": uptime - int(rest[19]) / hz})
        return sorted(out, key=lambda p: p["pid"])

    @cached_property
    def ppids(self) -> dict[int, int]:
        """pid → ppid of every readable process, built once per scan."""
        return {q["pid"]: q["ppid"] for q in self.procs}

    def worktree_dir(self, proc: dict) -> Path | None:
        """The worktree the process's cwd or argv is under."""
        for path in (proc["cwd"], *proc["argv"]):
            try:
                parts = Path(path).relative_to(self.worktrees).parts
            except ValueError:
                continue
            if len(parts) >= 2:
                return self.worktrees / parts[0] / parts[1]
        return None

    def worktree_of(self, proc: dict) -> tuple[str, str] | None:
        """``(task, "run"|"judge")`` when the process's cwd or argv is under a worktree."""
        wt = self.worktree_dir(proc)
        if wt is None:
            return None
        return (wt.name[6:], "judge") if wt.name.startswith("judge-") else (wt.name, "run")

    def environ(self, pid: int) -> dict[str, str] | None:
        """The process's START-UP environment (a later ``os.environ`` write never shows in
        ``/proc``); None when unreadable."""
        if pid not in self._environ:
            try:
                raw = (self.proc_root / str(pid) / "environ").read_bytes()
            except OSError:
                self._environ[pid] = None
            else:
                self._environ[pid] = dict(kv.decode(errors="replace").split("=", 1)
                                          for kv in raw.split(b"\0") if b"=" in kv)
        return self._environ[pid]

    def suite_scope(self, pid: int) -> str | None:
        """The ``chela-suite-<id>`` systemd scope the process runs in (its cgroup v2 path),
        else None — also when unreadable. A child re-parented to 1 keeps its cgroup."""
        if pid not in self._scopes:
            try:
                text = (self.proc_root / str(pid) / "cgroup").read_text()
            except OSError:
                text = ""
            hits = [m.group(1) for line in text.splitlines()
                    if (m := SUITE_SCOPE_RE.search(line.split(":", 2)[-1]))]
            self._scopes[pid] = hits[0] if hits else None
        return self._scopes[pid]

    def suite_timeout(self, proc: dict) -> float:
        """``judge.suite_timeout_seconds`` from the process's worktree WORKFLOW.md, else the
        judge's default."""
        from chela import judge, workflow

        try:
            return judge.judge_suite_timeout(
                workflow.load_workflow(self.worktree_dir(proc) / "WORKFLOW.md"))
        except Exception:  # noqa: BLE001 — unreadable or unparseable: the default
            return float(judge.SUITE_TIMEOUT_SECONDS)


def _imported_paths(source: str, test_file: str) -> set[str]:
    """Repo-relative files ``source`` may import directly: ``import a.b`` ⇒ ``a/b.py`` or
    ``a/b/__init__.py``; ``from a import b`` adds ``a/b.py``; a relative import resolves from
    ``test_file``'s directory. The caller keeps those the tree actually has."""
    out: set[str] = set()
    here = Path(test_file).parent.parts

    def add(*parts: str) -> None:
        parts = tuple(p for p in parts if p)
        for i in range(1, len(parts) + 1):        # `import a.b` runs a/__init__.py too
            stem = "/".join(parts[:i])
            out.update({f"{stem}/__init__.py"} | ({f"{stem}.py"} if i == len(parts) else set()))

    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            for alias in node.names:
                add(*alias.name.split("."))
        elif isinstance(node, ast.ImportFrom):
            if node.level > len(here) + 1:
                continue                          # above the repo root
            base = here[:len(here) - node.level + 1] if node.level else ()
            mod = [*base, *(node.module or "").split(".")]
            add(*mod)
            for alias in node.names:
                add(*mod, alias.name)
    return out


def _finding(rule: str, subject: str, summary: str, *, sig: str | None = None,
             **evidence) -> dict:
    """``sig`` (CMX-298): a finding about past facts — flakes that happened — carries one;
    while it is unchanged the finding is never re-notified, however old the notice."""
    out = {"rule": rule, "subject": subject, "summary": summary, "evidence": evidence}
    return dict(out, sig=sig) if sig is not None else out


def _is_test_runner(argv: list[str]) -> bool:
    """pytest / vitest themselves — a bare ``node`` only when it runs a test (a dev server
    idles at 0% by design)."""
    if not argv:
        return False
    if Path(argv[0]).name == "node":
        return any(a == "--test" or "vitest" in a for a in argv[1:])
    return (Path(argv[0]).name in TEST_RUNNERS
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


def _is_daemon(argv: list[str]) -> bool:
    return bool(argv) and any(m in argv[0] for m in DAEMON_MARKERS)


def _descends(src: Sources, pid: int, root: int) -> bool:
    """True iff ``pid`` is a (grand…)child of ``root``."""
    ppid = src.ppids
    seen = set()
    while pid not in seen and pid in ppid:
        seen.add(pid)
        pid = ppid[pid]
        if pid == root:
            return True
    return False


def _runs_tests(argv: list[str]) -> bool:
    """A test runner, or an interpreter running one (``python …/bin/pytest -q``)."""
    return _is_test_runner(argv) or (len(argv) > 1 and Path(argv[1]).name in TEST_RUNNERS)


def _suite_live(src: Sources, p: dict, wt: tuple[str, str]) -> bool:
    """CMX-353: True while ANY suite of the daemon's run is live — decided by run, never by
    ``PM2_HOME``: an xdist worker sets it after start-up, so its ``/proc`` environ never shows
    it (the 10-26 false finding). A suite is live while a process that is not itself
    orphaned shares the daemon's ``chela-suite-*`` scope, or runs the run's worktree inside
    such a scope, or is a test run of that worktree."""
    scope = src.suite_scope(p["pid"])
    for q in src.procs:
        if (q["pid"] == p["pid"] or q["ppid"] == 1 or _is_daemon(q["argv"])
                or _descends(src, q["pid"], p["pid"])):
            continue                              # orphans, daemons and its own children
        theirs = src.suite_scope(q["pid"])
        if scope is not None and theirs == scope:
            return True
        if src.worktree_of(q) == wt and (theirs is not None or _runs_tests(q["argv"])):
            return True
    return False


def _daemon_leak(src: Sources, p: dict, wt: tuple[str, str], status: str | None) -> str | None:
    """CMX-273: why a self-daemonizing process (re-parented to 1 by design) is leaked, or None
    while it is not: any suite of its run is live (:func:`_suite_live`), or it is a
    non-orphaned daemon of a run that is not over."""
    if _suite_live(src, p, wt):
        return None
    if status in OVER_STATUSES:
        return f"its run is {status}"
    if p["ppid"] != 1:
        return None
    env = src.environ(p["pid"])
    home = (env or {}).get("PM2_HOME")
    if not home:
        return ("it is orphaned (ppid 1) and its "
                + ("environ is unreadable" if env is None else "environ has no PM2_HOME"))
    if not os.path.isdir(home):
        return f"its PM2_HOME {home} is gone"
    return f"no suite of its run is live (PM2_HOME {home})"


def _tmux_socket(argv: list[str]) -> str | None:
    """The ``-L`` socket name of a tmux process (``-L name`` or ``-Lname``), else None. Only
    a global flag counts: the scan stops at the subcommand, whose own args are not tmux's."""
    if not argv or Path(argv[0]).name != "tmux":
        return None
    args = iter(argv[1:])
    for a in args:
        if not a.startswith("-"):
            return None                           # the subcommand
        if a == "-L":
            return next(args, "") or None
        if a.startswith("-L"):
            return a[2:]
        if a in TMUX_ARG_FLAGS:
            next(args, None)                      # its value is not a flag
    return None


def _helper_leak(src: Sources, p: dict, wt: tuple[str, str]) -> str | None:
    """CMX-279: why an orphaned (ppid 1) helper of a live test is leaked anyway, or None
    while its test still owns it — a tmux server always re-parents to 1, and so does a
    test's detached script. The owner is the test pid a ``tmux -L chelatest-<pid>-…`` socket
    names; for any other helper whose CWD is in the worktree, a live test run of that same
    run. Past the suite timeout no owner excuses it. A test run itself is never a helper:
    its orphaned copy is CMX-274's runaway."""
    sock = _tmux_socket(p["argv"])
    named = TEST_TMUX_SOCKET_RE.match(sock or "")
    cwd_wt = src.worktree_dir(dict(p, argv=[]))
    if _is_test_runner(p["argv"]) or (not named and cwd_wt != src.worktree_dir(p)):
        return "it is orphaned (ppid 1)"
    limit = src.suite_timeout(p)
    if p["age_s"] > limit:
        return f"it is orphaned (ppid 1) and older than the suite timeout ({limit:.0f}s)"
    for q in src.procs:
        if q["start"] > p["start"] or _descends(src, q["pid"], p["pid"]):
            continue                              # a reused pid starts later; its own child
        if named and q["pid"] == int(named.group(1)):
            return None
        if (not named and _is_test_runner(q["argv"]) and q["ppid"] != 1
                and src.worktree_of(q) == wt):
            return None                           # an orphaned test run owns nothing
    if named:
        return f"it is orphaned (ppid 1) and the test that owns tmux socket {sock} is gone"
    return "it is orphaned (ppid 1) and no live test run shares its worktree"


def rule_leaked_proc(src: Sources, state: dict) -> list[dict]:
    """CMX-253: a process under a run/judge worktree whose run is over, or that was orphaned.
    A ``--detached-child`` judge is setsid'd ON PURPOSE, so ppid 1 alone is not a leak there;
    nor for a live test's own detached helper — its tmux server or a script it started
    (:func:`_helper_leak`). A self-daemonizing process (:func:`_daemon_leak`) is never one
    while a suite of its run is live, and is reported only on its 2nd scan in a row
    (CMX-353; ``state["daemons"]`` holds the previous scan's suspects).
    CMX-274: also a test run under a RUNNING agent older than its suite timeout — a runaway
    the hand-in reaper has not reached yet (2 xdist workers pinned for 1h38m)."""
    out, seen = [], set(state.get("daemons") or [])
    for p in src.procs:
        wt = src.worktree_of(p)
        if not wt:
            continue
        status = src.runs.get(wt[0])
        if _is_daemon(p["argv"]):
            why = _daemon_leak(src, p, wt, status)
            key = f"{p['pid']}:{p['start']}"
            if why is not None:
                src.daemon_suspects.add(key)
            if why is not None and key in seen:  # CMX-353: on a 2nd scan in a row only
                out.append(_finding(
                    "leaked_proc", f"pid:{p['pid']}",
                    f"pid {p['pid']} under {wt[1]} {wt[0]} is alive but {why} on "
                    f"{DAEMON_PERSIST_SCANS} scans in a row — {DAEMON_DO_NOT_KILL}",
                    pid=p["pid"], ppid=p["ppid"], task=wt[0], run_status=status,
                    argv=p["argv"][:6], cwd=p["cwd"]))
            continue
        orphan = p["ppid"] == 1 and DETACHED_FLAG not in p["argv"]
        why = "it is orphaned (ppid 1)"
        if orphan:
            why = _helper_leak(src, p, wt)
            orphan = why is not None
        runaway = (wt[1] == "run" and status == "running" and _is_test_runner(p["argv"])
                   and p["age_s"] > src.suite_timeout(p))
        if status in OVER_STATUSES or orphan or runaway:
            why = (f"its run is {status}" if status in OVER_STATUSES else why if orphan
                   else f"a test run {p['age_s'] / 60:.0f}m old, past the suite timeout")
            out.append(_finding(
                "leaked_proc", f"pid:{p['pid']}",
                f"pid {p['pid']} under {wt[1]} {wt[0]} is alive but {why}",
                pid=p["pid"], ppid=p["ppid"], task=wt[0], run_status=status,
                argv=p["argv"][:6], cwd=p["cwd"]))
    return out


def rule_ci_red_unsettled(src: Sources, state: dict) -> list[dict]:
    """CMX-256: a ``run_ci_red`` naming no job (``job ?``) — the shape an IN_PROGRESS head
    read as red took. :func:`chela.inbox.ci_red` now refuses that; this catches a regression."""
    out = []
    for e in src.events:
        if e.get("type") != "run_ci_red":
            continue
        pl = e.get("payload") or {}
        if [f for f in (str(f).strip() for f in pl.get("failing") or []) if f not in ("", "?")]:
            continue
        sha = str(pl.get("ci_sha") or pl.get("pr_head_sha") or "")[:7]
        out.append(_finding("ci_red_unsettled", f"{pl.get('task_id')}@{sha}",
                            f"run_ci_red for {pl.get('task_id')} on {sha} named no failing job",
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
    out = [_finding("repeat_flake", tid, f"{tid} failed judge baselines of {len(tasks)} "
                    f"tasks in 24h: {', '.join(sorted(tasks))}", test=tid, tasks=tasks)
           for tid, tasks in sorted(seen.items()) if len(tasks) >= 2]
    # CMX-282: the same test red-then-green-on-re-run on ≥2 PRs' heads within 48 h ⇒ file
    # it and register it in tests/flaky.toml the same day. CMX-298: a flake on a head from
    # before the test's latest fix is history, not evidence.
    ci: dict[str, dict[str, float]] = {}
    for task, flake in src.ci_flakes:
        try:                                      # the dispatcher's `_now()` isoformat
            at = datetime.fromisoformat(str(flake.get("at"))).timestamp()
        except ValueError:
            continue
        if at >= src.now - QUARANTINE_WINDOW_S and not src.predates_fix(flake):
            ci.setdefault(str(flake["test"]), {})[str(task)] = at
    return out + [_finding("repeat_flake", f"ci:{tid}", f"quarantine candidate: {tid} — CI "
                           f"flake on {len(tasks)} PRs in 48h: {', '.join(sorted(tasks))}",
                           sig=",".join(sorted(tasks)), test=tid, tasks=tasks)
                  for tid, tasks in sorted(ci.items()) if len(tasks) >= 2]


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
    return {"ts": src.now, "partial": len(finished) < len(rules),
            "findings": [f for f in list(findings) if f["rule"] in finished],
            "rules_done": finished, "errors": dict(errors)}


def fleet_paused(chela_dir: Path) -> bool:
    """A fleet pause file that exists — even one we cannot parse — means paused."""
    return os.path.lexists(chela_dir / FLEET_PAUSE_FILE)


def record(chela_dir: Path, result: dict, state: dict, src: Sources) -> list[dict]:
    """Append the findings to ``ops/findings.jsonl``, update the dedupe state, and return the
    ones to notify: never notified, or last notified ≥ :data:`RENOTIFY_S` ago — unless it
    carries a ``sig`` equal to the one last notified (CMX-298: the same past flakes, re-read
    by every later scan, are not news). A finding that is gone from a rule that completed
    WITHOUT raising is forgotten, so its recurrence is new again; a rule that raised proves
    nothing about what it used to find."""
    notified, now = dict(state.get("notified") or {}), result["ts"]
    sigs = dict(state.get("sigs") or {})
    keys = {f"{f['rule']}:{f['subject']}" for f in result["findings"]}
    clean = set(result["rules_done"]) - set(result["errors"])
    for key in list(notified):
        if key not in keys and key.split(":", 1)[0] in clean:
            del notified[key]
            sigs.pop(key, None)
    fresh = []
    for f in result["findings"]:
        key = f"{f['rule']}:{f['subject']}"
        sig = f.get("sig")
        if sig is not None and key in notified:
            due = sigs.get(key) != sig
        else:
            due = now - notified.get(key, float("-inf")) >= RENOTIFY_S
        if due:
            notified[key] = now
            if sig is None:
                sigs.pop(key, None)
            else:
                sigs[key] = sig
            fresh.append(f)
    out = ops_dir(chela_dir)
    out.mkdir(parents=True, exist_ok=True)
    with open(out / "findings.jsonl", "a", encoding="utf-8") as fh:
        for f in fresh:
            fh.write(json.dumps(dict(f, ts=now)) + "\n")
    cpu = ({f"{p['pid']}:{p['start']}": [now, p["cpu_s"]] for p in src.procs
            if _is_test_runner(p["argv"]) and src.worktree_of(p)}
           if "hung_test" in clean else state.get("cpu") or {})
    daemons = (sorted(src.daemon_suspects) if "leaked_proc" in clean
               else state.get("daemons") or [])
    tmp = out / "state.json.tmp"
    tmp.write_text(json.dumps({"notified": notified, "sigs": sigs, "cpu": cpu,
                               "daemons": daemons}))
    os.replace(tmp, out / "state.json")
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
    src = Sources(chela_dir, proc_root, events_path=event_log.core_path())
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
