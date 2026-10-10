"""Keep a Remote Control session's claude.ai / desktop name equal to its window name (CMX-39).

A window chela launches FOR A HUMAN starts ``claude --remote-control <window name>``
(:func:`chela.spawn.spawn_window`), so claude.ai and the Claude desktop show the chela
window name from the first moment. A tmux rename never reaches Claude Code, though — the
only way in is Claude Code's own ``/rename <name>`` typed into the session (measured live:
it propagates to the desktop sidebar). So every rename chela makes of such a window — a
manual one from the dashboard, the reconcile loop's duplicate ``-N`` — goes through ONE
function here, :func:`request`, which queues ``/rename <new name>``.

**Delivery is the dangerous half.** ``/rename`` is typed into somebody's prompt. It is
sent ONLY when the session is idle (the native ``claude agents --json`` status, never a
pane guess) AND its prompt is empty — read off an SGR-aware capture, because Claude Code's
grey ghost SUGGESTION in an empty prompt reads exactly like a typed draft in a plain one
(see :func:`chela.dispatcher._drop_ghost_suggestion`). Busy, waiting, a real draft, a status
we can't read: the rename stays queued and the daemon retries every tick
(:func:`flush_pending`). It never interrupts a turn and never touches a draft.

**State lives on the window**, as tmux user options, like ``@chela_manual_name``: it dies
with the window, needs no sidecar file, and both processes that rename (the dashboard and
the daemon) see the same queue.

* :data:`PUSHED_OPTION` — the name Claude Code was last given (set at launch to the
  ``--remote-control`` value). Its presence is also what marks a window as a chela-launched
  Remote Control session: a window without it (a dispatcher agent, a judge, a plain shell, a
  launch with Remote Control off) never gets ``/rename``.
* :data:`PENDING_OPTION` — the name waiting to be pushed.

Out of scope: a rename made IN the desktop app does not come back to chela — Claude Code
exposes no API for it — and the next chela rename overwrites it.
"""
from __future__ import annotations

import logging
import re
import subprocess

from chela import config

log = logging.getLogger(__name__)

PUSHED_OPTION = "@chela_rc_name"
PENDING_OPTION = "@chela_rc_pending"

# A name we are willing to type after `/rename`: printable, one line, no escape bytes.
_SAFE_NAME_RE = re.compile(r"^[^\x00-\x1f\x7f-\x9f]{1,128}$")


def _target(wid: str) -> str:
    return f"{config.current_session()}:{wid}"


def _get_option(wid: str, option: str) -> str:
    try:
        out = subprocess.run(
            ["tmux", "display-message", "-p", "-t", _target(wid), f"#{{{option}}}"],
            capture_output=True, text=True, timeout=5,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return ""
    return out.stdout.strip() if out.returncode == 0 else ""


def _set_option(target: str, option: str, value: str | None) -> None:
    cmd = (["tmux", "set-window-option", "-t", target, option, value] if value is not None
           else ["tmux", "set-window-option", "-u", "-t", target, option])
    try:
        subprocess.run(cmd, capture_output=True, text=True, timeout=5)
    except (FileNotFoundError, subprocess.TimeoutExpired) as e:
        log.warning("rc_rename: set %s on %s failed: %s", option, target, e)


def mark_launched(target: str, name: str) -> None:
    """Record that ``target`` was launched with ``--remote-control <name>``.

    ``target`` is any tmux window ref (``@N`` or ``session:name``). This makes the window
    eligible for :func:`request` and records ``name`` as already pushed.
    """
    _set_option(target, PUSHED_OPTION, name)


def request(wid: str, new_name: str) -> str:
    """THE rename hook: queue ``/rename <new_name>`` for ``wid`` and try to deliver it now.

    Called by every path that renames a window (dashboard rename, reconcile duplicate
    suffix). A window chela did not launch with Remote Control is left alone
    (``"not-rc"``); a name equal to what was last pushed clears the queue (``"current"``).
    Otherwise the name is queued and :func:`deliver` attempts it — its outcome is returned.
    """
    pushed = _get_option(wid, PUSHED_OPTION)
    if not pushed:
        return "not-rc"
    if not _SAFE_NAME_RE.match(new_name or ""):
        log.warning("rc_rename: not pushing unsafe name %r to %s", new_name, wid)
        return "unsafe"
    _set_option(_target(wid), PENDING_OPTION, new_name)
    log.info("rc_rename: queued /rename %s for %s (was %s)", new_name, wid, pushed)
    return deliver(wid, new_name, pushed)


def _status(wid: str) -> str | None:
    from chela import agent_manager
    entry = agent_manager.session_entry(agent_manager.claude_pid(wid),
                                        agent_manager.session_status_map())
    return entry["status"] if entry else None


def prompt_is_empty(ansi_pane: str) -> bool:
    """True when an ``-e`` (SGR-carrying) capture shows an EMPTY ``❯`` input line.

    A ghost suggestion (faint, SGR 2) counts as empty — it is only ever drawn into an empty
    prompt; a typed draft does not. No visible prompt at all is NOT empty (fail closed).
    """
    from chela import dispatcher
    return dispatcher._pane_idle_empty_prompt(dispatcher._drop_ghost_suggestion(ansi_pane))


def deliver(wid: str, name: str, pushed: str | None = None) -> str:
    """Type ``/rename <name>`` into ``wid`` iff it is idle with an empty prompt.

    Returns ``"sent"``, ``"current"`` (already the pushed name — nothing typed),
    ``"busy"`` (status not ``idle``), ``"draft"`` (prompt not empty), or ``"failed"``
    (the send itself failed — logged; stays queued). Anything but ``sent``/``current``
    leaves :data:`PENDING_OPTION` set for the next :func:`flush_pending`.
    """
    from chela import messenger

    if pushed is None:
        pushed = _get_option(wid, PUSHED_OPTION)
    if name == pushed:
        _set_option(_target(wid), PENDING_OPTION, None)
        return "current"
    status = _status(wid)
    if status != "idle":
        log.debug("rc_rename: %s is %s — /rename %s stays queued", wid, status, name)
        return "busy"
    if not prompt_is_empty(messenger.capture_pane(wid, ansi=True)):
        log.debug("rc_rename: %s has a draft in its prompt — /rename %s stays queued",
                  wid, name)
        return "draft"
    if not messenger.send_tmux(wid, f"/rename {name}", interrupt=False):
        log.warning("rc_rename: /rename %s to %s FAILED to send — stays queued", name, wid)
        return "failed"
    _set_option(_target(wid), PUSHED_OPTION, name)
    _set_option(_target(wid), PENDING_OPTION, None)
    log.info("rc_rename: pushed /rename %s to %s (was %s)", name, wid, pushed)
    return "sent"


def flush_pending() -> dict[str, str]:
    """Retry every queued rename once — the daemon's per-tick call. ``{wid: outcome}``."""
    try:
        out = subprocess.run(
            ["tmux", "list-windows", "-t", config.current_session(), "-F",
             f"#{{window_id}}\t#{{{PENDING_OPTION}}}\t#{{{PUSHED_OPTION}}}"],
            capture_output=True, text=True, timeout=5,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as e:
        log.warning("rc_rename: list-windows failed: %s", e)
        return {}
    if out.returncode != 0:
        return {}
    results: dict[str, str] = {}
    for line in out.stdout.splitlines():
        parts = line.split("\t")
        if len(parts) != 3 or not parts[1]:
            continue
        wid, pending, pushed = parts
        results[wid] = deliver(wid, pending, pushed)
    return results
