"""The runtime-truth registry — one entry per fact chela's behaviour depends on.

**The bug class, in one sentence: a fact lives in two places across a process boundary,
and the checker reads the copy it OWNS instead of the copy that GOVERNS BEHAVIOUR.** It
landed nine times in two days, and every instance was green everywhere while the feature
was dead:

======== ===================================== =========================================
run      the artifact we wrote                 the artifact that actually ran
======== ===================================== =========================================
CMX-41   ``chela plugin`` rendered port 5001   the dashboard BOUND 5005 — every hook
                                               POSTed into a closed socket
CMX-53   the env file said dispatch ON         the daemon came up OFF — a 9-hour outage
CMX-56   the rendered plugin, ``timeout: 120`` the INSTALLED cache, ``timeout: 2`` — the
                                               gate path was dead all afternoon
CMX-63   ``manifest_drift`` (CMX-56's own fix) compared only ``entries[0]["hooks"][0]``
CMX-65   ``pytest -q`` = 980 green             3 JS suites executed by NOTHING, one RED
======== ===================================== =========================================

chela already solved this once, correctly, for **events**: ``event_log.read()`` is "the ONE
authority the UI and the inbox both read." That discipline was simply never applied to
runtime facts. This module applies it. Two rules, in code rather than in a comment:

**(a) For a fact chela OWNS: the process that ACTS publishes what it actually DID** — not
what it was configured to do. ``dashboard.port`` (the bound socket), ``daemon.json`` (the
daemon's effective capabilities) and the run row's ``window_id`` (CMX-62) are three
accidental inventions of that rule, each after being burned. Readers read the publication.

**(b) For a fact ANOTHER SYSTEM owns: READ BACK FROM THE OWNER.** Claude Code owns the
installed plugin cache; tmux owns window liveness; the pytest collector owns which suites
run. You cannot infer these from your own copy — you must ask.

So a :class:`Fact` names both halves: ``declared_by`` (where *we* write it) and
``owned_by`` (who actually governs the behaviour), plus a ``read_back`` that asks the
owner. :func:`audit` does the same three steps for every fact — read the declared value,
read back the owned value, compare — and ``chela doctor`` is nothing but that loop over
:func:`facts`. **No hand-written check.** Every hand-written check acquires a private
blind spot: that is literally how CMX-63 happened (a compare that looked at the first hook
only) and how CMX-65 happened (a wrapper naming one ``.mjs`` file). An enumerated check
structurally cannot.

**A fact whose owner cannot be read is reported LOUDLY as "CANNOT VERIFY" — never as a
silent pass.** A doctor that goes green because it could not look is the bug it exists to
catch, one level up.

**How this registry is itself kept honest.** It is an artifact, so it can drift from
reality like any other — a fact that stops being read back, or a check that can no longer
go red, would be exactly this bug wearing the registry's hat. The fence is
``tests/test_runtime_truth.py``: every entry in :func:`facts` must have a test that
CORRUPTS the owned value and asserts doctor reports it, naming the fact. A registry entry
with no red test fails the suite. A check that has never been seen to go red is not a
check.
"""
from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from chela import (
    agent_manager,
    capabilities,
    config,
    discovery,
    epoch,
    event_log,
    hold,
    hooks,
    messenger,
    sessions,
)
from chela.sources import get_source
from chela.workflow import load_workflow

OK = "ok"
WARN = "warn"
ERROR = "error"

_SYMBOL = {OK: "✓", WARN: "!", ERROR: "✗"}

# The variables the env file is expected to own. Anything else in it is still sourced;
# these are the ones a drift is worth naming.
KNOWN_VARS = (
    "CHELA_DIR",
    "CHELA_TMUX_SESSION",
    "CHELA_DASHBOARD_PORT",
    "CHELA_DASH_HOST",
    "CHELA_IGNORE_WINDOWS",
    "CHELA_TERMINALS_ENABLED",
    "CHELA_TERMINALS_EXPOSE",
    "CHELA_DISPATCH_WORKFLOWS",
    "CHELA_GATE_WAIT_S",
    "CHELA_GATE_MAX_WAITS",
)


@dataclass(frozen=True)
class Finding:
    """One line of the report. ``fact`` is stamped by :func:`audit`, so every finding says
    which registry entry produced it — the acceptance bar in ``tests/test_runtime_truth.py``
    is written against that name."""

    level: str
    title: str
    detail: str = ""
    fact: str = ""

    def render(self) -> str:
        line = f"{_SYMBOL.get(self.level, '·')} {self.title}"
        return f"{line}\n    {self.detail}" if self.detail else line


@dataclass(frozen=True)
class Observation:
    """What the OWNER says is really in force.

    Three outcomes, and the difference between the last two is the whole point:

    * a **value** — the owner answered;
    * :func:`absent` — the owner is not there (no dashboard is running, no daemon has
      published). A legitimate state, and the fact's own reporter says what it means;
    * :func:`cannot_verify` — the owner IS the authority and could not be read (the plugin
      cache moved between Claude Code releases, tmux is gone). NEVER green: :func:`audit`
      turns it into a loud finding no matter which fact it came from.
    """

    value: Any = None
    detail: str = ""
    missing: str = ""
    unverifiable: str = ""


def observed(value: Any = None, detail: str = "") -> Observation:
    return Observation(value=value, detail=detail)


def absent(reason: str) -> Observation:
    return Observation(missing=reason)


def cannot_verify(reason: str) -> Observation:
    return Observation(unverifiable=reason)


@dataclass(frozen=True)
class Fact:
    """One fact chela depends on, and how to find out what is REALLY in force.

    :param name: stable id — findings carry it, tests assert on it.
    :param declared_by: where *we* write it (an env file, a rendered manifest, a default).
    :param owned_by: who actually governs the behaviour (the bound socket, the running
        daemon, Claude Code's plugin cache, tmux, the pytest collector).
    :param declare: our copy of the fact.
    :param read_back: ask the owner what is really in force → an :class:`Observation`.
    :param report: compare the two, and say so. The only per-fact code there is.
    :param unverifiable_level: how loud an unreadable owner is. ``ERROR`` (exit 1) by
        default — override only where an unreachable owner is a *normal* state of the
        machine rather than a broken fleet, and say why at the entry.
    :param applies: skip a fact that is not a fact of THIS install (the test suites are
        not a fact of an installed wheel).
    """

    name: str
    declared_by: str
    owned_by: str
    declare: Callable[[], Any]
    read_back: Callable[[], Observation]
    report: Callable[[Any, Observation], list[Finding]]
    unverifiable_level: str = ERROR
    applies: Callable[[], bool] = lambda: True


# --- the engine: the same three steps for every fact, forever -----------------------

def audit(fact: Fact) -> list[Finding]:
    """Read the declared value, read back the owned value, compare. That is doctor."""
    try:
        declared = fact.declare()
    except Exception as exc:                       # pragma: no cover - defensive
        return [_stamp(fact, Finding(
            ERROR, f"{fact.name}: cannot read the value chela DECLARES",
            f"{type(exc).__name__}: {exc} — declared by {fact.declared_by}.",
        ))]
    try:
        obs = fact.read_back()
    except Exception as exc:
        obs = cannot_verify(f"{type(exc).__name__}: {exc}")
    if obs.unverifiable:
        return [_stamp(fact, Finding(
            fact.unverifiable_level,
            f"CANNOT VERIFY {fact.name} — {fact.owned_by} did not answer",
            f"{obs.unverifiable}\n"
            f"    chela declares this in {fact.declared_by}, but that copy does not "
            "govern anything: the owner does. Until the owner can be read, chela cannot "
            "say whether this fact is in force — and a check that goes green because it "
            "could not look is the bug it exists to catch.",
        ))]
    try:
        found = fact.report(declared, obs)
    except Exception as exc:                       # pragma: no cover - defensive
        return [_stamp(fact, Finding(
            ERROR, f"{fact.name}: the check itself failed",
            f"{type(exc).__name__}: {exc}",
        ))]
    return [_stamp(fact, f) for f in found]


def audit_all() -> list[Finding]:
    """Every fact in the registry, in the order a human wants to read them."""
    out: list[Finding] = []
    for fact in facts():
        if fact.applies():
            out.extend(audit(fact))
    return out


def _stamp(fact: Fact, finding: Finding) -> Finding:
    return replace(finding, fact=fact.name)


def fact(name: str) -> Fact:
    for f in facts():
        if f.name == name:
            return f
    raise KeyError(name)


# --- fact: the env file, and the environment processes are really running with -------

def _env_file_read() -> Observation:
    path = config.env_file_path()
    if path is None:
        return absent("CHELA_ENV_FILE is empty — the file is switched off")
    if not path.exists():
        return absent(f"no env file at {path}")
    return observed(config.parse_env_file(path))


def _env_file_report(path: Path | None, obs: Observation) -> list[Finding]:
    if obs.missing:
        if path is None:
            return [Finding(WARN, "env file disabled (CHELA_ENV_FILE is empty)",
                            "Config comes from the process environment only.")]
        return [Finding(
            WARN, f"no env file at {path}",
            "Running on defaults / whatever is exported. Copy examples/chela.env there "
            "to make the config a file instead of a habit.",
        )]
    assert path is not None                        # the owner answered, so there IS a file
    declared: dict[str, str] = obs.value
    out = [Finding(OK, f"env file {path} ({len(declared)} vars)")]
    # The file cannot relocate itself: CHELA_DIR is what tells us where to LOOK for it.
    dir_in_file = declared.get("CHELA_DIR")
    if dir_in_file and Path(dir_in_file).expanduser() != path.parent:
        out.append(Finding(
            ERROR, f"{path} sets CHELA_DIR={dir_in_file}, but it lives in {path.parent}",
            "The env file is found *via* CHELA_DIR, so it cannot move itself. Export "
            "CHELA_DIR in the environment (the launcher does), or drop the line.",
        ))
    return out


def declared_env() -> dict[str, str]:
    """What the env file says — chela's own copy of the config."""
    path = config.env_file_path()
    if path is None or not path.exists():
        return {}
    return config.parse_env_file(path)


def _running_env_report(declared: dict[str, str], obs: Observation) -> list[Finding]:
    """The running environment vs the file. A difference is not automatically wrong — an
    explicit export is *meant* to win — but it is always worth saying out loud, because it
    is indistinguishable from ``pm2 restart --update-env`` carrying a stale value."""
    running: dict[str, str] = obs.value
    drifted = [
        (key, running[key], value)
        for key, value in declared.items()
        if key in running and running[key] != value and key in KNOWN_VARS
    ]
    out = [
        Finding(
            WARN, f"{key}: running with {live!r}, env file says {in_file!r}",
            "An exported value wins over the file. If this is a stale PM2 env, "
            "`pm2 restart --update-env` will NOT clear it — `pm2 delete <app>` then "
            "`pm2 start ecosystem.config.js` from a non-tmux shell.",
        )
        for key, live, in_file in drifted
    ]
    if not drifted and declared:
        out.append(Finding(OK, "the running environment agrees with the env file"))
    return out


# --- fact: the tmux session, and the windows runs claim ------------------------------

def _tmux_or_unverifiable() -> str | None:
    """``None`` when tmux cannot be asked at all — the read-back has no owner to talk to."""
    return shutil.which("tmux")


def _session_read() -> Observation:
    if _tmux_or_unverifiable() is None:
        return cannot_verify("tmux is not on PATH, so chela cannot ask it which sessions "
                             "exist. chela IS a tmux orchestrator: with no tmux, nothing "
                             "it reports about the fleet means anything.")
    session = config.current_session()
    return observed(discovery.session_exists(session))


def _session_report(session: str, obs: Observation) -> list[Finding]:
    out: list[Finding] = []
    if os.environ.get("CHELA_TMUX_SESSION"):
        source = "CHELA_TMUX_SESSION"
    elif os.environ.get("TMUX_PANE"):
        source = "$TMUX_PANE"
        out.append(Finding(
            WARN, f"tmux session {session!r} — DERIVED from $TMUX_PANE, not configured",
            "Correct for an agent in its own pane; wrong for a service, where a leaked "
            "TMUX_PANE silently targets a webterm_* mirror session. Services must start "
            "via scripts/run-chela.sh (`env -u TMUX -u TMUX_PANE`).",
        ))
    else:
        source = "default"
    if obs.value:
        out.append(Finding(OK, f"tmux session {session!r} exists ({source})"))
    else:
        out.append(Finding(
            WARN, f"tmux session {session!r} does NOT EXIST — tmux says so ({source})",
            "chela's whole fleet lives in this session: every window lookup, every "
            "send-keys, the dispatcher and the terminal wall resolve against it, and they "
            "will all find nothing. Start it (`chela start`), or point "
            "CHELA_TMUX_SESSION at the session that is really there.",
        ))
    return out


def _in_flight_runs() -> dict[str, dict]:
    """``{task_id: {wid, epoch}}`` for every run that CLAIMS a live tmux window.

    The id is recorded in the run row at spawn (CMX-62) — rule (a): the process that acts
    writes down what it did — together with the tmux epoch that ISSUED it (CMX-77), because
    an id without its epoch is a number, not a window. Rows that predate either carry no id
    (or no stamp) and are honestly unknown: guessing one is worse than not knowing (CMX-48).
    """
    from chela import dispatcher                    # lazy: doctor must import cheaply

    if not Path(dispatcher.DB_PATH).exists():
        return {}
    return {
        str(run["task_id"]): {"wid": str(run["window_id"]),
                              "epoch": run.get("window_epoch")}
        for run in dispatcher.list_runs()
        if run.get("status") in ("claimed", "running") and run.get("window_id")
    }


def _windows_read() -> Observation:
    if _tmux_or_unverifiable() is None:
        return cannot_verify("tmux is not on PATH — chela cannot ask which windows are "
                             "alive, so it cannot tell a working agent from a corpse.")
    return observed({"windows": discovery.get_windows_by_id(), "epoch": epoch.current()})


