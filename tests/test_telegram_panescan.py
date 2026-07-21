"""Pane detectors — the sole TUI-regex module (Slice C1 permission + A2 AskUQ).

Locks in that :func:`detect_permission_gate` recognises the permission-prompt and
bash-approval TUI regions, and that :func:`detect_askuserquestion` recognises the
AskUserQuestion selector (question text + ordered options + cursor ordinal, with
the multi-tab / multi-select fallback), while both return None for a normal pane.
The exact snippets keyed on are recorded here as the version-canary: a Claude Code
reword that breaks these is a one-file edit in ``panescan.py``.
"""
from __future__ import annotations

from chela.telegram.panescan import (
    detect_askuserquestion,
    detect_exitplanmode,
    detect_permission_gate,
    scrape_gate_identity,
)

# A Bash permission prompt: "Do you want to proceed?" framed by "Esc to cancel".
PERMISSION_PANE = """\
 Bash command
   rm -rf build/
   Remove the build directory

 Do you want to proceed?
 ❯ 1. Yes
   2. Yes, and don't ask again for rm commands in this project
   3. No, and tell Claude what to do differently (esc)

 Esc to cancel
"""

# An edit permission prompt (different top wording, same footer).
EDIT_PERMISSION_PANE = """\
 Edit file
   src/app.py

 Do you want to make this edit to app.py?
 ❯ 1. Yes
   2. Yes, allow all edits during this session
   3. No, and tell Claude what to do differently (esc)

 Esc to cancel
"""

# A numbered permission menu with no "Esc to cancel" footer.
MENU_PANE = """\
 Some action is about to happen

 ❯ 1. Yes
   2. Yes, always
   3. No
"""

# A bash-approval block with no "Do you want to proceed?" line.
BASH_APPROVAL_PANE = """\
 This command requires approval
   npm run deploy

 Approve running this command?

 Esc to cancel
"""

# A normal working pane — no gate.
WORKING_PANE = """\
● I'll read the config file now.

● Read(config.py)
  ⎿ Read 42 lines

────────────────────────────────────────
 ❯
────────────────────────────────────────
  [Opus 4.8] Context: 34%
"""


def test_detects_bash_permission_prompt():
    gate = detect_permission_gate(PERMISSION_PANE)
    assert gate is not None
    assert gate.kind == "PermissionPrompt"
    assert "Do you want to proceed?" in gate.text
    assert "Esc to cancel" in gate.text


def test_gate_carries_the_scraped_command_identity():
    # The gate's identity comes from the pane (Slice C2): while the gate is pending
    # the gated tool_use is NOT in the transcript, so the dialog's own header is the
    # only place the command exists.
    gate = detect_permission_gate(PERMISSION_PANE)
    assert gate.tool == "Bash"
    assert gate.detail.startswith("rm -rf build/")


def test_gate_identity_from_the_edit_dialog_header():
    gate = detect_permission_gate(EDIT_PERMISSION_PANE)
    assert (gate.tool, gate.detail) == ("Edit", "src/app.py")


def test_gate_identity_from_the_prompt_when_there_is_no_header():
    # No "Edit file" header — the prompt itself names the file.
    tool, detail = scrape_gate_identity(
        " Do you want to make this edit to app.py?\n ❯ 1. Yes\n\n Esc to cancel\n"
    )
    assert (tool, detail) == ("Edit", "app.py")


def test_gate_identity_is_none_for_an_unrecognised_dialog():
    # The relay then falls back to the scraped region text — never a crash.
    assert scrape_gate_identity(MENU_PANE) == (None, None)


def test_detects_edit_permission_prompt():
    gate = detect_permission_gate(EDIT_PERMISSION_PANE)
    assert gate is not None
    assert gate.kind == "PermissionPrompt"
    assert "Do you want to make this edit" in gate.text


def test_detects_numbered_menu_without_footer():
    gate = detect_permission_gate(MENU_PANE)
    assert gate is not None
    assert gate.kind == "PermissionPrompt"
    assert "1. Yes" in gate.text


