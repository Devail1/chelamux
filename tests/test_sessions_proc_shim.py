"""The process facts must survive a host with NO ``/proc`` — macOS has none at all.

``chela.sessions`` reads every process fact from ``/proc``: the child list, ``comm``, the
command line, the cwd and the start time. On a kernel that has it, that is one file read
per fact and no subprocess — a budget these tests defend, because the hook path runs while
an agent is BLOCKED on it. On a host that does NOT (macOS), every one of those reads fails,
and before the shim the cascade was total: no ``claude_pid``, therefore no ``started`` /
``resumed`` / ``launched_in``, therefore :func:`chela.sessions.resolve_window`'s two
strongest signals collapsed untried and every window sharing a cwd resolved to ``None`` —
no ai-title, no recap, no PR link, and a Telegram relay that silently posted nothing.

These tests fake that host the only way that proves anything: point ``PROC`` at a path that
does not exist, spawn a REAL process, and require the facts to come back anyway. Delete the
POSIX fallback and every test below goes red on Linux, where it would otherwise be invisible
— which is the whole point, since CI is where a macOS-only regression hides.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time

import pytest

from chela import sessions

# A well-formed session id (SESSION_RE-valid) so `--resume` parsing is exercised for real.
SID = "ffd591c6-d903-4505-8749-8058a3abf054"

# The child is spawned as `python -c <sleep> claude --resume <sid>`: its `comm` is the
# interpreter, NOT "claude", so finding it REQUIRES the command-line leg — the tolerant
# match CMX-160 added, which on a /proc-less host had nothing to read. A bare `sleep`
# would pass on the `comm` leg alone and prove far less.
_ARGV_TAIL = ["claude", "--resume", SID]


@pytest.fixture
def no_proc(monkeypatch, tmp_path):
    """A host with no ``/proc`` — the macOS case, forced on whatever runs this.

    BOTH knobs are required, and the second is the point: ``_PROC_HOST`` is what decides
    whether a failed read may fall back at all. Pointing ``PROC`` alone at an empty tree is
    how the existing suite says "this fixture has no such process" ON a /proc host, and
    that must keep degrading rather than reaching for the live process table.
    """
    monkeypatch.setattr(sessions, "PROC", tmp_path / "nonexistent-proc")
    monkeypatch.setattr(sessions, "_PROC_HOST", False)


def _children_unguarded() -> list[int]:
    """``_sh_children`` with the /proc-host gate forced open, for fixture setup only."""
    saved = sessions._PROC_HOST
    sessions._PROC_HOST = False
    try:
        return sessions._sh_children(os.getpid())
    finally:
        sessions._PROC_HOST = saved


@pytest.fixture
def child(tmp_path):
    """A real, live child process of the test process, with a known cwd and argv."""
    cwd = tmp_path / "workdir"
    cwd.mkdir()
    proc = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(30)", *_ARGV_TAIL],
        cwd=str(cwd),
    )
    # Popen returns before the kernel has necessarily published the process to `ps`/`pgrep`.
    # Probed with the gate forced open, since the fixture that opens it may not be active.
    deadline = time.time() + 5
    while time.time() < deadline:
        if proc.pid in _children_unguarded():
            break
        time.sleep(0.05)
    try:
        yield proc, str(cwd)
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:      # pragma: no cover — belt and braces
            proc.kill()


requires_pgrep = pytest.mark.skipif(not shutil.which("pgrep"), reason="pgrep not installed")
requires_lsof = pytest.mark.skipif(not shutil.which("lsof"), reason="lsof not installed")


# --- the facts, with /proc gone ------------------------------------------------------

@requires_pgrep
def test_children_are_found_without_proc(no_proc, child):
    """``pgrep -P`` stands in for ``/proc/<pid>/task/<pid>/children``.

    This one is load-bearing above all the others: :func:`_claude_pid` walks the tree
    through it, so with no children the walk ends before any other fact is even asked for.
    """
    proc, _ = child
    assert proc.pid in sessions._children(os.getpid())


@requires_pgrep
def test_the_claude_process_is_found_without_proc(no_proc, child):
    """The whole cascade's root: a claude whose ``comm`` is NOT "claude" is still found,
    by the command-line leg, with no ``/proc`` to read it from."""
    proc, _ = child
    assert sessions._looks_like_claude(proc.pid) is True
    assert sessions._claude_pid(os.getpid()) == proc.pid


@requires_pgrep
def test_resumed_session_is_read_without_proc(no_proc, child):
    """``--resume <sid>`` — the ONE signal a resume-from-elsewhere cannot invalidate, and
    the tie-breaker between two windows sharing a directory."""
    proc, _ = child
    assert sessions._resumed_session(proc.pid) == SID


@requires_pgrep
def test_start_time_is_read_without_proc(no_proc, child):
    """``ps -o lstart=`` is an absolute local timestamp, so the fallback needs no boot
    time; it only has to be epoch seconds, comparable with the event log's."""
    proc, _ = child
    started = sessions.proc_started(proc.pid)
    assert started is not None
    assert abs(started - time.time()) < 120        # just spawned, not the epoch


