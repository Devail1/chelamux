"""The bug itself, driven against a REAL tmux server that really restarts.

``tests/test_epoch.py`` pins the LOGIC of CMX-77 with the window table and the epoch handed
in. That is worth having, and it is not proof: every tmux fact in it is a fixture, so it
reproduces the blind spot (a tidy ``@N`` and a tidy epoch string) rather than the outage.
The outage was a tmux SERVER dying and coming back — the ids renumbering from ``@0``, a
stranger inheriting the number the orchestrator's queue was addressed to, and nothing
saying so. Nothing is proved about that until a server is actually killed and actually
comes back, and the product code — :mod:`chela.epoch`, :mod:`chela.discovery`,
:mod:`chela.messenger` — is made to talk to it.

So this file does exactly that, twice:

  * :func:`test_the_epoch_is_the_pid_AND_the_start_time_read_from_the_server_itself` — the
    ONE line the module exists to get right. ``_FORMAT`` is the whole thesis: a pid alone
    is recycled by the kernel, and a recycled pid makes a dead address look current again.
    Mocks cannot check that line, because a mock IS the derivation it is meant to be
    checking. This asks the running server for its pid and its start time and insists the
    epoch carries BOTH — so two servers that share a pid and differ in start time can never
    compare equal.
  * :func:`test_a_tmux_restart_renumbers_the_fleet_and_the_queue_is_NOT_pasted_into_the_stranger`
    — the outage, end to end. Register the orchestrator under server A, watch a real
    delivery land in its pane (the positive control: the pipeline is live, so a later
    silence means something), kill A, start B, and let B hand ``@0`` to somebody else. The
    queue must not go to them; the alarm must go out; ``chela watch`` from the session that
    is really there must drain it.

⛔ TMUX ISOLATION — read this before touching a line of it. Every tmux call is pinned to a
scratch socket with ``-L <sock>``, a PER-COMMAND flag that cannot be lost, exactly as
``tests/test_terminals_selfheal.py`` does it. A BARE ``kill-server`` destroys the developer's
live fleet — that has happened three times, and this file's whole job is to kill servers.
Four independent things have to fail before that can happen here:

  1. ``-L <scratch sock>`` on every call this file makes itself (via :class:`Scratch`);
  2. a PATH shim that injects ``-L <sock>`` into the bare ``tmux`` the PRODUCT code runs
     (``epoch.current``, ``discovery``, ``messenger`` all shell out to plain ``tmux``);
  3. ``$TMUX_TMPDIR`` pointed at a scratch dir, so even a lost shim cannot resolve the
     default socket;
  4. the session name stays conftest's ``chela-tests-no-such-session`` — the real fleet has
     no session by that name, so a leaked call has nothing to target. It is created HERE, on
     the scratch socket, which is the only place it will ever exist.

:func:`_assert_scratch` refuses to run a command if the socket ever stops looking like a
scratch one. Nothing in this file may ever call ``kill-server`` without ``-L``.
"""
from __future__ import annotations

import logging
import os
import shutil
import subprocess
import tempfile
import time
import uuid

import pytest

from chela import config, discovery, epoch, event_log, inbox, runtime_truth

TMUX_BIN = shutil.which("tmux")

pytestmark = pytest.mark.skipif(TMUX_BIN is None, reason="tmux not installed")


def _assert_scratch(sock: str) -> None:
    """Hard guard: never, ever operate on the default socket (that's the live fleet)."""
    assert sock.startswith("chelatest-") and sock != "default", f"unsafe tmux socket: {sock}"


