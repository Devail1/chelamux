"""Permission-gate watcher — two-channel correlation + edge-triggered relay (C1).

Locks in the load-bearing behaviour of :class:`PermissionGateWatcher`:

  * it only reads a pane when the window's latest ``tool_use`` is unpaired
    (transcript-gated — no blind scraping);
  * a newly-detected gate relays EXACTLY ONE enriched line naming the real
    command from the transcript's unpaired ``tool_use``;
  * the relay is edge-triggered — a still-open gate is not re-posted, and the
    marker clears on the ``tool_result`` or when the pane stops showing a gate;
  * the transcript identity → tool + args extraction (Bash command, Edit path).
"""
from __future__ import annotations

from chela.telegram.gatewatch import PermissionGateWatcher, format_gate_message
from chela.telegram.panescan import Gate
from chela.telegram.parser import Message

PERMISSION_PANE = """\
 Do you want to proceed?
 ❯ 1. Yes
   2. No

 Esc to cancel
"""

WORKING_PANE = "● thinking...\n"


class _Registry:
    """Minimal binding registry: window → thread, with an unbound-window case."""

    def __init__(self, mapping):
        self._mapping = mapping

    def thread_for_window(self, wid):
        return self._mapping.get(wid)


class _Sender:
    """Records every send(text, parse_mode, thread) call."""

    def __init__(self):
        self.calls = []

    def __call__(self, text, parse_mode=None, thread=None):
        self.calls.append((text, parse_mode, thread))
        return True


def _capture(panes):
    """A capture stub returning canned pane text per window id."""

    def capture(wid):
        return panes.get(wid, "")

    return capture


def _tool_use(name, tuid, tool_input):
    return Message(
        "assistant", "tool_use", name,
        tool_name=name, tool_use_id=tuid, tool_input=tool_input,
    )


def _tool_result(name, tuid):
    return Message(
        "assistant", "tool_result", "done",
        tool_name=name, tool_use_id=tuid,
    )


def _watcher(sender, registry, panes):
    return PermissionGateWatcher(sender, registry, capture=_capture(panes))


# ── gated polling ─────────────────────────────────────────────────────────


def test_no_pending_tool_use_never_reads_the_pane():
    captured = []
    sender = _Sender()
    w = PermissionGateWatcher(
        sender, _Registry({"@1": "100"}),
        capture=lambda wid: (captured.append(wid), PERMISSION_PANE)[1],
    )
    # No tool_use observed → nothing pending → pane must not be scraped at all.
    w.poll(["@1"])
    assert captured == []
    assert sender.calls == []


def test_pending_tool_use_with_gate_relays_one_enriched_message():
    sender = _Sender()
    w = _watcher(sender, _Registry({"@1": "100"}), {"@1": PERMISSION_PANE})
    w.observe("@1", _tool_use("Bash", "u1", {"command": "rm -rf build/"}))
    w.poll(["@1"])
    assert len(sender.calls) == 1
    text, parse_mode, thread = sender.calls[0]
    assert text == "❓ Permission — Bash: rm -rf build/"
    assert parse_mode is None  # plain text — no MarkdownV2 escaping to get wrong
    assert thread == "100"


# ── edge trigger / de-dup ───────────────────────────────────────────────────


def test_same_open_gate_is_not_relayed_twice():
    sender = _Sender()
    w = _watcher(sender, _Registry({"@1": "100"}), {"@1": PERMISSION_PANE})
    w.observe("@1", _tool_use("Bash", "u1", {"command": "ls"}))
    w.poll(["@1"])
    w.poll(["@1"])  # gate still open, same unpaired tool_use
    w.poll(["@1"])
    assert len(sender.calls) == 1


def test_marker_clears_on_tool_result_and_new_gate_relays_again():
    sender = _Sender()
    panes = {"@1": PERMISSION_PANE}
    w = _watcher(sender, _Registry({"@1": "100"}), panes)
    w.observe("@1", _tool_use("Bash", "u1", {"command": "ls"}))
    w.poll(["@1"])
    assert len(sender.calls) == 1
    # Tool resolved → its result arrives → pending cleared.
    w.observe("@1", _tool_result("Bash", "u1"))
    w.poll(["@1"])  # no pending → no read, marker cleared
    assert len(sender.calls) == 1
    # A fresh blocked tool_use with a gate should relay again.
    w.observe("@1", _tool_use("Edit", "u2", {"file_path": "/repo/app.py"}))
    w.poll(["@1"])
    assert len(sender.calls) == 2
    assert sender.calls[1][0] == "❓ Permission — Edit: /repo/app.py"


def test_marker_clears_when_pane_no_longer_shows_a_gate():
    sender = _Sender()
    panes = {"@1": PERMISSION_PANE}
    w = _watcher(sender, _Registry({"@1": "100"}), panes)
    w.observe("@1", _tool_use("Bash", "u1", {"command": "ls"}))
    w.poll(["@1"])
    assert len(sender.calls) == 1
    # Same tool_use still pending, but the gate is gone (e.g. auto-approved) —
    # marker clears; if a gate reappears for the SAME tool it relays once more.
    panes["@1"] = WORKING_PANE
    w.poll(["@1"])
    assert len(sender.calls) == 1
    panes["@1"] = PERMISSION_PANE
    w.poll(["@1"])
    assert len(sender.calls) == 2


# ── binding gate ────────────────────────────────────────────────────────────


def test_unbound_window_is_not_relayed():
    sender = _Sender()
    w = _watcher(sender, _Registry({}), {"@1": PERMISSION_PANE})
    w.observe("@1", _tool_use("Bash", "u1", {"command": "ls"}))
    w.poll(["@1"])
    assert sender.calls == []


# ── transcript-identity extraction ──────────────────────────────────────────


def test_format_uses_bash_command_collapsing_whitespace():
    from chela.telegram.gatewatch import _PendingTool

    gate = Gate(text="Do you want to proceed?", kind="PermissionPrompt")
    msg = format_gate_message(_PendingTool("Bash", {"command": "make  test\n"}), gate)
    assert msg == "❓ Permission — Bash: make test"


def test_format_uses_edit_file_path():
    from chela.telegram.gatewatch import _PendingTool

    gate = Gate(text="…", kind="PermissionPrompt")
    msg = format_gate_message(_PendingTool("Edit", {"file_path": "/x/y.py"}), gate)
    assert msg == "❓ Permission — Edit: /x/y.py"


def test_format_unknown_tool_without_detail_names_the_tool():
    from chela.telegram.gatewatch import _PendingTool

    gate = Gate(text="Do you want to proceed?", kind="PermissionPrompt")
    msg = format_gate_message(_PendingTool("SomeTool", {"foo": "bar"}), gate)
    assert msg == "❓ Permission — SomeTool"


def test_format_falls_back_to_gate_text_without_identity():
    gate = Gate(text="Do you want to proceed?\n Esc to cancel", kind="PermissionPrompt")
    msg = format_gate_message(None, gate)
    assert msg.startswith("❓ Permission\n")
    assert "Do you want to proceed?" in msg


def test_latest_unpaired_tool_use_drives_the_gate():
    sender = _Sender()
    w = _watcher(sender, _Registry({"@1": "100"}), {"@1": PERMISSION_PANE})
    # Two unpaired tool_uses; the most-recent one is the likely-blocked tool.
    w.observe("@1", _tool_use("Read", "u1", {"file_path": "/a"}))
    w.observe("@1", _tool_use("Bash", "u2", {"command": "deploy"}))
    w.poll(["@1"])
    assert sender.calls[0][0] == "❓ Permission — Bash: deploy"
