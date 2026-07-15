"""🎭🤖 AUTO-LAUNCH THE ORCHESTRATOR — inbox-woken, attended-lease-gated (CMX-90).

The persona layer's last piece. Until now the orchestrator was the one persona that only ever
ran by *hand*: a human sat at a pane, ran ``chela watch`` to register it as the inbox target, and
worked the review loop. The judge and critic auto-launch (the judge on ``awaiting_review``, the
critic on dispatch); this closes the gap for the orchestrator — **chela launches it itself.**

Two design choices bound this v1, both straight from ``docs/ESCALATION_CONTRACT.md``:

* **Inbox-woken, NOT boot-persistent.** There is no orchestrator process sitting idle from boot.
  The wake trigger is the **decisions inbox having work with nobody live to take it** — a run hit
  ``awaiting_review`` (or any delegated event queued) and no orchestrator window is registered and
  alive. That is precisely the moment the human used to be paged; now it launches the persona
  instead. Nothing to do ⇒ nothing launched.
* **Attended-lease-gated (the supervision half).** The contract says the orchestrator stays
  human-attended until process isolation lands. :mod:`chela.personas.lease` is how "attended" is
  made real without isolation: the auto-launch fires **only while a human's attended-lease is
  active**. Grant it (``chela orchestrator attend``), and auto-launch is armed for that window;
  let it lapse, and the gate closes. The orchestrator is thus *attended-autonomous* — it acts
  autonomously within the lease, but never *unattended*.

⛔ **The decision is a pure, fail-closed function** (:func:`should_launch`) — the same discipline
as the merge gate. Every one of its inputs must hold or the launch does not fire, and *any*
unknown resolves to "do not launch". A wrong reading never spawns an orchestrator it should not;
at worst it withholds one a human then starts by hand. This is deliberately where the guard lives,
because the launch *action* (a tmux spawn) is the untestable half.

Off by default: ``CHELA_ORCHESTRATOR`` (``config.ORCHESTRATOR_ENABLED``) must be explicitly set —
auto-launching an agent that holds ``chela merge`` authority is not something a fresh install does
without being asked.
"""
from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import time
from pathlib import Path

from chela import config, event_log, inbox
from chela.config import TMUX_SESSION
from chela.personas import ORCHESTRATOR_PROMPT, lease

log = logging.getLogger(__name__)

# The tmux window the auto-launched orchestrator gets. One, by name — a second live orchestrator
# is exactly what the "already live" gate and the relaunch cooldown exist to prevent.
WINDOW_NAME = "orchestrator"

# A tiny bit of bookkeeping so we do not spawn a SECOND window while the first is still coming up:
# right after ``tmux new-window`` claude is not yet in the status map, so an "is one live?" probe
# would say no and relaunch. This file records the last auto-launch; the cooldown below holds the
# gate shut long enough for claude to start and register.
LAUNCH_STATE_FILE = "orchestrator-autolaunch.json"

# Long enough for `claude` to boot and appear in `claude agents --json`. If the launched window
# genuinely died, the cooldown lapses and the next inbox-woken tick relaunches — bounded, not a
# storm, because a launch only happens at all when there is queued work AND an active lease.
RELAUNCH_COOLDOWN_SECONDS = 180

# The inbox address states that mean a live orchestrator is ALREADY registered — a real window we
# must not double up on. GONE / DANGLING / NONE all mean "no live orchestrator", which (with the
# other gates) is a green light to launch one.
_LIVE_ADDR_STATES = (inbox.ADDR_OK, inbox.ADDR_UNSTAMPED)


def enabled() -> bool:
    """Is orchestrator auto-launch turned on at all? Off unless ``CHELA_ORCHESTRATOR`` is set."""
    return config.ORCHESTRATOR_ENABLED


# --- the decision: pure, fail-closed ----------------------------------------------------------

