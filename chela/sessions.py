"""Which Claude Code SESSION is a tmux window running? — the one place that answers.

**``cwd`` is not the answer, and believing it was took the outbound relay down for an
hour on 2026-07-14.** The transcript monitor resolved a window to its transcript by
asking tmux for the pane's ``#{pane_current_path}`` and taking the newest ``*.jsonl`` in
``~/.claude/projects/<encoded-cwd>/``. That is a guess wearing a source of truth's
clothes, and it is wrong in three separate ways:

* **``--resume``.** A session's transcript stays in the project dir it was BORN in. The
  fleet's tmux server died in an OOM; the orchestrator rebuilt the window at
  ``…/analytics/data_prep`` and ran ``claude --resume <sid>`` — so the live JSONL kept
  growing under ``…-projects-analytics/`` while the monitor searched
  ``…-projects-analytics-data-prep/``, which held **zero** transcripts. Resolver returned
  None, nothing was relayed, and **nothing said so**: bindings reconciled, topics existed,
  inbound worked (it only needs the ``wid``). The human just never heard back.
* **``cd``.** ``pane_current_path`` follows the shell; an agent that ``cd``s moves it out
  from under the resolver. (Claude Code never ``chdir``s its own process — that is why the
  *process* cwd and the payload ``cwd`` disagree, and why the process cwd is usable and
  the pane path is not.)
* **two windows, one directory.** They collide on *"newest file in the project dir wins"*
  and silently tail **each other's** transcript. A relay that posts one agent's output
  into another agent's topic is worse than silence.

This is the same key CMX-48 already ripped out of the event log ("events filed against
the wrong window"), so the answer is the same: **resolve by ``session_id``**, and use
evidence rather than proximity. Four signals, in order, each one a claim about *this*
window made by something that cannot be wrong about it:

1. **the event log** — a hook fires INSIDE the agent's process and carries its
   ``session_id``; :mod:`chela.event_log` stores it next to the ``wid``. Ground truth,
   and it follows a ``/clear`` (which starts a new session id) for free. Bounded by the
   claude process's start time: tmux **reuses window ids after a server restart** (the
   live log has 309 events under a long-dead ``@113``), so a mapping older than the
   process now in the window is a different agent's and is refused, not inherited.
2. **the command line** — ``claude --resume <sid>`` in ``/proc/<pid>/cmdline`` of the
   pane's own claude process. It belongs to that pane by construction, so it resolves the
   exact case above even when no hook ever fired (the daemon was down, the agent predates
   the plugin, the fleet was rebuilt by hand).
3. **the native status feed** — CMX-184. `claude agents --json` reports a ``sessionId``
   for every pid it lists (:func:`chela.agent_manager.session_and_cwd_for_pid`), which
   answers for a window that has fired no hook and was not ``--resume``d — the exact case
   the first two signals cannot reach, and the one a hook-less, adopted window (never
   spawned by chela, never resumed) is stuck in forever otherwise. Bounded by ``cwd``, not
   by start time: an earlier version of this tier compared the pid's ``/proc`` start time
   against the feed's own ``startedAt`` for that pid, but ``startedAt`` turned out to be
   when the *session* began, not when the process forked — a resumed session predates its
   process, a cold start postdates it — so the two are not the same quantity and no
   tolerance admits one while excluding the other. Measured on a live box, they disagreed
   by up to 113 days, in both directions, on processes that were never recycled at all.
   What DOES mean the same thing on both sides is ``cwd``: the pid's own origin
   (:attr:`Pane.origin`) must equal the ``cwd`` the feed cached for that same pid, or the
   pid was recycled between the feed's last refresh and now and the cached session belongs
   to a **dead** process's directory — refused, not inherited. A recycled pid that also
   happens to land back in the exact same directory is a far narrower coincidence than a
   recycled pid alone. Reads the cache the dashboard's background refresh already keeps
   warm; never spawns the command itself (see Cost, below).

   **CMX-219.** ``cwd`` equality is a PROXY for "same process", not the fact itself — and
   the proxy breaks the instant a genuinely live, un-recycled session changes its own
   working directory (``EnterWorktree``/``ExitWorktree``, or any other in-process
   ``chdir``): the feed's cached ``cwd`` is then simply STALE, up to one refresh cycle
   behind, and looks identical to a recycled pid landing in a different directory. That is
   exactly what ``chela doctor`` caught live on ``@217`` — a session five minutes old,
   pid alive, transcript growing on disk, refused anyway because its cached cwd no longer
   agreed with its live one. So a ``cwd`` disagreement is no longer trusted as recycling on
   its own: it is cross-checked against the pid's own ``/proc`` start time
   (:func:`proc_started`), which :mod:`chela.agent_manager` now caches alongside ``cwd`` at
   the same refresh. A start time is stable across a ``cd`` and changes the instant the pid
   IS recycled — the same floor tier 1 already trusts, applied here as a second, independent
   witness rather than a replacement for the ``cwd`` check (``cwd`` agreeing is still the
   fast, common-case pass; the start time is only consulted when ``cwd`` disagrees).
4. **the cwd** — today's path, demoted to LAST. It is right for the only case the other
   signals cannot cover: a brand-new window that has fired no hook, was not resumed, and
   has no pid entry in the native feed either. It never overrides a session id that is
   actually known, and it **refuses an origin two windows share** — exactly as
   :func:`chela.hooks._wid_in` does — because "newest file in the project dir wins" is
   precisely how one agent's output lands in another's topic. The event log is a bounded
   ring, so a quiet window's last hook event ages out on a busy fleet and resolution
   *falls back here*: this is a Tuesday, not an edge case.

Every signal that cannot be *bounded* is refused rather than believed. The event log is
only read against the claude process's start time (tmux reuses window ids); if that start
time cannot be read — no ``/proc``, a wrapper deeper than :data:`_MAX_DEPTH` — there is no
floor, so a recycled ``wid`` would inherit a **dead** agent's session. Unknown is not a
pass: the event log is then refused, and ``detail`` says so. The native status feed is
refused the same way — no readable pid ``cwd`` (from ``/proc``), or none cached in the
feed for that pid, no floor, no tier 3.

**CMX-189.** :func:`chela.agent_manager.session_and_cwd_for_pid` returns ``(None, None)``
for two different situations a caller cannot otherwise tell apart: the feed has answered
and genuinely has nothing for this pid, or THIS PROCESS has never completed a fetch at all
(nobody called ``start_background_refresh`` here). ``detail`` distinguishes them explicitly
— :func:`chela.agent_manager.native_status_ever_fetched` is checked first — because reading
a cold cache as "the feed was asked and had nothing" cost real debugging time twice on
2026-07-27.

A session id is globally unique, so a known session needs no project dir at all — glob
``~/.claude/projects/*/<sid>.jsonl`` (the id is validated first: it is pasted into a glob,
so a ``*`` in it would match an *arbitrary agent's* transcript). And nothing here guesses:
a window whose transcript cannot be established resolves to **None**, loudly (see
:meth:`chela.telegram.monitor.TranscriptMonitor._poll_window` and the ``relay.transcripts``
fact in :mod:`chela.runtime_truth`) — the silence is the bug.

**Cost.** One ``tmux list-windows`` (TTL-cached) plus a handful of ``/proc`` reads per
refresh: no ``pgrep``, no ``capture-pane``, and no ``claude agents --json`` call of ITS
OWN — tier 3 only ever reads the cache :mod:`chela.agent_manager`'s background refresh
thread already keeps warm on its own 30s timer, elsewhere. That budget is not decorative —
:mod:`chela.hooks` resolves through here while a live agent is BLOCKED on the hook (CMX-41
rejected the pgrep path precisely because it takes seconds, and the native feed itself
measures 12-18s cold — CMX-179 — which is exactly why this never spawns it inline). That
budget is untouched by the CMX-296 promotion in :func:`resolve_window` (a small JSON
read-modify-write, skipped once the pin already agrees): :mod:`chela.hooks` never calls
:func:`resolve_window` itself, only the cheaper facts (:func:`panes`,
:func:`transcript_for_session`, :func:`wid_claiming_session`) it needs directly.
"""
from __future__ import annotations

