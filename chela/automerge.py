"""🔀⚠️ AUTO-MERGE — the fully-unattended merge sweep, off by default (CMX-138).

Every merge path that exists today has a human either doing it or watching it: a human's own
``chela merge``, and the auto-launched orchestrator's ``chela merge`` (which ``contract.merge``
refuses unless a human's attended-lease is live — see ``config.AUTO_ORCHESTRATOR_ACTOR``). There
is deliberately **no fully-unattended auto-merge** (``docs/ESCALATION_CONTRACT.md``).

This module adds exactly one, and only when an operator explicitly opts in
(``CHELA_AUTO_MERGE`` / :data:`chela.config.AUTO_MERGE_ENABLED`, **off by default**): on every
daemon tick, hand each ``awaiting_review`` run the judge cleared straight to
:func:`chela.contract.merge` — the *same* gate a human or the lease-gated orchestrator would
hit (base branch / judge `clean` / CI green / MERGEABLE, still no ``--force``, still no
NEVER-line override). This module adds no gate of its own and loosens none of ``contract``'s —
it only decides *when* to ask, on a timer instead of on a human's say-so. Trusting the judge
is the whole bet a power user makes by turning this on: nothing here re-checks the judge's
verdict, it *is* the check.

⛔ **Deliberately narrower than "the orchestrator unattended."** This sweep never reasons, never
runs a shell command an LLM chose, never touches a repo or branch outside what ``contract.merge``
already scopes to. It performs exactly the one mechanical action a human's own ``chela merge``
performs — nothing more — just without anyone watching it happen. That is *why* it is safe to
offer as an opt-in at all while the orchestrator itself stays lease-gated
(``docs/ESCALATION_CONTRACT.md``'s isolation prerequisite is about a *judgment-making* agent with
shell access; this is neither).
"""
from __future__ import annotations

import logging

from chela import config, contract, dispatcher
from chela.judge import J_CLEAN

log = logging.getLogger("chela.automerge")


def enabled() -> bool:
    """Read live, never latched — an operator flips ``CHELA_AUTO_MERGE`` on a running daemon
    the same way every other policy knob here is (:func:`chela.judge_max_unknown_retries` &c.)."""
    return config.AUTO_MERGE_ENABLED


def candidates() -> list[dict]:
    """``awaiting_review`` runs the judge itself cleared — the ONLY subset auto-merge ever
    touches. Everything else (``blocked``/``cannot_verify``/never-judged, any other status) is
    left exactly where it already sits, for a human or the lease-gated orchestrator to handle;
    this sweep does not widen who may act on them, only who may act on the judge-clean ones."""
    return [
        run for run in dispatcher.list_runs()
        if run.get("status") == "awaiting_review" and run.get("judge_state") == J_CLEAN
    ]


def sweep(reason: str = "") -> list[dict]:
    """One pass: attempt :func:`chela.contract.merge` for every judge-clean ``awaiting_review``
    run, stamped as :data:`chela.config.AUTO_MERGE_ACTOR` so provenance never confuses "the
    daemon merged this alone" with a human or the attended orchestrator.

    Every result — merged or refused — is returned; only an actual merge (or an unreachable
    NEVER-tier refusal, which would mean this sweep somehow got past its own candidate filter)
    is logged loudly. The ordinary refusals (CI still pending, mergeability not yet computed) are
    silent and simply retried next tick — the same way the dispatcher's own CI poll works — so a
    30s drumbeat of "not yet" never buries the log.

    Never raises: one run's failure (a bad row, a `gh` hiccup `contract.merge` itself did not
    already turn into a refusal dict) must not stop the rest of the sweep or take the daemon tick
    down with it.
    """
    results = []
    for run in candidates():
        task_id = run.get("task_id")
        try:
            result = contract.merge(
                task_id,
                reason=reason or "CHELA_AUTO_MERGE: judge clean, unattended sweep",
                actor=config.AUTO_MERGE_ACTOR,
            )
        except Exception:
            log.exception("auto-merge: sweep of %s raised — skipping it this tick", task_id)
            continue
        results.append(result)
        if result.get("ok"):
            log.warning(
                "🔀⚠️ auto-merge: merged %s → %s UNATTENDED (CHELA_AUTO_MERGE, sha=%s)",
                task_id, result.get("base"), (result.get("merge_commit_sha") or "?")[:12],
            )
        elif result.get("tier") == "never":
            # Unreachable in practice — `candidates()` only offers judge-clean awaiting_review
            # rows, and the NEVER line is a base-branch fact `contract.merge` reads live. Loud
            # because it would mean this filter and that gate have drifted apart.
            log.error("auto-merge: %s hit the NEVER line — %s", task_id, result.get("error"))
    return results
