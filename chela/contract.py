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
    2. the judge said ``clean`` on this run, and its verdict is for the PR's CURRENT head
       commit (read live from GitHub) — a ``clean`` recorded against an older commit is not
       evidence about one pushed since;
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

from chela import config, dispatcher, event_log, notify, risktier
from chela.dispatcher import CI_PASSING
from chela.judge import J_CLEAN, J_PROSE_ONLY, MERGE_READY
from chela.personas import lease
from chela.workflow import load_workflow_cached
from chela.worktree import reap_run_worktree

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

# ``gh pr merge`` is a round-trip to GitHub, not a local op — share dispatcher's network
# timeout rather than redefining it here, so the two never drift apart (CMX-262).
GIT_TIMEOUT = dispatcher.GIT_NET_TIMEOUT_SECONDS

# ⚖️🔓 CMX-389 / CMX-79 — `chela merge --override`. The ONE way past the judge, and it is
# not autonomous. Since CMX-79 it is two decoupled halves: the request
# (:func:`request_override` — runs the gate minus the judge, records a request bound to the
# PR's exact head SHA, and RETURNS) and the operator's approval, which completes the merge by
# itself (:func:`complete_override`, called from :func:`chela.overrides.decide` by whichever
# surface the operator pressed — the Telegram button, the dashboard's confirm page, or a
# human's `chela merge-approve` in a plain terminal; never an agent, see
# `chela.overrides.agent_refusal` and the merge-gate hook, chela.mergegate). At that moment
# the gate is re-read live: the head must still be the recorded SHA, and the NEVER line, the
# base, CI green and MERGEABLE all still bind. The override is recorded (who approved, the
# head sha, the judge state it overrode, the reason) to the event log and the run's review
# history BEFORE the merge, and the reason rides in the squash body.
#
# A run a human might reasonably override is not only `awaiting_review`: a judge that
# blocked sends the run to `changes_requested`, and one that gave up sends it to
# `needs_human`. Those are exactly the runs an override exists for (#529).
OVERRIDE_STATUSES = ("awaiting_review", "changes_requested", "needs_human")


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


def _read_pr_files(pr_url: str | None, repo_dir: str | None) -> list[str] | None:
    """⚖️🪜 CMX-208: every path the PR changes, read live; None (never ``[]``) if unreadable."""
    number = dispatcher._pr_number(pr_url)
    if not number or not repo_dir:
        return None
    try:
        out = subprocess.run(["gh", "pr", "diff", number, "--name-only"],
                             cwd=repo_dir, capture_output=True, text=True, timeout=GIT_TIMEOUT)
    except (OSError, subprocess.TimeoutExpired):
        return None
    files = [f.strip() for f in out.stdout.splitlines() if f.strip()]
    return files if out.returncode == 0 and files else None


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


