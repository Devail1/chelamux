"""Contract-as-code core — the orchestrator's gated action surface.

Turns the escalation contract (``docs/ESCALATION_CONTRACT.md``) from a doc the LLM is
*asked* to follow into code it *cannot violate*. It is the judge's proven trick,
generalized (``docs/PERSONA_PATTERN.md``): the LLM *proposes* an action, and CODE
adjudicates whether it is allowed. A wrong LLM opinion — or a prompt-injection carried in
agent-authored text — cannot take an unsafe action, because the unsafe actions are simply
not reachable through this surface.

Two actions live here, each enforcing its slice of the contract:

- :func:`merge` — the **AUTONOMOUS merge gate**. A dispatched ``cmx-N`` PR merges only when
  *all* hold, checked HERE and read LIVE from GitHub at the moment of merging:

    1. the PR's base branch is the one autonomous target (``dev``) and is **never** a
       production-facing branch (``main``/``master``/…) — the contract's NEVER line;
    2. the judge said ``clean`` on this run;
    3. CI is green;
    4. GitHub reports the PR open and ``MERGEABLE``.

  Any miss **refuses** — there is no ``--force`` here, so the command literally cannot
  merge to ``main`` or merge a red PR regardless of what the LLM decided. Every merge is
  logged with its justification to the event log (provenance), because "an autonomous
  action with no logged justification is a bug."

- :func:`escalate` — the **one structured way** to hand a decision to the human. It records
  the escalation (kind, analysis, recommendation) to the event log and pushes it to the
  human over the notification channel. A *missed* escalation costs a question; a *wrong*
  autonomous action costs trust — the asymmetry is why the fail-closed default routes here.

This module is deliberately mechanical and side-effect-narrow so it needs no agent to
test: the gate is a pure function of (run row, live GitHub facts), and every refusal names
exactly which clause of the contract stopped it.
"""
from __future__ import annotations

import json
import logging
import os
import subprocess
from pathlib import Path

from chela import dispatcher, event_log, notify
from chela.dispatcher import CI_PASSING
from chela.judge import J_CLEAN

log = logging.getLogger("chela.contract")

# The one branch an autonomous merge may target. dev/dogfood is the ceiling
# (ESCALATION_CONTRACT.md §AUTONOMOUS). Env-overridable for a fleet whose integration
# branch is named differently — but the FORBIDDEN set below is absolute and no override
# can widen it.
AUTONOMOUS_BASE = os.environ.get("CHELA_MERGE_BASE", "dev").strip() or "dev"

# NEVER — production-facing branches. No env, no --force, no chain of small autonomous
# steps reaches these: merging to one is a per-instance human act (ESCALATION_CONTRACT.md
# §NEVER). Compared case-folded, and checked BEFORE the "is it the autonomous base" test so
# that even a misconfigured CHELA_MERGE_BASE=main is refused as a hard-line violation.
FORBIDDEN_BASES = frozenset({"main", "master", "production", "prod", "release", "stable"})

GIT_TIMEOUT = 60


def _refuse(task_id: str | None, tier: str, error: str, **extra) -> dict:
    """A gate refusal, shaped like every other dispatcher result. ``tier`` is the contract
    tier the refusal belongs to (``never`` / ``escalate``) so the caller can say *why* the
    action was not the orchestrator's to take."""
    return {"ok": False, "task_id": task_id, "tier": tier, "error": error, **extra}


