"""Decisions inbox — the orchestration loop's missing half.

An orchestrator agent is a Claude Code session: it can only act when a human
messages it, or when a background task it started exits. So an agent FINISHING is
structurally invisible to it — on 2026-07-13 an agent was dispatched, finished, and
nothing told the orchestrator; it polled the pane, and the human became the message
bus ("he's done"). This closes that loop: agent/run events are pushed straight into
the orchestrator's session, so it wakes up and acts.

**Push, gated on idle.** An event is delivered with :func:`chela.messenger.send_tmux`
ONLY when the orchestrator's window is ``idle``. Otherwise it queues (durably) and
goes out on the next idle tick. Two rules make that safe:

* ``idle`` is checked STRICTLY — ``not busy`` is not good enough. A ``waiting``
  session is sitting on a permission/question prompt, and pasting into it would
  ANSWER THE GATE with our notification. Only a session that is genuinely idle at
  its prompt is ever written to.
* ⛔ **And ``idle`` is not "the prompt will treat this as prose".** The status
  authority models whether a session is THINKING; it says nothing about the INPUT
  MODE its prompt is in. On 2026-07-15 the orchestrator's idle pane was in ``!``
  bash-input mode, this inbox typed a notification into it, and ``/bin/bash`` RAN
  IT — surviving only because the summary contained ``(rework 1)`` and died on the
  parens. The mode is now read off the TUI and an unsafe one is REFUSED
  (:func:`chela.messenger.refuses_paste`, which holds the event in the queue), and —
  because a refusal is a guess about somebody else's pane, while the text is the
  thing we control — every summary is neutralised (:func:`_event`) so it cannot
  execute in ANY mode. Two independent layers, because the chain is fully
  agent-controlled: an agent writes a PR title, this builds a summary from it, this
  types it at the prompt of the one session with merge authority and a real shell.
* The orchestrator is identified EXPLICITLY (:func:`orchestrator_wid`) — registered
  by the orchestrator itself via ``chela watch``, or pinned with
  ``$CHELA_ORCHESTRATOR_WID``. It is never guessed, so a notification can never land
  in a random agent's session. With no registration the whole feature is inert.

**Watches — why busy→idle alone is unusable.** EVERY agent turn ends busy→idle,
including the orchestrator's own replies to the human, so firing on the raw
transition would spam the orchestrator into a loop. Events are therefore scoped to
work the orchestrator actually DELEGATED: it registers interest when it dispatches
(``chela watch @3 --note "fix the parser"``), and only watched windows can produce
agent events. This also covers ad-hoc ``tmux send-keys`` dispatches — which are how
an orchestrator really works, are not dispatcher runs, and have no run-state event
of their own. The watch is cleared when the completion fires, so one dispatch yields
exactly one "finished".

**A gone window is not a dead agent.** An agent that finishes normally EXITS, and its
tmux window goes away — so "window gone" was reporting every SUCCESSFUL dispatch as
``DIED mid-task``, in the same tick that the run's ``awaiting_review`` event went out.
A vanished window is now corroborated against the runs DB (:func:`_gone_event`) before
any claim is made: settled run → the window was meant to go (silent, watch cleared);
still running with no PR → a genuine death, reported loudly; no run row at all → the
outcome is honestly reported as unknown.

**The loop cannot run away.** Delivering a push makes the orchestrator busy and then
idle — which is precisely the transition that would re-trigger. It can't: the
orchestrator's own window is excluded from the event scan, and :func:`watch` refuses
to watch it, so no busy→idle of the orchestrator is ever an event source. Events
originate only from OTHER windows the orchestrator explicitly asked about, and from
the runs DB.

**An event is a RECORD, not a sentence.** Each event carries a ``kind``, a one-line
``summary`` (what the tmux push renders) and a structured ``payload`` (run id, window,
PR url, task title, timestamps). It used to be a single pre-rendered ``text`` string
built at queue time, which had two consequences, both observed live on 2026-07-13:
the notification was the *entire* TODO item pasted into the orchestrator's window
(``title`` on a run row is the whole ``- [ ]`` line), and nothing downstream could
filter, re-check or re-render it — a string is not a fact. The summary is for the
human/orchestrator; the payload is for the log and the UI that will consume it next.

**`@0` IS AN ADDRESS, NOT AN IDENTITY — so every persisted one carries its tmux EPOCH.**
On 2026-07-14 an OOM killed the tmux server. The fleet came back renumbered (the
orchestrator went ``@0`` → ``@6``) and this file still read ``{"orchestrator": "@0"}``. The
inbox queued five ``run_review`` notifications addressed to a window that no longer existed
and delivered NONE of them — in complete silence, because the idle gate reads
``statuses.get(orch) != IDLE`` and a dead address is simply *absent* from the status map, so
it never opened. No error, no warning, no log line; ``chela doctor`` green 14/14. Five
finished PRs sat unreviewed until a human noticed. Now the orchestrator's address, every
watch and every queued event is stamped with :func:`chela.epoch.current`, an address that
did not come from the running tmux server is **never acted on** (it names a stranger's
window now — a wrong wid is worse than no wid, CMX-48), and being undeliverable is
**LOUD**: an ``ERROR`` every tick, a durable ``inbox_undeliverable`` event, a phone
notification, and a red ``chela doctor`` (``inbox.address``). The orchestrator re-registers
with ``chela watch`` — which is what any dispatch already does.

**A queued event is a claim about the PAST, so it is re-checked at DELIVERY time.**
Delivery is deliberately deferred until the orchestrator is idle, and the world moves
in the meantime: an ``awaiting_review`` event was delivered *after* its PR had already
been reviewed and merged, handing the orchestrator work that was already done. Before
a run event goes out it is re-validated against the CURRENT runs DB
(:func:`stale_reason`) and dropped — loudly, in the log — if the fact it asserts no
longer holds. The check is a lookup in the runs list the tick already fetched, so it
costs no I/O and holds no lock across one.

State (watches, the queue, and the run-status marks) lives in one JSON file under
``$CHELA_DIR`` so a daemon restart neither loses a pending event nor re-fires an old
one. Turn the whole thing off with ``CHELA_INBOX_ENABLED=false``.
"""
from __future__ import annotations

import fcntl
import json
import logging
import os
import re
import time
from contextlib import contextmanager
from pathlib import Path

from chela import agent_manager, discovery, epoch, event_log, messenger, notify, transcripts
from chela import config
from chela.config import INBOX_ENABLED
from chela.tui_text import sanitize_prompt

log = logging.getLogger(__name__)

# Statuses `claude agents --json` reports (see agent_manager.session_status_map).
BUSY, IDLE, WAITING = "busy", "idle", "waiting"

# How many events go out per tick. ONE: a delivery makes the orchestrator busy, and
# a second paste would land mid-thought (or, worse, race its status back to idle).
# The rest of the queue drains on subsequent idle ticks, oldest first.
MAX_DELIVERIES_PER_TICK = 1

