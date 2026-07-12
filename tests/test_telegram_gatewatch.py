"""Pane watcher — permission-gate correlation (C1) + AskUserQuestion relay (A2).

Locks in the load-bearing behaviour of :class:`PermissionGateWatcher`:

  * the permission gate only relays when the window's latest ``tool_use`` is
    unpaired (transcript-gated — no blind relay), naming the real command;
  * an AskUserQuestion selector is relayed straight from the pane (no
    transcript gate — its tool_use is post-answer), with a semantic answer
    keyboard for a single-select and the nav row only for the multi-tab shape;
  * both relays are edge-triggered — a still-open prompt is not re-posted; the
    gate marker clears on its ``tool_result`` / when the pane clears, and the
    selector marker clears when it leaves the pane (answered) or on the
    AskUserQuestion ``tool_result`` (belt-and-suspenders).
"""
from __future__ import annotations

from chela.telegram.gatewatch import (
    PermissionGateWatcher,
    format_askuq_message,
    format_gate_message,
)
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
    """Records every send(text, parse_mode, thread, reply_markup=...) call."""

    def __init__(self):
        self.calls = []

    def __call__(self, text, parse_mode=None, thread=None, reply_markup=None):
        self.calls.append((text, parse_mode, thread, reply_markup))
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


def test_no_pending_tool_use_never_relays_a_permission_gate():
    sender = _Sender()
    w = PermissionGateWatcher(sender, _Registry({"@1": "100"}), capture=_capture({"@1": PERMISSION_PANE}))
    # No tool_use observed → no transcript identity → the permission gate must NOT
    # relay (the pane is still read each tick for AskUserQuestion, but this pane
    # shows a permission prompt, not a selector, so nothing is posted).
    w.poll(["@1"])
    assert sender.calls == []


def test_pending_tool_use_with_gate_relays_one_enriched_message():
    sender = _Sender()
    w = _watcher(sender, _Registry({"@1": "100"}), {"@1": PERMISSION_PANE})
    w.observe("@1", _tool_use("Bash", "u1", {"command": "rm -rf build/"}))
    w.poll(["@1"])
    assert len(sender.calls) == 1
    text, parse_mode, thread, reply_markup = sender.calls[0]
    assert text == "❓ Permission — Bash: rm -rf build/"
    assert parse_mode is None  # plain text — no MarkdownV2 escaping to get wrong
    assert thread == "100"
    assert reply_markup is None  # C1 permission gate carries no keyboard


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


# ── AskUserQuestion (pane-triggered, no transcript gate) ────────────────────

# A real single-select selector (Claude Code 2.1.207): checkbox header, question,
# numbered options (❯ marks the cursor), the meta-rows, then the footer.
ASKUQ_SINGLE_PANE = """\
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

# A multi-question / multi-select selector renders the ``←  ☐ … →`` tab strip.
ASKUQ_MULTI_PANE = """\
←  ☐ Fruit  ☐ Color  ✔ Submit  →

Which fruit do you prefer?

❯ 1. Apple
     A crisp red fruit
  2. Banana
  3. Type something.
─────
  4. Chat about this

Enter to select · Tab/Arrow keys to navigate · Esc to cancel
"""


def _ask_result(tuid="a1"):
    return _tool_result("AskUserQuestion", tuid)


def test_single_select_selector_relays_question_with_semantic_keyboard():
    sender = _Sender()
    w = _watcher(sender, _Registry({"@1": "100"}), {"@1": ASKUQ_SINGLE_PANE})
    # No pending tool_use needed — the selector is detected straight from the pane.
    w.poll(["@1"])
    assert len(sender.calls) == 1
    text, parse_mode, thread, reply_markup = sender.calls[0]
    assert text == "❓ Which fruit do you prefer?"
    assert parse_mode is None  # plain text — scraped question, no MarkdownV2
    assert thread == "100"
    callbacks = [b["callback_data"] for row in reply_markup["inline_keyboard"] for b in row]
    # One semantic button per REAL option (meta-rows excluded), plus the nav row.
    assert [c for c in callbacks if not c.startswith("qa:nav:")] == ["qa:0", "qa:1", "qa:2"]
    assert any(c.startswith("qa:nav:") for c in callbacks)


def test_multi_tab_selector_relays_question_with_nav_only():
    sender = _Sender()
    w = _watcher(sender, _Registry({"@1": "100"}), {"@1": ASKUQ_MULTI_PANE})
    w.poll(["@1"])
    assert len(sender.calls) == 1
    text, _pm, _thread, reply_markup = sender.calls[0]
    assert text == "❓ Which fruit do you prefer?"
    callbacks = [b["callback_data"] for row in reply_markup["inline_keyboard"] for b in row]
    # Multi-tab shape → nav row only, never a semantic button.
    assert not any(not c.startswith("qa:nav:") for c in callbacks)


def test_selector_relayed_once_then_deduped_until_it_leaves_the_pane():
    sender = _Sender()
    panes = {"@1": ASKUQ_SINGLE_PANE}
    w = _watcher(sender, _Registry({"@1": "100"}), panes)
    w.poll(["@1"])
    w.poll(["@1"])  # same selector still on the pane — edge-triggered, not per-poll
    assert len(sender.calls) == 1
    # Answered → selector gone → marker clears; a fresh selector relays again.
    panes["@1"] = WORKING_PANE
    w.poll(["@1"])
    assert len(sender.calls) == 1
    panes["@1"] = ASKUQ_SINGLE_PANE
    w.poll(["@1"])
    assert len(sender.calls) == 2


def test_askuserquestion_tool_result_clears_the_marker():
    # Belt-and-suspenders: even if the pane is momentarily still showing the
    # selector, the AskUserQuestion tool_result (which lands at answer-time) clears
    # the de-dup marker so a genuinely new question can relay again.
    sender = _Sender()
    w = _watcher(sender, _Registry({"@1": "100"}), {"@1": ASKUQ_SINGLE_PANE})
    w.poll(["@1"])
    assert len(sender.calls) == 1
    w.observe("@1", _ask_result())  # answered
    w.poll(["@1"])
    assert len(sender.calls) == 2


def test_unbound_window_selector_is_not_relayed():
    sender = _Sender()
    w = _watcher(sender, _Registry({}), {"@1": ASKUQ_SINGLE_PANE})
    w.poll(["@1"])
    assert sender.calls == []


def test_format_askuq_message_is_the_question():
    from chela.telegram.panescan import AskUQ

    uq = AskUQ(question="Which fruit?", options=("Apple",), cursor=0, multi=False)
    assert format_askuq_message(uq) == "❓ Which fruit?"
    blank = AskUQ(question="", options=(), cursor=-1, multi=True)
    assert format_askuq_message(blank).startswith("❓ ")


# ── AskUserQuestion edit-in-place (no double-relay across mid-render) ─────────

# The selector as it looks part-way through its first render: only option 1 has
# been drawn (a different content signature than the settled ASKUQ_SINGLE_PANE).
ASKUQ_PARTIAL_PANE = """\
 ☐ Fruit