@requires_lsof
def test_cwd_is_read_without_proc(no_proc, child):
    """The claude process's OWN cwd — the origin that does not move when the agent cds."""
    proc, cwd = child
    assert sessions._proc_cwd(proc.pid) == os.path.realpath(cwd)


# --- the Linux fast path must not regress --------------------------------------------

def test_proc_is_used_alone_when_it_exists(tmp_path, monkeypatch):
    """With ``/proc`` readable, NOTHING shells out. The fallback is reached only when the
    read fails, so a Linux host keeps the "one tmux call and nothing else" budget that
    CMX-41 bought — a shim that always shelled out would be seconds per hook."""
    pid = 4242
    proc = tmp_path / "proc"
    d = proc / str(pid)
    (d / "task" / str(pid)).mkdir(parents=True)
    (d / "comm").write_text("claude\n")
    (d / "cmdline").write_bytes(b"\0".join([b"claude", b"--resume", SID.encode()]) + b"\0")
    (d / "task" / str(pid) / "children").write_text("")
    (d / "cwd").symlink_to(tmp_path)
    (d / "stat").write_text(f"{pid} (claude) S 1 " + " ".join(["0"] * 17) + " 500 0 0\n")
    (proc / "stat").write_text("btime 1000\n")
    monkeypatch.setattr(sessions, "PROC", proc)

    def explode(argv):                       # any fallback query is a budget regression
        raise AssertionError(f"shelled out on the /proc fast path: {argv}")

    monkeypatch.setattr(sessions, "_sh", explode)

    assert sessions._comm(pid) == "claude"
    assert sessions._looks_like_claude(pid) is True
    assert sessions._children(pid) == []
    assert sessions._resumed_session(pid) == SID
    assert sessions._proc_cwd(pid) == str(tmp_path)
    assert sessions.proc_started(pid) == 1000 + 500 / sessions._CLK_TCK


def test_a_stubbed_subprocess_cannot_invent_a_pid(no_proc, monkeypatch):
    """``_sh_children`` takes only all-digit lines, so output that did not come from
    ``pgrep`` yields nothing rather than a pid parsed out of some other command's stdout.

    Not hypothetical: the suite has tests that stub ``subprocess.run`` wholesale to fake
    ONE tmux call, and a loose parse would mine a window id or a pane pid out of that and
    report it as a live child.
    """
    monkeypatch.setattr(sessions, "_sh", lambda argv: "@5\tbash\t/home/u\t123\n")
    assert sessions._sh_children(999999) == []


def test_sh_children_beyond_the_cap_keeps_the_most_recent(no_proc, monkeypatch):
    """``_MAX_CHILDREN`` truncates a busy pid's children — `pgrep -P` lists them oldest
    first, so slicing the FRONT keeps stale siblings and drops the one this call exists
    to find: whichever process was spawned last (a nested subprocess-heavy test suite, or
    on a real box, a fleet of long-lived agent panes, both leave many older children
    around a busy pid)."""
    first = 1000
    last = first + sessions._MAX_CHILDREN + 5
    fake = "\n".join(str(p) for p in range(first, last + 1)) + "\n"
    monkeypatch.setattr(sessions, "_sh", lambda argv: fake)

    kids = sessions._sh_children(1)

    assert len(kids) == sessions._MAX_CHILDREN
    assert kids[-1] == last                 # most recently spawned: kept
    assert first not in kids                # oldest sibling: dropped, not the target