import logging
import os
import re
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from chela import config, event_log, sessionids, transcripts

log = logging.getLogger(__name__)

# A session id is pasted into a glob, so it is validated as the uuid Claude Code emits
# rather than trusted. Two attacks, and the second is the one that actually bites: `../../`
# from a payload or a command line must not walk the disk, and a GLOB METACHARACTER must
# not match — `transcript_for_session("*")` would otherwise glob `*/*.jsonl` and hand back
# some arbitrary agent's transcript.
SESSION_RE = re.compile(r"^[0-9a-fA-F][0-9a-fA-F-]{7,63}$")

# Where the process facts come from. A module-level Path so a test can point the whole
# lookup at a fixture tree.
#
# ``/proc`` is the FAST PATH, not the only path: every fact below is one file read there,
# no subprocess at all, and Linux never leaves it. A host without ``/proc`` — macOS, where
# it does not exist at ALL — falls back to the POSIX tools that report the same facts
# (``pgrep``/``ps``/``lsof``, see ``_sh_*`` below). That fallback is reached only when the
# ``/proc`` read itself FAILS, so the Linux budget is unchanged and the "one tmux call and
# nothing else" guarantee still holds there.
#
# Before this shim, a /proc-less host lost `claude_pid` — and with it `started`,
# `resumed` and `launched_in` — so :func:`resolve_window`'s two strongest signals
# collapsed before they were ever tried and every same-cwd window resolved to None.
PROC = Path("/proc")

# Does THIS host have a real /proc? Decided once, from the real filesystem — deliberately
# NOT from ``PROC``, which tests point at a fixture tree.
#
# The distinction is the whole safety property. On a /proc host, a read that fails is a real
# ABSENCE — a dead pid, a fixture that omits the process — and must stay one. If a failed
# read fell through to `pgrep` there instead, a test that points PROC at a fixture to say
# "no claude process to find" would quietly scan the machine's live process table and
# answer from whatever happened to be running: host-dependent, and flaky exactly where it
# is hardest to debug. So only a host with NO /proc at all — macOS — ever falls back.
_PROC_HOST = Path("/proc").is_dir()