# Run states that mean "this window was SUPPOSED to go away" — the dispatcher itself
# kills the window on `task-finished`, and a failed run has already been announced by
# run_events(). A watched window vanishing while its run sits in one of these is
# completion, not death.
#
# `changes_requested` and `needs_human` belong here for exactly the same reason: both are
# reached FROM awaiting_review, whose window the agent already killed on its way out. A
# window that is gone because its agent finished and its PR then failed review is not a
# corpse — reporting it as one is the false-DIED bug wearing a new state's hat.
SETTLED_RUN_STATES = ("awaiting_review", "changes_requested", "needs_human", "done", "failed")

# How long a vanished window's run row gets to settle before we call it a death.
# The window dies a moment BEFORE the row lands: `chela task-finished` flips the run
# to awaiting_review and kills the tmux window, and the daemon can easily sample the
# gone window while the write is still in flight. Deciding on the first sample is what
# made a successful agent get reported as DIED. So the first tick that sees the window
# gone only STAMPS it; the claim is made a tick later, re-reading the run state.
DEATH_CONFIRM_SECONDS = 30

# A run's `title` is the WHOLE tracker line — for this repo, a multi-paragraph brief
# with landmines and references. It is a task body, not a notification: pushing it
# verbatim pasted an essay into the orchestrator's window. The summary carries this
# much of it; the payload carries all of it.
SUMMARY_TITLE_CHARS = 60

_PR_NUMBER_RE = re.compile(r"/pull/(\d+)(?:[/?#]|$)")
# `*` and backticks only. NOT `_` or `~`: a tracker title is full of snake_case
# identifiers and approximations ("~4096 chars", "pr_state"), and stripping those
# characters mangles the words rather than unwrapping emphasis.
_MARKUP_RE = re.compile(r"[*`]+")


def enabled() -> bool:
    return INBOX_ENABLED


def store_path() -> Path:
    # ``config.CHELA_DIR`` per call, not latched at import — see event_log.log_path().
    return Path(os.environ.get("CHELA_INBOX_FILE") or (config.CHELA_DIR / "inbox.json"))


def _empty() -> dict:
    # `orchestrator_epoch` is the tmux server that ISSUED `orchestrator` — the half of the
    # address that used to be missing, and without which `@0` is just a number that means
    # something different after every tmux restart (chela.epoch). `orchestrator_name` is
    # what that window was CALLED: an address that has gone stale cannot be re-resolved
    # (never guess a wid), but the alarm can at least name what it was pointing at.
    # `address_alarm` de-dups the undeliverable alarm — it must be loud, not a per-tick
    # flood of identical rows in the event log.
    return {"orchestrator": None, "orchestrator_epoch": None, "orchestrator_name": None,
            "watches": {}, "queue": [], "runs_seen": {}, "address_alarm": None}


def load() -> dict:
    """Read the durable store. A missing/corrupt file reads as empty, never raises."""
    try:
        data = json.loads(store_path().read_text())
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return _empty()
    if not isinstance(data, dict):
        return _empty()
    base = _empty()
    base.update({k: data[k] for k in base if k in data and data[k] is not None})
    return base


def save(store: dict) -> None:
    """Persist atomically (tmp + rename) so a crash can't truncate the queue."""
    path = store_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(store, indent=2))
    tmp.replace(path)


@contextmanager
def locked_store():
    """Read-modify-write the store under an exclusive lock. THE fix for lost updates.

    The daemon and the CLI mutate the same file CONCURRENTLY — that is the normal case
    here, since you run ``chela watch`` from an agent session while the daemon ticks in
    the background. Plain load→modify→save loses writes: the daemon loads the store,
    spends a second probing statuses, then saves its stale copy back — silently erasing
    a ``chela watch`` that landed in between. Live, that looked like "my first watch
    never activates until I restart the daemon"; the watch was simply gone (reproduced
    deterministically: the CLI reports ok, the store comes back ``{}``).

    An advisory flock over the whole read-modify-write serialises them, so a concurrent
    watch either happens fully before the tick's read or fully after its write. Callers
    must keep the critical section short — do slow work (status probes, the runs query)
    BEFORE taking the lock.
    """
    path = store_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = path.with_suffix(".lock")
    with open(lock_path, "w") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        try:
            store = load()
            yield store
            save(store)
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)


# --- who the orchestrator is (explicit, never guessed) -------------------------

# What the recorded orchestrator address is WORTH, right now. The gate `deliver` opens on,
# and the fact `chela doctor` audits (runtime_truth: inbox.address).
ADDR_OK = "ok"                  # a live claude window, issued by the tmux server now running
ADDR_NONE = "unregistered"      # nobody has called `chela watch` — the inbox is inert by design
ADDR_DANGLING = "dangling"      # ⛔ issued by a DEAD tmux server: `@N` now names a stranger
ADDR_GONE = "gone"              # this epoch's `@N`, but no claude is running in it any more
ADDR_UNSTAMPED = "unstamped"    # recorded before CMX-77, or pinned by env — unverifiable

# The states in which a push must NOT go out. `dangling` and `gone` are the loud ones: there
# IS work to hand over and the address cannot take it. (`unregistered` is not an error — the
# feature is simply not in use; `unstamped` is delivered, warily, see address_state.)
UNDELIVERABLE = (ADDR_DANGLING, ADDR_GONE)


def orchestrator_wid(store: dict | None = None) -> str | None:
    """The ADDRESS we may push into: ``$CHELA_ORCHESTRATOR_WID``, else the registered one.

    An env pin always wins (an operator overriding the fleet's wiring). Otherwise it
    is whatever session registered itself by calling ``chela watch`` — i.e. the
    session that actually delegates work. None means "nobody is listening", and the
    inbox stays completely inert: we never fall back to a guess.

    ⛔ This is the address AS RECORDED — it is not a promise that the window is there, or
    that it is still the window that recorded it. ``@N`` is only meaningful inside the tmux
    server that issued it (:mod:`chela.epoch`), and this one may have been issued by a
    server that is now dead. :func:`address_state` is what says whether it is worth
    anything, and :func:`deliver` refuses to write to one that is not.
    """
    env = (os.environ.get("CHELA_ORCHESTRATOR_WID") or "").strip()
    if env:
        return env
    store = load() if store is None else store
    return store.get("orchestrator")


def orchestrator_epoch(store: dict | None = None) -> str | None:
    """The tmux server that issued the registered address — None if it is unstamped.

    An **env pin carries no epoch**, and cannot: it is a bare ``@N`` an operator exported.
    That is honest rather than convenient — a pin baked into a long-lived service env
    (a PM2 process that outlives the tmux server) is precisely an address that will one day
    name a stranger, and it must not masquerade as verified. It reads as ``unstamped``.
    """
    if (os.environ.get("CHELA_ORCHESTRATOR_WID") or "").strip():
        return None
    store = load() if store is None else store
    return store.get("orchestrator_epoch")


