"""Pane-scrape detector for permission/approval gates — the one TUI-regex module.

Unlike ``AskUserQuestion``/``ExitPlanMode`` (whose prompts are structured in the
JSONL transcript), a tool **permission gate** ("Do you want to proceed?" for a
Bash/Edit) is rendered only in Claude Code's live TUI — it never appears in the
transcript. Detecting a blocked agent therefore requires reading the tmux pane.

This module is the **sole home** for the Claude-Code permission/bash-approval TUI
regexes — the "signatures table". Keeping every such pattern here means a Claude
Code version bump that reworded a prompt is a one-file edit. Only the
permission/bash-approval patterns are ported from six-ddc/ccbot's
``terminal_parser.py`` (https://github.com/six-ddc/ccbot, MIT); the broader
interactive-UI / status-line parsing there is deliberately NOT ported (the
structured prompts come through the transcript). See the top-level NOTICE file
for upstream attribution.

Public API: :func:`detect_permission_gate` returns a :class:`Gate` (the matched
region text + a kind tag) when the pane shows a permission/bash-approval prompt,
else ``None``.
"""
from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class Gate:
    """A detected permission/approval gate.

    ``text`` is the extracted pane region (top→bottom marker, inclusive), used as
    the relay fallback when the transcript identity is unavailable. ``kind`` is
    the matching pattern's tag (``"PermissionPrompt"`` / ``"BashApproval"``).
    """

    text: str
    kind: str


@dataclass(frozen=True)
class _UIPattern:
    """A top/bottom marker pair that delimits a gate region in the pane.

    Extraction scans lines top-down: the first line matching any ``top`` regex
    marks the start, the first subsequent line matching any ``bottom`` regex
    marks the end (both boundary lines included). ``top``/``bottom`` are tuples
    so a reworded prompt across Claude Code versions is an added alternative, not
    a rewrite. An empty ``bottom`` extends the region to the last non-empty line.
    """

    name: str
    top: tuple[re.Pattern[str], ...]
    bottom: tuple[re.Pattern[str], ...]
    min_gap: int = 2  # minimum lines between top and bottom (inclusive)


# ── The signatures table (order matters — first match wins) ──────────────
#
# Ported verbatim (patterns only) from ccbot terminal_parser.py:74-99 — the
# PermissionPrompt + BashApproval UIPatterns. To support a new gate wording or a
# Claude Code version bump, edit ONLY this table.
GATE_PATTERNS: list[_UIPattern] = [
    _UIPattern(
        name="PermissionPrompt",
        top=(
            re.compile(r"^\s*Do you want to proceed\?"),
            re.compile(r"^\s*Do you want to make this edit"),
            re.compile(r"^\s*Do you want to create \S"),
            re.compile(r"^\s*Do you want to delete \S"),
        ),
        bottom=(re.compile(r"^\s*Esc to cancel"),),
    ),
    _UIPattern(
        # Permission menu with numbered choices (no "Esc to cancel" line)
        name="PermissionPrompt",
        top=(re.compile(r"^\s*❯\s*1\.\s*Yes"),),
        bottom=(),
        min_gap=2,
    ),
    _UIPattern(
        # Bash command approval
        name="BashApproval",
        top=(
            re.compile(r"^\s*Bash command\s*$"),
            re.compile(r"^\s*This command requires approval"),
        ),
        bottom=(re.compile(r"^\s*Esc to cancel"),),
    ),
]


_RE_LONG_DASH = re.compile(r"^─{5,}$")


def _shorten_separators(text: str) -> str:
    """Collapse lines of 5+ ``─`` characters to exactly ``─────`` (tidier relay)."""
    return "\n".join(
        "─────" if _RE_LONG_DASH.match(line) else line for line in text.split("\n")
    )


def _try_extract(lines: list[str], pattern: _UIPattern) -> str | None:
    """Return the region text matching ``pattern``, or None.

    Mirrors ccbot's extractor: first ``top`` match starts the region, first
    subsequent ``bottom`` match ends it. When ``bottom`` is empty the region
    runs to the last non-empty line (numbered permission menu has no fixed
    footer). Rejected if shorter than ``min_gap``.
    """
    top_idx: int | None = None
    bottom_idx: int | None = None

    for i, line in enumerate(lines):
        if top_idx is None:
            if any(p.search(line) for p in pattern.top):
                top_idx = i
        elif pattern.bottom and any(p.search(line) for p in pattern.bottom):
            bottom_idx = i
            break

    if top_idx is None:
        return None

    if not pattern.bottom:
        for i in range(len(lines) - 1, top_idx, -1):
            if lines[i].strip():
                bottom_idx = i
                break

    if bottom_idx is None or bottom_idx - top_idx < pattern.min_gap:
        return None

    region = "\n".join(lines[top_idx : bottom_idx + 1]).rstrip()
    return _shorten_separators(region)


def detect_permission_gate(pane_text: str) -> Gate | None:
    """Detect a permission/approval gate in captured pane text.

    Tries each pattern in :data:`GATE_PATTERNS` in declaration order; first match
    wins. Returns a :class:`Gate` with the region text + kind, or ``None`` when
    the pane shows no recognizable gate (a normal / working pane).
    """
    if not pane_text:
        return None

    lines = pane_text.strip().split("\n")
    for pattern in GATE_PATTERNS:
        region = _try_extract(lines, pattern)
        if region is not None:
            return Gate(text=region, kind=pattern.name)
    return None
