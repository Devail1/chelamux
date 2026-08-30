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


def _judge_verdict_is_stale(run: dict) -> bool:
    """Is ``run``'s ``clean`` verdict recorded against a commit other than its own known head?

    ``judge_sha`` is the commit a judge actually verified (stamped at judge launch); the run
    row also carries ``pr_head_sha`` (CI's cached head, refreshed on the dispatcher's own poll
    loop — see ``dispatcher.py``'s ``pr_checks``/``pr_head_sha`` columns). When BOTH are known
    and disagree, this row's ``clean`` was earned by a commit that is no longer the PR's head —
    a promotion conflict resolved on the branch, a rebase, anything pushed after judgement — and
    a verdict about a superseded commit is not a verdict about the one that would actually be
    merged.

    Mirrors :func:`chela.contract.merge`'s own live ``judge_sha != ci.head_sha`` refusal
    (CMX-238) at the CHEAPER, cached-data layer this filter already reads — it does not widen
    or replace that live check (``contract.merge`` still re-reads GitHub at merge time and is
    what actually stops the merge; this exists so the filter's own claim of "candidate" is not
    already wrong before ``contract.merge`` ever sees the row). Same conservatism as that check:
    either sha missing is NOT treated as staleness — an older row or a judge that never stamped
    one is not positive evidence of anything, so it is left to ``contract.merge``'s live read.
    """
    judge_sha = run.get("judge_sha")
    head_sha = run.get("pr_head_sha")
    return bool(judge_sha and head_sha and judge_sha != head_sha)


def candidates() -> list[dict]:
    """``awaiting_review`` runs the judge itself cleared, FOR THE COMMIT STILL AT THEIR HEAD —
    the ONLY subset auto-merge ever touches. Everything else (``blocked``/``cannot_verify``/
    never-judged/stale-verdict, any other status) is left exactly where it already sits, for a
    human or the lease-gated orchestrator to handle; this sweep does not widen who may act on
    them, only who may act on the judge-clean, judge-current ones."""
    return [
        run for run in dispatcher.list_runs()
        if run.get("status") == "awaiting_review" and run.get("judge_state") == J_CLEAN
        and not _judge_verdict_is_stale(run)
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
