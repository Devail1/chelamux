"""Text on its way into somebody else's TUI — the one place that makes it safe.

Anything chela PASTES into a tmux pane (`load-buffer` + `paste-buffer`) is not text to the
terminal that receives it: a raw ``\\x1b`` is an escape sequence and a raw ``\\x03`` is a
Ctrl-C aimed at that agent's Claude Code prompt. Rooms have always known this — this rule
was born in :func:`chela.rooms.sanitize` — but rooms are no longer the only sender: the CI
verdict (CMX-69) carries the RAW GitHub Actions log, escapes and all, down the same paste
path. So the rule lives here, and both callers share exactly one implementation of it.
"""
from __future__ import annotations

import re

# ANSI/OSC escape sequences first (they start with ESC, which the control-char pass below
# would otherwise strip, leaving the payload `[31m` as visible garbage).
ANSI_RE = re.compile(r"\x1b(?:\[[0-9;?]*[ -/]*[@-~]|\][^\x07\x1b]*(?:\x07|\x1b\\)|[@-Z\\-_])")
# Every C0 control except \n and \t, plus DEL and the C1 block. These are keystrokes, not
# text: a raw \x03 in a body is a Ctrl-C aimed at the recipient's TUI.
CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]")


def sanitize(text: str, max_chars: int | None = None) -> str:
    """Strip ANSI escapes and control characters; collapse CRLF; optionally cap the length.

    Newlines and tabs survive (real content has them; the paste path handles newlines);
    everything else in the control range does not. ``max_chars=None`` means "do not cap" —
    for a caller that does its own truncation (the CI verdict keeps the TAIL of a log, not
    its head).
    """
    text = (text or "").replace("\r\n", "\n").replace("\r", "\n")
    text = ANSI_RE.sub("", text)
    text = CONTROL_RE.sub("", text)
    text = text.strip()
    if max_chars is not None and len(text) > max_chars:
        text = text[:max_chars].rstrip() + "…"
    return text