# The fallback queries are single, read-only process lookups. Bounded because this runs on
# the hook path, with an agent BLOCKED on it.
_SHIM_TIMEOUT = 2.0

# The claude process is normally the pane shell's direct child, but a wrapper (a `sg`, an
# `env`, a launcher script) can sit in between. Walk a couple of generations, not the
# whole tree — this runs on the hook path.
_MAX_DEPTH = 3

_MAX_CHILDREN = 32

_TTL = 1.0
_panes_cache: dict = {"ts": 0.0, "panes": {}}
_panes_lock = threading.Lock()

_CLK_TCK = os.sysconf("SC_CLK_TCK") if hasattr(os, "sysconf") else 100


@dataclass(frozen=True)
class Pane:
    """What a window's pane — and the claude process inside it — actually claim.

    ``path`` is tmux's ``#{pane_current_path}``: it MOVES when the agent ``cd``s, so it is
    the weakest thing here and is used only as a last resort. ``launched_in`` is the claude
    process's own cwd, which does not move (Claude Code tracks its working directory
    internally and never ``chdir``s), and ``resumed`` is the session id off its command
    line — the only signal that survives a ``--resume`` from a different directory.
    """

    wid: str
    path: str = ""
    command: str = ""
    claude_pid: int | None = None
    launched_in: str | None = None
    resumed: str | None = None
    started: float | None = None

    @property
    def origin(self) -> str | None:
        """The directory this window's agent was launched in — the pane path only if the
        process could not be read (no /proc, no claude running)."""
        return self.launched_in or self.path or None


# --- process facts ------------------------------------------------------------------
#
# Each fact is one function with the /proc read first and the POSIX query second. Keeping
# the pair INSIDE the function is deliberate: every call site (and every test that points
# PROC at a fixture tree) keeps working untouched, and there is no "which backend am I on"
# flag that can drift out of step with what the machine can actually answer.

def _sh(argv: list[str]) -> str | None:
    """Run one small read-only process query; stdout, or None on any failure.

    Returns None immediately on a /proc host: there, a failed read means the fact is
    genuinely absent, and asking the process table instead would answer a fixture's
    question with the machine's live state (see :data:`_PROC_HOST`). This is the single
    gate every fallback below inherits.

    Never raises: a missing tool (``lsof`` is not installed everywhere), a non-zero exit
    (the pid died between listing and asking) and a timeout are all the same answer here —
    "this fact is unavailable" — which every caller already handles.
    """
    if _PROC_HOST:
        return None
    try:
        result = subprocess.run(argv, capture_output=True, text=True, timeout=_SHIM_TIMEOUT)
    except (OSError, subprocess.SubprocessError):
        return None
    return result.stdout if result.returncode == 0 else None


def _first_line(out: str | None) -> str:
    """The first non-empty line of a command's output, stripped."""
    for line in (out or "").splitlines():
        if line.strip():
            return line.strip()
    return ""


def _boot_time() -> float:
    """Epoch seconds the machine booted — the base for a process's start time."""
    try:
        for line in (PROC / "stat").read_text().splitlines():
            if line.startswith("btime "):
                return float(line.split()[1])
    except (OSError, ValueError, IndexError):
        pass
    return 0.0


def _comm(pid: int) -> str:
    try:
        return (PROC / str(pid) / "comm").read_text().strip()
    except OSError:
        pass
    # `ps -o comm=` prints an absolute path on macOS and a bare name on Linux; /proc's
    # `comm` is always the basename, so normalise to that and keep one comparison.
    return os.path.basename(_first_line(_sh(["ps", "-o", "comm=", "-p", str(pid)])))


def _ppid(pid: int) -> int | None:
    """A process's parent pid — /proc's ``stat`` first, ``ps -o ppid=`` as the fallback.

    The upward counterpart of :func:`_children`'s downward walk: :func:`own_claude_pid`
    climbs ancestry with this the same way :func:`_claude_pid` descends with ``_children``.
    """
    try:
        stat = (PROC / str(pid) / "stat").read_text()
        fields = stat[stat.rindex(")") + 1:].split()
        return int(fields[1])          # field 4 overall, 2nd after the comm
    except (OSError, ValueError, IndexError):
        pass
    text = _first_line(_sh(["ps", "-o", "ppid=", "-p", str(pid)]))
    return int(text) if text.isdigit() else None


