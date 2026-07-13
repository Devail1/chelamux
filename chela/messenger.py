"""Route messages between agents over tmux.

``send_tmux`` is the low-level primitive: given a tmux window id and text, type
it into that window's Claude Code prompt and press Enter. On top of it,
``send_message``/``broadcast`` resolve an agent reference to a live window with
:func:`resolve_window` and deliver there. Delivery is live-only: if the agent
has no live window the message is not delivered (there is no persistent queue).
"""
from __future__ import annotations
import logging
import subprocess
import time

from chela import config
from chela.discovery import get_windows_by_id

log = logging.getLogger(__name__)

_PROMPT_CHAR = "❯"  # marks Claude Code's input line
_PASTE_PLACEHOLDER = "Pasted text"  # e.g. "[Pasted text #1 +5 lines]"


def _capture_pane(target: str) -> str:
    """Return the visible text of a tmux pane (empty string on error)."""
    out = subprocess.run(
        ["tmux", "capture-pane", "-p", "-t", target],
        capture_output=True, text=True,
    )
    return out.stdout if out.returncode == 0 else ""


def _pane_has_unsubmitted_paste(pane: str) -> bool:
    """True when a collapsed paste placeholder still sits on the prompt line.

    Claude Code turns a pasted multi-line block into a
    ``[Pasted text #N +K lines]`` chip; the first Enter expands/acknowledges
    it rather than submitting, so the chip lingers on the ``❯`` input line and
    a second Enter is needed. Gating on the placeholder *after* the prompt glyph
    (not a bare empty prompt) is what keeps this from ever re-submitting an
    already-empty prompt.
    """
    for line in pane.splitlines():
        if _PROMPT_CHAR in line and _PASTE_PLACEHOLDER in line.split(_PROMPT_CHAR, 1)[1]:
            return True
    return False


def send_tmux(window_id: str, text: str) -> bool:
    """Send text to a tmux window. Returns True on success.

    Uses load-buffer + paste-buffer for multi-line text to avoid newlines being
    interpreted as premature Enter presses. Single-line text is sent literally
    (``-l``) with the Enter as a SEPARATE call after a short gap, so a long blob
    isn't read as paste input and the trailing Enter isn't absorbed as a newline
    (which strands the message on the ``❯`` input line unsubmitted).
    """
    target = f"{config.current_session()}:{window_id}"
    try:
        if text.startswith("/"):
            # Slash commands: send Escape first to interrupt any in-progress
            # response and return Claude Code to the prompt.
            subprocess.run(
                ["tmux", "send-keys", "-t", target, "Escape"],
                check=True, capture_output=True,
            )
            time.sleep(1.0)
        if "\n" in text:
            # Multi-line: use load-buffer + paste-buffer to avoid
            # newlines acting as Enter presses mid-message
            subprocess.run(
                ["tmux", "load-buffer", "-"],
                input=text.encode(), check=True, capture_output=True,
            )
            subprocess.run(
                ["tmux", "paste-buffer", "-t", target],
                check=True, capture_output=True,
            )
            time.sleep(0.5)
            subprocess.run(
                ["tmux", "send-keys", "-t", target, "Enter"],
                check=True, capture_output=True,
            )
            # A pasted block collapses into a "[Pasted text #N +K lines]" chip
            # that the first Enter only acknowledges; it strands on the prompt
            # until a second Enter submits it. Re-capture and, ONLY if the chip
            # is still on the input line, press Enter again. The placeholder
            # guard means a prompt that already submitted (now empty) is never
            # re-submitted, so this can't fire a stray empty prompt.
            time.sleep(0.3)
            if _pane_has_unsubmitted_paste(_capture_pane(target)):
                subprocess.run(
                    ["tmux", "send-keys", "-t", target, "Enter"],
                    check=True, capture_output=True,
                )
        else:
            # Single-line: send the text and Enter as SEPARATE calls with a
            # ~0.5s gap. A long blob injected in one shot reads to Claude Code's
            # TUI as fast/paste input and the immediately-trailing Enter is
            # absorbed as a newline, so the message strands wrapped on the ❯
            # input line instead of submitting. The gap lets the TUI settle the
            # text before the Enter lands — mirroring the multi-line paste path
            # above. ``-l`` sends the text literally, so a message that happens
            # to contain a tmux key name (e.g. "Up", "Enter", "C-c") is typed
            # verbatim rather than interpreted as a keypress.
            subprocess.run(
                ["tmux", "send-keys", "-t", target, "-l", text],
                check=True, capture_output=True,
            )
            time.sleep(0.5)
            subprocess.run(
                ["tmux", "send-keys", "-t", target, "Enter"],
                check=True, capture_output=True,
            )
        return True
    except subprocess.CalledProcessError as e:
        log.error("tmux send-keys failed for %s: %s", window_id, e.stderr.decode())
        return False


