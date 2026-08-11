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

    1. the PR's base branch is the one autonomous target **declared by this run's own
       dispatching workflow** (``workspace.base_branch`` — ``dev`` for chelamux itself,
       falling back to ``dev``/``CHELA_MERGE_BASE`` when a workflow declares nothing) and is
       **never** a production-facing branch (``main``/``master``/…) UNLESS that same
       workflow explicitly committed to exactly that branch — the contract's NEVER line,
       scoped per workflow so one repo's trunk convention can never widen another's;
    2. the judge said ``clean`` on this run;
    3. CI is green;
    4. GitHub reports the PR open and ``MERGEABLE``;
    5. **and — for the auto-launched orchestrator only — a human's attended-lease is live.**
       An action initiated by the auto-orchestrator (``$CHELA_ACTOR == auto-orchestrator``)
       is refused when the lease is stale/absent: it is auto-*launched*, but it may only
       *act* while a human is attending (``chela.personas.lease``). A human's own merge
       carries no actor stamp and is never gated this way — the human IS the attendance.

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

from chela import config, dispatcher, event_log, notify
from chela.dispatcher import CI_PASSING
from chela.judge import J_CLEAN
from chela.personas import lease
from chela.workflow import load_workflow_cached

log = logging.getLogger("chela.contract")

# The FALLBACK autonomous base — used only when a run's own dispatching workflow declares
# no `workspace.base_branch` (or its WORKFLOW.md can't be read/parsed). dev/dogfood is the
# ceiling (ESCALATION_CONTRACT.md §AUTONOMOUS). Env-overridable for a fleet whose integration
# branch is named differently — but the FORBIDDEN set below is absolute and the env alone can
# never widen it: only a per-workflow COMMITTED ``base_branch`` can (see ``_declared_base_branch``
# and the NEVER-line handling in :func:`merge`), and only for that workflow's own runs.
#
# A Dispatch-tab knob (CMX-220), restart_required — dashboard-writable through
# ``config.dashboard_setting`` (env still wins), latched here at THIS module's import same
# as ``config.JUDGE_ENABLED``/``CRITIC_ENABLED`` are latched in ``chela.config``. Making the
# fallback dashboard-settable does not loosen anything: the FORBIDDEN_BASES check below is
# unconditional and runs live at merge time regardless of where AUTONOMOUS_BASE came from.
AUTONOMOUS_BASE = config.dashboard_setting("merge_base", "CHELA_MERGE_BASE", "dev", cast=str).strip() or "dev"

# NEVER — production-facing branches. No env, no --force, no chain of small autonomous
# steps reaches these: merging to one is a per-instance human act (ESCALATION_CONTRACT.md
# §NEVER). Compared case-folded, and checked BEFORE the "is it the autonomous base" test so
# that even a misconfigured CHELA_MERGE_BASE=main is refused as a hard-line violation.
FORBIDDEN_BASES = frozenset({"main", "master", "production", "prod", "release", "stable"})

GIT_TIMEOUT = 60


def _actor(explicit: str | None = None) -> str:
    """Who is initiating this action — the auto-launched orchestrator names itself here.

    An explicit arg (tests, an in-process caller) wins; otherwise the actor is read LIVE from
    ``$CHELA_ACTOR`` in the calling session's environment. Only the auto-launched orchestrator
    stamps this (``config.AUTO_ORCHESTRATOR_ACTOR``); a human's shell has no stamp, which is
    exactly what keeps a human's own ``chela merge`` off the attended-lease path.
    """
    return (explicit if explicit is not None
            else os.environ.get(config.ACTOR_ENV, "")).strip()


def _refuse(task_id: str | None, tier: str, error: str, **extra) -> dict:
    """A gate refusal, shaped like every other dispatcher result. ``tier`` is the contract
    tier the refusal belongs to (``never`` / ``escalate``) so the caller can say *why* the
    action was not the orchestrator's to take.

    Every ``escalate``-tier call site in :func:`merge` passes ``recommendation`` and
    ``options`` through ``**extra`` (CMX-242) — the refusal IS the escalation surface, and
    whoever reads it (typically an LLM orchestrator invoking ``chela merge`` unattended)
    needs the same "here's what I'd try, here's your menu" a human-typed ``chela escalate``
    gets, not a bare reason it has to turn into a decision itself. ``never``-tier refusals
    (the forbidden-base line) deliberately carry neither: that is not a human's decision to
    be handed options for, it is a hard line with no menu.
    """
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


