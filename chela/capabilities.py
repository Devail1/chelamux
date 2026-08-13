"""What the daemon can actually DO — announced at startup, published for other processes.

**A disabled subsystem must announce itself.** The dispatcher was off for nine hours
while everything reported healthy: ``CHELA_DISPATCH_WORKFLOWS`` had gone missing from the
env file, ``DISPATCH_WORKFLOWS`` was ``[]``, and ``cmd_run``'s ``if DISPATCH_WORKFLOWS:``
guard skipped the dispatcher *and the reconcile loop that rides the same tick*. The only
tell was the ABSENCE of a log line — nobody reads an absence. No task was claimed, a
merged PR's run sat in ``awaiting_review`` forever, and (``concurrency.max: 1``) that
stale run held the only slot, so even a fixed config would have been blocked behind it.

Two rules come out of that, and this module exists to enforce them:

* **Silence never means off.** Every capability behind an ``if <config>:`` guard emits a
  startup line saying ON or OFF — a WARNING for the ones whose absence is a foot-gun.
  An empty config stays a *valid* state; it stops being an *invisible* one.
* **The config is not the capability.** ``chela doctor`` runs in a different process from
  the daemon (exactly the trap that broke the hooks plugin), so "the env says enabled" is
  not "the running daemon has it enabled" — both agreed here, and both were wrong. So the
  daemon writes down what it REALLY came up with, next to ``dashboard.port`` and for the
  same reason, and doctor reads that rather than guessing. With no daemon running, doctor
  says plainly that it is inferring.
"""
from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass, field
from pathlib import Path

from chela import config, hold, inbox, memcap, notify, update

# The state file is written once at startup and deleted on a clean exit. A crash leaves
# it behind; the pid check in live() is what makes a stale file harmless — same contract
# as config.live_dashboard().
STATE_FILE_NAME = "daemon.json"


@dataclass(frozen=True)
class Capability:
    """One thing the daemon either does or does not do, and why."""

    key: str                        # stable id: "dispatch", "reconcile", ...
    label: str                      # human name for a log line / the dashboard
    on: bool
    detail: str                     # what is (or is not) happening, in a sentence
    fix: str = ""                   # how to turn it on — only meaningful when off
    warn_when_off: bool = False     # OFF is a foot-gun, not a preference: log WARNING
    warn_when_on: bool = False      # ON is the risk (e.g. auto-merge) — log WARNING, not INFO
    # True only for a capability backed by a knob the running daemon re-reads on every use
    # (no restart needed — see the non-``restart_required`` ``DispatchKnob`` rows in
    # config.py, e.g. ``memory_slice_budget_bytes``). For those, "what the daemon published
    # at boot" is provably NOT "what the daemon is doing right now" the moment an operator
    # edits the knob live — see :func:`live`, which recomputes exactly these rather than
    # trusting the stale snapshot. Every other capability (dispatch, judge, ...) is latched
    # at some module's import, so its boot snapshot IS the running daemon's truth until a
    # restart — leave those alone.
    live_reload: bool = False
    extra: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {"key": self.key, "label": self.label, "on": self.on,
                "detail": self.detail, "fix": self.fix,
                "warn_when_off": self.warn_when_off, "warn_when_on": self.warn_when_on,
                "live_reload": self.live_reload,
                **self.extra}


def _update_available_capability() -> Capability:
    """CMX-142 part 1: is the checkout behind its upstream?

    ``fetch=False`` — this reads only the LOCAL remote-tracking ref, deliberately never a
    network call. ``effective()`` runs on every `chela doctor` invocation and every daemon
    boot; a `git fetch` in there would make both as slow (and as flaky) as the network,
    which is the same trap ``notify``/``dispatch`` avoid by reading local state only. The
    daemon's own periodic ``update.check_and_notify`` (see `cmd_run`) is what actually
    fetches, on its own hourly cadence — this row is only ever as fresh as that last fetch.
    """
    try:
        status = update.commits_behind(fetch=False)
    except update.NotAGitCheckout as e:
        return Capability(
            key="update_available", label="Update available", on=False,
            detail=f"not a git checkout — {e}", fix="pip install --upgrade chelamux",
        )
    if not status.ok:
        return Capability(
            key="update_available", label="Update available", on=False,
            detail=f"couldn't tell ({status.error})", fix="chela update --check",
        )
    if status.error:
        # ok=True but carrying a note (no upstream configured) — "up to date" would be a
        # guess dressed as an answer; say what's actually true instead.
        return Capability(
            key="update_available", label="Update available", on=False,
            detail=status.error, fix="",
        )
    return Capability(
        key="update_available", label="Update available", on=bool(status.behind),
        detail=(f"{status.behind} commit(s) behind — run `chela update`" if status.behind
                else "up to date (as of the last fetch)"),
        fix="chela update",
    )


