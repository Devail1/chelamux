"""Auto-topics — populate the binding registry from the live tmux fleet.

Slice A gave us N↔N routing over a persisted :class:`~chela.telegram.bindings.
BindingRegistry`; this is the "magic" that *fills* that registry automatically so
a human never seeds bindings by hand. A window-watch reconcile loop in the
``chela telegram`` daemon diffs the live agent windows against the registry each
tick and:

* **provisions** a Telegram forum topic for every agent (Claude) window that has
  no binding yet — :meth:`TopicManager.create_topic` (``createForumTopic``), then
  ``registry.bind`` + ``save``;
* **reaps** a window that has died — :meth:`TopicManager.close_topic`
  (``closeForumTopic`` — *archive*, never delete) + ``registry.unbind`` + ``save``.

A third path — the human **closing a topic** from Telegram — is handled by
:class:`TopicClosedHandler`: it ``unbind``s only and deliberately does **NOT**
kill the agent (Liav's default, differing from ccbot on purpose).

The whole thing is **idempotent**: it matches by ``window_id`` (never by name, so a
window rename can't orphan its topic) and skips any window that already has a
binding, so it never double-creates a topic. :func:`reconcile` is **pure** — it
takes the live-window snapshot and a topic API as arguments and touches no live
tmux/Telegram itself — so the whole loop is unit-testable against a stub API and a
fake window set with no live Telegram calls. Only :func:`live_agent_windows`
touches tmux (via :mod:`chela.discovery` + :mod:`chela.agent_manager`).

The Bot API transport is reused verbatim from :mod:`chela.telegram.relay` (direct
stdlib ``urllib`` — no new dependency), so ``createForumTopic`` /
``closeForumTopic`` go out the same wire as ``sendMessage``.

**Every write here is edge-triggered — a no-op write is a rate-limit leak.** The
loop ticks every few seconds forever, and it shares one Telegram rate limit with
the *real* traffic (agent messages, permission gates). So a topic is only created
when it has no binding, only renamed when its name actually **changed** (diffed
against :meth:`~chela.telegram.bindings.BindingRegistry.topic_name`, the cache of
what we last told Telegram), and only closed when its window died: at idle the
whole loop makes **zero** API calls. Decoration must never spend the budget that
real messages need — the same rule the status line follows by skipping an unchanged
edit and by opting out of the 429 retry loop (CMX-43). This module opts out too, by
construction: it calls the raw transport rather than
:meth:`~chela.telegram.relay.BotSender._call`, so a flood-controlled rename fails
fast and is simply retried on the next tick instead of sleeping in the daemon.

**Human prerequisite (landmine):** ``createForumTopic`` requires the bot to be a
forum admin with the *Manage Topics* permission. That is a one-time manual setup,
never something the test suite exercises (tests inject a stub API).

Adapted from six-ddc/ccbot (https://github.com/six-ddc/ccbot), MIT — its
``topic_closed_handler`` / ``_create_and_bind_window``, reworked onto chela's
discovery layer and Liav's unbind-not-kill default. See NOTICE for attribution.
"""
from __future__ import annotations

import logging
import os
from collections import Counter
from typing import Callable

from chela import epoch
from chela.agent_manager import is_generic_name
from chela.telegram.relay import Transport, _urllib_transport

log = logging.getLogger(__name__)


def _is_not_modified(resp: dict) -> bool:
    """True for Telegram's ``TOPIC_NOT_MODIFIED`` — "you wrote what was already there".

    A 400 whose description carries this marker means the edit was a no-op: the
    topic already holds the exact name/icon we sent. It is the API confirming the
    desired state, so the reconcile loop treats it as an accepted write (and caches
    the name) instead of retrying the same pointless call every tick.
    """
    if resp.get("ok"):
        return False
    return "TOPIC_NOT_MODIFIED" in str(resp.get("description") or "")


