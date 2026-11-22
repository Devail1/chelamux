"""``chela restart`` — restart interactive sessions in place onto a new Claude Code (CMX-467).

Updating Claude Code changes the binary on disk, not the processes already running it: every
open session keeps the old version until it is restarted. Doing that by hand, per window, is
what this replaces. Per session, and only when it is safe:

1. **Idle only.** The native ``session_status`` (``claude agents --json``, the same authority
   ``chela peek`` reads) must say ``idle`` — never a pane scrape. Busy, waiting or unknown:
   the session is not touched (``--wait`` waits for idle instead of skipping). And its prompt
   must be EMPTY (:func:`chela.messenger.input_draft`, which tells Claude Code's ghost
   suggestion from a typed draft): ``/exit`` typed after a draft would be submitted with it.
2. **Exit.** ``Escape`` then ``/exit``, then wait up to :data:`EXIT_TIMEOUT_S` (120 s) for
   Claude to exit — the window closes, or its pane is back at a shell. Exiting can take more
   than 30 s; the first hand-written restart gave up at 30 s and left a session down. A
   session that has not exited by then is left exactly as it is, and reported.
3. **Relaunch the SAME session id** through :func:`chela.restore.relaunch_session` — restore's
   own launch core, not a copy: the recorded launch settings (CMX-372), the persisted name
   (CMX-450, else the window's current name), Remote Control when it is on (CMX-460), and the
   fallback ladder (settings+RC, settings, plain+RC, plain). It is only "restarted" once the
   session is confirmed alive in the new window.
4. **The Telegram topic follows by session** — ``chela-telegram``'s reconcile rebinds the new
   window to its session's topic (CMX-450). This never writes ``telegram-bindings.json`` (that
   daemon owns it); it waits a little for the rebind and reports the topic the window got.
5. **The orchestrator** is re-registered at its new address.

⛔ **Never touched:** a window a dispatched run or a judge still claims, and a window whose
worktree belongs to a run that is not over — the dispatcher owns those.

**Restarting yourself.** A session cannot restart itself from inside its own turn: it is busy
while the command runs, and its ``/exit`` would kill the command. So a target that is the
caller's own window is handed to a DETACHED helper (:func:`arm_helper`: a new session via
``setsid``, so closing the window does not take it down) that waits for the window to go idle
and then runs the same steps. ``--all`` does every other session first, then the orchestrator
last; its result reaches the restarted orchestrator through the inbox.
"""
from __future__ import annotations

import contextlib
import fcntl
import logging
import os
import re
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from chela import config, discovery, dispatcher, restore, sessions

log = logging.getLogger(__name__)

# How long Claude gets to exit after `/exit`. Measured 2026-11-22: exit took over 30 s on a
# loaded box, and a 30 s cutoff left a session down. 120 s covered all 11 sessions.
EXIT_TIMEOUT_S = 120.0
# How long --wait waits for a busy session to go idle.
WAIT_IDLE_S = 1800.0
# How long to wait for chela-telegram to rebind the new window to its session's topic.
TOPIC_WAIT_S = 30.0
POLL_S = 1.0

RESTARTED = "restarted"
BUSY = "busy"                  # not idle — not touched
REFUSED = "refused"            # never ours to restart (dispatched, judge, no session…)
EXIT_TIMEOUT = "exit-timeout"  # /exit sent, Claude still running at the deadline
DOWN = "down"                  # Claude exited, but no relaunch came up alive
ARMED = "armed"                # handed to the detached helper (the caller's own window)

_SHELLS = frozenset({"bash", "zsh", "sh", "fish", "dash"})
_VERSION_RE = re.compile(r"(\d+\.\d+\.\d+)")
# The native installer keeps each Claude Code at ``…/claude/versions/<X.Y.Z>``.
_EXE_VERSION_RE = re.compile(r"/claude/versions/(\d+\.\d+\.\d+)(?: \(deleted\))?\Z")


