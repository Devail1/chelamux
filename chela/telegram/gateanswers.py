"""A tap on the phone → the agent's answer. **No keystroke is sent to the pane.**

This is the Telegram half of :mod:`chela.gateanswer`. The dashboard is holding the agent's
``PermissionRequest`` hook open; this side turns the taps into the ``answers`` map that
hook hands back. Between them they retire the keystroke substrate for a question:

* a **single-select** question is answered by one tap;
* a **multiSelect** question toggles (``☐``/``☑``) and commits with ``✅ Send`` — an answer
  that is a *set* has no expression at all in "move the cursor, press Enter";
* a **multi-question** run accumulates: each question's card is answered on its own, and
  the whole map is delivered only once **every** question has an answer.

That last rule is not fussiness. Measured against Claude Code 2.1.209: a **partial**
``answers`` map is accepted without complaint and the missing question is simply *dropped*
— the agent proceeds believing it asked, and never re-asks. So a half-answered gate would
silently discard a question the asker meant to ask, which is the same class of harm as
answering the wrong option. chela holds the answer until the map is complete
(:func:`chela.gateanswer.validate_answers` refuses anything less), and the cards say how
many are still outstanding.

The drafts live in memory in the ``chela telegram`` process, which is the only process that
sees the taps. Losing them costs nothing: a restart simply means the human re-taps, and the
gate itself is on disk with a deadline of its own.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

from chela import gateanswer
from chela.telegram.interactive import hook_reply_markup

log = logging.getLogger(__name__)

# A Telegram toast is a single short line; a long option label is clipped to fit.
_MAX_TOAST_LABEL = 60


@dataclass(frozen=True)
class Tap:
    """What one tap did — the toast to show, the keyboard to redraw, and whether we're done."""

    ok: bool
    toast: str
    markup: dict | None = None
    done: bool = False


@dataclass
class _Draft:
    """One gate's answers-so-far. ``committed`` is which questions are FINAL."""

    picks: dict[int, list[str]] = field(default_factory=dict)
    committed: set[int] = field(default_factory=set)


def _clip(text: str, limit: int = _MAX_TOAST_LABEL) -> str:
    flat = " ".join(str(text).split())
    return flat[: limit - 1] + "…" if len(flat) > limit else flat


def _labels(question: dict) -> list[str]:
    return [
        opt["label"] for opt in (question.get("options") or [])
        if isinstance(opt, dict) and isinstance(opt.get("label"), str)
    ]


