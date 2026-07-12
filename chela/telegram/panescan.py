"""Pane-scrape detectors for the live-TUI prompts — the one TUI-regex module.

Three kinds of blocked-agent prompt are rendered only in Claude Code's live TUI
and must be read off the tmux pane rather than the JSONL transcript:

* a tool **permission gate** ("Do you want to proceed?" for a Bash/Edit) — never
  in the transcript at all (:func:`detect_permission_gate`);
* an **AskUserQuestion** selector — measured live to land in the transcript only
  once the question is *answered* (the ``tool_use`` record is appended at
  answer-time, not while the selector is pending), so a transcript-triggered relay
  can never show the buttons while the question is still answerable. The pane shows
  it live, so detection comes from the pane (:func:`detect_askuserquestion`);
* an **ExitPlanMode** plan-approval selector ("Would you like to proceed?" with
  the Yes-auto / Yes-manual / No choices) — same story as AskUserQuestion: its
  ``tool_use`` lands only at answer-time, so the approve / keep-planning buttons
  are detected from the pane too (:func:`detect_exitplanmode`).

This module is the **sole home** for the Claude-Code TUI regexes — the "signatures
table". Keeping every such pattern here means a Claude Code version bump that
reworded a prompt is a one-file edit. The permission/bash-approval **and**
AskUserQuestion patterns are ported from six-ddc/ccbot's ``terminal_parser.py``
(https://github.com/six-ddc/ccbot, MIT); the broader status-line parsing there is
deliberately NOT ported. See the top-level NOTICE file for upstream attribution.

Public API: :func:`detect_permission_gate` returns a :class:`Gate` when the pane
shows a permission/bash-approval prompt; :func:`detect_askuserquestion` returns an
:class:`AskUQ` when the pane shows an AskUserQuestion selector;
:func:`detect_exitplanmode` returns an :class:`ExitPlan` when the pane shows a
plan-approval selector; all return ``None`` for a normal / working pane.
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


# ── AskUserQuestion selector ─────────────────────────────────────────────
#
# Measured against Claude Code 2.1.207. A single-select, single-question selector
# renders (leading spaces significant)::
#
#      ☐ Fruit                       <- checkbox + question HEADER
#
#     Which fruit do you prefer?     <- the question text
#
#     ❯ 1. Apple                     <- options; ❯ marks the cursor
#          A crisp red fruit         <- descriptions (indented, no number)
#       2. Banana
#          A soft yellow fruit
#       3. Cherry
#       4. Type something.           <- free-text meta-row (not a real option)
#     ─────
#       5. Chat about this           <- chat meta-row (not a real option)
#
#     Enter to select · ↑/↓ to navigate · Esc to cancel
#
# A multi-SELECT or MULTI-question selector instead renders a tab strip
# (``←  ☐ Fruits  ✔ Submit  →``) and ``[ ]`` checkboxes per option — the MVP
# treats both as the fallback shape (question text + nav row only, no semantic
# option buttons), so it is enough to detect the ``←  ☐`` tab strip.
_RE_ENTER_SELECT = re.compile(r"^\s*Enter to select")
_RE_MULTI_TAB = re.compile(r"^\s*←\s+[☐✔☒]")  # multi-tab / multi-select strip
_RE_SINGLE_HEAD = re.compile(r"^\s*[☐✔☒]")  # single-question checkbox header
# An option row: an optional ❯ cursor, the 1-based number, then the label. The
# indented description lines carry no ``N.`` so they never match.
_RE_OPTION = re.compile(r"^\s*(❯)?\s*(\d+)\.\s+(.*\S)\s*$")
# The trailing rows Claude Code always appends (free-text + chat escape hatches);
# they are navigable but are NOT real options, so they never get a semantic button.
_RE_META = re.compile(r"^(type something\.?|chat about this|submit)$", re.IGNORECASE)


@dataclass(frozen=True)
class AskUQ:
    """A detected AskUserQuestion selector.

    ``question`` is the scraped question text. ``options`` are the real option
    labels in display order (the free-text / chat meta-rows excluded); it is empty
    for the ``multi`` fallback shape. ``cursor`` is the 0-based ordinal of the
    ``❯``-marked row among ALL navigable numbered rows (semantic + meta) — so a
    tap on semantic option ``i`` (which occupies ordinal ``i``, options being the
    first rows) injects ``i - cursor`` Down/Up presses; it is ``-1`` when no cursor
    row was found. ``multi`` is True for a multi-tab / multi-select selector (or an
    otherwise unparseable one): the relay then offers the nav row only.
    """

    question: str
    options: tuple[str, ...]
    cursor: int
    multi: bool


def _first_match(lines: list[str], pattern: re.Pattern[str]) -> int | None:
    for i, line in enumerate(lines):
        if pattern.match(line):
            return i
    return None


def _extract_question(lines: list[str], head_idx: int, enter_idx: int) -> str:
    """The question text between the checkbox header and the first option row.

    Joins the non-blank lines after ``head_idx`` up to the first numbered option
    (handles a wrapped question); falls back to the header label (the checkbox /
    tab glyphs stripped) when there is nothing between them.
    """
    collected: list[str] = []
    for line in lines[head_idx + 1 : enter_idx]:
        if _RE_OPTION.match(line):
            break
        stripped = line.strip()
        if stripped:
            collected.append(stripped)
    if collected:
        return " ".join(collected)
    return lines[head_idx].strip().lstrip("←☐✔☒→ ").strip()


def _parse_options(
    lines: list[str], head_idx: int, enter_idx: int
) -> tuple[list[str], int]:
    """The real option labels (meta-rows excluded) + the cursor's row ordinal.

    ``cursor`` counts ALL numbered rows (semantic + meta) in display order, so the
    ordinal lines up with one Down/Up press per row for cursor-relative injection.
    """
    options: list[str] = []
    cursor = -1
    ordinal = 0
    for line in lines[head_idx + 1 : enter_idx]:
        m = _RE_OPTION.match(line)
        if not m:
            continue
        label = m.group(3).strip()
        if m.group(1):  # ❯ cursor on this row
            cursor = ordinal
        if not _RE_META.match(label):
            options.append(label)
        ordinal += 1
    return options, cursor


def detect_askuserquestion(pane_text: str) -> AskUQ | None:
    """Detect an AskUserQuestion selector in captured pane text.

    Requires the ``Enter to select`` footer plus a checkbox header (single ``☐`` or
    the ``←  ☐`` multi-tab strip) above it — so it never fires on a permission
    prompt or a normal pane. A multi-tab / multi-select selector returns the
    fallback shape (``options == ()``, ``multi is True``); a simple single-select
    returns the ordered option labels and the cursor ordinal. Returns ``None`` when
    the pane shows no selector.
    """
    if not pane_text:
        return None

    lines = pane_text.split("\n")
    enter_idx = _first_match(lines, _RE_ENTER_SELECT)
    if enter_idx is None:
        return None

    # The checkbox header closest above the footer is this selector's header.
    head_idx: int | None = None
    multi = False
    for i in range(enter_idx - 1, -1, -1):
        if _RE_MULTI_TAB.match(lines[i]):
            head_idx, multi = i, True
            break
        if _RE_SINGLE_HEAD.match(lines[i]):
            head_idx, multi = i, False
            break
    if head_idx is None:
        return None

    question = _extract_question(lines, head_idx, enter_idx)
    if multi:
        return AskUQ(question=question, options=(), cursor=-1, multi=True)

    options, cursor = _parse_options(lines, head_idx, enter_idx)
    if not options:
        # A single-question selector with no parseable options (unexpected) still
        # gets the nav row so the human is never handed a broken keyboard.
        return AskUQ(question=question, options=(), cursor=-1, multi=True)
    return AskUQ(question=question, options=tuple(options), cursor=cursor, multi=False)


# ── ExitPlanMode plan-approval selector (Slice B2) ───────────────────────────
#
# Like AskUserQuestion, ExitPlanMode's ``tool_use`` was measured to land in the
# transcript only *after* the plan is resolved, so the approve/keep-planning
# buttons (Slice B / CMX-20) were useless attached to the post-answer transcript
# record. The plan-approval prompt is a **live pane** UI, so it is detected here
# instead. A single-select prompt renders (leading spaces significant)::
#
#     ● Here is my plan:
#         1. Do the first thing
#         2. Do the second thing
#
#      Would you like to proceed?            <- the proceed prompt (anchor)
#      ❯ 1. Yes, and auto-accept edits        <- options (❯ marks the default)
#        2. Yes, and manually approve edits
#        3. No, keep planning
#
#      Esc to cancel                          <- bottom marker (confirms live UI)
#
# The plan text the human must read is everything **above** the proceed prompt;
# the options below are represented by the inline keyboard, so they are not part
# of the relayed body. The proceed-prompt + bottom markers are ported from
# six-ddc/ccbot's ``terminal_parser.py`` ExitPlanMode ``UIPattern`` (top /
# bottom); the ``Claude has written up a plan`` alternative is the v2.1.29+
# wording where the prompt wraps. See the top-level NOTICE for attribution.
_RE_PLAN_TOP = (
    re.compile(r"^\s*Would you like to proceed\?"),
    re.compile(r"^\s*Claude has written up a plan"),
)
_RE_PLAN_BOTTOM = (
    re.compile(r"^\s*ctrl-g to edit in "),
    re.compile(r"^\s*Esc to (cancel|exit)"),
)
_PLAN_MIN_GAP = 2  # min lines from the proceed prompt to the bottom marker


@dataclass(frozen=True)
class ExitPlan:
    """A detected ExitPlanMode plan-approval selector.

    ``text`` is the scraped plan region — the lines shown *above* the proceed
    prompt, which is what the human reads to decide. It may be empty when the plan
    has scrolled off the visible pane (the relay then posts a generic prompt); the
    approve / keep-planning buttons are option-count-independent, so they attach
    regardless.
    """

    text: str


def detect_exitplanmode(pane_text: str) -> ExitPlan | None:
    """Detect an ExitPlanMode plan-approval selector in captured pane text.

    Requires the ``Would you like to proceed?`` prompt (or the wrapped
    ``Claude has written up a plan`` variant) followed by a ``ctrl-g``/``Esc``
    footer — so it never fires on a permission prompt (``Do you want to
    proceed?``), an AskUserQuestion selector (no such prompt), or a normal pane.
    Returns an :class:`ExitPlan` carrying the plan region scraped from *above* the
    prompt, or ``None`` when the pane shows no plan-approval selector.
    """
    if not pane_text:
        return None

    lines = pane_text.split("\n")

    proceed_idx: int | None = None
    for i, line in enumerate(lines):
        if any(p.search(line) for p in _RE_PLAN_TOP):
            proceed_idx = i
            break
    if proceed_idx is None:
        return None

    bottom_idx: int | None = None
    for i in range(proceed_idx + 1, len(lines)):
        if any(p.search(lines[i]) for p in _RE_PLAN_BOTTOM):
            bottom_idx = i
            break
    if bottom_idx is None or bottom_idx - proceed_idx < _PLAN_MIN_GAP:
        return None

    plan = _shorten_separators("\n".join(lines[:proceed_idx])).strip()
    return ExitPlan(text=plan)