def _squash_merge(run: dict, repo_dir: str, pr_url: str, *, body: str | None = None,
                  match_head: str | None = None) -> dict:
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
    argv = ["gh", "pr", "merge", number, "--squash"]
    if body:
        argv += ["--body", body]
    if match_head:
        # GitHub refuses the merge if the head moved since — an override is approved for
        # ONE commit, never for whatever is pushed while the operator was deciding.
        argv += ["--match-head-commit", match_head]
    try:
        merge = subprocess.run(
            argv,
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
        reap_run_worktree(worktree_path, run.get("workflow_path"))   # CMX-151: a run's processes die with it
        _best_effort(task_id, "worktree-remove",
                     ["git", "worktree", "remove", "--force", worktree_path], repo_dir, 30)
    branch_name = run.get("branch_name")
    if branch_name:
        _best_effort(task_id, "branch-delete",
                     ["git", "branch", "-D", branch_name], repo_dir, 15)
        _best_effort(task_id, "remote-branch-delete",
                     ["git", "push", "origin", "--delete", branch_name], repo_dir, 30)
    return {"ok": True, "merge_commit_sha": merge_sha}


def merge(ident: str, *, reason: str = "", actor: str | None = None,
          override: bool = False) -> dict:
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
       ``cannot_verify`` / never-ran — is a human's call), AND that verdict is for the PR's
       current head commit (read live) — a stale ``clean`` left on the row by a judge that
       raced a later commit is not evidence about the commit actually being merged;
    5. CI is green per GitHub;
    6. GitHub reports the PR open and ``MERGEABLE``;
    7. (CMX-208) the PR's live file list is readable and touches no ALWAYS-HUMAN path, and a
       ``prose_only`` verdict (accepted like ``clean`` in 4) still matches a prose-only diff.

    There is deliberately **no ``--force``**: overriding a gate is an escalation, not an
    autonomous act. ``override=True`` (``chela merge --override --reason``) does NOT merge:
    it is :func:`request_override` — it opens an approval request for the operator and
    returns at once (``{"ok": True, "pending": True, "request_id": …}``). The merge happens
    when the operator approves, via :func:`complete_override`, which skips clause 4 ONLY;
    clauses 1-3 and 5-6 bind it exactly as they bind any merge.

    On success the merge is recorded to the event log with its full justification (the base,
    judge state and CI state it relied on), so a human can later ask *why did it merge that*
    and get a mechanical answer.
    """
    if override:
        return request_override(ident, reason=reason, actor=actor)
    run = dispatcher.resolve_run(ident)
    if run is None:
        return _no_run(ident)
    gate = _gate(run, override=False, actor=actor)
    if not gate.get("ok"):
        return gate
    return _do_merge(run, gate, reason=reason, actor=actor)


def _no_run(ident: str) -> dict:
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


def _gate(run: dict, *, override: bool, actor: str | None,
          expect_head: str | None = None) -> dict:
    """Clauses 1-6 of :func:`merge`'s gate (clause 4 skipped when ``override``), read live.

    ``{"ok": True, base, allowed_base, judge_state, ci, mergeable, pr_url, repo_dir}`` when
    the gate holds, or the refusal. ``expect_head`` (an override's recorded SHA) refuses with
    ``head_moved=True`` when the PR's live head is anything else — checked before CI, since a
    fresh push is also what turns CI pending.
    """
    task_id = run["task_id"]
    if override and run["status"] in OVERRIDE_STATUSES:
        pass
    elif run["status"] != "awaiting_review":
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
    if judge_state not in MERGE_READY and not override:
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
    #
    #    ⚖️🕳️ The SAME read also settles whether the judge's `clean` above is even about THIS
    #    commit. `judge_state='clean'` alone is not evidence about the PR's CURRENT head —
    #    the dispatcher judges ONE PASS PER HEAD COMMIT (`dispatcher.reopen`'s "new-commit
    #    gate" enforces the identical invariant on the human reopen path), and `judge_sha` is
    #    the commit the judge actually verified, stamped at launch and left untouched by a
    #    later verdict write that doesn't reprovision. A judge still mid-run on an OLDER head
    #    — or one whose verdict lost a race and got silently overwritten — leaves
    #    `judge_state='clean'` sitting on the row while a NEW commit has since landed: CI can
    #    be green and GitHub can report MERGEABLE on THAT new commit, and nothing else in
    #    this gate would notice the judge never saw it. Refuses only on a KNOWN mismatch
    #    (both shas read) — an unset `judge_sha` (an older row, or a judge that never stamped
    #    one) is not treated as a positive mismatch, so this never regresses a run this gate
    #    already trusted.
    ci = dispatcher._read_pr_checks(pr_url, repo_dir)
    judge_sha = run.get("judge_sha")
    if judge_sha and ci.head_sha and judge_sha != ci.head_sha and not override:
        return _refuse(
            task_id, "escalate", judge_state=judge_state,
            error=f"the judge's clean verdict was recorded against {judge_sha[:12]!r}, but "
                  f"the PR's current head is {ci.head_sha[:12]!r} — a commit has landed since "
                  "the judge last ran, and this gate never merges a commit the judge has not "
                  "verified. Refusing; the dispatcher re-judges the new head on its own.")

    # An override is approved for ONE commit. A head that moved — or one that cannot be read
    # — is not that commit.
    if expect_head is not None and ci.head_sha != expect_head:
        live = (ci.head_sha or "unreadable")[:12]
        return _refuse(
            task_id, "escalate", head_moved=True,
            error=f"the PR head moved ({expect_head[:12]} → {live}) since the override was "
                  "requested — a new push invalidates the request. Nothing was merged.",
            recommendation="Request a fresh override for the new head (`chela override "
                            "request`), or let the judge re-run on it.",
            options=["Request a fresh override for the new head",
                     "Let the judge re-run on the new head instead"],
        )

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

    # 7. ⚖️🪜 CMX-208 — the risk tier of the LIVE file list (unreadable ⇒ refused). The
    #    override IS the human, so it only carries the always-human trigger into its question.
    files, wf = _read_pr_files(pr_url, repo_dir), load_workflow_cached(wf_path)
    tier, trigger = (None, None) if files is None else risktier.classify(
        files, risktier.always_human(wf.workflow if wf.ok else None))
    if not override:
        problem = None
        if tier is None:
            problem = "the PR's changed files could not be read, so its risk tier is unknown"
        elif tier == risktier.HUMAN:
            problem = f"this PR touches {trigger!r}, an ALWAYS-HUMAN path"
        elif judge_state == J_PROSE_ONLY and tier != risktier.PROSE:
            problem = "the PR was stamped prose-only, but its live diff is not prose-only"
        if problem:
            return _refuse(
                task_id, "escalate", risk_tier=tier, always_human_path=trigger,
                error=f"{problem}. An autonomous merge needs a known, non-human tier. Refusing.",
                recommendation="Review the diff yourself, then `chela merge --override --reason "
                                f"\"…\"` — {problem}.",
                options=["Review the diff yourself, then `chela merge --override --reason \"…\"`",
                         "Retry `chela merge` once the PR's file list can be read"],
            )
    return {"ok": True, "base": base, "allowed_base": allowed_base, "judge_state": judge_state,
            "ci": ci, "mergeable": mergeable, "pr_url": pr_url, "repo_dir": repo_dir,
            "always_human_path": trigger}


def _do_merge(run: dict, gate: dict, *, reason: str, actor: str | None,
              body: str | None = None, match_head: str | None = None,
              audit: dict | None = None) -> dict:
    """THE GATE HELD. Merge, then record the decision with its justification."""
    task_id = run["task_id"]
    base, pr_url, judge_state = gate["base"], gate["pr_url"], gate["judge_state"]
    result = _squash_merge(run, gate["repo_dir"], pr_url, body=body, match_head=match_head)
    justification = {
        "task_id": task_id, "pr_url": pr_url, "base": base,
        "allowed_base": gate["allowed_base"], "judge_state": judge_state,
        "ci_state": gate["ci"].state, "pr_mergeable": gate["mergeable"],
        "reason": reason.strip(), "actor": _actor(actor) or "human",
    }
    if audit is not None:
        justification["override"] = audit
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
        f"merged {task_id} → {base} "
        + (f"(OVERRIDE approved by {audit['approved_by']}, judge {judge_state or 'never ran'!r}, "
           "CI green, mergeable)" if audit else f"(judge {judge_state}, CI green, mergeable)")
        + (f": {reason.strip()}" if reason.strip() else ""),
        payload=justification,
    )
    log.info("contract.merge: %s → %s (sha=%s)", task_id, base, result.get("merge_commit_sha"))
    return {
        "ok": True, "task_id": task_id, "base": base, "pr_url": pr_url,
        "merge_commit_sha": result.get("merge_commit_sha"),
        "branch_name": run.get("branch_name"),
        "event_seq": rec["seq"] if rec else None,
        "override": audit,
    }


def request_override(ident: str, *, reason: str, actor: str | None = None) -> dict:
    """⚖️🔓 Ask the operator to approve merging this run past the judge — and RETURN.

    Runs the whole gate except the judge (clauses 1-3, 5-6 of :func:`merge`, live), then
    records ONE pending request bound to the PR's exact head SHA
    (:func:`chela.overrides.create`) and announces it (event log + push notification; the
    Telegram bridge posts it with Approve / Deny buttons). It never merges and never waits:
    ``{"ok": True, "pending": True, "request_id", "head_sha", …}``. Asking again for the
    same run and head returns the request already pending instead of opening a second one.
    """
    from chela import overrides
    run = dispatcher.resolve_run(ident)
    if run is None:
        return _no_run(ident)
    task_id = run["task_id"]
    reason = (reason or "").strip()
    if not reason:
        return _refuse(
            task_id, "escalate",
            "an override needs a --reason — it is recorded in the audit log, the run's "
            "review history and the squash-merge body",
            recommendation="Retry with --reason \"<why the judge's verdict is wrong or "
                            "irrelevant here>\" — an override with no reason is refused.",
            options=[
                "Retry with --reason \"<why the judge's verdict is wrong or irrelevant here>\"",
                "Drop --override and let the judge re-run on a fixed head instead",
            ],
        )
    gate = _gate(run, override=True, actor=actor)
    if not gate.get("ok"):
        return gate
    head_sha = gate["ci"].head_sha
    if not head_sha:
        return _refuse(
            task_id, "escalate",
            "the PR's head commit could not be read, so an override cannot be bound to "
            "the commit the operator is approving. Refusing.",
            recommendation="Retry once `gh pr checks` can read the PR's head commit — an "
                            "override is approved for one exact commit.",
            options=["Retry once `gh pr checks` can read the PR's head commit",
                     "Let the judge re-run instead of overriding"],
        )
    label = run.get("branch_name") or task_id
    judge_state = gate["judge_state"]
    shown = judge_state or "never ran"
    for existing in overrides.pending():
        if existing.get("task_id") == task_id and existing.get("head_sha") == head_sha:
            return {"ok": True, "pending": True, "existing": True, "task_id": task_id,
                    "request_id": existing["id"], "head_sha": head_sha,
                    "pr_url": gate["pr_url"], "judge_state": judge_state,
                    "question": existing.get("question"), "ttl": existing.get("ttl")}
    trigger = gate.get("always_human_path")
    question = (f"Override-merge {label} into {gate['base']} past the judge ({shown!r} on "
                f"{head_sha[:12]})?"
                + (f" It touches ALWAYS-HUMAN path {trigger!r}." if trigger else "")
                + f" Reason: {reason}")
    meta = {"task_id": task_id, "label": label, "title": run.get("title"),
            "pr_url": gate["pr_url"], "base": gate["base"], "head_sha": head_sha,
            "judge_state": judge_state, "judge_sha": run.get("judge_sha"), "reason": reason,
            "actor": _actor(actor) or "human", "question": question}
    rec = overrides.create(meta)
    if rec is None:
        return _refuse(
            task_id, "escalate",
            "the override request could not be recorded (CHELA_OVERRIDE_WAIT_S is 0, or "
            "chela's overrides directory is not writable) — no request, no override.",
            recommendation="Set CHELA_OVERRIDE_WAIT_S to a positive number of seconds (the "
                            "request's lifetime) and retry.",
            options=["Set CHELA_OVERRIDE_WAIT_S to a positive number of seconds and retry",
                     "Let the judge re-run instead of overriding"],
        )
    request_id = rec["id"]
    event_log.append("orchestrator.merge_override_requested",
                     f"override requested for {label}: {reason}",
                     payload={**meta, "request_id": request_id, "ttl": rec["ttl"]})
    hint = approve_hint(request_id)
    if notify.enabled():
        notify.send(f"{question}\n\n{hint}", title=f"chela: approve override of {label}?")
    return {"ok": True, "pending": True, "task_id": task_id, "request_id": request_id,
            "head_sha": head_sha, "pr_url": gate["pr_url"], "judge_state": judge_state,
            "question": question, "hint": hint, "ttl": rec["ttl"]}


def approve_hint(request_id: str) -> str:
    """Where the operator approves — every surface that works WITHOUT Telegram is named
    (CMX-103): the dashboard's absolute confirm URL (its "Pending approvals" pill lists the
    same request) and ``chela merge-approve``. The Telegram button only when a bridge is
    configured, so a dashboard-only operator is not sent looking for one."""
    url = config.dashboard_url(f"/override/{request_id}")
    tg = "the Telegram button, " if (os.environ.get("TELEGRAM_BOT_TOKEN") or "").strip() else ""
    return (f"Approve with {tg}the dashboard ({url} — or its Pending approvals pill), or in "
            f"a plain terminal: chela merge-approve {request_id}  (deny: add --deny)")


def complete_override(rec: dict, approved_by: str) -> dict:
    """The approved half: merge NOW, re-checking the gate live — or say why not.

    Called by :func:`chela.overrides.decide` once the operator approved request ``rec``.
    ``{"ok", "state": merged|failed|expired, "detail", "merge_commit_sha"?}``. The PR head
    must still be the recorded SHA (else ``expired``), and the base, CI green and MERGEABLE
    must all still hold (else ``failed`` with the reason). The audit is written BEFORE the
    merge — the reconcile's out-of-gate detection must never see this PR merged without the
    override already on the row.
    """
    run = dispatcher.resolve_run(rec.get("task_id") or "")
    if run is None:
        return {"ok": False, "state": "failed", "detail": "the run no longer exists"}
    head_sha = rec.get("head_sha")
    if not head_sha:
        return {"ok": False, "state": "failed", "detail": "the request names no head commit"}
    # The approver is the operator, never the orchestrator's stamp — so no lease clause here.
    gate = _gate(run, override=True, actor="", expect_head=head_sha)
    if not gate.get("ok"):
        state = "expired" if gate.get("head_moved") else "failed"
        return {"ok": False, "state": state, "detail": gate.get("error")}
    if gate["base"] != rec.get("base"):
        return {"ok": False, "state": "failed",
                "detail": f"the PR's base changed ({rec.get('base')!r} → {gate['base']!r})"}
    task_id = run["task_id"]
    label = rec.get("label") or task_id
    judge_state = gate["judge_state"]
    shown = judge_state or "never ran"
    reason = rec.get("reason") or ""
    audit = {"request_id": rec.get("id"), "approved_by": approved_by, "head_sha": head_sha,
             "judge_state": judge_state, "judge_sha": run.get("judge_sha"),
             "reason": reason, "actor": rec.get("actor") or "human", "at": dispatcher._now()}
    dispatcher.record_merge_override(task_id, audit)
    event_log.append("orchestrator.merge_override",
                     f"override of {label} APPROVED by {approved_by} (judge {shown!r} on "
                     f"{head_sha[:12]}): {reason}",
                     payload={**{k: rec.get(k) for k in ("task_id", "label", "pr_url", "base")},
                              **audit})
    body = (f"⚖️ Merged past the judge by an operator-approved override "
            f"(`chela merge --override`, approved by {approved_by}; judge was "
            f"{shown!r} on {head_sha[:12]}).\n\nReason: {reason}")
    result = _do_merge(run, gate, reason=reason, actor=rec.get("actor") or "",
                       body=body, match_head=head_sha, audit=audit)
    if not result.get("ok"):
        return {"ok": False, "state": "failed", "detail": result.get("error")}
    return {"ok": True, "state": "merged", "detail": None,
            "merge_commit_sha": result.get("merge_commit_sha"),
            "event_seq": result.get("event_seq")}


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
