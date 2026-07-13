"""Pane-scrape detectors for the live-TUI prompts — the one TUI-regex module.

Three kinds of blocked-agent prompt are rendered only in Claude Code's live TUI
and must be read off the tmux pane rather than the JSONL transcript:

* a tool **permission gate** ("Do you want to proceed?" for a Bash/Edit) — never
  in the transcript at all (:func:`detect_permission_gate`). Its *identity* (which
  command it wants to run) is scraped from the dialog too
  (:func:`scrape_gate_identity`): the gated call's ``tool_use`` is appended to the
  JSONL only once the human answers, so during the gate the pane is the one place
  the command exists;
* an **AskUserQuestion** selector — measured live to land in the transcript only
  once the question is *answered* (the ``tool_use`` record is appended at
  answer-time, not while the selector is pending), so a transcript-triggered relay
  can never show the buttons while the question is still answerable. The pane shows
  it live, so detection comes from the pane (:func:`detect_askuserquestion`);
* an **ExitPlanMode** plan-approval selector ("Would you like to proceed?" with
  the Yes-auto / Yes-manual / No choices) — same story as AskUserQuestion: its
  ``tool_use`` lands only at answer-time, so the approve / keep-planning buttons
  are detected from the pane too (:func:`detect_exitplanmode`).

A fourth thing is read off the same pane, and for the same reason: the **status
line** — Claude Code's live "working" verb (``✻ Cerebrating… (2m 45s · ↓ 12.0k
tokens)``) plus the background-shell count. It is a TUI *render*, not an event:
there is no ``Boondoggling`` hook and there never will be, so unlike the gates
(which hooks may eventually supersede) the pane is the correct source here
permanently (:func:`detect_status`).

This module is the **sole home** for the Claude-Code TUI regexes — the "signatures
table". Keeping every such pattern here means a Claude Code version bump that
reworded a prompt is a one-file edit. The permission/bash-approval, AskUserQuestion
**and status-line** patterns are ported from six-ddc/ccbot's ``terminal_parser.py``
(https://github.com/six-ddc/ccbot, MIT). See the top-level NOTICE file for upstream
attribution.

Public API: :func:`detect_permission_gate` returns a :class:`Gate` when the pane
shows a permission/bash-approval prompt; :func:`detect_askuserquestion` returns an
:class:`AskUQ` when the pane shows an AskUserQuestion selector;
:func:`detect_exitplanmode` returns an :class:`ExitPlan` when the pane shows a
plan-approval selector — all return ``None`` for a normal / working pane; and
:func:`detect_status` returns a :class:`Status` when the pane shows a *working*
agent, ``None`` when it is idle.
"""
from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class Gate:
    """A detected permission/approval gate.

    ``text`` is the extracted pane region (top→bottom marker, inclusive), used as
    the relay fallback when no identity could be scraped. ``kind`` is the matching
    pattern's tag (``"PermissionPrompt"`` / ``"BashApproval"``).

    ``tool``/``detail`` are the gate's identity scraped from the pane (``"Bash"`` /
    ``"rm -rf build/"``, ``"Edit"`` / ``"src/app.py"``) — see
    :func:`scrape_gate_identity`. The pane is the only place they exist while the
    gate is pending: Claude Code appends the gated call's ``tool_use`` to the
    transcript only once the human answers, so a transcript lookup during the gate
    finds nothing (or, worse, some *other* still-unpaired call).
    """

    text: str
    kind: str
    tool: str | None = None
    detail: str | None = None


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


# ── The gate's identity (what is it asking permission FOR) ───────────────
#
# Claude Code heads a permission dialog with the call it wants to make, above the
# "Do you want to proceed?" line::
#
#      Bash command
#        rm -rf build/
#        Remove the build directory
#
#      Do you want to proceed?
#
# The header names the tool and the indented block under it is the command (a
# multi-line command keeps rendering until the blank line before the prompt; the
# last line is the human-readable description Claude wrote, which we keep — it is
# usually one short line and it is what makes the relayed gate readable).
#
# The file tools head their dialog the same way ("Edit file" / "Create file" …);
# when there is no header at all, the prompt itself names the file ("Do you want
# to make this edit to app.py?").
_RE_GATE_HEADER = re.compile(r"^\s*(Bash) command\s*$|^\s*(Edit|Create|Write|Read) file\s*$")
_RE_GATE_FILE_PROMPT = re.compile(
    r"^\s*Do you want to (?:make this edit to|create|delete)\s+(\S+?)\??\s*$"
)
# Where the identity block ends — the prompt line that follows it.
_RE_GATE_PROMPT = re.compile(r"^\s*(Do you want to|This command requires approval)")