def capture_pane(window_id: str, *, ansi: bool = False) -> str:
    """Return the visible text of ``window_id``'s tmux pane (empty on error).

    The public wrapper the Telegram bridge's ``/screenshot`` command uses to
    snapshot an agent's terminal; resolves the window against the current
    session the same way :func:`send_tmux` does. ``ansi=True`` adds tmux's
    ``-e`` so SGR colour escapes ride along in the capture (what the PNG
    renderer parses); the default (no ``-e``) returns plain text. Read-only —
    ``capture-pane`` never mutates the pane.
    """
    target = f"{config.current_session()}:{window_id}"
    cmd = ["tmux", "capture-pane", "-p", "-t", target]
    if ansi:
        cmd.insert(2, "-e")  # -> tmux capture-pane -e -p -t <target>
    out = subprocess.run(cmd, capture_output=True, text=True)
    return out.stdout if out.returncode == 0 else ""


def send_key(window_id: str, key: str) -> bool:
    """Send a single tmux key press (``Up``, ``C-c``, ``Space``…) to ``window_id``.

    The generic primitive behind the Telegram bridge's ``/screenshot`` control-key
    keyboard: each tapped button maps to a tmux ``send-keys`` key *name* delivered
    here, so an operator can drive the bound terminal from their phone. ``key`` is
    a tmux key name — no Enter is appended (``Enter`` is itself a valid key name,
    so the caller asks for it explicitly). Read-only otherwise; returns True on
    success, False if tmux errors.
    """
    target = f"{config.current_session()}:{window_id}"
    try:
        subprocess.run(
            ["tmux", "send-keys", "-t", target, key],
            check=True, capture_output=True,
        )
        return True
    except subprocess.CalledProcessError as e:
        log.error("tmux send-keys %s failed for %s: %s", key, window_id, e.stderr.decode())
        return False


def send_escape(window_id: str) -> bool:
    """Send a single Escape keypress to ``window_id`` (no Enter). True on success.

    Mirrors the Escape :func:`send_tmux` fires before a slash command, exposed
    on its own for the bridge's ``/esc`` command: it interrupts an in-progress
    Claude Code response and returns the window to its prompt without submitting
    anything. A thin alias for :func:`send_key` with the ``Escape`` key name.
    """
    return send_key(window_id, "Escape")


def resolve_window(agent: str | None) -> str | None:
    """Resolve an agent reference to its live tmux window id — id OR display name.

    THE one liveness authority for the message path, and deliberately the same
    one ``/api/agents`` walks: the live tmux window table (``discovery``). tmux
    never lies about what is running right now, so "not in this table" is the
    only thing that legitimately means offline.

    Accepts what every other chela surface accepts — a window id (``@32``, or a
    bare ``32``) or a window name. That breadth IS the fix: this used to be a
    name-only lookup, so ``chela msg @32`` (a window *id*, which is what the
    wall, ``/api/agents``, ``chela peek`` and ``chela drive`` all show you) found
    no window *named* ``@32`` and reported a live, busy agent as "offline" while
    dropping the message. Returns None only when the window is genuinely gone.

    Note this is liveness, not busy/idle: a *busy* agent is a perfectly valid
    recipient (Claude Code queues the paste and picks it up), so nothing here
    consults ``session_status_map``/``claude_pid``. Gating delivery on "idle"
    is what would silently drop a message to a working agent.
    """
    agent = (agent or "").strip()
    if not agent:
        return None
    windows = get_windows_by_id()  # {window_id: name} — ids never collide, names can
    if agent in windows:
        return agent
    if agent.isdigit() and f"@{agent}" in windows:
        return f"@{agent}"
    for wid, name in windows.items():
        if name == agent:
            return wid
    return None


def send_message(from_agent: str, to_agent: str, message: str, priority: str = "normal") -> bool:
    """Send a message to an agent via tmux. Live-only — no fallback queue.

    ``to_agent`` is anything :func:`resolve_window` accepts (window id or name).
    Returns True if delivered to a live window, False if the window is genuinely
    not live or the tmux send failed. Callers that must not lose a message should
    resolve first and report the two cases apart (``chela msg`` does).
    """
    window_id = resolve_window(to_agent)
    if window_id is None:
        log.warning("%s is not a live tmux window — message NOT delivered", to_agent)
        return False
    # Prefix with the sender so the recipient has context on who pinged them.
    if send_tmux(window_id, f"[{from_agent}] {message}"):
        return True
    log.warning("tmux send to %s (%s) failed — message NOT delivered", to_agent, window_id)
    return False


def broadcast(from_agent: str, message: str, priority: str = "normal") -> dict[str, bool]:
    """Send a message to every other live agent. Returns {agent: delivered?}.

    Iterates the window-id-keyed table so two windows sharing a display name both
    get the message, and skips the sender's OWN window — an orchestrator that
    broadcasts into its own pane feeds its message back to itself, which is a
    loop. The sender is matched by window id when it names one, else by name.
    """
    sender_wid = resolve_window(from_agent)
    results: dict[str, bool] = {}
    for wid, name in sorted(get_windows_by_id().items()):
        if wid == sender_wid or name == from_agent:
            continue
        # Name-keyed for the caller's benefit, but disambiguated on collision so a
        # duplicate name can't make one delivery's result overwrite another's.
        key = name if name not in results else f"{name} ({wid})"
        results[key] = send_message(from_agent, wid, message, priority)
    return results