def should_launch(*, flag_on: bool, lease_active: bool, has_pending_work: bool,
                  orchestrator_live: bool, recently_launched: bool) -> tuple[bool, str]:
    """Should chela auto-launch the orchestrator right now? — ``(go, reason)``.

    Pure and fail-closed: EVERY condition must hold, and the first that does not is reported as
    the reason it did not fire (so ``chela orchestrator status`` and the event log can say *why*).
    A launch fires only when it is both **armed** (the flag is on and a human's lease is active)
    and **needed** (there is queued inbox work, no orchestrator is already live, and we did not
    just launch one). Corrupt any single check to a constant and a run that should have been
    withheld will fire — which is what the guard tests assert.
    """
    if not flag_on:
        return False, ("orchestrator auto-launch is off — set CHELA_ORCHESTRATOR=true to arm it")
    if not lease_active:
        return False, ("no active attended-lease — a human must run `chela orchestrator attend` "
                       "first (the supervision gate; auto-launch stays closed without it)")
    if not has_pending_work:
        return False, "no pending inbox work — nothing to wake the orchestrator for"
    if orchestrator_live:
        return False, "an orchestrator is already registered and live — not launching a second"
    if recently_launched:
        return False, ("an orchestrator was auto-launched moments ago — giving it time to come "
                       "up before considering another")
    return True, ""


# --- relaunch bookkeeping ---------------------------------------------------------------------

def _state_path() -> Path:
    return config.CHELA_DIR / LAUNCH_STATE_FILE


def record_launch(wid: str, now: float | None = None) -> None:
    """Stamp an auto-launch so the cooldown can hold the gate shut while claude boots.

    Best-effort: a stamp we fail to write only means the cooldown does not apply, and the
    "already live" gate still stops a double-launch the moment claude registers. Never raises.
    """
    now = time.time() if now is None else now
    try:
        config.CHELA_DIR.mkdir(parents=True, exist_ok=True)
        _state_path().write_text(
            json.dumps({"wid": wid, "launched_at": now}) + "\n", encoding="utf-8")
    except OSError as e:
        log.warning("could not record orchestrator auto-launch stamp: %s", e)


def recently_launched(now: float | None = None,
                      cooldown: float = RELAUNCH_COOLDOWN_SECONDS) -> bool:
    """Did we auto-launch within the cooldown? An unreadable/absent stamp reads as 'no'.

    Fail-*open* here, deliberately and unlike the rest of the module: this gate exists only to
    suppress a redundant relaunch, so treating a missing stamp as "not recent" errs toward
    launching — and the launch itself is still fully gated by flag + lease + pending-work +
    not-already-live. The worst case is one extra window, which the 'already live' gate then
    prevents from recurring; a corrupt stamp must never be able to *withhold* supervision.
    """
    now = time.time() if now is None else now
    try:
        data = json.loads(_state_path().read_text(encoding="utf-8"))
        launched_at = float(data["launched_at"])
    except FileNotFoundError:
        return False
    except (OSError, ValueError, TypeError, KeyError):
        return False
    return (now - launched_at) < cooldown


# --- deriving the live inputs -----------------------------------------------------------------

def orchestrator_live(store: dict, statuses: dict[str, str],
                      now_epoch: str | None = None) -> bool:
    """Is a live orchestrator already registered with the inbox?

    Reuses the inbox's own :func:`chela.inbox.address_state` — the single authority on whether the
    recorded orchestrator address names a real, current window. OK/unstamped ⇒ a live window is
    registered; NONE/GONE/DANGLING ⇒ nobody is home, so an auto-launch may proceed.
    """
    state, _ = inbox.address_state(store, statuses, now_epoch)
    return state in _LIVE_ADDR_STATES


def evaluate(store: dict, statuses: dict[str, str], now_epoch: str | None = None,
             now: float | None = None) -> tuple[bool, str]:
    """Read the live world and return the launch decision — ``(go, reason)``.

    The thin impure shell around :func:`should_launch`: it gathers the five booleans from the
    inbox store, the status map, the attended-lease and the relaunch stamp, then defers the
    verdict to the pure function. Keeping the read here and the logic there is what makes the
    logic testable without a daemon.
    """
    return should_launch(
        flag_on=enabled(),
        lease_active=lease.active(now) is not None,
        has_pending_work=bool(store.get("queue")),
        orchestrator_live=orchestrator_live(store, statuses, now_epoch),
        recently_launched=recently_launched(now),
    )


# --- the launch action (the untestable half; kept thin) ---------------------------------------

