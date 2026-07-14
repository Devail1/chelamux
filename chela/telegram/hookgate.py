"""The HOOK payload as the content authority for a gate — the pane only says it is live.

A gate is rendered *from a TUI render*, and that is the bug this module exists to end.
Hit live 2026-07-14: a 3-question ``AskUserQuestion`` whose options carried ``preview``
boxes reached the phone as the question text and a bare nav row — **zero options, zero
descriptions, zero previews, nothing to tap** — while the event log already held, in
``hook.pre_tool_use``, the complete ``tool_input``: every question, every option's
``label``, ``description`` and ``preview``. The data was in the log before the message
ever reached the phone; the relay threw it away and re-derived a worse copy from pixels.

**The fix is not a better regex.** :mod:`chela.telegram.panescan` measured its option
patterns against *a* selector shape — single question, single select, options at the head
of their line. A **multi-question** selector draws a tab strip; a selector whose options
carry a ``preview`` switches the TUI to a side-by-side layout (options left, preview box
right) and the option rows stop starting their line. Either one alone trips the
documented "or an otherwise unparseable one" fallback. The scraper will keep meeting
shapes it was not measured against. The hook will not: it is handed the structure.

So the split is:

* **the hook payload is the CONTENT** — what the question is, what the options are, what
  they mean (:func:`pending_gate`);
* **the pane is the LIVENESS** — a ``pre_tool_use`` with no ``post_tool_use`` could also
  mean the agent *died* at the gate, so :class:`~chela.telegram.gatewatch.PermissionGateWatcher`
  only renders a payload for a window whose pane is showing a selector *right now*. It
  also stays the whole content source for a **pre-plugin** agent (hooks are read at agent
  startup, so a fleet launched before the plugin was installed emits none).

**``PreToolUse``, not ``PermissionRequest``.** Both carry the full ``tool_input``, and
``PermissionRequest`` is the one that fires while the agent is blocked — but it carries
**no ``tool_use_id``** (measured, Claude Code 2.1.207), so there is nothing to pair a
resolution against. ``PreToolUse`` has one, and ``PostToolUse`` echoes it, which is what
makes "still pending" a *fact* rather than a guess.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

from chela import event_log

log = logging.getLogger(__name__)

# The tools that BLOCK on a human. `AskUserQuestion` / `ExitPlanMode` are not hook
# events — they are tools, and they arrive as PreToolUse / PermissionRequest carrying
# `tool_name` + the full `tool_input` (measured on Claude Code 2.1.207).
INTERACTIVE_TOOLS: tuple[str, ...] = ("AskUserQuestion", "ExitPlanMode")

_PRE = "hook.pre_tool_use"
_POST = "hook.post_tool_use"


@dataclass(frozen=True)
class Option:
    """One option of an ``AskUserQuestion`` question, as the *asker* wrote it.

    ``preview`` is the block of (often box-drawing) text Claude Code renders beside the
    option list — the thing whose mere presence re-laid-out the TUI and broke the scrape.
    It is **optional and usually absent**: an empty preview must render nothing at all,
    not an empty code block.
    """

    label: str
    description: str = ""
    preview: str = ""


@dataclass(frozen=True)
class Question:
    """One question of an ``AskUserQuestion`` call. ``multi_select`` takes a LIST of labels."""

    question: str
    header: str = ""
    multi_select: bool = False
    options: tuple[Option, ...] = ()


@dataclass(frozen=True)
class HookGate:
    """An interactive tool call that the log says is still waiting on a human.

    ``tool_use_id`` is the gate's identity — the key the eventual ``PostToolUse``
    resolves it by, and (next slice) the key an answer must be matched against so a
    stale tap cannot be applied to whatever is on screen by then.
    """

    tool_use_id: str
    tool: str
    questions: tuple[Question, ...]
    seq: int


def _text(value) -> str:
    return value if isinstance(value, str) else ""


def parse_questions(tool_input) -> tuple[Question, ...]:
    """``tool_input`` → the questions, defensively.

    A payload shape we do not recognise yields ``()`` and the caller falls back to the
    pane — the same direction every other unknown in this subsystem fails.
    """
    if not isinstance(tool_input, dict):
        return ()
    raw = tool_input.get("questions")
    if not isinstance(raw, list):
        return ()
    questions: list[Question] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        options: list[Option] = []
        for opt in item.get("options") or []:
            if isinstance(opt, dict) and _text(opt.get("label")):
                options.append(Option(
                    label=_text(opt.get("label")),
                    description=_text(opt.get("description")),
                    preview=_text(opt.get("preview")),
                ))
        questions.append(Question(
            question=_text(item.get("question")),
            header=_text(item.get("header")),
            multi_select=bool(item.get("multiSelect")),
            options=tuple(options),
        ))
    return tuple(questions)


def pending_gate(wid: str, *, read=event_log.read) -> HookGate | None:
    """The window's most recent **unresolved** interactive tool call, or None.

    **"Pending" is defined as: a ``hook.pre_tool_use`` for an interactive tool
    (:data:`INTERACTIVE_TOOLS`) with no ``hook.post_tool_use`` bearing the same
    ``tool_use_id``.** That is the whole definition, and it is why the content is read
    from ``PreToolUse`` rather than from the ``PermissionRequest`` that fires at the same
    moment: ``PermissionRequest`` carries no ``tool_use_id``, so it can be paired with
    nothing and can never be known to be *over*.

    **Scoped to the current ``boot_id``.** Events written before PR #61 were correlated
    on the session's ``cwd`` and are filed against the **wrong window** (CMX-48) — a gate
    resolved against one of those would be posted to a topic belonging to a different
    agent. Every one of them predates the current epoch, so honouring only the current
    ``boot_id`` excludes them without a seq boundary to hand-maintain. The cost is that a
    gate left pending across a daemon restart is not resolvable from the log — the pane
    fallback covers it, which is exactly what the fallback is for.

    **Pending is NOT the same as on-screen.** A ``pre_tool_use`` with no ``post_tool_use``
    also describes an agent that *died* at the gate. This function only reads the log; the
    caller must corroborate against the pane before it posts anything (see
    :class:`~chela.telegram.gatewatch.PermissionGateWatcher._poll_window`) — the
    false-``DIED`` bug (CMX-35) is the same mistake, made from the other direction.
    """
    try:
        batch = read(types=[_PRE, _POST], wid=wid)
    except Exception:  # noqa: BLE001 — a lookup failure must never cost us the pane relay
        log.debug("hookgate: event-log read failed for %s", wid, exc_info=True)
        return None

    boot = batch.get("boot_id")
    events = [e for e in (batch.get("events") or []) if e.get("boot_id") == boot]

    resolved: set[str] = set()
    for event in events:
        if event.get("type") == _POST:
            tuid = (event.get("payload") or {}).get("tool_use_id")
            if isinstance(tuid, str):
                resolved.add(tuid)

    for event in reversed(events):
        if event.get("type") != _PRE:
            continue
        payload = event.get("payload") or {}
        tool = payload.get("tool_name")
        tuid = payload.get("tool_use_id")
        if tool not in INTERACTIVE_TOOLS or not isinstance(tuid, str) or tuid in resolved:
            continue
        return HookGate(
            tool_use_id=tuid,
            tool=str(tool),
            questions=parse_questions(payload.get("tool_input")),
            seq=int(event.get("seq") or 0),
        )
    return None
