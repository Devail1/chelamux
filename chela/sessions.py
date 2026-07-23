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
evidence rather than proximity. Three signals, in order, each one a claim about *this*
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
3. **the cwd** — today's path, demoted to LAST. It is right for the only case the other
   two cannot cover: a brand-new window that has fired no hook and was not resumed. It
   never overrides a session id that is actually known, and it **refuses an origin two
   windows share** — exactly as :func:`chela.hooks._wid_in` does — because "newest file in
   the project dir wins" is precisely how one agent's output lands in another's topic. The
   event log is a bounded ring, so a quiet window's last hook event ages out on a busy
   fleet and resolution *falls back here*: this is a Tuesday, not an edge case.

Every signal that cannot be *bounded* is refused rather than believed. The event log is
only read against the claude process's start time (tmux reuses window ids); if that start
time cannot be read — no ``/proc``, a wrapper deeper than :data:`_MAX_DEPTH` — there is no
floor, so a recycled ``wid`` would inherit a **dead** agent's session. Unknown is not a
pass: the event log is then refused, and ``detail`` says so.

A session id is globally unique, so a known session needs no project dir at all — glob
``~/.claude/projects/*/<sid>.jsonl`` (the id is validated first: it is pasted into a glob,
so a ``*`` in it would match an *arbitrary agent's* transcript). And nothing here guesses:
a window whose transcript cannot be established resolves to **None**, loudly (see
:meth:`chela.telegram.monitor.TranscriptMonitor._poll_window` and the ``relay.transcripts``
fact in :mod:`chela.runtime_truth`) — the silence is the bug.

**Cost.** One ``tmux list-windows`` (TTL-cached) plus a handful of ``/proc`` reads per
refresh: no ``pgrep``, no ``capture-pane``, no ``claude agents --json``. That budget is
not decorative — :mod:`chela.hooks` resolves through here while a live agent is BLOCKED on
the hook (CMX-41 rejected the pgrep path precisely because it takes seconds).
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

from chela import config, event_log, transcripts

log = logging.getLogger(__name__)

# A session id is pasted into a glob, so it is validated as the uuid Claude Code emits
# rather than trusted. Two attacks, and the second is the one that actually bites: `../../`
# from a payload or a command line must not walk the disk, and a GLOB METACHARACTER must
# not match — `transcript_for_session("*")` would otherwise glob `*/*.jsonl` and hand back
# some arbitrary agent's transcript.
SESSION_RE = re.compile(r"^[0-9a-fA-F][0-9a-fA-F-]{7,63}$")

# Where the process facts come from. A module-level Path so a test can point the whole
# lookup at a fixture tree, and so a kernel without /proc simply degrades (the cmdline
# signal disappears; the event log and the cwd fallback still answer).
PROC = Path("/proc")

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
        return ""


def _children(pid: int) -> list[int]:
    """Direct children of ``pid``, straight from /proc — no pgrep, no process table scan."""
    try:
        raw = (PROC / str(pid) / "task" / str(pid) / "children").read_text()
    except OSError:
        return []
    out = []
    for token in raw.split()[:_MAX_CHILDREN]:
        try:
            out.append(int(token))
        except ValueError:
            continue
    return out


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
    """
    if _comm(pid) == "claude":
        return True
    try:
        raw = (PROC / str(pid) / "cmdline").read_bytes()
    except OSError:
        return False
    return b"claude" in raw


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


def _proc_cwd(pid: int) -> str | None:
    try:
        return os.readlink(str(PROC / str(pid) / "cwd")) or None
    except OSError:
        return None


def _resumed_session(pid: int) -> str | None:
    """``--resume <sid>`` / ``--resume=<sid>`` off a process's command line, validated."""
    try:
        raw = (PROC / str(pid) / "cmdline").read_bytes()
    except OSError:
        return None
    argv = [a for a in raw.decode("utf-8", "replace").split("\0") if a]
    for i, arg in enumerate(argv):
        candidate = None
        if arg in ("--resume", "-r") and i + 1 < len(argv):
            candidate = argv[i + 1]
        elif arg.startswith("--resume="):
            candidate = arg.split("=", 1)[1]
        if candidate and SESSION_RE.match(candidate):
            return candidate
    return None


def _proc_started(pid: int) -> float | None:
    """Epoch seconds the process started — the floor under a stale ``wid`` mapping."""
    try:
        stat = (PROC / str(pid) / "stat").read_text()
    except OSError:
        return None
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
            started=_proc_started(pid) if pid else None,
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
    source: str = "none"          # event_log | cmdline | cwd | none
    detail: str = ""

    @property
    def ok(self) -> bool:
        return self.path is not None


def resolve_window(wid: str, base: Path | None = None, pane: Pane | None = None,
                   pane_map: dict[str, Pane] | None = None) -> Resolution:
    """window id → the transcript it is really writing, by session id, cwd last.

    Returns a :class:`Resolution` whose ``path`` is None when the window's transcript
    cannot be established — never a plausible-looking guess. ``detail`` then says what was
    tried, because this failure is otherwise completely silent.
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
            return Resolution(wid, sid, path, "event_log",
                              f"the event log's newest session for {wid}")
        tried.append(f"the event log names session {sid} for {wid}, but no "
                     f"{sid}.jsonl exists under the projects dir")

    if pane and pane.resumed:
        path = transcript_for_session(pane.resumed, base)
        if path is not None:
            return Resolution(wid, pane.resumed, path, "cmdline",
                              f"`claude --resume {pane.resumed}` in the pane")
        tried.append(f"the pane runs `claude --resume {pane.resumed}`, but no "
                     f"{pane.resumed}.jsonl exists under the projects dir")

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
    return res.detail or "no evidence at all: no event, no command line, no cwd"