def _orchestrator_repo_dir() -> str:
    """Where the orchestrator agent runs. The first dispatch workflow's repo dir if one is
    configured (that is the repo it orchestrates), else the current working directory."""
    workflows = config.DISPATCH_WORKFLOWS
    if workflows:
        return str(Path(workflows[0]).parent)
    return os.getcwd()


def _spawn_orchestrator_window(repo_dir: str) -> str:
    """Open a tmux window and start ``claude`` in it with the orchestrator persona seeded.

    Mirrors the dispatcher's proven TWO-STEP pattern (:func:`chela.dispatcher._new_window` +
    ``send-keys 'claude …'``): never ``tmux new-window '<cmd>'`` — claude must be a child of the
    pane shell, or ``agent_manager.claude_pid`` never correlates it and the window gets no
    Telegram topic. Returns the new window's ``@id`` (or the bare name if tmux gave no id).
    """
    subprocess.run(["tmux", "kill-window", "-t", f"{TMUX_SESSION}:{WINDOW_NAME}"],
                   capture_output=True)  # best-effort: clear any stale same-name window first
    out = subprocess.run(
        ["tmux", "new-window", "-t", f"{TMUX_SESSION}:", "-n", WINDOW_NAME,
         "-c", repo_dir, "-P", "-F", "#{window_id}"],
        check=True, capture_output=True, text=True,
    )
    wid = out.stdout.strip() if isinstance(out.stdout, str) else ""
    target = wid if re.fullmatch(r"@\d+", wid) else WINDOW_NAME
    if re.fullmatch(r"@\d+", target):
        subprocess.run(
            ["tmux", "send-keys", "-t", f"{TMUX_SESSION}:{target}",
             f"export CHELA_WID={target}", "Enter"],
            check=True, capture_output=True,
        )
    # The persona is its runnable system prompt. ``--append-system-prompt`` loads it as the
    # orchestrator's identity for the whole session, on top of the user's Claude Code config.
    cmd = (f"claude --permission-mode auto "
           f"--append-system-prompt \"$(cat {ORCHESTRATOR_PROMPT})\"")
    subprocess.run(
        ["tmux", "send-keys", "-t", f"{TMUX_SESSION}:{target}", cmd, "Enter"],
        check=True, capture_output=True,
    )
    return target


def wake(repo_dir: str | None = None) -> dict:
    """Auto-launch the orchestrator and register it with the inbox. The ACTION half.

    Spawns the persona window, then registers it as THE inbox orchestrator so the queued
    ``awaiting_review`` events deliver straight into it — the window that just woke is the window
    the work is handed to. Records the launch (for the cooldown) and logs it with provenance, so a
    human can later ask *why did chela launch an orchestrator*. ⛔ The caller is responsible for
    the gate (:func:`evaluate`); this assumes the decision already said go.
    """
    repo_dir = repo_dir or _orchestrator_repo_dir()
    wid = _spawn_orchestrator_window(repo_dir)
    record_launch(wid)
    registered = inbox.register(wid)
    event_log.append(
        "orchestrator.autolaunch",
        f"auto-launched the orchestrator persona in {wid} (inbox-woken, under an attended-lease)",
        payload={"wid": wid, "repo_dir": repo_dir,
                 "registered": bool(registered.get("ok")),
                 "queued": registered.get("queued")},
        wid=wid if re.fullmatch(r"@\d+", wid) else None,
    )
    log.info("orchestrator: auto-launched in %s (repo=%s, registered=%s)",
             wid, repo_dir, registered.get("ok"))
    return {"ok": True, "wid": wid, "registered": registered}


def maybe_wake(store: dict, statuses: dict[str, str], now_epoch: str | None = None,
               repo_dir: str | None = None) -> dict | None:
    """One inbox-woken pass: evaluate the gate and, only if it says go, auto-launch. Daemon entry.

    Returns the launch result on a launch, or ``None`` when the gate withheld one (the common
    case). A withheld launch is logged at debug — a launch is logged loudly by :func:`wake`. The
    daemon calls this each tick, wrapped so a failure can never take the loop down.
    """
    go, reason = evaluate(store, statuses, now_epoch)
    if not go:
        log.debug("orchestrator auto-launch withheld: %s", reason)
        return None
    log.info("orchestrator auto-launch firing: inbox has work, lease active, none live")
    return wake(repo_dir)
