"""`chela.release_notes` is what `.github/workflows/release.yml` calls to build a
GitHub Release's `--notes-file` — a real, tested function instead of inline
`sed`/`awk` in the workflow YAML. These tests exercise it both directly and as the
CLI the workflow actually shells out to.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from chela.release_notes import (
    ReleaseNotFoundError,
    extract_release_notes,
    latest_released_version,
)

_REPO_ROOT = Path(__file__).resolve().parent.parent
_CHANGELOG = _REPO_ROOT / "CHANGELOG.md"

_SAMPLE = """\
# Changelog

## [Unreleased]

- some unreleased line

## [2.0.0] — 2026-02-02

### Added

- second release body
- more of it

## [1.0.0] — 2026-01-01

### Added

- first release body

---

Footer note, not part of any release.

[2.0.0]: https://example.com/compare/v1.0.0...v2.0.0
"""


def test_extracts_body_between_two_headings():
    notes = extract_release_notes(_SAMPLE, "2.0.0")
    assert notes == "### Added\n\n- second release body\n- more of it\n"


def test_extracts_body_of_last_release_stopping_at_footer_rule():
    notes = extract_release_notes(_SAMPLE, "1.0.0")
    assert notes == "### Added\n\n- first release body\n"
    assert "Footer note" not in notes
    assert "---" not in notes


def test_extracts_unreleased_section():
    notes = extract_release_notes(_SAMPLE, "Unreleased")
    assert notes == "- some unreleased line\n"


def test_missing_version_raises():
    with pytest.raises(ReleaseNotFoundError):
        extract_release_notes(_SAMPLE, "9.9.9")


def test_latest_released_version_skips_unreleased():
    assert latest_released_version(_SAMPLE) == "2.0.0"


def test_latest_released_version_requires_a_dated_section():
    with pytest.raises(ReleaseNotFoundError):
        latest_released_version("## [Unreleased]\n\n- nothing shipped yet\n")


def test_real_changelog_0_3_0_section_does_not_leak_0_2_0():
    notes = extract_release_notes(_CHANGELOG.read_text(), "0.3.0")
    assert "Honest self-reporting" in notes
    assert "macOS-onboarding and fleet-safety push" not in notes


def test_real_changelog_0_2_0_section_does_not_leak_0_3_0_or_footer():
    notes = extract_release_notes(_CHANGELOG.read_text(), "0.2.0")
    assert "macOS support" in notes
    assert "Honest self-reporting" not in notes
    assert "This file is the source of the release notes" not in notes


def _run_cli(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "chela.release_notes", *args],
        cwd=_REPO_ROOT,
        capture_output=True,
        text=True,
    )


def test_cli_prints_notes_for_a_real_version():
    result = _run_cli("0.3.0")
    assert result.returncode == 0
    assert "Honest self-reporting" in result.stdout


def test_cli_strips_a_leading_v():
    result = _run_cli("v0.3.0")
    assert result.returncode == 0
    assert "Honest self-reporting" in result.stdout


def test_cli_exits_nonzero_on_unknown_version():
    result = _run_cli("9.9.9")
    assert result.returncode == 1
    assert "no '## [9.9.9]'" in result.stderr