@dataclass
class Result:
    """One session's restart, as the report prints it."""

    wid: str
    name: str = ""
    outcome: str = REFUSED
    session_id: str | None = None
    new_wid: str | None = None
    step: str = ""
    topic: str | None = None
    detail: str = ""

    def line(self) -> str:
        who = f"{self.name or '?'} {self.wid}"
        if self.outcome == RESTARTED:
            topic = f"topic {self.topic}" if self.topic else "no topic"
            return (f"✅ {who} → {self.new_wid}: resumed {(self.session_id or '')[:8]} "
                    f"[{self.step}], {topic}{self.detail}")
        mark = {BUSY: "⏸", ARMED: "⏳"}.get(self.outcome, "⛔")
        return f"{mark} {who}: {self.outcome} — {self.detail}"


@dataclass
class Ops:
    """Every tmux / process / store touch :func:`restart_one` makes, as a seam so the logic
    tests without tmux. :func:`live_ops` wires the real ones."""

    window: Callable[[str], dict | None]          # wid -> {"name", "cwd"} or None
    status: Callable[[str], str | None]           # wid -> native session_status
    session_of: Callable[[str], str | None]       # wid -> live session id
    runs: Callable[[], list[dict]]
    send_keys: Callable[[str, list[str]], None]
    exited: Callable[[str], bool]                 # wid -> True once Claude is gone
    window_alive: Callable[[str], bool]
    kill_window: Callable[[str], None]
    relaunch: Callable[..., restore.Relaunch]
    topic_for: Callable[[str], str | None]
    draft: Callable[[str], str | None]           # wid -> unsent prompt text ("" empty)
    orchestrator: Callable[[], str | None]
    register_orchestrator: Callable[[str], dict]
    sleep: Callable[[float], None] = time.sleep
    clock: Callable[[], float] = time.monotonic


def claimed_by_run(wid: str, cwd: str | None, runs: list[dict]) -> str | None:
    """Why ``wid`` belongs to the dispatcher (``None`` when it does not): a run that is not
    over claims it as its agent or judge window, or owns its worktree."""
    for row in runs or []:
        if dispatcher.run_is_terminal(row):
            continue
        task = row.get("task_id") or "?"
        if str(row.get("window_id") or "") == wid:
            return f"dispatched run {task} owns this window"
        if str(row.get("judge_window_id") or "") == wid:
            return f"the judge of {task} runs in this window"
    owner = restore.live_worktree_owner(cwd, runs)
    if owner:
        return f"its worktree belongs to run {owner}, still in flight"
    return None


def _wait(ops: Ops, until: Callable[[], bool], seconds: float) -> bool:
    """Poll ``until`` every :data:`POLL_S` for up to ``seconds``; True once it held."""
    deadline = ops.clock() + seconds
    while True:
        if until():
            return True
        if ops.clock() >= deadline:
            return False
        ops.sleep(POLL_S)