def _worktree_disk_budget_capability() -> Capability:
    """🧹💽 CMX-164: the `memcap` analog for disk. Off is the safe, unopinionated default
    for a fresh install — a small repo never needs it — but a heavier one (a Rust
    `target/`, an ML venv, a Node monorepo) can fill the disk with nothing to stop it,
    so an operator who knows their repo's footprint gets a rail to turn on.
    """
    return Capability(
        key="worktree_disk_budget", label="🧹💽 Worktree disk budget",
        on=bool(config.worktree_disk_budget_bytes()),
        detail=(f"refusing a fresh claim once a workflow's worktree root exceeds "
                f"{config.human_size(config.worktree_disk_budget_bytes())}"
                if config.worktree_disk_budget_bytes() else
                "off — CHELA_WORKTREE_DISK_BUDGET is unset/0, so no rail stops a heavy "
                "repo from filling the disk"),
        fix="set CHELA_WORKTREE_DISK_BUDGET=20G (or any K/M/G/T byte size) in "
            f"{config.env_file_path() or '$CHELA_DIR/chela.env'} — takes effect on "
            "the next dispatch, no restart required",
        # dispatcher.py reads worktree_disk_budget_bytes() fresh on every tick — this
        # is not a ``restart_required`` DispatchKnob, so the boot-time publish() must
        # not be trusted as this capability's current state (see live_reload's doc).
        live_reload=True,
    )


def _memory_slice_capability() -> Capability:
    """🧠🔒 CMX-264/280: the `memcap` analog for memory. A per-job memory ceiling does not
    bound the box — see docs/RESOURCE_ISOLATION.md's 2026-07-14 incident, where 4
    agents each under their own 6G cap still authorised 24G on a 19G box and the
    kernel's global OOM killer took tmux and the orchestrator with it, not the jobs
    that caused it. This rail puts every dispatched agent AND judge into one SHARED
    cgroup slice so the ceiling applies to their SUM. Off by default (unset/0), same
    posture as the disk-budget rail above; further gated on a working
    `systemd --user` session, so an operator can turn the knob on and still see
    exactly why it isn't taking effect on their box.

    CMX-280: ``CHELA_MEMORY_SLICE_BUDGET`` unset/0 answers "did chela turn this on?",
    not "is a bound in force?" — this box's own cgroup can already be bounded by
    something chela never touched (an operator's own ``~/bin/memcap``-style wrapper,
    see ``docs/RESOURCE_ISOLATION.md``), and reporting that as OFF is actively
    misleading: whoever sizes ``judge_max_concurrent`` off a doctor line that says OFF
    gets it wrong in either direction. :func:`chela.memcap.live_bound` answers the
    question that matters — a ceiling already in force on this process's own cgroup
    ancestry bounds an unwrapped dispatched agent exactly as much as chela's own rail
    would, whoever set it.
    """
    fix = ("set CHELA_MEMORY_SLICE_BUDGET=12G (or any K/M/G/T byte size) in "
           f"{config.env_file_path() or '$CHELA_DIR/chela.env'} — takes effect on "
           "the next dispatch, no restart required")
    if config.memory_slice_budget_bytes() and memcap.available():
        return Capability(
            key="memory_slice_budget", label="🧠🔒 Shared memory slice", on=True,
            detail=(f"dispatched agents and judges launch into one shared "
                    f"{memcap.SLICE_NAME} capped at "
                    f"{config.human_size(config.memory_slice_budget_bytes())} TOTAL"),
            fix=fix, live_reload=True,
        )
    if config.memory_slice_budget_bytes():
        return Capability(
            key="memory_slice_budget", label="🧠🔒 Shared memory slice", on=False,
            detail="CHELA_MEMORY_SLICE_BUDGET is set but `systemd-run` is not on PATH "
                   "— launching unwrapped, no memory ceiling enforced",
            fix=fix, live_reload=True,
        )
    bound = memcap.live_bound()
    if bound and not bound["chela_owned"]:
        current = bound["current_bytes"]
        occupancy = ""
        if current is not None:
            pct = current / bound["max_bytes"] * 100
            headroom = config.human_size(bound["max_bytes"] - current)
            occupancy = (f", currently using {config.human_size(current)} "
                         f"({pct:.0f}%, ~{headroom} headroom)")
        return Capability(
            key="memory_slice_budget", label="🧠🔒 Shared memory slice", on=True,
            detail=(f"a bound IS in force from outside chela — `{bound['unit']}` caps "
                    f"this session at {config.human_size(bound['max_bytes'])}"
                    f"{occupancy}. CHELA_MEMORY_SLICE_BUDGET is unset/0 — chela did not "
                    "set this ceiling, but every dispatched agent and judge is under "
                    "it anyway, launched or not"),
            fix="", live_reload=True,
        )
    return Capability(
        key="memory_slice_budget", label="🧠🔒 Shared memory slice", on=False,
        detail="off — CHELA_MEMORY_SLICE_BUDGET is unset/0, so no rail bounds the "
               "combined memory of every dispatched agent and judge",
        fix=fix, live_reload=True,
    )


