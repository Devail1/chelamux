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


def _kill_own_group(*_) -> None:
    os.killpg(0, signal.SIGKILL)


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
