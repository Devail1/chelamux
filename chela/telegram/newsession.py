"""``/new`` — launch a Claude session from Telegram, from anywhere.

The :class:`~chela.telegram.inbound.RegistryRouter` deliberately DROPS any message
from an unbound topic (the forum's General included): a topic must be bound to a
window before its text is relayed. That is the right rule for *chat*, but it also
means a phone with only the General topic open has **no way to start an agent** —
the "launch a session from anywhere" workflow ccbot shipped in its
``directory_browser`` was lost when the fold ported the *binding* machinery but not
the *launcher*. This module restores it as a ``/new`` bridge command.

Two halves, split so the UI unit-tests with no tmux and no ``python-telegram-bot``:

* the **pure browser UI** — :func:`build_browser` renders a folder as an inline
  keyboard, :func:`decode_new_callback` reads a tap back. Callback payloads carry
  only a small token (``n:cd:<idx>``, ``n:pg:<page>``, ``n:up``, ``n:go``,
  ``n:x``) so they never approach Telegram's 64-byte limit; the current path and
  its subdir list live in the caller's per-user state instead (ccbot's approach).
* the **launcher** — :func:`launch_claude_window` opens a tmux window in the chosen
  directory and starts ``claude`` in it. It does NOT re-implement the window spawn:
  it delegates to :func:`chela.spawn.spawn_window`, the ONE window-creation path the
  dashboard launcher uses too, so the two can never drift (see that module). It is
  injected into :func:`chela.telegram.inbound.build_application` so tests drive a stub.

**Launch-and-BIND, without this module binding anything.** ``/new`` only *launches*
the window; the bind is emergent. A ``/new`` window is an ordinary agent window (not
dispatcher-owned), so the auto-topics reconcile loop
(:func:`chela.telegram.reconcile.reconcile_bindings`) provisions a forum topic for it
and binds it on its next tick — the same idempotent path every other agent window
takes. So the new session's topic simply *appears* in the forum a moment later,
already routing, and this module never duplicates the reconcile's create/bind logic
(which would race it). Auto-topics is on for the ``chela telegram`` daemon by design;
with it off there is nothing to create the topic, and ``/new`` says so.

Gated on the bound ``chat_id`` ONLY — never on a topic binding — because the whole
point is to work from the General topic, which has no binding. That is the same
CMX-8 security boundary every other handler honours; it just stops one step earlier.

Adapted from six-ddc/ccbot (https://github.com/six-ddc/ccbot), MIT — its
``directory_browser`` window-picker/folder-browser, reworked onto chela's discovery
and auto-topics layers. See the top-level NOTICE for attribution.
"""
from __future__ import annotations

import logging
import os
import re
from pathlib import Path

log = logging.getLogger(__name__)

# Callback-data prefix marking a tap from the ``/new`` folder browser, so the
# browser's CallbackQueryHandler can tell these from any other inline keyboard
# (``k:`` screenshot keys, ``qa:`` answers, ``m:`` mirror). Kept short — every
# byte here is a byte not spent on the payload, and Telegram caps callback_data
# at 64 bytes.
NEW_CB_PREFIX = "n:"

# Subdirectories shown per browser page. Two per keyboard row, so this is an even
# number of rows; small enough that a deep, wide directory still fits a phone.
DIRS_PER_PAGE = 8

# A directory-name button caption is trimmed so a long name can't blow out the
# keyboard's width on a narrow phone (the folder is still reachable — the full
# name is never in the callback, only its index).
_LABEL_MAX = 16


def start_dir() -> Path:
    """Where the browser opens: the configured projects dir, else ``$HOME``.

    Reuses the launcher's projects-dir resolution
    (:func:`chela.launcher._projects_dir` — GUI value, then
    ``$CHELA_PROJECTS_DIR``, then ``~/projects``) so ``/new`` and the dashboard
    launcher agree on where work lives, and falls back to home when that dir does
    not exist so a fresh install still opens *somewhere* sensible.
    """
    try:
        from chela import launcher

        base = launcher._projects_dir()
        if base.is_dir():
            return base
    except Exception:  # noqa: BLE001 — never let config resolution break the browser
        log.debug("could not resolve the projects dir; opening at home", exc_info=True)
    return Path.home()


def _tilde(path: Path | str) -> str:
    """``path`` with the home prefix collapsed to ``~`` for display."""
    text = str(path)
    home = str(Path.home())
    if text == home:
        return "~"
    if text.startswith(home + os.sep):
        return "~" + text[len(home):]
    return text


def list_subdirs(path: Path | str, *, show_hidden: bool = False) -> list[str]:
    """Sorted immediate subdirectory names of ``path`` (dotfiles hidden by default).

    An unreadable directory (permissions, a path that vanished) yields ``[]``
    rather than raising, so the browser degrades to "no subdirectories here" and
    the operator can still ``Start here`` or step back up.
    """
    try:
        entries = sorted(
            d.name
            for d in Path(path).iterdir()
            if d.is_dir() and (show_hidden or not d.name.startswith("."))
        )
    except (PermissionError, OSError):
        return []
    return entries


