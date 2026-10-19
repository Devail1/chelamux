"""Run a command so that no process it starts can outlive the run that launched it (CMX-151).

``subprocess.run`` kills only its DIRECT child — on a timeout, a Ctrl-C, and never at all
when the caller is itself SIGKILLed. ``node --test`` forks a process per test file, so
every one of those ways leaves orphans: on 2026-10-16 eight of them (ppid=1, cwd a
removed worktree) held ~4 GB of a 12 GB box for 3.5 hours.

:func:`run_contained` closes all three routes:

* the command runs under a tiny GUARDIAN (``python -m chela.procguard <argv>``) that leads
  its own session, so the whole tree shares one process group;
* the guardian carries ``PR_SET_PDEATHSIG = SIGTERM``: when the launcher dies — even by
  SIGKILL, which runs no ``finally`` — the kernel signals the guardian, which SIGKILLs its
  group;
* with ``watch=<pid>`` the guardian also kills its group when THAT process dies — the
  pytest-xdist master, whose SIGKILL a worker only notices once its current test ends;
* on every exit the launcher sees (success, failure, timeout, exception) it SIGKILLs the
  group itself.

⚠️ ``PR_SET_PDEATHSIG`` fires when the parent THREAD exits, so call this from the thread
that lives as long as the run (pytest's main thread does). Linux-only; elsewhere the
death signal is skipped and only the launcher-side group kill remains.
"""
from __future__ import annotations

import ctypes
import os
import signal
import subprocess
import sys
import threading
import time

PR_SET_PDEATHSIG = 1


def _set_pdeathsig(sig: int, parent: int) -> None:
    """``preexec_fn``: deliver ``sig`` to this child when ``parent`` dies. If the parent is
    already gone (it died between fork and here), deliver it now — the kernel won't."""
    prctl = getattr(ctypes.CDLL(None, use_errno=True), "prctl", None)
    if prctl is None:
        return
    prctl(PR_SET_PDEATHSIG, sig)
    if os.getppid() != parent:
        os.kill(os.getpid(), sig)


def _kill_group(pgid: int) -> None:
    """``pgid`` is the guardian's pid. It names a group only because the guardian leads a
    new session; were it not, no such group exists and this is a no-op — never a wider kill."""
    try:
        os.killpg(pgid, signal.SIGKILL)
    except ProcessLookupError:
        pass                        # every member already gone


def run_contained(argv: list[str], *, timeout: float, cwd: str | None = None,
                  env: dict[str, str] | None = None,
                  watch: int | None = None) -> subprocess.CompletedProcess:
    """``subprocess.run(argv, capture_output=True, timeout=…)`` whose WHOLE process tree
    dies with the run — see the module docstring."""
    parent = os.getpid()
    proc = subprocess.Popen(
        [sys.executable, "-m", "chela.procguard", str(watch or 0), *argv],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, cwd=cwd, env=env,
        start_new_session=True,
        preexec_fn=lambda: _set_pdeathsig(signal.SIGTERM, parent),
    )
    try:
        out, err = proc.communicate(timeout=timeout)
    except BaseException:
        _kill_group(proc.pid)
        proc.wait()
        raise
    _kill_group(proc.pid)
    return subprocess.CompletedProcess(argv, proc.returncode, out, err)


# CMX-253: how long a timed-out suite's group gets between SIGTERM and SIGKILL.
TERM_GRACE_SECONDS = 5.0


def _signal_group(pgid: int, sig: int) -> None:
    try:
        os.killpg(pgid, sig)
    except ProcessLookupError:
        pass                        # every member already gone


