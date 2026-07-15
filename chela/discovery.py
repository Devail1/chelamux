"""Window discovery — tmux is the single source of truth.

Each agent runs in a tmux window of the configured session; the window name is
the agent's display name. No external state file, no daemon to coordinate with —
tmux never lies about what is live right now.
"""
from __future__ import annotations
import logging
import subprocess

from chela import config
from chela.config import IGNORE_WINDOWS

log = logging.getLogger(__name__)

# Name of the window ensure_session() creates to anchor a freshly created session
# (tmux has no windowless session). Matches the wall's shell-N scheme so it reads
# as an ordinary tile rather than a stray artefact.
ANCHOR_WINDOW = "shell-1"


def _get_live_windows() -> dict[str, str]:
    """Live windows of the chela session as ``{window_id: window_name}``."""
    session = config.current_session()
    try:
        result = subprocess.run(
            ["tmux", "list-windows", "-t", session,
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
        r = subprocess.run(["tmux", "has-session", "-t", session],
                           capture_output=True, text=True, timeout=5)
        return r.returncode == 0
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return False


def ensure_session(session: str | None = None) -> bool:
    """The session exists — creating it if it doesn't. True unless tmux is unreachable.

    A missing session is an expected BOOT-ORDERING condition, not an error: nothing
    outside chela recreates it, so after a reboot (or `wsl --shutdown`, which takes
    the whole tmux server with it) the first chela process to come up finds nothing
    there. It is chela's OWN session, so the right move is to create it rather than
    fail — callers that treated "no session" as fatal turned a recoverable boot race
    into an outage (see the crash-loop note in scripts/agent-terminals.sh).

    CREATE-ONLY, NEVER CLOBBER — the session holds the user's live agent windows, so
    destroying it must be impossible, not merely unlikely. ``-A`` (attach-or-create)
    is the safe primitive: on a session that already exists it is a genuine no-op
    (same session id, same windows), where a plain ``new-session -s`` would fail with
    "duplicate session". So even if the ``session_exists`` gate false-negatives — a
    transient client error against a busy server — the worst case is a no-op. The gate
    is the optimisation; ``-A`` is the guarantee. We touch nothing that already exists.

    That also makes it race-safe: two starters may create concurrently and the loser's
    call is simply absorbed. Note ``-A -d`` exits NONZERO with no tty ("open terminal
    failed") even on success, so the exit code is ignored — ``has-session`` alone decides.

    A session must own at least one window, so the anchor window is named to match the
    wall's own scheme (``shell-1``, cf. :func:`chela.spawn.next_shell_name`); passing ``-n``
    is itself what pins automatic-rename off, so no follow-up option write is needed.
    """
    session = session or config.current_session()
    if session_exists(session):
        return True
    try:
        subprocess.run(
            ["tmux", "new-session", "-A", "-d", "-s", session, "-n", ANCHOR_WINDOW],
            capture_output=True, text=True, timeout=10,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError):
        log.warning("tmux unreachable; could not create session '%s'", session)
        return False
    if session_exists(session):
        log.info("Created tmux session '%s'", session)
        return True
    return False


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
    """Working directory of an agent's window, read straight from its tmux pane."""
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