class Scratch:
    """A tmux server of our own, that we may kill — and a way to ask it what it is.

    Deliberately NOT a way to talk to any other server: every call goes through
    :meth:`_tmux`, which pins ``-L`` and asserts the socket is a scratch one first.
    """

    def __init__(self, sock: str, env: dict[str, str]):
        _assert_scratch(sock)
        self.sock, self.env = sock, env
        self.session = config.current_session()      # "chela-tests-no-such-session" (conftest)

    def _tmux(self, *args: str, check: bool = True) -> subprocess.CompletedProcess:
        _assert_scratch(self.sock)
        return subprocess.run([TMUX_BIN, "-L", self.sock, *args],
                              env=self.env, capture_output=True, text=True, check=check)

    def start(self, *window_names: str) -> None:
        """Boot a server and give it these windows, in order. A FRESH server always numbers
        its windows from ``@0`` — which is the entire bug.

        The retry is not superstition: a ``new-session`` that reaches the socket of a server
        that is still finishing its own death dies with "server exited unexpectedly". The
        restart this file exists to drive is exactly that window, so it has to be waited out.
        """
        first, rest = window_names[0], window_names[1:]
        deadline = time.time() + 10
        while True:
            done = self._tmux("new-session", "-d", "-s", self.session, "-n", first,
                              check=False)
            if done.returncode == 0:
                break
            assert time.time() < deadline, f"tmux would not start a server: {done.stderr}"
            time.sleep(0.1)
        for name in rest:
            self._tmux("new-window", "-d", "-t", self.session, "-n", name)

    def kill(self) -> None:
        """The OOM. Scoped to the scratch socket ONLY — never a bare ``kill-server``.

        And waited for: tmux tears the server down asynchronously, so returning while it is
        still dying would hand the NEXT server a socket that is about to be unlinked.
        """
        self._tmux("kill-server", check=False)
        deadline = time.time() + 10
        while self._tmux("list-sessions", check=False).returncode == 0:
            assert time.time() < deadline, "the scratch tmux server would not die"
            time.sleep(0.1)

    def ask(self, fmt: str) -> str:
        return self._tmux("display-message", "-p", fmt).stdout.strip()

    def wid(self, name: str) -> str:
        """The window id tmux has given the window called ``name``, right now."""
        return next(w for w, n in discovery.get_windows_by_id().items() if n == name)

    def pane(self, wid: str) -> str:
        return self._tmux("capture-pane", "-p", "-J", "-t", f"{self.session}:{wid}",
                          check=False).stdout


