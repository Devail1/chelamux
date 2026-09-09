"""Guards for issue #458 — the TUI signatures table as DATA, not hardcoded regex.

``chela/telegram/panescan.py`` now reads its top/bottom marker patterns and status
spinner glyphs from ``chela/telegram/detection_manifest.toml`` (or a local override at
``$CHELA_DIR/agent-detection/claude-code.toml``) via
:mod:`chela.telegram.detection_manifest`, instead of hardcoding them. The counterweight
this file locks in:

* with NO override present, a pinned corpus of real captured panes classifies exactly
  as it did before the extraction (dropping any one pattern from the manifest must
  flip at least one of these assertions to RED);
* a malformed/unreadable override fails LOUDLY (an ERROR log) and falls back to the
  bundled default — never a silent "no patterns" (the CMX-337/#434 shape);
* an override genuinely overrides — a synthetic pattern it adds is detected; and
* a manifest edit takes effect on the next call, no ``chela-telegram`` restart.
"""
from __future__ import annotations

import logging
import os

import pytest

from chela.telegram import detection_manifest
from chela.telegram.detection_manifest import ManifestError
from chela.telegram.panescan import detect_dialog, detect_permission_gate, detect_status

# ── The pinned corpus — real captured-pane shapes, one per manifest pattern kind ──

# No "Do you want to proceed?" line, so this is distinguishable from PermissionPrompt
# (which is tried first) — only "This command requires approval" fires BashApproval.
BASH_APPROVAL_PANE = """\
 This command requires approval
   npm run deploy

 Approve running this command?

 Esc to cancel
"""

RESTORE_CHECKPOINT_PANE = """\
 Restore the code to before this change?
   3 files changed

 Enter to continue
"""

IDLE_PANE = "some prior output\n\n" + "─" * 40 + "\n> \n" + "─" * 40 + "\n"

WORKING_PANE = (
    "some prior output\n\n· Cerebrating… (2m 45s · ↓ 12.0k tokens)\n"
    + "─" * 40
    + "\n> \n"
    + "─" * 40
    + "\n"
)


def test_bundled_manifest_reproduces_todays_gate_and_dialog_order():
    """The exact tables ``panescan.py`` used to hardcode, byte-for-byte."""
    m = detection_manifest.load_bundled()
    assert [p.name for p in m.gate_patterns] == [
        "PermissionPrompt",
        "PermissionPrompt",
        "BashApproval",
    ]
    assert [p.name for p in m.dialog_patterns] == [
        "ExitPlanMode",
        "AskUserQuestion",
        "AskUserQuestion",
        "ToolApproval",
        "PermissionPrompt",
        "PermissionPrompt",
        "BashApproval",
        "RestoreCheckpoint",
        "Settings",
    ]
    assert sorted(m.spinners) == sorted("·✻✽✶✳✢")
    assert m.active_marker == "…"


def test_no_override_present_pinned_corpus_classifies_unchanged():
    """No override file at all ⇒ bundled applies ⇒ classification is exactly today's.

    Dropping any single ``[[pattern]]`` entry from the bundled manifest must flip one
    of these assertions to RED (the corruption this guard exists to catch).
    """
    assert detection_manifest.override_manifest_path().is_file() is False

    gate = detect_permission_gate(BASH_APPROVAL_PANE)
    assert gate is not None and gate.kind == "BashApproval"

    dialog = detect_dialog(RESTORE_CHECKPOINT_PANE)
    assert dialog is not None and dialog.name == "RestoreCheckpoint"

    assert detect_status(IDLE_PANE) is None

    working = detect_status(WORKING_PANE)
    assert working is not None and working.active is True


