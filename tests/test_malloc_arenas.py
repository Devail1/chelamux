"""CMX-14: long-running services cap glibc's malloc arenas in-process, not only via run-chela.sh.

A dashboard started straight from PM2 (``python -m chela.main dashboard``) never saw the
launcher's ``MALLOC_ARENA_MAX=2`` and plateaued at 1.06 GB, 906 MB of it in 34 thread-arena
heaps. These tests pin the three things the fix needs: the cap is chosen right, it is
REALLY applied to glibc (the arena count is measured, not mocked), and ``chela <service>``
applies it before the service starts.
"""

from __future__ import annotations

import ctypes
import ctypes.util
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from chela import main as chela_main
from chela import malloc_arenas

REPO = Path(__file__).resolve().parents[1]


# --- which cap -------------------------------------------------------------

def test_default_cap_when_unset():
    assert malloc_arenas.wanted({}) == malloc_arenas.DEFAULT_ARENA_MAX == 2
    assert malloc_arenas.wanted({"MALLOC_ARENA_MAX": ""}) == 2


def test_env_value_wins():
    # chela.env is sourced into os.environ only after glibc read it, so a value set
    # there has to be re-applied in-process.
    assert malloc_arenas.wanted({"MALLOC_ARENA_MAX": "4"}) == 4


@pytest.mark.parametrize("raw", ["0", "-3", "lots"])
def test_zero_or_garbage_opts_out(raw):
    assert malloc_arenas.wanted({"MALLOC_ARENA_MAX": raw}) is None


# --- applying it -------------------------------------------------------------

class _FakeLibc:
    def __init__(self, ret=1):
        self.calls = []
        self.ret = ret

    def mallopt(self, param, value):
        self.calls.append((param, value))
        return self.ret


def test_cap_calls_mallopt_with_arena_max():
    libc = _FakeLibc()
    assert malloc_arenas.cap({}, libc=libc) == 2
    assert libc.calls == [(malloc_arenas.M_ARENA_MAX, 2)]


def test_cap_reports_nothing_when_mallopt_refuses_or_is_missing():
    assert malloc_arenas.cap({}, libc=_FakeLibc(ret=0)) is None    # musl's stub
    assert malloc_arenas.cap({}, libc=object()) is None            # no mallopt (macOS)


def test_opt_out_never_touches_libc():
    libc = _FakeLibc()
    assert malloc_arenas.cap({"MALLOC_ARENA_MAX": "0"}, libc=libc) is None
    assert libc.calls == []


# --- against real glibc ------------------------------------------------------

_COUNT_ARENAS = textwrap.dedent("""
    import ctypes, sys, tempfile, threading
    from chela import malloc_arenas
    if sys.argv[1] == "cap":
        malloc_arenas.cap()
    N = 16
    start, done = threading.Barrier(N), threading.Barrier(N)
    def work():
        start.wait()                      # every thread alive at once: none can reuse
        keep = [bytearray(20000) for _ in range(50)]   # an exited thread's arena
        done.wait()
    ts = [threading.Thread(target=work) for _ in range(N)]
    for t in ts: t.start()
    for t in ts: t.join()
    libc = ctypes.CDLL(None)
    libc.fopen.restype = ctypes.c_void_p
    libc.fopen.argtypes = [ctypes.c_char_p, ctypes.c_char_p]
    libc.fclose.argtypes = [ctypes.c_void_p]
    libc.malloc_info.argtypes = [ctypes.c_int, ctypes.c_void_p]
    with tempfile.NamedTemporaryFile() as f:
        fp = libc.fopen(f.name.encode(), b"w")
        libc.malloc_info(0, fp)
        libc.fclose(fp)
        print(open(f.name).read().count("<heap nr="))
""")


def _arenas(mode: str) -> int:
    env = {k: v for k, v in os.environ.items() if k != "MALLOC_ARENA_MAX"}
    env.update(PYTHONPATH=str(REPO), PYTHONDONTWRITEBYTECODE="1")
    out = subprocess.run([sys.executable, "-c", _COUNT_ARENAS, mode], env=env,
                         capture_output=True, text=True, timeout=60, check=True)
    return int(out.stdout.strip())


@pytest.mark.skipif(
    not sys.platform.startswith("linux") or ctypes.util.find_library("c") is None
    or not hasattr(ctypes.CDLL(None), "malloc_info") or (os.cpu_count() or 1) < 2,
    reason="needs glibc (malloc_info) on a multi-core box",
)
def test_cap_really_bounds_glibc_arenas():
    uncapped = _arenas("nocap")
    assert uncapped > 2, "16 live threads should grow past 2 arenas uncapped"  # the test can tell
    assert _arenas("cap") <= malloc_arenas.DEFAULT_ARENA_MAX


# --- `chela <service>` applies it first ----------------------------------------

def _run_main(monkeypatch, argv: list[str], handler: str) -> list[str]:
    order: list[str] = []
    monkeypatch.setattr(chela_main.malloc_arenas, "cap", lambda: order.append("cap"))
    monkeypatch.setattr(chela_main, handler, lambda args: order.append(handler))
    monkeypatch.setattr(sys, "argv", ["chela", *argv])
    chela_main.main()
    return order


@pytest.mark.parametrize("cmd, handler", [
    ("dashboard", "cmd_dashboard"),
    ("run", "cmd_run"),
    ("telegram", "cmd_telegram"),
    ("collab", "cmd_collab"),
])
def test_services_cap_before_starting(monkeypatch, cmd, handler):
    assert _run_main(monkeypatch, [cmd], handler) == ["cap", handler]


def test_one_shot_commands_do_not_cap(monkeypatch):
    assert _run_main(monkeypatch, ["status"], "cmd_status") == ["cmd_status"]