def topic_name_for(cwd: str | None, window_name: str) -> str:
    """The topic's name: a DELIBERATE window name wins, else the agent's project.

    The tmux window name is the single source of truth for an agent's display name,
    so a name a human chose — via the dashboard rename or ``tmux rename-window`` —
    is what the topic is called, and the bridge keeps the two bound as it changes.

    Only when the window carries no intentional name (a generic ``shell-1``, or
    tmux's command-follow ``claude``; see
    :func:`chela.agent_manager.is_generic_name`) do we fall back to naming the topic
    after the *project*: ``basename(cwd)`` — e.g. ``/home/liav/projects/chelamux`` →
    ``chelamux`` — so an auto-created topic reads as the work rather than as
    ``shell-1``. That falls back to ``window_name`` in turn when the cwd carries no
    project signal: empty/``None``, the filesystem root ``/``, or the user's home
    directory (a ``~``-rooted session would otherwise collapse to the useless
    login-name basename, e.g. ``liavedunix``).

    Pure — cwd is passed in (in production resolved via
    :func:`chela.discovery.get_window_cwd_by_id`), so it unit-tests with no live
    tmux/Telegram.
    """
    if window_name and not is_generic_name(window_name):
        return window_name
    if not cwd:
        return window_name
    normalized = os.path.normpath(cwd)
    home = os.path.normpath(os.path.expanduser("~"))
    if normalized == home or normalized == os.sep:
        return window_name
    return os.path.basename(normalized) or window_name


def disambiguate_topic_names(names: dict[str, str]) -> dict[str, str]:
    """Break ties when two windows resolve to the identical topic name.

    :func:`topic_name_for` is deliberately per-window pure — it has no idea a
    sibling window exists. Two agents parked in the SAME cwd with generic names
    (tmux's command-follow ``claude``, or a bare ``shell-N``) both fall back to
    the identical project basename, so two windows in ``~/projects/chelamux``
    would each get a topic titled "chelamux" — same title, no way to tell which
    is which from the Telegram topic list (CMX-147).

    Every name shared by more than one window gets its window id appended —
    ``"chelamux (@3)"`` / ``"chelamux (@7)"`` — so the fallback still reads as
    the project but each topic is tell-apart-able. ALL of the colliding windows
    get the suffix, not just the second one seen, so there is no "plain" one
    that reads as more canonical than its sibling — and the result never
    depends on dict iteration order, since collisions are decided by counting
    first.
    """
    counts = Counter(names.values())
    return {
        wid: f"{name} ({wid})" if counts[name] > 1 else name
        for wid, name in names.items()
    }


class TopicManager:
    """Create and close Telegram forum topics via the direct Bot API.

    The topic-lifecycle counterpart to :class:`~chela.telegram.relay.BotSender`:
    same injectable ``transport`` (``transport(method, fields) -> resp``), so the
    reconcile loop can be driven against a stub that returns canned thread ids
    with no live Telegram calls. In production the transport is
    :func:`chela.telegram.relay._urllib_transport` — the same stdlib ``urllib``
    wire as the outbound relay, so no new dependency is pulled in.
    """

    def __init__(self, token: str, chat_id: str | int, *, transport: Transport | None = None):
        self._chat_id = str(chat_id)
        self._transport = transport or _urllib_transport(token)

    def create_topic(self, name: str) -> str | None:
        """Create a forum topic named ``name``; return its ``message_thread_id``.

        Returns the new thread id as ``str`` (registry ids are compared as str),
        or ``None`` if Telegram rejected the call (e.g. the bot lacks the
        *Manage Topics* permission) — the caller leaves the window unbound and
        retries next tick rather than crashing the daemon.
        """
        resp = self._transport("createForumTopic", {"chat_id": self._chat_id, "name": name})
        if not resp.get("ok"):
            log.warning("createForumTopic(%s) failed: %s", name, resp.get("description", resp))
            return None
        thread = (resp.get("result") or {}).get("message_thread_id")
        if thread is None:
            log.warning("createForumTopic(%s) returned no message_thread_id: %s", name, resp)
            return None
        return str(thread)

    def rename_topic(self, thread_id: str | int, name: str) -> bool:
        """Rename forum topic ``thread_id`` to ``name``. True if the topic now has it.

        The propagation half of a window rename: ``editForumTopic`` with only ``name``
        set leaves the topic's icon untouched. A failure (missing *Manage Topics*
        permission, deleted topic) is logged and swallowed — the caller then leaves
        the cached name unset, so the next tick simply retries rather than wedging
        the daemon over a cosmetic rename.

        **``TOPIC_NOT_MODIFIED`` is a success, not a failure (landmine).** Telegram
        answers it when the topic *already carries* the name we just wrote — which is
        proof the desired state holds, so we return ``True`` and let the caller cache
        the name. Reading it as a failure is how this leaked a rename per bound topic
        per tick, forever (a binding written before the name cache existed reads as
        "unsynced" → rewrite → ``TOPIC_NOT_MODIFIED`` → still unsynced → …), and those
        no-op writes earned real 429s that then delayed *actual* agent messages.
        """
        resp = self._transport(
            "editForumTopic",
            {"chat_id": self._chat_id, "message_thread_id": thread_id, "name": name},
        )
        if resp.get("ok"):
            return True
        if _is_not_modified(resp):
            log.debug("editForumTopic(%s -> %s): already named that; caching", thread_id, name)
            return True
        log.warning("editForumTopic(%s -> %s) failed: %s",
                    thread_id, name, resp.get("description", resp))
        return False

    def close_topic(self, thread_id: str | int) -> bool:
        """Close (archive, not delete) the forum topic ``thread_id``.

        Returns ``True`` if Telegram accepted the close. A failure is logged and
        swallowed — the binding is still dropped, since the window is gone either
        way; a stale-open topic is a lesser evil than a wedged reconcile loop.
        """
        resp = self._transport(
            "closeForumTopic", {"chat_id": self._chat_id, "message_thread_id": thread_id}
        )
        if not resp.get("ok"):
            log.warning("closeForumTopic(%s) failed: %s", thread_id, resp.get("description", resp))
            return False
        return True


