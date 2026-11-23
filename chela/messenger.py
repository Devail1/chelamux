"""Route messages between agents over tmux — or, for agent-to-agent messages,
Claude Code's own peer-messaging Unix socket.

``send_tmux`` is the low-level primitive: given a tmux window id and text, type
it into that window's Claude Code prompt and press Enter. It is the transport for
the USER's input (the Telegram bridge, dashboard typing, the user's schedules) and
for gated slash commands — never for a message from one agent (or chela) to another.

``send_message``/``broadcast``, ``rooms.py``'s targeted dispatch, the decisions
inbox, ``chela drive`` and the fleet pause are agent messages: each resolves a live
window, then sends over :func:`send_peer` — a direct write to the target session's
own Unix socket (Claude Code 2.1.224+ binds one per session and ingests newline-
delimited JSON on it) — through :func:`deliver_peer`. ⛔ **There is no tmux
fallback (CMX-482).** A paste arrives as a bare user turn with no ``from``, so the
receiver shows no cross-session banner and another agent's text reads as the
user's; it also depends on the pane's terminal input MODE (CMX-79 — text typed
while a pane is mid `!`-bash-command would execute). When the socket cannot be
reached (an older Claude Code build, a session that hasn't bound it yet, a stale
socket file) the result is :data:`UNREACHABLE`: the caller holds the message where
a queue exists, logs it, and a CLI exits non-zero. ``chela doctor`` names the
windows in that state (``runtime_truth``, :func:`peer_transport_kind`).

⛔ **A socket accepting the bytes is a HANDOFF, not a delivery.** :func:`send_peer`
briefly listens on its own reply socket for a receipt; a receiver whose
``crossSessionInbound`` is ``hold``/``refuse`` drops the message and echoes
``held``/``denied``/``expired`` — and an ACCEPTED message produces no receipt at
all, ever (measured). So silence within the wait window is the success signal,
and callers (:func:`send_message`) must treat an adverse receipt as failure, never
as delivered — that inversion, missed, is exactly what made CMX-222's first cut
report success for a message a receiver had actually dropped.

Windows launched with ``--messaging-socket-path`` (:func:`messaging_socket_launch_arg`
— wired into ``dispatcher.py``/``personas/autolaunch.py``) get a chela-owned,
deterministic per-window socket path (:func:`deterministic_peer_socket_path`); a
window launched before that flag existed is still reachable through
:func:`_peer_socket_path`'s legacy pid-derived guess, kept as a fallback.
"""
from __future__ import annotations
import json
import logging
import os
import re
import socket
import subprocess
import time
import uuid
from pathlib import Path
from typing import NamedTuple

from chela import config, event_log
from chela.discovery import get_windows_by_id

log = logging.getLogger(__name__)

# How long send_peer waits to connect/write before giving up (the message is then
# UNREACHABLE and held). A dead or backed-up peer must not stall message delivery.
_PEER_CONNECT_TIMEOUT = 2.0

# How long send_peer listens on its own reply socket for a receipt before concluding
# none is coming. Measured (CMX-222/223): a `held` receipt lands in ~20ms; an ACCEPTED
# message produces no receipt at all, ever. This only has to be bigger than the former —
# it is not a substitute for the eventual receipt, which is read the instant it arrives.
_RECEIPT_WAIT_SECONDS = 0.25

# A receipt naming one of these means the receiver's own gate (`crossSessionInbound`)
# dropped the message even though the socket accepted the bytes — a handoff, not a
# delivery. `sent` (no adverse receipt inside the wait window) and `delivered` are the
# only statuses that count as success; see the module docstring's fail-open note.
ADVERSE_RECEIPT_STATUSES = ("held", "denied", "expired")

# AF_UNIX's sockaddr_un.sun_path ceiling. A chela-owned socket path deeper than this
# can't be bound at all — checked before it is ever handed to `claude` as a launch flag.
_SUN_PATH_MAX = 104

# How long a reachability PROBE (peer_socket_reachable) waits to connect before deciding
# nothing is listening. Deliberately short and separate from _PEER_CONNECT_TIMEOUT: a probe
# runs from `chela doctor` over the whole fleet and must fail fast on a dead socket, not
# stall for 2s per window. Matches Claude Code's own bundle, which classifies a socket
# live/dead the same way with the same 250ms timeout.
_PROBE_TIMEOUT = 0.25

# CMX-380: what makes a peer message arrive NAMED. Measured against Claude Code 2.1.283
# (a throwaway receiver, one send per candidate frame): a native `SendMessage` does NOT
# name itself with a top-level wire field — it pre-wraps its text in the receiver's own
# `<cross-session-message from=… from-name=… from-mode=…>` envelope inside
# `message.content`, and the receiver lifts `from-name` out of that envelope into
# `origin.name` (the `@ <name>` header). Top-level `from_name`/`fromName`/`name` were
# each ignored (still the anonymous "Another Claude session sent a message:" render).
# The envelope changes the RENDER only: the arrival keeps `origin.kind: "peer"`, the
# socket-verified pid, `isMeta`, and the full anti-laundering paragraph in the model's
# context — so naming a message never makes it look like the user.
_PEER_ENVELOPE_TAG = "cross-session-message"

# The ONE name chela signs every peer message with — daemon/inbox notices, room relays,
# `chela msg`, the dashboard alike. ⛔ `from-name` is SENDER-ASSERTED: the receiver never
# checks it against the socket-verified pid (measured — a bare python3 sender was shown as
# `@chela`), so it is only honest when it names the process that really sent it, and that
# is chela's own process in every case. Naming a relay after the ORIGINATING agent's window
# would put another session's name on a message that session never sent; the originator
# stays attributed in the text itself (`send_message`'s `[from]` prefix, rooms' prompt).
PEER_SENDER_NAME = "chela"