def effective() -> list[Capability]:
    """The capabilities a daemon started with THIS process's config would have.

    Read from the same values ``cmd_run`` branches on, so the announcement cannot drift
    from the behaviour it describes.
    """
    workflows = config.DISPATCH_WORKFLOWS
    n = len(workflows)
    names = ", ".join(str(p) for p in workflows)
    # Both of these ride `if DISPATCH_WORKFLOWS:` in the daemon loop. The coupling is the
    # non-obvious part — a reader who knows dispatch is off does NOT thereby know that
    # nothing will ever reconcile a finished run — so it is spelled out twice, on purpose.
    dispatch_fix = (
        "set CHELA_DISPATCH_WORKFLOWS=/path/to/repo/WORKFLOW.md (colon-separated) in "
        f"{config.env_file_path() or '$CHELA_DIR/chela.env'} and restart the daemon"
    )
    # A HELD queue is a dispatcher that claims nothing — a disabled subsystem by any
    # honest reading, and this module's whole point is that one announces itself. It is
    # still `on` (the capability exists; reconciliation still rides it, and the hold is
    # a *transient* state a startup snapshot cannot track), so the hold is carried
    # alongside rather than folded into the flag: announce() warns about it, and the LIVE
    # surfaces — `chela doctor`, /api/settings — re-read the hold FILE rather than trust
    # this snapshot, because a hold taken after startup would never show up in it.
    held = hold.active()
    dispatch = Capability(
        key="dispatch",
        label="Work dispatcher",
        on=bool(workflows),
        detail=(f"{n} workflow{'' if n == 1 else 's'}, every "
                f"{config.dispatch_tick_interval()}s: {names}" if workflows else
                "CHELA_DISPATCH_WORKFLOWS is empty — NO task will ever be claimed from a "
                "tracker"),
        fix=dispatch_fix,
        warn_when_off=True,
        extra={
            "workflows": [str(p) for p in workflows],
            "hold": held.as_dict() if held else None,
        },
    )
    reconcile = Capability(
        key="reconcile",
        label="Run reconciliation",
        on=bool(workflows),
        detail=("merged/closed PRs close out their runs and free their concurrency slot"
                if workflows else
                "OFF FOR THE SAME REASON — reconciliation rides the dispatch tick, so an "
                "empty CHELA_DISPATCH_WORKFLOWS also means a merged PR's run sits in "
                "`awaiting_review` forever, holding its concurrency slot"),
        fix=dispatch_fix,
        warn_when_off=True,
    )
    return [
        Capability(
            key="scheduler", label="Scheduler", on=True,
            detail=f"polling every {config.scheduler_poll_interval()}s",
        ),
        Capability(
            key="capture", label="Cost history capture", on=True,
            detail=(f"context_snapshots every {config.capture_interval_seconds()}s, "
                    f"{config.context_snapshot_retention_days()}d retention"),
        ),
        dispatch,
        reconcile,
        Capability(
            key="notify", label="Needs-input notifications", on=notify.enabled(),
            detail=(f"every {config.NOTIFY_INTERVAL}s" if notify.enabled()
                    else "no notification will be sent when an agent blocks on a prompt"),
            fix="set CHELA_NOTIFY_URL",
        ),
        Capability(
            key="inbox", label="Decisions inbox", on=inbox.enabled(),
            detail=("pushing finished/blocked work to the orchestrator (inert until a "
                    "session runs `chela watch`)" if inbox.enabled()
                    else "a finished agent stays invisible to the orchestrator"),
            fix="unset CHELA_INBOX_ENABLED=false",
        ),
        # Not this daemon's to serve — the dashboard and scripts/agent-terminals.sh do —
        # but it is read from the same env file, so an operator reading this log wants its
        # state here too. Said honestly rather than implied.
        Capability(
            key="terminals", label="Terminal wall", on=config.TERMINALS_ENABLED,
            detail=("served by the dashboard / ttyd supervisor, not this daemon"
                    if config.TERMINALS_ENABLED
                    else "CHELA_TERMINALS_ENABLED=false — the dashboard serves no terminals"),
            fix="unset CHELA_TERMINALS_ENABLED=false",
        ),
        _update_available_capability(),
        _worktree_disk_budget_capability(),
        _memory_slice_capability(),
        # 🔀⚠️ CMX-138. The one fully-UNATTENDED merge path in the whole system — see
        # chela.automerge. OFF is the safe, expected state for every install but an operator's
        # own; ON gets its own WARNING line every boot (never just an INFO), because "silence
        # never means off" cuts both ways — a risky capability staying quietly ON is the same
        # blind spot as a needed one silently OFF.
        Capability(
            key="auto_merge", label="🔀⚠️ Auto-merge", on=config.AUTO_MERGE_ENABLED,
            detail=("UNATTENDED — every judge-clean `awaiting_review` PR is squash-merged on "
                    "this daemon's own tick, with NO human attending and NO attended-lease "
                    "required (contract.merge's base/CI/mergeable gate still applies in full; "
                    "only the human-attendance requirement is gone). Trust your judge."
                    if config.AUTO_MERGE_ENABLED else
                    "off — merging stays a human or attended-orchestrator act (the safe default)"),
            fix="unset CHELA_AUTO_MERGE — OFF is the recommended default for every install "
                "but an operator's own",
            warn_when_on=True,
        ),
        # ⬆️⚠️ CMX-148, part 2 of CMX-142. Same "silence never means off, and a risky ON
        # never means quiet" contract as auto_merge above, for the other fully-UNATTENDED
        # act this daemon can take on its own — see chela.update.auto_apply_sweep.
        Capability(
            key="auto_update", label="⬆️⚠️ Auto-update", on=config.AUTO_UPDATE_ENABLED,
            detail=("UNATTENDED — whenever this checkout falls behind its upstream, this "
                    "daemon pulls, `uv sync`s, and restarts its own `chela-*` services "
                    "(including itself) on its own hourly tick, with NO human attending "
                    "(`update.apply()`'s dirty-tree/diverged-branch refusal still applies "
                    "in full; only the human-attendance requirement is gone)."
                    if config.AUTO_UPDATE_ENABLED else
                    "off — updating stays a human act via `chela update` (the safe default)"),
            fix="unset CHELA_AUTO_UPDATE — OFF is the recommended default for every install "
                "but an operator's own",
            warn_when_on=True,
        ),
    ]