def _windows_report(claimed: dict[str, dict], obs: Observation) -> list[Finding]:
    """A claimed window is alive only if tmux has that id AND the id is still THEIRS.

    ⛔ "tmux has no such window" was never the whole check. After a server restart tmux has
    ``@3`` again — issued afresh, to somebody else (CMX-77) — so a run row claiming ``@3``
    reads as perfectly healthy while its agent is long dead and a stranger sits at that
    address. Both halves are checked: the id, and the epoch that issued it.
    """
    live: dict[str, str] = obs.value["windows"]
    now: str | None = obs.value["epoch"]
    if not claimed:
        return []                                  # nothing is in flight — nothing to check
    dead, reissued = {}, {}
    for task, claim in claimed.items():
        wid = claim["wid"]
        if epoch.is_dangling(claim.get("epoch"), now):
            reissued[task] = wid
        elif wid not in live:
            dead[task] = wid
    out = [
        Finding(
            WARN, f"run {task} claims window {wid}, but tmux has no such window",
            "The agent is gone and the run row still says it is working. Reconciliation "
            "closes this out (`completed_gone`) on the next dispatch tick and frees the "
            "concurrency slot — but reconciliation rides the dispatch tick (CMX-53), so "
            "with the dispatcher OFF nothing ever will, and the slot is held by a corpse.",
        )
        for task, wid in sorted(dead.items())
    ]
    out += [
        Finding(
            ERROR,
            f"run {task} claims window {wid} — but that id was issued by a tmux server that "
            "is GONE, and tmux has since given it to somebody else",
            f"The tmux server restarted ({epoch.describe(claimed[task].get('epoch'))} → "
            f"{epoch.describe(now)}), which killed this run's agent and renumbered the fleet. "
            f"The row still says it is working, and {wid} is now "
            f"{live.get(wid, '(nothing)')!r} — so every id-keyed surface would file this "
            "dead run's events under a LIVE agent. Reconciliation frees the slot on the next "
            "dispatch tick; with the dispatcher OFF, nothing ever will.",
        )
        for task, wid in sorted(reissued.items())
    ]
    if not out:
        out.append(Finding(OK, f"{len(claimed)} in-flight run(s): every claimed tmux window "
                               "is alive, under the server that issued its id"))
    return out


# --- fact: the branch a parked run must return to, and whether git still has it -------
#
# The rework loop (CMX-68) parks a run in `changes_requested` (the reviewer sent the PR
# back) or `needs_human` (the loop hit its cap) and comes back to it LATER — re-spawning
# the agent in its own worktree, on its own branch, so the branch history and the open PR
# survive. The run row is chela's copy of that claim. **git owns whether it is true.** A
# branch deleted in between (a tidy-up, a `git push --delete`, a worktree pruned by hand)
# turns the whole loop into a run that can never resume — and nothing would say so: the
# row still reads `changes_requested`, and the dispatcher only finds out at the moment it
# tries. Rule (b): read back from the owner.


# How long a run may sit in `changes_requested` before the doctor says so. A sent-back run
# is normally re-spawned within ONE tick (60s); it legitimately waits longer only while every
# concurrency slot is busy. An hour of waiting is either a very long queue or — the case this
# exists for — a run nothing will EVER come back for: its workflow was dropped from
# CHELA_DISPATCH_WORKFLOWS, or a hold was taken and forgotten. Neither says anything today.
PARKED_STALL_SECONDS = 3600


def _seconds_since(stamp: str | None) -> float | None:
    if not stamp:
        return None
    try:
        when = datetime.fromisoformat(str(stamp))
    except (TypeError, ValueError):
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - when).total_seconds()


def _parked_runs() -> dict[str, dict]:
    """Every run parked in the review loop — the branch it claims, and how long it has sat.

    ``{task_id: {branch, worktree, repo, workflow, status, waiting}}``. ``waiting`` is the
    age of the last verdict (``review_history[-1].at`` — the moment the reviewer sent it
    back), or None when it cannot be read; it is what turns "parked" into "STUCK".
    """
    from chela import dispatcher                    # lazy: doctor must import cheaply

    if not Path(dispatcher.DB_PATH).exists():
        return {}
    parked: dict[str, dict] = {}
    for run in dispatcher.list_runs():
        if run.get("status") not in ("changes_requested", "needs_human"):
            continue
        branch, wf_path = run.get("branch_name"), run.get("workflow_path")
        if not branch or not wf_path:
            continue                               # nothing claimed — nothing to check
        reviews = dispatcher.reviews_of(run)
        sent_back_at = reviews[-1].get("at") if reviews else run.get("ended_at")
        parked[str(run["task_id"])] = {
            "branch": str(branch),
            "worktree": str(run.get("worktree_path") or ""),
            "repo": str(Path(wf_path).parent),
            "workflow": str(wf_path),
            "status": str(run.get("status")),
            "waiting": _seconds_since(sent_back_at),
        }
    return parked


def _git_branches(repo: str) -> set[str] | None:
    """Every local branch in ``repo`` — or None when git could not answer for it."""
    out = subprocess.run(
        ["git", "-C", repo, "for-each-ref", "--format=%(refname:short)", "refs/heads/"],
        capture_output=True, text=True, timeout=15,
    )
    if out.returncode != 0:
        return None
    return {line.strip() for line in out.stdout.splitlines() if line.strip()}


def _parked_read() -> Observation:
    if shutil.which("git") is None:
        return cannot_verify("git is not on PATH — chela cannot ask whether the branches "
                             "its parked runs point at still exist, and a rework that "
                             "cannot find its branch has lost the work it was sent back "
                             "to fix.")
    parked = _parked_runs()
    if not parked:
        return observed({})                        # nothing parked — nothing to ask about
    by_repo: dict[str, set[str]] = {}
    for repo in sorted({p["repo"] for p in parked.values()}):
        branches = _git_branches(repo)
        if branches is None:
            return cannot_verify(f"git could not list the branches of {repo} — it is the "
                                 "repo a parked run has to go back into")
        by_repo[repo] = branches
    return observed(by_repo)


def _stalled_report(parked: dict[str, dict]) -> list[Finding]:
    """Is anything ever going to COME for these runs? (The other half of "parked".)

    A `changes_requested` run is a promise: the next tick re-spawns it. Three ordinary
    conditions break that promise silently — the workflow is not in the daemon's
    CHELA_DISPATCH_WORKFLOWS, the queue is on hold, or the run has simply been sitting far
    longer than a tick — and in all three the run waits forever with a verdict against it
    and no agent coming. `changes_requested` emits no event of its own once it is parked
    (only on the edge), so without this the silence is total.
    """
    waiting = {t: c for t, c in parked.items() if c.get("status") == "changes_requested"}
    if not waiting:
        return []
    dispatched = {str(p) for p in config.DISPATCH_WORKFLOWS}
    out: list[Finding] = []
    for task, claim in sorted(waiting.items()):
        wf_path = claim.get("workflow") or ""
        if str(Path(wf_path).resolve()) not in dispatched:
            out.append(Finding(
                ERROR,
                f"run {task} is sent back for rework, but NOTHING dispatches {wf_path}",
                "The reviewer failed its PR and the run is waiting to be re-spawned — and "
                "this workflow is not in CHELA_DISPATCH_WORKFLOWS, so no tick will ever "
                "come for it. It will sit in `changes_requested` forever, holding its "
                "branch, its worktree and an open PR. Add the workflow to the daemon's env "
                f"and restart it, or turn the loop by hand: chela dispatch {wf_path}",
            ))
            continue
        age = claim.get("waiting")
        if age is not None and age >= PARKED_STALL_SECONDS:
            out.append(Finding(
                WARN,
                f"run {task} has been sent back for rework for {int(age // 3600)}h and "
                "still has not been re-spawned",
                "Legitimate only while every concurrency slot is busy — a rework normally "
                "restarts on the NEXT tick. Otherwise the daemon is not ticking this "
                "workflow (check it is running), its WORKFLOW.md does not parse (dispatch "
                "is blocked until it does), or a hold was taken and forgotten (`chela hold "
                "--release`): a hold pauses claims, and a re-spawn is a claim.",
            ))
    return out


def _parked_report(parked: dict[str, dict], obs: Observation) -> list[Finding]:
    by_repo: dict[str, set[str]] = obs.value or {}
    if not parked:
        return []                                  # nothing is parked — nothing to check
    gone = [
        (task, claim) for task, claim in sorted(parked.items())
        if claim["branch"] not in by_repo.get(claim["repo"], set())
    ]
    out = [
        Finding(
            ERROR,
            f"run {task} is parked on branch {claim['branch']!r} — git has no such branch",
            "The run is waiting to be re-spawned into that branch (the rework loop), and "
            "the branch is gone: its commits and its open PR are unreachable, so the "
            "rework can never resume. The dispatcher escalates it to `needs_human` the "
            "moment it tries — this says so BEFORE it tries. Restore the branch (it may "
            "still be on the remote: `git fetch origin "
            f"{claim['branch']}:{claim['branch']}`) or close the run out by hand.",
        )
        for task, claim in gone
    ]
    if not gone:
        out.append(Finding(OK, f"{len(parked)} run(s) parked in review: every branch git "
                               "has to hand back still exists"))
    # The branch existing is not enough — something has to COME for it.
    return out + _stalled_report(parked)


# --- fact: the CHECKS on a PR that is waiting to be merged ----------------------------
#
# Rule (b), and the most expensive instance of it so far. On 2026-07-14 chela did not know a
# CI run existed: `_pr_state` read `state,mergeable` — and `mergeable` is GitHub's
# MERGE-CONFLICT field, not its checks. PR #80 was red, was reviewed, was merged, and `dev`
# was broken until a hotfix. The agent had said "tests pass" (true, on ITS machine); nobody
# asked the system that owns the answer. This fact asks it.


def _reviewed_prs() -> dict[str, dict]:
    """Every run parked in ``awaiting_review`` with a live PR — and what OUR row says its
    checks are (``pr_checks``, the cache the tick refreshes and the merge buttons read)."""
    from chela import dispatcher                    # lazy: doctor must import cheaply

    if not Path(dispatcher.DB_PATH).exists():
        return {}
    out: dict[str, dict] = {}
    for run in dispatcher.list_runs():
        if run.get("status") != "awaiting_review":
            continue
        pr, wf_path = run.get("pr_url"), run.get("workflow_path")
        if not pr or not wf_path or run.get("pr_state") not in (None, "open"):
            continue                               # nothing to ask about, or already shipped
        out[str(run["task_id"])] = {
            "pr": str(pr),
            "repo": str(Path(wf_path).parent),
            "checks": str(run.get("pr_checks") or "unread"),
        }
    return out


def _gh_pr_checks(pr_url: str, repo: str):
    """Ask GitHub. A seam, so the suite can hand this an ANSWER instead of a network."""
    from chela import dispatcher

    return dispatcher._read_pr_checks(pr_url, repo)


def _checks_read() -> Observation:
    from chela import dispatcher

    parked = _reviewed_prs()
    if not parked:
        return observed({})                        # nothing under review — nothing to ask
    live: dict[str, str] = {}
    for task, claim in sorted(parked.items()):
        ci = _gh_pr_checks(claim["pr"], claim["repo"])
        if ci.state == dispatcher.CI_UNKNOWN:
            return cannot_verify(
                f"GitHub could not be asked about the checks on {claim['pr']} ({ci.detail}). "
                "⛔ That is NOT a pass: an unread check state is exactly what let a red PR "
                "merge and break the base branch, and chela will refuse to merge this one "
                "until the answer can be read.")
        live[task] = ci.state
    return observed(live)


def _checks_report(declared: dict[str, dict], obs: Observation) -> list[Finding]:
    from chela import dispatcher

    live: dict[str, str] = obs.value or {}
    if not declared:
        return []
    out: list[Finding] = []
    for task, claim in sorted(declared.items()):
        state = live.get(task)
        if state is None:
            continue
        if state == dispatcher.CI_FAILING:
            out.append(Finding(
                ERROR,
                f"run {task} is waiting to be merged and its CI is RED",
                "GitHub says this PR's checks are failing, and the run is sitting in "
                "`awaiting_review` — which is where a human (or the Merge button) picks it "
                "up. The dispatcher sends a red PR back to its agent on the next tick, so "
                "this normally clears itself within one poll. If it does NOT: nothing is "
                "ticking this workflow, and the PR is one click away from breaking the base "
                f"branch. Look: {claim['pr']}",
            ))
        elif claim["checks"] != state:
            out.append(Finding(
                WARN,
                f"run {task}: chela's copy of the check state is stale "
                f"({claim['checks']!r}; GitHub says {state!r})",
                "Harmless in itself — the tick refreshes it — but the Kanban's Merge button "
                "and the batch merge read that copy. A stale copy that says `passing` is the "
                "shape of the original bug. If it does not correct itself on the next tick, "
                "the dispatcher is not polling this PR.",
            ))
    if not out:
        out.append(Finding(OK, f"{len(declared)} PR(s) awaiting review: GitHub's checks "
                               "agree with what chela recorded, and none are red"))
    return out


# --- fact: the port the dashboard actually BOUND -------------------------------------

def _port_read() -> Observation:
    live = config.live_dashboard()
    if live is None:
        return absent("nothing has published dashboard.port")
    return observed(int(live["port"]), detail=f"pid {live['pid']}")


def _port_report(configured: int, obs: Observation) -> list[Finding]:
    if obs.missing:
        return [Finding(
            OK, f"dashboard port {configured} (configured; no dashboard running)",
            f"Nothing has published {config.dashboard_port_file()} — a `chela plugin` "
            "rendered now targets the configured port.",
        )]
    port = obs.value
    if port != configured:
        return [Finding(
            ERROR,
            f"dashboard is LISTENING on {port}, but the config says {configured}",
            "A --port flag beats the env, and the env is supposed to be the source of "
            f"truth. Set CHELA_DASHBOARD_PORT={port} in the env file and restart "
            "the dashboard without --port. (`chela plugin` follows the live port, so "
            "hooks still work — but the next clean start will not.)",
        )]
    return [Finding(OK, f"dashboard listening on {port} ({obs.detail})")]


# --- fact: is the dashboard's update-apply lock actually held by a live process? -----
#
# CMX-226's `/api/update/apply` route already tells an operator who clicks Update AGAIN
# that a held lock looks wedged (`stuck: true` in its 409) — but that is visible only to
# someone who clicks. `chela doctor` (a human's own CLI invocation) and the daemon's
# periodic notify edge both run in a DIFFERENT process from the dashboard
# (chela-daemon, not chela-dashboard), so neither can see `chela.dashboard.app`'s
# in-process `_update_apply_lock` / `_update_apply_started_at` directly — the exact
# cross-process gap `daemon.capabilities` and `dashboard.port` above already solve, and
# solved here the same way: the dashboard PUBLISHES the hold
# (`config.publish_update_apply_lock`, called right beside where the route sets
# `_update_apply_started_at`) and this fact reads that back (`config.live_update_apply_
# lock`), pid-checked exactly like `live_dashboard` — a file whose pid is dead is a lock
# the process holding it died with, and a restarted dashboard already handed out a
# fresh, unheld `threading.Lock()`. Without this, a stuck lock silently disables the
# dashboard's whole deploy path (CMX-199/200's whole premise) for anyone who never
# clicks Update a second time to find out.

def _update_apply_lock_read() -> Observation:
    live = config.live_update_apply_lock()
    if live is None:
        return observed(None)
    return observed(live)