def scrape_gate_identity(pane_text: str) -> tuple[str | None, str | None]:
    """The ``(tool, detail)`` a permission gate is asking about, scraped from the pane.

    Reads the dialog header ("Bash command" → ``("Bash", "rm -rf build/")``,
    "Edit file" → ``("Edit", "src/app.py")``) or, absent a header, the file named
    in the prompt itself ("Do you want to make this edit to app.py?"). Either half
    may be ``None`` — the caller then falls back to the scraped region text, so an
    unrecognised dialog still relays *something* a human can act on.
    """
    lines = pane_text.split("\n")
    for i, line in enumerate(lines):
        m = _RE_GATE_HEADER.match(line)
        if not m:
            continue
        tool = m.group(1) or m.group(2)
        body: list[str] = []
        for nxt in lines[i + 1 :]:
            if _RE_GATE_PROMPT.match(nxt):
                break
            stripped = nxt.strip()
            if stripped:
                body.append(stripped)
        return tool, " · ".join(body) or None

    for line in lines:
        m = _RE_GATE_FILE_PROMPT.match(line)
        if m:
            return "Edit", m.group(1)
    return None, None


def detect_permission_gate(pane_text: str) -> Gate | None:
    """Detect a permission/approval gate in captured pane text.

    Tries each pattern in :data:`GATE_PATTERNS` in declaration order; first match
    wins. Returns a :class:`Gate` with the region text + kind + the scraped
    identity (:func:`scrape_gate_identity`), or ``None`` when the pane shows no
    recognizable gate (a normal / working pane).
    """
    if not pane_text:
        return None

    lines = pane_text.strip().split("\n")
    for pattern in GATE_PATTERNS:
        region = _try_extract(lines, pattern)
        if region is not None:
            tool, detail = scrape_gate_identity(pane_text)
            return Gate(text=region, kind=pattern.name, tool=tool, detail=detail)
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
# The horizontal rule Claude Code draws above the "Chat about this" row — it sits
# between option rows, so it must not be mistaken for a description line.
_RE_RULE = re.compile(r"^[\s─—-]*$")


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

    ``descriptions`` is positionally parallel to ``options`` — the indented prose
    Claude Code draws under each option, ``""`` where an option has none. The TUI
    renders **every** option's description, not just the cursor-focused one
    (measured on Claude Code 2.1.207 with a 4-option, long-description selector),
    so this is real scraped text: nothing here is inferred or synthesized. Note a
    label too long for the pane width wraps onto the same indented continuation
    lines, so it folds into the description — which reads correctly, since the
    relay prints the description directly under its option.
    """

    question: str
    options: tuple[str, ...]
    cursor: int
    multi: bool
    descriptions: tuple[str, ...] = ()


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
) -> tuple[list[str], list[str], int]:
    """The real option labels + their descriptions + the cursor's row ordinal.

    Meta-rows (free-text / chat) are excluded from the labels, so ``options`` holds
    only the pickable options; ``descriptions`` is positionally parallel to it (the
    indented prose under each option, ``""`` when it has none). ``cursor`` counts
    ALL numbered rows (semantic + meta) in display order, so the ordinal lines up
    with one Down/Up press per row for cursor-relative injection.
    """
    options: list[str] = []
    descriptions: list[str] = []
    cursor = -1
    ordinal = 0
    collecting: list[str] | None = None  # the current option's description lines
    for line in lines[head_idx + 1 : enter_idx]:
        m = _RE_OPTION.match(line)
        if m:
            label = m.group(3).strip()
            if m.group(1):  # ❯ cursor on this row
                cursor = ordinal
            if _RE_META.match(label):
                collecting = None  # a meta-row's text is not a description
            else:
                options.append(label)
                descriptions.append("")
                collecting = []
            ordinal += 1
            continue
        if collecting is None or _RE_RULE.match(line):
            continue
        stripped = line.strip()
        if stripped:
            collecting.append(stripped)
            descriptions[-1] = " ".join(collecting)
    return options, descriptions, cursor


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

    options, descriptions, cursor = _parse_options(lines, head_idx, enter_idx)
    if not options:
        # A single-question selector with no parseable options (unexpected) still
        # gets the nav row so the human is never handed a broken keyboard.
        return AskUQ(question=question, options=(), cursor=-1, multi=True)
    return AskUQ(
        question=question,
        options=tuple(options),
        cursor=cursor,
        multi=False,
        descriptions=tuple(descriptions),
    )


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


# ── Status line — the live "Claude is working" verb ─────────────────────────

# The glyphs Claude Code cycles through at the head of its status line (measured
# on 2.1.207: the same frame animates `·` → `✶` → `✽` → `✻` several times a
# second). Ported from ccbot's ``STATUS_SPINNERS``.
STATUS_SPINNERS = frozenset("·✻✽✶✳✢")

# How far above / below the chrome separator the status line and the mode line
# can sit (blank spacer rows in between), and how far up the pane to look for the
# separator itself.
_CHROME_SEARCH = 10
_STATUS_LOOKBACK = 4
_CHROME_RULE_MIN = 20

# The shell counter, which on 2.1.207 is NOT part of the status line at all: it
# is a segment of the **mode line**, the last row of the bottom chrome —
# ``⏵⏵ auto mode on · 2 shells · ← for agents``. Anchored to a leading ``·`` (or
# the start of the segment) so Claude's own prose — "Ran 4 shell commands" — can
# never match; it is read only from the rows BELOW the separator, which is chrome
# by construction, so the body cannot reach it either.
_RE_SHELLS = re.compile(r"(?:^|·)\s*(\d+)\s+shells?\b")

# ⚠️ THE TRAP, and the reason this detector is an ALLOWLIST. When a turn ENDS,
# Claude Code does not clear the status slot — it rewrites it in the **past tense**,
# behind the **same spinner glyph**, and leaves it there:
#
#     ✻ Cerebrating… (2m 45s · ↓ 12.0k tokens)      <- WORKING
#     ✽ Razzmatazzing… (27s · ↓ 1.3k tokens)        <- WORKING (the verb is random)
#     ✻ Worked for 1m 17s · 1 shell still running   <- the turn is OVER…
#     ✻ Churned for 2m 31s                          <- …and so is this one
#
# Both shapes sit in the same slot, at column 0, behind the same glyph — so a
# spinner-anchored parse matches both, "delete when the status line leaves the
# pane" NEVER fires (it never leaves), and the relay is left showing a frozen
# "Worked for 1m 17s" forever. That would defeat the entire point of an *ephemeral*
# message, so the past-tense summary must resolve to None (→ poof), exactly like an
# idle pane. Absence of the line is NOT the end-of-turn signal.
#
# The discriminator is NOT the verb — both verbs are drawn from an open-ended
# whimsical set ("Worked", "Churned", …), so blocklisting them is whack-a-mole. It
# is the **ellipsis**: a live verb is always rendered mid-action, ``Cerebrating…``,
# and a finished summary never is. Keying on the ellipsis is an allowlist, so an
# unrecognised line fails CLOSED — treated as "not working", i.e. poofed — which is
# the safe direction: a missing status message costs a little live-ness, a sticky
# one is a lie that never goes away. Measured on Claude Code 2.1.207.
_STATUS_ACTIVE = "…"

# The settled line's turn duration — "Worked for 1m 17s", "Churned for 2m 31s".
# Read only from the settled shape, where it means exactly one thing (how long the
# finished turn took); the ACTIVE line's parenthetical can carry a second, unrelated
# time ("… · thought for 1s") that would corrupt a naive sum.
_RE_DUR = re.compile(r"(\d+)\s*([hms])\b")
_DUR_UNITS = {"h": 3600, "m": 60, "s": 1}


def _duration_seconds(text: str) -> int | None:
    """Total seconds in a "1m 17s" / "2m 31s" / "45s" duration, or None."""
    total: int | None = None
    for n, unit in _RE_DUR.findall(text):
        total = (total or 0) + int(n) * _DUR_UNITS[unit]
    return total


@dataclass(frozen=True)
class Status:
    """The status line of an agent — either working, or settled.

    ``verb`` is the text after the spinner glyph. ``shells`` is the number of
    background shells running, or None when none are.

    ``active`` is the distinction that drives the whole relay lifecycle:

    * **active** (``✻ Cerebrating… (2m 45s · ↓ 12.0k tokens)``) — the turn is in
      flight. Pure liveness: worthless the moment it stops being true, so the relay
      ticks it and then stops.
    * **settled** (``✻ Worked for 1m 17s · 1 shell still running``) — the turn is
      OVER, and Claude leaves this line sitting in the same slot behind the same
      glyph. It is not noise: ``seconds`` is how long the turn took and ``shells``
      is background work that **outlived the turn** — a live warning, not a receipt.

    ``seconds`` is the settled turn's duration; it is None for an active status (the
    active line's parenthetical carries an elapsed time too, but also sometimes an
    unrelated one — "thought for 1s" — so it is not read).

    :func:`detect_status` returns None only when there is **no** status line at all.
    """

    verb: str
    shells: int | None = None
    active: bool = True
    seconds: int | None = None


def detect_status(pane_text: str) -> Status | None:
    """Scrape the working verb (+ background shell count) from a live pane.

    **The anchoring is the whole trick.** Claude's ordinary output is full of ``·``
    bullets — this very docstring would match a naive "line starts with a spinner"
    grep — so the status line is found *positionally* instead: locate the **chrome
    separator** (the run of ``─`` that closes the scrollback and opens the prompt
    box) in the last few rows, then read the first non-blank line **above** it. If
    that line does not open with a spinner glyph, there is no status line and we
    return None rather than searching further up into the transcript body.

    The shell count is a second, equally cheap read of the **same** captured text:
    on Claude Code 2.1.207 it is not in the status line but in the mode line below
    the chrome (measured, not assumed — see :data:`_RE_SHELLS`). It rides along for
    the price of one regex over four rows, and it is half of what a phone actually
    wants to know, so it is parsed here rather than dropped.

    Returns None for an idle pane, for a pane whose visible text merely *contains*
    bullets, for a pane with no chrome at all (a scrolled-back or non-Claude
    window) — and, critically, for the **past-tense summary a finished turn leaves
    behind** (:data:`_RE_STATUS_DONE`), which wears the same spinner glyph in the
    same slot and never goes away. Never a guess.
    """
    if not pane_text:
        return None

    lines = pane_text.split("\n")

    # The chrome separator: the topmost ──── rule in the last few rows. (Claude
    # draws two — above and below the prompt box — and the status line sits above
    # the first, so the topmost is the one to anchor on.)
    chrome_idx: int | None = None
    for i in range(max(0, len(lines) - _CHROME_SEARCH), len(lines)):
        stripped = lines[i].strip()
        if len(stripped) >= _CHROME_RULE_MIN and all(c == "─" for c in stripped):
            chrome_idx = i
            break
    if chrome_idx is None:
        return None  # no chrome visible → we cannot say anything about the status

    verb: str | None = None
    for i in range(chrome_idx - 1, max(chrome_idx - 1 - _STATUS_LOOKBACK, -1), -1):
        line = lines[i]
        if not line.strip():
            continue
        # The first non-blank row above the chrome is either a status line or the
        # tail of the transcript. The spinner must sit at **column 0**: Claude
        # gutter-indents every line of its own output, so an indented "· a bullet"
        # — which its prose is full of, and which lands in exactly this position
        # whenever a turn's last line is a bullet — is body text, not a status.
        # Don't search further up: that is how a stale spinner line from an earlier
        # turn gets mistaken for a live one.
        if line[0] in STATUS_SPINNERS:
            verb = line[1:].strip()
        break
    if not verb:
        return None

    # Working, or the settled summary of a turn that is already over? See
    # _STATUS_ACTIVE — the ellipsis is the discriminator, and it is an allowlist,
    # so an unrecognised shape settles rather than ticking forever.
    active = _STATUS_ACTIVE in verb

    # The shell count lives in the mode line (below the chrome) while a turn runs,
    # and in the settled line itself once it ends ("· 1 shell still running") — so
    # look in both, the status line first: after the turn it is the authority on
    # what is *still* running.
    shells: int | None = None
    for line in [verb, *lines[chrome_idx:]]:
        m = _RE_SHELLS.search(line)
        if m:
            shells = int(m.group(1))
            break

    seconds = None if active else _duration_seconds(verb)
    return Status(verb=verb, shells=shells, active=active, seconds=seconds)