def announce(caps: list[Capability], log: logging.Logger) -> None:
    """Say, at startup, what is on and what is off. Never silence.

    Startup ONLY — deliberately not per tick: a drumbeat of the same line buries the log
    within a day, and a line nobody reads is the same as no line. The live surface for
    this is ``/api/settings`` (and ``chela doctor``), where a human can actually look.
    """
    log.info("Capabilities: %s", " ".join(
        f"{c.key}={'ON' if c.on else 'OFF'}" for c in caps))
    for cap in caps:
        held = cap.extra.get("hold")
        if cap.on and held:
            # ON, and claiming nothing anyway. A daemon that boots into a held queue and
            # says only "ON" is the nine-hour silence all over again, one layer in.
            log.warning(
                "%s: ON but the queue is HELD — %s. No task will be claimed until it is "
                "released (`chela dispatch --resume`); reconciliation continues.",
                cap.label, held.get("summary", "held"),
            )
        elif cap.on and cap.warn_when_on:
            log.warning("%s: ON — %s", cap.label, cap.detail)
        elif cap.on:
            log.info("%s: ON — %s", cap.label, cap.detail)
        elif cap.warn_when_off:
            log.warning("%s: OFF — %s%s", cap.label, cap.detail,
                        f" | fix: {cap.fix}" if cap.fix else "")
        else:
            log.info("%s: OFF — %s%s", cap.label, cap.detail,
                     f" | {cap.fix} to enable" if cap.fix else "")


