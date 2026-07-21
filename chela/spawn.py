"""One tmux-window spawn, shared by the dashboard's ``/api/agents/spawn`` and ``/new``.

There are exactly two ways a chela window is born from a user gesture: the dashboard
launcher (:func:`chela.dashboard.app.api_agents_spawn`) and the Telegram ``/new`` bridge
(:func:`chela.telegram.newsession.launch_claude_window`). They must open the window the
SAME way, and the only way to guarantee that is for there to be ONE way. This module is it.

**Why a second copy is a bug, not a convenience.** The window-open sequence is load-bearing
in ways that are invisible until they drift apart: ``-P -F '#{window_id}'`` so the spawn is
addressable *by id* (a name collides; an id never does), the name pinned against BOTH of
tmux's rename mechanisms so a ``claude`` launched into it never flickers the window — and so
the topic — to "claude", ``CHELA_WID`` exported so the agent knows its own window, the
trailing ``:`` on the session target so tmux resolves the session and not a same-named
window. Miss one in a *second* copy and that path quietly loses a property the other keeps:
the day one gains an epoch/naming change and the other does not is the CMX-77 desync class
arriving through the back door. So both callers route through :func:`spawn_window`; the
setup is identical because it is literally the same code.

The window-creation half only. What each caller does with the result is its own: the
dashboard records the cwd in its Recent list and answers JSON; ``/new`` relays a Telegram
message and leaves the *bind* to the auto-topics reconcile. And command VALIDATION stays
with each caller, at the door untrusted input comes through — the dashboard vets a
user-supplied ``command`` against its allowlist before ever calling here; ``/new`` passes
the configured :data:`chela.agent_manager.DEFAULT_LAUNCH_CMD`, which is not user input.
"""
from __future__ import annotations

import logging
import os
import re
import subprocess
from dataclasses import dataclass

from chela import agent_manager, config, discovery

log = logging.getLogger(__name__)

# tmux echoes a window id like ``@7`` from ``new-window -P -F '#{window_id}'``. Used to tell
# that id from a fallback (an older tmux, an odd build) so a spawn that HAS an id stays
# addressable by it — CHELA_WID and the id-keyed lock both depend on having the real id.
_WID_RE = re.compile(r"@\d+")

# A generated window name must satisfy the same charset the dashboard rename API enforces
# (letters, digits, '-' or '_'). ``shell-N`` always does; this asserts the invariant so a
# future change to the name scheme can't silently produce a name tmux/the API would reject.
_WINDOW_NAME_RE = re.compile(r"^[A-Za-z0-9_-]+$")


@dataclass
class SpawnResult:
    """The outcome of one :func:`spawn_window` call.

    ``ok`` — did a window open. On success ``name`` is its tmux window name, ``cwd`` the
    resolved absolute directory it opened in, and ``wid`` the ``@N`` id when tmux gave one
    back (``None`` if this tmux build did not echo one — the window still exists, it is just
    only addressable by name). On failure ``error`` carries a human-facing reason and the
    rest are ``None``.
    """

    ok: bool
    name: str | None = None
    wid: str | None = None
    cwd: str | None = None
    error: str | None = None


def next_shell_name(existing: set[str]) -> str:
    """Smallest ``shell-N`` (N >= 1) not already a live window name.

    A generic ``shell-N`` on purpose: the auto-topics reconcile then names the resulting
    topic after the PROJECT — the cwd basename — rather than after ``shell-3``
    (:func:`chela.telegram.reconcile.topic_name_for` keys off
    :func:`chela.agent_manager.is_generic_name`).
    """
    n = 1
    while f"shell-{n}" in existing:
        n += 1
    return f"shell-{n}"


def _send(target: str, text: str) -> None:
    """``send-keys`` the literal ``text`` then Enter into ``target``; log a failure, never raise.

    The window already exists by the time this runs, so a tmux hiccup here is a *half*-launch
    (the shell is up, the keystrokes did not land) — worth a log line, never a reason to
    unwind a window that is already open. This is the dashboard's long-standing behaviour, now
    the shared one. ``-l`` sends the bytes literally (no key-name lookup), and tmux buffers
    them into the pty, so they land even before the shell has finished drawing its prompt.
    """
    try:
        subprocess.run(["tmux", "send-keys", "-t", target, "-l", text],
                       capture_output=True, text=True, timeout=10)
        subprocess.run(["tmux", "send-keys", "-t", target, "Enter"],
                       capture_output=True, text=True, timeout=10)
    except (FileNotFoundError, subprocess.TimeoutExpired) as e:
        log.warning("send-keys to %s failed (%r): %s", target, text, e)