def _update_apply_lock_report(_declared: None, obs: Observation) -> list[Finding]:
    from chela import update                        # lazy: doctor must import cheaply

    live = obs.value
    if live is None:
        return [Finding(OK, "update-apply lock is not held")]
    elapsed = int(time.time() - live["started_at"])
    ceiling = update.apply_stuck_after_seconds()
    if elapsed <= ceiling:
        return [Finding(
            OK, f"update-apply lock has been held {elapsed}s (pid {live['pid']}) — "
                f"within the {ceiling}s ceiling for an honest run")]
    return [Finding(
        WARN,
        f"update-apply lock has been held {elapsed}s (pid {live['pid']}) — past the "
        f"{ceiling}s ceiling any honest `update.apply()` run can take",
        "Every subprocess apply() shells out to is individually timeout-bounded (see "
        "chela/update.py's GIT_TIMEOUT_SECONDS / _SHELL_TIMEOUT_SECONDS), so a hold "
        "this long is not a slow run in progress — it's a wedged lock (the background "
        "thread that owned it died without reaching its own `finally: release()`) that "
        "nothing but a restart clears, and until then the dashboard's Update control "
        "refuses every click. `pm2 restart chela-dashboard` clears it.",
    )]


# --- fact: the native `claude agents --json` status feed the dashboard polls --------
# CMX-179: the timeout guarding this call (`agent_manager._STATUS_CMD_TIMEOUT`) was BELOW
# the command's real warm-start cost, so every call failed and the dashboard's busy/idle
# pills silently froze fleet-wide — for 12 days, with 17,411 identical WARNING log lines
# and nothing else saying so. The timeout is fixed, but a regression of the same shape
# (the command gets slower again, or breaks outright) would be exactly as silent without a
# check that actually asks it. This one does, right now, every `chela doctor` run.

def _native_status_probe() -> tuple[bool, str] | None:
    """A seam: the real answer is a fresh `claude agents --json` call (costs up to
    ``agent_manager._STATUS_CMD_TIMEOUT`` seconds for real); the suite hands this a fixed
    one instead of shelling out. ``None`` means `claude` itself could not be asked (not on
    PATH) — same shape as :func:`_gh_auth_status`, and for the same reason: the PATH check
    must live INSIDE the seam, not in the caller, or a fixture that replaces this whole
    function (as the test suite does) cannot bypass it."""
    if shutil.which("claude") is None:
        return None
    return agent_manager.probe_native_status_feed()


def _native_status_read() -> Observation:
    if config.live_dashboard() is None:
        # Nobody is polling this feed right now — nothing for the fact to report on, and
        # probing it anyway would tax every `chela doctor` run on an idle install for free.
        return absent("no dashboard is running — nothing is polling the native status feed")
    result = _native_status_probe()
    if result is None:
        return cannot_verify(
            "`claude` is not on PATH, so chela cannot ask whether the native status feed "
            "answers — and the dashboard's busy/idle pills would fail the exact same way.")
    ok, detail = result
    return observed({"ok": ok, "detail": detail})


def _native_status_report(_declared: None, obs: Observation) -> list[Finding]:
    if obs.missing:
        return [Finding(OK, f"native status feed: {obs.missing}")]
    result = obs.value
    if not result["ok"]:
        return [Finding(
            ERROR,
            f"claude agents --json did NOT answer ({result['detail']})",
            "The dashboard's busy/idle status for every window is served from a cache "
            "that only updates on a SUCCESSFUL call — a persistent failure here freezes "
            "every status pill fleet-wide at its last-known value, silently. Run `claude "
            "agents --json` by hand to see the real error, and check "
            "agent_manager._STATUS_CMD_TIMEOUT against how long it actually takes.",
        )]
    return [Finding(OK, f"claude agents --json answers ({result['detail']})")]


# --- fact: the two plugin manifests, and only one of them runs -----------------------

def _rendered_path() -> Path:
    return config.CHELA_DIR / "plugin" / "hooks" / "hooks.json"


def _rendered_read() -> Observation:
    path = _rendered_path()
    if not path.exists():
        # Nothing rendered: the operator has not run `chela plugin`, so they are not using
        # hooks and there is nothing to be stale. Step one is `chela plugin`.
        return absent("nothing rendered — `chela plugin` has not been run")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return cannot_verify(
            f"{path} is there but unreadable ({exc}), so chela cannot say what the hooks "
            f"it would REINSTALL declare. Re-render it: `chela plugin --dir "
            f"{path.parent.parent}`.")
    if not isinstance(data, dict):
        return cannot_verify(f"{path} is not a manifest object")
    return observed(data)


def _rendered_report(expected: dict, obs: Observation) -> list[Finding]:
    if obs.missing:
        return []
    path = _rendered_path()
    drift = hooks.manifest_drift(obs.value, expected)
    if drift:
        return [Finding(
            ERROR, f"the rendered plugin at {path} is STALE",
            "This is the copy the plugin is INSTALLED from, so a stale one here "
            "reinstalls stale:\n"
            + _lines(drift)
            + f"\n    Re-render it: `chela plugin --dir {path.parent.parent}` — then "
            "reinstall it (see below).",
        )]
    return [Finding(OK, f"rendered plugin posts to port {effective_port()} ({path})")]


def _installed_read() -> Observation:
    return observed(hooks.installed_plugins())


def _installed_report(expected: dict, obs: Observation) -> list[Finding]:
    """The manifest an agent actually loads. Found by DISCOVERY (the install path is
    recorded by Claude Code, and carries the plugin version) — never by a path we build,
    which a version bump would silently invalidate."""
    copies = obs.value
    if not copies:
        return [Finding(
            ERROR, "chela's plugin is rendered but NOT INSTALLED — no agent runs its hooks",
            "Nothing under "
            f"{hooks.plugins_dir()} claims to be the chela plugin, so the manifest chela "
            "renders is a file nobody reads: no events, no gates, no phone answers. "
            "Install it from Claude Code — `/plugin marketplace add "
            f"{config.CHELA_DIR / 'plugin'}` then `/plugin install chela@chela` — or, if "
            "it IS installed, chela cannot see where: Claude Code's plugin cache is an "
            "implementation detail, and this check refuses to pass without reading the "
            "manifest that actually runs.",
        )]
    out: list[Finding] = []
    for copy in copies:
        if copy.hooks is None:
            out.append(Finding(
                ERROR, f"cannot verify the INSTALLED plugin at {copy.manifest}",
                f"{copy.error}. That copy — not the one chela renders — is what every "
                "agent loads at startup, so chela cannot say whether the hooks work. "
                "Reinstall it from Claude Code (`/plugin install chela@chela`).",
            ))
            continue
        drift = hooks.manifest_drift(copy.hooks, expected)
        if drift:
            out.append(Finding(
                ERROR,
                "the INSTALLED plugin disagrees with the one chela renders — "
                "THE HOOKS THAT RUN ARE STALE",
                f"Agents do not read the manifest chela renders. They read:\n"
                f"    {copy.manifest}\n"
                f"    (found via {copy.found_via}; plugin version "
                f"{copy.version or 'unknown'})\n"
                + _lines(drift)
                + "\n    Fix: `chela plugin`, then in Claude Code `/plugin uninstall "
                "chela@chela` + `/plugin install chela@chela` to refresh that copy. "
                "Hooks are read at agent STARTUP — a running agent keeps the stale ones "
                "until it is restarted.",
            ))
        else:
            out.append(Finding(
                OK,
                f"installed plugin matches the rendered one (v{copy.version or '?'})",
                f"the manifest agents actually load: {copy.manifest} "
                f"(found via {copy.found_via})",
            ))
    return out


def effective_port() -> int:
    """The port a hook must POST to — the live one when a dashboard is running."""
    return config.live_dashboard_port()


def installed_hooks_stale() -> bool:
    """True when at least one INSTALLED copy of the plugin disagrees with what
    ``hooks.hooks_spec()`` renders right now — the exact same comparison
    ``plugin.installed`` uses above (:func:`hooks.installed_plugins`,
    :func:`hooks.manifest_drift`), reused rather than reimplemented so `chela update`'s
    post-update reminder can never drift from what `chela doctor` checks.

    ``False`` when nothing is installed at all — that is a DIFFERENT problem
    (``plugin.installed`` already reports it loudly) and not what this reminder is for: an
    update has nothing to remind about a plugin the operator never installed.
    """
    expected = hooks.hooks_spec(effective_port())
    return any(
        copy.hooks is not None and hooks.manifest_drift(copy.hooks, expected)
        for copy in hooks.installed_plugins()
    )


def _plugin_applies() -> bool:
    """A machine with nothing rendered is not running hooks; an INSTALLED copy still has
    to be checked though — a plugin installed from a checkout renders nothing here."""
    return _rendered_path().exists() or bool(hooks.installed_plugins())


# --- fact: the INSTALLED plugin matches, so an agent SHOULD be firing hooks — but does
# one actually reach the event log? -----------------------------------------------------
#
# `plugin.rendered`/`plugin.installed` above compare MANIFESTS — url, timeout, command —
# and stop there. CMX-41 was exactly a manifest that matched on paper while every hook
# POSTed into a closed socket, and every hook fails OPEN by design (`curl ... || true`,
# `SessionStart`'s recap: "fail open"): a broken POST tells the agent NOTHING and tells
# chela nothing either, anywhere but here. So this fact goes one link further than the
# manifest and asks the one place a hook's arrival is ever recorded — the event log —
# whether a hook from a LIVE, already-past-boot agent is actually in it. A window whose
# claude process has been running long enough that `SessionStart` has certainly fired (it
# runs at process start, before anything else) and that has produced no `hook.*` event at
# all is either still POSTing into a dead port, or was launched before the plugin was
# installed and needs a restart to pick it up — either way, something chela can now say
# out loud instead of a phone that quietly never rings.

# Long enough after process start that SessionStart has certainly fired and been received
# (it runs first, before any other hook) — short enough that the bounded event-log ring
# (CHELA_EVENTS_RING) has not plausibly rolled past it on a busy fleet. Windows outside
# this band are not asked: too new to judge, or too old for "nothing recent" to mean
# anything (the ring ages out, not the hook).
_HOOK_GRACE_SECONDS = 20.0
_HOOK_STALE_SECONDS = 600.0


def _hooks_flowing_declared() -> dict[str, sessions.Pane]:
    """Live claude-agent windows old enough that ``SessionStart`` should long since have
    fired and been received."""
    now = time.time()
    return {
        wid: pane for wid, pane in sessions.panes().items()
        if pane.claude_pid and pane.started
        and _HOOK_GRACE_SECONDS < now - pane.started < _HOOK_STALE_SECONDS
    }


def _hooks_flowing_read() -> Observation:
    """The event log's own copy: the newest hook-sourced event seen from each window."""
    seen: dict[str, float] = {}
    for rec in event_log.ring():
        wid = rec.get("wid")
        rtype = rec.get("type") or ""
        if wid and rtype.startswith(hooks.TYPE_PREFIX):
            seen[wid] = max(seen.get(wid, 0.0), rec.get("ts") or 0.0)
    return observed(seen)


def _hooks_flowing_report(declared: dict[str, sessions.Pane],
                          obs: Observation) -> list[Finding]:
    if not declared:
        return []                       # no live agent old enough to judge yet
    seen: dict[str, float] = obs.value
    silent = [
        wid for wid, pane in sorted(declared.items())
        if seen.get(wid) is None or seen[wid] < pane.started
    ]
    if silent:
        return [Finding(
            ERROR,
            f"{len(silent)} live agent window(s) have fired NO hook: {', '.join(silent)}",
            "Claude Code has the plugin installed (see plugin.installed) and this window's "
            "claude process has been running well past when `SessionStart` fires — but the "
            "event log, the ONLY place a hook's arrival is ever recorded, has nothing from "
            "it. Hooks fail OPEN, so a stale dashboard port (CMX-41), a firewalled "
            "loopback, or a hook script error reports NOTHING to the agent and NOTHING "
            "here until this check. The other likely cause is the same fix either way: "
            "the agent was launched BEFORE the plugin was (re)installed — hooks are read "
            "at agent startup, so a running agent keeps none until it is restarted. "
            "Confirm `plugin.installed` and `dashboard.port` agree, then restart the "
            "agent.",
        )]
    return [Finding(
        OK, f"{len(declared)} live agent window(s): hooks are reaching the event log")]


def _hooks_flowing_applies() -> bool:
    return bool(hooks.installed_plugins())


# --- fact: a hook DID reach the log, but chela could not say whose window it was ------
#
# `plugin.hooks_flowing` above asks "did ANY hook arrive from this live window" and stops
# there. It misses the opposite shape entirely: a hook arrives, is appended, and STILL
# never lands in that window's lane, because `hooks.wid_for_session` (CMX-48/CMX-190)
# resolved to None. `chela/hooks.py:ingest` deliberately keeps `session_id` on that record
# rather than dropping it (an unattributed event is "visibly ownerless", never worse than
# a misattributed one — the same rule `chela/inbox.py` states for its own `wid=None` rows)
# — but nothing ever READ that copy back. The record sat in the log, correlatable by
# session_id, and `chela events --wid @N` could never surface it: an agent was hook-blind
# for a whole session and nothing said so out loud.
#
# CMX-227 measured *why* against four days of this host's own production log (~13.2k
# `hook.*` records, 81 sessions, 2077 orphaned events across 53 sessions) instead of
# guessing. The breakdown does not match what this fact used to imply:
#
#   ~95% (1981/2077 events, ~40/53 sessions) — the session never ran in a chela-tracked
#   tmux window AT ALL, ever, on any record. Every one of these traced to a slug under
#   `~/.claude/projects/…/memory` (or a sibling project's `memory` dir): the nightly
#   `dream.py` memory-consolidation job, which launches a headless, non-interactive
#   `claude` process outside of any tmux pane chela manages. `chela`'s hooks plugin is
#   installed globally, so these headless runs POST hooks too — but there is no window id
#   to resolve, ever, by design, the same way `hooks._explicit_wid` treats "no $CHELA_WID"
#   as a non-fault ("a session chela did not launch"). This is NOT a hole in attribution;
#   it is this fact crying wolf against a case it never named as a possibility.
#
#   ~4% (77/2077 events, 2 sessions) — genuine CMX-190 ambiguity: two sessions sharing one
#   origin cwd with overlapping active windows (both were the `-home-liavedunix` orchestrator
#   slug — a relaunch overlapping its predecessor's tail events). Confirmed real, and rare.
#
#   ~1% (19/2077 events, 11 sessions) — a teardown race, always the LAST one or two events
#   of the session (`hook.session_end`, sometimes preceded by one `hook.post_tool_use`),
#   always on a dispatcher-spawned worktree agent. The agent's own final action
#   (`chela task-finished`) kills its own tmux window as its last act, so its own
#   `SessionEnd` POST resolves after the window it names is already gone. Structural and
#   self-inflicted, not a generic timing flake — "the window closing between the tool call
#   and the POST landing" the old comment named, but only ever the session's own suicide.
#
# Recommendation (not implemented here — this was an investigation, not a fix): the WARN
# below should stop leading with CMX-190/teardown-race, since together they are ~5% of
# what actually fires it. A follow-up could downgrade or filter sessions that never once
# resolved a wid AND never appear in `chela.sessionids.entries()` — the headless-job shape
# — so the fact reserves WARN for the ambiguity/race shapes it can actually act on.
#
# Every `hook.*` record has a real Claude Code session behind it (unlike the inbox's own
# bookkeeping rows, which legitimately belong to "chela itself") — so `wid=None` here is
# never chela's OWN bookkeeping wearing no owner on purpose. But per the measurement above,
# it is very often a session that was never chela's WINDOW to begin with (a headless job) —
# a hole in *this fact's naming of the cause*, not necessarily a hole in attribution.