# ⛔ No `from-mode` attribute is sent, on purpose. Native senders add one, but it is NOT a
# label: the receiver's inbound gate reads it (a bypass-class receiver accepts a sender
# asserting "bypass"). Read from the bundle, "prompting" gives the same gate outcome as
# asserting nothing, but that was never MEASURED against a bypass-mode receiver, and a
# field a safety gate consumes is not shipped on a reading. The name alone is what renders
# the arrival named (measured), so the gate sees exactly what it saw before CMX-380.

# The receiver's own `from-name` value class is `[^"<>\n\r]+`, capped at 64 chars.
_PEER_NAME_MAX = 64
_PEER_NAME_QUOTES = re.compile(r'["<>]')
_PEER_NAME_CONTROL = re.compile(r"[\x00-\x1f\x7f-\x9f\u200b-\u200f\u2028-\u202e\u2060-\u2064\ufeff]")
# The envelope's `from` attribute value class: anything else is %-encoded, the same way the
# receiver's own sender encodes a `uds:` address.
_PEER_ADDR_UNSAFE = re.compile(r"[^A-Za-z0-9:_/.\\-]")
# A body that closes the envelope early would end it there; neutralise any
# `</cross-session-message` the way the receiver's own sender does (`<` -> `<\`).
_PEER_BODY_CLOSE = re.compile(r"<(?!\\)(?=\s*/\s*cross[-_]session[-_]message)", re.IGNORECASE)


class PeerSendResult(NamedTuple):
    """The outcome of one :func:`send_peer` call.

    ``handed_off`` is True the moment the bytes reach the target's socket — that is
    NOT the same as delivered (see ``status``). ``status`` is None only when
    ``handed_off`` is False (no socket could be reached at all); otherwise it is
    ``"sent"`` (no adverse receipt arrived inside the wait window — the accept path's
    signature, since an accepted message produces no receipt at all), ``"delivered"``
    (an explicit positive receipt), or one of :data:`ADVERSE_RECEIPT_STATUSES`.
    """
    handed_off: bool
    status: str | None

_PROMPT_CHAR = "❯"  # marks Claude Code's input line
_PASTE_PLACEHOLDER = "Pasted text"  # e.g. "[Pasted text #1 +5 lines]"

# The input box's left border — what tells an input LINE apart from anything else on screen.
# The footer hint below the box literally reads "! for bash mode"; a detector that scanned
# raw lines would read that as bash mode and refuse every pane forever.
_BOX_EDGE = "│"
# The first glyph of the input line IS the input mode. `❯` is the prose prompt; `!` and `#`
# are Claude Code's bash-input and memory modes, in which what we type is NOT prose — in
# `!` mode it is a shell command, run by that session's own unsandboxed shell.
_MODE_GLYPHS = {"❯": "prompt", "!": "bash", "#": "memory"}
# Modes we refuse to type into. `bash`: our text would be EXECUTED (CMX-79 — observed live).
# `memory`: our text would be WRITTEN INTO that agent's CLAUDE.md — persistent instructions,
# not a notification.
UNSAFE_INPUT_MODES = ("bash", "memory")
# The input box is at the bottom of the pane. Bounding the scan keeps a `!` further up the
# scrollback (an agent quoting a shell command) from being read as the mode.
_INPUT_SCAN_LINES = 15


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


def pane_input_mode(pane: str) -> str:
    """What the pane's input line will DO with what we type: prompt/bash/memory/unknown.

    The status authority (``claude agents --json``) reports busy/idle/waiting — it models
    whether a session is THINKING. It says nothing about what MODE its prompt is in, and the
    two are independent: an ``idle`` pane sitting in ``!`` bash-input mode will happily run
    the next line it receives as a shell command. That is CMX-79, and it is why ``idle`` was
    never the same thing as "the prompt will treat this as prose".

    Read off the TUI, because the TUI is the only place the mode exists: the input box's
    first glyph is the mode (``❯`` prose, ``!`` bash, ``#`` memory). Scanned bottom-up over
    the box's own lines (``│``) so the footer hint "! for bash mode" — which is not in the
    box — can never be mistaken for the mode itself.

    ``unknown`` means we could not read it (capture failed, TUI redesign, a pane that isn't
    Claude Code at all). Callers must NOT treat that as unsafe: refusing on unknown would let
    one tmux hiccup silently wedge every notification. That fail-open is precisely why the
    TEXT is neutralised too (:func:`chela.tui_text.sanitize_prompt`) — an undetected mode has
    to be survivable, not merely unlikely.
    """
    for line in reversed((pane or "").splitlines()[-_INPUT_SCAN_LINES:]):
        if _BOX_EDGE not in line:
            continue                    # not an input-box line (footer, body, box border)
        inner = line.split(_BOX_EDGE, 1)[1].strip()
        if inner and inner[0] in _MODE_GLYPHS:
            return _MODE_GLYPHS[inner[0]]
    return "unknown"


def refuses_paste(window_id: str) -> str | None:
    """The input mode that makes ``window_id`` unsafe to type into — None if it is fine.

    One authority, every sender. The decisions inbox is where this was observed, but it is
    not the only thing that types into somebody's prompt (rooms, the CI verdict, ``chela
    msg``, the Telegram bridge) — and in bash-input mode every one of them would have been
    executed just the same.
    """
    mode = pane_input_mode(_capture_pane(config.window_target(window_id)))
    return mode if mode in UNSAFE_INPUT_MODES else None