def address_state(store: dict, statuses: dict[str, str],
                  now_epoch: str | None = None) -> tuple[str, str]:
    """Is the recorded orchestrator address worth writing to? — ``(state, why)``.

    Three ways an address rots, and only one of them was ever visible:

    * **dangling** — the tmux server that issued it is gone (an OOM, a reboot, a
      ``kill-server``). The fleet came back renumbered, so ``@0`` either does not exist or,
      worse, belongs to somebody else now. Never written to: a wrong wid is worse than no
      wid (CMX-48) — a review notification pasted into an agent's prompt is an instruction
      that agent will act on.
    * **gone** — this epoch's ``@N``, but nothing is running claude in it any more (the
      orchestrator exited). Nothing to deliver to.
    * **unstamped** — recorded before CMX-77, or pinned via ``$CHELA_ORCHESTRATOR_WID``.
      It cannot be verified either way, so it is still delivered (refusing would break every
      store that predates this, and cry wolf on every operator pin) — but it is reported, and
      one ``chela watch`` from the orchestrator replaces it with an address that CAN be
      verified.

    ``now_epoch`` unknown (no tmux, no server) means nothing can be compared: an unreadable
    owner never accuses a stamp of being stale — it is what ``chela doctor`` reports as
    CANNOT VERIFY, which is the one thing that must never read as green.

    An EMPTY ``statuses`` map is not evidence that the orchestrator is gone: it is what a
    hiccup in ``claude agents --json`` looks like, and alarming on it would cry wolf about
    the whole fleet at once. Liveness is only asserted against a map that saw *something*.
    """
    wid = orchestrator_wid(store)
    if not wid:
        return ADDR_NONE, ("no session has registered as the orchestrator (`chela watch`), "
                           "so the inbox has nobody to push to")
    stamped = orchestrator_epoch(store)
    if epoch.is_dangling(stamped, now_epoch):
        name = store.get("orchestrator_name") or "?"
        return ADDR_DANGLING, (
            f"{wid} was issued by {epoch.describe(stamped)}, and tmux is now running "
            f"{epoch.describe(now_epoch)} — the server RESTARTED and renumbered the fleet. "
            f"That id does not name the orchestrator ({name!r}) any more, and may well name "
            "another agent, so nothing will be written to it. Re-register from the "
            "orchestrator's session: `chela watch` (any dispatch does it for you).")
    if statuses and wid not in statuses:
        return ADDR_GONE, (
            f"tmux has no claude running in {wid} — the session that registered as the "
            "orchestrator is gone. Its queue is intact and will go out to whichever session "
            "registers next (`chela watch`).")
    if not stamped and now_epoch:
        return ADDR_UNSTAMPED, (
            f"{wid} carries no tmux epoch (recorded before CMX-77, or pinned with "
            "$CHELA_ORCHESTRATOR_WID), so chela cannot tell whether it still names the "
            "session that registered it. Re-register with `chela watch` to stamp it.")
    return ADDR_OK, ""


# --- watches: the orchestrator registers interest when it delegates -------------

def watch(wid: str, note: str = "", *, by: str | None = None) -> dict:
    """Register interest in ``wid``: report when it finishes, blocks, or dies.

    ``by`` is the caller's own window (``$CHELA_WID`` via
    :func:`chela.orchestrator.self_wid`) and registers it as THE orchestrator — the
    session that delegates is the session that gets told. Watching your own window is
    refused: that is the self-notify loop, and it is closed here at the source.

    **Both ids are stamped with the tmux epoch they were issued in** (:mod:`chela.epoch`).
    This is the one moment the stamp can be taken honestly — the windows are live and in
    front of us — and it is what lets every later reader tell "the window I meant" from "the
    number that window used to have". It also makes this the RECOVERY path: after a tmux
    restart the orchestrator's next dispatch re-registers a valid address, and the queue that
    piled up while the old one dangled goes out.
    """
    names = discovery.get_windows_by_id()          # slow-ish (tmux) — do it OUTSIDE the lock
    if wid not in names:
        return {"ok": False, "error": f"no such window: {wid}"}
    now = epoch.current()
    with locked_store() as store:                  # ...so a concurrent daemon tick can't
        if by:                                     #    clobber the watch we are writing
            store["orchestrator"] = by
            store["orchestrator_epoch"] = now
            store["orchestrator_name"] = names.get(by)
            store["address_alarm"] = None          # a fresh address: any old alarm is spent
        target = orchestrator_wid(store)
        if target and wid == target:
            return {"ok": False, "error": "refusing to watch the orchestrator's own window"}
        # `since` is the completion evidence line: work the transcript shows AFTER this
        # instant is work this dispatch caused (see agent_events). `name` outlives the
        # window itself and is what links it back to its run row (see run_for_window).
        # `epoch` is what makes the id itself trustworthy a day later.
        store["watches"][wid] = {"note": note.strip(), "since": time.time(),
                                 "name": names[wid], "epoch": now}
    return {"ok": True, "wid": wid, "note": note.strip(), "orchestrator": target,
            "epoch": now}


def register(by: str) -> dict:
    """Register ``by`` as THE orchestrator without watching anything.

    The recovery command (``chela watch`` with no window): after tmux restarts, the stored
    address is dangling and the inbox is holding a queue it refuses to misdeliver. This
    re-stamps it — from the session that runs it, so it is still never a guess.
    """
    names = discovery.get_windows_by_id()
    if by not in names:
        return {"ok": False, "error": f"no such window: {by}"}
    now = epoch.current()
    with locked_store() as store:
        store["orchestrator"] = by
        store["orchestrator_epoch"] = now
        store["orchestrator_name"] = names.get(by)
        store["address_alarm"] = None
        queued = len(store["queue"])
    return {"ok": True, "orchestrator": by, "epoch": now, "queued": queued}


def unwatch(wid: str) -> dict:
    with locked_store() as store:
        existed = store["watches"].pop(wid, None) is not None
    return {"ok": existed, "wid": wid}


def watches() -> dict:
    return load()["watches"]


# --- event generation (pure — no tmux, no send) ---------------------------------

def _line(wid: str, name: str, body: str, note: str = "") -> str:
    """One compact, actionable line. The orchestrator reads it as an instruction.

    The framing punctuation is ``·`` and curly quotes, NOT parentheses and ``"``. Those are
    shell metacharacters, and every summary is neutralised before it is typed at a prompt
    (:func:`_event`), so a frame built from them would just have its own punctuation stripped
    back out — it was ``(name)``, and the live bash-mode execution died on exactly those
    parens. Framing that is already inert renders identically before and after the sanitizer.
    """
    tail = f" — note: “{note}”" if note else ""
    return f"📥 {wid} · {name} {body}{tail}"