# --- publishing: what the RUNNING daemon really came up with ------------------------

def state_file() -> Path:
    return config.CHELA_DIR / STATE_FILE_NAME


def publish(caps: list[Capability], boot_id: str = "") -> None:
    """Record this daemon's effective capabilities for every other process to read.

    Best-effort, like ``config.publish_dashboard_port``: a daemon that cannot write the
    file still runs — it just leaves doctor inferring from config instead of observing,
    which doctor then says out loud.
    """
    try:
        config.CHELA_DIR.mkdir(parents=True, exist_ok=True)
        state_file().write_text(json.dumps({
            "pid": os.getpid(),
            "ts": time.time(),
            "boot_id": boot_id,
            "session": config.current_session(),
            "capabilities": [c.as_dict() for c in caps],
        }) + "\n", encoding="utf-8")
    except OSError:
        pass


def clear() -> None:
    """Drop the published state on a clean shutdown (a crash leaves it; the pid check in
    :func:`live` is what makes that harmless either way)."""
    try:
        state_file().unlink()
    except OSError:
        pass


def live() -> dict | None:
    """What the RUNNING daemon published, or None if no daemon is running.

    A file whose pid is gone is stale — the daemon died — and counts as no daemon at all,
    so a crashed instance cannot keep claiming a capability nothing is providing.

    A ``live_reload`` capability (``memory_slice_budget``, ``worktree_disk_budget``) is
    reconciled against THIS process's current config before returning — measured
    2026-08-13: a 12G ``CHELA_MEMORY_SLICE_BUDGET`` added to the env file after the daemon
    booted was already bounding the box (``memcap.wrap_launch_cmd`` re-reads the knob every
    dispatch, no restart needed — that is the whole point of not marking it
    ``restart_required``), while ``chela doctor``/the dashboard kept reporting it OFF from
    the stale boot-time snapshot. Every other capability is latched at some module's
    import, so its boot snapshot genuinely IS the running daemon's truth until a restart —
    those are returned exactly as published, unchanged.
    """
    try:
        data = json.loads(state_file().read_text(encoding="utf-8"))
        pid = int(data.get("pid") or 0)
        caps = data["capabilities"]
    except (OSError, ValueError, TypeError, KeyError):
        return None
    if not isinstance(caps, list):
        return None
    if pid > 0:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return None
        except PermissionError:
            pass                 # alive, owned by someone else
        except OSError:
            return None
    fresh_by_key = None
    reconciled = []
    for cap in caps:
        if isinstance(cap, dict) and cap.get("live_reload"):
            if fresh_by_key is None:
                fresh_by_key = {c.key: c.as_dict() for c in effective()}
            cap = fresh_by_key.get(cap.get("key"), cap)
        reconciled.append(cap)
    return {**data, "capabilities": reconciled}


def live_capability(key: str) -> dict | None:
    """One capability as the running daemon published it, or None (no daemon / unknown
    key). ``None`` means *we do not know*, which is never the same as ``off``."""
    data = live()
    if not data:
        return None
    for cap in data["capabilities"]:
        if isinstance(cap, dict) and cap.get("key") == key:
            return cap
    return None
