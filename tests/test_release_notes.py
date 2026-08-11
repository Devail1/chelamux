"""`chela.release_notes` is what `.github/workflows/release.yml` calls to build a
GitHub Release's `--notes-file` — a real, tested function instead of inline
`sed`/`awk` in the workflow YAML. These tests exercise it both directly and as the
CLI the workflow actually shells out to.
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import pytest

from chela.release_notes import (
    ReleaseNotFoundError,
    UnrecognisedHeadingError,
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


# Keep a Changelog — the spec CHANGELOG.md itself links to — dates headings
# with a plain ASCII hyphen (`## [1.0.0] - 2017-06-20`), not this project's
# em dash. A contributor following that spec must still parse correctly.
_HYPHEN_SAMPLE = """\
# Changelog

## [Unreleased]

- some unreleased line

## [2.0.0] - 2026-02-02

### Added

- second release body

## [1.0.0] - 2026-01-01

### Added

- first release body
"""


def test_extracts_body_of_a_hyphen_dated_heading():
    notes = extract_release_notes(_HYPHEN_SAMPLE, "2.0.0")
    assert notes == "### Added\n\n- second release body\n"


def test_latest_released_version_finds_a_hyphen_dated_newest_release():
    assert latest_released_version(_HYPHEN_SAMPLE) == "2.0.0"


def test_heading_with_unrecognised_separator_raises_instead_of_vanishing():
    corrupted = _HYPHEN_SAMPLE.replace("## [2.0.0] - 2026-02-02", "## [2.0.0] (final)")
    with pytest.raises(UnrecognisedHeadingError):
        extract_release_notes(corrupted, "1.0.0")
    with pytest.raises(UnrecognisedHeadingError):
        latest_released_version(corrupted)


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


# Parallel worktree agents each append their own `### Added`/`### Changed`/
# `### Fixed` subsection under `## [Unreleased]`, blind to each other's
# concurrent edits, so the same category heading can land in the file two or
# three times before a release ships.
_DUPLICATE_HEADINGS_SAMPLE = """\
# Changelog

## [Unreleased]

### Fixed

- first fixed item

### Changed

- first changed item

### Fixed

- second fixed item

### Added

- first added item

### Changed

- second changed item

## [1.0.0] — 2026-01-01

### Added

- first release body
"""


def test_duplicate_subheadings_are_merged_into_one_block_each():
    notes = extract_release_notes(_DUPLICATE_HEADINGS_SAMPLE, "Unreleased")
    assert notes.count("### Fixed") == 1
    assert notes.count("### Changed") == 1
    assert notes.count("### Added") == 1
    assert "first fixed item" in notes
    assert "second fixed item" in notes
    assert "first changed item" in notes
    assert "second changed item" in notes
    assert "first added item" in notes


def test_duplicate_subheadings_keep_first_appearance_order():
    notes = extract_release_notes(_DUPLICATE_HEADINGS_SAMPLE, "Unreleased")
    headings = re.findall(r"^### (.+)$", notes, re.MULTILINE)
    assert headings == ["Fixed", "Changed", "Added"]


def test_duplicate_subheadings_do_not_leak_into_other_releases():
    notes = extract_release_notes(_DUPLICATE_HEADINGS_SAMPLE, "1.0.0")
    assert notes == "### Added\n\n- first release body\n"


def test_no_duplicate_subheadings_leaves_body_untouched():
    # A section with one heading per category is returned byte-for-byte
    # unchanged — merging only kicks in when a title actually repeats.
    notes = extract_release_notes(_SAMPLE, "2.0.0")
    assert notes == "### Added\n\n- second release body\n- more of it\n"


def test_real_changelog_unreleased_section_has_no_duplicate_headings():
    notes = extract_release_notes(_CHANGELOG.read_text(), "Unreleased")
    headings = re.findall(r"^### (.+)$", notes, re.MULTILINE)
    assert len(headings) == len(set(headings)), (
        f"duplicate ### headings survived extraction: {headings}"
    )