def _event(kind: str, summary: str, payload: dict, *, wid: str | None = None,
           clear_watch: bool = False, silent: bool = False,
           watch_key: str | None = None) -> dict:
    """The queued event record: what happened, in one line, plus the facts behind it.

    ``summary`` is the ONLY thing ever pushed into a session — one line, no newlines.
    ``payload`` is the structured record (run id, wid, PR url, full title, timestamps)
    that a log, a filter, a de-dup or a UI can actually work with, and that
    :func:`stale_reason` re-checks at delivery. Keeping the two apart is what stops a
    notification from being an essay and stops a fact from being un-re-checkable.

    ⛔ The summary is built from AGENT-AUTHORED text — a PR title, a tracker line, a CI
    error — and it is TYPED AT A PROMPT. So it is neutralised HERE, at the source, before it
    is ever durable: no shell metacharacters, no control bytes, no mode-switching first
    character (:func:`chela.tui_text.sanitize_prompt`). CMX-79: an inbox summary was executed
    as a shell command by an orchestrator pane sitting in ``!`` bash-input mode; it died on
    the parens in "(rework 1)", and a PR title containing ``$(…)`` would not have. The
    payload keeps the raw title — a record is read, not typed.

    ``wid`` ATTRIBUTES the event (it is the Feed's lane), so it must only ever hold an id
    that names the agent this event is about IN THE CURRENT EPOCH. ``watch_key`` is the
    bookkeeping id instead — the key to retire in ``watches`` — for the one event whose
    subject is precisely that its id no longer names anybody (``watch_epoch_lost``).
    """
    event = {"kind": kind, "summary": sanitize_prompt(summary), "payload": payload,
             "wid": wid, "clear_watch": clear_watch, "ts": time.time()}
    if silent:
        event["silent"] = True
    if watch_key:
        event["watch_key"] = watch_key
    return event


def render(event: dict) -> str:
    """The one line an event renders to. Legacy queues held a pre-rendered ``text``.

    An event queued by an older daemon (before events were records) is still sitting in
    ``inbox.json`` across the upgrade, and must still be deliverable — hence the
    fallback. New events never set ``text``.

    Neutralised AGAIN here, deliberately: ``inbox.json`` survives the upgrade, so the live
    queue already holds summaries built before :func:`_event` sanitized anything — and those
    are exactly the ones written while nothing was watching for ``$(…)``. Sanitizing only at
    queue time would leave the one queue that holds the observed payload as the thing the fix
    misses. :func:`chela.tui_text.sanitize_prompt` is idempotent, so a clean summary is
    unchanged by the second pass.
    """
    return sanitize_prompt(event.get("summary") or event.get("text") or "")


def _short_title(title: str, limit: int = SUMMARY_TITLE_CHARS) -> str:
    """A tracker line, cut down to something you can read in a notification.

    Strips markdown emphasis (the tracker's titles are mostly ``**bold**``), collapses
    the whitespace, and truncates on a word boundary. The full title is in the payload.
    """
    text = " ".join(_MARKUP_RE.sub("", title or "").split())
    if len(text) <= limit:
        return text
    head = text[:limit].rsplit(" ", 1)[0] or text[:limit]
    return head + "…"


def pr_ref(pr_url: str | None) -> str:
    """``PR #47`` when the url looks like a PR, else the url itself (or nothing)."""
    if not pr_url:
        return ""
    m = _PR_NUMBER_RE.search(pr_url)
    return f"PR #{m.group(1)}" if m else pr_url


def did_work_since(wid: str, since: float) -> bool:
    """Did this agent's transcript gain an ASSISTANT turn after ``since``?

    The completion evidence that makes detection independent of the poll rate. The
    busy→idle edge alone cannot see a task shorter than the sampling interval: a 15s
    delegation between two 30s ticks is sampled ``idle, idle``, the daemon never
    observes ``busy``, and the completion is dropped FOREVER. Quick delegations are
    exactly what this feature exists to catch, so "finished" must not depend on having
    caught the window mid-flight.

    An assistant turn written after the watch was registered is proof the agent did
    work for THIS dispatch; combined with the window now being idle, it is finished.
    We require an *assistant* turn specifically: the dispatched prompt itself lands as
    a *user* record, so counting any activity would read "your prompt arrived" as "the
    agent replied". Best-effort — no transcript (or an unreadable one) simply yields
    False, leaving the busy→idle edge as the detector.
    """
    cwd = discovery.get_window_cwd_by_id(wid)
    last = transcripts.last_assistant_activity(cwd)
    return last is not None and last > since


def run_for_window(name: str | None, runs: list[dict]) -> dict | None:
    """The dispatcher run that owns the window called ``name`` (newest first), if any.

    The dispatcher names an agent's window after its branch (``window_name`` on the
    run row), which is the only handle a *gone* window still gives us — its id is
    meaningless once tmux has reaped it. A retry re-uses the name, so we take the
    newest matching row; :func:`chela.dispatcher.list_runs` already sorts DESC.
    An ad-hoc ``tmux send-keys`` dispatch has no row at all → None.
    """
    if not name:
        return None
    for run in runs:
        if run.get("window_name") == name:
            return run
    return None


def _gone_event(wid: str, name: str, note: str, run: dict | None) -> dict | None:
    """What a vanished watched window actually MEANS, given its run row.

    A window disappearing is not evidence of death — an agent that finishes normally
    EXITS, and `chela task-finished` kills the window on purpose. Inferring death from
    the disappearance alone reported every successful dispatch as ``DIED mid-task``,
    while the same tick queued that run's ``awaiting_review`` event: two contradictory
    messages for one run, with the false one being the alarming one.

    So the disappearance is corroborated against the run state:

    * settled run (``awaiting_review`` / ``done`` / ``failed``) or a PR exists →
      the window was *supposed* to go: emit nothing (``run_events`` already announced
      it — a second event here is the self-contradiction) and clear the watch.
    * run still ``claimed`` / ``running`` with no PR → a genuine mid-task death. This
      is the whole point of the watch and it must still shout.
    * no run row (ad-hoc dispatch) → we simply do not know. Say that, rather than
      asserting an outcome we cannot see.
    """
    payload = _window_payload(wid, name, note, run)
    if run is not None and (run.get("status") in SETTLED_RUN_STATES or run.get("pr_url")):
        return _event("completed_gone", _line(wid, name, "completed (window closed)", note),
                      payload, wid=wid, clear_watch=True, silent=True)
    if run is None:
        return _event("gone_unknown",
                      _line(wid, name, "window closed — no run state to confirm the "
                                       "outcome; check before assuming either way.", note),
                      payload, wid=wid, clear_watch=True)
    return _event("died",
                  _line(wid, name, "DIED mid-task (window gone) — the work was not "
                                   "finished.", note),
                  payload, wid=wid, clear_watch=True)


def _window_payload(wid: str, name: str, note: str, run: dict | None = None) -> dict:
    """The facts behind a window event — the run row's identity, not its prose."""
    payload = {"wid": wid, "window_name": name, "note": note}
    if run is not None:
        payload.update({"task_id": run.get("task_id"), "run_status": run.get("status"),
                        "pr_url": run.get("pr_url"), "task_title": run.get("title")})
    return payload