class Drafts:
    """The taps for the gates currently blocked on this human, keyed by ``tool_use_id``.

    Keyed by the **gate**, never by the window or the topic: a tap carries the id of the
    gate it was rendered for, so a tap that arrives after that gate resolved (the agent
    moved on, the wait timed out, the agent died) finds no open gate and is **refused** —
    it can never be applied to whatever question happens to be on screen by then. That is
    CMX-32's mis-answer, and it does not get to return through a new door.
    """

    def __init__(self, open_gate=gateanswer.open_gate, submit=gateanswer.submit_answer):
        self._open_gate = open_gate
        self._submit = submit
        self._drafts: dict[str, _Draft] = {}

    def forget(self, tool_use_id: str) -> None:
        self._drafts.pop(tool_use_id, None)

    def selected(self, tool_use_id: str, question_index: int) -> set[int]:
        """Which option ordinals of a question are toggled right now — for the OTHER surface.

        A hook option button is carried on two messages: the CMX-49 card and the pane mirror
        (CMX-54). The tapped one is redrawn from the :class:`Tap` this book returns; the
        other is re-rendered on the next poll and has to ask, or its ``☑`` ticks would
        disagree with the card's and the human would not know which set they are about to
        send.
        """
        gate = self._open_gate(tool_use_id)
        if gate is None or not 0 <= question_index < len(gate.questions):
            return set()
        picked = (self._drafts.get(tool_use_id) or _Draft()).picks.get(question_index) or []
        labels = _labels(gate.questions[question_index])
        return {i for i, label in enumerate(labels) if label in picked}

    def pick(self, tool_use_id: str, question_index: int, option_index: int) -> Tap:
        """Tap option ``option_index`` of question ``question_index``.

        Single-select: that is the question's answer, final. multiSelect: it TOGGLES, and
        the question stays open until ``✅ Send``.
        """
        gate = self._open_gate(tool_use_id)
        if gate is None:
            self.forget(tool_use_id)
            return Tap(False, "⌛ Too late — that question isn't waiting any more. "
                              "Answer it in the terminal.")
        questions = gate.questions
        if not 0 <= question_index < len(questions):
            return Tap(False, "❌ That question isn't part of this gate.")
        question = questions[question_index]
        labels = _labels(question)
        if not 0 <= option_index < len(labels):
            return Tap(False, "❌ That option isn't on offer.")

        draft = self._drafts.setdefault(tool_use_id, _Draft())
        label = labels[option_index]
        multi = bool(question.get("multiSelect"))
        picks = draft.picks.setdefault(question_index, [])

        if multi:
            if label in picks:
                picks.remove(label)
            else:
                picks.append(label)
            draft.committed.discard(question_index)
            return Tap(
                True,
                f"☑ {_clip(label)}" if label in picks else f"☐ {_clip(label)}",
                markup=self._markup(gate, question_index, draft),
            )

        draft.picks[question_index] = [label]
        draft.committed.add(question_index)
        return self._advance(gate, question_index, draft, f"✓ {_clip(label)}")

    def send(self, tool_use_id: str, question_index: int) -> Tap:
        """Commit a ``multiSelect`` question's toggled set (the ``✅ Send`` button)."""
        gate = self._open_gate(tool_use_id)
        if gate is None:
            self.forget(tool_use_id)
            return Tap(False, "⌛ Too late — that question isn't waiting any more. "
                              "Answer it in the terminal.")
        draft = self._drafts.setdefault(tool_use_id, _Draft())
        picked = draft.picks.get(question_index) or []
        if not picked:
            return Tap(False, "Pick at least one option first.")
        draft.committed.add(question_index)
        return self._advance(gate, question_index, draft,
                             f"✓ {len(picked)} selected")

    # -- internals ---------------------------------------------------------

    def _markup(self, gate, question_index: int, draft: _Draft) -> dict | None:
        question = gate.questions[question_index]
        labels = _labels(question)
        chosen = {
            i for i, label in enumerate(labels)
            if label in (draft.picks.get(question_index) or [])
        }
        return hook_reply_markup(
            labels, gate.tool_use_id, question_index,
            multi_select=bool(question.get("multiSelect")), selected=chosen,
        )

    def _advance(self, gate, question_index: int, draft: _Draft, toast: str) -> Tap:
        """A question just became final — deliver the gate if that was the last one."""
        total = len(gate.questions)
        markup = self._markup(gate, question_index, draft)
        outstanding = total - len(draft.committed)
        if outstanding > 0:
            return Tap(True, f"{toast} · {outstanding} question"
                             f"{'' if outstanding == 1 else 's'} still to answer",
                       markup=markup)

        answers: dict[str, object] = {}
        for i, question in enumerate(gate.questions):
            picked = draft.picks.get(i) or []
            text = question.get("question")
            answers[str(text)] = picked if question.get("multiSelect") else picked[0]

        ok, reason = self._submit(gate.tool_use_id, answers)
        self.forget(gate.tool_use_id)
        if not ok:
            log.warning("gate %s could not be answered from Telegram: %s",
                        gate.tool_use_id, reason)
            return Tap(False, f"❌ {reason} — answer it in the terminal.", markup=markup)
        return Tap(True, "✅ Answered — the agent has it (no keystrokes sent)",
                   markup=markup, done=True)


# The one draft book of the `chela telegram` process (the only process that sees a tap).
DRAFTS = Drafts()