def _declared_base_branch(workflow_path: str | None) -> str | None:
    """This run's OWN dispatching workflow's committed ``workspace.base_branch`` — the
    per-repo declaration :func:`merge` resolves the allowed autonomous base from (PER-WORKFLOW
    AUTONOMOUS MERGE BASE). Reads the same hot-reloaded, stat-gated cache the daemon's poll loop
    already uses (:func:`chela.workflow.load_workflow_cached`), so a workflow's declared base is
    always the one currently committed to disk.

    None — never a default — when ``workflow_path`` is falsy, the file is missing/unparseable
    (``WorkflowStatus.ok`` is False), or the front matter simply omits ``workspace.base_branch``.
    Fail-closed: callers must treat None as "fall back to :data:`AUTONOMOUS_BASE`", not as any
    particular branch name — an unreadable declaration is never silently assumed to be ``dev``
    (or anything else).
    """
    if not workflow_path:
        return None
    status = load_workflow_cached(workflow_path)
    if not status.ok or status.workflow is None:
        return None
    declared = status.workflow.get("workspace", "base_branch", default=None)
    if not isinstance(declared, str):
        return None
    return declared.strip() or None


def _is_control_repo(repo_dir: str) -> bool:
    """Is ``repo_dir`` the repo chela's OWN source is running from — the control plane?

    Self-protection (ESCALATION_CONTRACT.md's per-workflow base declaration must never
    reach back and loosen chela's own gate): even if THIS repo's own ``WORKFLOW.md`` ever
    declared a forbidden ``workspace.base_branch``, that declaration must not be honored here
    — the daemon must never be talked into autonomously merging to its own production
    ``main``. Detected by comparing ``repo_dir`` against the directory this very module's
    package lives in (``chela/contract.py`` → its repo root), which is where the CURRENTLY
    RUNNING chela's source sits, regardless of which repo it happens to be dispatching.
    """
    try:
        here = Path(repo_dir).expanduser().resolve()
        control = Path(__file__).resolve().parent.parent
    except OSError:
        return False
    return here == control


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