def wid_for_window_name(name: str | None, windows: dict[str, str]) -> str | None:
    """The live window called ``name`` — or None. It is NEVER a guess.

    A run event knows its ``window_name`` (the dispatcher names the agent's window after
    its branch) but was queued with ``wid=None``, so 17 of the log's ``run_review`` rows
    landed in no agent's lane despite naming their agent in the payload. The window is
    still live at the instant the event is queued, so the id is resolvable *then* — which
    is where it must happen: after the window is reaped, nothing can recover it.

    ⛔ Ambiguity resolves to None, not to a plausible pick. Two windows sharing a name
    (a retry racing its predecessor's exit) is exactly the case where a wrong ``wid``
    would attribute an agent's events to a different agent — and CMX-48's lesson is that
    a wrong ``wid`` is worse than no ``wid``: an unattributed event is visibly ownerless,
    a misattributed one is invisibly false.
    """
    if not name:
        return None
    matches = [wid for wid, wname in (windows or {}).items() if wname == name]
    return matches[0] if len(matches) == 1 else None


def run_wid(run: dict, windows: dict[str, str] | None = None,
            now_epoch: str | None = None) -> str | None:
    """The window a run was dispatched into — from the RUN ROW, not from live tmux.

    The row records ``window_id`` at spawn (``dispatcher._spawn``), which is the only
    lossless moment: a dispatched agent ends by calling ``chela task-finished``, which
    KILLS ITS OWN WINDOW, and only *then* does the run reconcile to ``awaiting_review``
    and this event get queued. So by the time :func:`wid_for_window_name` looks, the
    window is already reaped — that was not the edge case, it was every case, and every
    ``run_review`` (the "your agent finished, go review the PR" row — the single most
    important one in the Feed) landed in the ``chela itself`` lane.

    The name lookup remains the FALLBACK: it is still right for a run that predates the
    recorded id, or one whose window is somehow still alive. Neither path guesses — a run
    with no recorded id and no unambiguous live window stays ``None`` and belongs to
    chela itself. ⛔ An id is never inferred from a branch, a worktree, or a reused
    window: tmux recycles ids, and filing a dead agent's work under a *live* agent is the
    worst version of this bug (CMX-48 — a wrong wid is worse than no wid).

    ⛔ **And "tmux recycles ids" is not a figure of speech — it happens wholesale every time
    the server restarts** (CMX-77). A row written before the 2026-07-14 OOM claims ``@3``;
    ``@3`` today is a different agent entirely. So the recorded id is used only when the row
    carries the epoch it was issued in and that epoch is still the one running
    (``window_epoch``, stamped at spawn). Otherwise the id is DROPPED — back to the name
    lookup, which reads live tmux and therefore cannot name a dead window at all — and an
    event that cannot be attributed stays honestly ownerless.
    """
    recorded = (run.get("window_id") or "").strip()
    if re.fullmatch(r"@\d+", recorded) and not epoch.is_dangling(
            run.get("window_epoch"), now_epoch):
        return recorded
    return wid_for_window_name(run.get("window_name"), windows or {})


def _epoch_lost_event(wid: str, meta: dict, stamped: str | None,
                      now_epoch: str | None) -> dict:
    """A watched window whose id was issued by a tmux server that is now DEAD.

    Every status this watch could be evaluated against is a lie: the agent it was registered
    for died with the server, and the ``@N`` it was registered under has been handed out
    again — so ``busy``, ``idle`` and "gone" all now describe a STRANGER. Reading any of them
    would report someone else's work as this dispatch finishing (the false-DIED bug's evil
    twin: a false FINISHED, which the orchestrator would act on).

    So the watch is retired and the truth is reported: the outcome is unknown, and the run
    row is where to look. ``wid=None`` deliberately — the event is not ABOUT the window that
    holds that id today, and attributing it there is exactly the misattribution this whole
    change exists to end. ``watch_key`` carries the stale id for the bookkeeping.
    """
    name = meta.get("name") or wid
    note = meta.get("note", "")
    tail = f' — note: "{note}"' if note else ""
    return _event(
        "watch_epoch_lost",
        f"📥 the tmux SERVER restarted ({epoch.describe(stamped)} → "
        f"{epoch.describe(now_epoch)}): the agent you were watching in {wid} ({name}) died "
        f"with it, and {wid} now belongs to a different window. Outcome UNKNOWN — check its "
        f"run/PR before assuming either way.{tail}",
        {"wid": wid, "window_name": name, "note": note, "epoch": stamped,
         "now_epoch": now_epoch},
        wid=None, clear_watch=True, watch_key=wid)


def agent_events(prev: dict[str, str], cur: dict[str, str], store: dict,
                 runs: list[dict] | None = None,
                 windows: dict[str, str] | None = None,
                 now_epoch: str | None = None) -> list[dict]:
    """Events from agent status transitions, scoped to WATCHED windows only.

    Edge-triggered (mirrors :func:`chela.notify.check_waiting`) so a window that sits
    idle — or sits waiting — across many ticks is announced exactly once.

    Completion is detected TWO ways, because the edge alone is not enough. The
    busy→idle transition is the fast, precise signal when we catch it; but a task that
    starts and finishes BETWEEN two polls is never observed as busy, so it would be
    missed forever. So an idle watched window that has done work since its watch was
    registered (:func:`did_work_since`) is also finished. The evidence path needs no
    baseline at all, which is why it also survives a daemon restart mid-task.

    A window that is GONE is corroborated against ``runs`` (see :func:`_gone_event`)
    rather than being called dead on sight, and only after ``DEATH_CONFIRM_SECONDS``,
    so the run row racing the window's exit cannot produce a false death. This writes
    ``name``/``gone_since`` back onto the watch — the caller persists the store.

    The orchestrator's own window is never a source, so the busy→idle its own reply
    produces (including the reply to one of our own pushes) can never become an event.

    ``windows`` is the live ``{wid: name}`` table; the caller passes the one it already
    fetched (:func:`tick` reads it once, outside the store lock) so a tick costs one
    ``tmux list-windows``, not two.
    """
    orch = orchestrator_wid(store)
    names = windows if windows is not None else discovery.get_windows_by_id()
    runs = runs or []
    now_ts = time.time()
    out: list[dict] = []
    for wid, meta in sorted(store["watches"].items()):
        if wid == orch:
            continue                      # never notify about the orchestrator itself
        meta = meta or {}
        note = meta.get("note", "")
        since = meta.get("since") or 0.0
        was, now = prev.get(wid), cur.get(wid)

        # ⛔ FIRST, before any status is believed: was this id issued by the tmux server
        # that is running now? If not, it names somebody else and every branch below would
        # be reasoning about the wrong window.
        stamped = meta.get("epoch")
        if epoch.is_dangling(stamped, now_epoch):
            out.append(_epoch_lost_event(wid, meta, stamped, now_epoch))
            continue

        if wid not in names:
            # Gone. Remember the name we last saw it under: it is the ONLY link back to
            # the run row (window ids die with the window), and it is why the false
            # death report could only ever say "@6 (@6)".
            name = meta.get("name") or wid
            gone_since = meta.get("gone_since")
            if not gone_since:
                meta["gone_since"] = now_ts   # first sample only stamps; never decides
                store["watches"][wid] = meta
            run = run_for_window(meta.get("name"), runs)
            settled = run is not None and (
                run.get("status") in SETTLED_RUN_STATES or run.get("pr_url"))
            if not settled and now_ts - (gone_since or now_ts) < DEATH_CONFIRM_SECONDS:
                continue                  # let the run row catch up before we accuse
            event = _gone_event(wid, name, note, run)
            if event:
                out.append(event)
            continue

        meta["name"] = names[wid]         # keep the id→name link fresh while it lives
        meta.pop("gone_since", None)      # it came back (or tmux blipped) — not gone
        store["watches"][wid] = meta
        name = names[wid]
        finished_edge = was == BUSY and now == IDLE
        # The not-missed path: idle now, and the transcript proves it worked for us.
        # Gated on `idle` so an agent still mid-task (busy/waiting) is never called done.
        finished_evidence = now == IDLE and did_work_since(wid, since)
        if finished_edge or finished_evidence:
            out.append(_event("finished",
                              _line(wid, name, "finished the task you dispatched — "
                                               "verify + commit.", note),
                              _window_payload(wid, name, note,
                                              run_for_window(name, runs)),
                              wid=wid, clear_watch=True))
            continue
        if was is None:
            continue                      # no baseline — the transition below needs one
        if was != WAITING and now == WAITING:
            # Keep the watch: it is blocked, not done — it still owes you the work.
            out.append(_event("blocked",
                              _line(wid, name, "is BLOCKED on a prompt (permission/"
                                               "question) — answer it.", note),
                              _window_payload(wid, name, note,
                                              run_for_window(name, runs)),
                              wid=wid))
    return out