def test_detects_bash_approval_block():
    gate = detect_permission_gate(BASH_APPROVAL_PANE)
    assert gate is not None
    assert gate.kind == "BashApproval"
    assert "requires approval" in gate.text


def test_normal_pane_is_not_a_gate():
    assert detect_permission_gate(WORKING_PANE) is None


def test_empty_pane_is_none():
    assert detect_permission_gate("") is None
    assert detect_permission_gate("   \n  \n") is None


# ── AskUserQuestion selector (Slice A2) ──────────────────────────────────────
#
# The exact snippets keyed on, captured live from Claude Code 2.1.207.

# Single question, single-select: checkbox header, question, ❯-cursored options,
# the free-text / chat meta-rows, then the "Enter to select" footer.
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

# The same selector after one ↓ press — the ❯ cursor has moved to option 2.
ASKUQ_SINGLE_PANE_CURSOR_ON_2 = """\
 ☐ Fruit

Which fruit do you prefer?

  1. Apple
     A crisp red fruit
❯ 2. Banana
     A soft yellow fruit
  3. Cherry
  4. Type something.
─────
  5. Chat about this

Enter to select · ↑/↓ to navigate · Esc to cancel
"""

# Multi-question (or multi-select): the ``←  ☐ … →`` tab strip → fallback shape.
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


def test_single_select_parses_question_options_and_cursor():
    uq = detect_askuserquestion(ASKUQ_SINGLE_PANE)
    assert uq is not None
    assert uq.multi is False
    assert uq.question == "Which fruit do you prefer?"
    # Only the real options — the "Type something." / "Chat about this" meta-rows
    # are dropped.
    assert uq.options == ("Apple", "Banana", "Cherry")
    assert uq.cursor == 0  # ❯ on option 1 (Apple) at render


def test_single_select_tracks_a_moved_cursor():
    uq = detect_askuserquestion(ASKUQ_SINGLE_PANE_CURSOR_ON_2)
    assert uq is not None
    assert uq.options == ("Apple", "Banana", "Cherry")
    assert uq.cursor == 1  # ❯ moved to option 2 (Banana)


def test_single_select_scrapes_a_description_for_every_option():
    uq = detect_askuserquestion(ASKUQ_SINGLE_PANE)
    assert uq is not None
    # Positionally parallel to `options` — Claude Code renders EVERY option's
    # description, not just the cursor-focused one (CMX-32, measured live).
    assert uq.descriptions == (
        "A crisp red fruit",
        "A soft yellow fruit",
        "A small red fruit",
    )


# Captured live (Claude Code 2.1.207, CMX-32) — a 4-option selector with long
# labels and multi-line WRAPPED descriptions, the shape that made the question
# unanswerable when the options only existed as button captions.
ASKUQ_LONG_PANE = """\
 ☐ History

Which repo-history strategy should we use?

❯ 1. Squash the entire branch into a single commit before merging into main
     Every change on the branch collapses into one commit with one message. Main stays extremely clean and
     each merge maps to exactly one logical unit of work.
  2. Rebase the branch onto main and preserve every individual commit as-is
     History stays linear and every commit is retained, so bisect can pinpoint the exact commit that broke
     something.
  3. Merge with a true merge commit and keep the full branch topology intact
     Nothing is rewritten: the branch's real shape, timing, and parallel work are all preserved.
  4. Type something.
─────
  5. Chat about this

Enter to select · ↑/↓ to navigate · Esc to cancel
"""


def test_long_option_descriptions_are_unwrapped_and_stay_with_their_option():
    uq = detect_askuserquestion(ASKUQ_LONG_PANE)
    assert uq is not None
    assert uq.options[0].startswith("Squash the entire branch")
    assert len(uq.options) == 3  # meta-rows excluded
    # A description wrapped across pane lines is rejoined into one string …
    assert uq.descriptions[0].startswith("Every change on the branch collapses")
    assert uq.descriptions[0].endswith("one logical unit of work.")
    # … and never bleeds into the next option (nor does the ───── rule).
    assert "Rebase" not in uq.descriptions[0]
    assert "─" not in uq.descriptions[2]
    assert uq.descriptions[2].startswith("Nothing is rewritten")