def _ring_bound_note(records: list[dict]) -> str:
    """What :func:`chela.event_log.ring` actually let a fact see — bounded and rolling
    (``deque(maxlen=RING_SIZE)``, "live reads never touch the rolled files", verbatim).

    A fact that scans the ring and finds nothing must never say so as an unqualified
    universal claim: the ring can just as easily be silent because the evidence SCROLLED
    OUT as because the fault never happened. Naming the window scanned is what keeps a
    green here from being over-read as "never happened" when it only means "not in the
    last N events".
    """
    if not records:
        return "the ring is empty — nothing has been logged yet"
    first, last = records[0].get("seq"), records[-1].get("seq")
    return f"last {len(records)} event(s) in the ring (seq {first}-{last})"


def _hooks_unattributed_read() -> Observation:
    """Every ``hook.*`` record in the ring that landed with no window but still names a
    session — the copy :func:`chela.hooks.ingest` actually wrote, read back exactly as a
    ``--wid`` filter would see it (or rather, would fail to)."""
    records = event_log.ring()
    orphans: dict[str, int] = {}
    for rec in records:
        rtype = rec.get("type") or ""
        if rec.get("wid") is None and rtype.startswith(hooks.TYPE_PREFIX):
            sid = rec.get("session_id")
            if sid:
                orphans[sid] = orphans.get(sid, 0) + 1
    return observed({"orphans": orphans, "bound": _ring_bound_note(records)})


def _hooks_unattributed_report(_declared: None, obs: Observation) -> list[Finding]:
    orphans: dict[str, int] = obs.value["orphans"]
    bound = obs.value["bound"]
    if not orphans:
        return [Finding(OK, f"no hook-blind sessions in the {bound} — every hook.* "
                            "record in that window either resolved a window or carries "
                            "no session to attribute")]
    total = sum(orphans.values())
    named = ", ".join(sorted(orphans))
    return [Finding(
        WARN,
        f"{total} hook.* event(s) unattributable — {len(orphans)} hook-blind session(s) "
        f"in the {bound}: {named}",
        "chela.hooks.wid_for_session could not resolve a live window for these sessions. "
        "Measured (CMX-227): most of the time this session never ran in a chela-tracked "
        "window at all — a headless/non-interactive claude process (e.g. a cron job) that "
        "chela did not launch, same as an unset $CHELA_WID being a non-fault. Check "
        "`chela.sessionids.entries()` for the session_id first: absent there across the "
        "session's whole lifetime means it was never chela's window to attribute. Only if "
        "it DOES appear there is this the rarer real gap — two agents launched in one cwd "
        "(CMX-190) or the window closing right as the POST landed (most often a dispatched "
        "agent's own `chela task-finished` killing its window on its way out). The events "
        "were NOT dropped either way: session_id is kept on the record, but "
        "`chela events --wid @N` can never reach them because there is no wid to filter "
        "on. Correlate by session_id against `~/.claude/projects/*/<session_id>.jsonl`, or "
        "`chela events --type <type>` and grep the JSON for these ids. This scan is "
        "bounded to the ring above — an older or quieter hook-blind session may already "
        "have scrolled out; it is not ruled out by this being clean.",
    )]


# --- fact: an X-Chela-Wid header that named a DEAD window ----------------------------
#
# `_explicit_wid` (hooks.py) correctly refuses a header naming a window that is not live
# right now — falling through to the same origin-based inference any other hook uses — but
# it says NOTHING when it does. An unset header is the ordinary case (a session chela did
# not launch has no `$CHELA_WID`) and must never warn. `chela.hooks.ingest` keeps the
# rejected value on the record (`rejected_wid`), distinct from the unset case
# (`rejected_wid=None` there too) — this fact reads it back.
#
# CMX-192/CMX-231/CMX-236: this fact used to call every rejected header "always a fault" —
# a manual relaunch inheriting a stale `$CHELA_WID` from tmux's global environment after
# its window closed. But measured against production logs, the dominant (often only)
# shape is a `hook.session_start` naming a window that WAS real and WAS live — just not
# any more, because tmux replaced it with a different wid shortly before the header
# arrived. That is an ordinary teardown artifact, not the CMX-192 fault, and the two read
# identically off a single "not live right now" check.
#
# Telling them apart needs a scope a bare wid match does not have: tmux window ids are
# small integers, unique only WITHIN one tmux server's life — issued per SERVER and never
# recycled while that server runs (`chela.epoch`), but freely reused by the NEXT server. So
# `@2` resolving to *somebody's* window elsewhere in a multi-week ring is not evidence the
# window a rejected header named was ever live — only that the same short string was, quite
# possibly under a different tmux server entirely. Session-scoping was tried and rejected
# (CMX-231 rework #2): `rejected_wid` only ever fires on `hook.session_start`, which is BY
# CONSTRUCTION a session's first record, so "did this session resolve it before" can never
# be true — the OK branch was dead code. The scope that actually matches "this was
# genuinely alive, in the same run of the world" is the tmux EPOCH itself: every record
# `chela.hooks.ingest` writes is stamped with the epoch it saw at write time (CMX-236), and
# a `rejected_wid` that some OTHER record — any session — resolved under that SAME epoch
# was a real window in the here-and-now, whatever became of it since. A `rejected_wid` with
# no such match — because it belongs to a dead epoch, or never resolved at all — has no
# evidence it was ever live under the epoch that is rejecting it now: the CMX-192 shape.
#
# `hooks._explicit_wid`/`_explicit_wid_dead` separately gained a forced-refresh retry
# before giving up on a header (CMX-231) — a real but DIFFERENT race (a session replacing
# its own claude process inside a still-open window faster than the ≤1s pane cache
# refreshes). It just keeps a window that appears a moment later from ever reaching
# `rejected_wid` in the first place; it does not, by itself, distinguish teardown from
# CMX-192 for the records that DO reach here.

def _hooks_rejected_wid_read() -> Observation:
    """Every ``hook.*`` record in the ring whose ``X-Chela-Wid`` named a window that was
    not live — ``rejected_wid``, read back exactly as :func:`chela.hooks.ingest` wrote
    it, together with the tmux epoch THAT record was stamped with. Distinct from the
    unset case, which never sets ``rejected_wid`` at all.

    Also collects, PER EPOCH, every ``wid`` that ANY record resolved under that epoch —
    the scope :func:`_hooks_rejected_wid_report` needs to tell a window that was
    genuinely live under the SAME tmux server as the rejection (a teardown artifact)
    apart from a same-numbered window that only ever belonged to a DIFFERENT server (the
    CMX-192 shape). Records with no epoch (written before CMX-236, or a host where tmux
    could not be asked) resolve nothing into this map and are never used to downgrade a
    rejection — an unreadable epoch is not license to guess.
    """
    records = event_log.ring()
    dead: dict[str, tuple[str, str | None]] = {}
    resolved_by_epoch: dict[str, set[str]] = {}
    for rec in records:
        wid = rec.get("wid")
        rec_epoch = rec.get("epoch")
        if wid and rec_epoch:
            resolved_by_epoch.setdefault(rec_epoch, set()).add(wid)
    for rec in records:
        rtype = rec.get("type") or ""
        rejected = rec.get("rejected_wid")
        if rejected and rtype.startswith(hooks.TYPE_PREFIX):
            sid = rec.get("session_id") or "?"
            dead[sid] = (rejected, rec.get("epoch"))
    return observed({"dead": dead, "resolved_by_epoch": resolved_by_epoch,
                     "bound": _ring_bound_note(records)})


def _hooks_rejected_wid_report(_declared: None, obs: Observation) -> list[Finding]:
    dead: dict[str, tuple[str, str | None]] = obs.value["dead"]
    resolved_by_epoch: dict[str, set[str]] = obs.value["resolved_by_epoch"]
    bound = obs.value["bound"]
    if not dead:
        return [Finding(OK, f"no rejected X-Chela-Wid headers in the {bound} — every "
                            "header seen either named a live window or was unset")]
    teardown = {sid: wid for sid, (wid, rec_epoch) in dead.items()
                if rec_epoch and wid in resolved_by_epoch.get(rec_epoch, ())}
    orphaned = {sid: wid for sid, (wid, rec_epoch) in dead.items()
                if sid not in teardown}
    findings: list[Finding] = []
    if teardown:
        named = ", ".join(f"{sid}→{wid}" for sid, wid in sorted(teardown.items()))
        findings.append(Finding(
            OK,
            f"{len(teardown)} rejected X-Chela-Wid header(s) in the {bound} named a "
            f"window that WAS live under the SAME tmux epoch: {named}",
            "chela.hooks._explicit_wid_dead rejected these because the named window was "
            "not live at the moment the header arrived, but some OTHER record in the "
            "ring resolved that same wid under the SAME tmux server — a teardown "
            "artifact, not the CMX-192 fault: the window was real and was live under "
            "this epoch, it just closed or was replaced before this particular request "
            "landed. Deliberately NOT a ring-wide wid match: tmux window ids are small "
            "integers reused by every new tmux server, so scoping to the epoch is what "
            "keeps a DIFFERENT server's same-numbered window from masquerading as this "
            "one's teardown. No action needed.",
        ))
    if orphaned:
        named = ", ".join(f"{sid}→{wid}" for sid, wid in sorted(orphaned.items()))
        findings.append(Finding(
            WARN,
            f"{len(orphaned)} session(s) sent an X-Chela-Wid naming a window in the "
            f"{bound} with no evidence it was ever live under that same tmux epoch: "
            f"{named}",
            "A well-formed $CHELA_WID that names a window which is not live right now, "
            "and never resolved anything else under the SAME tmux epoch anywhere in "
            "this ring either, usually means the agent was relaunched by hand and "
            "inherited a stale window id from tmux's global environment surviving a "
            "server restart (the CMX-192 root cause) — window ids are reused by every "
            "new tmux server, so a wid from a dead epoch can easily name a window that "
            "is very much alive today, under someone else's identity. "
            "chela.hooks.wid_for_session still fell back to origin-based inference for "
            "these, so the event was not necessarily lost — but the stale id is worth "
            "chasing down: is the window in `chela status` right now, and does the "
            "session's OWN transcript show a `--resume` shortly before this? A record "
            "with no readable epoch at all (pre-CMX-236, or a host where tmux could not "
            "be asked) always lands here too — an unreadable epoch is never grounds to "
            "call this a harmless teardown. This scan is also bounded to the ring above "
            "— a window that WAS live earlier under this same epoch but scrolled out of "
            "the ring before this check ran would look like this shape too; widen the "
            "window before concluding it never existed.",
        ))
    return findings


# --- fact: does every LIVE window have a reachable PEER-MESSAGING SOCKET? -------------
#
# CMX-222/223 made the peer UDS socket the transport for `chela msg`/`broadcast`, room
# `handoff`/`question`/`blocker` dispatch, and the decisions inbox's verdict delivery —
# and every one of those tries the socket FIRST, then falls back to `send_tmux` SILENTLY
# the moment it cannot be reached (chela.messenger's own module docstring names this: the
# fallback is correct — a mixed fleet must not lose messages — and it is exactly what
# makes it dangerous. The fleet can silently degrade to the pre-CMX-222 paste transport,
# with CMX-79's bash-mode-injection risk back in play, and nothing anywhere reports it.
# `chela doctor` emitted ZERO facts about peer messaging before this one. This closes
# that gap the same way `plugin.hooks_flowing` closed "the manifest matches but the hook
# never arrives": ask the OWNER — the socket file on disk — not chela's own belief that
# `--messaging-socket-path` was used at launch.

def _peer_transport_declared() -> dict[str, sessions.Pane]:
    """Every live claude-agent window — the population any peer-eligible send (`chela
    msg`/`broadcast`, rooms' dispatch, the decisions inbox's verdict delivery) could
    target."""
    return {wid: pane for wid, pane in sessions.panes().items() if pane.claude_pid}


def _peer_transport_read() -> Observation:
    declared = _peer_transport_declared()
    if not declared:
        return observed({})
    return observed({
        wid: messenger.peer_transport_kind(wid, pane.claude_pid)
        for wid, pane in declared.items()
    })


def _peer_transport_report(declared: dict[str, sessions.Pane],
                           obs: Observation) -> list[Finding]:
    if not declared:
        return []
    kinds: dict[str, str] = obs.value
    unreachable = sorted(wid for wid, kind in kinds.items() if kind == "tmux fallback")
    default = sorted(wid for wid, kind in kinds.items() if kind == "default")
    out: list[Finding] = []
    if unreachable:
        out.append(Finding(
            WARN,
            f"{len(unreachable)} live window(s) have NO reachable peer-messaging "
            f"socket: {', '.join(unreachable)}",
            "chela msg/broadcast, room handoff/question/blocker dispatch, and the "
            "decisions inbox's verdict delivery all try the peer socket FIRST and fall "
            "back to send_tmux SILENTLY the instant it cannot be reached — an older "
            "Claude Code build, a window launched before --messaging-socket-path "
            "existed, a socket that has not bound yet, or a socket FILE that outlived "
            "the process behind it (a SIGKILLed agent never runs its own unlink). The "
            "fallback is correct (a mixed fleet must not lose messages), and it is "
            "exactly what makes this dangerous: every message to these windows quietly "
            "degrades to typing into the pane, re-opening CMX-79's bash-mode-injection "
            "risk, with nothing but this check saying so. Relaunch the window so it "
            "picks up --messaging-socket-path (dispatcher.py / personas/autolaunch.py "
            "already wire it in), or confirm its Claude Code build supports the peer "
            "socket.",
        ))
    if default:
        out.append(Finding(
            WARN,
            f"{len(default)} live window(s) reach their peer-messaging socket only "
            f"through the legacy pid-derived guess, not a chela-owned path: "
            f"{', '.join(default)}",
            "These windows were launched before --messaging-socket-path existed, or "
            "its path overflowed the AF_UNIX sun_path ceiling (messaging_socket_"
            "launch_arg logs when that happens) — messenger._peer_socket_path is "
            "reading OUR OWN XDG_RUNTIME_DIR/TMPDIR/getuid() as a stand-in for the "
            "target's, which only holds today because the live daemon happens to "
            "export the same values every session inherits. They work right now, but "
            "they are one environment drift away from silently failing the same way "
            "an unreachable window does — relaunch them so they pick up a chela-owned, "
            "window-keyed path (dispatcher.py / personas/autolaunch.py already wire "
            "--messaging-socket-path in) instead of depending on that coincidence.",
        ))
    if out:
        return out
    return [Finding(
        OK, f"{len(declared)} live window(s): every one reaches its peer-messaging "
            "socket through the chela-owned deterministic path — chela "
            "msg/broadcast/rooms/inbox use it, not the tmux fallback")]


