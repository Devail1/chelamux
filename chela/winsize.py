"""A tmux window never takes a size too small to read (CMX-503).

On 11-24 a dashboard restart refused 10 of 13 sessions: each window was 66x1 with no client
attached, so its prompt could not be read. A tmux window keeps the size its last client gave
it after that client detaches, and one tiny client (a minimized or collapsed Wall tile is a
one-row terminal) is enough to leave it there. The same size breaks every other pane read
(rename, title flush) and hard-wraps or hides output.

Two halves:

1. **The gate** (:func:`install_gate`): server-global tmux hooks, so it holds for every
   client — a Wall tile, a phone, a plain ``tmux attach`` — whether or not the dashboard
   runs. While a window is at least :data:`MIN_COLS` x :data:`MIN_ROWS`, ``window-resized``
   records that size on the window (``@chela-ok-w`` / ``@chela-ok-h``). When a client
   shrinks it below that, the same hook puts the window back at its last usable size and
   pins it there (``window-size manual``, marked ``@chela-min-pin``), so the tiny client
   sees a corner of a readable window instead of the window shrinking to it. The pin is
   lifted (back to the global ``window-size``) the moment a client big enough to size the
   window attaches, resizes, or switches to it. A pin is never lifted that chela did not set:
   the presenter's ``window-size manual`` (dashboard ``_pin_grid``) carries no marker.
2. **The reader** (:func:`ensure_readable`): before a read that needs the prompt, an
   undersized window with no client viewing it is given a normal size; one that a client
   still holds small is reported as "window is N row(s) tall", not just "unreadable".

tmux's per-client ``ignore-size`` cannot do the gate's job: tmux drops it whenever EVERY
client of a window carries it (measured, tmux 3.4), so a lone one-row client still sizes
its window.
"""
from __future__ import annotations

import logging
import subprocess

log = logging.getLogger(__name__)

# The smallest window a client may give a window. Equal to the presenter grid's own floor
# (dashboard ``api_term_grid`` clamps to >= 20 x 6), so a presented grid is never fought.
MIN_COLS = 20
MIN_ROWS = 6
# The size an undersized, unwatched window is given when it never had a usable one recorded
# (the size used by hand on 11-24).
NORMAL_COLS = 120
NORMAL_ROWS = 40

# Index on each hook array: chela's own slot, so installing replaces only chela's hook and
# never an operator's own (``set-hook -g client-resized`` without an index would).
HOOK_INDEX = 93
_PIN = "@chela-min-pin"
_OK_W = "@chela-ok-w"
_OK_H = "@chela-ok-h"
_TIMEOUT = 5

# ⚠️ `e|<|` and `e|>=|`, never a bare `<`: tmux's plain comparison is a STRING one, under
# which "29" < "6".
_SMALL_WINDOW = (f"#{{||:#{{e|<|:#{{window_height}},{MIN_ROWS}}},"
                 f"#{{e|<|:#{{window_width}},{MIN_COLS}}}}}")
_USABLE_CLIENT = (f"#{{&&:#{{e|>=|:#{{client_height}},{MIN_ROWS}}},"
                  f"#{{e|>=|:#{{client_width}},{MIN_COLS}}}}}")
# Too small: back to the last usable size (else the normal one) and pinned there. tmux does
# not expand formats in resize-window's -x/-y, so the resize goes through run-shell, which
# does; $TMUX (set for run-shell) keeps the inner tmux on this same server.
_RESTORE = (
    f"set-window-option window-size manual ; set-window-option {_PIN} 1 ; "
    f"run-shell \"tmux resize-window -t '#{{window_id}}' "
    f"-x #{{?{_OK_W},#{{{_OK_W}}},{NORMAL_COLS}}} -y #{{?{_OK_H},#{{{_OK_H}}},{NORMAL_ROWS}}}\""
)
_RECORD = (f"set-window-option -F {_OK_W} \"#{{window_width}}\" ; "
           f"set-window-option -F {_OK_H} \"#{{window_height}}\"")
_UNPIN = f"set-window-option -u window-size ; set-window-option -u {_PIN}"