def run_events(runs: list[dict], seen: dict[str, str],
               windows: dict[str, str] | None = None,
               now_epoch: str | None = None) -> tuple[list[dict], dict[str, str]]:
    """Events from the dispatcher runs DB: → ``awaiting_review``, ``needs_human``, ``failed``.

    Edge-triggered on the run's status, against a DURABLE mark: a run parked in
    awaiting_review must announce once, not once per 30s tick, and not again after a
    daemon restart. Runs are delegated work by definition, so these need no watch.

    The event's ``summary`` is one line — a label, the state, the PR, and a *snippet*
    of the title. The run's ``title`` is the whole tracker line (here: a brief with
    landmines and a verify plan), so putting it in the notification pasted the entire
    task body into the orchestrator's window. It lives in the payload instead.

    **These events are ATTRIBUTED from the run row** (:func:`run_wid`), which recorded
    the window's ``@id`` at spawn. That is what makes attribution survive the window's
    death — and the window is ALWAYS dead by now, because a dispatched agent finishes by
    killing its own window. Resolving the id against live tmux at this point (which is
    what this used to do) never fired. A run with no recorded id and no unambiguous live
    window stays ``wid=None`` and belongs to chela itself: an honest ownerless event, not
    a hole.
    """
    out: list[dict] = []
    fresh: dict[str, str] = {}
    for run in runs:
        task_id, status = run.get("task_id"), run.get("status")
        if not task_id:
            continue
        fresh[task_id] = status
        if seen.get(task_id) == status:
            continue                      # already announced at this status
        wid = run_wid(run, windows, now_epoch)
        title = run.get("title") or ""
        # The branch is the handle a human recognises ("cmx-38"); the id is the handle
        # the dispatcher does. Prefer the branch, fall back to the id.
        label = run.get("branch_name") or task_id
        snippet = _short_title(title)
        payload = {"task_id": task_id, "run_status": status, "title": title,
                   "branch_name": run.get("branch_name"),
                   "window_name": run.get("window_name"),
                   "window_id": run.get("window_id"),
                   "window_epoch": run.get("window_epoch"),
                   "pr_url": run.get("pr_url"),
                   "pr_state": run.get("pr_state"), "attempt": run.get("attempt"),
                   "started_at": run.get("started_at"), "ended_at": run.get("ended_at")}
        if status == "awaiting_review":
            pr = run.get("pr_url")
            ref = f"{pr_ref(pr)} — {pr}" if pr else "no PR link"
            out.append(_event("run_review",
                              f"📥 {label} awaiting review — {ref}"
                              f"{' · ' + snippet if snippet else ''}", payload, wid=wid))
        elif status == "needs_human":
            # The rework loop gave up (CMX-68): the PR was sent back MAX_REWORKS times and
            # still fails review. This is the one run state a human MUST see — the loop is
            # bounded precisely so it surfaces here instead of spinning — so it carries the
            # HISTORY: every verdict this run ever received, not just the last thing said.
            # Nothing has been thrown away: the branch, the worktree and the PR are intact.
            reviews = _reviews(run)
            payload["rework_count"] = run.get("rework_count") or 0
            payload["reviews"] = reviews
            payload["last_error"] = run.get("last_error")
            payload["worktree_path"] = run.get("worktree_path")
            pr = run.get("pr_url")
            ref = f"{pr_ref(pr)} — {pr}" if pr else "no PR link"
            out.append(_event(
                "run_needs_human",
                # No parens, no `(s)`: the summary is sanitized before it is typed at a
                # prompt (_event), and bracket punctuation comes back out mid-word.
                f"📥 {label} NEEDS A HUMAN — reworks: {payload['rework_count']} · verdicts "
                f"on the row: {len(reviews)} · the PR still fails review — {ref}"
                f"{' · ' + snippet if snippet else ''}", payload, wid=wid))
        elif status == "changes_requested":
            # ⛔ NOT a silent state (CMX-68 review). A run sits here waiting for a dispatcher
            # tick to re-spawn it — and if the queue is HELD, the WORKFLOW.md does not parse,
            # or the workflow was dropped from CHELA_DISPATCH_WORKFLOWS, that tick never
            # comes and the run parks here forever. Announcing the edge is what makes the
            # silence audible: the verdict landed, and the loop is supposed to turn. If it
            # doesn't, `chela doctor` says so (runtime_truth._parked_report) — but the
            # orchestrator hears about the state at all only because of this line.
            payload["rework_count"] = run.get("rework_count") or 0
            payload["reviews"] = _reviews(run)
            payload["last_error"] = run.get("last_error")
            payload["worktree_path"] = run.get("worktree_path")
            pr = run.get("pr_url")
            ref = f"{pr_ref(pr)} — {pr}" if pr else "no PR link"
            nxt = (f"rework {payload['rework_count'] + 1}" if not run.get("last_error")
                   else f"RETRY after: {str(run['last_error'])[:60]}")
            out.append(_event(
                "run_changes_requested",
                f"📥 {label} sent back for rework — {nxt} — the next dispatcher tick "
                f"re-spawns it in its own worktree — {ref}"
                f"{' · ' + snippet if snippet else ''}", payload, wid=wid))
        elif status == "failed":
            err = (run.get("last_error") or "").splitlines()
            payload["last_error"] = run.get("last_error")
            out.append(_event("run_failed",
                              f"📥 {label} FAILED{' — ' + err[0][:120] if err else ''}"
                              f"{' · ' + snippet if snippet else ''}", payload, wid=wid))
    return out, fresh


