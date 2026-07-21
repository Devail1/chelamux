"""Pane watcher — the three live-TUI prompts, relayed with answer keyboards.

Locks in the load-bearing behaviour of :class:`PermissionGateWatcher`:

  * a permission gate relays **with NO ``tool_use`` in the transcript** — the
    live-broken case C1 shipped with (it read the pane only for a window with an
    unpaired ``tool_use``, but the gated call is appended to the JSONL only at
    approval-time, so the gate never posted). Identity is scraped from the pane
    dialog, and it carries the ✅ Allow once / ❌ Deny keyboard (C2);
  * an AskUserQuestion selector is relayed straight from the pane too, with a
    semantic answer keyboard for a single-select and the nav row only for the
    multi-tab shape;
  * an ExitPlanMode plan approval likewise, with the approve / keep-planning
    keyboard — and a plan pane never ALSO relays a permission gate (its
    "❯ 1. Yes, …" row matches the permission-menu signature);
  * every relay is edge-triggered — a still-open prompt is not re-posted, a
    changed scrape edits it in place — and **poofs** (the message is deleted) when
    the prompt leaves the pane or its ``tool_result`` lands, so no live keyboard is
    left behind on an answered prompt.

Every watcher here is built with ``mirror=False``. These tests are about the SEMANTIC
cards — what each prompt renders and what its keyboard answers — and a permission gate or
a plan approval now also posts a pane **mirror** alongside its card (CMX-52), which would
show up here only as an off-by-one in every ``len(sender.calls)``. The mirror, and the
fact that it coexists with these cards rather than replacing them, is covered on its own
in ``tests/test_telegram_mirror.py``.
"""
from __future__ import annotations

from chela.telegram.gatewatch import (
    PermissionGateWatcher,
    format_askuq_message,
    format_gate_message,
    format_plan_message,
)
from chela.telegram.panescan import Gate
from chela.telegram.parser import Message