def test_malformed_override_fails_loudly_and_falls_back_to_bundled(caplog):
    override = detection_manifest.override_manifest_path()
    override.parent.mkdir(parents=True, exist_ok=True)
    override.write_text("this is not [ valid toml", encoding="utf-8")
    detection_manifest._reset_cache_for_tests()

    with caplog.at_level(logging.ERROR, logger="chela.telegram.detection_manifest"):
        m = detection_manifest.load()

    assert m.patterns == detection_manifest.load_bundled().patterns
    errors = [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert errors, "a malformed override must log LOUDLY, not fail silently"
    assert str(override) in errors[0].message


def test_unreadable_override_directory_falls_back_to_bundled(caplog):
    """A path that exists but isn't a regular file (can't be parsed) is the same
    'present but broken' case as bad TOML — same loud-fallback contract."""
    override = detection_manifest.override_manifest_path()
    override.mkdir(parents=True)  # a directory, not a file, at the manifest's path
    detection_manifest._reset_cache_for_tests()

    m = detection_manifest.load()

    assert m.patterns == detection_manifest.load_bundled().patterns


def test_missing_pattern_key_is_rejected_with_manifest_error(tmp_path):
    bad = tmp_path / "bad.toml"
    bad.write_text(
        '[status]\nspinners = ["."]\nactive_marker = "…"\n\n'
        '[[pattern]]\nname = "NoTop"\ntables = ["dialog"]\n',
        encoding="utf-8",
    )
    with pytest.raises(ManifestError, match="top"):
        detection_manifest.load_file(bad)


def test_override_genuinely_overrides_a_synthetic_prompt_is_detected():
    """An override adding a brand-new pattern makes chela detect a prompt shape the
    bundled default has never seen — proof the override is live, not decorative.

    Corrupting the resolution to ignore the override (serve bundled regardless of its
    presence) flips this to RED.
    """
    bundled = detection_manifest.load_bundled().source.read_text(encoding="utf-8")
    synthetic = bundled + (
        '\n[[pattern]]\n'
        'name = "SyntheticTestPrompt"\n'
        'tables = ["dialog"]\n'
        'top = [\'^\\s*SYNTHETIC_TEST_PROMPT_MARKER\']\n'
        'bottom = []\n'
        'min_gap = 1\n'
    )
    override = detection_manifest.override_manifest_path()
    override.parent.mkdir(parents=True, exist_ok=True)
    override.write_text(synthetic, encoding="utf-8")
    detection_manifest._reset_cache_for_tests()

    pane = "SYNTHETIC_TEST_PROMPT_MARKER\nsome detail\nmore detail\n"
    dialog = detect_dialog(pane)

    assert dialog is not None
    assert dialog.name == "SyntheticTestPrompt"

    # And the bundled patterns still work through the (full-replacement) override,
    # since it was built as bundled-plus-one.
    gate = detect_permission_gate(BASH_APPROVAL_PANE)
    assert gate is not None and gate.kind == "BashApproval"


def test_override_edit_takes_effect_without_a_restart():
    """A manifest edit is picked up on the next call — no process restart, no
    explicit reload command. Simulated here as: write v1, detect; rewrite as v2 (a
    stricter min_gap that the same pane no longer satisfies), detect again — same
    process, same loaded module, no restart in between."""
    override = detection_manifest.override_manifest_path()
    override.parent.mkdir(parents=True, exist_ok=True)

    v1 = (
        '[status]\nspinners = ["."]\nactive_marker = "…"\n\n'
        '[[pattern]]\nname = "Reloadable"\ntables = ["dialog"]\n'
        'top = [\'^\\s*RELOAD_MARKER\']\nbottom = []\nmin_gap = 1\n'
    )
    override.write_text(v1, encoding="utf-8")
    detection_manifest._reset_cache_for_tests()
    pane = "RELOAD_MARKER\nbody line\nanother body line\n"
    assert detect_dialog(pane) is not None

    v2 = v1.replace("min_gap = 1", "min_gap = 50")
    override.write_text(v2, encoding="utf-8")
    # Force a distinct mtime — some filesystems have coarse (1s) mtime resolution,
    # and a same-tick rewrite must still be observed as a change on the NEXT load(),
    # with no cache reset call and no restart.
    stat = override.stat()
    os.utime(override, (stat.st_atime, stat.st_mtime + 5))

    assert detect_dialog(pane) is None, "manifest edit was not picked up without a restart"
