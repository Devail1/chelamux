"""Permission-gate pane detector — the sole TUI-regex module (Slice C1).

Locks in that :func:`detect_permission_gate` recognises the permission-prompt and
bash-approval TUI regions (which never reach the JSONL transcript) and returns
None for a normal/working pane. The exact snippets keyed on are recorded here as
the version-canary: a Claude Code reword that breaks these is a one-file edit in
``panescan.py``.
"""
from __future__ import annotations

from chela.telegram.panescan import detect_permission_gate

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