# A real Bash gate (Claude Code 2.1.207): the dialog is headed by the command it
# wants to run — the only place that command exists while the gate is pending.
PERMISSION_PANE = """\
 Bash command
   rm -rf build/

 Do you want to proceed?
 ❯ 1. Yes
   2. Yes, and don't ask again for rm commands in this project
   3. No, and tell Claude what to do differently (esc)

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
    return PermissionGateWatcher(
        sender, registry, capture=_capture(panes), mirror=False)


# ── ungated pane detection (the C1 bug C2 fixes) ──────────────────────────


def test_gate_relays_with_no_tool_use_in_the_transcript():
    # THE core fix. While a gate is pending, the gated call is NOT in the JSONL
    # (Claude Code appends its tool_use only at approval-time), so C1's
    # "only read the pane when a tool_use is unpaired" precondition was never true
    # and the ❓ Permission message never posted. Detection is now pane-only.
    sender = _Sender()
    w = _watcher(sender, _Registry({"@1": "100"}), {"@1": PERMISSION_PANE})
    w.poll(["@1"])  # nothing observed from the transcript at all
    assert len(sender.calls) == 1
    text, parse_mode, thread, reply_markup = sender.calls[0]
    # The command comes from the pane dialog's own "Bash command" header.
    assert text == "❓ Permission — Bash: rm -rf build/"
    assert parse_mode is None  # plain text — no MarkdownV2 escaping to get wrong
    assert thread == "100"
    # Slice C2's keyboard: Enter allows the default "1. Yes" once, Escape denies.
    callbacks = [b["callback_data"] for row in reply_markup["inline_keyboard"] for b in row]
    assert callbacks == ["qa:nav:ent", "qa:nav:esc"]


def test_gate_falls_back_to_an_unpaired_tool_use_when_the_dialog_is_unrecognised():
    # A reworded dialog we can't scrape an identity from still names the tool when
    # the transcript happens to have one unpaired.
    sender = _Sender()
    bare = " Do you want to proceed?\n ❯ 1. Yes\n   2. No\n\n Esc to cancel\n"
    w = _watcher(sender, _Registry({"@1": "100"}), {"@1": bare})
    w.observe("@1", _tool_use("Bash", "u1", {"command": "make  test\n"}))
    w.poll(["@1"])
    assert sender.calls[0][0] == "❓ Permission — Bash: make test"


# ── edge trigger / de-dup ───────────────────────────────────────────────────


def test_same_open_gate_is_not_relayed_twice():
    sender = _Sender()
    w = _watcher(sender, _Registry({"@1": "100"}), {"@1": PERMISSION_PANE})
    w.poll(["@1"])
    w.poll(["@1"])  # gate still open, same scrape
    w.poll(["@1"])
    assert len(sender.calls) == 1


def test_marker_clears_when_pane_no_longer_shows_a_gate():
    sender = _Sender()
    panes = {"@1": PERMISSION_PANE}
    w = _watcher(sender, _Registry({"@1": "100"}), panes)
    w.poll(["@1"])
    assert len(sender.calls) == 1
    # Answered → the gate leaves the pane → tracking clears …
    panes["@1"] = WORKING_PANE
    w.poll(["@1"])
    assert len(sender.calls) == 1
    # … so the next gate relays again.
    panes["@1"] = PERMISSION_PANE
    w.poll(["@1"])
    assert len(sender.calls) == 2


def test_a_different_gate_relays_again_without_leaving_the_pane():
    sender = _Sender()
    panes = {"@1": PERMISSION_PANE}
    w = _watcher(sender, _Registry({"@1": "100"}), panes)
    w.poll(["@1"])
    # A second gate (a different command) drawn straight after the first is
    # answered: a changed scrape, so it must not be swallowed by the de-dup.
    panes["@1"] = PERMISSION_PANE.replace("rm -rf build/", "npm run deploy")
    w.poll(["@1"])
    assert len(sender.calls) == 2
    assert sender.calls[1][0] == "❓ Permission — Bash: npm run deploy"


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


def test_latest_unpaired_tool_use_drives_the_fallback_identity():
    sender = _Sender()
    bare = " Do you want to proceed?\n ❯ 1. Yes\n   2. No\n\n Esc to cancel\n"
    w = _watcher(sender, _Registry({"@1": "100"}), {"@1": bare})
    # Two unpaired tool_uses; the most-recent one is the likely-blocked tool.
    w.observe("@1", _tool_use("Read", "u1", {"file_path": "/a"}))
    w.observe("@1", _tool_use("Bash", "u2", {"command": "deploy"}))
    w.poll(["@1"])
    assert sender.calls[0][0] == "❓ Permission — Bash: deploy"


def test_scraped_pane_identity_wins_over_a_stale_unpaired_tool_use():
    # The pane is authoritative: the gated call is not in the transcript, so an
    # unpaired tool_use is some OTHER call and must not mislabel the gate.
    sender = _Sender()
    w = _watcher(sender, _Registry({"@1": "100"}), {"@1": PERMISSION_PANE})
    w.observe("@1", _tool_use("Read", "u1", {"file_path": "/somewhere/else.py"}))
    w.poll(["@1"])
    assert sender.calls[0][0] == "❓ Permission — Bash: rm -rf build/"


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
    assert parse_mode is None  # plain text — scraped question, no MarkdownV2
    assert thread == "100"
    # The BODY carries the question AND every option in full, numbered — a button
    # caption truncates to one line, the body wraps, so this is the only surface
    # that can actually show the choices.
    assert text.startswith("❓ Which fruit do you prefer?")
    assert "1. Apple" in text and "2. Banana" in text and "3. Cherry" in text
    assert "Type something" not in text  # meta-rows are not options
    # … and each option's scraped description rides under it.
    assert "A crisp red fruit" in text and "A soft yellow fruit" in text
    callbacks = [b["callback_data"] for row in reply_markup["inline_keyboard"] for b in row]
    # One compact selector per REAL option (meta-rows excluded), plus the nav row.
    assert [c for c in callbacks if not c.startswith("qa:nav:")] == ["qa:0", "qa:1", "qa:2"]
    assert any(c.startswith("qa:nav:") for c in callbacks)
    captions = [b["text"] for row in reply_markup["inline_keyboard"] for b in row]
    assert captions[:3] == ["1", "2", "3"]  # numbers, keyed to the numbered body


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


def test_format_askuq_message_numbers_every_option_in_the_body():
    from chela.telegram.panescan import AskUQ

    uq = AskUQ(
        question="Which fruit?",
        options=("Apple", "Banana"),
        cursor=0,
        multi=False,
        descriptions=("A crisp red fruit", ""),
    )
    body = format_askuq_message(uq)
    assert body.splitlines()[0] == "❓ Which fruit?"
    # Numbered, in scraped order, 1:1 with the qa:<i> selector buttons.
    assert "1. Apple" in body and "2. Banana" in body
    assert body.index("1. Apple") < body.index("2. Banana")
    # The description is indented under its own option, never attached to another.
    assert "\n   A crisp red fruit" in body
    assert body.index("A crisp red fruit") < body.index("2. Banana")


def test_format_askuq_message_falls_back_when_the_question_scraped_empty():
    from chela.telegram.panescan import AskUQ

    blank = AskUQ(question="", options=(), cursor=-1, multi=True)
    assert format_askuq_message(blank).startswith("❓ ")


def test_format_askuq_message_body_omits_options_for_the_multi_fallback():
    from chela.telegram.panescan import AskUQ

    # The multi shape gets the nav row (no per-option buttons), so numbering
    # options in the body would promise selectors that aren't there.
    multi = AskUQ(question="Pick some", options=(), cursor=-1, multi=True)
    assert format_askuq_message(multi) == "❓ Pick some"


def test_format_askuq_message_truncates_a_pathological_option_never_drops_it():
    from chela.telegram.panescan import AskUQ

    uq = AskUQ(
        question="Which?",
        options=("A" * 6000, "B" * 6000, "sane"),
        cursor=0,
        multi=False,
        descriptions=("D" * 6000, "", ""),
    )
    body = format_askuq_message(uq)
    assert len(body) <= 4096  # Telegram's hard cap on a message body
    # Every option still has its numbered line — an unlisted option is unpickable,
    # since its selector button is still on the keyboard.
    assert "1. A" in body and "2. B" in body and "3. sane" in body


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
        mirror=False,
    )


def test_changed_scrape_edits_in_place_instead_of_double_posting():
    bot = _Bot()
    panes = {"@1": ASKUQ_PARTIAL_PANE}
    w = _editing_watcher(bot, _Registry({"@1": "100"}), panes)
    # First scrape (mid-render, only option 1) → ONE post.
    w.poll(["@1"])
    assert len(bot.posts) == 1
    assert bot.posts[0][1].startswith("❓ Which fruit do you prefer?\n\n1. Apple")
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


# ── ExitPlanMode plan approval (pane-triggered, no transcript gate — B2) ─────

# A plan-approval pane (Claude Code 2.1.207): the plan text, the proceed prompt +
# numbered choices, then the "Esc to cancel" footer. Mirrors AskUserQuestion —
# the tool_use lands post-answer, so it's detected straight from the pane.
EXITPLAN_PANE = """\
● Here is my plan:

  1. Add detect_exitplanmode to panescan.py
  2. Wire the pane trigger into gatewatch

 Would you like to proceed?
 ❯ 1. Yes, and auto-accept edits
   2. Yes, and manually approve edits
   3. No, keep planning

 Esc to cancel