def restart_one(wid: str, ops: Ops, *, wait_idle: float = 0.0,
                exit_timeout: float = EXIT_TIMEOUT_S,
                topic_wait: float = TOPIC_WAIT_S) -> Result:
    """Restart one interactive session in place (see the module docstring for the steps)."""
    res = Result(wid)
    win = ops.window(wid)
    if win is None:
        res.detail = "not a live window"
        return res
    res.name, cwd = win.get("name") or "", win.get("cwd")
    why = claimed_by_run(wid, cwd, ops.runs())
    if why:
        res.detail = f"{why} — the dispatcher owns it"
        return res
    sid = ops.session_of(wid)
    if not sid or not sessions.SESSION_RE.match(sid):
        res.detail = "no live Claude session id for this window"
        return res
    res.session_id = sid
    if not cwd:
        res.detail = "the window's cwd is unknown"
        return res

    def idle() -> bool:
        return ops.status(wid) == "idle"

    if not (_wait(ops, idle, wait_idle) if wait_idle > 0 else idle()):
        res.outcome = BUSY
        res.detail = (f"not idle after waiting {wait_idle:.0f}s" if wait_idle > 0
                      else f"not idle ({ops.status(wid) or 'status unknown'})")
        return res

    # An idle session can still hold a half-typed prompt: `/exit` typed after it would be
    # appended and SUBMITTED as one turn. Unreadable is not "empty" — refuse both.
    draft = ops.draft(wid)
    if draft is None or draft:
        res.detail = ("its prompt holds an unsent draft — not typing /exit over it"
                      if draft else "its prompt could not be read — not typing /exit blind")
        return res
    was_orchestrator = ops.orchestrator() == wid
    old_topic = ops.topic_for(wid)
    ops.send_keys(wid, ["Escape"])
    ops.sleep(POLL_S)
    ops.send_keys(wid, ["-l", "/exit"])
    ops.send_keys(wid, ["Enter"])
    if not _wait(ops, lambda: ops.exited(wid), exit_timeout):
        res.outcome = EXIT_TIMEOUT
        res.detail = (f"/exit sent, Claude still running after {exit_timeout:.0f}s — "
                      "left as it is")
        return res
    if ops.window_alive(wid):
        ops.kill_window(wid)

    r = ops.relaunch(sid, cwd, name=res.name or None)
    res.step = r.step
    if not r.alive:
        res.outcome = DOWN
        res.detail = (f"Claude exited but no relaunch came up alive "
                      f"({r.result.error or 'never came up'}) — by hand: "
                      f"cd {cwd} && claude --resume {sid}")
        return res
    res.outcome, res.new_wid = RESTARTED, r.result.wid
    res.detail = r.detail
    if was_orchestrator and res.new_wid:
        reg = ops.register_orchestrator(res.new_wid)
        res.detail += (", orchestrator re-registered" if reg.get("ok") else
                       f", re-registering the orchestrator FAILED: {reg.get('error') or '?'}")
    if old_topic and res.new_wid:
        new_wid = res.new_wid
        _wait(ops, lambda: ops.topic_for(new_wid) is not None, topic_wait)
        res.topic = ops.topic_for(new_wid)
        if res.topic is None:
            res.detail += f" (had topic {old_topic}; not rebound yet)"
        elif res.topic != old_topic:
            res.detail += f" (was topic {old_topic})"
    return res


def interactive_windows() -> list[str]:
    """Every window running Claude, in window-id order. Dispatched runs and judges are
    among them; :func:`restart_one` refuses those."""
    from chela import agent_manager

    wids = [w for w in discovery.get_windows_by_id()
            if agent_manager.claude_pid(w) is not None]
    return sorted(wids, key=lambda w: int(w.lstrip("@")) if w.lstrip("@").isdigit() else 0)


def restart_all(ops: Ops, *, wids: list[str], self_wid: str | None,
                arm: Callable[[str], Result], exit_timeout: float = EXIT_TIMEOUT_S,
                topic_wait: float = TOPIC_WAIT_S) -> list[Result]:
    """Restart every session in ``wids`` one at a time. Busy ones are retried once at the
    end. The orchestrator goes LAST; a target that is the caller's own window (``self_wid``)
    is handed to ``arm`` (the detached helper) instead of being restarted inline."""
    orch = ops.orchestrator()
    last = [w for w in (orch,) if w and w in wids]
    first = [w for w in wids if w not in last]
    results: dict[str, Result] = {}

    def run(w: str) -> Result:
        if w == self_wid:
            return arm(w)
        return restart_one(w, ops, exit_timeout=exit_timeout, topic_wait=topic_wait)

    for w in first:
        results[w] = run(w)
    for w in first:
        if results[w].outcome == BUSY:
            results[w] = run(w)
    for w in last:
        results[w] = run(w)
        if results[w].outcome == BUSY and w != self_wid:
            results[w] = arm(w)      # the orchestrator is never skipped: it restarts when idle
    return [results[w] for w in first + last]


# --- one restart at a time -------------------------------------------------------------

def lock_path() -> Path:
    return config.CHELA_DIR / "restart.lock"


class Busy(Exception):
    """Another ``chela restart`` holds the lock."""