def _cap_children(pid: int, kids: list[int]) -> list[int]:
    """Keep the newest ``_MAX_CHILDREN`` (CMX-210 — both sources list oldest-first), and
    if that actually cut anything, say so OUT LOUD.

    A caller downstream of this (:func:`_claude_pid`, and past it every ``claude_pid is
    None`` check in the codebase) cannot tell "this pid genuinely has no such child" from
    "this pid has more than _MAX_CHILDREN and the one you wanted may have been among the
    dropped" — those are different facts, and only the first one means the process is
    gone. Ordering (CMX-210) makes the miss rarer; it does not make it distinguishable.
    A WARNING here is the only signal that a `None` answer downstream might actually be
    this cap, not a dead process — silent truncation reads as a confident answer when
    it is really "didn't fully check" (this is what cmx-210 left unfixed).
    """
    if len(kids) > _MAX_CHILDREN:
        log.warning(
            "pid %d has %d children — _MAX_CHILDREN=%d kept the newest, dropped %d of "
            "the oldest; a miss below this may be that cap, not a genuinely absent child",
            pid, len(kids), _MAX_CHILDREN, len(kids) - _MAX_CHILDREN,
        )
    return kids[-_MAX_CHILDREN:]


def _children(pid: int) -> list[int]:
    """Direct children of ``pid`` — straight from /proc, else ``pgrep -P``."""
    try:
        raw = (PROC / str(pid) / "task" / str(pid) / "children").read_text()
    except OSError:
        return _sh_children(pid)
    out = []
    for token in raw.split():
        try:
            out.append(int(token))
        except ValueError:
            continue
    return _cap_children(pid, out)


def _sh_children(pid: int) -> list[int]:
    """``pgrep -P <pid>`` — one pid per line.

    Each line must be ENTIRELY digits to count. `pgrep` emits nothing else, so this only
    ever rejects output that did not come from `pgrep` at all — which is exactly what a
    test that stubs `subprocess.run` wholesale hands back, and inheriting a pid from it
    would be a fact invented out of another command's stdout.
    """
    out = _sh(["pgrep", "-P", str(pid)])
    kids: list[int] = []
    for line in (out or "").splitlines():
        token = line.strip()
        if token.isdigit():
            kids.append(int(token))
    return _cap_children(pid, kids)


def _cmdline_argv(pid: int) -> list[str]:
    """A process's argv — NUL-split from /proc, else whitespace-split from ``ps -o args=``.

    ``ps`` flattens argv into one space-joined string, so an argument containing a space
    splits in two here. Both readers of this only look for ``--resume <sid>`` and the
    substring ``claude``, and a session id can hold neither a space nor a quote, so the
    flattening cannot change either answer.
    """
    try:
        raw = (PROC / str(pid) / "cmdline").read_bytes()
    except OSError:
        return (_sh(["ps", "-o", "args=", "-p", str(pid)]) or "").split()
    return [a for a in raw.decode("utf-8", "replace").split("\0") if a]


def _looks_like_claude(pid: int) -> bool:
    """Tolerant "is this process claude" test — the same substring match
    :func:`chela.agent_manager.claude_pid` makes via ``pgrep -P <pane_pid> -f claude``, so
    the two window-attribution paths agree instead of one silently failing where the other
    succeeds (CMX-160).

    ``/proc``'s ``comm`` is the executable's BASENAME (and truncated to 15 bytes), not the
    full invocation — a claude launched through a version manager or a wrapper can report
    as ``node`` or a versioned binary rather than bare ``claude``, and an exact-equals
    check against ``comm`` alone then returns None: no ``claude_pid``, so no ``started``/
    ``resumed``/``launched_in``, and :func:`resolve_window`'s two strongest signals
    collapse before they are ever tried. So this falls back to a substring scan of the
    full command line — the same thing ``pgrep -f`` matches against — before giving up.

    Both reads go through the /proc-or-POSIX pair above, so the tolerance CMX-160 added is
    real on a host with no /proc rather than a branch that can never be reached: there,
    ``comm`` and ``cmdline`` were BOTH unreadable, so this returned False for a process
    ``ps`` plainly reports as ``claude`` (CMX-1xx).
    """
    if _comm(pid) == "claude":
        return True
    return any("claude" in arg for arg in _cmdline_argv(pid))


def _claude_pid(pane_pid: int) -> int | None:
    """The claude process running in a pane (breadth-first, a couple of generations)."""
    frontier = [pane_pid]
    for _ in range(_MAX_DEPTH):
        nxt: list[int] = []
        for pid in frontier:
            for child in _children(pid):
                if _looks_like_claude(child):
                    return child
                nxt.append(child)
        if not nxt:
            return None
        frontier = nxt
    return None


# A `chela watch` invoked with no window climbs through a Bash-tool subshell, and
# sometimes an interpreter/launcher wrapper in between, before it reaches the claude
# process itself — a couple more generations than the downward pane walk (`_MAX_DEPTH`)
# needs, since it crosses layers `_claude_pid` never has to. Bounded so an oddly-nested
# launcher (or an init-reparented orphan) can't walk all the way to pid 1.
_MAX_ANCESTRY = 6


