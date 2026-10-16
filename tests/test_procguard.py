"""CMX-151: a run's test processes cannot outlive the run.

Every process here is a dummy spawned by the test, in a tmp dir, under its own session —
never the operator's. A test signals only pids it recorded itself, and never ``killpg(0)``:
the group kills under test are faked.
"""
from __future__ import annotations

import os
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

from chela import procguard

ROOT = Path(__file__).resolve().parent.parent

# Stands in for one `node --test` file: a child process that forks a grandchild and then
# hangs forever — exactly the tree that survived CMX-128.
_HANG = """
import { test } from 'node:test';
import { spawn } from 'node:child_process';
import { writeFileSync } from 'node:fs';
test('hangs', async () => {
  const c = spawn(process.execPath, ['-e', 'setInterval(() => {}, 1000)'], { stdio: 'ignore' });
  writeFileSync(process.env.PIDS, `${process.pid} ${c.pid}`);
  await new Promise(() => {});
});
"""

# Stands in for pytest: launches the suite, one way or the other, and waits. The `worker`
# modes are pytest-xdist: a child worker runs the suite, watching (or not) this master.
_WORKER = ("import os, sys; from chela.procguard import run_contained; "
           "run_contained(sys.argv[2:], timeout=120, watch=int(sys.argv[1]) or None)")
_LAUNCHER = f"""
import os, subprocess, sys
from chela.procguard import run_contained
mode, cmd = sys.argv[1], sys.argv[2:]
if mode == "contained":
    run_contained(cmd, timeout=120)
elif mode == "plain":
    subprocess.run(cmd, capture_output=True, timeout=120)
else:
    watch = str(os.getpid() if mode == "worker" else 0)
    subprocess.run([sys.executable, "-c", {_WORKER!r}, watch, *cmd])
"""


def _alive(pid: int) -> bool:
    try:
        with open(f"/proc/{pid}/stat") as f:
            return f.read().rsplit(")", 1)[1].split()[0] != "Z"
    except OSError:
        return False


def _group_members(pgid: int) -> list[int]:
    out = []
    for e in filter(str.isdigit, os.listdir("/proc")):
        try:
            if os.getpgid(int(e)) == pgid and _alive(int(e)):
                out.append(int(e))
        except OSError:
            pass
    return out


def _wait_for(pred, timeout: float = 10.0) -> bool:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if pred():
            return True
        time.sleep(0.05)
    return pred()


@pytest.mark.parametrize("mode", ["contained", "plain", "worker", "worker-unwatched"])
def test_sigkilled_pytest_leaves_no_node_test_process_behind(tmp_path, mode):
    """SIGKILL the launcher of a real `node --test` → its whole tree dies within seconds.
    `plain` is the negative control: the same tree under `subprocess.run` DOES survive,
    proving this test can see a leak at all. `worker`: SIGKILL the xdist MASTER while its
    worker lives on — `watch` kills the tree; `worker-unwatched` is its negative control."""
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not installed")
    suite, pids = tmp_path / "hang.test.mjs", tmp_path / "pids"
    suite.write_text(_HANG)
    env = {k: v for k, v in os.environ.items() if not k.startswith("NODE_CHANNEL")}
    env.update(PIDS=str(pids), PYTHONPATH=str(ROOT))
    launcher = subprocess.Popen(
        [sys.executable, "-c", _LAUNCHER, mode, node, "--test", str(suite)],
        cwd=str(tmp_path), env=env,
        start_new_session=True,     # the whole dummy tree lives outside pytest's session
    )
    tree: list[int] = []
    try:
        assert _wait_for(lambda: pids.exists() and len(pids.read_text().split()) == 2, 30)
        tree = [int(p) for p in pids.read_text().split()]
        group = os.getpgid(tree[0])
        os.kill(launcher.pid, signal.SIGKILL)
        launcher.wait()
        died = _wait_for(lambda: not any(_alive(p) for p in tree), 5)
        assert died is (mode in ("contained", "worker")), [p for p in tree if _alive(p)]
        if died:        # `node --test` itself too: nothing left in the group
            assert _wait_for(lambda: not _group_members(group), 5), _group_members(group)
    finally:
        launcher.kill()
        for p in tree:
            try:
                os.kill(p, signal.SIGKILL)
            except ProcessLookupError:
                pass


def test_run_contained_returns_the_commands_result_with_cwd_and_env(tmp_path):
    env = dict(os.environ, CMX151="marker")
    cp = procguard.run_contained(
        [sys.executable, "-c",
         "import os, sys; print(os.getcwd(), os.environ['CMX151']); "
         "sys.stderr.write('err'); sys.exit(3)"],
        timeout=30, cwd=str(tmp_path), env=env,
    )
    assert cp.returncode == 3
    assert cp.stdout.decode().split() == [str(tmp_path), "marker"]
    assert cp.stderr == b"err"
    assert cp.args[1] == "-c"           # the caller's argv, not the guardian's


