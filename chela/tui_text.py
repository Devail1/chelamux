"""Text on its way into somebody else's TUI — the one place that makes it safe.

Anything chela PASTES into a tmux pane (`load-buffer` + `paste-buffer`) is not text to the
terminal that receives it: a raw ``\\x1b`` is an escape sequence and a raw ``\\x03`` is a
Ctrl-C aimed at that agent's Claude Code prompt. Rooms have always known this — this rule
was born in :func:`chela.rooms.sanitize` — but rooms are no longer the only sender: the CI
verdict (CMX-69) carries the RAW GitHub Actions log, escapes and all, down the same paste
path. So the rule lives here, and both callers share exactly one implementation of it.

:func:`sanitize` makes text safe as *text*. :func:`sanitize_prompt` makes it safe as a
*prompt line* — the stronger rule, for a one-line notification chela types at somebody's
Claude Code prompt, where the receiving TUI may not be treating the line as prose at all
(CMX-79: the orchestrator's pane was in ``!`` bash-input mode and RAN the notification).
"""
from __future__ import annotations

import re

# ANSI/OSC escape sequences first (they start with ESC, which the control-char pass below
# would otherwise strip, leaving the payload `[31m` as visible garbage).
ANSI_RE = re.compile(r"\x1b(?:\[[0-9;?]*[ -/]*[@-~]|\][^\x07\x1b]*(?:\x07|\x1b\\)|[@-Z\\-_])")
# Every C0 control except \n and \t, plus DEL and the C1 block. These are keystrokes, not
# text: a raw \x03 in a body is a Ctrl-C aimed at the recipient's TUI.
CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]")

# Shell metacharacters — the entire difference between a notification and a command. The
# text we type at a prompt is AGENT-AUTHORED (a PR title, a tracker line, a CI error), so
# `$(…)` and backticks in it are attacker-authored the moment an agent is prompt-injected.
# Replaced with a space rather than deleted: `$(id)` must not become the command `id`, and
# a summary is for reading, not for round-tripping. `#` survives — "PR #91" is half the
# point of the line, and a LEADING `#` is handled by MODE_PREFIX_RE below.
SHELL_META_RE = re.compile(r"""[`$\\;|&<>(){}\[\]!*?"']""")
# A prompt line's FIRST character chooses Claude Code's input mode: `!` bash, `#` memory,
# `/` slash-command. A line we type must never be able to make that choice — a summary that
# walks a SAFE prose pane into bash mode and then submits itself is the same bug, self-
# inflicted. (`!` is already gone above; this catches the rest, including any exposed by
# the leading-whitespace strip.)
MODE_PREFIX_RE = re.compile(r"^[/#\s]+")


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


def sanitize_prompt(text: str, max_chars: int | None = None) -> str:
    """One line of untrusted text, safe to TYPE at somebody's Claude Code prompt.

    :func:`sanitize` assumes the receiver reads what we send as prose. That assumption is
    the bug (CMX-79): the orchestrator's pane was in ``!`` bash-input mode, the decisions
    inbox typed a notification into it, and ``/bin/bash`` executed it. It only failed
    because the summary happened to contain ``(rework 1)`` and died on the parens — a PR
    title carrying ``$(…)`` or backticks would have RUN, in the one session that holds merge
    authority and an unsandboxed shell. The chain is fully agent-controlled end to end: an
    agent writes a PR title → the inbox builds a summary from it → the inbox types it at the
    prompt.

    So this is the rule for anything chela puts on a prompt LINE: no control bytes, no shell
    metacharacters, no newlines (a newline is a submit), and no leading character that would
    switch the input mode. What survives is words, and words are inert in EVERY mode — which
    is what makes this a backstop rather than a second guess at the pane's state. Refusing
    an unsafe pane (:func:`chela.messenger.refuses_paste`) is the first line; text that
    cannot execute is what makes an UNDETECTED mode survivable too.

    Not a replacement for :func:`sanitize`: a multi-line body (a room message, a CI log)
    still goes through that one — stripping every ``$`` out of a build log would mangle the
    content it exists to carry, and a body is pasted, not typed at a mode-switching first
    character.
    """
    text = SHELL_META_RE.sub(" ", sanitize(text))
    text = " ".join(text.split())          # collapse to ONE line — a newline is a submit
    text = MODE_PREFIX_RE.sub("", text)
    if max_chars is not None and len(text) > max_chars:
        text = text[:max_chars].rstrip() + "…"
    return text