# --- fact: what the RUNNING daemon came up with --------------------------------------

def _daemon_read() -> Observation:
    live = capabilities.live()
    if live is None:
        return absent(f"no daemon has published {capabilities.state_file()}")
    return observed(live)


def _daemon_report(declared: list[dict], obs: Observation) -> list[Finding]:
    """The capability check: is the daemon actually DOING the job, not merely configured
    to. Observed from what the running daemon published; inferred (and said to be
    inferred) when none is running."""
    out: list[Finding] = []
    if obs.missing:
        caps = declared
        out.append(Finding(
            WARN, obs.missing,
            "`chela run` is not running (or predates this check). The capabilities below "
            "are INFERRED from this shell's config — they are what a daemon started now "
            "would do, not what anything is doing.",
        ))
    else:
        live = obs.value
        caps = [c for c in live["capabilities"] if isinstance(c, dict)]
        out.append(Finding(
            OK, f"daemon running (pid {live.get('pid')}, session "
                f"{live.get('session') or '?'}) — capabilities read from it, not from config"))

    for cap in caps:
        label = cap.get("label") or cap.get("key") or "?"
        if cap.get("on"):
            out.append(Finding(OK, f"{label}: ON", str(cap.get("detail") or "")))
        elif cap.get("warn_when_off"):
            detail = str(cap.get("detail") or "")
            fix = str(cap.get("fix") or "")
            out.append(Finding(
                WARN, f"{label}: OFF", f"{detail} — fix: {fix}" if fix else detail))
        # A capability that is merely *unset* (notifications, the inbox) is reported by
        # the daemon's startup log; repeating every off-by-choice toggle here would bury
        # the one that matters. `warn_when_off` is what marks a foot-gun.

    dispatch = next((c for c in caps if c.get("key") == "dispatch"), None)
    if dispatch is None or obs.missing:
        return out
    # A daemon started before the env changed carries the OLD capability. That
    # config-vs-running disagreement is exactly the CMX-42 trap, and it is invisible
    # unless something says it: the running process, not the file, is what dispatches —
    # and only a restart closes the gap.
    if bool(dispatch.get("on")) != bool(config.DISPATCH_WORKFLOWS):
        out.append(Finding(
            ERROR,
            "the RUNNING daemon's dispatcher is "
            f"{'ON' if dispatch.get('on') else 'OFF'}, but this shell's config says "
            f"{'ON' if config.DISPATCH_WORKFLOWS else 'OFF'}",
            "The daemon is running on a stale environment — CHELA_DISPATCH_WORKFLOWS "
            "changed after it started. Restart it (e.g. `pm2 restart chela-daemon`); "
            "until then the config describes a daemon that does not exist. (If this "
            "shell simply has a different env than the service, that is the same drift "
            "the env file exists to end — export nothing, source the file.)",
        ))
    return out


# --- fact: each dispatched WORKFLOW.md, and the tracker it claims to read -------------

def dispatched_workflows() -> list[Path]:
    """The workflows the RUNNING daemon dispatches — its published list, not this shell's
    config, because those are exactly the two copies that disagreed for nine hours."""
    cap = capabilities.live_capability("dispatch")
    if cap is None:
        cap = next((c.as_dict() for c in capabilities.effective()
                    if c.key == "dispatch"), {})
    return [Path(p) for p in (cap.get("workflows") or [])]


def _workflows_read() -> Observation:
    """Ask the filesystem and the tracker, not our own config: does each workflow EXIST,
    PARSE, and have a tracker to read? All three are file reads — no subprocess: doctor is
    run interactively and must stay instant."""
    states: list[dict] = []
    for path in dispatched_workflows():
        if not path.exists():
            states.append({"path": path, "state": "missing"})
            continue
        try:
            wf = load_workflow(path)
        except Exception as exc:
            states.append({"path": path, "state": "unparseable", "detail": str(exc)})
            continue
        try:
            source = get_source(wf)
        except Exception as exc:
            states.append({"path": path, "state": "no_source", "detail": str(exc)})
            continue
        tracker = getattr(source, "path", None)     # a gh_issues tracker is not a file
        if tracker is not None and not Path(tracker).exists():
            states.append({"path": path, "state": "no_tracker", "tracker": tracker})
            continue
        # A source that parsed but REFUSES to yield work (gh_issues with no
        # `require_label`). Without this the queue simply reads empty, which is
        # indistinguishable from "nothing to do" — the exact silent-stall shape
        # `repo.services_current` exists to prevent elsewhere.
        config_error = getattr(source, "config_error", None)
        if config_error:
            states.append({"path": path, "state": "refusing", "detail": config_error})
            continue
        states.append({"path": path, "state": "ok", "tracker": tracker,
                       "project": wf.project_key})
    return observed(states)


def _workflows_report(declared: list[Path], obs: Observation) -> list[Finding]:
    out: list[Finding] = []
    for found in obs.value:
        path, state = found["path"], found["state"]
        if state == "missing":
            out.append(Finding(
                ERROR, f"dispatch workflow {path} does not exist",
                "The daemon is configured to dispatch a file that is not there: it will "
                "claim no work. Fix CHELA_DISPATCH_WORKFLOWS or restore the file.",
            ))
        elif state == "unparseable":
            out.append(Finding(
                ERROR, f"dispatch workflow {path.name} does not parse",
                f"{found['detail']} — the daemon keeps reconciling on its last known-good "
                "config but starts NO new work until this parses.",
            ))
        elif state == "no_source":
            out.append(Finding(ERROR, f"{path.name}: unusable tracker", found["detail"]))
        elif state == "refusing":
            out.append(Finding(
                ERROR, f"{path.name}: tracker is refusing to claim work", found["detail"],
            ))
        elif state == "no_tracker":
            out.append(Finding(
                ERROR, f"{path.name}: tracker {found['tracker']} does not exist",
                "The dispatcher reads its work items from this file. With no file there "
                "is no queue — and nothing says so.",
            ))
        else:
            tracker = found["tracker"]
            out.append(Finding(
                OK, f"{path.name} parses (project {found['project']})",
                f"tracker: {tracker}" if tracker else "tracker: gh_issues",
            ))
    return out


# --- fact: the AGENT COMMAND each dispatched workflow resolves to, and whether the
# shell that runs it can actually find the binary -------------------------------------
#
# `resolve_agent_cmd()` picks a command line — the workflow's own `agent.cmd`, else the
# Settings permission mode, else the built-in default — and chela hands it to tmux
# `send-keys` VERBATIM. Nobody has ever asked whether it will actually run: a typo'd
# `agent.cmd`, or a fleet whose `claude` lives somewhere the spawning shell's PATH does
# not reach (the exact bug class `CHELA_TMUX_SESSION`'s PM2 trap is — a service PATH that
# quietly differs from an interactive one), types a command into a fresh pane that
# answers ``command not found`` and never becomes ready. `_wait_for_ready` then times out
# and sends the prompt ANYWAY, so the run sits `claimed` — holding a concurrency slot —
# with a window that is not running an agent, and nothing says why.

def _agent_commands() -> dict[str, dict]:
    """``{workflow path: {repo, cmd, source}}`` — what resolve_agent_cmd() will actually
    hand to tmux for each dispatched workflow that parses (an unparseable one is already
    reported by ``dispatch.workflows``)."""
    from chela import dispatcher                    # lazy: doctor must import cheaply

    out: dict[str, dict] = {}
    for path in dispatched_workflows():
        if not path.exists():
            continue
        try:
            wf = load_workflow(path)
        except Exception:
            continue
        cmd, source = dispatcher.resolve_agent_cmd(wf)
        out[str(path)] = {"repo": str(path.parent), "cmd": cmd, "source": source}
    return out


def _agent_cmd_which(binary: str) -> str | None:
    """A seam: the real answer is the shell PATH; the suite hands this a fixed one."""
    return shutil.which(binary)


def _agent_cmd_read() -> Observation:
    declared = _agent_commands()
    if not declared:
        return observed({})                        # nothing dispatches — nothing to ask
    return observed({
        path: _agent_cmd_which(shlex.split(claim["cmd"])[0]) is not None
        for path, claim in declared.items()
    })


def _agent_cmd_report(declared: dict[str, dict], obs: Observation) -> list[Finding]:
    if not declared:
        return []
    found: dict[str, bool] = obs.value or {}
    missing = [
        (path, claim) for path, claim in sorted(declared.items())
        if not found.get(path, True)
    ]
    out = [
        Finding(
            ERROR,
            f"{Path(path).name}: agent command "
            f"{shlex.split(claim['cmd'])[0]!r} is not on PATH",
            f"Resolved (source: {claim['source']}) to: {claim['cmd']!r}. tmux types this "
            "into a fresh window and gets `command not found` back — the window never "
            "becomes ready, `_wait_for_ready` times out and sends the prompt anyway, and "
            "the run sits `claimed` forever holding a concurrency slot with no agent "
            "behind it. Install the binary, fix `agent.cmd` in the WORKFLOW.md, or check "
            "the Settings permission mode.",
        )
        for path, claim in missing
    ]
    if not out:
        out.append(Finding(
            OK, f"{len(declared)} dispatched workflow(s): the agent command resolves to "
                "a binary that is really on PATH"))
    return out


# --- fact: whether `gh` can actually authenticate the PR every dispatched agent opens --
#
# Every dispatched WORKFLOW.md's prompt ends with `gh pr create` (see
# examples/WORKFLOW.md, step 4) — chela's OWN dispatcher reads a PR's checks back with
# `gh` too (``pr.checks``, above). An adopter who followed `skills/chela-setup`'s
# prerequisite list (`gh --version`) confirmed the binary is THERE; nobody ever asked
# whether it is actually LOGGED IN. Unauthenticated, every agent finishes its whole task
# and fails on the very last line of an otherwise-successful run — reported nowhere but
# that one agent's own transcript.

def _gh_auth_status() -> bool | None:
    """A seam: the real answer is `gh auth status`'s exit code; the suite hands this a
    fixed one instead of shelling out. ``None`` means `gh` itself could not be asked."""
    if shutil.which("gh") is None:
        return None
    out = subprocess.run(
        ["gh", "auth", "status"], capture_output=True, text=True, timeout=15)
    return out.returncode == 0


def _gh_auth_read() -> Observation:
    if not dispatched_workflows():
        return observed(None)                      # nothing dispatches — no PR to open
    authed = _gh_auth_status()
    if authed is None:
        return cannot_verify(
            "`gh` is not on PATH, so chela cannot ask whether it is authenticated — and "
            "every dispatched workflow's agent ends its run with `gh pr create`, which "
            "would fail the exact same way.")
    return observed(authed)


def _gh_auth_report(_declared: None, obs: Observation) -> list[Finding]:
    if obs.value is None:
        return []
    if not obs.value:
        return [Finding(
            ERROR,
            "gh is NOT authenticated — every dispatched agent's `gh pr create` will fail",
            "`gh auth status` says no account is logged in. An agent runs its whole task "
            "to completion and only discovers this on its very last step, then reports "
            "the blocker in its own transcript — nothing else surfaces it. Run `gh auth "
            "login` as the user chela's fleet runs as.",
        )]
    return [Finding(OK, "gh is authenticated — dispatched agents can open PRs")]


# --- fact: the BASE BRANCH each dispatched workflow forks from and targets ------------
#
# `_spawn()` runs `git worktree add -b <branch> <path> <base_branch>` with `check=True` —
# a `workspace.base_branch` that does not exist in the repo (the adopter's default branch
# is `master`, the example says `main`; a typo; a branch never pushed) fails EVERY
# dispatch of that workflow the same way: the task is claimed, marked `failed` with a raw
# git stderr line as the only explanation, and retried until MAX_ATTEMPTS — burning every
# attempt on a config error nothing ever named.

def _base_branches() -> dict[str, dict]:
    """``{workflow path: {repo, base_branch}}`` for every dispatched workflow that
    parses — the ref every worktree forks from and every PR targets."""
    out: dict[str, dict] = {}
    for path in dispatched_workflows():
        if not path.exists():
            continue
        try:
            wf = load_workflow(path)
        except Exception:
            continue
        out[str(path)] = {
            "repo": str(path.parent),
            "base_branch": str(wf.get("workspace", "base_branch", default="master")),
        }
    return out


def _ref_exists(repo: str, branch: str) -> bool:
    """Ask git directly: does `branch` exist as a local OR `origin`-tracking ref?"""
    for ref in (f"refs/heads/{branch}", f"refs/remotes/origin/{branch}"):
        out = subprocess.run(
            ["git", "-C", repo, "rev-parse", "--verify", "--quiet", ref],
            capture_output=True, text=True, timeout=15,
        )
        if out.returncode == 0:
            return True
    return False


def _base_branch_read() -> Observation:
    declared = _base_branches()
    if not declared:
        return observed({})                        # nothing dispatches — nothing to ask
    if shutil.which("git") is None:
        return cannot_verify(
            "git is not on PATH — chela cannot ask whether the branch every worktree "
            "forks from and every PR targets actually exists.")
    return observed({
        path: _ref_exists(claim["repo"], claim["base_branch"])
        for path, claim in declared.items()
    })


def _base_branch_report(declared: dict[str, dict], obs: Observation) -> list[Finding]:
    if not declared:
        return []
    found: dict[str, bool] = obs.value or {}
    missing = [
        (path, claim) for path, claim in sorted(declared.items())
        if not found.get(path, True)
    ]
    out = [
        Finding(
            ERROR,
            f"{Path(path).name}: base_branch {claim['base_branch']!r} does not exist in "
            f"{claim['repo']}",
            "Every worktree is created with `git worktree add -b <branch> ... "
            f"{claim['base_branch']}` — with no such ref this fails at the FIRST "
            "dispatch: the task is claimed, marked `failed` with a raw git stderr line "
            f"as the only explanation, and retried until MAX_ATTEMPTS. Fix "
            f"workspace.base_branch in {path}, or create the branch.",
        )
        for path, claim in missing
    ]
    if not out:
        out.append(Finding(
            OK, f"{len(declared)} dispatched workflow(s): base_branch exists in git for "
                "each"))
    return out