def test_run_contained_kills_a_background_grandchild_when_the_command_exits(tmp_path):
    """The command exits 0 but leaves a child running — gone when run_contained returns."""
    cp = procguard.run_contained(
        ["sh", "-c", "sleep 60 >/dev/null 2>&1 & echo $!"], timeout=30, cwd=str(tmp_path),
    )
    orphan = int(cp.stdout)
    assert _wait_for(lambda: not _alive(orphan), 5)


def test_run_contained_on_timeout_raises_and_kills_the_whole_tree(tmp_path):
    pidfile = tmp_path / "pid"
    with pytest.raises(subprocess.TimeoutExpired):
        procguard.run_contained(
            ["sh", "-c", f"sleep 60 >/dev/null 2>&1 & echo $! > {pidfile}; sleep 60"],
            timeout=2, cwd=str(tmp_path),
        )
    assert _wait_for(lambda: not _alive(int(pidfile.read_text())), 5)


class _Libc:
    def __init__(self, calls):
        self.prctl = lambda *a: calls.append(("prctl", *a))


@pytest.mark.parametrize("parent_alive", [True, False])
def test_set_pdeathsig_arms_the_signal_and_fires_it_if_the_parent_already_died(
    monkeypatch, parent_alive,
):
    calls = []
    monkeypatch.setattr(procguard.ctypes, "CDLL", lambda *a, **k: _Libc(calls))
    monkeypatch.setattr(procguard.os, "getppid", lambda: 4242 if parent_alive else 1)
    monkeypatch.setattr(procguard.os, "kill", lambda pid, sig: calls.append(("kill", pid, sig)))
    procguard._set_pdeathsig(signal.SIGTERM, 4242)
    assert calls[0] == ("prctl", 1, signal.SIGTERM)           # PR_SET_PDEATHSIG == 1
    assert calls[1:] == ([] if parent_alive else [("kill", os.getpid(), signal.SIGTERM)])


def test_set_pdeathsig_without_prctl_is_a_no_op(monkeypatch):
    """Not Linux (no prctl in libc): skipped, never a crash in the forked child."""
    killed = []
    monkeypatch.setattr(procguard.ctypes, "CDLL", lambda *a, **k: object())
    monkeypatch.setattr(procguard.os, "getppid", lambda: 1)
    monkeypatch.setattr(procguard.os, "kill", lambda *a: killed.append(a))
    procguard._set_pdeathsig(signal.SIGTERM, 4242)
    assert killed == []


def test_identity_is_the_start_time_and_none_once_gone_or_a_zombie():
    zombie = subprocess.Popen(["true"])
    assert _wait_for(lambda: open(f"/proc/{zombie.pid}/stat").read().rsplit(")", 1)[1].split()[0] == "Z")
    with open("/proc/self/stat") as f:
        start = f.read().rsplit(")", 1)[1].split()[19]      # field 22: starttime
    assert procguard._identity(os.getpid()) == start
    assert procguard._identity(zombie.pid) is None
    zombie.wait()
    assert procguard._identity(zombie.pid) is None


@pytest.mark.parametrize("seen", [
    [None],                              # already gone at the first look → kill now
    ["7", "7", "7", None],               # alive, alive, then gone
    ["7", "7", "8"],                     # the pid was REUSED by a new process → it died
])
def test_watch_kills_the_group_once_the_watched_process_is_gone(monkeypatch, seen):
    seq, killed = iter(seen), []
    monkeypatch.setattr(procguard, "_identity", lambda pid: next(seq))
    monkeypatch.setattr(procguard, "_kill_own_group", lambda: killed.append(True))
    procguard._watch(4242, interval=0)
    assert killed == [True] and next(seq, "done") == "done"     # not one poll early


def test_the_guardian_kills_its_group_by_its_OWN_pid_never_killpg_0(monkeypatch):
    """`killpg(0)` from a guardian that shares its launcher's group would take that group
    down; naming the group by the guardian's pid cannot."""
    calls = []
    monkeypatch.setattr(procguard.os, "getpid", lambda: 4242)
    monkeypatch.setattr(procguard.os, "killpg", lambda g, s: calls.append(("killpg", g, s)))
    monkeypatch.setattr(procguard.os, "kill", lambda p, s: calls.append(("kill", p, s)))
    procguard._kill_own_group()
    assert calls == [("killpg", 4242, signal.SIGKILL), ("kill", 4242, signal.SIGKILL)]


def test_a_guardian_leading_no_group_still_kills_itself(monkeypatch):
    calls = []

    def killpg(g, s):
        raise ProcessLookupError(g)
    monkeypatch.setattr(procguard.os, "getpid", lambda: 4242)
    monkeypatch.setattr(procguard.os, "killpg", killpg)
    monkeypatch.setattr(procguard.os, "kill", lambda p, s: calls.append((p, s)))
    procguard._kill_own_group()
    assert calls == [(4242, signal.SIGKILL)]


def test_kill_group_sigkills_the_named_group_and_ignores_one_already_gone(monkeypatch):
    calls = []

    def killpg(g, s):
        calls.append((g, s))
        if g == 2:
            raise ProcessLookupError(g)
    monkeypatch.setattr(procguard.os, "killpg", killpg)
    procguard._kill_group(4242)
    procguard._kill_group(2)
    assert calls == [(4242, signal.SIGKILL), (2, signal.SIGKILL)]