@contextlib.contextmanager
def one_at_a_time(timeout: float = 0.0, *, path: Path | None = None,
                  sleep=time.sleep, clock=time.monotonic):
    """Hold the restart lock (``flock`` on :func:`lock_path`) for the duration. Two restarts
    at once could both see a session idle, both ``/exit`` it and both relaunch it — two
    windows resuming one session. ``timeout`` 0 refuses at once (:class:`Busy`); a helper
    waits its turn. The kernel drops the lock when the holder dies, so it never wedges."""
    target = path or lock_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    with open(target, "a") as fh:
        deadline = clock() + timeout
        while True:
            try:
                fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if clock() >= deadline:
                    raise Busy("another chela restart is running") from None
                sleep(POLL_S)
        try:
            yield
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)


def running(path: Path | None = None) -> bool:
    """Whether a ``chela restart`` holds the lock right now (a probe: taken and released)."""
    try:
        with one_at_a_time(0.0, path=path):
            return False
    except Busy:
        return True


# --- the detached helper ---------------------------------------------------------------

def helper_log_path() -> Path:
    return config.CHELA_DIR / "restart-helper.log"


def helper_argv(args: list[str]) -> list[str]:
    """The helper's command: ``chela restart <args> --helper`` (a helper restarts inline and
    is never re-armed, and sends its result to the inbox)."""
    return [sys.executable, "-m", "chela.main", "restart", *args, "--helper"]


def helper_env(environ: dict | None = None) -> dict:
    """The helper's environment: the caller's, minus what names the caller's window
    (``CHELA_WID``, ``TMUX_PANE`` — the window is about to close), with the tmux session
    pinned so it does not derive it from that dead pane."""
    env = dict(os.environ if environ is None else environ)
    session = config.current_session()
    env.pop("CHELA_WID", None)
    env.pop("TMUX_PANE", None)
    env["CHELA_TMUX_SESSION"] = session
    return env


def spawn_helper(args: list[str], *, popen=subprocess.Popen,
                 log_path: Path | None = None) -> tuple[int, Path]:
    """Start ``chela restart <args> --helper`` DETACHED — its own session
    (``start_new_session``, i.e. ``setsid``), stdin closed, output appended to
    :func:`helper_log_path` — so it outlives the window it restarts. ``(pid, log path)``;
    raises ``OSError`` when it cannot start."""
    path = log_path or helper_log_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a") as out:
        proc = popen(helper_argv(args), stdin=subprocess.DEVNULL, stdout=out,
                     stderr=subprocess.STDOUT, start_new_session=True,
                     env=helper_env(), cwd=str(Path.home()))
    return proc.pid, path


def arm_helper(wid: str, *, popen=subprocess.Popen, log_path: Path | None = None) -> Result:
    """Hand ``wid`` to a detached helper that waits for it to go idle, then restarts it."""
    try:
        pid, path = spawn_helper([wid, "--wait"], popen=popen, log_path=log_path)
    except OSError as e:
        return Result(wid, outcome=REFUSED, detail=f"could not start the helper: {e}")
    return Result(wid, outcome=ARMED,
                  detail=f"helper pid {pid} restarts it once it is idle "
                         f"(log: {path}; the result lands in the inbox)")


# --- the real wiring -------------------------------------------------------------------

def _tmux(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["tmux", *args], capture_output=True, text=True, timeout=10)


def _native_status(wid: str) -> str | None:
    from chela import agent_manager, sandbox_status

    entry = agent_manager.session_entry(agent_manager.claude_pid(wid),
                                        agent_manager.session_status_map(force=True))
    status, _source = sandbox_status.resolve(wid, entry["status"] if entry else None)
    return status


def _window_alive(wid: str) -> bool:
    return wid in discovery.get_windows_by_id()


def _exited(wid: str) -> bool:
    """Claude is gone from ``wid``: the window closed, or its pane is back at a shell AND no
    Claude process is left in it (a wrapper can show ``bash`` while Claude still runs)."""
    from chela import agent_manager

    if not _window_alive(wid):
        return True
    cmd = _tmux("display-message", "-p", "-t", wid, "#{pane_current_command}").stdout.strip()
    return cmd in _SHELLS and agent_manager.claude_pid(wid) is None