def reconcile_bindings(
    registry,
    live_windows: dict[str, str],
    agent_ids,
    topic_api,
    cwd_for: Callable[[str], str | None] | None = None,
    dispatched: set[str] | frozenset[str] | None = None,
    gate_for: Callable[[str], object | None] | None = None,
    bind_dispatched: bool = False,
    now_epoch: str | None = None,
) -> bool:
    """Diff the registry against the live fleet; provision + reap. Return changed.

    Pure (no live tmux/Telegram/sqlite of its own) so it unit-tests against stubs:

    * ``registry``    — a :class:`~chela.telegram.bindings.BindingRegistry`.
    * ``live_windows``— ``{window_id: display_name}`` of *every* live window.
    * ``agent_ids``   — the subset of live window ids that are agent (Claude)
      windows; only these get topics (shells/servers are skipped).
    * ``topic_api``   — a :class:`TopicManager` (or stub) exposing
      ``create_topic(name) -> thread_id | None`` and ``close_topic(thread_id)``.
    * ``cwd_for``     — optional ``window_id -> cwd`` resolver (in production
      :func:`chela.discovery.get_window_cwd_by_id`); injected so this stays pure.
      When given, a provisioned topic is named after the agent's *project* (its cwd
      basename) via :func:`topic_name_for` instead of the raw tmux window name.
      Two windows sharing a cwd and both carrying generic names collide on that
      same basename; :func:`disambiguate_topic_names` appends each one's window id
      before anything is created or renamed, so they read as distinct topics.
    * ``dispatched``  — the window ids the DISPATCHER owns, read off the ``runs``
      table (:func:`dispatched_window_ids`), not guessed from a window name.
    * ``gate_for``    — ``window_id -> blocked-on-a-human evidence | None`` probe (in
      production :func:`blocked_on_human`); what makes a dispatched agent's binding LAZY
      (below). ⛔ It must cover **permission gates**, which live only on the pane — see
      the warning below.
    * ``bind_dispatched`` — ``True`` restores the old behaviour: a dispatched agent
      gets a topic like any other (``CHELA_TELEGRAM_BIND_DISPATCHED``).
    * ``now_epoch``   — the tmux server issuing window ids right now
      (:func:`chela.epoch.current`; injected, so this stays pure). A binding is STAMPED with
      it, and one stamped with a **different** epoch is reaped like a dead window — because
      that is what it is. tmux renumbers from ``@0`` when its server restarts (an OOM took
      ours on 2026-07-14), so a binding that outlives its server does not merely go stale: it
      keeps relaying, from whatever agent inherited that number, into a topic a human opened
      for a different one — and routes their replies back into that stranger's prompt. A
      binding with no stamp (a file written before CMX-77) is ADOPTED into the current epoch
      once its window is seen live: chela cannot tell such a file from one written under this
      server, and saying so is the honest limit — from the next restart on, it is verifiable.

    **Provision** every agent window with no binding: create a topic named after
    the agent's project (:func:`topic_name_for`, falling back to the window name)
    and ``bind`` the returned thread id. Skipping already-bound windows (matched by
    id) is what makes this idempotent — a restart or a window rename never
    double-creates. A ``create_topic`` that returns ``None`` leaves the window
    unbound to retry next tick. **Reap** every *bound* window that is no longer
    live: ``close_topic`` its thread, then ``unbind``. Returns ``True`` if any
    binding changed, so the caller knows to ``registry.save()``.

    **A dispatched agent is bound LAZILY — only once it BLOCKS (the default).** The
    forum is a human's inbox, and a fleet of short-lived worktree workers each
    creating a topic on spawn and archiving it on exit turns that inbox into a
    changelog. So a window the dispatcher owns gets **no topic while it is working**,
    and one **the moment it blocks on a human** (``gate_for`` — in production
    :func:`blocked_on_human`). The result is the feature, not a compromise: **the forum
    shows only agents that want a human**, and the phone-gate surface — an agent blocking
    reaches Liav with zero keypresses — is fully preserved for exactly the agents most
    likely to need it.

    ⛔ **``gate_for`` MUST see a permission gate, not just an interactive tool call.**
    :func:`~chela.telegram.hookgate.pending_gate` alone is **not** that probe, and
    believing it was is the bug this contract exists to prevent: it only reports
    :data:`~chela.telegram.hookgate.INTERACTIVE_TOOLS` (``AskUserQuestion`` /
    ``ExitPlanMode``). A blocked **Bash/Edit permission prompt** — by far the likeliest
    thing to stop a worktree agent — is *never in the transcript at all* and is visible
    **only on the pane**. Probe with pending_gate alone and a dispatched agent that hits
    one is never bound, so its pane is never polled by
    :class:`~chela.telegram.gatewatch.PermissionGateWatcher` (which only scrapes *bound*
    windows) and it blocks **forever, silently**. Hence :func:`blocked_on_human`: the hook
    payload **or** the pane.

    ⚠️ **The lazy binding is dropped when the WINDOW exits, not when the gate closes.**
    Un-binding on gate-resolve would archive the topic mid-conversation and then
    ``createForumTopic`` a *brand-new* one on the agent's next gate — topic churn per
    gate, which is the very disease this fixes. Once an agent has asked for a human it
    keeps its one topic (so the human can answer, follow up, and read the reply) until
    it dies, and the normal reap archives it then.

    ⛔ A binding is a **VIEW**, not the agent's identity: an unbound dispatched window
    is still on the Wall, still in the decisions inbox, still in the event log.
    """
    changed = False
    dispatched = set(dispatched or ())

    def _wants_topic(wid: str) -> bool:
        """A dispatched agent earns a topic only when it is BLOCKED on a human."""
        if bind_dispatched or wid not in dispatched:
            return True
        if gate_for is None:
            return False
        try:
            return gate_for(wid) is not None
        except Exception:  # noqa: BLE001 — a gate probe must never wedge the loop
            log.debug("auto-topics: gate probe failed for %s", wid, exc_info=True)
            return False

    # Reap FIRST: a bound window that is no longer live is dead — archive its topic and drop
    # the binding. So is one bound in a PREVIOUS tmux epoch, even though something answers to
    # its id today: the agent that owned that topic died with the server, and the window
    # wearing its number now is a stranger (CMX-77). It runs before provisioning precisely so
    # that stranger gets its OWN topic on this same tick, rather than inheriting a dead
    # agent's conversation for a tick first. Snapshot the window list (we mutate it).
    for wid in registry.windows():
        dangling = epoch.is_dangling(registry.epoch_for(wid), now_epoch)
        if wid in live_windows and not dangling:
            if now_epoch and registry.stamp(wid, now_epoch):
                changed = True             # adopt a pre-CMX-77 binding into this epoch
            continue
        thread = registry.thread_for_window(wid)
        if thread is not None:
            topic_api.close_topic(thread)
        registry.unbind(wid)
        why = "the tmux server restarted; its id was reissued" if dangling else "gone"
        log.info("auto-topics: window %s %s — closed topic %s, unbound", wid, why, thread)
        changed = True

    # Names first, for EVERY current agent window (bound or not) — so a collision
    # between two windows sharing a cwd + generic name (CMX-147) is visible before
    # anything is created or renamed, and so a topic bound in an earlier, still-
    # unambiguous tick gets the disambiguating suffix retroactively once a sibling
    # shows up (the rename loop below picks that up like any other name change).
    raw_names = {
        wid: topic_name_for(cwd_for(wid) if cwd_for is not None else None,
                             live_windows.get(wid, wid))
        for wid in agent_ids
    }
    desired_names = disambiguate_topic_names(raw_names)

    # Provision: an agent window with no binding gets a fresh topic. Idempotent —
    # a window that already has a binding (by id) is skipped, so no double-create.
    for wid in agent_ids:
        if registry.thread_for_window(wid) is not None:
            continue
        if not _wants_topic(wid):
            continue  # dispatcher-owned and working away quietly — not the forum's problem
        name = desired_names[wid]
        thread = topic_api.create_topic(name)
        if thread is None:
            continue  # create failed (perms?); leave unbound and retry next tick
        registry.bind(wid, thread, now_epoch)
        registry.set_topic_name(wid, name)
        log.info("auto-topics: created topic %s for %s (%s)", thread, wid, name)
        changed = True

    # Rename: keep a bound topic's name tied to its window's. A rename lands in tmux
    # (the source of truth) and reaches Telegram here, on the next tick, so the two
    # stay bound instead of drifting apart the moment anyone renames anything. We
    # diff against the name we last gave Telegram, so a steady-state tick makes no
    # API call at all; an unknown name (a binding from before this existed, or a
    # rename Telegram rejected) resyncs once.
    for wid in agent_ids:
        thread = registry.thread_for_window(wid)
        if thread is None:
            continue
        desired = desired_names[wid]
        if registry.topic_name(wid) == desired:
            continue
        if not topic_api.rename_topic(thread, desired):
            continue  # perms/deleted topic; leave it unsynced and retry next tick
        registry.set_topic_name(wid, desired)
        log.info("auto-topics: renamed topic %s for %s -> %s", thread, wid, desired)
        changed = True

    return changed