def own_claude_pid(pid: int | None = None) -> int | None:
    """The nearest CLAUDE ancestor of ``pid`` (default: this process) — the pid-resolution
    half of CMX-255's windowless-orchestrator mechanism.

    A windowless session has no tmux pane for :func:`chela.agent_manager.claude_pid` to
    walk (that resolver needs a ``#{pane_pid}``, and there is no pane at all) — but a
    ``chela watch`` invoked FROM inside such a session is, by construction, a descendant
    of it. Its own process ancestry proves the answer directly instead of guessing at it:
    no scan of the whole process table, no name/cwd heuristic across unrelated processes,
    just "is my own parent, or its parent, the claude process that spawned me". None if no
    ancestor looks like claude within :data:`_MAX_ANCESTRY` generations — e.g. run from a
    plain shell, never inside a claude session at all.
    """
    cur = os.getpid() if pid is None else pid
    for _ in range(_MAX_ANCESTRY):
        parent = _ppid(cur)
        if not parent or parent <= 1:
            return None
        if _looks_like_claude(parent):
            return parent
        cur = parent
    return None


def session_id_for_pid(pid: int) -> str | None:
    """Best-effort session identity for a live pid that has no window to resolve it
    through — the identity half of CMX-255's windowless-orchestrator mechanism.

    Two of :func:`resolve_window`'s four signals apply to a bare pid (the other two — the
    event log, the cwd — are keyed by WINDOW and have no pid-only form): the process's own
    ``--resume <sid>`` command line (:func:`_resumed_session`, belongs to it by
    construction), then the native ``claude agents --json`` feed's cached ``sessionId`` for
    this pid (:func:`chela.agent_manager.session_and_cwd_for_pid`, CMX-184) — a pure cache
    read of whatever the caller's own background refresh already keeps warm, never a fetch
    of its own. None means unresolved, never a guess: the windowless registration still
    proceeds without an identity, exactly like ``chela watch``'s existing "no session
    identity" path for the window-addressed case.
    """
    resumed = _resumed_session(pid)
    if resumed:
        return resumed
    from chela import agent_manager        # deferred: agent_manager sits above sessions
    sid, _ = agent_manager.session_and_cwd_for_pid(pid)
    return sid


def _proc_cwd(pid: int) -> str | None:
    try:
        return os.readlink(str(PROC / str(pid) / "cwd")) or None
    except OSError:
        pass
    # `lsof -Fn` is field output: one field per line, the value after a 1-char tag. `-d cwd`
    # narrows it to the one descriptor we want, so the first `n` line IS the cwd.
    for line in (_sh(["lsof", "-a", "-p", str(pid), "-d", "cwd", "-Fn"]) or "").splitlines():
        if line.startswith("n"):
            return line[1:].strip() or None
    return None


def _resumed_session(pid: int) -> str | None:
    """``--resume <sid>`` / ``--resume=<sid>`` off a process's command line, validated."""
    argv = _cmdline_argv(pid)
    for i, arg in enumerate(argv):
        candidate = None
        if arg in ("--resume", "-r") and i + 1 < len(argv):
            candidate = argv[i + 1]
        elif arg.startswith("--resume="):
            candidate = arg.split("=", 1)[1]
        if candidate and SESSION_RE.match(candidate):
            return candidate
    return None


def proc_started(pid: int) -> float | None:
    """Epoch seconds the process started — the floor under a stale ``wid`` mapping.

    Public (not ``_``-prefixed): :mod:`chela.agent_manager` calls this too, at its own
    native-status refresh, to cache the pid's start time alongside the cwd it already
    caches — CMX-219, tier 3's second bound (see the module docstring above).
    """
    try:
        stat = (PROC / str(pid) / "stat").read_text()
    except OSError:
        return _sh_started(pid)
    # The comm field can contain spaces and parens; everything after the last ')' is safe.
    try:
        fields = stat[stat.rindex(")") + 1:].split()
        ticks = float(fields[19])            # field 22 overall, 20th after the comm
    except (ValueError, IndexError):
        return None
    boot = _boot_time()
    if not boot or not _CLK_TCK:
        return None
    return boot + ticks / _CLK_TCK


def _sh_started(pid: int) -> float | None:
    """``ps -o lstart=`` → epoch seconds.

    ``lstart`` is an ABSOLUTE local timestamp ("Thu Jul 23 14:03:35 2026"), so unlike
    /proc's jiffies-since-boot it needs no boot time to interpret — which is why the
    fallback does not have to reproduce :func:`_boot_time` at all. The only consumer
    compares it against event-log timestamps, and both are epoch seconds.
    """
    text = _first_line(_sh(["ps", "-o", "lstart=", "-p", str(pid)]))
    if not text:
        return None
    try:
        return time.mktime(time.strptime(text, "%a %b %d %H:%M:%S %Y"))
    except ValueError:
        return None


# --- the pane map -------------------------------------------------------------------