def spawn_window(cwd: str | os.PathLike, *, command: str | None = None) -> SpawnResult:
    """Open ONE tmux window in ``cwd`` and, if given, launch ``command`` in it.

    The single window-creation path both the dashboard launcher and the Telegram ``/new``
    bridge call, so the two can never drift. Steps, in order:

    * resolve ``cwd`` (``~`` + symlinks) and require it exists — else a ``no such directory``
      failure result (never a raised exception into a request/update queue);
    * ensure the chela session exists — a missing session is an expected boot-ordering
      condition, so create it rather than fail (:func:`chela.discovery.ensure_session`);
      only tmux being wholly unreachable fails the spawn;
    * pick the next free ``shell-N`` name and open the window with ``-P -F '#{window_id}'``
      so the spawn is addressable by id, targeting ``<session>:`` (trailing ``:`` forces
      session resolution — a bare session name is ambiguous to tmux when a *window* shares
      it);
    * pin the name against BOTH ``allow-rename`` (OSC) and ``automatic-rename`` (command
      follow) so a launched ``claude`` never flickers the window — and its bound topic — to
      "claude" (:func:`chela.agent_manager.lock_window_name`);
    * export ``CHELA_WID`` into the fresh shell so the agent knows its own window id
      (self-identity for peek/read/drive), whenever tmux gave us the id;
    * if ``command`` is given, ``send-keys`` it — we start a shell and *send* the command
      rather than running it as the window command, so the pane survives the command exiting.

    Command VALIDATION is the caller's job, done before calling here: the dashboard vets a
    user-supplied ``command`` against its ``claude``-only allowlist (untrusted input);
    ``/new`` passes the trusted :data:`chela.agent_manager.DEFAULT_LAUNCH_CMD`.
    """
    real = os.path.realpath(os.path.expanduser(str(cwd)))
    if not os.path.isdir(real):
        return SpawnResult(ok=False, error=f"no such directory: {cwd}")

    # The session may not exist yet (fresh boot, or a `wsl --shutdown` that took the tmux
    # server with it). It's chela's own session, so create it rather than fail the spawn with
    # a raw "error connecting to /tmp/tmux-1000/default" the user can do nothing about.
    # Idempotent + race-safe; only tmux being unreachable fails.
    if not discovery.ensure_session():
        return SpawnResult(
            ok=False, error="tmux is unreachable — cannot create the chela session")

    session = config.current_session()
    name = next_shell_name(set(discovery.get_all_windows()))
    if not _WINDOW_NAME_RE.match(name):
        return SpawnResult(ok=False, error=f"invalid window name: {name}")
    target = f"{session}:{name}"
    try:
        proc = subprocess.run(
            ["tmux", "new-window", "-t", f"{session}:", "-n", name, "-c", real,
             "-P", "-F", "#{window_id}"],
            capture_output=True, text=True, timeout=10,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as e:
        return SpawnResult(ok=False, error=str(e))
    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout or "tmux new-window failed").strip()
        return SpawnResult(ok=False, error=err)

    wid = (proc.stdout or "").strip()
    have_wid = bool(_WID_RE.fullmatch(wid))
    # Pin the name against tmux's automatic-rename (command-follow) and allow-rename (OSC).
    # `new-window -n` already disables automatic-rename; assert both explicitly so the
    # invariant can't drift out from under a claude launched into the window.
    agent_manager.lock_window_name(wid if have_wid else target)
    if have_wid:
        _send(target, f"export CHELA_WID={wid}")
    if command:
        _send(target, command)

    log.info("spawned window %s (%s) in %s%s", name, wid or "no-id", real,
             f" running {command!r}" if command else "")
    return SpawnResult(ok=True, name=name, wid=wid if have_wid else None, cwd=real)