def blocked_on_human(wid: str, *, gate=None, capture=None, detect=None):
    """Is this window BLOCKED on a human right now? The hook log **OR** the pane.

    The production ``gate_for`` probe (:func:`reconcile_bindings`), and it is two sources
    on purpose, because **neither one alone can see every gate**:

    * the **hook log** (:func:`~chela.telegram.hookgate.pending_gate`) sees an unresolved
      ``AskUserQuestion`` / ``ExitPlanMode`` — the only tools it tracks
      (:data:`~chela.telegram.hookgate.INTERACTIVE_TOOLS`);
    * the **pane** (:func:`~chela.telegram.panescan.detect_dialog`) sees *any* dialog on
      screen, whatever its shape.

    ⛔ **The pane half is not a nicety — it is the whole point.** A **permission gate** on
    a Bash/Edit (the likeliest thing to stop a worktree agent: one non-allowlisted command
    and it sits there) emits a ``PreToolUse`` for a tool ``pending_gate`` does not track,
    so the log says *nothing is pending* while the agent is frozen. The gate exists
    **only** as pixels. Probe with the log alone and that agent is never bound → its pane
    is never scraped by :class:`~chela.telegram.gatewatch.PermissionGateWatcher` (which
    polls *bound* windows only) → **nobody is ever told, and it blocks forever.** The pane
    covers the second door too: ``pending_gate`` is ``boot_id``-scoped, so a gate raised
    before a daemon restart, or by an agent started with no hooks plugin at all, is
    likewise invisible in the log and visible only here.

    :func:`~chela.telegram.panescan.detect_dialog` is the right pane probe precisely
    because it **parses nothing** — a top marker, a bottom marker, and the region between
    them — so a dialog shape no semantic scraper was measured against still reads as "this
    agent is waiting on a human", which is the only question being asked here.

    Each source is guarded separately: a failing hook read must not cost us the pane (and
    vice versa), because either one alone still catches gates the other cannot see. Both
    failing returns ``None`` — no topic this tick, retried the next.

    One tmux capture per **unbound dispatched** window per tick, and that set is small
    (the fleet's concurrency cap) and shrinks to nothing the moment a window binds.
    """
    from chela.messenger import capture_pane
    from chela.telegram.hookgate import pending_gate
    from chela.telegram.panescan import detect_dialog

    gate = pending_gate if gate is None else gate
    capture = capture_pane if capture is None else capture
    detect = detect_dialog if detect is None else detect

    try:
        hooked = gate(wid)
        if hooked is not None:
            return hooked
    except Exception:  # noqa: BLE001 — a log hiccup must not cost us the pane probe
        log.debug("auto-topics: hook-gate probe failed for %s", wid, exc_info=True)

    try:
        return detect(capture(wid))
    except Exception:  # noqa: BLE001 — nor a tmux hiccup the whole reconcile
        log.debug("auto-topics: pane gate probe failed for %s", wid, exc_info=True)
        return None