"""

# The same plan mid-render (the second step hasn't drawn yet) — a different
# scraped signature, so a re-scrape edits the message rather than double-posting.
EXITPLAN_PARTIAL_PANE = """\
● Here is my plan:

  1. Add detect_exitplanmode to panescan.py

 Would you like to proceed?
 ❯ 1. Yes, and auto-accept edits
   2. No, keep planning

 Esc to cancel
"""


def _plan_result(tuid="p1"):
    return _tool_result("ExitPlanMode", tuid)


def test_plan_selector_relays_the_plan_with_approve_keyboard():
    sender = _Sender()
    w = _watcher(sender, _Registry({"@1": "100"}), {"@1": EXITPLAN_PANE})
    # No pending tool_use needed — the plan approval is detected from the pane.
    w.poll(["@1"])
    assert len(sender.calls) == 1
    text, parse_mode, thread, reply_markup = sender.calls[0]
    assert text.startswith("📋")
    assert "Here is my plan:" in text            # the scraped plan is the body …
    assert "Would you like to proceed?" not in text  # … not the options (buttons carry those)
    assert parse_mode is None                     # plain text — scraped, no MarkdownV2
    assert thread == "100"
    callbacks = [b["callback_data"] for row in reply_markup["inline_keyboard"] for b in row]
    assert "qa:nav:ent" in callbacks              # ✅ Approve (auto mode) → Enter
    assert "qa:nav:esc" in callbacks              # 📝 Keep planning → Escape
    # Option-count-independent: no semantic index buttons for ExitPlanMode.
    assert not any(c.startswith("qa:") and not c.startswith("qa:nav:") for c in callbacks)


def test_plan_selector_relayed_once_then_deduped_until_it_leaves_the_pane():
    sender = _Sender()
    panes = {"@1": EXITPLAN_PANE}
    w = _watcher(sender, _Registry({"@1": "100"}), panes)
    w.poll(["@1"])
    w.poll(["@1"])  # same plan still on the pane — edge-triggered, not per-poll
    assert len(sender.calls) == 1
    # Resolved → selector gone → marker clears; a fresh plan relays again.
    panes["@1"] = WORKING_PANE
    w.poll(["@1"])
    assert len(sender.calls) == 1
    panes["@1"] = EXITPLAN_PANE
    w.poll(["@1"])
    assert len(sender.calls) == 2


def test_exitplanmode_tool_result_clears_the_marker():
    # Belt-and-suspenders: the ExitPlanMode tool_result (lands at answer-time)
    # clears the de-dup marker so a genuinely new plan can relay again.
    sender = _Sender()
    w = _watcher(sender, _Registry({"@1": "100"}), {"@1": EXITPLAN_PANE})
    w.poll(["@1"])
    assert len(sender.calls) == 1
    w.observe("@1", _plan_result())  # resolved
    w.poll(["@1"])
    assert len(sender.calls) == 2


def test_unbound_window_plan_is_not_relayed():
    sender = _Sender()
    w = _watcher(sender, _Registry({}), {"@1": EXITPLAN_PANE})
    w.poll(["@1"])
    assert sender.calls == []


def test_plan_changed_scrape_edits_in_place_instead_of_double_posting():
    bot = _Bot()
    panes = {"@1": EXITPLAN_PARTIAL_PANE}
    w = _editing_watcher(bot, _Registry({"@1": "100"}), panes)
    # First scrape (mid-render) → ONE post.
    w.poll(["@1"])
    assert len(bot.posts) == 1
    # The plan finishes rendering (a different signature) → EDIT, not a new post.
    panes["@1"] = EXITPLAN_PANE
    w.poll(["@1"])
    assert len(bot.posts) == 1
    assert len(bot.edits) == 1
    assert bot.edits[0][0] == bot.posts[0][0]


def test_plan_answered_posts_fresh_never_edits_the_old_message():
    bot = _Bot()
    panes = {"@1": EXITPLAN_PANE}
    w = _editing_watcher(bot, _Registry({"@1": "100"}), panes)
    w.poll(["@1"])
    assert len(bot.posts) == 1
    panes["@1"] = WORKING_PANE  # resolved → tracked id dropped
    w.poll(["@1"])
    panes["@1"] = EXITPLAN_PANE  # a genuinely new plan
    w.poll(["@1"])
    assert len(bot.posts) == 2
    assert bot.edits == []


def test_format_plan_message_bodies():
    from chela.telegram.panescan import ExitPlan

    assert format_plan_message(ExitPlan(text="do X\ndo Y")).startswith("📋")
    assert "do X" in format_plan_message(ExitPlan(text="do X"))
    # An empty scrape (plan scrolled off) still gets a generic prompt, never crashes.
    assert format_plan_message(ExitPlan(text="")).startswith("📋")
    # A very long plan is truncated with a /screenshot pointer.
    long_plan = format_plan_message(ExitPlan(text="x" * 5000))
    assert "/screenshot" in long_plan
    assert len(long_plan) < 5000


# ── one pane, one prompt (a plan pane must not ALSO relay a gate) ────────────


def test_plan_pane_does_not_also_relay_a_permission_gate():
    # The plan-approval selector's "❯ 1. Yes, and auto-accept edits" row matches the
    # permission-menu signature too. With permission detection now ungated, the
    # selectors take precedence — one pane relays exactly one prompt.
    sender = _Sender()
    w = _watcher(sender, _Registry({"@1": "100"}), {"@1": EXITPLAN_PANE})
    w.poll(["@1"])
    assert len(sender.calls) == 1
    assert sender.calls[0][0].startswith("📋")


def test_askuq_pane_does_not_also_relay_a_permission_gate():
    sender = _Sender()
    w = _watcher(sender, _Registry({"@1": "100"}), {"@1": ASKUQ_SINGLE_PANE})
    w.poll(["@1"])
    assert len(sender.calls) == 1
    assert sender.calls[0][0].startswith("❓ Which fruit")


# ── poof on resolve (the answered prompt's message is deleted) ───────────────


class _DeletingBot(_Bot):
    """A fake BotSender that also records deleteMessage calls."""

    def __init__(self):
        super().__init__()
        self.deleted = []

    def delete(self, message_id):
        self.deleted.append(message_id)
        return True


def _poofing_watcher(bot, registry, panes):
    return PermissionGateWatcher(
        bot.post, registry, capture=_capture(panes),
        post=bot.post, edit=bot.edit, delete=bot.delete, mirror=False,
    )


def test_answered_question_message_is_deleted():
    bot = _DeletingBot()
    panes = {"@1": ASKUQ_SINGLE_PANE}
    w = _poofing_watcher(bot, _Registry({"@1": "100"}), panes)
    w.poll(["@1"])
    panes["@1"] = WORKING_PANE  # answered → selector leaves the pane
    w.poll(["@1"])
    assert bot.deleted == [bot.posts[0][0]]


def test_resolved_plan_and_gate_messages_are_deleted():
    for pane in (EXITPLAN_PANE, PERMISSION_PANE):
        bot = _DeletingBot()
        panes = {"@1": pane}
        w = _poofing_watcher(bot, _Registry({"@1": "100"}), panes)
        w.poll(["@1"])
        panes["@1"] = WORKING_PANE
        w.poll(["@1"])
        assert bot.deleted == [bot.posts[0][0]]


def test_tool_result_poofs_the_prompt_even_before_the_pane_repaints():
    # Belt-and-suspenders: the AskUserQuestion / ExitPlanMode tool_result lands at
    # answer-time, so it deletes the message without waiting for the next capture.
    bot = _DeletingBot()
    w = _poofing_watcher(bot, _Registry({"@1": "100"}), {"@1": ASKUQ_SINGLE_PANE})
    w.poll(["@1"])
    w.observe("@1", _ask_result())
    assert bot.deleted == [bot.posts[0][0]]


def test_a_still_open_prompt_is_never_deleted():
    bot = _DeletingBot()
    w = _poofing_watcher(bot, _Registry({"@1": "100"}), {"@1": PERMISSION_PANE})
    w.poll(["@1"])
    w.poll(["@1"])
    assert bot.deleted == []


# ── the gate rendered from its HOOK PAYLOAD, not from the pane (CMX-49) ──────
#
# The live bug (2026-07-14): a 3-question AskUserQuestion whose options carried
# `preview` boxes arrived on the phone as its question text and a bare nav row —
# zero options, zero descriptions, zero previews, nothing to tap — while
# `hook.pre_tool_use` in the event log already held every one of them. The pane
# scrape parses that shape as "multi / unparseable" (a multi-question selector
# draws a tab strip; a preview re-lays the TUI out side-by-side), and always will:
# the scraper keeps meeting shapes it was not measured against. The hook does not.

from chela.telegram.gatewatch import Card, format_hook_askuq_cards  # noqa: E402
from chela.telegram.hookgate import HookGate, Option, Question  # noqa: E402


def _gate(*questions, tuid="toolu_01"):
    return HookGate(tool_use_id=tuid, tool="AskUserQuestion",
                    questions=tuple(questions), seq=184)


# The real shape, trimmed: three questions, each option carrying a box-drawing preview.
SIDEBAR = Question(
    question="How aggressive should the sidebar consolidation be?",
    header="Sidebar",
    options=(
        Option("Spine: 5 views, Feed is home", "Feed becomes the default landing view.",
               "BEFORE (6)      AFTER (5)\n  Wall            Feed"),
        Option("Conservative: add Feed", "Lower risk, less payoff.",
               "BEFORE (6)      AFTER (6)"),
    ),
)
ACT_PATH = Question(
    question="Scope of the Feed: read-only, or can you ACT on a row?",
    header="Act path",
    options=(Option("Read-only", "Ship the render first."),
             Option("Actionable", "Answer a gate from the row.")),
)
BACKFILL = Question(
    question="The events in the live log are test traffic. Keep them?",
    header="Backfill",
    options=(Option("Keep"), Option("Wipe")),
)


class _HookBot(_DeletingBot):
    """Records the parse_mode too — a preview rides in an HTML ``<pre>`` block."""

    def __init__(self):
        super().__init__()
        self.sent = []      # (text, parse_mode, thread, markup)

    def post(self, text, parse_mode=None, thread=None, reply_markup=None):
        self.sent.append((text, parse_mode, thread, reply_markup))
        return super().post(text, parse_mode, thread, reply_markup)


def _hook_watcher(bot, panes, gate, registry=None):
    return PermissionGateWatcher(
        bot.post, registry or _Registry({"@1": "100"}), capture=_capture(panes),
        post=bot.post, edit=bot.edit, delete=bot.delete,
        pending=lambda _wid: gate, mirror=False,
    )


def test_a_multi_question_gate_renders_from_the_payload_one_message_per_question():
    bot = _HookBot()
    # The pane is the shape that BROKE: the scraper reads it as unparseable (no options).
    w = _hook_watcher(bot, {"@1": ASKUQ_MULTI_PANE}, _gate(SIDEBAR, ACT_PATH, BACKFILL))
    w.poll(["@1"])

    # One message PER QUESTION — three questions × previews is a wall of text otherwise,
    # and the TUI itself walks the questions one at a time.
    assert len(bot.sent) == 3
    first, second, third = bot.sent
    assert "Question 1/3" in first[0] and "Sidebar" in first[0]
    assert "Question 2/3" in second[0] and "Question 3/3" in third[0]

    # Everything the scrape threw away: the options, their descriptions, and the previews
    # (in a <pre> block, which scrolls horizontally on a phone instead of wrapping the
    # box-drawing into soup).
    body, parse_mode, thread, markup = first
    assert parse_mode == "HTML" and thread == "100"
    assert "1. Spine: 5 views, Feed is home" in body
    assert "2. Conservative: add Feed" in body
    assert "Feed becomes the default landing view." in body
    assert "<pre>BEFORE (6)      AFTER (5)\n  Wall            Feed</pre>" in body
    assert "AFTER (6)</pre>" in body
    # Not one word of it came from the pane: the scrape of this shape has no options.
    assert "Which fruit" not in body

    # A multi-question shape cannot be answered by ordinal (option i means a different
    # thing per question), so: nav row only, and the card SAYS it must be answered in the
    # terminal — rather than looking merely unhelpful.
    callbacks = [b["callback_data"] for row in markup["inline_keyboard"] for b in row]
    assert all(c.startswith("qa:nav:") for c in callbacks)
    assert "terminal" in body


def test_a_window_with_no_hook_events_still_renders_from_the_pane():
    # The pre-plugin fleet: hooks are read at agent STARTUP, so an agent launched before
    # the plugin was installed emits none. The pane scrape must not regress.
    bot = _HookBot()
    w = _hook_watcher(bot, {"@1": ASKUQ_SINGLE_PANE}, None)
    w.poll(["@1"])
    assert len(bot.sent) == 1
    body, parse_mode, _thread, markup = bot.sent[0]
    assert parse_mode is None                       # the plain scraped render, unchanged
    assert body.startswith("❓ Which fruit do you prefer?")
    callbacks = [b["callback_data"] for row in markup["inline_keyboard"] for b in row]
    assert [c for c in callbacks if not c.startswith("qa:nav:")] == ["qa:0", "qa:1", "qa:2"]


def test_a_gate_is_only_rendered_while_the_pane_shows_a_selector():
    # An unresolved pre_tool_use also describes an agent that DIED at the gate. The pane
    # is what says the selector is on screen right now — corroborate, never assume.
    bot = _HookBot()
    w = _hook_watcher(bot, {"@1": WORKING_PANE}, _gate(SIDEBAR, ACT_PATH))
    w.poll(["@1"])
    assert bot.sent == []


def test_a_single_select_gate_keeps_its_semantic_option_buttons():
    # The one shape whose ordinal mapping can be PROVEN: one question, single-select, and
    # the pane scrape found the same number of options the payload declares. It keeps the
    # working 1 2 3 selector — the zero-keypress answer path is the NEXT task.
    bot = _HookBot()
    fruit = Question(
        question="Which fruit do you prefer?",
        header="Fruit",
        options=(Option("Apple", "A crisp red fruit"),
                 Option("Banana", "A soft yellow fruit"),
                 Option("Cherry", "A small red fruit")),
    )
    w = _hook_watcher(bot, {"@1": ASKUQ_SINGLE_PANE}, _gate(fruit))
    w.poll(["@1"])
    assert len(bot.sent) == 1
    body, parse_mode, _thread, markup = bot.sent[0]
    assert parse_mode == "HTML"
    callbacks = [b["callback_data"] for row in markup["inline_keyboard"] for b in row]
    assert [c for c in callbacks if not c.startswith("qa:nav:")] == ["qa:0", "qa:1", "qa:2"]
    assert "terminal" not in body                   # it IS answerable — claim nothing else
    assert "Question 1/1" not in body               # a lone question needs no counter


def test_a_multiselect_question_is_never_given_ordinal_buttons():
    bot = _HookBot()
    multi = Question(question="Pick some", header="Fruit", multi_select=True,
                     options=(Option("Apple"), Option("Banana"), Option("Cherry")))
    w = _hook_watcher(bot, {"@1": ASKUQ_SINGLE_PANE}, _gate(multi))
    w.poll(["@1"])
    body, _pm, _thread, markup = bot.sent[0]
    callbacks = [b["callback_data"] for row in markup["inline_keyboard"] for b in row]
    assert all(c.startswith("qa:nav:") for c in callbacks)   # an answer is a SET, not an i
    assert "multi-select" in body and "terminal" in body


def test_an_option_with_no_preview_renders_no_empty_code_block():
    bot = _HookBot()
    w = _hook_watcher(bot, {"@1": ASKUQ_MULTI_PANE}, _gate(ACT_PATH))   # no previews
    w.poll(["@1"])
    body = bot.sent[0][0]
    assert "<pre>" not in body
    assert "1. Read-only" in body and "2. Actionable" in body


def test_a_preview_that_does_not_fit_is_reported_never_silently_dropped():
    # A card that renders as though it were complete when it is not is the same silent
    # degrade as the wrong wid (CMX-48) and the silent mis-answer (CMX-32).
    bot = _HookBot()
    huge = Question(
        question="Pick one",
        header="Huge",
        options=tuple(Option(f"Option {i}", "d" * 200, "P" * 4000) for i in range(4)),
    )
    w = _hook_watcher(bot, {"@1": ASKUQ_MULTI_PANE}, _gate(huge))
    w.poll(["@1"])
    body = bot.sent[0][0]
    assert len(body) <= 4096                         # Telegram's hard cap
    for i in range(4):
        assert f"{i + 1}. Option {i}" in body        # a dropped option is unpickable
    assert "preview" in body.lower() and ("clipped" in body or "NOT shown" in body)


def test_every_question_message_is_poofed_when_the_selector_leaves_the_pane():
    bot = _HookBot()
    panes = {"@1": ASKUQ_MULTI_PANE}
    w = _hook_watcher(bot, panes, _gate(SIDEBAR, ACT_PATH, BACKFILL))
    w.poll(["@1"])
    assert len(bot.posts) == 3
    panes["@1"] = WORKING_PANE                       # answered
    w.poll(["@1"])
    # All three, not just the first: a live keyboard left on an answered question fires
    # keystrokes at whatever the agent went on to do.
    assert bot.deleted == [mid for mid, *_rest in bot.posts]


def test_an_unchanged_gate_is_not_reposted_and_a_changed_one_edits_in_place():
    bot = _HookBot()
    gate = _gate(SIDEBAR, ACT_PATH)
    w = _hook_watcher(bot, {"@1": ASKUQ_MULTI_PANE}, gate)
    w.poll(["@1"])
    w.poll(["@1"])
    assert len(bot.posts) == 2 and bot.edits == []   # edge-triggered, 2 questions
    # The same gate, re-rendered (a repaint changed a description) → edit, don't re-post.
    w._pending_gate = lambda _wid: _gate(
        Question(question=SIDEBAR.question, header=SIDEBAR.header,
                 options=(Option("Spine: 5 views, Feed is home", "Reworded."),
                          Option("Conservative: add Feed", "Lower risk."))),
        ACT_PATH,
    )
    w.poll(["@1"])
    assert len(bot.posts) == 2 and len(bot.edits) == 2


def test_a_rejected_html_body_falls_back_to_plain_text_never_to_silence():
    # Telegram rejects a message whose entities it cannot parse. A formatting failure must
    # cost us the formatting, not the content — the options are the whole message.
    bot = _HookBot()
    posts: list = []

    def flaky_post(text, parse_mode=None, thread=None, reply_markup=None):
        posts.append((text, parse_mode))
        return None if parse_mode == "HTML" else 42

    bot.post = flaky_post
    w = _hook_watcher(bot, {"@1": ASKUQ_MULTI_PANE}, _gate(SIDEBAR))
    w.poll(["@1"])
    assert [pm for _t, pm in posts] == ["HTML", None]
    plain = posts[1][0]
    assert "<pre>" not in plain
    assert "1. Spine: 5 views, Feed is home" in plain and "BEFORE (6)" in plain


def test_format_hook_askuq_cards_escapes_html_in_the_payload():
    # The payload is agent-authored text: a `<b>` in a label must not become markup, and
    # must not make Telegram reject the whole card.
    cards = format_hook_askuq_cards(
        _gate(Question(question="A & B <or> C?", header="H",
                       options=(Option("<b>bold</b>", "a & b", "x < y"),))),
        answerable=False,
    )
    assert len(cards) == 1
    card = cards[0]
    assert isinstance(card, Card) and card.parse_mode == "HTML"
    assert "&lt;b&gt;bold&lt;/b&gt;" in card.text and "A &amp; B" in card.text
    assert "<pre>x &lt; y</pre>" in card.text
    # The plain fallback is the same body with our markup undone — never HTML-escaped soup.
    assert "<b>bold</b>" in (card.plain or "") and "x < y" in (card.plain or "")


# ── CMX-50: a HELD gate is answerable — with zero keypresses ─────────────────
#
# While the daemon holds an agent's `PermissionRequest` hook open (chela.gateanswer), a
# tap is handed straight back through it. So the card stops apologising and grows real
# buttons — for EVERY shape, including the multi-question and multiSelect pickers the
# keystroke path had to refuse (there is no `❯` cursor to inject against, and the one time
# it guessed it silently answered option 2 for a tap on option 3 — CMX-32).

from chela.gateanswer import OpenGate  # noqa: E402

FRUIT = Question(
    question="Which fruit do you prefer?",
    header="Fruit",
    options=(Option("Apple", "A crisp red fruit"),
             Option("Banana", "A soft yellow fruit"),
             Option("Cherry", "A small red fruit")),
)
MULTI_SELECT = Question(
    question="Which extras?",
    header="Extras",
    multi_select=True,
    options=(Option("Metrics"), Option("Tracing")),
)


def _held(tuid="toolu_01", budget=90.0):
    return OpenGate(tool_use_id=tuid, wid="@1", questions=[], deadline=9e18, budget=budget)


def _held_watcher(bot, panes, gate, held):
    return PermissionGateWatcher(
        bot.post, _Registry({"@1": "100"}), capture=_capture(panes),
        post=bot.post, edit=bot.edit, delete=bot.delete,
        pending=lambda _wid: gate, held=lambda _tuid: held, mirror=False,
    )


def test_a_held_multi_question_gate_gets_real_answer_buttons_on_every_question():
    bot = _HookBot()
    gate = _gate(SIDEBAR, ACT_PATH, BACKFILL)
    w = _held_watcher(bot, {"@1": ASKUQ_MULTI_PANE}, gate, _held())

    w.poll(["@1"])

    assert len(bot.sent) == 3
    for i, (text, _pm, _thread, markup) in enumerate(bot.sent):
        data = [b["callback_data"] for row in markup["inline_keyboard"] for b in row]
        # Each card's buttons name THIS gate and THIS question — never a cursor move.
        assert data == [f"qa:h:toolu_01:{i}:0", f"qa:h:toolu_01:{i}:1"]
        assert "no keystrokes" in text
        # And the human is told the agent is being held, and for how long: an agent frozen
        # on a human is the whole risk of the feature, so it is not hidden from them.
        assert "held for up to 90s" in text
        assert "can't yet map a tap" not in text     # the apology is gone


def test_a_held_multiselect_question_toggles_and_offers_send():
    bot = _HookBot()
    w = _held_watcher(bot, {"@1": ASKUQ_MULTI_PANE}, _gate(MULTI_SELECT), _held())

    w.poll(["@1"])

    text, _pm, _thread, markup = bot.sent[0]
    rows = markup["inline_keyboard"]
    assert [b["text"] for b in rows[0]] == ["☐ 1", "☐ 2"]
    assert rows[-1][0]["callback_data"] == "qa:hs:toolu_01:0"
    assert "toggle" in text and "✅ Send" in text


def test_a_gate_nobody_is_holding_still_falls_back_to_the_keystroke_path():
    # The pre-plugin agent: no hook is blocked on it, so there is nothing to answer
    # through. The scraped single-select keyboard (and its cursor arithmetic) remains.
    bot = _HookBot()
    w = _held_watcher(bot, {"@1": ASKUQ_SINGLE_PANE}, _gate(FRUIT), None)

    w.poll(["@1"])

    _text, _pm, _thread, markup = bot.sent[0]
    data = [b["callback_data"] for row in markup["inline_keyboard"] for b in row]
    assert data[:2] == ["qa:0", "qa:1"]             # the legacy keystroke selector


def test_a_gate_whose_hold_EXPIRES_loses_its_answer_buttons():
    # The hook gave up and the agent is no longer listening. A button that would be
    # refused is worse than no button: the card must say "answer it in the terminal".
    bot = _HookBot()
    w = _held_watcher(bot, {"@1": ASKUQ_MULTI_PANE}, _gate(SIDEBAR, ACT_PATH), None)

    w.poll(["@1"])

    text, _pm, _thread, markup = bot.sent[0]
    data = [b["callback_data"] for row in markup["inline_keyboard"] for b in row]
    assert all(d.startswith("qa:nav:") for d in data)
    assert "Answer this one in the terminal" in text


def test_a_gate_that_becomes_held_re_renders_with_answer_buttons():
    # The pane relay can post a gate a tick before the hook's POST lands. That message
    # must GROW its buttons, not sit there un-answerable — so the hold is in the de-dup
    # signature.
    bot = _HookBot()
    gate = _gate(SIDEBAR)
    held: list = [None]
    w = PermissionGateWatcher(
        bot.post, _Registry({"@1": "100"}), capture=_capture({"@1": ASKUQ_MULTI_PANE}),
        post=bot.post, edit=bot.edit, delete=bot.delete,
        pending=lambda _wid: gate, held=lambda _tuid: held[0], mirror=False,
    )

    w.poll(["@1"])
    assert bot.edits == []
    held[0] = _held()
    w.poll(["@1"])

    assert len(bot.sent) == 1, "the same message is edited, never double-posted"
    assert len(bot.edits) == 1
    _mid, text, markup = bot.edits[0]
    assert markup["inline_keyboard"][0][0]["callback_data"] == "qa:h:toolu_01:0:0"
    assert "no keystrokes" in text