def _reviews(run: dict) -> list[dict]:
    """The run's verdict history (``dispatcher.reviews_of``), tolerantly.

    Parsed here rather than imported so ``run_events`` stays a pure function over plain
    dicts — it is fed a runs snapshot, not a DB. A row written by an older dispatcher has
    no history and reads as an empty list.
    """
    try:
        parsed = json.loads(run.get("review_history") or "[]")
    except (json.JSONDecodeError, TypeError, ValueError):
        return []
    return [r for r in parsed if isinstance(r, dict)] if isinstance(parsed, list) else []


# --- the daemon tick ------------------------------------------------------------

def status_snapshot() -> dict[str, str]:
    """``{wid: busy|idle|waiting}`` for every live window running claude."""
    return agent_manager.status_by_wid()


def stale_reason(event: dict, runs: list[dict]) -> str | None:
    """Why this queued event is no longer TRUE — or None if it still is.

    A queued event is a claim about the past. Delivery is deferred until the
    orchestrator is idle, and the world does not wait: live on 2026-07-13 an
    ``awaiting_review`` event was pushed *after* its PR had been reviewed and merged,
    so the orchestrator was handed an instruction to do work that was already done.
    The event was true when queued and rotted in the queue — which is exactly why the
    check belongs HERE, at delivery, and not only at queue time.

    Only claims that can rot are re-checked, and only against the runs list the tick
    already fetched: no network, no DB read, no I/O of any kind, so this stays safe to
    call inside the store lock. Window events (finished/died/blocked) assert something
    that already happened and are left alone.
    """
    kind = event.get("kind")
    if kind not in ("run_review", "run_failed", "run_needs_human", "run_changes_requested"):
        return None
    task_id = (event.get("payload") or {}).get("task_id")
    if not task_id:
        return None                        # legacy/unstructured event — deliver it
    run = next((r for r in runs if r.get("task_id") == task_id), None)
    if run is None:
        return "run row is gone"
    status, pr_state = run.get("status"), run.get("pr_state")
    if kind == "run_review":
        if status != "awaiting_review":
            # Includes the rework loop's own transition: an `awaiting_review` event that
            # was still queued when the reviewer sent the PR back is a claim about a run
            # that has moved on, and delivering it would ask for a review twice.
            return f"run is now {status!r}, not awaiting_review"
        if pr_state in ("merged", "closed"):
            # The row can lag the PR by a reconcile tick: pr_state is refreshed before
            # awaiting_review → done. A merged PR is not awaiting review either way.
            return f"PR is {pr_state}"
    elif kind == "run_failed" and status != "failed":
        return f"run is now {status!r}, not failed"
    elif kind == "run_needs_human":
        if status != "needs_human":
            return f"run is now {status!r}, not needs_human"
        if pr_state in ("merged", "closed"):
            return f"PR is {pr_state}"      # a human merged it anyway — nothing to escalate
    elif kind == "run_changes_requested":
        # This one rots FAST and by design: the dispatcher re-spawns a sent-back run on the
        # very next tick, so an event that waited for an idle orchestrator usually finds the
        # run already `running` again. That is the loop working, and saying so would be
        # noise. It is delivered only while the run is still waiting.
        if status != "changes_requested":
            return f"run is now {status!r}, not changes_requested"
        if pr_state in ("merged", "closed"):
            return f"PR is {pr_state}"      # merged despite the verdict — the loop is moot
    return None


def _undeliverable(store: dict, state: str, wid: str, why: str,
                   queued: int, alarms: list | None) -> None:
    """An address that cannot take the work SHOUTS. This is CMX-77's whole point.

    The 2026-07-14 outage was not that the address rotted — addresses rot; a tmux server can
    be OOM-killed at any moment. It was that NOTHING SAID SO. Five ``run_review`` events
    queued behind a dead ``@0``, the daemon logged nothing, doctor stayed green, and the
    orchestrator sat waiting for an inbox that had quietly stopped existing. So this fires on
    every surface a human or an agent actually watches:

    * ``ERROR`` in the daemon log — every tick, for as long as it is true: this is not a
      transient, it does not fix itself, and a once-only line scrolls away in a minute;
    * a durable ``inbox_undeliverable`` event — the Feed and the audit trail;
    * a phone push, if notifications are configured — the human who "had to notice" is told;
    * a red ``chela doctor`` (``inbox.address``), which is what any of this is checked with.

    The log line repeats; the event and the push do NOT — they are de-duped on the address
    state, in the store, so an alarm that stays true for a day is one row in the Feed and one
    buzz in a pocket, not 2,880 of each. Both are handed back to the caller (``alarms``)
    rather than sent from here: this runs under the store lock, and a push is an HTTP POST
    with a ten-second timeout — doing it here would hold the lock across the network and
    block the very command that FIXES this (``chela watch``, which takes the same lock).
    """
    log.error("inbox: UNDELIVERABLE (%s) — %d event(s) queued for %s: %s",
              state, queued, wid, why)
    key = f"{state}:{wid}"
    if store.get("address_alarm") == key:
        return                             # same address, same failure: already announced
    store["address_alarm"] = key
    if alarms is None:
        return
    alarms.append({
        "summary": f"📥 THE DECISIONS INBOX CANNOT DELIVER — {queued} event(s) are queued "
                   f"for {wid} and that address is {state}. {why}",
        "payload": {"orchestrator": wid, "state": state, "detail": why, "queued": queued,
                    "epoch": store.get("orchestrator_epoch"),
                    "orchestrator_name": store.get("orchestrator_name")},
    })


def raise_alarms(alarms: list[dict]) -> None:
    """Publish the undeliverable alarms a tick raised — OUTSIDE the store lock.

    The durable record first (it is the one that cannot be missed: it survives a restart and
    lands in the Feed), then the phone. ``notify.send`` swallows its own failures, and
    ``event_log.append`` never raises, so an alarm about a broken inbox cannot itself take the
    daemon down.
    """
    for alarm in alarms:
        event_log.append("inbox_undeliverable", alarm["summary"], alarm["payload"])
        if notify.enabled():
            notify.send(alarm["summary"],
                        title="chela: the decisions inbox is not being delivered")