def merge(ident: str, *, reason: str = "", actor: str | None = None) -> dict:
    """AUTONOMOUSLY merge a dispatched PR — but only if the contract's whole gate holds.

    Every clause is checked here and the GitHub-derived ones are read LIVE at the moment of
    merging, not from the 60s-old run-row cache. The gate, in order (each refusal names the
    clause and the contract tier it belongs to):

    1. the run exists and is ``awaiting_review``;
    2. **the ATTENDED-LEASE — for the auto-launched orchestrator only.** When the caller is the
       auto-launched orchestrator (``$CHELA_ACTOR == auto-orchestrator``), a human's attended-lease
       must be *active* right now, or the merge is refused as an escalation. This is the ACTION-time
       half of "attended-autonomous": the orchestrator is auto-*launched*, but it may only *act*
       (merge) while a human is still attending — the moment the lease lapses it must ``chela
       escalate`` instead. A **human's** own ``chela merge`` carries no actor stamp, so this gate
       never applies to a human — the human's presence IS the attendance;
    3. the PR's base branch equals the AUTONOMOUS TARGET this run's own dispatching
       workflow declared (its committed ``workspace.base_branch`` — falling back to ``dev``
       when it declares nothing or can't be read), and is not a production-facing branch
       (**NEVER**) UNLESS that same workflow explicitly declared exactly that branch (never
       via the env fallback, never on another workflow's behalf, never for chela's own
       control repo) — an unreadable base is refused too (unknown ≠ safe);
    4. the judge said ``clean`` on this run (anything else — ``blocked`` /
       ``cannot_verify`` / never-ran — is a human's call);
    5. CI is green per GitHub;
    6. GitHub reports the PR open and ``MERGEABLE``.

    There is deliberately **no ``--force``**: overriding a gate is an escalation, not an
    autonomous act, so this command cannot do it. A human who knows a failure is unrelated
    merges by hand — that is the "explicit per-instance human act" the NEVER tier requires.

    On success the merge is recorded to the event log with its full justification (the base,
    judge state and CI state it relied on), so a human can later ask *why did it merge that*
    and get a mechanical answer.
    """
    run = dispatcher.resolve_run(ident)
    if run is None:
        return _refuse(
            None, "escalate",
            f"no run matches {ident!r} (task id, branch, or window name)",
            recommendation="Run `chela status` to list real task ids/branches/window names "
                            "and retry with the correct one — this identifier does not "
                            "resolve to any run chela knows about.",
            options=[
                "Run `chela status` to list real task ids/branches/window names and retry with the correct one",
                "If the run truly doesn't exist, there is nothing to merge — check dispatch history instead",
            ],
        )
    task_id = run["task_id"]

    if run["status"] != "awaiting_review":
        return _refuse(
            task_id, "escalate",
            f"run is in status {run['status']!r}, not 'awaiting_review' — only a "
            "run actually under review can be merged",
            recommendation=f"Wait for the run to reach awaiting_review, then retry `chela "
                            f"merge` — it is currently {run['status']!r}, and only a run "
                            "under review can be merged.",
            options=[
                "Wait for the run to reach awaiting_review, then retry `chela merge`",
                "If it's stuck in this status, `chela reopen` it or investigate why it never reached review",
            ],
        )

    pr_url = run.get("pr_url")
    if not pr_url:
        return _refuse(
            task_id, "escalate", "the run has no PR url to merge",
            recommendation="Check the run row / dashboard for why no PR was ever recorded, "
                            "and open one by hand if needed — there is no PR url on this "
                            "run to merge.",
            options=[
                "Check the run row / dashboard for why no PR was ever recorded, and open one by hand if needed",
                "Abandon the task if the work was never turned into a PR",
            ],
        )

    wf_path = run.get("workflow_path")
    repo_dir = str(Path(wf_path).parent) if wf_path else None
    if not repo_dir or not Path(repo_dir).is_dir():
        return _refuse(
            task_id, "escalate", f"the workflow repo dir is missing ({wf_path})",
            recommendation=f"Restore the workflow's repo dir ({wf_path!r}) or fix its path "
                            "in WORKFLOW.md — the dispatching workflow's repo directory no "
                            "longer exists on disk.",
            options=[
                f"Restore the workflow's repo dir ({wf_path!r}) or fix its path in WORKFLOW.md",
                "Abandon the task if the repo dir is permanently gone",
            ],
        )

    # 2. THE ATTENDED-LEASE — the ACTION-time supervision gate, for the auto-orchestrator only.
    #    The auto-launched orchestrator may merge ONLY while a human's lease is live; stale or
    #    absent ⇒ it must escalate, not act. A human merge (no actor stamp) is never gated here.
    if _actor(actor) == config.AUTO_ORCHESTRATOR_ACTOR and lease.active() is None:
        return _refuse(
            task_id, "escalate", actor=config.AUTO_ORCHESTRATOR_ACTOR,
            error="the auto-launched orchestrator has NO active attended-lease — its "
                  "autonomous actions require a human to be attending (`chela "
                  "orchestrator attend`). The lease has lapsed or was never granted, so "
                  "this merge is unattended and is REFUSED. Escalate to a human instead.",
            recommendation="Run `chela orchestrator attend` to grant a fresh attended-lease, "
                            "then retry — the auto-launched orchestrator may only act while a "
                            "human is attending.",
            options=[
                "Run `chela orchestrator attend` to grant a fresh attended-lease, then retry",
                "Merge it yourself as a human (a human's own `chela merge` is never lease-gated)",
            ],
        )

    # 3. THE NEVER LINE — the base branch, read live and checked first & hardest.
    #
    #    The allowed base is resolved PER RUN from its OWN dispatching workflow's committed
    #    `workspace.base_branch` (a repo whose trunk IS `main` — e.g. lean-alpha — declares
    #    that in its WORKFLOW.md). A workflow that declares nothing, or whose file can't be
    #    read, falls back to the global AUTONOMOUS_BASE (`dev`) — fail-closed, never a guess.
    base = _read_pr_base(pr_url, repo_dir)
    if base is None:
        return _refuse(
            task_id, "escalate",
            "could not read this PR's base branch — and a target nobody could "
            "read is never assumed safe. Refusing to merge.",
            recommendation="Check `gh pr view` / GitHub API access from the workflow's repo "
                            "dir and retry — the base branch could not be read live from "
                            "GitHub, and an unreadable target is never assumed safe.",
            options=[
                "Check `gh pr view` / GitHub API access from the workflow's repo dir and retry",
                "Merge it yourself as a human once you've confirmed the PR's actual base branch",
            ],
        )

    declared_base = _declared_base_branch(wf_path)
    allowed_base = declared_base or AUTONOMOUS_BASE

    if base.casefold() in FORBIDDEN_BASES:
        # A forbidden base is reachable ONLY as an explicit, COMMITTED per-workflow
        # declaration of exactly that branch — never via the AUTONOMOUS_BASE fallback (so
        # the env alone, e.g. a misconfigured CHELA_MERGE_BASE=main, can never widen it),
        # never for a workflow OTHER than the one that declared it, and never for chela's
        # own control repo (self-protection — see `_is_control_repo`).
        widened_by_own_declaration = (
            declared_base is not None
            and base.casefold() == declared_base.casefold()
            and not _is_control_repo(repo_dir)
        )
        if not widened_by_own_declaration:
            return _refuse(
                task_id, "never", pr_base=base,
                error=f"this PR targets {base!r} — a production-facing branch. Merging to it "
                      "is NEVER autonomous unless THIS run's own dispatching workflow "
                      f"explicitly declares {base!r} as its workspace.base_branch (it does "
                      f"not: declared={declared_base!r}), and it is never honored for chela's "
                      "own control repo. Otherwise this is a human's explicit, per-instance "
                      "act. Refusing.")

    if base != allowed_base:
        return _refuse(
            task_id, "escalate", pr_base=base,
            error=f"this PR targets {base!r}, not the autonomous base "
                  f"{allowed_base!r} declared for its dispatching workflow. Merging "
                  "outside that grant is an escalation. Refusing.",
            recommendation=f"Re-target the PR's base to {allowed_base!r} on GitHub, then "
                            f"retry `chela merge` — this PR targets {base!r}, not the "
                            f"autonomous base {allowed_base!r} declared for its workflow.",
            options=[
                f"Re-target the PR's base to {allowed_base!r} on GitHub, then retry `chela merge`",
                f"If {base!r} really is the intended target, merge it yourself as a human's explicit act",
            ],
        )

    # 4. The judge's verdict — clean, or it is not the orchestrator's to merge.
    judge_state = run.get("judge_state")
    if judge_state != J_CLEAN:
        shown = judge_state or "never ran"
        return _refuse(
            task_id, "escalate", judge_state=judge_state,
            error=f"the judge is {shown!r} on this run, not {J_CLEAN!r}. A run that "
                  "is not judge-clean is a human's call (blocked → rework, "
                  "cannot_verify / no-judge → escalate). Refusing to merge.",
            recommendation=f"Wait for the judge to finish and report {J_CLEAN!r}, then "
                            f"retry — the judge is {shown!r} on this run, not {J_CLEAN!r}.",
            options=[
                f"Wait for the judge to finish and report {J_CLEAN!r}, then retry",
                f"If it's stuck at {shown!r}, investigate why (`chela judge run`) before merging by hand",
            ],
        )

    # 5. CI — green per GitHub, read live. Anything else (pending / red / none / unreadable)
    #    is refused: for an AUTONOMOUS merge, only a check that was SEEN to pass is a pass.
    ci = dispatcher._read_pr_checks(pr_url, repo_dir)
    if ci.state != CI_PASSING:
        detail = {
            dispatcher.CI_FAILING: f"CI is RED (failing: {', '.join(ci.failing) or 'unnamed check(s)'})",
            dispatcher.CI_PENDING: "the checks have not settled yet",
            dispatcher.CI_NONE: "this PR has NO checks at all, and no checks is not passing checks",
            dispatcher.CI_UNKNOWN: f"the checks could not be read ({ci.detail})",
        }.get(ci.state, f"CI state is {ci.state!r}")
        # CMX-242: this was the incident that created this ticket — a caller refused here has
        # no way to know that an empty commit changes the sha and makes the judge's clean
        # verdict stale, so "just push something to trigger CI" is a trap this must name.
        ci_options = [
            "Open the PR's Checks tab, resolve/re-run whatever is red or pending, then retry `chela merge`",
            "If no checks are registered at all, push a REAL commit (not an empty one — an "
            "empty commit changes the sha and makes the judge's clean verdict stale) or "
            "re-trigger whatever workflow registers checks",
            "Merge it yourself as a human once you've verified CI by hand",
        ]
        return _refuse(
            task_id, "escalate", ci_state=ci.state,
            error=f"{detail}. An autonomous merge requires green CI. Refusing.",
            recommendation=ci_options[0] + f" — {detail}.",
            options=ci_options,
        )

    # 6. Mergeable — GitHub's own verdict on whether the merge is clean, read live.
    pr_state, mergeable = dispatcher._read_pr_status(pr_url, repo_dir)
    if pr_state and pr_state != "open":
        return _refuse(
            task_id, "escalate", pr_state=pr_state,
            error=f"the PR is {pr_state!r}, not open. Refusing to merge.",
            recommendation=f"Check the PR's actual state on GitHub (merged vs. closed) "
                            f"before doing anything else — chela reported it {pr_state!r}, "
                            "which this gate never auto-resolves.",
            options=[
                "Check the PR's actual state on GitHub (merged vs. closed) before doing anything else",
                "If merged, nothing to do — the task is done",
                "If closed without merging, reopen it on GitHub and retry `chela merge`",
            ],
        )
    if mergeable != "MERGEABLE":
        return _refuse(
            task_id, "escalate", pr_mergeable=mergeable,
            error=f"GitHub reports this PR {mergeable or 'un-mergeable'!r}, not "
                  "MERGEABLE (a conflict, or mergeability not yet computed). "
                  "Resolving a conflict is a human's call. Refusing to merge.",
            recommendation=f"Resolve the merge conflict on the branch, push, then retry "
                            f"`chela merge` — GitHub reports this PR "
                            f"{mergeable or 'un-mergeable'!r}.",
            options=[
                "Resolve the merge conflict on the branch, push, then retry `chela merge`",
                "If GitHub hasn't finished computing mergeability yet, wait a moment and retry",
            ],
        )

    # THE GATE HELD. Merge, then record the decision with its justification.
    result = _squash_merge(run, repo_dir, pr_url)
    justification = {
        "task_id": task_id, "pr_url": pr_url, "base": base, "allowed_base": allowed_base,
        "judge_state": judge_state, "ci_state": ci.state, "pr_mergeable": mergeable,
        "reason": reason.strip(), "actor": _actor(actor) or "human",
    }
    if not result.get("ok"):
        event_log.append(
            "orchestrator.merge_failed",
            f"merge of {task_id} passed the gate but gh failed: {result.get('error', '')}",
            payload={**justification, "error": result.get("error")},
        )
        return _refuse(
            task_id, "escalate",
            error=f"the gate held but the merge itself failed: {result.get('error')}",
            recommendation="Read the `gh pr merge` error above and resolve it, then retry "
                            "`chela merge` — the gate held (base/judge/CI/mergeable all "
                            f"passed) but the merge command itself failed: {result.get('error')}.",
            options=[
                "Read the `gh pr merge` error above and resolve it, then retry `chela merge`",
                "Merge it yourself with `gh pr merge` (or on GitHub) once the error is resolved",
            ],
        )

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
