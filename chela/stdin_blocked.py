"""CMX-443: find a process in an agent's tree that is blocked reading a stdin nobody writes.

An agent wrote its commit message as ``$(cat <<'EOF' … EOF)``; a quote in the message broke
the heredoc, so ``cat`` sat reading the background shell's stdin — a pipe that never closes —
and the agent waited on that shell for over an hour. The 🧊 STUCK notice fired but could not
say why. This module reads ``/proc`` so the notice can name the process to kill.

What counts as blocked on stdin, all read from ``/proc/<pid>`` without ptrace (``syscall``
is ptrace-gated under Yama, ``wchan`` is not):

* a LEAF of the process tree — no live process names it as parent (a shell waiting on its
  child is not the culprit, the child is);
* sleeping (state ``S``) in a read wait: ``wchan`` is a pipe read (``pipe_read`` /
  ``anon_pipe_read``) while fd 0 is a pipe, or a tty read (``n_tty_read`` /
  ``wait_woken``) while fd 0 is a terminal — a process sleeping on anything else, or
  writing its output, is busy, not blocked;
* its cwd is inside the agent's worktree (how the agent's processes are found, as for the
  CMX-110 self-check probe);
* it has existed at least ``config.stdin_blocked_alert_s()`` — the age floor; nothing
  in ``/proc`` says how long the current read has waited;
* and, when the notice is written, it started no earlier than the run's last activity
  (:func:`stalled_since`) — so a long-lived stdin reader such as a stdio MCP server, which
  the agent worked past, is never named as the thing to kill.

Linux-only by nature: anywhere else, or for a process that vanished or is not ours to read,
it finds nothing, and the notice reads exactly as it did before.
"""
from __future__ import annotations

import os
import time
from pathlib import Path

_PROC = Path("/proc")

# The kernel wait channel of a read sleeping on a pipe (``anon_pipe_read`` since 6.x) or a tty.
PIPE_READ_WCHANS = frozenset({"pipe_read", "anon_pipe_read"})
TTY_READ_WCHANS = frozenset({"n_tty_read", "wait_woken"})
SHELLS = frozenset({"bash", "sh", "dash", "zsh", "ksh", "fish"})


def _stdin_kind(link: str) -> str | None:
    """``"pipe"`` / ``"tty"`` for what fd 0 points at, or None (a file, /dev/null, socket)."""
    if link.startswith("pipe:["):
        return "pipe"
    if link.startswith("/dev/pts/") or link.startswith("/dev/tty"):
        return "tty"
    return None


def _reads_stdin(wchan: str, kind: str | None) -> bool:
    """Is a process sleeping in ``wchan`` with fd 0 of ``kind`` waiting on that stdin?"""
    if kind == "pipe":
        return wchan in PIPE_READ_WCHANS
    if kind == "tty":
        return wchan in TTY_READ_WCHANS
    return False


def scan() -> list[dict]:
    """``[{"pid", "comm", "ppid", "parent", "cwd", "started"}]`` for every process blocked on
    stdin (see the module doc, minus the cwd and age tests — those are per run). ONE pass over
    ``/proc`` per tick; ``parent`` is the parent's command name, or None if unreadable."""
    try:
        boot = next((float(line.split()[1]) for line in
                     (_PROC / "stat").read_text().splitlines() if line.startswith("btime ")),
                    None)
        hz = os.sysconf("SC_CLK_TCK")
        dirs = [d for d in _PROC.iterdir() if d.name.isdigit()]
    except (OSError, ValueError, IndexError):
        return []
    table: dict[int, tuple[str, str, int, float]] = {}
    for d in dirs:
        try:
            raw = (d / "stat").read_text()
            comm = raw[raw.index("(") + 1:raw.rindex(")")]
            fields = raw.rsplit(")", 1)[1].split()
            table[int(d.name)] = (comm, fields[0], int(fields[1]), float(fields[19]))
        except (OSError, ValueError, IndexError):
            continue
    # a zombie child (never reaped) does not stop its parent being the live leaf
    parents = {ppid for _, state, ppid, _ in table.values() if state != "Z"}
    found: list[dict] = []
    for pid, (comm, state, ppid, ticks) in table.items():
        if pid in parents or state != "S":
            continue
        d = _PROC / str(pid)
        try:
            if not _reads_stdin((d / "wchan").read_text().strip(),
                                _stdin_kind(os.readlink(d / "fd" / "0"))):
                continue
            cwd = Path(os.readlink(d / "cwd"))
        except OSError:
            continue
        parent = table.get(ppid)
        found.append({"pid": pid, "comm": comm, "ppid": ppid,
                      "parent": parent[0] if parent else None, "cwd": cwd,
                      "started": boot + ticks / hz if boot is not None else None})
    return found


def for_worktree(worktree: str, procs: list[dict], now: float,
                 min_age: float) -> list[dict]:
    """The blocked processes in ``procs`` running inside ``worktree`` that have existed at
    least ``min_age`` seconds — oldest first. A process with no readable start is left out:
    an age we could not read is not an age past the floor."""
    try:
        root = Path(worktree).resolve()
    except OSError:
        return []
    mine = [p for p in procs
            if (p["cwd"] == root or root in p["cwd"].parents)
            and p["started"] is not None and now - p["started"] >= min_age]
    return sorted(mine, key=lambda p: (p["started"], p["pid"]))


# How long before the agent's last activity a culprit may have started: the agent writes its
# transcript as it launches the shell, a moment before the shell forks the ``cat``.
STALL_SLACK_S = 60.0


def stalled_since(procs: list[dict], activity: float) -> list[dict]:
    """The processes in ``procs`` that started no earlier than ``activity - STALL_SLACK_S``:
    nothing has happened in the run since they began, so their read IS the stall. One that
    predates later activity (a stdio MCP server, a ``tail -f``) was not stopping the agent
    then, and is not evidence now."""
    return [p for p in procs
            if p.get("started") is not None and p["started"] >= activity - STALL_SLACK_S]


def describe(proc: dict) -> str:
    """The notice clause: "blocked on stdin: cat PID N, under background shell bash PID M,
    started HH:MM. Killing it frees the agent without losing work." No backticks or parens:
    the inbox strips shell metacharacters from every summary (CMX-79), so the clause is
    written to read the same before and after."""
    parent = proc.get("parent")
    if parent in SHELLS:
        under = f"under background shell {parent} PID {proc['ppid']}"
    elif parent:
        under = f"under {parent} PID {proc['ppid']}"
    else:
        under = f"under PID {proc['ppid']}"
    started = time.strftime("%H:%M", time.localtime(proc["started"]))
    return (f"blocked on stdin: {proc['comm']} PID {proc['pid']}, {under}, started "
            f"{started}. Killing it frees the agent without losing work.")