def send_tmux(window_id: str, text: str, *, interrupt: bool = True) -> bool:
    """Send text to a tmux window. Returns True on success.

    Uses load-buffer + ``paste-buffer -p`` (bracketed paste) for multi-line text so
    Claude Code's TUI reads the whole blob as one paste instead of a run of Enter
    presses — plain ``paste-buffer`` replaces each embedded newline with a CR, which
    reads as the user hitting Enter mid-message (CMX-375: split a short message into
    two prompts, and stranded a longer one unsent on the ❯ line 7/12 times in
    production). Single-line text is sent literally (``-l``) with the Enter as a
    SEPARATE call after a short gap, so a long blob isn't read as paste input and the
    trailing Enter isn't absorbed as a newline (which strands the message on the ``❯``
    input line unsubmitted).

    Refuses a window whose prompt is in an unsafe INPUT MODE (:func:`refuses_paste`): in
    ``!`` bash mode our text is not a message, it is a command that session's shell runs.
    Refusing by returning False — rather than sending — is what lets a durable sender HOLD
    its item: the decisions inbox leaves the event queued and re-tries on a later tick, so
    nothing is lost by waiting for the pane to be prose again.

    ``interrupt=False`` skips the Escape a slash command is normally preceded by — for a
    caller that already verified the session idle and must never cancel a turn
    (:mod:`chela.rc_rename`).
    """
    target = config.window_target(window_id)
    unsafe = refuses_paste(window_id)
    if unsafe:
        log.warning("refusing to type into %s: its prompt is in %s-input mode — the text "
                    "would be %s, not read", window_id, unsafe,
                    "EXECUTED as a shell command" if unsafe == "bash" else "stored as memory")
        return False
    try:
        if interrupt and text.startswith("/"):
            # Slash commands: send Escape first to interrupt any in-progress
            # response and return Claude Code to the prompt.
            subprocess.run(
                ["tmux", "send-keys", "-t", target, "Escape"],
                check=True, capture_output=True,
            )
            time.sleep(1.0)
        if "\n" in text:
            # Multi-line: use load-buffer + paste-buffer -p (bracketed paste) to
            # avoid newlines acting as Enter presses mid-message. Measured
            # (TODO.md CMX-375): plain `paste-buffer` (no `-p`) replaces each
            # embedded LF with a CR — indistinguishable from the user pressing
            # Enter mid-message — so a 2-line message split into two prompts, and
            # longer ones stranded unsent on the ❯ line 7/12 times in 9 days of
            # production traffic. `-p` wraps the buffer in bracketed-paste control
            # codes instead, which Claude Code's TUI reads as one paste (4/4
            # submitted in reproduction, vs 12/12 stranded without it).
            subprocess.run(
                ["tmux", "load-buffer", "-"],
                input=text.encode(), check=True, capture_output=True,
            )
            subprocess.run(
                ["tmux", "paste-buffer", "-p", "-t", target],
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


def resend_enter(window_id: str) -> bool:
    """Re-send Enter ONLY — for a seed whose paste landed but whose separately-sent
    Enter was swallowed by a late TUI redraw (a startup notice, an MCP handshake,
    generically any splash that redraws after the paste). Returns True on success.

    Deliberately not a re-:func:`send_tmux`: the prompt text is already sitting,
    unsubmitted, on the ``❯`` input line, so re-running the full send would type it
    a SECOND time on top of itself rather than just submitting what is already
    there. Guarded by the same :func:`refuses_paste` check as ``send_tmux`` — the
    pane may have flipped to bash-input mode since the original paste, and an
    Enter sent into that mode would execute whatever now sits on that line.
    """
    target = config.window_target(window_id)
    unsafe = refuses_paste(window_id)
    if unsafe:
        log.warning("refusing to send Enter into %s: its prompt is in %s-input mode",
                    window_id, unsafe)
        return False
    try:
        subprocess.run(
            ["tmux", "send-keys", "-t", target, "Enter"],
            check=True, capture_output=True,
        )
        return True
    except subprocess.CalledProcessError as e:
        log.error("tmux send-keys Enter failed for %s: %s", window_id, e.stderr.decode())
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
    target = config.window_target(window_id)
    cmd = ["tmux", "capture-pane", "-p", "-t", target]
    if ansi:
        cmd.insert(2, "-e")  # -> tmux capture-pane -e -p -t <target>
    out = subprocess.run(cmd, capture_output=True, text=True)
    return out.stdout if out.returncode == 0 else ""


_RE_PANE_SIZE = re.compile(r"(\d+)x(\d+)")


def pane_size(window_id: str) -> tuple[int, int] | None:
    """``(columns, rows)`` of ``window_id``'s tmux pane, or None when tmux can't say.

    The ``/screenshot`` renderer sizes its PNG to this grid (CMX-343): ``capture-pane -p``
    strips trailing blanks, so the capture alone can't tell a mostly-empty 120-column pane
    from a 2-column one. Read-only, like :func:`capture_pane`.
    """
    target = config.window_target(window_id)
    cmd = ["tmux", "display-message", "-p", "-t", target, "#{pane_width}x#{pane_height}"]
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return None
    if out.returncode != 0:
        return None
    m = _RE_PANE_SIZE.fullmatch(out.stdout.strip())
    if m is None:
        return None
    cols, rows = int(m.group(1)), int(m.group(2))
    return (cols, rows) if cols > 0 and rows > 0 else None


_RE_SGR = re.compile(r"\x1b\[([0-9;]*)m")
# What may precede the first visible glyph after `❯`: spaces (Claude Code renders a NO-BREAK
# SPACE, U+00A0, there) and SGR escapes.
_RE_INPUT_LEAD = re.compile(r"(?:\x1b\[[0-9;]*m|[ \xa0])*")
# Claude Code's placeholder while a turn runs with messages queued — the prompt is EMPTY.
_QUEUED_PLACEHOLDER = "Press up to edit queued messages"
# The chip Claude Code turns a pasted image path into on the input line.
_RE_IMAGE_CHIP = re.compile(r"\[Image #\d+\]")
# An image delivery re-reads its prompt this many times, this far apart, for the submit.
_SUBMIT_POLLS = 6
_SUBMIT_POLL_SECONDS = 0.5
# chela.hooks.event_type("UserPromptSubmit") — the log type of Claude Code's submit hook.
_PROMPT_SUBMIT_EVENT = "hook.user_prompt_submit"
# How much of a caption's first line a submitted prompt must carry to be ours.
_CARRIES_PREFIX = 60
# The line chela.telegram.media.receive_photo delivers ahead of a downloaded photo's path.
# Claude Code turns that path into an attachment, so the agent's transcript records the
# turn as ``"<caption>\n\n📎 image:"`` + an ``image`` block — the submit verify above and
# the outbound parser (never echo a photo back into its topic, CMX-36) both key on it.
INBOUND_IMAGE_MARKER = "📎 image:"


class PendingDraft(Exception):
    """The prompt already holds unsubmitted text — typing more would merge into it."""


def input_draft(pane: str) -> str | None:
    """The text sitting unsubmitted on a captured pane's ``❯`` input line.

    ``""`` when the line is empty, None when no input line is visible (capture failed, a
    pane that isn't Claude Code). ``pane`` must be an ``-e`` capture: Claude Code renders
    its ghost suggestion into an EMPTY prompt as faint (SGR 2) text right after ``❯``, and
    only the escapes tell it apart from a typed draft. The input line is the bottom-most
    ``❯`` line directly under the box's top rule — submitted turns echoed above it carry
    the glyph too, and so does a permission menu's selected option (``❯ 1. Yes``).
    """
    lines = (pane or "").splitlines()
    while lines and not _RE_SGR.sub("", lines[-1]).strip():
        lines.pop()  # an inline TUI leaves the rest of a tall pane blank below its box
    lines = lines[-_INPUT_SCAN_LINES:]
    for i in range(len(lines) - 1, 0, -1):
        line = lines[i]
        plain = _RE_SGR.sub("", line).replace(_BOX_EDGE, "").strip()
        above = _RE_SGR.sub("", lines[i - 1]).strip()
        if not plain.startswith(_PROMPT_CHAR) or not above.startswith(("─", "╭")):
            continue
        lead = _RE_INPUT_LEAD.match(line.split(_PROMPT_CHAR, 1)[1]).group(0)
        if any("2" in m.split(";") for m in _RE_SGR.findall(lead)):
            return ""  # the ghost suggestion is a placeholder: the prompt is empty
        draft = plain[len(_PROMPT_CHAR):].strip()
        return "" if draft == _QUEUED_PLACEHOLDER else draft
    return None


def _read_draft(window_id: str) -> str | None:
    try:
        return input_draft(capture_pane(window_id, ansi=True))
    except OSError:
        return None


def deliver_prompt(window_id: str, text: str) -> bool:
    """:func:`send_tmux`, raising :class:`PendingDraft` if the prompt already holds a draft.

    Text typed onto an unsubmitted prompt is appended to it and submitted as ONE turn
    (CMX-117: a stranded photo delivery took the next Telegram message as its label). An
    unreadable prompt fails OPEN, as :func:`refuses_paste` does — refusing on a tmux hiccup
    would wedge every Telegram message.
    """
    draft = _read_draft(window_id)
    if draft:
        raise PendingDraft(draft)
    return send_tmux(window_id, text)


def _hook_cursor() -> dict | None:
    """The event log's position before a send — None if it cannot be read."""
    try:
        return event_log.tip()
    except Exception:  # noqa: BLE001 — no cursor only costs the hook evidence
        log.warning("deliver: event-log tip unreadable — verifying on the pane alone",
                    exc_info=True)
        return None


def _carries(prompt: str, text: str) -> bool:
    """Is the submitted ``prompt`` the turn our ``text`` went into?

    Claude Code turns the pasted path into an attachment, so the path itself is gone from
    the prompt; the image marker before it survives, and so does a caption's opening (the
    hook log keeps only a preview, past which a long caption hides the marker).
    """
    head = (text.strip().splitlines() or [""])[0][:_CARRIES_PREFIX]
    return INBOUND_IMAGE_MARKER in prompt or bool(head) and head in prompt


def _prompt_submitted(window_id: str, text: str, cursor: dict | None) -> bool:
    """Did ``window_id``'s Claude Code submit a turn carrying ``text`` after ``cursor``?

    The AUTHORITATIVE submit signal (CMX-344): UserPromptSubmit fires when Claude Code
    takes the turn, so it cannot be fooled by what the pane happens to show. Only a prompt
    that carries our delivery counts (:func:`_carries`) — a scheduler injection or a human
    turn in the same window says nothing about a chip stranded meanwhile. A missing cursor
    or an unreadable log is no evidence — never a submit — and the pane check decides.
    """
    if cursor is None:
        return False
    try:
        found = event_log.read(cursor["seq"], after_boot=cursor["boot_id"],
                               types=[_PROMPT_SUBMIT_EVENT], wid=window_id)
    except Exception:  # noqa: BLE001 — see the docstring
        log.warning("deliver: event log unreadable — verifying on the pane alone",
                    exc_info=True)
        return False
    return any(_carries(str((e.get("payload") or {}).get("prompt") or ""), text)
               for e in found["events"])


def deliver_image(window_id: str, text: str) -> str:
    """Deliver an image path and VERIFY Claude Code submitted it.

    ``"submitted"`` once the window's UserPromptSubmit hook fires (:func:`_prompt_submitted`)
    or, as the fallback, the prompt reads empty; ``"unsubmitted"`` if neither happened, or
    the prompt could not be read (unknown is never success); ``"failed"`` if the send
    failed. Claude Code converts the pasted path into an ``[Image #N]`` chip asynchronously
    and can swallow an Enter that lands meanwhile, stranding the chip on the prompt — while
    the chip is still there and no submit was seen, Enter is pressed once more.
    """
    cursor = _hook_cursor()
    if not deliver_prompt(window_id, text):
        return "failed"
    resent = False
    for _ in range(_SUBMIT_POLLS):
        time.sleep(_SUBMIT_POLL_SECONDS)
        if _prompt_submitted(window_id, text, cursor):
            return "submitted"
        draft = _read_draft(window_id)
        if draft == "":
            return "submitted"
        if draft and not resent and _RE_IMAGE_CHIP.search(draft):
            resent = True
            resend_enter(window_id)
    # The hook can land after the last pane read: one more poll's wait, then a last look.
    time.sleep(_SUBMIT_POLL_SECONDS)
    return "submitted" if _prompt_submitted(window_id, text, cursor) else "unsubmitted"


def send_key(window_id: str, key: str) -> bool:
    """Send a single tmux key press (``Up``, ``C-c``, ``Space``…) to ``window_id``.

    The generic primitive behind the Telegram bridge's ``/screenshot`` control-key
    keyboard: each tapped button maps to a tmux ``send-keys`` key *name* delivered
    here, so an operator can drive the bound terminal from their phone. ``key`` is
    a tmux key name — no Enter is appended (``Enter`` is itself a valid key name,
    so the caller asks for it explicitly). Read-only otherwise; returns True on
    success, False if tmux errors.
    """
    target = config.window_target(window_id)
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


def deterministic_peer_socket_path(window_id: str) -> Path:
    """The chela-owned socket path an agent for ``window_id`` is (or would be) launched
    with via ``--messaging-socket-path`` (:func:`messaging_socket_launch_arg`) —
    ``$CHELA_DIR/socks/<window_id, sans '@'>.sock``.

    Deterministic and keyed on the WINDOW, not the pid: it retires the pid<->window
    mapping :func:`_peer_socket_path`'s legacy guess still depends on (today, on the
    daemon's OWN environment standing in for the target's), and the name-collision
    problem — two windows sharing a display name still have distinct ids. A session
    launched before this flag existed simply has no file here yet, which is exactly
    the ``.exists()`` check :func:`_peer_socket_path` falls back past.
    """
    safe = window_id.lstrip("@")
    return config.CHELA_DIR / "socks" / f"{safe}.sock"


def messaging_socket_launch_arg(window_id: str) -> str | None:
    """The ``--messaging-socket-path <path>`` flag to launch ``window_id`` with, or
    None if that path would overflow the AF_UNIX ``sun_path`` ceiling (~104 bytes;
    checked, not assumed — the deepest real worktree path can get close).

    A launcher that gets None omits the flag entirely: the session then binds
    Claude Code's own default location, still reachable via
    :func:`_peer_socket_path`'s legacy pid-derived guess — an oversized chela path
    degrades delivery to the pre-CMX-223 guess, it does not break it.
    """
    path = deterministic_peer_socket_path(window_id)
    encoded_len = len(str(path).encode())
    if encoded_len >= _SUN_PATH_MAX:
        log.warning(
            "messaging socket path for %s is %d bytes (>= %d-byte sun_path ceiling) — "
            "launching without --messaging-socket-path: %s",
            window_id, encoded_len, _SUN_PATH_MAX, path,
        )
        return None
    return f"--messaging-socket-path {path}"


def _default_peer_socket_candidates(pid: int) -> list[Path]:
    """The legacy pid-derived guess locations Claude Code binds to when given no explicit
    ``--messaging-socket-path`` (newest build first): ``$XDG_RUNTIME_DIR/cc-socks/<pid>.sock``
    and ``$TMPDIR-or-/tmp/cc-socks-<uid>/<pid>.sock`` — read off OUR OWN environment,
    standing in for the target's (see :func:`_peer_socket_path`'s docstring for why that
    holds today). Extracted so a windowless send (:func:`peer_socket_path_for_pid`) can use
    exactly this half of :func:`_peer_socket_candidate` without the window-keyed
    deterministic path, which never applies to a session with no window at all.
    """
    candidates = []
    runtime_dir = os.environ.get("XDG_RUNTIME_DIR")
    if runtime_dir:
        candidates.append(Path(runtime_dir) / "cc-socks" / f"{pid}.sock")
    tmp_dir = os.environ.get("TMPDIR") or "/tmp"
    candidates.append(Path(tmp_dir) / f"cc-socks-{os.getuid()}" / f"{pid}.sock")
    return candidates


def _peer_socket_candidate(window_id: str, pid: int) -> tuple[Path, str] | None:
    """Like :func:`_peer_socket_path`, but also names WHICH family the path came from —
    ``"deterministic"`` (chela-owned, keyed on the window) or ``"default"`` (the legacy
    pid-derived guess, read off our own environment). None if neither has a file.

    Existence-only, same as :func:`_peer_socket_path` was: this answers "which file would
    ``send_peer`` try first", not "would it connect" — see :func:`peer_socket_reachable`
    for the latter, and :func:`peer_transport_kind` for the two combined.
    """
    deterministic = deterministic_peer_socket_path(window_id)
    if deterministic.exists():
        return deterministic, "deterministic"
    for path in _default_peer_socket_candidates(pid):
        if path.exists():
            return path, "default"
    return None


def peer_socket_path_for_pid(pid: int) -> Path | None:
    """The Unix socket a WINDOWLESS pid's Claude Code session listens on, if any —
    existence-only, the pid-addressed counterpart of :func:`_peer_socket_path` used by
    :func:`send_peer_to_pid` (CMX-255).

    Only ever the legacy pid-derived guess (:func:`_default_peer_socket_candidates`): the
    chela-owned deterministic path (:func:`deterministic_peer_socket_path`) is keyed on a
    WINDOW id chela's own dispatcher assigned at launch via ``--messaging-socket-path``, and
    a windowless session — by definition never dispatched into a window — was never launched
    with that flag, so that path can never apply to it.
    """
    for path in _default_peer_socket_candidates(pid):
        if path.exists():
            return path
    return None


def _peer_socket_path(window_id: str, pid: int) -> Path | None:
    """The Unix socket ``window_id``'s Claude Code session listens on, if any.

    Checks the chela-owned deterministic path FIRST
    (:func:`deterministic_peer_socket_path` — populated when the session was
    launched with ``--messaging-socket-path``), then falls back to the legacy guess:
    the two locations Claude Code itself binds to when given no explicit path
    (newest build first), ``$XDG_RUNTIME_DIR/cc-socks/<pid>.sock`` and
    ``$TMPDIR-or-/tmp/cc-socks-<uid>/<pid>.sock`` — read off OUR OWN environment,
    standing in for the target's (it happens to hold today because the live daemon
    exports the same ``XDG_RUNTIME_DIR`` every session inherits, which is exactly
    why this is a fallback and not the primary path). None means "no such socket" —
    a plain, expected outcome (older Claude Code, or the session hasn't bound one
    yet), not an error. Existence-only — see :func:`peer_socket_reachable` for whether
    anything is actually listening there.
    """
    candidate = _peer_socket_candidate(window_id, pid)
    return candidate[0] if candidate else None


def peer_socket_reachable(path: Path, timeout: float = _PROBE_TIMEOUT) -> bool:
    """Whether a live process is actually listening on the AF_UNIX socket at ``path`` —
    not just whether the file exists.

    A socket FILE surviving its process is the ordinary case, not a rare one: the
    deterministic path is keyed on the WINDOW, not the pid (:func:`deterministic_peer_socket_path`),
    so it is designed to outlive any one session. An agent SIGKILLed never runs its own
    unlink; a bare ``claude`` later started in that window with no
    ``--messaging-socket-path`` leaves the stale file sitting there, passing
    ``.exists()`` forever while nothing accepts a connection
    (:func:`test_send_peer_false_when_socket_refuses_connection` in
    ``tests/test_messenger.py`` is this exact scenario).

    Connects and closes immediately, sending ZERO bytes — this must never be able to
    hand a target agent a turn (a doctor run has to stay side-effect-free), and only
    :func:`send_peer`'s actual write does that. Matches Claude Code's own bundle, which
    classifies a socket live/dead the same way with the same short timeout.
    """
    if not path.exists():
        return False
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
            sock.settimeout(timeout)
            sock.connect(str(path))
        return True
    except OSError:
        return False


def peer_transport_kind(window_id: str, pid: int, timeout: float = _PROBE_TIMEOUT) -> str:
    """Which transport a :func:`send_peer` to ``window_id`` would actually take right
    now: ``"deterministic"`` (chela-owned path, confirmed reachable), ``"default"``
    (the legacy pid-derived guess, confirmed reachable — works today, but only because
    it happens to read OUR OWN environment as a stand-in for the target's, and needs a
    relaunch to pick up a chela-owned path), or :data:`UNREACHABLE` (no socket file, or
    a stale one nothing is listening on — :func:`send_peer` will fail and every agent
    message to the window is held, never typed into the pane).
    """
    candidate = _peer_socket_candidate(window_id, pid)
    if candidate is None:
        return UNREACHABLE
    path, kind = candidate
    if not peer_socket_reachable(path, timeout=timeout):
        return UNREACHABLE
    return kind


def _await_receipt(server: socket.socket, msg_id: str) -> str:
    """Block up to :data:`_RECEIPT_WAIT_SECONDS` for a receipt correlated to ``msg_id``.

    Returns ``"sent"`` if nothing arrives in time, or something arrives that doesn't
    correlate — the ACCEPT path produces NO receipt at all, ever (measured), so
    silence within a short window is the SUCCESS signal here, not a timeout failure.
    Returns the receipt's own ``status`` (``"held"``/``"denied"``/``"expired"``/
    ``"delivered"``) the instant one lands whose ``orig_msg_id`` matches ours — never
    synthesised, only ever read off the wire.
    """
    try:
        conn, _ = server.accept()
    except OSError:
        return "sent"
    try:
        conn.settimeout(_RECEIPT_WAIT_SECONDS)
        data = conn.recv(65536)
    except OSError:
        return "sent"
    finally:
        conn.close()
    try:
        receipt = json.loads(data.decode().splitlines()[0])
    except (UnicodeDecodeError, ValueError, IndexError):
        return "sent"
    if receipt.get("type") != "control" or receipt.get("action") != "peer_message_status":
        return "sent"
    if receipt.get("orig_msg_id") != msg_id:
        return "sent"
    return receipt.get("status") or "sent"


def sanitize_peer_name(name: str | None) -> str:
    """``name`` made safe for the envelope's ``from-name`` attribute: quotes, angle
    brackets, and control/format characters (newlines included) removed, whitespace
    collapsed, capped at the receiver's 64 chars. Empty after that ⇒ :data:`PEER_SENDER_NAME`.
    The receiver strips ``["<>]`` itself, but a name reaching it raw is chela's bug, not
    its to catch — an unescaped ``"`` would end the attribute and let the name forge the
    ones after it.
    """
    spaced = _PEER_NAME_CONTROL.sub(" ", _PEER_NAME_QUOTES.sub("", name or ""))
    cleaned = " ".join(spaced.split())[:_PEER_NAME_MAX].strip()
    return cleaned or PEER_SENDER_NAME


def _peer_envelope(reply_address: str, sender_name: str, content: str) -> str:
    """``content`` wrapped in the receiver's own ``<cross-session-message>`` envelope
    carrying ``from``/``from-name`` — the frame a native ``SendMessage`` sends, minus its
    gate-read ``from-mode`` (deliberately never sent — see the module constants), and the ONLY thing that makes the arrival render named (see
    :data:`_PEER_ENVELOPE_TAG`). Attribute order and the newline-delimited body are
    load-bearing: the receiver re-serialises what it parsed and discards the envelope
    (falling back to the anonymous render, content intact) on any mismatch.
    """
    address = _PEER_ADDR_UNSAFE.sub(
        lambda m: "".join(f"%{b:02X}" for b in m.group().encode()), reply_address)
    body = _PEER_BODY_CLOSE.sub("<\\\\", content)
    return (f'<{_PEER_ENVELOPE_TAG} from="{address}" '
            f'from-name="{sanitize_peer_name(sender_name)}">\n'
            f"{body}\n</{_PEER_ENVELOPE_TAG}>")


def send_peer(window_id: str, from_agent: str, content: str) -> PeerSendResult:
    """Deliver ``content`` straight into ``window_id``'s Claude Code message queue over
    its peer-messaging Unix socket, then listen briefly for a receipt. Returns a
    :class:`PeerSendResult`; ``handed_off`` is False if no such socket could be
    reached at all — the message is then unreachable, never typed into the pane
    (:func:`deliver_peer`). ``content`` is sent
    EXACTLY as given — no ``[from] `` wrapping is added here (unlike the old
    contract): callers that want attribution add it themselves before calling, the
    same contract :func:`send_tmux` already has. That is what lets a caller with its
    own fully-formatted, already-attributed prompt (rooms' :func:`build_prompt`) use
    this without a nested double-wrap. The arrival is NAMED ``chela`` (CMX-380) — the
    real, socket-verified sender, never ``from_agent`` (see :data:`PEER_SENDER_NAME`) —
    via the envelope :func:`_send_over_socket` wraps the text in.

    Resolves ``window_id`` to a pid via the tmux pane it lives in
    (:func:`chela.agent_manager.claude_pid`), then hands off to :func:`_send_over_socket`
    (the wire format, receipt correlation, and reply-socket lifecycle are documented
    there) — the same body :func:`send_peer_to_pid` shares for a windowless target.
    """
    from chela import agent_manager  # deferred: agent_manager imports send_tmux from us

    pid = agent_manager.claude_pid(window_id)
    if pid is None:
        return PeerSendResult(False, None)
    sock_path = _peer_socket_path(window_id, pid)
    if sock_path is None:
        return PeerSendResult(False, None)
    return _send_over_socket(sock_path, content, target_desc=f"{window_id} (pid {pid})")


def send_peer_to_pid(pid: int, from_agent: str, content: str) -> PeerSendResult:
    """Like :func:`send_peer`, but addressed by a raw pid with no tmux window at all —
    the delivery half of CMX-255's windowless-orchestrator mechanism (the pid-resolution
    half, finding whose pid to use in the first place, is :func:`chela.sessions.own_claude_pid`
    at registration time; this only ever ADDRESSES an already-known pid).

    Looks up ONLY the legacy pid-derived socket (:func:`peer_socket_path_for_pid`) — a
    windowless session was never launched with ``--messaging-socket-path`` (chela's own
    dispatcher is the only thing that ever sets it, keyed on a window id it assigned), so
    the chela-owned deterministic path :func:`send_peer` checks first never applies here.
    ``handed_off=False`` here means genuinely undeliverable — the same as for
    :func:`send_peer`, since no agent message ever falls back to a pane paste.
    """
    sock_path = peer_socket_path_for_pid(pid)
    if sock_path is None:
        return PeerSendResult(False, None)
    return _send_over_socket(sock_path, content, target_desc=f"pid {pid}")


def _send_over_socket(sock_path: Path, content: str, *, target_desc: str) -> PeerSendResult:
    """Shared body of :func:`send_peer`/:func:`send_peer_to_pid` once a socket path has
    been resolved: connect, hand off ``content`` as one line of newline-delimited JSON —
    ``{"type": "user", "message": {"role": "user", "content": ...}, "from": "uds:<our
    reply socket>", "msg_id": <uuid4>}``, the wire format Claude Code's own
    ``uds-messaging`` listener parses (verified against the running ``claude`` binary) —
    then listen briefly for a receipt. ``content`` goes inside the receiver's own
    ``<cross-session-message>`` envelope naming :data:`PEER_SENDER_NAME` (:func:`_peer_envelope`,
    CMX-380 — that envelope, not a top-level field, is what renders the arrival named)
    but is otherwise sent as given — no ``[from] `` wrapping is added here (unlike the
    old contract): callers that want attribution in the text add it themselves before
    calling, the same contract :func:`send_tmux` already has. ``role`` stays ``"user"``
    and ``from`` stays our reply socket: the receiver classifies the arrival as a peer
    message off the socket, not off the envelope.

    ``msg_id`` MUST be a real UUID: a non-UUID id comes back on a receipt with
    ``orig_msg_id`` ABSENT, breaking correlation silently (measured). ``from`` must be OUR
    OWN listening socket, in the SAME DIRECTORY as the target's socket, or the receipt is
    skipped silently (also measured) — so a reply socket is bound here for this one send
    and unlinked when we're done listening on it, never reused across sends (a stale reply
    path could otherwise correlate a LATER receipt to the wrong call).
    """
    msg_id = str(uuid.uuid4())
    # The reply socket's FILENAME only has to be collision-safe for one short-lived
    # listen, not carry the whole msg_id — a 10-hex-char stub keeps the deepest real
    # worktree-adjacent path (checked below) well clear of the sun_path ceiling. The
    # full msg_id still travels in the JSON payload, which is what correlation reads.
    reply_path = sock_path.parent / f"r-{msg_id.replace('-', '')[:10]}.sock"
    if len(str(reply_path).encode()) >= _SUN_PATH_MAX:
        log.warning("reply socket path for %s is %d bytes (>= %d-byte sun_path ceiling) "
                    "— can't listen for a receipt; treating as unreachable: %s", target_desc,
                    len(str(reply_path).encode()), _SUN_PATH_MAX, reply_path)
        return PeerSendResult(False, None)
    reply_server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        reply_server.bind(str(reply_path))
        reply_server.listen(1)
        payload = {
            "type": "user",
            "message": {"role": "user",
                        "content": _peer_envelope(f"uds:{reply_path}", PEER_SENDER_NAME, content)},
            "from": f"uds:{reply_path}",
            "msg_id": msg_id,
        }
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
                sock.settimeout(_PEER_CONNECT_TIMEOUT)
                sock.connect(str(sock_path))
                sock.sendall((json.dumps(payload) + "\n").encode())
        except OSError as e:
            log.warning("peer socket send to %s failed: %s", target_desc, e)
            return PeerSendResult(False, None)

        reply_server.settimeout(_RECEIPT_WAIT_SECONDS)
        status = _await_receipt(reply_server, msg_id)
        if status in ADVERSE_RECEIPT_STATUSES:
            log.warning("peer message to %s was %s — handed off but NOT delivered",
                        target_desc, status)
        return PeerSendResult(True, status)
    finally:
        reply_server.close()
        reply_path.unlink(missing_ok=True)


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


# The outcomes of :func:`deliver_peer`/:func:`send_message`. Anything else a caller sees is
# one of :data:`ADVERSE_RECEIPT_STATUSES` (the receiver's own gate dropped it).
DELIVERED = "delivered"
# CMX-482: no peer socket could be reached. ⛔ An agent message is NEVER retried as a pane
# paste (:func:`send_tmux`): a paste arrives as a bare user turn with no ``from``, so the
# receiver shows no cross-session banner and another agent's text reads as the user's.
# The caller holds the message where a queue exists and says so loudly.
UNREACHABLE = "unreachable"
# The target is not a live tmux window at all.
OFFLINE = "offline"


def deliver_peer(window_id: str, from_agent: str, text: str, *, via: str) -> str:
    """Send ``text`` to ``window_id`` over its peer socket, and nothing else — the ONE
    transport for a message to an agent (bucket 1 in ``docs/TMUX_TOUCHPOINTS.md``).

    Returns :data:`DELIVERED`, :data:`UNREACHABLE` (no socket could be reached), or the
    adverse receipt status (``held``/``denied``/``expired``: the receiver's own
    ``crossSessionInbound`` gate dropped it). An unreachable socket is announced
    (:func:`announce_unreachable`) naming the window and ``via`` (the caller: ``chela
    msg``, a room, …), so a held message is never silent.
    """
    peer = send_peer(window_id, from_agent, text)
    if peer.handed_off:
        if peer.status in ADVERSE_RECEIPT_STATUSES:
            log.warning("%s: %s %s the peer message — NOT delivered", via, window_id,
                        peer.status)
            return peer.status
        return DELIVERED
    announce_unreachable(window_id, via=via, wid=window_id)
    return UNREACHABLE


# A daemon retries a held message every tick; one event-log entry per target per window of
# this many seconds says it loudly without flooding the log. Every attempt is still logged.
UNREACHABLE_ANNOUNCE_SECONDS = 600.0
_unreachable_announced: dict[tuple[str, str], float] = {}


def announce_unreachable(target: str, *, via: str, wid: str | None,
                         now: float | None = None) -> bool:
    """Say loudly that an agent message to ``target`` was not delivered because no peer
    socket could be reached: a log warning every time, and a ``peer_unreachable`` event-log
    entry at most once per :data:`UNREACHABLE_ANNOUNCE_SECONDS` per target and sender
    (``via``), so one caller's retries never hide another caller's failure. Returns whether
    the event was written."""
    log.warning("%s: %s has no reachable peer-messaging socket — message NOT delivered "
                "(never typed into the pane instead)", via, target)
    now = time.time() if now is None else now
    key = (target, via)
    last = _unreachable_announced.get(key)
    if last is not None and now - last < UNREACHABLE_ANNOUNCE_SECONDS:
        return False
    _unreachable_announced[key] = now
    event_log.append(
        "peer_unreachable",
        f"📵 {via}: {target} has no reachable peer socket; message held, not typed "
        f"into the pane",
        {"target": target, "via": via}, wid=wid)
    return True


def send_message(from_agent: str, to_agent: str, message: str, priority: str = "normal") -> str:
    """Send a message to an agent over its peer socket. Live-only — no queue.

    ``to_agent`` is anything :func:`resolve_window` accepts (window id or name). Returns
    :data:`DELIVERED` only when the message actually reached the recipient; otherwise
    :data:`OFFLINE` (no live window), :data:`UNREACHABLE` (no peer socket could be
    reached), or the adverse receipt status. ⛔ **A peer-socket handoff whose receipt comes
    back ``held``/``denied``/``expired`` is NOT a success** (CMX-223): the receiver's gate
    is a deliberate policy decision, and typing the same text into its pane would route
    around it. ⛔ **Neither is an unreachable socket a reason to type into the pane**
    (CMX-482): that paste would arrive as a user turn, unattributed. Callers report the
    outcome and exit non-zero (``chela msg`` does).
    """
    window_id = resolve_window(to_agent)
    if window_id is None:
        log.warning("%s is not a live tmux window — message NOT delivered", to_agent)
        return OFFLINE
    # Prefix with the sender so the recipient has context on who pinged them.
    return deliver_peer(window_id, from_agent, f"[{from_agent}] {message}",
                        via=f"message from {from_agent}")


def broadcast(from_agent: str, message: str, priority: str = "normal") -> dict[str, str]:
    """Send a message to every other live agent. Returns {agent: outcome} (see
    :func:`send_message`).

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
