"""Tests for the readiness-poll and idle-watchdog dropped-prompt handling.

Covers the startup-race bug where a fixed sleep sent the prompt during Claude
Code's startup splash, where it was silently dropped, leaving the agent idle at
an empty prompt.
"""
from __future__ import annotations

from unittest.mock import patch

from chela import dispatcher


# A pane that's still on the startup splash — no ready footer, no prompt glyph.
_NOT_READY = "Loading…\n\n  starting up"
# A pane with the bypass-permissions footer and an empty prompt box.
_READY_EMPTY = (
    "╭───────────────────────────────────╮\n"
    "│ ❯                                 │\n"
    "╰───────────────────────────────────╯\n"
    "  ⏵⏵ bypass permissions on (shift+tab to cycle)"
)
# An empty prompt box with NO footer — the default `--permission-mode auto`
# case, which does not render the bypass-permissions footer.
_READY_EMPTY_AUTO = (
    "╭───────────────────────────────────╮\n"
    "│ ❯                                 │\n"
    "╰───────────────────────────────────╯\n"
)
# A ready pane where the agent is actively working (no bare empty prompt).
_READY_WORKING = (
    "● Reading dispatcher.py…\n"
    "  ✶ Working… (esc to interrupt)\n"
    "  ⏵⏵ bypass permissions on (shift+tab to cycle)"
)


def test_pane_ready_detects_footer_and_glyph():
    assert dispatcher._pane_ready(_READY_EMPTY)
    assert dispatcher._pane_ready(_READY_WORKING)
    assert dispatcher._pane_ready("some text with ❯ glyph")
    assert not dispatcher._pane_ready(_NOT_READY)


def test_pane_idle_empty_prompt():
    # Empty input line at the prompt glyph → idle strand (footer present).
    assert dispatcher._pane_idle_empty_prompt(_READY_EMPTY)
    # Same, but with NO footer (auto-mode): still detected, because the
    # watchdog gates on the prompt glyph, not the bypass-permissions footer.
    assert dispatcher._pane_idle_empty_prompt(_READY_EMPTY_AUTO)
    # Working pane has the footer but no prompt glyph / bare empty prompt.
    assert not dispatcher._pane_idle_empty_prompt(_READY_WORKING)
    # No glyph at all → not at a ready prompt, so not an idle strand.
    assert not dispatcher._pane_idle_empty_prompt(_NOT_READY)
    # Prompt glyph present but the input line carries queued text → not idle.
    queued = "│ ❯ go fix the bug                  │\n"
    assert not dispatcher._pane_idle_empty_prompt(queued)


def test_wait_for_ready_polls_until_ready():
    """Pane is not-ready twice then ready; _wait_for_ready returns True only
    after the ready indicator appears, and captures it on the third poll."""
    captures = [_NOT_READY, _NOT_READY, _READY_EMPTY]

    with patch.object(dispatcher, "_capture_pane", side_effect=captures) as cap, \
         patch.object(dispatcher.time, "sleep") as sleep, \
         patch.object(dispatcher.time, "monotonic", side_effect=range(100)):
        ready = dispatcher._wait_for_ready("agent-3", min_wait=2, timeout=60, poll=1)

    assert ready is True
    # Polled exactly until the ready pane appeared (3 captures).
    assert cap.call_count == 3
    # Honored min_wait first, then polled with the poll interval.
    sleep_args = [c.args[0] for c in sleep.call_args_list]
    assert sleep_args[0] == 2  # min_wait honored as a minimum
    assert all(a == 1 for a in sleep_args[1:])  # then poll interval


def test_wait_for_ready_times_out_when_never_ready():
    """If the pane never becomes ready, _wait_for_ready returns False at the
    cap so the caller can send the prompt anyway (degrade, not hang)."""
    with patch.object(dispatcher, "_capture_pane", return_value=_NOT_READY), \
         patch.object(dispatcher.time, "sleep"), \
         patch.object(dispatcher.time, "monotonic", side_effect=[0, 0, 5, 10, 61]):
        ready = dispatcher._wait_for_ready("agent-3", min_wait=0, timeout=60, poll=1)

    assert ready is False