def dispatched_window_ids(runs: list[dict] | None = None,
                          live_windows: dict[str, str] | None = None,
                          now_epoch: str | None = None) -> set[str]:
    """The windows the DISPATCHER owns — read off the ``runs`` table.

    The run row **owns** the ``window_id`` of every window the dispatcher spawned
    (recorded at spawn — the only lossless moment; see
    :func:`chela.inbox.run_wid`). That row is the *fact*; the window's name is only a
    *label*. ⛔ So this never regexes ``cmx-\\d+`` out of a window name: a human can
    rename a window, and a human window can be *called* anything.

    **Two kinds of row qualify, and the second one is a bug fix.**

    * **In flight** (:data:`chela.dispatcher.ACTIVE_STATUSES`) — its window is supposed to
      exist *now*, so its recorded id is honoured unconditionally.
    * **Any other status whose window is STILL ALIVE under the name the row recorded.**
      A run does not stop being dispatcher-owned the instant its status changes:
      :func:`chela.dispatcher.mark_awaiting_review` commits ``awaiting_review`` and only
      *then* kills the window, and a ``failed`` / ``needs_human`` run's window can linger
      indefinitely. In that gap the window is live and no longer "in flight", so scoping to
      ACTIVE alone makes it look like a *human's* window — and the reconcile eagerly
      creates it a topic that the reap archives seconds later. That is exactly the churn
      this whole feature exists to kill, arriving through the back door.

    The **recorded name** is what makes the second case safe, and it is not a name *guess*:
    it is the row's own ``window_name``, compared against what tmux currently calls that
    id. tmux hands out ``@N`` ids afresh after a server restart, so a finished run's
    recorded id can be a **human's** window in this boot — and honouring that blindly would
    silently strip the orchestrator of its topic. A recycled id is a window with a
    *different* name, so it fails this check and stays a human's window. (An in-flight row
    is exempt because a human is free to *rename* a live worker's window without
    disowning it.)

    ⛔ **``now_epoch`` is what makes the FIRST case safe, and it was the hole** (CMX-77). An
    in-flight row honoured its recorded id unconditionally — and a tmux restart leaves rows
    reading ``running`` whose agents died with the server, so their ids were handed to
    somebody else. That is a *human's* window being silently disowned by a corpse's row, and
    the name check could never catch it because an in-flight row skips it by design. A row
    whose ``window_epoch`` is not the epoch running now is dropped outright: whatever holds
    that id today, it is not that run's agent. An unstamped row (written before CMX-77)
    keeps the old behaviour — unverifiable is not the same as wrong.

    ``live_windows`` (``{window_id: name}``, the reconcile's own live fleet) is what that
    check reads; omit it and only the in-flight rows qualify. ``runs`` is injectable so
    this unit-tests with no sqlite; in production it is :func:`chela.dispatcher.list_runs`.
    A failure to read the DB returns an empty set — i.e. "nothing is dispatched", the
    pre-CMX-73 behaviour — rather than wedging the reconcile loop over a locked database.

    🤫 **A THIRD kind of row-owned window: the judge** (CMX-97). ``_spawn_judge`` launches
    it with ``record_window=False`` — deliberately, because ``window_id`` above is the
    RUN's own window, and a short-lived judge must never be stamped over it — so the judge
    would otherwise be invisible here and read as a *human's* window: a Telegram topic
    nobody should message, and a full-size pop on the Wall instead of docking minimized
    like every other dispatcher-owned worker. It gets its own ``judge_window_id`` /
    ``judge_window_epoch`` pair (stamped by ``_spawn_judge``, same epoch-safety shape as
    the row's own) and is honoured by the same two rules: in flight
    (``judge_state == judge.J_RUNNING``) unconditionally, or settled-but-not-yet-reaped —
    ``judge._cleanup`` kills the window LAST, after the verdict is already written — proven
    by the deterministic ``judge.judge_window_name(branch)`` the same way the row's own
    ``window_name`` proves it above.
    """
    from chela import dispatcher
    from chela import judge as judge_mod

    try:
        rows = dispatcher.list_runs() if runs is None else runs
    except Exception:  # noqa: BLE001 — a DB hiccup must never stop the bridge reconciling
        log.debug("auto-topics: could not read runs for dispatched windows", exc_info=True)
        return set()

    live = live_windows or {}
    owned: set[str] = set()
    for row in rows:
        wid = str(row.get("window_id") or "").strip()
        if wid and not epoch.is_dangling(row.get("window_epoch"), now_epoch):
            if row.get("status") in dispatcher.ACTIVE_STATUSES:
                owned.add(wid)
            else:
                # Settled/parked, but its window may not be reaped yet. Only the row's
                # OWN recorded name can prove the live @N is still that window and not a
                # recycled id.
                name = str(row.get("window_name") or "").strip()
                if name and live.get(wid) == name:
                    owned.add(wid)

        jwid = str(row.get("judge_window_id") or "").strip()
        if not jwid or epoch.is_dangling(row.get("judge_window_epoch"), now_epoch):
            continue
        if row.get("judge_state") == judge_mod.J_RUNNING:
            owned.add(jwid)
            continue
        expected = judge_mod.judge_window_name(str(row.get("branch_name") or ""))
        if expected and live.get(jwid) == expected:
            owned.add(jwid)
    return owned