def test_proc_children_beyond_the_cap_keeps_the_most_recent(tmp_path, monkeypatch):
    """The /proc path ('.../children' is also oldest-first) must truncate the same way."""
    pid = 4242
    d = tmp_path / "proc" / str(pid) / "task" / str(pid)
    d.mkdir(parents=True)
    first = 1000
    last = first + sessions._MAX_CHILDREN + 5
    (d / "children").write_text(" ".join(str(p) for p in range(first, last + 1)) + " ")
    monkeypatch.setattr(sessions, "PROC", tmp_path / "proc")

    kids = sessions._children(pid)

    assert len(kids) == sessions._MAX_CHILDREN
    assert kids[-1] == last
    assert first not in kids


def test_sh_children_beyond_the_cap_warns_out_loud(no_proc, monkeypatch, caplog):
    """Truncating a busy pid's children is a fact a caller needs, not just a fact this
    module keeps to itself — a downstream ``None`` (e.g. ``_claude_pid``) reads exactly
    like "no such child" whether the answer is genuine or just this cap, so the ONE place
    that knows which one it was must say so out loud (follow-up to CMX-210)."""
    first = 1000
    last = first + sessions._MAX_CHILDREN + 5
    fake = "\n".join(str(p) for p in range(first, last + 1)) + "\n"
    monkeypatch.setattr(sessions, "_sh", lambda argv: fake)

    with caplog.at_level("WARNING", logger="chela.sessions"):
        sessions._sh_children(4242)

    assert len(caplog.records) == 1
    msg = caplog.records[0].getMessage()
    assert "4242" in msg
    assert str(sessions._MAX_CHILDREN) in msg
    assert "6" in msg  # dropped count: (last - first + 1) - _MAX_CHILDREN == 6


def test_proc_children_beyond_the_cap_warns_out_loud(tmp_path, monkeypatch, caplog):
    """Same visibility requirement via the /proc path."""
    pid = 4242
    d = tmp_path / "proc" / str(pid) / "task" / str(pid)
    d.mkdir(parents=True)
    first = 1000
    last = first + sessions._MAX_CHILDREN + 5
    (d / "children").write_text(" ".join(str(p) for p in range(first, last + 1)) + " ")
    monkeypatch.setattr(sessions, "PROC", tmp_path / "proc")

    with caplog.at_level("WARNING", logger="chela.sessions"):
        sessions._children(pid)

    assert len(caplog.records) == 1
    assert str(pid) in caplog.records[0].getMessage()


def test_children_within_the_cap_stays_silent(no_proc, monkeypatch, caplog):
    """The cap not being hit is NOT news — a warning on every call would flood the logs
    the first time it fires for real and bury the signal this test's sibling exists to
    surface."""
    fake = "\n".join(str(p) for p in range(1000, 1000 + sessions._MAX_CHILDREN)) + "\n"
    monkeypatch.setattr(sessions, "_sh", lambda argv: fake)

    with caplog.at_level("WARNING", logger="chela.sessions"):
        kids = sessions._sh_children(1)

    assert len(kids) == sessions._MAX_CHILDREN
    assert caplog.records == []


def test_a_missing_tool_is_just_an_unknown_fact(no_proc, monkeypatch):
    """No ``/proc`` AND no tools (a stripped container) degrades to the documented
    "fact unavailable" — never an exception on the hook path."""
    monkeypatch.setattr(sessions, "_sh", lambda argv: None)
    assert sessions._children(1) == []
    assert sessions._comm(1) == ""
    assert sessions._looks_like_claude(1) is False
    assert sessions._resumed_session(1) is None
    assert sessions.proc_started(1) is None
    assert sessions._proc_cwd(1) is None