@pytest.fixture
def tmux(tmp_path, monkeypatch):
    """A scratch tmux server the PRODUCT CODE will find when it shells out to ``tmux``.

    ``epoch.current()``, ``discovery`` and ``messenger`` all run a bare ``tmux``, so the
    only way to make them talk to our server instead of the developer's is to own the PATH
    they resolve it on. The shim re-execs the real binary with ``-L <sock>`` — per
    invocation, so it cannot be dropped the way a ``$TMUX_TMPDIR`` can.
    """
    sock = f"chelatest-{os.getpid()}-{uuid.uuid4().hex[:6]}"
    _assert_scratch(sock)

    shim = tmp_path / "shim"
    shim.mkdir()
    tmux_shim = shim / "tmux"
    tmux_shim.write_text(f'#!/bin/sh\nexec {TMUX_BIN} -L {sock} "$@"\n')
    tmux_shim.chmod(0o755)

    # Belt-and-braces (the `-L` above is the guarantee): a short scratch socket dir, so a
    # bare tmux that somehow escaped the shim still cannot resolve the default socket. NOT
    # under tmp_path — a unix socket path is capped at ~108 chars and pytest's tmp nests deep.
    tmuxdir = tempfile.mkdtemp(prefix="cmx77", dir="/tmp")

    monkeypatch.setenv("PATH", f"{shim}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("TMUX_TMPDIR", tmuxdir)
    monkeypatch.delenv("TMUX", raising=False)
    monkeypatch.delenv("TMUX_PANE", raising=False)   # never inherit this agent's own pane

    server = Scratch(sock, dict(os.environ))
    yield server
    server.kill()
    shutil.rmtree(tmuxdir, ignore_errors=True)


# --- the derivation: the one line mocks cannot check ------------------------------------

def test_the_epoch_is_the_pid_AND_the_start_time_read_from_the_server_itself(tmux):
    """`_FORMAT` IS the module. A pid alone is not an identity — the kernel recycles them.

    The day a fresh tmux server is handed the pid of the one that died, a pid-only epoch
    makes every stale address look current again, and CMX-77 is silently back: the queue
    goes into whoever inherited `@0`. The pair (pid, start time) cannot collide — two
    servers may share either half, never both.

    This asserts against tmux's OWN answers, so it fails if the format ever loses a half —
    which is the only way to guard a line whose entire content is a derivation.
    """
    tmux.start("orchestrator")

    stamp = epoch.current()
    pid, started = tmux.ask("#{pid}"), tmux.ask("#{start_time}")
    assert stamp and pid and started
    assert started.isdigit(), "start_time should be the server's boot instant, not a label"

    assert pid in stamp, "the epoch must name the server process"
    assert started in stamp, (
        "the epoch carries the pid but NOT the start time — so a tmux server the kernel "
        "hands a recycled pid to is indistinguishable from the one that died, and every "
        "address stamped under the dead one silently reads as current again"
    )
    assert stamp != pid, "a pid-only epoch is the collision this module exists to prevent"

    # The collision itself: the SAME pid, a LATER server. It must not read as the same epoch.
    reborn = stamp.replace(started, str(int(started) + 3600))
    assert reborn != stamp
    assert epoch.is_dangling(stamp, reborn) is True, \
        "a later server that reused the pid is a DIFFERENT epoch — or nothing is stamped"
    # ...and the epoch we just read really is the one that is running (not a stale constant).
    assert epoch.is_dangling(stamp, epoch.current()) is False


def test_with_no_server_running_the_epoch_is_UNKNOWN_not_a_value(tmux):
    """The other half of the derivation: when tmux has nothing to say, neither do we.

    There is no server on this socket yet. `current()` must be None — never ""; never a
    value that could sit in `is_dangling` and accuse a perfectly good stamp of being stale
    (that would invalidate every address in the fleet on one tmux hiccup).
    """
    assert epoch.current() is None
    assert epoch.is_dangling("786-1784045825", epoch.current()) is False


# --- the outage: a server that really dies, and really comes back renumbered --------------

IDLE = inbox.IDLE


@pytest.fixture
def live_inbox(tmp_path, monkeypatch):
    """The real inbox, on a real file — with only the two things tmux cannot give us stubbed.

    `status_snapshot` shells out to `claude agents --json`: there are no claude sessions in
    these scratch windows, so the status map is ours to say (and it is what makes the test
    sharp — the stranger is IDLE, i.e. the old gate would have opened and delivered). The
    transcript read is stubbed to nothing for the same reason. Every WINDOW ID, every EPOCH
    and every SEND in this test is real.
    """
    monkeypatch.setenv("CHELA_INBOX_FILE", str(tmp_path / "inbox.json"))
    monkeypatch.setenv("CHELA_EVENTS_FILE", str(tmp_path / "events.jsonl"))
    monkeypatch.delenv("CHELA_ORCHESTRATOR_WID", raising=False)
    monkeypatch.setattr(inbox, "INBOX_ENABLED", True)
    monkeypatch.setattr(inbox.transcripts, "last_assistant_activity", lambda cwd: None)
    monkeypatch.setattr(inbox.notify, "enabled", lambda: False)   # no phone push from a test

    statuses: dict[str, str] = {}
    monkeypatch.setattr(inbox, "status_snapshot", lambda: dict(statuses))
    return statuses


def _runs(n: int) -> list[dict]:
    """The finished PRs the orchestrator has to be told about — real enough rows that the
    delivery-time re-validation (`inbox.stale_reason`) still sees them as TRUE."""
    return [{"task_id": f"T{i}", "title": "a task", "status": "awaiting_review",
             "branch_name": f"cmx-7{i}", "window_name": f"cmx-7{i}", "pr_state": "open",
             "pr_url": f"https://github.com/x/y/pull/{i}"} for i in range(n)]


def _kinds() -> list[str]:
    return [e["type"] for e in event_log.read()["events"]]


def _doctor_inbox() -> list[runtime_truth.Finding]:
    return runtime_truth.audit(runtime_truth.fact("inbox.address"))


def _assert_nothing_was_pasted_into(pane: str) -> None:
    """Not "the pane is byte-identical" — a shell's own prompt draws itself a moment late,
    and a test that fails on that would be testing starship. The claim is narrower and it is
    the one that matters: NO review notification ever appears in a stranger's terminal. Every
    inbox message opens with 📥, and a review says what it is. (NOT the branch name: this
    suite runs from the cmx-77 worktree, so the shell's own prompt says "cmx-77" in every
    pane — a marker that matches the prompt would fail on the prompt, not on a paste.)"""
    assert "📥" not in pane and "awaiting review" not in pane, \
        "a review notification was pasted into the window that INHERITED the id"


def test_a_tmux_restart_renumbers_the_fleet_and_the_queue_is_NOT_pasted_into_the_stranger(
        tmux, live_inbox, caplog):
    """2026-07-14, driven for real: the server dies, the fleet comes back renumbered.

    Server A issues `@0` to the orchestrator and the inbox is addressed to it. Server A is
    killed. Server B starts and gives `@0` — the SAME number — to somebody else's agent. The
    queue behind that address must not be delivered to them (it is an instruction, and they
    would act on it), the failure must be LOUD (an ERROR, a durable event, a red doctor —
    the whole cost of the outage was that it was silent), and the queue must survive to be
    delivered to whoever registers next.
    """
    # --- server A: the fleet as it was ---------------------------------------------------
    tmux.start("orchestrator")
    orch = tmux.wid("orchestrator")
    epoch_a = epoch.current()
    assert inbox.register(orch)["epoch"] == epoch_a, "the address is stamped as it is written"

    # The positive control, and it is not optional: prove the pipeline DELIVERS under the
    # server the address was issued in. Without it, the silence after the restart proves
    # nothing — a test harness that never delivers anything would "pass" just as well.
    live_inbox[orch] = IDLE
    inbox.tick({}, runs=_runs(1))
    assert "cmx-70" in tmux.pane(orch), "the review never reached the orchestrator's pane"
    assert inbox.load()["queue"] == []

    # --- the OOM ------------------------------------------------------------------------
    tmux.kill()
    assert epoch.current() is None, "the server is dead; there is no epoch to be in"

    # --- server B: same numbers, different windows ---------------------------------------
    tmux.start("cmx-88-worker")                     # a stranger's agent, renumbered onto @0
    stranger = tmux.wid("cmx-88-worker")
    epoch_b = epoch.current()

    assert stranger == orch, (
        "tmux did not reissue the orchestrator's id, so this run never reproduced the bug"
    )
    assert epoch_b and epoch_b != epoch_a, "a new server must not wear the dead one's epoch"
    assert inbox.load()["orchestrator"] == orch     # ...and the store still says @0 is ours

    # The stranger is IDLE — the old gate (`statuses.get(orch) != IDLE`) would have opened
    # and pasted five review notifications straight into their prompt.
    live_inbox.clear()
    live_inbox[stranger] = IDLE

    # Four more PRs finish while the address is rotten (T0 went out before the restart) —
    # this is the queue that grew, in silence, while five reviews sat waiting.
    with caplog.at_level(logging.ERROR, logger="chela.inbox"):
        inbox.tick({}, runs=_runs(5))

    _assert_nothing_was_pasted_into(tmux.pane(stranger))
    assert len(inbox.load()["queue"]) == 4, "the events must survive to be delivered later"
    assert "UNDELIVERABLE" in caplog.text and "dangling" in caplog.text, "it failed SILENTLY"
    assert "inbox_undeliverable" in _kinds(), "nothing durable said the inbox had stopped"

    # ...and the doctor — the surface a human actually looks at — goes RED about this exact
    # address, against the live tmux server. It was green 14/14 through the real outage.
    findings = _doctor_inbox()
    assert [f.level for f in findings] == [runtime_truth.ERROR]
    assert orch in findings[0].title and "GONE" in findings[0].title
    assert findings[0].fact == "inbox.address"

    # --- recovery: the orchestrator says where it really is -------------------------------
    tmux._tmux("new-window", "-d", "-t", tmux.session, "-n", "orchestrator")
    reborn = tmux.wid("orchestrator")
    assert reborn != orch                            # a new window, a new number: @1

    assert inbox.register(reborn)["queued"] == 4, "the queue the dead address was holding"
    live_inbox[reborn] = IDLE

    for _ in range(4):
        inbox.tick({}, runs=_runs(5))

    pane = tmux.pane(reborn)
    assert all(f"cmx-7{i}" in pane for i in range(1, 5)), "the queue never drained to the orch"
    assert inbox.load()["queue"] == []
    _assert_nothing_was_pasted_into(tmux.pane(stranger))   # ...and never was, throughout
    assert not [f for f in _doctor_inbox() if f.level == runtime_truth.ERROR]


def test_a_watch_survives_its_own_server_but_never_the_next_one(tmux, live_inbox):
    """The same rot, one store over: a watch is an `@N` too.

    The orchestrator watched `@1`. tmux restarted and gave `@1` to a different agent. A
    status read against that number is now a read of a STRANGER — and it is exactly the read
    that produces "your agent finished; verify + commit", about work nobody did. The watch
    is retired instead, and the truth (outcome UNKNOWN) is what goes out.
    """
    tmux.start("orchestrator", "cmx-9")
    orch, agent = tmux.wid("orchestrator"), tmux.wid("cmx-9")
    inbox.register(orch)
    assert inbox.watch(agent, "fix the parser", by=orch)["ok"]
    assert inbox.watches()[agent]["epoch"] == epoch.current()

    tmux.kill()
    tmux.start("orchestrator", "someone-else")       # @0, @1 — reissued to different windows
    reborn, stranger = tmux.wid("orchestrator"), tmux.wid("someone-else")
    assert stranger == agent, "tmux did not reissue the watched id; the bug is not reproduced"
    inbox.register(reborn)                           # the orchestrator re-registers, as it must

    live_inbox.update({reborn: IDLE, stranger: IDLE})
    inbox.tick({stranger: inbox.BUSY})               # a busy->idle edge on the STRANGER

    assert inbox.watches() == {}, "the dead watch survived to lie about somebody else's work"
    pane = tmux.pane(reborn)
    assert "tmux SERVER restarted" in pane and "UNKNOWN" in pane
    assert "finished" not in pane, "it reported a stranger's idleness as our agent's success"
    assert "watch_epoch_lost" in _kinds()


def test_the_scratch_socket_never_touches_the_real_fleet(tmux):
    """The guard on the guard. This file kills tmux servers for a living, and a bare
    `kill-server` has taken the live fleet down three times. If the isolation ever breaks,
    it must break HERE — loudly, in a test that asserts it — not in the developer's session.
    """
    tmux.start("orchestrator")
    # The product code, resolving a bare `tmux` off PATH, must land on OUR server...
    assert epoch.current() == tmux.ask("#{pid}-#{start_time}")
    # ...and our server is not the one the developer is sitting in.
    assert os.environ["TMUX_TMPDIR"].startswith("/tmp/cmx77")
    assert tmux.session == "chela-tests-no-such-session", \
        "conftest's session pin is the last line of defence — a leaked call must find NOTHING"
    with pytest.raises(AssertionError):
        Scratch("default", dict(os.environ))         # the socket that is the live fleet