def _load_panes() -> dict[str, Pane]:
    """``{window_id: Pane}`` — ONE tmux call, then /proc for the process facts."""
    try:
        result = subprocess.run(
            ["tmux", "list-windows", "-t", config.current_session(), "-F",
             "#{window_id}\t#{pane_current_command}\t#{pane_current_path}\t#{pane_pid}"],
            capture_output=True, text=True, timeout=5,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return {}
    if result.returncode != 0:
        return {}
    out: dict[str, Pane] = {}
    for line in result.stdout.splitlines():
        parts = line.split("\t")
        if len(parts) != 4:
            continue
        wid, command, path, pane_pid = (p.strip() for p in parts)
        if not wid:
            continue
        pid = None
        try:
            pid = _claude_pid(int(pane_pid)) if pane_pid else None
        except ValueError:
            pid = None
        out[wid] = Pane(
            wid=wid, path=path, command=command, claude_pid=pid,
            launched_in=_proc_cwd(pid) if pid else None,
            resumed=_resumed_session(pid) if pid else None,
            started=proc_started(pid) if pid else None,
        )
    return out


def panes(force: bool = False) -> dict[str, Pane]:
    """The pane map, TTL-cached. ``force`` re-reads now (a window that just appeared)."""
    now = time.time()
    if not force and now - _panes_cache["ts"] < _TTL:
        return _panes_cache["panes"]
    with _panes_lock:
        if not force and time.time() - _panes_cache["ts"] < _TTL:
            return _panes_cache["panes"]      # a concurrent caller refreshed it
        _panes_cache["panes"] = _load_panes()
        _panes_cache["ts"] = time.time()
    return _panes_cache["panes"]


def wid_claiming_session(session_id: str | None,
                         pane_map: dict[str, Pane] | None = None) -> str | None:
    """The window whose claude process was launched with ``--resume <session_id>``.

    The strongest window→session link there is: the pane's own process says so. Two panes
    claiming one session is impossible in practice and resolves to None anyway — a wrongly
    filed event is worse than an unfiled one (CMX-48).
    """
    if not session_id:
        return None
    claims = [p.wid for p in (panes() if pane_map is None else pane_map).values()
              if p.resumed == session_id]
    return claims[0] if len(claims) == 1 else None


def session_of_window(wid: str | None,
                      pane_map: dict[str, Pane] | None = None) -> str | None:
    """The claude SESSION a live window is running — its stable identity, not its address.

    Same evidence order as :func:`resolve_window`, minus the transcript hop: the event log
    first (hook-borne, so it comes from inside the agent's own process, and it follows a
    ``/clear`` to a fresh session id), then the pane's own ``claude --resume <sid>`` command
    line. Bounded by the claude process's start time, so a recycled window id cannot inherit a
    dead agent's session (:func:`_session_from_log`). ``None`` when nothing can say — never a
    guess. This is the ``@N`` → identity half; :func:`wid_for_session` is the inverse.
    """
    if not wid:
        return None
    pane_map = panes() if pane_map is None else pane_map
    pane = pane_map.get(wid)
    if pane and pane.started is not None:
        sid = _session_from_log(wid, since=pane.started)
        if sid:
            return sid
    if pane and pane.resumed:
        return pane.resumed
    return None


def wid_for_session(session_id: str | None,
                    pane_map: dict[str, Pane] | None = None) -> str | None:
    """The LIVE window currently running ``session_id`` — the inverse of :func:`session_of_window`.

    This is what lets a PERSISTED address self-heal after tmux renumbers the fleet: the window
    id changed, but the session resumed into the new window is the same identity. Two
    self-verifying signals, strongest first:

    * the pane's own command line — ``claude --resume <sid>`` (:func:`wid_claiming_session`),
      which belongs to that pane by construction and refuses an ambiguous claim;
    * the event log — the newest window a hook filed this session under, accepted ONLY if that
      window is live now AND its own process still resolves to this session
      (:func:`session_of_window`, bounded by the pane's start time) — so a recycled ``@N`` that
      a stale record still names cannot be inherited.

    ``None`` when nothing live is running the session (it really is gone) — never a guess: a
    wrong window is worse than none (CMX-48).
    """
    if not session_id or not SESSION_RE.match(session_id):
        return None
    pane_map = panes() if pane_map is None else pane_map
    claimed = wid_claiming_session(session_id, pane_map)
    if claimed:
        return claimed
    for rec in reversed(event_log.ring()):
        if rec.get("session_id") != session_id:
            continue
        wid = rec.get("wid")
        if not wid or wid not in pane_map:
            continue
        if session_of_window(wid, pane_map) == session_id:
            return wid
    return None


# --- session → transcript -----------------------------------------------------------

def transcript_for_session(session_id: str | None, base: Path | None = None) -> Path | None:
    """``<sid>`` → its transcript, wherever it lives. The project dir is not needed.

    A session id is globally unique, so this globs ``~/.claude/projects/*/<sid>.jsonl``
    rather than deriving the directory from a cwd that may not be the session's at all.
    The path is resolved through symlinks so a hand-made shim and its target are one file
    (the monitor keys its read offset on the path).
    """
    if not session_id or not SESSION_RE.match(session_id):
        return None
    root = base or transcripts.CLAUDE_PROJECTS_DIR
    try:
        hits = sorted({Path(os.path.realpath(p)) for p in root.glob(f"*/{session_id}.jsonl")})
    except OSError:
        return None
    if not hits:
        return None
    if len(hits) == 1:
        return hits[0]
    return max(hits, key=lambda p: p.stat().st_mtime)


def _session_from_log(wid: str, since: float) -> str | None:
    """The newest ``session_id`` the event log has filed against ``wid`` — hook-borne, so
    it comes from inside the agent's own process.

    ``since`` is the claude process's start time, and it is REQUIRED: tmux reuses window
    ids across a server restart, so a mapping recorded before the process now living in
    that window belongs to a **different agent** and is refused. A record with no readable
    timestamp is refused for the same reason — an unbounded mapping is exactly the thing
    the floor exists to reject. Newest-first, so the first record older than the floor ends
    the search: everything behind it is older still.
    """
    for rec in reversed(event_log.ring()):
        if rec.get("wid") != wid:
            continue
        ts = rec.get("ts")
        if not isinstance(ts, (int, float)) or ts < since:
            return None
        sid = rec.get("session_id")
        if isinstance(sid, str) and sid:
            return sid
    return None


def _norm(path: str) -> str:
    """Both sides of an origin comparison through one normaliser (symlinks, ``~``, ``/``)."""
    try:
        return os.path.realpath(os.path.expanduser(path))
    except (OSError, ValueError):
        return path


def _windows_sharing(origin: str, wid: str, pane_map: dict[str, Pane]) -> list[str]:
    """The OTHER windows launched in ``origin`` — the panes the cwd guess cannot tell apart."""
    want = _norm(origin)
    return sorted(p.wid for p in pane_map.values()
                  if p.wid != wid and p.origin and _norm(p.origin) == want)


@dataclass(frozen=True)
class Resolution:
    """A window's transcript, and — as importantly — WHICH EVIDENCE said so."""

    wid: str
    session_id: str | None = None
    path: Path | None = None
    source: str = "none"          # event_log | cmdline | native_status | cwd | none
    detail: str = ""

    @property
    def ok(self) -> bool:
        return self.path is not None


def _promote(wid: str, session_id: str) -> None:
    """Durably pin a tier-1/tier-2 resolution — CMX-296.

    :mod:`chela.sessionids` is written at spawn time, but only for windows chela itself
    launched (:mod:`chela.spawn`, the dashboard resume path). An orchestrator or agent
    started by hand — a manual ``claude`` in a tmux pane, or a ``--resume`` a human typed
    outside chela — never earns one that way, no matter how many times it resolves here,
    and stays dependent on the event log's fleet-wide, bounded ring for every future
    resolution. Promoting a resolution actually made here (event log or ``--resume`` on the
    command line — never the cwd guess, which is not an identification at all) closes that
    for exactly the windows that need it, and does so for every caller of
    :func:`resolve_window` — `chela doctor`'s fact check and the Telegram outbound relay's
    transcript poller both resolve windows chela did not spawn, not just its own.

    Skips the write when the pin already agrees, so a caller that resolves the same window
    on every tick (the transcript poller does, once per bound window) does not turn into a
    steady stream of file writes for a session id that never changes. Best-effort, same
    contract as :func:`chela.spawn._record_session_id`: a store-write failure must never
    fail the resolution it is riding along with.
    """
    try:
        if sessionids.session_id_for(wid) == session_id:
            return
        sessionids.set_session_id(wid, session_id)
    except Exception:  # noqa: BLE001 — a promotion failure must never fail resolution itself
        log.warning("sessions: failed to promote resolved session id for %s", wid,
                    exc_info=True)


def resolve_window(wid: str, base: Path | None = None, pane: Pane | None = None,
                   pane_map: dict[str, Pane] | None = None) -> Resolution:
    """window id → the transcript it is really writing, by session id, cwd last.

    Returns a :class:`Resolution` whose ``path`` is None when the window's transcript
    cannot be established — never a plausible-looking guess. ``detail`` then says what was
    tried, because this failure is otherwise completely silent.

    A resolution made via the event log or ``--resume`` (never the cwd guess) is also
    promoted into the durable :mod:`chela.sessionids` pin — see :func:`_promote`.
    """
    if not wid:
        return Resolution(wid="", detail="no window id")
    if pane_map is None:
        pane_map = panes()
    if pane is None:
        pane = pane_map.get(wid)
    tried: list[str] = []

    sid = None
    if pane is None or pane.started is None:
        # No floor, so no bound on how old a mapping may be — and tmux recycles window ids.
        # Believing the log here inherits a DEAD agent's session into a live topic. Unknown
        # is not a pass.
        tried.append(f"the claude process in {wid} could not be read (no /proc, or a "
                     "wrapper too deep), so its start time is unknown and the event log's "
                     "session for this window is REFUSED: a recycled window id would "
                     "inherit a dead agent's session")
    else:
        sid = _session_from_log(wid, since=pane.started)
    if sid:
        path = transcript_for_session(sid, base)
        if path is not None:
            _promote(wid, sid)
            return Resolution(wid, sid, path, "event_log",
                              f"the event log's newest session for {wid}")
        tried.append(f"the event log names session {sid} for {wid}, but no "
                     f"{sid}.jsonl exists under the projects dir")

    if pane and pane.resumed:
        path = transcript_for_session(pane.resumed, base)
        if path is not None:
            _promote(wid, pane.resumed)
            return Resolution(wid, pane.resumed, path, "cmdline",
                              f"`claude --resume {pane.resumed}` in the pane")
        tried.append(f"the pane runs `claude --resume {pane.resumed}`, but no "
                     f"{pane.resumed}.jsonl exists under the projects dir")

    if pane and pane.claude_pid:
        from chela import agent_manager  # lazy, and a cache READ only — see the module Cost note
        nsid, ncwd = agent_manager.session_and_cwd_for_pid(pane.claude_pid)
        if nsid and SESSION_RE.match(nsid):
            cwd_agrees = (pane.origin is not None and ncwd is not None
                          and _norm(ncwd) == _norm(pane.origin))
            # CMX-219: a cwd disagreement alone is not proof of recycling — a live,
            # un-recycled process can legitimately chdir (EnterWorktree/ExitWorktree) and
            # the feed's cached cwd simply lags behind by up to one refresh cycle. Cross-
            # check against the pid's own /proc start time, cached by chela.agent_manager
            # at the same refresh: it is stable across a cd and changes the instant the pid
            # IS recycled, so agreement here is real proof the cwd mismatch was a `cd`, not
            # a different process wearing the same pid.
            cached_started = agent_manager.started_for_pid(pane.claude_pid)
            same_process = (cached_started is not None and pane.started is not None
                            and cached_started == pane.started)
            if not cwd_agrees and not same_process:
                tried.append(
                    f"the native status feed reports session {nsid} for pid "
                    f"{pane.claude_pid}, but its cached cwd does not agree with the "
                    "pane's own origin, and the pid's own start time does not confirm "
                    "it is still the same process either (or one of the two is unknown) "
                    "— REFUSED: the pid may have been recycled since the feed last saw "
                    "it, and a recycled pid would inherit a dead process's session")
            else:
                path = transcript_for_session(nsid, base)
                if path is not None:
                    detail = (f"`claude agents --json` reports session {nsid} for pid "
                              f"{pane.claude_pid}")
                    if not cwd_agrees:
                        detail += (" — its cached cwd is stale, but the pid's own start "
                                   "time confirms this is still the same, un-recycled "
                                   "process (CMX-219: a cd, not a recycle)")
                    return Resolution(wid, nsid, path, "native_status", detail)
                tried.append(
                    f"the native status feed names session {nsid} for pid "
                    f"{pane.claude_pid}, but no {nsid}.jsonl exists under the projects dir")
        elif not agent_manager.native_status_ever_fetched():
            # CMX-189: a cold cache (this process has never completed a `claude agents
            # --json` fetch — e.g. it never called start_background_refresh) and a warm
            # cache that simply has nothing for this pid both make session_and_cwd_for_pid
            # return (None, None). Silently falling through to the cwd tier here is how
            # this read as "the feed was consulted and had nothing" on 2026-07-27, when it
            # had never been asked in this process at all.
            tried.append(
                f"the native status feed has NEVER answered in this process (no "
                f"background refresh has completed yet), so tier 3 has nothing to say "
                f"about pid {pane.claude_pid} — a cold cache, NOT the feed reporting no "
                "session for it")
        else:
            tried.append(
                f"the native status feed has answered in this process, but reports "
                f"nothing for pid {pane.claude_pid}")

    cwd = pane.origin if pane else None
    if cwd is None:
        from chela import discovery                # lazy: keeps the hook path off tmux twice
        cwd = discovery.get_window_cwd_by_id(wid)

    # The cwd cannot tell two agents in one directory apart: it hands both of them whichever
    # file was written last, i.e. it posts one agent's output into the other's topic — worse
    # than silence. `hooks._wid_in` refuses this; so does this. (And it is REACHABLE: the
    # event log is a bounded ring, so a quiet window's last hook event ages out.)
    shared = _windows_sharing(cwd, wid, pane_map) if cwd else []
    if shared:
        tried.append(f"the cwd fallback is REFUSED: {', '.join([wid] + shared)} were all "
                     f"launched in {cwd}, and the newest transcript there could belong to "
                     "any of them — a relay into the wrong agent's topic is worse than "
                     "silence")
        return Resolution(wid, None, None, "none", "; ".join(tried))

    path = transcripts.transcript_for_cwd(cwd, base=base)
    if path is not None:
        return Resolution(wid, None, Path(os.path.realpath(path)), "cwd",
                          f"newest transcript under the project dir of {cwd} — a GUESS: "
                          "no hook has ever named this window's session, and it was not "
                          "resumed")
    tried.append(f"no transcript under the project dir of {cwd or 'an unknown cwd'} "
                 "(the cwd fallback)")
    return Resolution(wid, sid, None, "none", "; ".join(tried))


def transcript_for_window(wid: str, base: Path | None = None) -> Path | None:
    """:func:`resolve_window`, for a caller that only wants the path."""
    return resolve_window(wid, base=base).path


def explain(wid: str, base: Path | None = None) -> str:
    """Why a window resolved the way it did — what a LOUD failure prints."""
    res = resolve_window(wid, base=base)
    if res.ok:
        return f"{res.path} (via {res.source}: {res.detail})"
    return res.detail or "no evidence at all: no event, no command line, no native status, no cwd"