def _read_pr_base(pr_url: str | None, repo_dir: str | None) -> str | None:
    """The PR's base branch per GitHub (``baseRefName``), or None if it cannot be read.

    None is fail-closed: an unknown base is never silently treated as the allowed base —
    :func:`merge` refuses on None exactly as it refuses on a forbidden base. The base is
    read LIVE (not from the run row) because it is the fact the whole NEVER line turns on,
    and it is the one a stale cache must never be trusted for.
    """
    number = dispatcher._pr_number(pr_url)
    if not number or not repo_dir:
        return None
    try:
        out = subprocess.run(
            ["gh", "pr", "view", number, "--json", "baseRefName"],
            cwd=repo_dir, capture_output=True, text=True, timeout=15,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if out.returncode != 0:
        return None
    try:
        data = json.loads(out.stdout)
    except (json.JSONDecodeError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    return (data.get("baseRefName") or "").strip() or None


def _best_effort(task_id: str | None, label: str, argv: list[str], cwd: str, timeout: int) -> None:
    """Run a post-merge cleanup step, logging (never raising) on failure — the merge already
    landed on GitHub, so a worktree/branch that won't delete is a mess to tidy, not a
    reason to report the merge as failed. Mirrors the dashboard's ``_merge_one`` cleanup."""
    try:
        cp = subprocess.run(argv, cwd=cwd, capture_output=True, text=True, timeout=timeout)
        if cp.returncode != 0:
            log.warning("merge cleanup %s failed for %s: %s",
                        label, task_id, (cp.stderr or cp.stdout or "").strip())
    except (OSError, subprocess.TimeoutExpired) as e:
        log.warning("merge cleanup %s failed for %s: %s", label, task_id, e)


def _squash_merge(run: dict, repo_dir: str, pr_url: str) -> dict:
    """Squash-merge the PR, then best-effort clean up the local worktree, local branch and
    remote branch. ⛔ **Mechanics only — the gate is the caller's job** (:func:`merge`); this
    assumes every contract check already passed.

    Squash (not rebase): a rebase-merge silently drops the post-PR TODO-strike commit, after
    which the dispatcher redispatches the already-merged task (the PR #12 incident). Cleanup
    is ours rather than ``gh pr merge --delete-branch`` because ``gh`` exits non-zero when the
    branch is checked out in a worktree (the dogfood case) even though the remote merge
    succeeded — the same reason the dashboard does its own cleanup.
    """
    task_id = run.get("task_id")
    number = dispatcher._pr_number(pr_url)
    try:
        merge = subprocess.run(
            ["gh", "pr", "merge", number, "--squash"],
            cwd=repo_dir, capture_output=True, text=True, timeout=GIT_TIMEOUT,
        )
    except OSError as e:
        return {"ok": False, "error": f"gh CLI could not be run: {e}"}
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "gh pr merge timed out"}
    if merge.returncode != 0:
        return {"ok": False, "error": (merge.stderr or merge.stdout or "gh pr merge failed").strip()}

    merge_sha = None
    try:
        sha_proc = subprocess.run(
            ["gh", "pr", "view", number, "--json", "mergeCommit", "-q", ".mergeCommit.oid"],
            cwd=repo_dir, capture_output=True, text=True, timeout=15,
        )
        if sha_proc.returncode == 0:
            merge_sha = sha_proc.stdout.strip() or None
    except (OSError, subprocess.TimeoutExpired):
        pass

    worktree_path = run.get("worktree_path")
    if worktree_path and Path(worktree_path).exists():
        _best_effort(task_id, "worktree-remove",
                     ["git", "worktree", "remove", "--force", worktree_path], repo_dir, 30)
    branch_name = run.get("branch_name")
    if branch_name:
        _best_effort(task_id, "branch-delete",
                     ["git", "branch", "-D", branch_name], repo_dir, 15)
        _best_effort(task_id, "remote-branch-delete",
                     ["git", "push", "origin", "--delete", branch_name], repo_dir, 30)
    return {"ok": True, "merge_commit_sha": merge_sha}


def merge(ident: str, *, reason: str = "") -> dict:
    """AUTONOMOUSLY merge a dispatched PR — but only if the contract's whole gate holds.

    Every clause is checked here and the GitHub-derived ones are read LIVE at the moment of
    merging, not from the 60s-old run-row cache. The gate, in order (each refusal names the
    clause and the contract tier it belongs to):

    1. the run exists and is ``awaiting_review``;
    2. the PR's base branch is not a production-facing branch (**NEVER**), and is the one
       autonomous target ``dev`` — an unreadable base is refused too (unknown ≠ safe);
    3. the judge said ``clean`` on this run (anything else — ``blocked`` /
       ``cannot_verify`` / never-ran — is a human's call);
    4. CI is green per GitHub;
    5. GitHub reports the PR open and ``MERGEABLE``.

    There is deliberately **no ``--force``**: overriding a gate is an escalation, not an
    autonomous act, so this command cannot do it. A human who knows a failure is unrelated
    merges by hand — that is the "explicit per-instance human act" the NEVER tier requires.

    On success the merge is recorded to the event log with its full justification (the base,
    judge state and CI state it relied on), so a human can later ask *why did it merge that*
    and get a mechanical answer.
    """
    run = dispatcher.resolve_run(ident)
    if run is None:
        return _refuse(None, "escalate",
                       f"no run matches {ident!r} (task id, branch, or window name)")
    task_id = run["task_id"]

    if run["status"] != "awaiting_review":
        return _refuse(task_id, "escalate",
                       f"run is in status {run['status']!r}, not 'awaiting_review' — only a "
                       "run actually under review can be merged")

    pr_url = run.get("pr_url")
    if not pr_url:
        return _refuse(task_id, "escalate", "the run has no PR url to merge")

    wf_path = run.get("workflow_path")
    repo_dir = str(Path(wf_path).parent) if wf_path else None
    if not repo_dir or not Path(repo_dir).is_dir():
        return _refuse(task_id, "escalate", f"the workflow repo dir is missing ({wf_path})")

    # 2. THE NEVER LINE — the base branch, read live and checked first & hardest.
    base = _read_pr_base(pr_url, repo_dir)
    if base is None:
        return _refuse(task_id, "escalate",
                       "could not read this PR's base branch — and a target nobody could "
                       "read is never assumed safe. Refusing to merge.")
    if base.casefold() in FORBIDDEN_BASES:
        return _refuse(task_id, "never", pr_base=base,
                       error=f"this PR targets {base!r} — a production-facing branch. Merging "
                             "to it is NEVER autonomous under any circumstance; it is a human's "
                             "explicit, per-instance act. Refusing.")
    if base != AUTONOMOUS_BASE:
        return _refuse(task_id, "escalate", pr_base=base,
                       error=f"this PR targets {base!r}, not the autonomous base "
                             f"{AUTONOMOUS_BASE!r}. Merging outside the standing dev/dogfood "
                             "grant is an escalation. Refusing.")

    # 3. The judge's verdict — clean, or it is not the orchestrator's to merge.
    judge_state = run.get("judge_state")
    if judge_state != J_CLEAN:
        shown = judge_state or "never ran"
        return _refuse(task_id, "escalate", judge_state=judge_state,
                       error=f"the judge is {shown!r} on this run, not {J_CLEAN!r}. A run that "
                             "is not judge-clean is a human's call (blocked → rework, "
                             "cannot_verify / no-judge → escalate). Refusing to merge.")

    # 4. CI — green per GitHub, read live. Anything else (pending / red / none / unreadable)
    #    is refused: for an AUTONOMOUS merge, only a check that was SEEN to pass is a pass.
    ci = dispatcher._read_pr_checks(pr_url, repo_dir)
    if ci.state != CI_PASSING:
        detail = {
            dispatcher.CI_FAILING: f"CI is RED (failing: {', '.join(ci.failing) or 'unnamed check(s)'})",
            dispatcher.CI_PENDING: "the checks have not settled yet",
            dispatcher.CI_NONE: "this PR has NO checks at all, and no checks is not passing checks",
            dispatcher.CI_UNKNOWN: f"the checks could not be read ({ci.detail})",
        }.get(ci.state, f"CI state is {ci.state!r}")
        return _refuse(task_id, "escalate", ci_state=ci.state,
                       error=f"{detail}. An autonomous merge requires green CI. Refusing.")

    # 5. Mergeable — GitHub's own verdict on whether the merge is clean, read live.
    pr_state, mergeable = dispatcher._read_pr_status(pr_url, repo_dir)
    if pr_state and pr_state != "open":
        return _refuse(task_id, "escalate", pr_state=pr_state,
                       error=f"the PR is {pr_state!r}, not open. Refusing to merge.")
    if mergeable != "MERGEABLE":
        return _refuse(task_id, "escalate", pr_mergeable=mergeable,
                       error=f"GitHub reports this PR {mergeable or 'un-mergeable'!r}, not "
                             "MERGEABLE (a conflict, or mergeability not yet computed). "
                             "Resolving a conflict is a human's call. Refusing to merge.")

    # THE GATE HELD. Merge, then record the decision with its justification.
    result = _squash_merge(run, repo_dir, pr_url)
    justification = {
        "task_id": task_id, "pr_url": pr_url, "base": base, "judge_state": judge_state,
        "ci_state": ci.state, "pr_mergeable": mergeable, "reason": reason.strip(),
    }
    if not result.get("ok"):
        event_log.append(
            "orchestrator.merge_failed",
            f"merge of {task_id} passed the gate but gh failed: {result.get('error', '')}",
            payload={**justification, "error": result.get("error")},
        )
        return _refuse(task_id, "escalate",
                       error=f"the gate held but the merge itself failed: {result.get('error')}")

    justification["merge_commit_sha"] = result.get("merge_commit_sha")
    rec = event_log.append(
        "orchestrator.merge",
        f"merged {task_id} → {base} (judge clean, CI green, mergeable)"
        + (f": {reason.strip()}" if reason.strip() else ""),
        payload=justification,
    )
    log.info("contract.merge: %s → %s (sha=%s)", task_id, base, result.get("merge_commit_sha"))
    return {
        "ok": True, "task_id": task_id, "base": base, "pr_url": pr_url,
        "merge_commit_sha": result.get("merge_commit_sha"),
        "branch_name": run.get("branch_name"),
        "event_seq": rec["seq"] if rec else None,
    }


def escalate(summary: str, *, kind: str = "decision", recommendation: str = "",
             run: str | None = None, options: list[str] | None = None) -> dict:
    """Hand a decision to the human — the ONE structured escalation path.

    Records the escalation to the event log (durable provenance) and pushes it to the human
    over the notification channel (best-effort — a flaky notifier never loses the record,
    which is written first). The orchestrator does the analysis and forms a recommendation;
    :func:`escalate` is how it *asks* instead of guessing. Returns
    ``{ok, event_seq, notified, kind, run?}``.
    """
    summary = (summary or "").strip()
    if not summary:
        return {"ok": False, "error": "an escalation with no summary is not an escalation"}
    kind = (kind or "decision").strip() or "decision"
    recommendation = (recommendation or "").strip()

    payload: dict = {"kind": kind, "tier": "escalate"}
    if recommendation:
        payload["recommendation"] = recommendation
    if options:
        payload["options"] = [o for o in options if (o or "").strip()]
    run_id = None
    if run:
        resolved = dispatcher.resolve_run(run)
        run_id = resolved["task_id"] if resolved else run
        payload["run"] = run_id
        if resolved and resolved.get("pr_url"):
            payload["pr_url"] = resolved["pr_url"]

    rec = event_log.append(f"orchestrator.escalation.{kind}", summary, payload=payload)

    body = summary
    if recommendation:
        body += f"\n\nRecommendation: {recommendation}"
    if payload.get("options"):
        body += "\n\nOptions:\n" + "\n".join(f"  - {o}" for o in payload["options"])
    if run_id:
        body += f"\n\nRun: {run_id}"
    notified = notify.send(body, title=f"chela escalation: {kind}") if notify.enabled() else False

    log.info("contract.escalate: %s (kind=%s, notified=%s)", summary[:80], kind, notified)
    return {
        "ok": True, "kind": kind, "run": run_id,
        "event_seq": rec["seq"] if rec else None, "notified": notified,
    }
