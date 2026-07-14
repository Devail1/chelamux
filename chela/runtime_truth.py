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
import shutil
import subprocess
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from chela import capabilities, config, discovery, epoch, hold, hooks, sessions
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


def _plugin_applies() -> bool:
    """A machine with nothing rendered is not running hooks; an INSTALLED copy still has
    to be checked though — a plugin installed from a checkout renders nothing here."""
    return _rendered_path().exists() or bool(hooks.installed_plugins())


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
    return sorted(
        str(p.relative_to(root)) for p in root.rglob("*.test.mjs")
        if not _SKIP_DIRS.intersection(p.relative_to(root).parts)
    )


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
            "and nothing said so. Fix: run `chela watch` in the orchestrator's session — the "
            "queue is intact and goes out on its next idle tick.",
        )]

    if wid not in live:
        return [Finding(
            ERROR if queued else WARN,
            f"the decisions inbox is addressed to {wid}, and tmux has no such window",
            f"The session that registered as the orchestrator is gone. {queued} event(s) are "
            "queued behind that address and nothing is delivering them. Register the session "
            "that is doing the orchestrating: `chela watch`.",
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


def _ago(seconds: float) -> str:
    if seconds < 90:
        return f"{int(seconds)}s ago"
    if seconds < 5400:
        return f"{int(seconds / 60)}m ago"
    return f"{seconds / 3600:.1f}h ago"


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
            name="tests.js_suites",
            declared_by="the *.test.mjs files in the repo",
            owned_by="the pytest collector — it decides what actually RUNS",
            declare=_js_suites_on_disk,
            read_back=_collector_read,
            report=_collector_report,
            applies=_collector_applies,
        ),
    ]


def _lines(items: list[str]) -> str:
    return "\n".join(f"    - {item}" for item in items)
