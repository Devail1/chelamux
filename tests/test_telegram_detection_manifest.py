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
from pathlib import Path

import pytest

from chela.telegram import detection_manifest
from chela.telegram.detection_manifest import ManifestError
from chela.telegram.panescan import (
    detect_dialog,
    detect_exitplanmode,
    detect_permission_gate,
    detect_status,
)

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


def test_unreadable_override_file_falls_back_to_bundled(monkeypatch, caplog):
    """A present, regular file that raises OSError on read (a chmod-000 file, an I/O
    error) is the exact case `load()`'s ``except (OSError, ...)`` exists for — distinct
    from the directory case above, which never reaches that except at all because
    ``override.is_file()`` is already False. Simulated via monkeypatch (not chmod) so
    this passes the same whether the test runs as root or not."""
    override = detection_manifest.override_manifest_path()
    override.parent.mkdir(parents=True, exist_ok=True)
    override.write_text("placeholder", encoding="utf-8")
    detection_manifest._reset_cache_for_tests()

    real_read_text = Path.read_text

    def broken_read_text(self, *args, **kwargs):
        if self == override:
            raise OSError("simulated I/O error")
        return real_read_text(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", broken_read_text)

    with caplog.at_level(logging.ERROR, logger="chela.telegram.detection_manifest"):
        m = detection_manifest.load()

    assert m.patterns == detection_manifest.load_bundled().patterns
    errors = [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert errors, "an unreadable override must log LOUDLY, not fail silently"
    assert str(override) in errors[0].message


def test_bad_regex_in_pattern_is_rejected_with_manifest_error(tmp_path):
    """``re.error`` (NOT a ``ValueError``) must be converted to ``ManifestError`` inside
    ``_compile_all`` — if it escapes uncaught, it also escapes ``load()``'s except
    tuple and every pane scan raises instead of falling back to bundled."""
    bad = tmp_path / "bad.toml"
    bad.write_text(
        '[status]\nspinners = ["."]\nactive_marker = "…"\n\n'
        '[[pattern]]\nname = "BadRegex"\ntables = ["dialog"]\n'
        "top = ['(unclosed']\nbottom = []\n",
        encoding="utf-8",
    )
    with pytest.raises(ManifestError, match="regex"):
        detection_manifest.load_file(bad)


def test_missing_pattern_key_is_rejected_with_manifest_error(tmp_path):
    bad = tmp_path / "bad.toml"
    bad.write_text(
        '[status]\nspinners = ["."]\nactive_marker = "…"\n\n'
        '[[pattern]]\nname = "NoTop"\ntables = ["dialog"]\n',
        encoding="utf-8",
    )
    with pytest.raises(ManifestError, match="top"):
        detection_manifest.load_file(bad)


def test_manifest_with_zero_pattern_entries_is_rejected_with_manifest_error(tmp_path):
    """Accepting a manifest with no ``[[pattern]]`` entries at all is exactly the
    'silently degrade to no patterns' outcome the module docstring forbids (CMX-337 /
    issue #434). ``pattern = []`` (a plain empty array, not an omitted key) is what
    exercises the ``not entries`` check in ``_parse`` rather than the KeyError path."""
    bad = tmp_path / "bad.toml"
    bad.write_text(
        # `pattern = []` must come BEFORE the `[status]` table header — once inside
        # `[status]`, a bare `pattern = []` line would set `status.pattern`, not the
        # root-level `pattern` key `_parse` actually reads via `raw["pattern"]`.
        'pattern = []\n[status]\nspinners = ["."]\nactive_marker = "…"\n',
        encoding="utf-8",
    )
    with pytest.raises(ManifestError, match="pattern"):
        detection_manifest.load_file(bad)


def test_pattern_with_empty_name_is_rejected_with_manifest_error(tmp_path):
    bad = tmp_path / "bad.toml"
    bad.write_text(
        '[status]\nspinners = ["."]\nactive_marker = "…"\n\n'
        '[[pattern]]\nname = ""\ntables = ["dialog"]\n'
        "top = ['^\\s*X']\nbottom = []\n",
        encoding="utf-8",
    )
    with pytest.raises(ManifestError, match="name"):
        detection_manifest.load_file(bad)


def test_dialog_patterns_and_gate_patterns_filter_by_table_membership(tmp_path):
    """``dialog_patterns``/``gate_patterns`` must filter :attr:`Manifest.patterns` by
    table membership, not merely return the whole list in declaration order — the
    bundled manifest can't catch a corruption that returns ``self.patterns`` verbatim
    from ``dialog_patterns`` because every one of its nine entries happens to carry
    "dialog" (see docs/defeat_shapes/306). This manifest has a gate-ONLY entry that
    must be absent from ``dialog_patterns``."""
    manifest_toml = tmp_path / "manifest.toml"
    manifest_toml.write_text(
        '[status]\nspinners = ["."]\nactive_marker = "…"\n\n'
        '[[pattern]]\nname = "GateOnly"\ntables = ["gate"]\n'
        "top = ['^\\s*GATE_ONLY']\nbottom = []\nmin_gap = 1\n\n"
        '[[pattern]]\nname = "DialogOnly"\ntables = ["dialog"]\n'
        "top = ['^\\s*DIALOG_ONLY']\nbottom = []\nmin_gap = 1\n\n"
        '[[pattern]]\nname = "Both"\ntables = ["gate", "dialog"]\n'
        "top = ['^\\s*BOTH']\nbottom = []\nmin_gap = 1\n",
        encoding="utf-8",
    )
    m = detection_manifest.load_file(manifest_toml)

    assert [p.name for p in m.gate_patterns] == ["GateOnly", "Both"]
    assert [p.name for p in m.dialog_patterns] == ["DialogOnly", "Both"]


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


# ── Wiring guards — the call sites must READ the manifest, never copy its values ──
#
# Each guard below uses an override whose value DIFFERS from the bundled default, so a
# call site that quietly reverted to a hardcoded literal (which necessarily matches
# *today's* bundled value, since it was copied from it) is distinguishable from one that
# actually reads `manifest.spinners` / `manifest.active_marker` / the pattern's own
# `top` regexes on every call. See docs/defeat_shapes/303 for why a value that merely
# equals the bundled default is not evidence a call site reads it live.


def _write_override(text: str) -> None:
    override = detection_manifest.override_manifest_path()
    override.parent.mkdir(parents=True, exist_ok=True)
    override.write_text(text, encoding="utf-8")
    detection_manifest._reset_cache_for_tests()


def test_status_spinners_come_from_the_manifest_not_a_hardcoded_copy():
    _write_override(
        '[status]\nspinners = ["#"]\nactive_marker = "…"\n\n'
        '[[pattern]]\nname = "Dummy"\ntables = ["dialog"]\n'
        'top = [\'^\\s*DUMMY_MARKER\']\nbottom = []\nmin_gap = 1\n'
    )

    pane = (
        "some prior output\n\n# Cerebrating… (5s)\n"
        + "─" * 40
        + "\n> \n"
        + "─" * 40
        + "\n"
    )
    status = detect_status(pane)
    assert status is not None and status.active is True

    # A hardcoded copy of the bundled glyph set ('·✻✽✶✳✢') would still classify the
    # bundled corpus's own spinner as working under this override — it must not.
    assert detect_status(WORKING_PANE) is None


def test_status_active_marker_comes_from_the_manifest_not_a_hardcoded_copy():
    _write_override(
        '[status]\nspinners = ["·"]\nactive_marker = "LIVE"\n\n'
        '[[pattern]]\nname = "Dummy"\ntables = ["dialog"]\n'
        'top = [\'^\\s*DUMMY_MARKER\']\nbottom = []\nmin_gap = 1\n'
    )

    pane = (
        "some prior output\n\n· Cerebrating LIVE (5s)\n"
        + "─" * 40
        + "\n> \n"
        + "─" * 40
        + "\n"
    )
    status = detect_status(pane)
    assert status is not None and status.active is True

    # The bundled ellipsis is not this override's marker — a hardcoded copy of "…"
    # would still call the bundled corpus's own working line "active"; it must not.
    settled = detect_status(WORKING_PANE)
    assert settled is not None and settled.active is False


def test_exitplanmode_top_markers_come_from_the_manifest_not_hardcoded_regexes():
    _write_override(
        '[status]\nspinners = ["·"]\nactive_marker = "…"\n\n'
        '[[pattern]]\nname = "ExitPlanMode"\ntables = ["dialog"]\n'
        'top = [\'^\\s*CUSTOM_PROCEED_MARKER\']\n'
        'bottom = [\'^\\s*Esc to cancel\']\nmin_gap = 1\n'
    )

    pane = (
        "● Here is my plan:\n\n"
        "  1. Do a thing\n\n"
        " CUSTOM_PROCEED_MARKER\n"
        " ❯ 1. Yes\n\n"
        " Esc to cancel\n"
    )
    plan = detect_exitplanmode(pane)
    assert plan is not None
    assert "Here is my plan" in plan.text

    # The bundled wording ("Would you like to proceed?") is not this override's top
    # marker — a hardcoded copy of it would still fire here; it must not.
    bundled_wording_pane = " Would you like to proceed?\n ❯ 1. Yes\n\n Esc to cancel\n"
    assert detect_exitplanmode(bundled_wording_pane) is None


def test_exitplanmode_bottom_markers_come_from_the_manifest_not_hardcoded_regexes():
    """Isolates ``plan_pattern.bottom`` specifically: top and min_gap are left at
    values a hardcoded call site would also satisfy, so only the bottom marker can
    explain a difference. ``'^\\s*Esc to cancel'`` is deliberately NOT used as the
    override's bottom (the bundled bottom regex ``'^\\s*Esc to (cancel|exit)'`` also
    matches it, so it can't distinguish wired from hardcoded — see the round-2 note
    on defeat shape 303)."""
    _write_override(
        '[status]\nspinners = ["·"]\nactive_marker = "…"\n\n'
        '[[pattern]]\nname = "ExitPlanMode"\ntables = ["dialog"]\n'
        'top = [\'^\\s*Would you like to proceed\\?\']\n'
        'bottom = [\'^\\s*CUSTOM_BOTTOM_MARKER\']\nmin_gap = 1\n'
    )

    pane = (
        "● Here is my plan:\n\n"
        "  1. Do a thing\n\n"
        " Would you like to proceed?\n"
        " ❯ 1. Yes\n\n"
        " CUSTOM_BOTTOM_MARKER\n"
    )
    plan = detect_exitplanmode(pane)
    assert plan is not None
    assert "Here is my plan" in plan.text

    # The bundled bottom wording ("Esc to cancel") is not this override's bottom
    # marker — a hardcoded copy of it would still find a bottom line here and return
    # a match; it must not, since this override never matches "Esc to cancel".
    bundled_bottom_pane = (
        " Would you like to proceed?\n"
        " ❯ 1. Yes\n\n"
        " Esc to cancel\n"
    )
    assert detect_exitplanmode(bundled_bottom_pane) is None


def test_exitplanmode_min_gap_comes_from_the_manifest_not_the_bundled_value():
    """Isolates ``plan_pattern.min_gap``: top and bottom are the bundled wording (so a
    hardcoded call site's marker regexes also match), and only the gap threshold
    differs from the bundled default of 2."""
    _write_override(
        '[status]\nspinners = ["·"]\nactive_marker = "…"\n\n'
        '[[pattern]]\nname = "ExitPlanMode"\ntables = ["dialog"]\n'
        'top = [\'^\\s*Would you like to proceed\\?\']\n'
        'bottom = [\'^\\s*Esc to cancel\']\nmin_gap = 5\n'
    )

    # Gap of 3 lines: satisfies the bundled default (min_gap=2) but not this
    # override's min_gap=5 — a call site still reading the bundled/hardcoded value
    # would accept this pane; the wired call site must reject it.
    pane = " Would you like to proceed?\n ❯ 1. Yes\n\n Esc to cancel\n"
    assert detect_exitplanmode(pane) is None

    # A gap of 5 lines satisfies this override's min_gap=5 — proves the marker
    # regexes themselves still match and only the gap check differed above.
    wide_pane = (
        " Would you like to proceed?\n"
        " line 1\n line 2\n line 3\n line 4\n"
        " Esc to cancel\n"
    )
    assert detect_exitplanmode(wide_pane) is not None


def test_exitplanmode_returns_none_when_manifest_defines_no_exitplanmode_pattern():
    """No existing fixture ever loads a manifest that omits 'ExitPlanMode' entirely,
    so the early `if plan_pattern is None: return None` guard has never been driven —
    dead-coding it is invisible unless a manifest without that pattern is exercised."""
    _write_override(
        '[status]\nspinners = ["·"]\nactive_marker = "…"\n\n'
        '[[pattern]]\nname = "SomethingElse"\ntables = ["dialog"]\n'
        'top = [\'^\\s*SOME_OTHER_MARKER\']\nbottom = []\nmin_gap = 1\n'
    )

    pane = (
        "● Here is my plan:\n\n"
        "  1. Do a thing\n\n"
        " Would you like to proceed?\n"
        " ❯ 1. Yes, and auto-accept edits\n\n"
        " Esc to cancel\n"
    )
    assert detect_exitplanmode(pane) is None


def test_manifest_pattern_selects_by_name_not_by_position():
    """`.pattern(name)` must return the entry whose ``name`` matches — not whichever
    entry happens to be first. The bundled manifest's own patterns can't catch a
    positional lookup because 'ExitPlanMode' already IS the first entry there; this
    override reorders a different-named pattern in front of it."""
    _write_override(
        '[status]\nspinners = ["·"]\nactive_marker = "…"\n\n'
        '[[pattern]]\nname = "FirstOne"\ntables = ["dialog"]\n'
        'top = [\'^\\s*FIRST_MARKER\']\nbottom = []\nmin_gap = 1\n\n'
        '[[pattern]]\nname = "ExitPlanMode"\ntables = ["dialog"]\n'
        'top = [\'^\\s*Would you like to proceed\\?\']\n'
        'bottom = [\'^\\s*Esc to cancel\']\nmin_gap = 2\n'
    )

    p = detection_manifest.load().pattern("ExitPlanMode")
    assert p is not None
    assert p.name == "ExitPlanMode"


def test_pattern_with_invalid_tables_value_is_rejected(tmp_path):
    bad = tmp_path / "bad.toml"
    bad.write_text(
        '[status]\nspinners = ["."]\nactive_marker = "…"\n\n'
        '[[pattern]]\nname = "Typo"\ntables = ["dialogue"]\n'
        "top = ['^\\s*X']\nbottom = []\n",
        encoding="utf-8",
    )
    with pytest.raises(ManifestError, match="tables"):
        detection_manifest.load_file(bad)


def test_empty_spinners_list_is_rejected_with_manifest_error(tmp_path):
    bad = tmp_path / "bad.toml"
    bad.write_text(
        '[status]\nspinners = []\nactive_marker = "…"\n\n'
        '[[pattern]]\nname = "X"\ntables = ["dialog"]\n'
        "top = ['^\\s*X']\nbottom = []\n",
        encoding="utf-8",
    )
    with pytest.raises(ManifestError, match="spinners"):
        detection_manifest.load_file(bad)


def test_empty_active_marker_is_rejected_with_manifest_error(tmp_path):
    bad = tmp_path / "bad.toml"
    bad.write_text(
        '[status]\nspinners = ["."]\nactive_marker = ""\n\n'
        '[[pattern]]\nname = "X"\ntables = ["dialog"]\n'
        "top = ['^\\s*X']\nbottom = []\n",
        encoding="utf-8",
    )
    with pytest.raises(ManifestError, match="active_marker"):
        detection_manifest.load_file(bad)


def test_pattern_omitting_min_gap_defaults_to_two(tmp_path):
    """The default carried over from the deleted ``_UIPattern`` dataclass — the only
    value an override entry that omits ``min_gap`` gets. Every existing fixture sets
    ``min_gap`` explicitly, so this default was never independently exercised."""
    ok = tmp_path / "ok.toml"
    ok.write_text(
        '[status]\nspinners = ["."]\nactive_marker = "…"\n\n'
        '[[pattern]]\nname = "NoMinGap"\ntables = ["dialog"]\n'
        "top = ['^\\s*X']\nbottom = []\n",
        encoding="utf-8",
    )
    p = detection_manifest.load_file(ok).pattern("NoMinGap")
    assert p is not None
    assert p.min_gap == 2
