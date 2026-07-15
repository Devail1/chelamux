"""🧑‍⚖️ THE CRITIC — advisory brief-review upstream of the dispatcher (persona-pattern step 3).

The judge (``chela/judge.py``) reviews *code* on ``awaiting_review``; the CRITIC reviews the
*brief* the moment a task is picked for dispatch — "plan review is the new linter". It is
[`docs/PERSONA_PATTERN.md`](../docs/PERSONA_PATTERN.md) applied a third time: **mechanical
facts computed in code, judgment proposed by the LLM.**

⛔ v1 IS DELIBERATELY THE LOWEST-RISK SLICE, and both halves of that are ENFORCED here, not
promised:

* **ADVISORY-ONLY.** The critic decides NOTHING about a dispatch — it never blocks it, never
  delays it, never changes it. It runs *after* the agent is already launched, its output is a
  note on the run row, and its caller (:func:`chela.dispatcher._run_critic`) swallows every
  failure. This mirrors the judge's founding property ("the reviewing agent decides
  NOTHING"): there, a wrong opinion cannot cause a wrong BLOCK because blocking needs a
  mechanically-verified fact; here, a wrong opinion — or an outright crash — cannot cost a
  dispatch, because the dispatch has already happened and nothing the critic returns is read
  back into it. The worst a broken critic can do is leave a run without its advisory note.
* **BRIEFS-ONLY.** The critic fires on a *brief* (the rendered prompt an agent receives at
  dispatch). It has NO PR trigger — a fresh PR is the judge's slot, not the critic's.

THE SPLIT, exactly as the persona doc frames it:

* **Code computes the FACTS.** Does the brief carry the four mandatory fields an agent needs
  to work without guessing — an *objective*, its *boundaries*, its *guardrails*, and how to
  *verify* it? Each is detected mechanically (a signal matches the text or it does not — a
  fact, in the same sense a failing check is a fact), and per the persona doc these facts
  *could* gate. ⛔ In v1 they do NOT: the presence-of-a-field is surfaced as an advisory note
  only. The gate is a later slice, once the advisory has earned trust.
* **The LLM proposes the JUDGMENT.** "Is this the right work? Is it scoped well? Does it read
  awkwardly?" — design / necessity / scope opinions. That half is advisory by nature (a wrong
  opinion costs a glance, not a rework round) and plugs into this same non-gating surface; it
  is the next increment and is intentionally NOT wired in v1, which keeps the lowest-risk
  slice free of any new agent-spawn surface.

The whole reason the judgment half is *allowed* to be fallible is the same reason the judge's
notes are: an advisory that is wrong is cheap. The moment any of this is allowed to GATE, only
the mechanical facts may — never an opinion. That invariant is inherited from the judge and
must survive into every later slice.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

# The four fields a dispatchable brief should carry. An agent handed a brief that names its
# objective, states its boundaries, spells out its guardrails, and says how to verify the
# result is one that can work without guessing — and guessing is what spends an agent on the
# wrong thing. The ORDER is the order a reader wants them in; it is also the order
# :func:`advisory_body` lists what is missing.
FIELDS: tuple[str, ...] = ("objective", "boundaries", "guardrails", "verify")

# Mechanical signals per field. A field is PRESENT when the brief text matches ANY of its
# signals (case-insensitive). This is deliberately a keyword/heading match, not an LLM read:
# presence-of-a-signal is a FACT the same way the judge's "the suite went red" is a fact —
# the regex matches or it does not — which is the whole reason it is safe to surface without
# a model's opinion entering. A false negative (a brief that says what to do without using any
# of these words) only ever produces a NOTE, never a gate, so an imperfect detector is a
# cheap wrong-glance, exactly the cost the advisory-only design is built to absorb.
_SIGNALS: dict[str, tuple[str, ...]] = {
    "objective": (r"\bobjective\b", r"\bgoal\b", r"##\s*your task\b", r"\byour task\b",
                  r"\bimplement\b"),
    "boundaries": (r"\bboundaries\b", r"\bin scope\b", r"\bout of scope\b", r"\bscope\b",
                   r"\bconstraint"),
    "guardrails": (r"\bguardrail", r"\bguard\b", r"\bdo not\b", r"\bdon'?t\b",
                   r"\bmust not\b", r"\bnever\b", "⛔"),
    "verify": (r"\bverify\b", r"\bself-verify\b", r"\bvalidat", r"\btest\b", r"\bpytest\b",
               r"\bruff\b", r"\bci\b", r"done criteria"),
}

_COMPILED: dict[str, tuple[re.Pattern[str], ...]] = {
    field_name: tuple(re.compile(sig, re.IGNORECASE) for sig in sigs)
    for field_name, sigs in _SIGNALS.items()
}


@dataclass
class BriefReview:
    """The mechanical facts about one brief. ``present`` is decided here and nowhere else.

    Every field is a FACT (a signal matched the brief text, or it did not). ``advisory`` is
    the *only* thing derived from those facts, and it is a NOTE — it gates nothing in v1.
    """
    present: dict[str, bool] = field(default_factory=dict)

    @property
    def missing(self) -> list[str]:
        """The mandatory fields the brief does NOT carry, in :data:`FIELDS` order."""
        return [f for f in FIELDS if not self.present.get(f)]

    @property
    def complete(self) -> bool:
        """Does the brief carry all four fields? (Then there is nothing to advise.)"""
        return not self.missing

    def as_dict(self) -> dict:
        return {"present": dict(self.present), "missing": self.missing,
                "complete": self.complete}


def review_brief(brief_text: str) -> BriefReview:
    """Compute the mechanical facts about ``brief_text``. Pure — no I/O, no side effects.

    For each of the four mandatory fields, a field is PRESENT when any of its signals occurs
    in the brief. A non-string or empty brief carries no fields (everything is missing) rather
    than raising — the critic is advisory, and it must never be the thing that turns a dispatch
    into an error.
    """
    text = brief_text if isinstance(brief_text, str) else ""
    present = {
        field_name: any(pat.search(text) for pat in pats)
        for field_name, pats in _COMPILED.items()
    }
    return BriefReview(present=present)


def advisory_body(review: BriefReview) -> str:
    """The advisory note for one review — empty when the brief is complete (nothing to say).

    ⛔ It says, in its own words, that it changed nothing: an advisory that reads like a gate
    is a lie about what the critic did. A complete brief gets "" — the critic ran and had
    nothing to add, which is a different fact from "the critic never ran" (that one is a NULL
    ``critic_notes`` column) and must not be dressed up as either an approval or a complaint.
    """
    if review.complete:
        return ""
    fields = ", ".join(review.missing)
    return (
        "🧑‍⚖️ THE CRITIC (advisory — this did NOT block, delay, or change the dispatch): "
        f"the brief names no explicit {fields}. A brief that states its objective, its "
        "boundaries, its guardrails, and how to verify the result is one an agent can satisfy "
        "without guessing. This is a note, not a gate — the dispatch already happened."
    )


def critic_enabled(wf) -> bool:
    """Is the critic on for this workflow?

    Two kill switches, mirroring the judge's: ``CHELA_CRITIC=0`` stops the whole fleet's
    critics (one env var, not an edit to every WORKFLOW.md), and ``critic: {enabled: false}``
    turns it off for a single workflow. Unlike the judge it needs no ``test_cmd`` — a brief is
    always reviewable — so it defaults ON. Being advisory-only, "on" costs nothing it can get
    wrong: the worst an over-eager critic does is write a note nobody asked for.
    """
    from chela.config import CRITIC_ENABLED

    if not CRITIC_ENABLED:
        return False
    if wf.get("critic", "enabled", default=True) is False:
        return False
    return True