# hook -> command (tmux's `{ … }` blocks nest without another level of quoting). A client
# hook acts on the window its session shows.
HOOKS: dict[str, str] = {
    "window-resized": f"if-shell -F '{_SMALL_WINDOW}' {{ {_RESTORE} }} {{ {_RECORD} }}",
    **{name: f"if-shell -F '#{{&&:#{{{_PIN}}},{_USABLE_CLIENT}}}' {{ {_UNPIN} }}"
       for name in ("client-attached", "client-resized", "session-window-changed")},
}


def install_gate() -> bool:
    """Install :data:`HOOKS` on the running tmux server (idempotent: each lands in its
    :data:`HOOK_INDEX` slot). True only when tmux accepted every one; a failure is logged,
    never raised — a window that may shrink is no reason to fail a spawn."""
    ok = True
    for name, command in HOOKS.items():
        try:
            proc = subprocess.run(["tmux", "set-hook", "-g", f"{name}[{HOOK_INDEX}]", command],
                                  capture_output=True, text=True, timeout=_TIMEOUT)
        except (OSError, subprocess.SubprocessError) as e:
            log.warning("winsize: could not install the %s hook: %s", name, e)
            return False
        if proc.returncode != 0:
            log.warning("winsize: tmux refused the %s hook: %s", name, proc.stderr.strip())
            ok = False
    return ok


def window_size(wid: str) -> tuple[int, int, int] | None:
    """``(cols, rows, clients viewing it)`` for window ``wid``, or None when tmux can't say."""
    try:
        proc = subprocess.run(
            ["tmux", "display-message", "-p", "-t", wid,
             "#{window_width} #{window_height} #{window_active_clients}"],
            capture_output=True, text=True, timeout=_TIMEOUT)
    except (OSError, subprocess.SubprocessError):
        return None
    parts = proc.stdout.split()
    if proc.returncode != 0 or len(parts) != 3 or not all(p.isdigit() for p in parts):
        return None
    cols, rows, clients = (int(p) for p in parts)
    return cols, rows, clients


def undersized(cols: int, rows: int) -> bool:
    return cols < MIN_COLS or rows < MIN_ROWS


def _rows_text(cols: int, rows: int) -> str:
    return f"window is {rows} row{'' if rows == 1 else 's'} tall ({cols}x{rows})"


def ensure_readable(wid: str) -> str | None:
    """Make ``wid`` big enough to read before a read that needs its prompt.

    None when it is (already, or now: an undersized window no client views is resized to
    :data:`NORMAL_COLS` x :data:`NORMAL_ROWS` and pinned like the gate pins it, so the next
    client big enough lifts the pin). Otherwise why not — "window is 1 row tall (66x1), …"
    — for the caller's refusal. A size tmux cannot report is not a reason here: the read
    itself then decides."""
    size = window_size(wid)
    if size is None:
        return None
    cols, rows, clients = size
    if not undersized(cols, rows):
        return None
    if clients:
        return (f"{_rows_text(cols, rows)}, and {clients} attached client"
                f"{'' if clients == 1 else 's'} keep{'s' if clients == 1 else ''} it that size")
    install_gate()
    steps = (["set-window-option", "-t", wid, "window-size", "manual"],
             ["set-window-option", "-t", wid, _PIN, "1"],
             ["resize-window", "-t", wid, "-x", str(NORMAL_COLS), "-y", str(NORMAL_ROWS)])
    for argv in steps:
        try:
            subprocess.run(["tmux", *argv], capture_output=True, text=True, timeout=_TIMEOUT)
        except (OSError, subprocess.SubprocessError) as e:
            log.warning("winsize: resizing %s failed: %s", wid, e)
            break
    after = window_size(wid)
    if after is None or undersized(after[0], after[1]):
        cols, rows = (after[0], after[1]) if after else (cols, rows)
        return f"{_rows_text(cols, rows)} and could not be resized"
    log.info("winsize: %s was %dx%d with no client — resized to %dx%d",
             wid, size[0], size[1], after[0], after[1])
    return None