def test_multi_tab_selector_is_the_fallback_shape():
    uq = detect_askuserquestion(ASKUQ_MULTI_PANE)
    assert uq is not None
    assert uq.multi is True          # multi-tab → nav-only fallback
    assert uq.options == ()          # no semantic buttons
    assert uq.question == "Which fruit do you prefer?"


def test_permission_and_working_panes_are_not_selectors():
    assert detect_askuserquestion(PERMISSION_PANE) is None
    assert detect_askuserquestion(WORKING_PANE) is None
    assert detect_askuserquestion(MENU_PANE) is None


def test_askuq_empty_pane_is_none():
    assert detect_askuserquestion("") is None
    assert detect_askuserquestion("   \n  \n") is None


# ── ExitPlanMode plan-approval selector (Slice B2) ───────────────────────────
#
# The plan-approval prompt ("Would you like to proceed?" with the Yes-auto /
# Yes-manual / No choices) is a live-pane UI whose tool_use lands only at
# answer-time — so it is scraped from the pane exactly like AskUserQuestion.

# A plan approval: the plan text, then the proceed prompt + numbered choices,
# then the "Esc to cancel" footer. The plan is ABOVE the options (the options are
# carried by the buttons, so they're not part of the scraped body).
EXITPLAN_PANE = """\
● Here is my plan:

  1. Add detect_exitplanmode to panescan.py
  2. Wire the pane trigger into gatewatch
  3. Suppress the transcript double-relay

 Would you like to proceed?
 ❯ 1. Yes, and auto-accept edits
   2. Yes, and manually approve edits
   3. No, keep planning

 Esc to cancel
"""

# The v2.1.29+ wording where the prompt wraps ("Claude has written up a plan …").
EXITPLAN_WRAPPED_PANE = """\
● Plan:

  Refactor the parser and add tests.

 Claude has written up a plan. Would you like to proceed?
 ❯ 1. Yes, and auto-accept edits
   2. No, keep planning

 ctrl-g to edit in $EDITOR
"""


def test_detects_exitplanmode_and_scrapes_the_plan_region():
    plan = detect_exitplanmode(EXITPLAN_PANE)
    assert plan is not None
    # The plan text above the options is scraped as the body …
    assert "Here is my plan:" in plan.text
    assert "Add detect_exitplanmode to panescan.py" in plan.text
    # … while the proceed prompt and the numbered options are NOT (the buttons
    # carry the choices, so they'd be redundant in the body).
    assert "Would you like to proceed?" not in plan.text
    assert "auto-accept edits" not in plan.text
    assert "Esc to cancel" not in plan.text


def test_detects_exitplanmode_wrapped_prompt_variant():
    plan = detect_exitplanmode(EXITPLAN_WRAPPED_PANE)
    assert plan is not None
    assert "Refactor the parser" in plan.text
    assert "Would you like to proceed?" not in plan.text


def test_exitplanmode_permission_and_askuq_panes_are_not_plans():
    # A permission prompt says "Do you want to proceed?" (not "Would you like…"),
    # an AskUserQuestion has no proceed prompt at all, a normal pane neither.
    assert detect_exitplanmode(PERMISSION_PANE) is None
    assert detect_exitplanmode(ASKUQ_SINGLE_PANE) is None
    assert detect_exitplanmode(ASKUQ_MULTI_PANE) is None
    assert detect_exitplanmode(WORKING_PANE) is None
    assert detect_exitplanmode(MENU_PANE) is None


def test_exitplanmode_empty_pane_is_none():
    assert detect_exitplanmode("") is None
    assert detect_exitplanmode("   \n  \n") is None
