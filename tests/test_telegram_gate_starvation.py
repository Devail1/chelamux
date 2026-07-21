"""The two ways a gate that rendered on the pane never reached Telegram (CMX-74).

RECONSTRUCTED — not reproduced — from the 2026-07-14 artifacts: the orchestrator window
raised two ``AskUserQuestion`` selectors, both rendered in its pane, and **neither**
arrived on the phone. The event log dates them exactly — ``pre_tool_use`` →
``post_tool_use`` 5s apart for the first, 45s for the second — and the daemon log shows a
Telegram flood-control storm (``Too Many Requests: retry after 18``…) bracketing both,
while a control gate raised in a 429-free stretch (same window, same boot, same tool) DID
arrive. Nobody watched a gate vanish through a flood-controlled daemon in real time; the
stall across each gate's life is inferred, and the storm is the only variable that
differs. Two defects, one storm:

  1. **Starvation.** The pane poll shared its thread with the transcript relay, whose
     sends *sleep* through flood control (:data:`~chela.telegram.relay._MAX_RETRY_AFTER`
     × :data:`~chela.telegram.relay._MAX_SEND_TRIES` — up to a minute and a half per
     payload). A gate is a live thing that exists for seconds; a relay backlog is a
     network-blocking pump that can stall for minutes. Behind one, the other is never
     read. That is why the *chattiest* window loses its gates: the burst it just wrote
     is what earns the 429 that hides the question it is about to ask.
  2. **A dropped post recorded as delivered.** :meth:`PermissionGateWatcher._sync` is
     edge-triggered on a signature. It wrote that signature even when every ``post``
     came back ``None`` (flood control having exhausted its retries), so the gate was
     marked relayed while nothing existed on Telegram — and never re-posted.

Both are asserted here against the real production loop functions and the real watcher.
"""
from __future__ import annotations

import argparse
import threading
from types import SimpleNamespace

import pytest

from chela import main
from chela.telegram.gatewatch import PermissionGateWatcher

# A real single-select AskUserQuestion selector (Claude Code 2.1.207) — the same
# measured render ``tests/test_telegram_panescan.py`` keys its detector tests on.
ASKUQ_PANE = """\
 ☐ Fruit

Which fruit do you prefer?

❯ 1. Apple
     A crisp red fruit
  2. Banana
     A soft yellow fruit
  3. Cherry
     A small red fruit
  4. Type something.
─────
  5. Chat about this

Enter to select · ↑/↓ to navigate · Esc to cancel
"""


class _Registry:
    chat_id = "-1001"

    def __init__(self, mapping):
        self._mapping = mapping

    def windows(self):
        return list(self._mapping)

    def thread_for_window(self, wid):
        return self._mapping.get(wid)


class _Sender:
    """Records sends, and lets a test wait for the first one."""

    def __init__(self):
        self.calls = []
        self.sent = threading.Event()

    def __call__(self, text, parse_mode=None, thread=None, reply_markup=None):
        self.calls.append((text, parse_mode, thread, reply_markup))
        self.sent.set()
        return True


def test_a_gate_is_relayed_while_the_transcript_relay_is_flood_blocked():
    """The pane poll must not sit behind the relay's flood-control sleep.

    The stuck monitor stands in for a relay mid-429: it is *inside* a
    ``sleep(retry_after)`` and will not return for as long as Telegram says. In the
    single-thread topology this test cannot pass at all — ``gate_watcher.poll`` was
    only ever reached after ``monitor.poll`` returned, so a gate that lived and died
    inside the stall was never once captured.
    """
    sender = _Sender()
    registry = _Registry({"@6": "5067"})
    watcher = PermissionGateWatcher(
        sender, registry, capture=lambda wid: ASKUQ_PANE, mirror=False)

    stop = threading.Event()
    blocked = threading.Event()   # the relay is inside its flood-control sleep
    release = threading.Event()   # …until the test lets it out

    class _FloodBlockedMonitor:
        def poll(self, window_ids):
            blocked.set()
            release.wait(10)

    relay = threading.Thread(
        target=main._outbound_loop,
        args=(_FloodBlockedMonitor(), registry, 1, stop),
        daemon=True,
    )
    panes = threading.Thread(
        target=main._pane_loop, args=(watcher, registry, 1, stop), daemon=True)
    relay.start()
    panes.start()
    try:
        assert blocked.wait(5), "the relay never entered its flood-control sleep"
        assert sender.sent.wait(5), (
            "the gate never reached Telegram: the pane poll was starved by the "
            "flood-blocked transcript relay"
        )
    finally:
        stop.set()
        release.set()
        relay.join(5)
        panes.join(5)

    text, _parse_mode, thread, _markup = sender.calls[0]
    assert thread == "5067"
    assert "Which fruit do you prefer?" in text