def _group_alive(pgid: int) -> bool:
    try:
        os.killpg(pgid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True                 # a member exists, it just is not ours to signal
    return True


def _drain(proc: subprocess.Popen, grace: float) -> tuple[str, str]:
    """Whatever the dead group still had buffered. A process that escaped the group (its
    own ``setsid``) may hold the pipes open forever, so the read is bounded too."""
    try:
        return proc.communicate(timeout=grace)
    except subprocess.TimeoutExpired:
        proc.kill()
        for pipe in (proc.stdout, proc.stderr):
            if pipe is not None:
                pipe.close()
        proc.wait()
        return "", ""


def _ppids() -> dict[int, int]:
    out = {}
    for d in os.listdir("/proc"):
        try:
            with open(f"/proc/{d}/stat") as f:
                out[int(d)] = int(f.read().rsplit(")", 1)[1].split()[1])
        except (OSError, ValueError, IndexError):
            continue                # not a pid, or it exited mid-scan
    return out


def process_tree(roots: list[int]) -> list[int]:
    """``roots`` and every descendant, parents first (CMX-214: a judge freezes as a tree).
    By parentage, not by group: a suite leads its own session, so a group misses it."""
    children: dict[int, list[int]] = {}
    for pid, ppid in _ppids().items():
        children.setdefault(ppid, []).append(pid)
    seen: list[int] = []
    todo = list(roots)
    while todo:
        pid = todo.pop(0)
        if pid not in seen:
            seen.append(pid)
            todo.extend(sorted(children.get(pid, ())))
    return seen


def run_shell_group(cmd: str, *, timeout: float, cwd: str | None = None,
                    env: dict[str, str] | None = None,
                    grace: float = TERM_GRACE_SECONDS,
                    frozen=None) -> subprocess.CompletedProcess:
    """``subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=…)`` for a
    test suite, whose WHOLE process group dies with the run (CMX-253).

    ``subprocess.run`` kills only the shell on a timeout. ``uv run pytest -n 4`` is a shell,
    uv, pytest and four xdist workers; on 2026-10-18 two such trees outlived a sweep's
    1500s timeout and sat idle for 75+ minutes. Here the command leads its own session, so
    its tree shares one process group: on a timeout the group gets SIGTERM, then SIGKILL
    after ``grace`` seconds, and the shell is reaped. ``TimeoutExpired`` is re-raised with
    the output read so far. On every other exit the group is SIGKILLed too, so nothing the
    suite left in the background survives it either.

    No guardian (:func:`run_contained`): its ``python -m chela.procguard`` would import from
    the cwd, and the cwd here is a worktree whose ``chela/`` a mutation may have corrupted.
    ``frozen(since)`` (CMX-214): seconds SIGSTOPped since ``since``; the deadline moves by it.
    """
    proc = subprocess.Popen(cmd, shell=True, cwd=cwd, env=env, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, text=True, errors="replace",
                            start_new_session=True)
    start = time.time()

    def remaining() -> float:
        return start + timeout + (frozen(start) if frozen else 0.0) - time.time()

    try:
        while True:
            try:
                out, err = proc.communicate(timeout=max(remaining(), 0.0))
                break
            except subprocess.TimeoutExpired:
                if remaining() <= 0:
                    raise
    except subprocess.TimeoutExpired:
        _signal_group(proc.pid, signal.SIGTERM)
        deadline = time.monotonic() + grace
        try:
            proc.communicate(timeout=grace)
        except subprocess.TimeoutExpired:
            pass                    # SIGTERM was not enough
        # The shell exiting is not the group exiting: the rest share the same deadline.
        while _group_alive(proc.pid) and time.monotonic() < deadline:
            time.sleep(0.05)
        _signal_group(proc.pid, signal.SIGKILL)
        # A retried communicate() returns ALL the output since the start, none of it lost.
        out, err = _drain(proc, grace)
        raise subprocess.TimeoutExpired(cmd, timeout, output=out, stderr=err) from None
    except BaseException:
        _signal_group(proc.pid, signal.SIGKILL)
        _drain(proc, grace)
        raise
    _signal_group(proc.pid, signal.SIGKILL)
    return subprocess.CompletedProcess(cmd, proc.returncode, out, err)


def _kill_own_group(*_) -> None:
    """SIGKILL the guardian's group, then the guardian. The group is named by the
    guardian's OWN pid, never ``killpg(0)``: a guardian that somehow shares its launcher's
    group (no new session) then finds no such group and kills only itself — its child
    follows by its death signal — instead of taking the launcher's whole group down."""
    me = os.getpid()
    try:
        os.killpg(me, signal.SIGKILL)
    except ProcessLookupError:
        pass                        # not a group leader: there is no group of ours to kill
    os.kill(me, signal.SIGKILL)


def _identity(pid: int) -> str | None:
    """``pid``'s start time — what tells it apart from a later process reusing the pid —
    or None once it is gone (a zombie counts as gone)."""
    try:
        with open(f"/proc/{pid}/stat") as f:
            fields = f.read().rsplit(")", 1)[1].split()
    except OSError:
        return None
    return None if fields[0] in ("Z", "X") else fields[19]


def _watch(pid: int, interval: float = 0.25) -> None:
    """Poll until ``pid`` exits, then kill this group (``os.pidfd_open`` is missing from
    some Python builds). Already gone at the first look counts as dead."""
    born = _identity(pid)
    while born is not None and _identity(pid) == born:
        time.sleep(interval)
    _kill_own_group()


def _guard(watch: int, argv: list[str]) -> int:
    """The guardian: run ``argv`` in this process group; on SIGTERM (the launcher died) or
    the death of ``watch`` (if nonzero), SIGKILL the whole group, this process included."""
    signal.signal(signal.SIGTERM, _kill_own_group)
    if watch:
        threading.Thread(target=_watch, args=(watch,), daemon=True).start()
    me = os.getpid()
    child = subprocess.Popen(argv, preexec_fn=lambda: _set_pdeathsig(signal.SIGKILL, me))
    return child.wait()


if __name__ == "__main__":
    sys.exit(_guard(int(sys.argv[1]), sys.argv[2:]))
