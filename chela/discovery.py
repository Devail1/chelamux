"""Window discovery — tmux is the single source of truth.

Each agent runs in a tmux window of the configured session; the window name is
the agent's display name. No external state file, no daemon to coordinate with —
tmux never lies about what is live right now.
"""
from __future__ import annotations
import logging
import subprocess
from collections.abc import Sequence

from chela import config, envutil, probecache
from chela.config import IGNORE_WINDOWS

log = logging.getLogger(__name__)

def _get_live_windows() -> dict[str, str]:
    """Live windows of the chela session as ``{window_id: window_name}``."""
    session = config.current_session()
    try:
        result = subprocess.run(
            ["tmux", "list-windows", "-t", config.session_target(session),
             "-F", "#{window_id}\t#{window_name}"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0:
            out: dict[str, str] = {}
            for line in result.stdout.splitlines():
                wid, _, name = line.partition("\t")
                wid, name = wid.strip(), name.strip()
                if wid and name and name not in IGNORE_WINDOWS:
                    out[wid] = name
            return out
    except (subprocess.TimeoutExpired, FileNotFoundError):
        log.warning("Failed to query tmux for live windows (session=%s)", session)
    return {}


def get_all_windows() -> dict[str, str]:
    """``{display_name: window_id}`` for every live window in the session."""
    return {name: wid for wid, name in _get_live_windows().items()}


def get_windows_by_id() -> dict[str, str]:
    """``{window_id: display_name}`` for every live window in the session.

    The window-id-keyed view. Prefer this over ``get_all_windows()`` whenever a
    window id is the identity you have: window *names* collide (two shells, two
    repos with the same basename), but ids never do — so a name→id lookup can
    resolve to the wrong window while an id→name lookup is unambiguous.
    """
    return _get_live_windows()


def get_window_id(agent_name: str) -> str | None:
    """tmux window id for an agent, by display name (its window name)."""
    return get_all_windows().get(agent_name)


def session_exists(session: str | None = None) -> bool:
    """True if the chela tmux session is live right now."""
    session = session or config.current_session()
    try:
        r = subprocess.run(["tmux", "has-session", "-t", config.session_target(session)],
                           capture_output=True, text=True, timeout=5)
        return r.returncode == 0
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return False


def list_sessions() -> list[str] | None:
    """Every tmux session name on the server, or ``None`` when tmux cannot be asked (no
    binary, no server, a timeout) — unknown, never an empty fleet."""
    try:
        r = subprocess.run(["tmux", "list-sessions", "-F", "#{session_name}"],
                           capture_output=True, text=True, timeout=5)
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return None
    if r.returncode != 0:
        return None
    return [line.strip() for line in r.stdout.splitlines() if line.strip()]


def open_window(name: str, cwd: str, command: Sequence[str] = (), *,
                session: str | None = None, timeout: float = 10,
                check: bool = False) -> subprocess.CompletedProcess:
    """Open window ``name`` in ``cwd`` in the chela session; prints its ``@id`` on stdout.

    CMX-473 — there is NO anchor window. When the session exists this is a plain
    ``new-window``. When it does not (a fresh boot, a ``wsl --shutdown``, or the user
    closed its last window, which ends a tmux session), the session is created WITH this
    window as its first one — ``new-session -d -s <session> -n <name> -c <cwd>`` — instead
    of an empty placeholder shell that then shows up as a session of its own on the
    sidebar, the Wall and Telegram. So "no session" just means "no windows": every
    reader treats it that way, and only a spawn (this function) ever creates the session.

    CREATE-ONLY, NEVER CLOBBER: ``new-session`` without ``-A`` on a session that already
    exists fails with "duplicate session" and touches nothing. That is how a lost race
    (two spawners both saw it missing) resolves: the loser re-checks, finds the session,
    and opens its window with ``new-window`` like any other spawn.

    🧯 CMX-21 — creating the session can START the tmux server, whose global env every
    later window inherits, so that call runs with :func:`chela.envutil.server_env` and the
    server's table is scrubbed once it exists. Any open is REFUSED (returncode 1, no tmux
    call at all) when ``TMUX_TMPDIR`` names a directory that no longer exists: tmux would
    silently fall back to the default socket, i.e. the live server. ``new-window`` gets
    :func:`chela.envutil.child_env`, as before.

    ``command`` (argv, optional) is the window's command, run by tmux with no shell in
    between; empty means the default shell. ``check=True`` raises
    :class:`subprocess.CalledProcessError` on failure, like :func:`subprocess.run`.
    """
    session = session or config.current_session()
    tail = ["-n", name, "-c", cwd, "-P", "-F", "#{window_id}"]
    if command:
        tail += ["--", *command]
    missing = envutil.tmux_tmpdir_missing()
    if missing:
        log.error("REFUSING to open window %s in tmux session '%s': TMUX_TMPDIR=%s does not "
                  "exist, and tmux would silently fall back to the DEFAULT socket (the live "
                  "server). Unset TMUX_TMPDIR or recreate the dir.", name, session, missing)
        proc = subprocess.CompletedProcess(
            ["tmux"], 1, "", f"refusing to use tmux: TMUX_TMPDIR={missing} does not exist")
    else:
        proc: subprocess.CompletedProcess | None = None
        if not session_exists(session):
            proc = subprocess.run(
                ["tmux", "new-session", "-d", "-s", session, *tail],
                capture_output=True, text=True, timeout=timeout,
                env=envutil.server_env(),
            )
            if proc.returncode == 0:
                log.info("Created tmux session '%s' with window %s", session, name)
                envutil.scrub_tmux_secrets()   # CMX-21: and TMUX_TMPDIR, which server_env kept
            elif session_exists(session):
                proc = None    # lost the create race: the session is there now
    if proc is None:
        proc = subprocess.run(
            ["tmux", "new-window", "-t", config.window_target("", session), *tail],
            capture_output=True, text=True, timeout=timeout, env=envutil.child_env(),
        )
    if check and proc.returncode != 0:
        raise subprocess.CalledProcessError(proc.returncode, proc.args, proc.stdout, proc.stderr)
    return proc


def get_window_cwd_by_id(window_id: str) -> str | None:
    """Working directory of a window, read straight from its tmux pane by id.

    Collision-free counterpart to ``get_window_cwd`` (which resolves by name):
    with a window id in hand there is no name→id ambiguity to trip over.
    """
    if not window_id:
        return None
    try:
        result = subprocess.run(
            ["tmux", "display-message", "-p", "-t", window_id, "#{pane_current_path}"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0:
            return result.stdout.strip() or None
    except (subprocess.TimeoutExpired, FileNotFoundError):
        log.warning("Failed to read pane cwd for window %s", window_id)
    return None


def get_window_cwd(agent_name: str) -> str | None:
    """Working directory of an agent's window, read straight from its tmux pane.

    Inside a :func:`chela.probecache.batch` the name resolves through the window list the
    batch was opened with and the path comes from the shared pane snapshot (the same
    ``#{pane_current_path}``, listed once) instead of two tmux calls per agent (CMX-17).
    """
    batch = probecache.active()
    if batch is not None:
        wid = (batch.windows.get(agent_name) if batch.windows is not None
               else get_window_id(agent_name))
        if not wid:
            return None
        pane = batch.pane(wid)
        return (pane.path or None) if pane is not None else None
    wid = get_window_id(agent_name)
    if not wid:
        return None
    try:
        result = subprocess.run(
            ["tmux", "display-message", "-p", "-t", wid, "#{pane_current_path}"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0:
            return result.stdout.strip() or None
    except (subprocess.TimeoutExpired, FileNotFoundError):
        log.warning("Failed to read pane cwd for %s", agent_name)
    return None