def build_browser(
    path: Path | str, page: int = 0, *, show_hidden: bool = False
) -> tuple[str, list[list[tuple[str, str]]], list[str]]:
    """Render ``path`` as a folder browser: ``(text, button_rows, subdirs)``.

    ``button_rows`` is a pure ``[[(label, callback_data), …], …]`` — the handler
    turns it into a PTB ``InlineKeyboardMarkup`` — so this whole function is
    testable with no ``python-telegram-bot`` import. ``subdirs`` is the FULL
    sorted subdir list; the caller caches it so a ``n:cd:<idx>`` tap resolves the
    (possibly off-page) folder by index instead of packing its name into the
    callback.

    Buttons: two folders per row (``📁 name`` → ``n:cd:<global-index>``); a page
    nav row (``◀`` / ``page/total`` / ``▶``) only when there is more than one
    page; and an action row — ``⬆ Up`` (unless already at the filesystem root),
    ``✅ Start here``, ``✖ Cancel``.
    """
    here = Path(path).expanduser()
    subdirs = list_subdirs(here, show_hidden=show_hidden)

    total_pages = max(1, (len(subdirs) + DIRS_PER_PAGE - 1) // DIRS_PER_PAGE)
    page = max(0, min(page, total_pages - 1))
    start = page * DIRS_PER_PAGE
    page_dirs = subdirs[start : start + DIRS_PER_PAGE]

    rows: list[list[tuple[str, str]]] = []
    for i in range(0, len(page_dirs), 2):
        row: list[tuple[str, str]] = []
        for j, name in enumerate(page_dirs[i : i + 2]):
            label = name if len(name) <= _LABEL_MAX else name[: _LABEL_MAX - 1] + "…"
            row.append((f"📁 {label}", f"{NEW_CB_PREFIX}cd:{start + i + j}"))
        rows.append(row)

    if total_pages > 1:
        nav: list[tuple[str, str]] = []
        if page > 0:
            nav.append(("◀", f"{NEW_CB_PREFIX}pg:{page - 1}"))
        nav.append((f"{page + 1}/{total_pages}", f"{NEW_CB_PREFIX}noop"))
        if page < total_pages - 1:
            nav.append(("▶", f"{NEW_CB_PREFIX}pg:{page + 1}"))
        rows.append(nav)

    action: list[tuple[str, str]] = []
    if here != here.parent:  # not the filesystem root
        action.append(("⬆ Up", f"{NEW_CB_PREFIX}up"))
    action.append(("✅ Start here", f"{NEW_CB_PREFIX}go"))
    action.append(("✖ Cancel", f"{NEW_CB_PREFIX}x"))
    rows.append(action)

    shown = _tilde(here)
    if subdirs:
        text = f"📂 Start a Claude session in:\n{shown}\n\nTap a folder to enter, or ✅ to start here."
    else:
        text = f"📂 Start a Claude session in:\n{shown}\n\n(no subdirectories) — ✅ to start here."
    return text, rows, subdirs


def decode_new_callback(data: str) -> tuple[str, int | None] | None:
    """Read a ``/new`` browser tap back to ``(kind, value)``, or ``None`` if not ours.

    ``kind`` is one of ``cd`` / ``pg`` (with an int ``value``), or ``up`` / ``go``
    / ``cancel`` / ``noop`` (``value`` is ``None``). Anything without the
    :data:`NEW_CB_PREFIX`, or a malformed index, returns ``None`` so the handler
    can answer the tap inertly rather than act on garbage.
    """
    if not data.startswith(NEW_CB_PREFIX):
        return None
    body = data[len(NEW_CB_PREFIX):]
    if body == "up":
        return "up", None
    if body == "go":
        return "go", None
    if body == "x":
        return "cancel", None
    if body == "noop":
        return "noop", None
    m = re.fullmatch(r"(cd|pg):(\d+)", body)
    if m:
        return m.group(1), int(m.group(2))
    return None


def launch_claude_window(cwd: str | Path) -> tuple[str | None, str | None]:
    """Open a tmux window in ``cwd`` and start ``claude`` in it: ``(window_id, error)``.

    The production launcher injected into
    :func:`chela.telegram.inbound.build_application`. Returns the new window's
    ``@id`` (or its name, if this tmux build echoed no id) and ``None`` on success,
    or ``(None, message)`` on failure — the caller relays the message to the
    operator rather than raising into the update queue.

    Just an adapter: the actual window-open is :func:`chela.spawn.spawn_window`, the
    SAME path the dashboard launcher takes, so ``/new`` and the dashboard can never
    diverge in how they set a window up. ``/new`` always launches the configured
    :data:`chela.agent_manager.DEFAULT_LAUNCH_CMD` (trusted, not user input — so no
    per-caller command validation is needed here).
    """
    from chela import agent_manager, spawn

    result = spawn.spawn_window(cwd, command=agent_manager.DEFAULT_LAUNCH_CMD)
    if not result.ok:
        return None, result.error
    log.info("telegram /new: launched %s in %s", result.name, result.cwd)
    return (result.wid or result.name), None
