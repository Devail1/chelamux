"""The two ways a gate that rendered on the pane never reached Telegram (CMX-74).

Measured live on 2026-07-14: the orchestrator window raised two ``AskUserQuestion``
selectors, both rendered in its pane, and **neither** arrived on the phone. The event
log dates them exactly — ``pre_tool_use`` → ``post_tool_use`` 5s apart for the first,
45s for the second — and the daemon log shows a Telegram flood-control storm (``Too
Many Requests: retry after 18``…) across both windows. Two defects, one storm:

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

import threading

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
    """THE repro. The pane poll must not sit behind the relay's flood-control sleep.

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