Which fruit do you prefer?

❯ 1. Apple
     A crisp red fruit

Enter to select · ↑/↓ to navigate · Esc to cancel
"""


class _Bot:
    """A fake BotSender recording post/edit calls and handing out message ids."""

    def __init__(self):
        self.posts = []
        self.edits = []
        self._next_id = 100

    def post(self, text, parse_mode=None, thread=None, reply_markup=None):
        self._next_id += 1
        self.posts.append((self._next_id, text, thread, reply_markup))
        return self._next_id

    def edit(self, message_id, text, parse_mode=None, reply_markup=None):
        self.edits.append((message_id, text, reply_markup))
        return True


def _editing_watcher(bot, registry, panes):
    return PermissionGateWatcher(
        bot.post,  # sender is unused on the askuq edit path but must be callable
        registry,
        capture=_capture(panes),
        post=bot.post,
        edit=bot.edit,
    )


def test_changed_scrape_edits_in_place_instead_of_double_posting():
    bot = _Bot()
    panes = {"@1": ASKUQ_PARTIAL_PANE}
    w = _editing_watcher(bot, _Registry({"@1": "100"}), panes)
    # First scrape (mid-render, only option 1) → ONE post.
    w.poll(["@1"])
    assert len(bot.posts) == 1
    assert bot.posts[0][1] == "❓ Which fruit do you prefer?"
    # Selector finishes rendering (all three options) → a DIFFERENT signature.
    panes["@1"] = ASKUQ_SINGLE_PANE
    w.poll(["@1"])
    # No second post — the existing message is edited in place with the full list.
    assert len(bot.posts) == 1
    assert len(bot.edits) == 1
    edited_id, _text, markup = bot.edits[0]
    assert edited_id == bot.posts[0][0]
    callbacks = [b["callback_data"] for row in markup["inline_keyboard"] for b in row]
    assert [c for c in callbacks if not c.startswith("qa:nav:")] == ["qa:0", "qa:1", "qa:2"]


def test_unchanged_scrape_neither_edits_nor_reposts():
    bot = _Bot()
    w = _editing_watcher(bot, _Registry({"@1": "100"}), {"@1": ASKUQ_SINGLE_PANE})
    w.poll(["@1"])
    w.poll(["@1"])  # identical selector — edge-triggered
    assert len(bot.posts) == 1
    assert bot.edits == []


def test_answered_selector_posts_fresh_never_edits_the_old_message():
    bot = _Bot()
    panes = {"@1": ASKUQ_SINGLE_PANE}
    w = _editing_watcher(bot, _Registry({"@1": "100"}), panes)
    w.poll(["@1"])
    assert len(bot.posts) == 1
    # Answered → selector leaves the pane → tracked message id is dropped.
    panes["@1"] = WORKING_PANE
    w.poll(["@1"])
    # A genuinely new question posts fresh (never edits the answered message).
    panes["@1"] = ASKUQ_SINGLE_PANE
    w.poll(["@1"])
    assert len(bot.posts) == 2
    assert bot.edits == []


def test_edit_failure_falls_back_to_a_fresh_post():
    bot = _Bot()
    bot.edit = lambda *a, **k: False  # simulate the tracked message being deleted
    panes = {"@1": ASKUQ_PARTIAL_PANE}
    w = _editing_watcher(bot, _Registry({"@1": "100"}), panes)
    w.poll(["@1"])
    assert len(bot.posts) == 1
    panes["@1"] = ASKUQ_SINGLE_PANE
    w.poll(["@1"])  # edit returns False → post a fresh message
    assert len(bot.posts) == 2