def test_the_pane_loop_survives_a_poll_that_raises():
    """A failing tick must not take the loop's thread down with it."""
    registry = _Registry({"@6": "5067"})
    stop = threading.Event()
    polled = threading.Event()
    calls = []

    class _AngryWatcher:
        def poll(self, window_ids):
            calls.append(list(window_ids))
            if len(calls) == 1:
                raise RuntimeError("tmux went away")
            polled.set()

    t = threading.Thread(
        target=main._pane_loop, args=(_AngryWatcher(), registry, 1, stop), daemon=True)
    t.start()
    try:
        assert polled.wait(5), "the pane loop died on a raising poll"
    finally:
        stop.set()
        t.join(5)
    assert calls[0] == ["@6"]


# ── the second defect: a post Telegram never delivered ──────────────────────


def test_a_gate_telegram_dropped_is_re_posted_on_the_next_tick():
    """A ``post`` that returned no message id delivered NOTHING — do not record it.

    Under flood control ``BotSender.post`` gives up after its bounded retries and
    returns ``None``. The tracker is edge-triggered on the render's signature, so a
    signature written for a message that does not exist is a gate that is never posted
    again — silently, for as long as the pane holds still. Which a *pending* gate does
    by definition: it is waiting for a human.
    """
    registry = _Registry({"@6": "5067"})
    posted = []
    outcomes = [None, 77]  # Telegram drops the first post, accepts the second

    def post(text, parse_mode, thread, markup):
        posted.append((text, thread))
        return outcomes.pop(0) if outcomes else 99

    watcher = PermissionGateWatcher(
        _Sender(), registry, capture=lambda wid: ASKUQ_PANE,
        post=post, edit=lambda *a: True, delete=lambda mid: True, mirror=False)

    watcher.poll(["@6"])          # dropped by Telegram
    watcher.poll(["@6"])          # same unanswered gate, same pane → post it again
    assert len(posted) == 2, "the dropped gate was recorded as delivered and never retried"
    watcher.poll(["@6"])          # now it IS on Telegram — edge trigger holds
    assert len(posted) == 2


# ── and the ways the FIX could bring the bug back ────────────────────────────


def test_a_resolving_gate_cannot_freeze_the_pane_loop():
    """The relay thread may not hold the watcher's lock across a Telegram call.

    :meth:`PermissionGateWatcher.observe` runs on the transcript relay's thread — the
    flood-controlled one — and an answered gate makes it *delete* the message it posted.
    A delete is a Bot API call like any other, so a 429'd one can take a minute; done
    under the lock that :meth:`poll` takes for every window, it stops the pane loop
    FLEET-WIDE. Which is this ticket's bug, re-entering through this ticket's cleanup
    path — and it only fires when a gate WAS posted, i.e. exactly when the daemon is busy
    relaying gates.

    The delete here is the flood-blocked one: it is *inside* the sleep and will not return
    until the test lets it out. The pane poll must complete anyway.
    """
    registry = _Registry({"@6": "5067"})
    in_delete = threading.Event()
    release = threading.Event()

    def delete(message_id):
        in_delete.set()
        release.wait(10)
        return True

    watcher = PermissionGateWatcher(
        _Sender(), registry, capture=lambda wid: ASKUQ_PANE,
        post=lambda *a: 42, edit=lambda *a: True, delete=delete, mirror=False)

    watcher.poll(["@6"])  # the gate is up, message 42 is tracked

    # The relay thread sees the AskUserQuestion's tool_result → resolve → delete → stall.
    answered = SimpleNamespace(
        content_type="tool_result", tool_use_id="t1", tool_name="AskUserQuestion")
    relay = threading.Thread(target=watcher.observe, args=("@6", answered), daemon=True)
    relay.start()
    assert in_delete.wait(5), "the relay thread never reached the delete"

    polled = threading.Event()

    def _tick():
        watcher.poll(["@6"])
        polled.set()

    panes = threading.Thread(target=_tick, daemon=True)
    panes.start()
    try:
        assert polled.wait(5), (
            "the pane poll was frozen behind a flood-blocked delete on the relay thread"
        )
    finally:
        release.set()
        relay.join(5)
        panes.join(5)