# --- fact: can every unattended BASE-BRANCH WRITER actually reach a remote? -----------
#
# CMX-174 moved the tracker strike and the trial ledger (see `dispatcher._strike_merged_
# tasks` / `dispatcher._write_trial_ledger`) onto an isolated, chela-owned worktree,
# detached from whatever the human's interactive checkout happens to be doing — a
# dogfood branch, an uncommitted edit, a rebase no longer disables them, because they
# never read that checkout's HEAD or working tree at all. What's left, deliberately, is
# the one precondition that genuinely is NOT chela's to fix: `_base_write_worktree`
# needs a git remote to fetch and push through. With none, it logs a WARNING and skips —
# every tick, forever — and nothing else ever says so: merged tasks keep rendering as
# open cards, and a trial ledger opted into for an honesty bar (lean-alpha's deflated-
# Sharpe N) silently stops growing. This fact is the one thing worth surfacing about that
# writer now that its old failure mode (the human's checkout) cannot happen any more.

def _base_write_targets() -> dict[str, dict]:
    """``{workflow path: {repo, base_branch}}`` for every dispatched workflow that parses
    AND needs an unattended base-branch write: a markdown tracker (the strike always
    applies to one) or a `trial_ledger:` opt-in (any tracker kind)."""
    out: dict[str, dict] = {}
    for path in dispatched_workflows():
        if not path.exists():
            continue
        try:
            wf = load_workflow(path)
        except Exception:
            continue
        try:
            source = get_source(wf)
        except Exception:
            continue
        needs_write = getattr(source, "path", None) is not None or bool(wf.get("trial_ledger"))
        if not needs_write:
            continue
        out[str(path)] = {
            "repo": str(path.parent),
            "base_branch": str(wf.get("workspace", "base_branch", default="master")),
        }
    return out


def _has_remote(repo: str) -> bool:
    """Ask git directly: does this repo have ANY remote configured?"""
    out = subprocess.run(
        ["git", "-C", repo, "remote"], capture_output=True, text=True, timeout=15,
    )
    return out.returncode == 0 and bool(out.stdout.strip())


def _base_write_read() -> Observation:
    declared = _base_write_targets()
    if not declared:
        return observed({})                        # nothing needs an unattended write
    if shutil.which("git") is None:
        return cannot_verify(
            "git is not on PATH — chela cannot ask whether these repos have a remote to "
            "write the tracker strike / trial ledger through.")
    return observed({
        path: _has_remote(claim["repo"]) for path, claim in declared.items()
    })


def _base_write_report(declared: dict[str, dict], obs: Observation) -> list[Finding]:
    if not declared:
        return []
    found: dict[str, bool] = obs.value or {}
    missing = [
        (path, claim) for path, claim in sorted(declared.items())
        if not found.get(path, True)
    ]
    out = [
        Finding(
            ERROR,
            f"{Path(path).name}: {claim['repo']} has no git remote — the tracker strike "
            "/ trial ledger cannot write base_branch",
            "`_base_write_worktree` fetches `origin` before every unattended write to "
            f"{claim['base_branch']!r}; with no remote configured it logs a WARNING and "
            "skips, EVERY tick, forever. Merged tasks keep rendering as open cards, and "
            f"a trial ledger opted into (if any) silently stops growing. Add an `origin` "
            f"remote to {claim['repo']}.",
        )
        for path, claim in missing
    ]
    if not out:
        out.append(Finding(
            OK, f"{len(declared)} dispatched workflow(s) needing base-branch writes: each "
                "repo has a remote to write the tracker strike / trial ledger through"))
    return out


# --- fact: the dispatch hold — a paused queue is a DISABLED SUBSYSTEM -----------------

def _hold_read() -> Observation:
    return observed(hold.read())


def _hold_report(_declared: None, obs: Observation) -> list[Finding]:
    """A HELD queue claims nothing — say so, because "no runs started today" looks exactly
    like a quiet day. Read from the hold FILE, not from the daemon's published
    capabilities: a hold taken after the daemon booted is not in that snapshot, and the
    file is the shared truth precisely because the two live in different processes.

    No hold is the normal state and gets no line — but an EXPIRED hold does, because it
    means somebody paused the queue and never came back, and whatever they were going to
    reorder, they didn't.
    """
    held = obs.value
    if held is None:
        return []
    if held.expired():
        return [Finding(
            WARN, f"the dispatch hold EXPIRED — {held.summary()}",
            "Dispatch has RESUMED (an expired hold self-releases on the next tick, and "
            "says so). Whoever took it did not come back: the queue may never have been "
            "rewritten, so the top item may not be the one that was intended. "
            f"File: {hold.path()}",
        )]
    return [Finding(
        WARN, f"the queue is HELD — dispatch is claiming NOTHING ({held.summary()})",
        "This is deliberate: someone is rewriting the queue and does not want a task "
        "claimed out from under the reorder. Reconciliation still runs (merged PRs close "
        "out and free their slot). Release with `chela dispatch --resume`; it also "
        "self-releases at its expiry, loudly.",
    )]


# --- fact: the test suites the pytest COLLECTOR really executes ----------------------
#
# CMX-65: `uv run pytest -q` said 980 passed while three `.test.mjs` suites were executed
# by NOTHING and one of them was RED on `dev`. The suite that RUNS is not the suite that
# EXISTS, and only the collector knows which is which — so this asks it. It is the one
# fact whose read-back shells out, and the only one that does not apply to an installed
# chela (a wheel has no test suite). That is the machine — dev, CI — where an unrun suite
# is the bug, so it is checked exactly there.

_SKIP_DIRS = {"node_modules", "vendor", ".git"}


def repo_root() -> Path | None:
    """The source checkout chela is running FROM, or ``None`` for an installed copy."""
    root = Path(__file__).resolve().parent.parent
    if (root / "pyproject.toml").exists() and (root / "tests").is_dir():
        return root
    return None


def _js_suites_on_disk() -> list[str]:
    root = repo_root()
    if root is None:
        return []
    found: list[str] = []
    # os.walk + pruning `dirnames` in place, not `root.rglob(...)` filtered after the
    # fact: rglob still DESCENDS into .git to find nothing, and under CI's parallel
    # pytest workers a concurrent git ref update can make a `.git/refs/...` entry
    # vanish mid-walk — FileNotFoundError, from a directory this fact never needed to
    # enter in the first place.
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS]
        found.extend(
            str((Path(dirpath) / name).relative_to(root))
            for name in filenames if name.endswith(".test.mjs")
        )
    return sorted(found)


def collected_js_suites(root: Path) -> Observation:
    """Ask the OWNER: which ``.test.mjs`` files does the pytest collector actually reach?

    Read back from ``--collect-only``, never from our own glob — the point of the fact is
    that the two can disagree, and they did.
    """
    proc = subprocess.run(
        # sys.executable, never a bare `python`: an installed chela runs from a venv whose
        # interpreter may not be on PATH at all, and a check that cannot run is a check
        # that reports CANNOT VERIFY for a reason that has nothing to do with the fact.
        [sys.executable, "-m", "pytest", "--collect-only", "-q", "-p", "no:cacheprovider"],
        capture_output=True, text=True, timeout=120, cwd=str(root),
    )
    if proc.returncode not in (0, 5):               # 5 = collected nothing
        return cannot_verify(
            "`pytest --collect-only` exited "
            f"{proc.returncode}: {(proc.stderr or proc.stdout)[-400:].strip()}")
    found = {
        suite
        for line in proc.stdout.splitlines()
        for suite in _js_suites_on_disk()
        if suite in line
    }
    return observed(found)


def _collector_read() -> Observation:
    root = repo_root()
    if root is None:                                # pragma: no cover - not a checkout
        return absent("not a source checkout — an installed chela has no test suite")
    return collected_js_suites(root)


def _collector_report(declared: list[str], obs: Observation) -> list[Finding]:
    if obs.missing:
        return []
    unrun = [s for s in declared if s not in obs.value]
    if unrun:
        return [Finding(
            ERROR, f"{len(unrun)} JS suite(s) exist that pytest does NOT execute",
            _lines(unrun)
            + "\n    A test that exists but is never executed is not a test; it is a "
            "comment that costs CI nothing to ignore — `tests/views.test.mjs` was RED on "
            "`dev` for a day while `pytest -q` reported 980 passed (CMX-65). "
            "`tests/test_js_suites.py` runs every `*.test.mjs` it FINDS; if one is not "
            "collected, something is excluding it.",
        )]
    return [Finding(OK, f"pytest executes all {len(declared)} JS suite(s)",
                    "asked the collector (`pytest --collect-only`), not our own glob")]


def _collector_applies() -> bool:
    # Not under pytest: doctor would collect the suite from inside the suite. The fact is
    # audited directly (with the collector stubbed) in tests/test_runtime_truth.py.
    return repo_root() is not None and "PYTEST_CURRENT_TEST" not in os.environ


# --- fact: every bridged window resolves to a transcript ------------------------------
#
# The outbound relay of a window with no transcript is not slow, not erroring and not
# retrying: it is DOING NOTHING, in complete silence, while every other surface stays
# green (bindings reconcile, topics exist, INBOUND still works — it only needs the wid).
# That is exactly how the relay stayed dead for an hour on 2026-07-14 with a human sitting
# in front of a live topic. Nothing ever asserted it, so nothing ever noticed. This does.

def _bound_windows() -> dict[str, str]:
    """``{wid: topic name}`` — the windows the Telegram bridge has promised to relay."""
    from chela.telegram import bindings                # lazy: doctor must import cheaply

    path = bindings.default_bindings_path()
    if not path.exists():
        return {}
    try:
        registry = bindings.BindingRegistry.load(path)
    except (OSError, ValueError):
        return {}
    return {wid: registry.topic_name(wid) or "" for wid in registry.windows()}


def _relay_read() -> Observation:
    if _tmux_or_unverifiable() is None:
        return cannot_verify("tmux is not on PATH, so chela cannot ask which window is "
                             "running which session — and a window it cannot resolve is a "
                             "topic that silently relays nothing.")
    bound = _bound_windows()
    if not bound:
        return absent("no window is bound to a Telegram topic")
    live = set(discovery.get_windows_by_id())
    return observed({wid: sessions.resolve_window(wid) for wid in bound if wid in live})


def _relay_report(bound: dict[str, str], obs: Observation) -> list[Finding]:
    if obs.missing:
        return []                                      # the bridge is not in use here
    resolved: dict[str, sessions.Resolution] = obs.value
    out: list[Finding] = []
    for wid, res in sorted(resolved.items()):
        topic = bound.get(wid) or wid
        if not res.ok:
            out.append(Finding(
                ERROR, f"{wid} ({topic}) is bound to a topic but resolves to NO transcript",
                f"{res.detail}\n"
                "    Outbound is DEAD for this window — and it fails silently: the binding "
                "reconciles, the topic exists, inbound still works, and nothing is ever "
                "relayed back. Check whether the agent is running, and whether its session "
                "is one chela can name (a hook event, or `claude --resume <id>`).",
            ))
            continue
        age = time.time() - res.path.stat().st_mtime
        out.append(Finding(
            OK, f"{wid} ({topic}) → {res.path.name} (via {res.source}, "
                f"last written {_ago(age)})",
            "" if res.source != "cwd" else
            "Resolved by the CWD FALLBACK: no hook has ever named this window's session "
            "and it was not resumed, so this is the newest transcript in the project dir "
            "of its cwd — a guess, and the one that broke on 2026-07-14. It becomes a fact "
            "as soon as the agent fires one hook.",
        ))
    return out


# --- fact: the ADDRESS the decisions inbox pushes to ----------------------------------
#
# CMX-77, and the most expensive silence yet. An OOM killed the tmux server on 2026-07-14;
# the fleet came back RENUMBERED (the orchestrator went @0 → @6) and `inbox.json` still read
# {"orchestrator": "@0"}. Five `run_review` notifications queued behind an address that no
# longer existed and NONE were delivered — no error, no warning, no log line, and this
# doctor green 14/14, because nothing here had ever been asked to look at the one thing that
# mattered: whether the window chela intends to push into is the window it thinks it is.
# `@N` is an ADDRESS, not an identity (chela.epoch): tmux issues it per SERVER. So chela's
# copy is the recorded address, tmux owns which server is issuing ids now — and the two
# disagreeing is precisely the outage.


def _inbox_address() -> dict:
    """chela's copy: the address the inbox would push to, and what is waiting behind it."""
    from chela import inbox                          # lazy: doctor must import cheaply

    if not inbox.enabled():
        return {}
    store = inbox.load()
    return {
        "wid": inbox.orchestrator_wid(store),
        "epoch": inbox.orchestrator_epoch(store),
        "session": inbox.orchestrator_session(store),
        "name": store.get("orchestrator_name"),
        "queued": len(store.get("queue") or []),
    }


def _inbox_read() -> Observation:
    """Ask tmux: which server is issuing window ids, and which windows exist under it?"""
    if _tmux_or_unverifiable() is None:
        return cannot_verify("tmux is not on PATH, so chela cannot ask which server issued "
                             "the window id its decisions inbox is addressed to — and an "
                             "inbox addressed to a dead id delivers NOTHING, in silence.")
    now = epoch.current()
    if now is None:
        return cannot_verify("no tmux server is running, so there is no epoch to compare the "
                             "inbox's recorded window id against. Every id in the store was "
                             "issued by a server that is gone.")
    return observed({"epoch": now, "windows": discovery.get_windows_by_id()})