def deliver(store: dict, statuses: dict[str, str],
            runs: list[dict] | None = None,
            now_epoch: str | None = None,
            alarms: list[dict] | None = None) -> list[dict]:
    """Push queued events into the orchestrator — ONLY if its window is ``idle``.

    The gate is a strict equality against ``idle``. ``waiting`` must never be written
    to: that session is sitting on a permission/question prompt, and our paste would
    be consumed as the ANSWER to that prompt. ``busy`` we leave alone by design (never
    interrupt a session mid-thought) — the event just waits for the next idle tick.

    **And the address itself is checked BEFORE the status is** (:func:`address_state`). That
    order is the CMX-77 fix: ``statuses.get(orch) != IDLE`` is also what a DEAD address looks
    like — a window that no longer exists is simply absent from the status map, which is
    indistinguishable from a busy orchestrator, and reads as "wait for the next tick" forever.
    That silence cost five unreviewed PRs. An address that cannot take the work is now refused
    LOUDLY (:func:`_undeliverable`), and an address from a dead tmux epoch is refused even if
    something IS running under that number now — especially then, because that something is
    another agent, and a review instruction pasted into its prompt is one it will act on.

    A live, idle pane in an unsafe INPUT MODE (``!`` bash, ``#`` memory) is the last unsafe
    state, and it is refused by :func:`chela.messenger.send_tmux` itself — one authority,
    every sender. The refusal reaches us as a failed send, which is exactly the behaviour we
    want: the event stays at the head of the durable queue and goes out on a later tick. It
    is HELD, never dropped — the notification still matters once the pane is prose again.
    CMX-77 says WHICH window may be written to; this says WHAT may be typed into it.

    Every event is re-validated against the CURRENT runs (:func:`stale_reason`) on its
    way out; one that has rotted in the queue is dropped and LOGGED — never silently,
    or a real event lost to a bug becomes undebuggable. Only the event's ``summary``
    is pushed: the payload is the record, not the notification.

    Returns the events actually delivered (each exactly once — a delivered event is
    popped from the durable queue before we return, so no tick can re-send it).
    """
    orch = orchestrator_wid(store)
    if not orch or not store["queue"]:
        return []
    state, why = address_state(store, statuses, now_epoch)
    if state in UNDELIVERABLE:
        _undeliverable(store, state, orch, why, len(store["queue"]), alarms)
        return []
    if state == ADDR_UNSTAMPED:
        log.warning("inbox: %s", why)
    if statuses.get(orch) != IDLE:
        return []
    # A real, idle window at the address: whatever it was alarming about is over, and the
    # next failure — even the same kind — is news again rather than a de-duped repeat.
    store["address_alarm"] = None
    runs = runs or []

    sent: list[dict] = []
    while store["queue"] and len(sent) < MAX_DELIVERIES_PER_TICK:
        event = store["queue"][0]
        stale = stale_reason(event, runs)
        if stale:
            store["queue"].pop(0)
            log.warning("inbox: dropping stale %s (%s) — %s", event.get("kind"),
                        (event.get("payload") or {}).get("task_id") or "?", stale)
            continue                       # a dropped event doesn't spend the tick's slot
        text = render(event)
        if not text:
            store["queue"].pop(0)          # nothing to say — don't wedge the queue on it
            log.warning("inbox: dropping unrenderable %s event", event.get("kind"))
            continue
        if not messenger.send_tmux(orch, text):
            # Includes the unsafe-input-mode refusal: HOLD, never drop. The pane will be
            # back at its prose prompt eventually, and the event is still true.
            log.warning("inbox: delivery of %s to %s refused/failed; holding it queued",
                        event.get("kind"), orch)
            break
        store["queue"].pop(0)
        sent.append(event)
        log.info("inbox: delivered %s -> %s", event["kind"], orch)
    return sent


def tick(prev: dict[str, str], runs: list[dict] | None = None) -> dict[str, str]:
    """One daemon pass: scan for events, queue them, deliver what the gate allows.

    Returns the current status snapshot, to be fed back in as ``prev`` next tick (the
    caller holds it in memory, exactly like ``notify.check_waiting``'s ``seen`` set).
    """
    if not enabled():
        return {}

    # Slow work FIRST, outside the lock: `claude agents --json` (a heavyweight process)
    # and the runs query. Holding the store lock across them is what let a tick clobber
    # a concurrent `chela watch` — the very bug locked_store() exists to close.
    statuses = status_snapshot()
    if runs is None:
        from chela import dispatcher
        runs = dispatcher.list_runs()
    # The live window table, read ONCE and outside the lock (tmux is slow-ish). It is
    # what attributes an event to an agent — both the window events and, since the Feed's
    # lanes, the run events (see wid_for_window_name).
    windows = discovery.get_windows_by_id()
    # The tmux server that is issuing window ids RIGHT NOW — read once, outside the lock,
    # and handed to every reader of a persisted `@N`. It is what tells an id that still
    # names its window from one that is a number tmux has since given to somebody else.
    now_epoch = epoch.current()
    alarms: list[dict] = []                # raised inside the lock, published outside it

    with locked_store() as store:
        events = agent_events(prev, statuses, store, runs, windows=windows,
                              now_epoch=now_epoch)
        r_events, store["runs_seen"] = run_events(runs, store.get("runs_seen", {}),
                                                  windows=windows, now_epoch=now_epoch)
        events += r_events

        for event in events:
            # A `silent` event carries no message — it exists only to retire a watch
            # whose window went away because the work SUCCEEDED. Queueing anything for
            # it would be the self-contradiction (run_events already announced the run).
            if not event.get("silent"):
                store["queue"].append(event)
            watch_key = event.get("watch_key") or event.get("wid")
            if event.get("clear_watch") and watch_key:
                # Interest is satisfied — one dispatch, one completion. (A "blocked"
                # event keeps the watch: the agent still owes you the finish.) `watch_key`
                # rather than `wid` because a watch retired for having a DEAD id must not
                # be attributed to whatever holds that id today (see _epoch_lost_event).
                store["watches"].pop(watch_key, None)
            log.info("inbox: %s %s (%s)", "resolved" if event.get("silent") else "queued",
                     event["kind"], event.get("wid") or "run")

        # `runs` is the snapshot fetched above, outside the lock — re-validating against
        # it is a list scan, so the critical section stays as short as it was.
        deliver(store, statuses, runs, now_epoch=now_epoch, alarms=alarms)

    # The durable record. Written OUTSIDE the store lock — an append is another file's
    # I/O, and locked_store()'s one rule is that nothing slow happens inside it. EVERY
    # event is logged, including the `silent` ones (a watch retired because the work
    # succeeded is a fact worth having): the queue is what the orchestrator is TOLD, the
    # log is what HAPPENED, and conflating the two is what left the inbox with no
    # history to reconcile against. `event_log.append` never raises, so this cannot take
    # the inbox down with it.
    for event in events:
        event_log.from_inbox(event)
    # ...and the same rule for the alarm: an inbox that cannot deliver is the loudest thing
    # this module has to say, and saying it costs a file append and (if configured) an HTTP
    # POST with a ten-second timeout. Neither belongs inside the lock that `chela watch` —
    # the command that FIXES a dangling address — has to take.
    raise_alarms(alarms)
    return statuses