def test_a_gate_telegram_keeps_refusing_is_re_posted_on_a_backoff():
    """Re-posting every tick forever feeds the flood control it is retrying into.

    A gate is pending on a *human*: its pane can hold still for hours. The first retry is
    immediate (one 429 in a burst is usually gone a second later); after that the wait
    doubles. The gate is never abandoned — what is bounded is the request rate.
    """
    registry = _Registry({"@6": "5067"})
    clock = [1000.0]
    posted = []

    def post(text, parse_mode, thread, markup):
        posted.append(text)
        return None  # Telegram refuses everything, all storm long

    watcher = PermissionGateWatcher(
        _Sender(), registry, capture=lambda wid: ASKUQ_PANE, post=post,
        edit=lambda *a: True, delete=lambda mid: True, mirror=False,
        now=lambda: clock[0])

    watcher.poll(["@6"])
    assert len(posted) == 1
    watcher.poll(["@6"])                      # first retry: immediate
    assert len(posted) == 2
    watcher.poll(["@6"])                      # …then it backs off
    assert len(posted) == 2, "a refused gate re-posted every tick — that IS the flood"
    clock[0] += 5.0                           # _REPOST_BACKOFF_BASE
    watcher.poll(["@6"])
    assert len(posted) == 3
    clock[0] += 5.0                           # the wait has doubled to 10s
    watcher.poll(["@6"])
    assert len(posted) == 3
    clock[0] += 5.0
    watcher.poll(["@6"])
    assert len(posted) == 4


def test_a_landed_gate_clears_the_backoff():
    """The backoff is a storm response, not a state — a post that lands ends it."""
    registry = _Registry({"@6": "5067"})
    clock = [1000.0]
    posted = []
    outcomes = [None, 77]

    def post(text, parse_mode, thread, markup):
        posted.append(text)
        return outcomes.pop(0) if outcomes else 99

    watcher = PermissionGateWatcher(
        _Sender(), registry, capture=lambda wid: ASKUQ_PANE, post=post,
        edit=lambda *a: True, delete=lambda mid: True, mirror=False,
        now=lambda: clock[0])

    watcher.poll(["@6"])   # refused
    watcher.poll(["@6"])   # immediate retry — lands as message 77
    assert len(posted) == 2
    # The gate is answered and re-raised inside the same second. A backoff left behind by
    # the storm would swallow the new gate; there is no storm any more.
    watcher.forget("@6")
    watcher.poll(["@6"])
    assert len(posted) == 3


def test_a_refused_replacement_never_takes_the_live_gate_off_the_phone():
    """Poof the old message only once the new one is up.

    A re-render whose edit fails is poofed and re-posted. If the poof goes first and
    Telegram then refuses the post, the human's phone loses a gate it *had* and gets
    nothing back — a live prompt deleted by the very code that exists to deliver it.
    """
    registry = _Registry({"@6": "5067"})
    # The gate re-renders with new content (a mid-render partial settling is the real
    # case), so the watcher has to update the message it already posted.
    settled = ASKUQ_PANE.replace("Which fruit do you prefer?", "Which fruit, exactly?")
    panes = [ASKUQ_PANE, settled, settled]
    posts = [42, None, 43]
    deleted = []

    def post(text, parse_mode, thread, markup):
        return posts.pop(0)

    watcher = PermissionGateWatcher(
        _Sender(), registry, capture=lambda wid: panes.pop(0), post=post,
        edit=lambda *a: False,  # Telegram will not edit it (refused / message gone)
        delete=lambda mid: deleted.append(mid) or True, mirror=False)

    watcher.poll(["@6"])                 # the gate is on the phone as message 42
    watcher.poll(["@6"])                 # edit fails, the replacement post is refused
    assert deleted == [], "the live gate was deleted for a replacement that never landed"
    watcher.poll(["@6"])                 # now the replacement lands
    assert deleted == [42], "the superseded message was left behind with live buttons"