def _inbox_report(declared: dict, obs: Observation) -> list[Finding]:
    if not declared:
        return []                                    # the inbox is switched off
    live: dict[str, str] = obs.value["windows"]
    now: str = obs.value["epoch"]
    wid, stamped, queued = declared["wid"], declared["epoch"], declared["queued"]
    # CMX-82: the inbox re-resolves a rotted address from the orchestrator's recorded session
    # identity every tick. If doctor still sees it rotted, that self-heal has NOT succeeded —
    # either no identity was recorded (pre-CMX-82 / an env pin), or the session is not running
    # under any live window. Say which, so the reader knows whether `chela watch` is even needed.
    if declared.get("session"):
        heal = (f" chela is trying to self-heal this from the orchestrator's recorded session "
                f"({declared['session']}); still rotted means that session is not live under any "
                "window right now.")
    else:
        heal = (" No session identity is recorded (registered before CMX-82, or an env pin), so "
                "there is nothing to self-heal from.")

    if not wid:
        if queued:
            return [Finding(
                ERROR, f"the decisions inbox is holding {queued} event(s) and NOBODY is "
                       "registered to receive them",
                "Events are queued (a finished agent, a PR awaiting review) and no session "
                "has registered as the orchestrator, so nothing is being delivered and "
                "nothing ever will be. Run `chela watch` in the orchestrator's session — it "
                "drains on the next idle tick.",
            )]
        return [Finding(OK, "decisions inbox: no orchestrator registered (inert by design)")]

    if epoch.is_dangling(stamped, now):
        return [Finding(
            ERROR,
            f"the decisions inbox is addressed to {wid} — an id issued by a tmux server that "
            "is GONE",
            f"Recorded under {epoch.describe(stamped)}; tmux is now running "
            f"{epoch.describe(now)}. The server RESTARTED and renumbered every window, so "
            f"{wid} does not name the orchestrator ({declared['name'] or '?'}) any more — it "
            f"names another agent, or nothing. {queued} event(s) are queued behind it and "
            "chela will not push them into a stranger's session (a wrong wid is worse than "
            "no wid). This is the 2026-07-14 outage exactly: five finished PRs went "
            "unreviewed because the queue was addressed to a window that no longer existed "
            "and nothing said so." + heal + " Fix: run `chela watch` in the orchestrator's "
            "session — the queue is intact and goes out on its next idle tick.",
        )]

    if wid not in live:
        return [Finding(
            ERROR if queued else WARN,
            f"the decisions inbox is addressed to {wid}, and tmux has no such window",
            f"The session that registered as the orchestrator is gone. {queued} event(s) are "
            "queued behind that address and nothing is delivering them." + heal + " Register "
            "the session that is doing the orchestrating: `chela watch`.",
        )]

    if not stamped:
        return [Finding(
            WARN,
            f"the decisions inbox is addressed to {wid}, which carries NO tmux epoch",
            "Recorded before CMX-77, or pinned with $CHELA_ORCHESTRATOR_WID. It is still "
            "delivered to — but chela cannot tell whether it still names the session that "
            "registered it, and after a tmux restart that id belongs to somebody else. Run "
            "`chela watch` in the orchestrator's session to stamp it. (An env pin cannot be "
            "stamped at all: baked into a service env, it outlives the tmux server it was "
            "true for.)",
        )]

    return [Finding(
        OK, f"decisions inbox → {wid} ({live[wid]}), issued by the tmux server now running"
            + (f"; {queued} event(s) queued for its next idle tick" if queued else ""))]


# --- fact: on a host with no /proc (macOS), can chela's window-resolution FALLBACK
# actually run? --------------------------------------------------------------------------
#
# `chela.sessions`' `/proc` reads are the FAST PATH, not the only one: a host with no
# `/proc` at all (macOS) falls back to `ps`/`pgrep` (see `sessions._sh`, `_children`/
# `_sh_children`, `_comm`). Losing `pgrep` collapses `_claude_pid` to always-None — and
# with it `started`, `resumed` and `launched_in` — so `resolve_window`'s two STRONGEST
# signals (the event log, bounded by process start time; `--resume <sid>` off the command
# line) never fire, for every window on the host, silently: resolution still "succeeds"
# via the weakest signal alone (tmux's own pane cwd), just wrongly in the one case that
# signal cannot tell apart — two windows sharing a directory — which is the exact failure
# class this registry exists to catch (CMX-48, the 2026-07-14 relay outage). Losing `ps`
# degrades the same fallback pair. Only a fact of a host WITHOUT `/proc`: Linux never
# leaves the fast path, so there is nothing here to check there.

_WINDOW_SHIM_BINARIES = ("pgrep", "ps")


def _window_shim_which(binary: str) -> str | None:
    """A seam: the real answer is the shell PATH; the suite hands this a fixed one."""
    return shutil.which(binary)


def _windows_resolvable_read() -> Observation:
    return observed({b: _window_shim_which(b) is not None for b in _WINDOW_SHIM_BINARIES})


def _windows_resolvable_report(declared: tuple[str, ...], obs: Observation) -> list[Finding]:
    found: dict[str, bool] = obs.value
    missing = [b for b in declared if not found.get(b)]
    if missing:
        return [Finding(
            ERROR,
            f"no /proc on this host, and {', '.join(missing)} not on PATH — window "
            "resolution is running BLIND",
            "chela.sessions falls back to `ps`/`pgrep` when there is no `/proc` (macOS). "
            f"Without {', '.join(missing)}, `claude_pid` — and with it `started`, "
            "`resumed`, `launched_in` — is always None, so every window resolves ONLY via "
            "the weakest signal, tmux's own pane cwd: the same failure class as the "
            "2026-07-14 relay outage, permanently instead of rarely. Install "
            f"{' and '.join(missing)} (both ship with macOS by default; a minimal "
            "container image is the usual way to lose them).",
        )]
    return [Finding(
        OK, f"no /proc on this host — window resolution falls back to "
            f"{', '.join(declared)}, both on PATH")]


def _windows_resolvable_applies() -> bool:
    return not sessions._PROC_HOST


# --- fact: the bundled coverage-fallback font, and whether it REALLY covers the TUI
# marker glyphs both render surfaces fall back to it for ---------------------------------
#
# CMX-156/CMX-159's whole point: JetBrains Mono, Symbols Nerd Font and every font-picker
# option all lack `⏺ ❌ ✅ ✦ ✷ ✨ ⚙` — the web terminal's tool-marker and spinner glyphs — so
# both the dashboard (`chela/dashboard/app.py` `_TERM_FONTS`) and the telegram
# `/screenshot` PNG renderer (`chela/telegram/screenshot.py`) fall back to one bundled
# Symbola subset for exactly those codepoints. `tests/test_term_symbol_fallback.py`
# already proves the REPO's copy has them; this fact proves the copy on THIS INSTALL still
# does — a corrupted download, a packaging miss, or a hand-edit that re-subsets the font
# would otherwise reintroduce tofu (`▢`) on both surfaces in total silence: a font-fallback
# failure renders SOMETHING (a box), never an error, on either surface.

_FONTS_DIR = Path(__file__).resolve().parent / "dashboard" / "static" / "fonts"
_COVERAGE_FONT = "Symbola-Subset.ttf"
# Mirrors tests/test_term_symbol_fallback.py's _REQUIRED_GLYPHS and the README's
# documented subset (U+2300-23FF, U+2600-27BF) — the exact glyphs CMX-159 found falling
# through the whole font stack to tofu.
_TUI_MARKER_GLYPHS = "⏺❌✅✦✷✨⚙"


def _font_coverage_cmap(path: Path):
    """The font's real cmap, or ``None`` if fontTools is not installed (the ``[telegram]``
    extra) — a seam so the suite can hand this a fixture without needing the extra."""
    try:
        from fontTools.ttLib import TTFont
    except ImportError:
        return None
    try:
        font = TTFont(str(path), lazy=True, fontNumber=0)
        try:
            return font.getBestCmap()
        finally:
            font.close()
    except Exception:
        return {}


def _font_coverage_read() -> Observation:
    if not _FONTS_DIR.exists():
        return observed({"exists": False, "cmap": None})
    path = _FONTS_DIR / _COVERAGE_FONT
    if not path.exists():
        return observed({"exists": False, "cmap": None})
    return observed({"exists": True, "cmap": _font_coverage_cmap(path)})


def _font_coverage_report(declared: str, obs: Observation) -> list[Finding]:
    glyphs = declared
    val = obs.value
    if not val["exists"]:
        return [Finding(
            ERROR,
            f"{_COVERAGE_FONT} is MISSING from {_FONTS_DIR}",
            "The dashboard web terminal and telegram `/screenshot` both fall back to this "
            f"file for {glyphs!r} — no other bundled font contains them (CMX-156/159). "
            "Without it both surfaces render tofu (▢) for every tool-marker and spinner "
            "glyph, with no error anywhere: a missing @font-face 404s silently in the "
            "browser, and the screenshot renderer's cmap lookup just skips to the next "
            "(non-covering) font in its chain.",
        )]
    cmap = val["cmap"]
    if cmap is None:
        return [Finding(
            OK, f"{_COVERAGE_FONT} present ({_FONTS_DIR})",
            "fontTools is not installed (the `[telegram]` extra), so chela can only "
            "confirm the file EXISTS, not that it still contains the glyphs both surfaces "
            "need. Install `chelamux[telegram]` for a full check.",
        )]
    missing = [ch for ch in glyphs if ord(ch) not in cmap]
    if missing:
        return [Finding(
            ERROR,
            f"{_COVERAGE_FONT} is missing glyph(s): {''.join(missing)!r}",
            f"The file is there but its cmap no longer covers {missing!r} — a re-subset "
            "or a corrupted download. The dashboard web terminal and telegram "
            "`/screenshot` both rely on this exact file for these glyphs; either would "
            "now render tofu (▢) for the missing ones, silently.",
        )]
    return [Finding(OK, f"{_COVERAGE_FONT} covers all {len(glyphs)} TUI marker glyphs")]


def _ago(seconds: float) -> str:
    if seconds < 90:
        return f"{int(seconds)}s ago"
    if seconds < 5400:
        return f"{int(seconds / 60)}m ago"
    return f"{seconds / 3600:.1f}h ago"


# --- fact: is this checkout's branch actually in sync with its upstream? -------------
#
# CMX-168 taught ``chela update`` to recover when the upstream history was rewritten
# (``git filter-repo`` + force-push): back up the pre-rewrite HEAD, then reset onto the
# new history. But that recovery only ever runs when a human (or the auto-update sweep)
# actually invokes ``chela update`` — someone who instead runs ``chela doctor`` learned
# nothing was wrong at all. This fact closes that gap: it asks the same question
# ``chela update`` asks (is HEAD diverged from ``@{u}``?) and points at the fix, but it
# NEVER fetches or resets anything itself — the ``reset --hard`` action lives only in
# ``chela.update.apply``. ``fetch=False`` keeps this as fresh as the last real fetch
# (a `chela update --check`, the daemon's periodic notifier) and never a network call —
# the same trade the `update_available` capability row already makes.
#
# CMX-199: this fact used to check ONLY divergence (``ahead > 0``) and print "repo is in
# sync" the moment that was false — even with the checkout dozens of commits BEHIND. On
# 2026-07-31, five PRs merged to ``dev`` in one day and none of them ran: the checkout sat
# 5 commits behind and every `chela-*` PM2 service kept serving whatever it last loaded,
# with 0 restarts, for hours — and `chela doctor` said "in sync" the whole time. "In sync"
# now means what it says: no divergence AND nothing to pull.

def _upstream_synced_applies() -> bool:
    from chela import update                        # lazy: doctor must import cheaply

    try:
        update.repo_root()
    except update.NotAGitCheckout:
        return False                                 # no fact of a pip install
    return True


def _upstream_synced_status():
    """Seam: the real answer is ``chela.update.commits_behind(fetch=False)``; the test
    suite hands this a fixed status instead of shelling out to git."""
    from chela import update                        # lazy: doctor must import cheaply

    return update.commits_behind(fetch=False)


def _upstream_synced_read() -> Observation:
    from chela import update                        # lazy: doctor must import cheaply

    try:
        status = _upstream_synced_status()
    except update.NotAGitCheckout as e:
        return cannot_verify(str(e))
    if not status.ok:
        return cannot_verify(status.error or "git rev-list failed")
    return observed(status)


def _upstream_synced_report(_declared: None, obs: Observation) -> list[Finding]:
    status = obs.value
    if status.error:
        return []                                    # e.g. "no upstream configured"
    if status.ahead > 0:
        return [Finding(
            ERROR,
            f"repo is {status.ahead} commit(s) AHEAD of its upstream on branch "
            f"{status.branch!r} — diverged, not fast-forwardable",
            "This is exactly the shape an upstream history rewrite (e.g. `git filter-repo` + "
            "force-push) leaves behind — as well as genuine unpushed local commits. `chela "
            "update` tells the two apart and recovers safely from a real rewrite (backs up "
            "the pre-rewrite HEAD to a `refs/chela-backup/...` ref, then resets onto the new "
            "history), or refuses loudly, explaining why, if it is not one. Run `chela "
            "update` to find out which — doctor only detects the condition; it never "
            "fetches or resets this repo itself.",
        )]
    if status.behind > 0:
        return [Finding(
            WARN,
            f"repo is {status.behind} commit(s) BEHIND its upstream on branch "
            f"{status.branch!r} — merged work sitting unpulled",
            "CMX-199: this is the exact shape that let five merged PRs sit inert for a "
            "full day — the checkout falls behind and every `chela-*` PM2 service keeps "
            "serving whatever it last loaded, with nothing anywhere saying so. Run `chela "
            "update` (pulls, `uv sync`s, and restarts every running `chela-*` service in "
            "one step) or use the dashboard's Update control in the Settings drawer — "
            "doctor only detects the gap; it never pulls or restarts anything itself.",
        )]
    return [Finding(
        OK, "repo is in sync with its upstream (no local divergence, nothing to pull)")]


# --- fact: is the RUNNING code the checkout's HEAD, or just the checkout itself? -----
#
# ``repo.upstream_synced`` above is a fact about the CHECKOUT — whether the files on disk
# match the branch's upstream. It says nothing about whether the `chela-*` PM2 services
# actually serving traffic have loaded those files. `chela update` pulls and restarts in
# one step, so the gap only opens when someone bypasses it — a bare `git pull` run by
# hand. That leaves the checkout genuinely in sync (`ahead == behind == 0`) while every
# running service keeps executing the process image from its OWN last start, unaware
# anything on disk changed. This fact catches that: it compares each online service's PM2
# start time against the checked-out commit's own (fixed) committer date. Like
# `repo.upstream_synced`, it only ever reads — the `pm2 restart` action lives in
# `chela.update.apply`.

def _services_current_status():
    """Seam: the real answer is ``chela.update.services_running_stale_code()``; the test
    suite hands this a fixed status instead of shelling out to git/pm2."""
    from chela import update                        # lazy: doctor must import cheaply

    return update.services_running_stale_code()


def _services_current_read() -> Observation:
    from chela import update                        # lazy: doctor must import cheaply

    try:
        status = _services_current_status()
    except update.NotAGitCheckout as e:
        return cannot_verify(str(e))
    if not status.ok:
        return cannot_verify(status.error or "git log failed")
    return observed(status)


def _services_current_report(_declared: None, obs: Observation) -> list[Finding]:
    status = obs.value
    if not status.stale:
        return [Finding(
            OK, "running chela-* services match the checked-out code (or none are up)")]
    names = ", ".join(status.stale)
    return [Finding(
        WARN,
        f"{len(status.stale)} running service(s) predate the checked-out code: {names}",
        "These PM2 services started before the commit now checked out existed, so they "
        "cannot be running it — the checkout itself may report as fully in sync while "
        "this is true, since a bare `git pull` (bypassing `chela update`, which pulls AND "
        f"restarts together) never restarts anything. `pm2 restart {names}` (or `chela "
        "update`, idempotent when there's nothing left to pull) picks up the new code.",
    )]


# --- fact: rows a hard tmux death orphaned, that nothing else surfaces --------------

