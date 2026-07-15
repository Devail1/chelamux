"""🎭 THE PERSONA LAYER — the single declared source of truth for chela's personas.

`docs/PERSONA_PATTERN.md` describes three control-plane personas — the **judge**, the
**critic**, and the **orchestrator** — as *one proven idea, three times*: **mechanical facts
in code, judgment in the LLM.** Until now that layer was scattered and half-invisible: the
judge lives in ``chela/judge.py`` (auto on ``awaiting_review``), the critic in
``chela/critic.py`` (on dispatch), and the orchestrator existed only as design docs plus a
human running the loop by hand.

This module is the **declaration** of that layer — one :class:`Persona` per persona, naming
its trigger, mode, action surface and prompt source. It is *non-invasive*: it DESCRIBES the
judge and critic, referencing their existing implementations; it does **not** re-plumb their
working launch paths. It also does **not** launch, wake, or run the orchestrator — auto-launch
is a later, isolation-gated step (CMX-90). What this gives the rest of chela is a single place
to ask "what personas exist, and what is each allowed to do", consumed read-only by the
dashboard "Personas" panel (``/api/personas``) and locked down by ``tests/test_personas.py``.

The orchestrator's *runnable* system prompt lives next to this file as ``orchestrator.md`` —
the load-bearing text synthesized from ``docs/ORCHESTRATOR_PERSONA.md`` and
``docs/ESCALATION_CONTRACT.md``.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

_HERE = Path(__file__).resolve().parent

#: The orchestrator's runnable system prompt (the load-bearing text, not a stub).
ORCHESTRATOR_PROMPT = _HERE / "orchestrator.md"


def _env_on(name: str, default: bool) -> bool:
    """Read a boolean env flag the same way ``config.JUDGE_ENABLED`` does: an unset flag
    keeps the default, and only an explicit falsey value turns it off."""
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() not in ("false", "0", "no", "off", "")


@dataclass(frozen=True)
class Persona:
    """One declared persona. Pure data — every field is a description, never a live handle to
    the persona's implementation (declaring the layer must not re-plumb what already runs).

    ``prompt_source`` points at where the persona's prompt is authored (a module for the
    judge/critic, ``orchestrator.md`` for the orchestrator); it is a pointer for humans and the
    panel, not an import path this module dereferences.
    """

    key: str
    title: str
    trigger: str
    mode: str
    action_surface: str
    prompt_source: str
    summary: str
    enabled: bool
    docs: tuple[str, ...] = field(default_factory=tuple)

    def as_dict(self) -> dict:
        """JSON-serializable view for the dashboard endpoint."""
        return {
            "key": self.key,
            "title": self.title,
            "trigger": self.trigger,
            "mode": self.mode,
            "action_surface": self.action_surface,
            "prompt_source": self.prompt_source,
            "summary": self.summary,
            "enabled": self.enabled,
            "docs": list(self.docs),
        }


def registry() -> list[Persona]:
    """The persona layer, built fresh so ``enabled`` reflects the live env flags.

    Order is judge → critic → orchestrator — the pipeline order (review code, review the brief,
    run the loop) and the build order in ``docs/PERSONA_PATTERN.md``.
    """
    return [
        Persona(
            key="judge",
            title="The Judge",
            trigger="awaiting_review",
            mode="adjudicative",
            action_surface="verdict — clean / blocked / cannot_verify (blocks on mechanical facts only)",
            prompt_source="chela/judge.py",
            summary=(
                "Adversarial review whose BLOCKING verdicts are facts, not opinions: it proposes "
                "guard corruptions, chela applies each one and runs the repo's own suite. "
                "Green-under-corruption ⇒ the guard is decoration ⇒ block."
            ),
            enabled=_env_on("CHELA_JUDGE", True),
            docs=("docs/PERSONA_PATTERN.md",),
        ),
        Persona(
            key="critic",
            title="The Critic",
            trigger="dispatch",
            mode="advisory",
            action_surface="advisory comment — a non-blocking note on the run's event history",
            prompt_source="chela/critic.py",
            summary=(
                "Reviews the brief the moment a task is dispatched — 'plan review is the new "
                "linter'. Mechanical field/coupling checks in code, one advisory LLM opinion; it "
                "never blocks, delays, or alters a dispatch."
            ),
            enabled=_env_on("CHELA_CRITIC", True),
            docs=("docs/PERSONA_PATTERN.md",),
        ),
        Persona(
            key="orchestrator",
            title="The Orchestrator",
            trigger="boot + inbox event",
            mode="attended-autonomous",
            action_surface=(
                "gated chela commands — dispatch / review / merge / escalate "
                "(never raw gh pr merge / pkill / an agent's shell)"
            ),
            prompt_source="chela/personas/orchestrator.md",
            summary=(
                "Runs the fleet: discover → scope → dispatch → review → decide → relay. Acts "
                "within the standing-auth merge envelope (CI-green + judge-clean + its own "
                "verification), escalates judgment / security / irreversible calls. Human-attended "
                "until isolation lands; this layer declares it but does NOT auto-launch it (CMX-90)."
            ),
            # DECLARED, not launched: the orchestrator does not run yet, so the layer shows it
            # as an embedded-but-dormant persona rather than an active one.
            enabled=False,
            docs=(
                "docs/ORCHESTRATOR_PERSONA.md",
                "docs/ESCALATION_CONTRACT.md",
                "docs/PERSONA_PATTERN.md",
            ),
        ),
    ]


#: The declared personas, in registry order. Built once at import for callers that just want
#: the declarations; the dashboard calls :func:`registry` for live ``enabled`` values.
PERSONAS: list[Persona] = registry()

#: The keys every persona declaration must cover — the completeness contract the tests assert.
PERSONA_KEYS = ("judge", "critic", "orchestrator")


def find(key: str) -> Persona | None:
    """Look up a declared persona by key, or ``None``."""
    return next((p for p in registry() if p.key == key), None)