# ── D2: the daemon entrypoint must actually START the pane thread ────────────


class _FakeThread:
    """Records the threads the daemon starts, and starts none of them."""

    started: list["_FakeThread"] = []

    def __init__(self, target=None, args=(), kwargs=None, daemon=None, name=None):
        self.target = target
        self.args = args
        self.daemon = daemon

    def start(self):
        _FakeThread.started.append(self)

    def join(self, timeout=None):
        pass


def _tg_args(**over):
    """The ``chela telegram`` argparse Namespace, with the parser's own defaults."""
    base = dict(wid=None, bind=["@6:5067"], interval=2, auto_topics=False,
                reconcile_interval=15, no_inbound=False)
    base.update(over)
    return argparse.Namespace(**base)


@pytest.fixture
def daemon_env(monkeypatch):
    """Run ``cmd_telegram`` far enough to see what it spawns — and spawn nothing."""
    import chela.telegram as tg

    _FakeThread.started = []
    monkeypatch.setattr(threading, "Thread", _FakeThread)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "0:test-token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "-1001")
    monkeypatch.delenv("TELEGRAM_TOPIC_ID", raising=False)
    monkeypatch.setattr(
        main, "_build_bindings_registry", lambda args, chat: _Registry({"@6": "5067"}))

    made = []
    real_watcher = tg.PermissionGateWatcher

    def _spy(*a, **kw):
        watcher = real_watcher(*a, **kw)
        made.append(watcher)
        return watcher

    monkeypatch.setattr(tg, "PermissionGateWatcher", _spy)
    return SimpleNamespace(watchers=made, threads=_FakeThread.started)


def _pane_threads(threads):
    return [t for t in threads if t.target is main._pane_loop]


def test_the_no_inbound_daemon_starts_the_pane_thread(daemon_env, monkeypatch):
    """``chela telegram --no-inbound`` must SPAWN the pane loop, not merely define it.

    The loop function can be perfect and the daemon can still never call it — and every
    unit test in this file would stay green, because they drive ``_pane_loop`` directly.
    So assert the artifact that RUNS: the entrypoint starts the thread, with the watcher
    it wired to Telegram.
    """
    monkeypatch.setattr(main, "_outbound_loop", lambda *a, **kw: None)  # foreground; return

    main.cmd_telegram(_tg_args(no_inbound=True))

    panes = _pane_threads(daemon_env.threads)
    assert len(panes) == 1, "the --no-inbound daemon never started the pane thread"
    watcher, registry, interval, stop = panes[0].args
    assert watcher is daemon_env.watchers[0]
    assert registry.windows() == ["@6"]
    assert interval == 2
    assert isinstance(stop, threading.Event)
    assert panes[0].daemon is True


def test_the_inbound_daemon_starts_the_pane_thread_beside_the_relay(daemon_env, monkeypatch):
    """The PTB branch too — and on a SEPARATE thread from the transcript relay.

    Two threads, not one: that separation is the whole fix. A single thread carrying both
    is the topology in which a flood-controlled relay hides a live gate.
    """
    import chela.telegram as tg

    polled = []
    app = SimpleNamespace(run_polling=lambda: polled.append(True))
    monkeypatch.setattr(tg, "build_application", lambda *a, **kw: app)

    main.cmd_telegram(_tg_args(no_inbound=False))

    assert polled == [True], "the daemon never reached its inbound loop"
    panes = _pane_threads(daemon_env.threads)
    relays = [t for t in daemon_env.threads if t.target is main._outbound_loop]
    assert len(panes) == 1, "the inbound daemon never started the pane thread"
    assert len(relays) == 1, "the inbound daemon never started the transcript relay"
    assert panes[0].args[0] is daemon_env.watchers[0]
    assert panes[0] is not relays[0]