def live_agent_windows() -> tuple[dict[str, str], set[str]]:
    """``(live_windows, agent_ids)`` from live tmux — the daemon's reconcile input.

    The only tmux-touching part of this module (kept out of :func:`reconcile` so
    that stays pure). ``live_windows`` is ``{window_id: display_name}`` for every
    live window; ``agent_ids`` is the subset classified as a Claude session by
    :func:`chela.agent_manager.window_type` (``"claude"`` wins over shell/server)
    — those, and only those, get topics.
    """
    from chela import agent_manager, discovery

    live = discovery.get_windows_by_id()
    agents = {wid for wid in live if agent_manager.window_type(wid) == "claude"}
    return live, agents


class TopicClosedHandler:
    """Unbind a window when a human closes its Telegram topic — *no kill*.

    Wired to PTB's ``StatusUpdate.FORUM_TOPIC_CLOSED`` (see
    :func:`chela.telegram.inbound.build_application`). On a topic-closed service
    message it looks up the window bound to that thread and ``unbind``s it — and
    **deliberately does not kill the agent** (Liav's default; ccbot kills the
    window here, we don't). ``on_change`` (typically ``registry.save``) fires only
    after an actual unbind so the drop is persisted. Unknown/unbound threads are a
    no-op. Pure — driven directly in tests with a thread id, no PTB needed.
    """

    def __init__(self, registry, on_change: Callable[[], None] | None = None):
        self._registry = registry
        self._on_change = on_change

    def handle(self, thread_id: str | int | None) -> bool:
        """Unbind the window bound to ``thread_id``. Return True if one was."""
        window_id = self._registry.window_for_thread(thread_id)
        if window_id is None:
            log.debug("topic-closed for unbound thread %s; ignoring", thread_id)
            return False
        self._registry.unbind(window_id)
        log.info("topic %s closed — unbound %s (agent left running)", thread_id, window_id)
        if self._on_change is not None:
            self._on_change()
        return True