def _draft(wid: str) -> str | None:
    from chela import messenger

    try:
        return messenger.input_draft(messenger.capture_pane(wid, ansi=True))
    except OSError:
        return None


def _send_keys(wid: str, keys: list[str]) -> None:
    _tmux("send-keys", "-t", wid, *keys)


def _topic_for(wid: str) -> str | None:
    from chela.telegram.bindings import BindingRegistry

    try:
        return BindingRegistry.load().thread_for_window(wid)
    except Exception:  # noqa: BLE001 — an unreadable bindings file is "no topic known"
        log.debug("restart: telegram bindings unreadable", exc_info=True)
        return None


def _session_of(wid: str) -> str | None:
    return sessions.live_session_of_window(wid) or sessions.session_of_window(wid)


def live_ops() -> Ops:
    from chela import inbox

    return Ops(
        window=lambda w: (None if (n := discovery.get_windows_by_id().get(w)) is None
                          else {"name": n, "cwd": discovery.get_window_cwd_by_id(w)}),
        status=_native_status,
        session_of=_session_of,
        runs=dispatcher.list_runs,
        send_keys=_send_keys,
        exited=_exited,
        window_alive=_window_alive,
        kill_window=restore._default_kill_window,
        relaunch=restore.relaunch_session,
        topic_for=_topic_for,
        draft=_draft,
        orchestrator=lambda: inbox.load().get("orchestrator"),
        register_orchestrator=lambda w: inbox.register(w, source="restart"),
    )


# --- which Claude Code each session runs (the dashboard's offer) ------------------------

def version_of_exe(path: str | None) -> str | None:
    """The version in a Claude Code binary's path — the native installer keeps each one at
    ``…/claude/versions/<X.Y.Z>`` — or ``None`` when the path names none."""
    if not path:
        return None
    m = _EXE_VERSION_RE.search(path)
    return m.group(1) if m else None


def running_version(pid: int | None) -> str | None:
    """The Claude Code version process ``pid`` runs, from its own ``/proc/<pid>/exe``."""
    if not pid:
        return None
    try:
        return version_of_exe(os.readlink(f"/proc/{pid}/exe"))
    except OSError:
        return None


def installed_version(which=None, run=subprocess.run) -> str | None:
    """The Claude Code a NEW launch gets: the version in the resolved ``claude`` path, else
    ``claude --version``. ``None`` when neither says — unknown never offers a restart."""
    import shutil

    found = (which or shutil.which)("claude")
    if not found:
        return None
    v = version_of_exe(os.path.realpath(found))
    if v:
        return v
    try:
        out = run([found, "--version"], capture_output=True, text=True, timeout=10,
                  stdin=subprocess.DEVNULL)
    except (OSError, subprocess.TimeoutExpired):
        return None
    m = _VERSION_RE.search(out.stdout or "") if out.returncode == 0 else None
    return m.group(1) if m else None


def _vkey(v: str) -> tuple[int, ...]:
    return tuple(int(x) for x in v.split("."))


def is_older(running: str | None, installed: str | None) -> bool:
    """``running`` is a strictly older version than ``installed``. Either unknown: False."""
    if not running or not installed:
        return False
    return _vkey(running) < _vkey(installed)


def stale_sessions(*, installed: str | None, windows: dict[str, str],
                   pid_of: Callable[[str], int | None],
                   version_of: Callable[[int | None], str | None] | None = None,
                   runs: list[dict] | None = None,
                   cwd_of: Callable[[str], str | None] = lambda w: None) -> list[dict]:
    """The interactive sessions running an older Claude Code than ``installed``:
    ``[{wid, name, version}]``. Dispatched runs and judges are left out — a restart never
    touches them, so they are not offered."""
    version_of = version_of or running_version
    out = []
    for wid, name in sorted(windows.items()):
        v = version_of(pid_of(wid))
        if not is_older(v, installed):
            continue
        if claimed_by_run(wid, cwd_of(wid), runs or []):
            continue
        out.append({"wid": wid, "name": name, "version": v})
    return out