def _restore_scan(now: str) -> int:
    """Seam: the real answer is ``len(chela.restore.scan_all(...))`` over the three live
    stores (``inbox.json`` watches, the dispatcher's ``runs`` table, ``session-ids.json``).
    A module-level function, like :func:`_parked_runs` / :func:`_reviewed_prs` above, so the
    test suite can hand it a fixed count instead of reaching ``dispatcher.DB_PATH`` /
    ``sessionids``'s own store path — both cached at import time against the real
    ``~/.chela``, not the fixture's temp one.
    """
    from chela import dispatcher, inbox, restore, sessionids

    store = inbox.load()
    try:
        runs = dispatcher.list_runs()
    except Exception:
        runs = []
    return len(restore.scan_all(store["watches"], runs, sessionids.entries(), now))


def _restore_read() -> Observation:
    """Every store ``chela restore`` scans, read live — see ``chela/restore.py``.

    ``chela doctor`` was green through the 2026-07-14 OOM ``chela/epoch.py``'s own
    docstring documents: detection existed, but nothing counted the stamped rows a dead
    server left behind and put the count somewhere a green doctor run would have to look
    past. This closes that hole without adding a private check — it reads back the same
    :func:`chela.restore.scan_all` a human would get from running the CLI.
    """
    if _tmux_or_unverifiable() is None:
        return cannot_verify("tmux is not on PATH, so chela cannot compare a stamped row's "
                             "epoch against the one running now.")
    now = epoch.current()
    if now is None:
        return cannot_verify("no tmux server is running, so there is no current epoch to "
                             "compare stamped rows against.")
    return observed(_restore_scan(now))


def _restore_report(_declared: None, obs: Observation) -> list[Finding]:
    n = obs.value
    if not n:
        return [Finding(OK, "no stamped rows from a dead tmux epoch")]
    return [Finding(
        WARN, f"{n} stamped row(s) from a dead epoch → `chela restore`",
        "A hard tmux death (OOM, restart) left these pointing at a server that no longer "
        "exists — this is the exact condition that stayed invisible through the 2026-07-14 "
        "OOM `chela/epoch.py`'s own docstring documents. Run `chela restore` to see every "
        "row, which store it is in, and whether it is REVIVABLE (its session is alive under "
        "a new address — re-register it) or MANUAL (it carries the exact relaunch command).",
    )]


# --- the registry ---------------------------------------------------------------------

def facts() -> list[Fact]:
    """Every fact chela's behaviour depends on — the whole registry, in reading order.

    Add an entry here and ``chela doctor`` checks it with **no new doctor code**. The
    price of an entry is a red test (see this module's docstring): corrupt the owned value,
    watch the gate go red, name the fact.
    """
    return [
        Fact(
            name="env.file",
            declared_by="the operator (examples/chela.env)",
            owned_by="the env file on disk — the copy every chela process sources",
            declare=config.env_file_path,
            read_back=_env_file_read,
            report=_env_file_report,
        ),
        Fact(
            name="env.running",
            declared_by="the env file",
            owned_by="the RUNNING process environment (what pm2 / the shell exported)",
            declare=declared_env,
            read_back=lambda: observed(
                {k: v for k, v in os.environ.items() if k in KNOWN_VARS}),
            report=_running_env_report,
        ),
        Fact(
            name="tmux.session",
            declared_by="CHELA_TMUX_SESSION (else $TMUX_PANE, else the default)",
            owned_by="tmux",
            declare=config.current_session,
            read_back=_session_read,
            report=_session_report,
            # WARN, not ERROR: doctor is run on machines whose fleet is deliberately down
            # (a fresh install, CI, a container), and exiting 1 there would cry wolf at
            # the operator and the dispatcher, which both read the exit code. Loud, not
            # fatal — but never silent.
            unverifiable_level=WARN,
        ),
        Fact(
            name="dashboard.port",
            declared_by="CHELA_DASHBOARD_PORT (else the default)",
            owned_by="the process that BOUND the socket (it publishes dashboard.port)",
            declare=config.dashboard_port,
            read_back=_port_read,
            report=_port_report,
        ),
        Fact(
            name="dashboard.update_lock",
            declared_by="nothing — chela never predicts this; the lock is either held "
                        "past the ceiling an honest update.apply() run can take, or it "
                        "isn't",
            owned_by="the dashboard process itself (it publishes update-apply-lock.json "
                     "for as long as its update-apply lock is held) — pid-checked, like "
                     "dashboard.port",
            declare=lambda: None,
            read_back=_update_apply_lock_read,
            report=_update_apply_lock_report,
        ),
        Fact(
            name="agents.native_status_feed",
            declared_by="nothing — chela never configures whether `claude agents --json` "
                        "succeeds; the dashboard's status cache just assumes it answers "
                        "within agent_manager._STATUS_CMD_TIMEOUT",
            owned_by="the `claude` CLI itself — asked fresh, right now, the same call the "
                     "dashboard's status cache makes",
            declare=lambda: None,
            read_back=_native_status_read,
            report=_native_status_report,
        ),
        Fact(
            name="plugin.rendered",
            declared_by="hooks.hooks_spec() — the manifest chela renders right now",
            owned_by="$CHELA_DIR/plugin/hooks/hooks.json — the copy `/plugin install` "
                     "COPIES FROM",
            declare=lambda: hooks.hooks_spec(effective_port()),
            read_back=_rendered_read,
            report=_rendered_report,
        ),
        Fact(
            name="plugin.installed",
            declared_by="hooks.hooks_spec() — the manifest chela renders right now",
            owned_by="Claude Code's plugin cache — the manifest EVERY AGENT LOADS at "
                     "startup",
            declare=lambda: hooks.hooks_spec(effective_port()),
            read_back=_installed_read,
            report=_installed_report,
            applies=_plugin_applies,
        ),
        Fact(
            name="plugin.hooks_flowing",
            declared_by="chela.sessions — the live claude-agent windows old enough that "
                        "SessionStart should long since have fired",
            owned_by="the event log — the ONLY place a hook's arrival is ever recorded",
            declare=_hooks_flowing_declared,
            read_back=_hooks_flowing_read,
            report=_hooks_flowing_report,
            applies=_hooks_flowing_applies,
        ),
        Fact(
            name="plugin.hooks_attributed",
            declared_by="chela.hooks.ingest — every hook event it appends",
            owned_by="the event log's own copy — hook.* records where wid_for_session "
                     "actually landed None",
            declare=lambda: None,
            read_back=_hooks_unattributed_read,
            report=_hooks_unattributed_report,
            applies=_hooks_flowing_applies,
        ),
        Fact(
            name="plugin.hooks_wid_rejected",
            declared_by="chela.hooks.ingest — every hook event it appends",
            owned_by="the event log's own copy — hook.* records whose X-Chela-Wid named "
                     "a window that was not live (rejected_wid), never the unset case",
            declare=lambda: None,
            read_back=_hooks_rejected_wid_read,
            report=_hooks_rejected_wid_report,
            applies=_hooks_flowing_applies,
        ),
        Fact(
            name="peer.transport",
            declared_by="chela.sessions — the live claude-agent windows any peer-"
                        "eligible send (chela msg/broadcast, rooms, the decisions "
                        "inbox) could target",
            owned_by="whether a connect() actually succeeds against "
                     "deterministic_peer_socket_path, else the legacy pid-derived guess "
                     "(messenger.peer_transport_kind) — not merely whether the file exists",
            declare=_peer_transport_declared,
            read_back=_peer_transport_read,
            report=_peer_transport_report,
        ),
        Fact(
            name="daemon.capabilities",
            declared_by="this shell's config (capabilities.effective())",
            owned_by="the RUNNING daemon — it publishes daemon.json at startup, pid-checked",
            declare=lambda: [c.as_dict() for c in capabilities.effective()],
            read_back=_daemon_read,
            report=_daemon_report,
        ),
        Fact(
            name="dispatch.workflows",
            declared_by="CHELA_DISPATCH_WORKFLOWS (as the running daemon published it)",
            owned_by="the filesystem and the tracker each WORKFLOW.md names",
            declare=dispatched_workflows,
            read_back=_workflows_read,
            report=_workflows_report,
        ),
        Fact(
            name="dispatch.agent_cmd",
            declared_by="each dispatched workflow's resolved `agent.cmd` "
                        "(resolve_agent_cmd's own precedence: WORKFLOW.md, else "
                        "Settings, else the built-in default)",
            owned_by="the shell PATH — whether the resolved command's binary can "
                     "actually be found and run",
            declare=_agent_commands,
            read_back=_agent_cmd_read,
            report=_agent_cmd_report,
        ),
        Fact(
            name="dispatch.gh_auth",
            declared_by="nothing — chela never records this; every dispatched "
                        "WORKFLOW.md's prompt ends with `gh pr create`",
            owned_by="gh's own auth state (`gh auth status`)",
            declare=lambda: None,
            read_back=_gh_auth_read,
            report=_gh_auth_report,
        ),
        Fact(
            name="dispatch.base_branch",
            declared_by="each dispatched workflow's `workspace.base_branch` (default "
                        "`master`) — what every worktree forks from and every PR "
                        "targets",
            owned_by="git — the branch (local, or `origin`-tracking) either exists in "
                     "the repo or it does not",
            declare=_base_branches,
            read_back=_base_branch_read,
            report=_base_branch_report,
        ),
        Fact(
            name="dispatch.base_write_remote",
            declared_by="nothing — chela never records this; a workflow needing an "
                        "unattended base-branch write (a markdown tracker's strike, or "
                        "a `trial_ledger:` opt-in) either has a repo remote to write "
                        "through or it does not",
            owned_by="git — `git -C <repo> remote`, the one precondition "
                     "`dispatcher._base_write_worktree` cannot self-heal",
            declare=_base_write_targets,
            read_back=_base_write_read,
            report=_base_write_report,
        ),
        Fact(
            name="dispatch.hold",
            declared_by="nothing — an operator TAKES a hold; there is no config for it",
            owned_by="$CHELA_DIR/dispatch-hold.json — the file the daemon reads each tick",
            declare=lambda: None,
            read_back=_hold_read,
            report=_hold_report,
        ),
        Fact(
            name="tmux.windows",
            declared_by="the run row's window_id + window_epoch, recorded at spawn "
                        "(CMX-62, CMX-77)",
            owned_by="tmux — window liveness, and which server issued the id, are its to "
                     "answer and nobody else's",
            declare=_in_flight_runs,
            read_back=_windows_read,
            report=_windows_report,
            unverifiable_level=WARN,      # same reason as tmux.session
        ),
        Fact(
            name="inbox.address",
            declared_by="$CHELA_DIR/inbox.json — the window id the orchestrator registered "
                        "(or $CHELA_ORCHESTRATOR_WID)",
            owned_by="tmux — it issues `@N` PER SERVER, so it alone can say whether that id "
                     "still names the window it was recorded for",
            declare=_inbox_address,
            read_back=_inbox_read,
            report=_inbox_report,
            unverifiable_level=WARN,      # same reason as tmux.session
        ),
        Fact(
            name="runs.parked_branch",
            declared_by="the run row's branch_name, for runs parked in the rework loop "
                        "(changes_requested / needs_human)",
            owned_by="git — the branch either exists or the work is unreachable, and that "
                     "is not chela's to say",
            declare=_parked_runs,
            read_back=_parked_read,
            report=_parked_report,
        ),
        Fact(
            name="pr.checks",
            declared_by="the run row's pr_checks — chela's cache of GitHub's rollup, which "
                        "is what the Merge button and the batch merge actually gate on",
            owned_by="GitHub — it runs the checks, and whether a PR can ship is not chela's "
                     "to say (nor the agent's: 'my tests passed' is not the same claim)",
            declare=_reviewed_prs,
            read_back=_checks_read,
            report=_checks_report,
        ),
        Fact(
            name="relay.transcripts",
            declared_by="the Telegram bindings — every window that has been PROMISED a "
                        "topic",
            owned_by="the transcript the window's agent is really writing (chela.sessions "
                     "resolves it by session id — the event log, then `claude --resume`, "
                     "then, only then, the cwd)",
            declare=_bound_windows,
            read_back=_relay_read,
            report=_relay_report,
            unverifiable_level=WARN,      # same reason as tmux.session
        ),
        Fact(
            name="windows.resolvable",
            declared_by="chela.sessions — its own POSIX fallback path, exercised only "
                        "when this host has no /proc",
            owned_by="the shell PATH — whether pgrep/ps, the fallback's own dependencies, "
                     "are actually there",
            declare=lambda: _WINDOW_SHIM_BINARIES,
            read_back=_windows_resolvable_read,
            report=_windows_resolvable_report,
            applies=_windows_resolvable_applies,
        ),
        Fact(
            name="fonts.glyph_coverage",
            declared_by="the render surfaces that fall back to it for coverage "
                        "(chela/dashboard/app.py _TERM_FONTS, chela/telegram/"
                        "screenshot.py) — both need the TUI marker glyphs and neither "
                        "font they pick before it contains them (CMX-156/159)",
            owned_by="the font file on disk (chela/dashboard/static/fonts/"
                     f"{_COVERAGE_FONT}) — its own cmap, the same way the /screenshot "
                     "renderer picks a face per glyph",
            declare=lambda: _TUI_MARKER_GLYPHS,
            read_back=_font_coverage_read,
            report=_font_coverage_report,
        ),
        Fact(
            name="tests.js_suites",
            declared_by="the *.test.mjs files in the repo",
            owned_by="the pytest collector — it decides what actually RUNS",
            declare=_js_suites_on_disk,
            read_back=_collector_read,
            report=_collector_report,
            applies=_collector_applies,
        ),
        Fact(
            name="repo.upstream_synced",
            declared_by="nothing — chela never records this; a checkout's branch "
                        "either tracks its upstream cleanly or it doesn't",
            owned_by="git — the local remote-tracking ref (`@{u}`), as fresh as the "
                     "last real `git fetch` chela ran",
            declare=lambda: None,
            read_back=_upstream_synced_read,
            report=_upstream_synced_report,
            applies=_upstream_synced_applies,
        ),
        Fact(
            name="repo.services_current",
            declared_by="nothing — chela never records this; a running service either "
                        "started after the code it's running was committed, or it didn't",
            owned_by="PM2 (`pm_uptime` — each online chela-* service's own last-start "
                     "time) compared against git's committer date for the checked-out "
                     "HEAD",
            declare=lambda: None,
            read_back=_services_current_read,
            report=_services_current_report,
            applies=_upstream_synced_applies,     # same "is this a git checkout" gate
        ),
        Fact(
            name="restore.dead_epoch_rows",
            declared_by="nothing — chela never predicts this; a stamped row either "
                        "matches the running tmux epoch or it doesn't",
            owned_by="tmux (the running epoch) joined against inbox.json watches, the "
                     "dispatcher's runs table, and session-ids.json — the same three "
                     "stores `chela restore` scans",
            declare=lambda: None,
            read_back=_restore_read,
            report=_restore_report,
            unverifiable_level=WARN,      # same reason as tmux.session
        ),
    ]


def _lines(items: list[str]) -> str:
    return "\n".join(f"    - {item}" for item in items)
