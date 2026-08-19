"""`chela.release_notes` is what `.github/workflows/release.yml` calls to build a
GitHub Release's `--notes-file` — a real, tested function instead of inline
`sed`/`awk` in the workflow YAML. These tests exercise it both directly and as the
CLI the workflow actually shells out to.
"""
from __future__ import annotations

import re
import subprocess
import sys
from datetime import date as _date
from pathlib import Path

import pytest

from chela import release_notes
from chela.release_notes import (
    ReleaseNotFoundError,
    UnrecognisedHeadingError,
    collect_fragments,
    extract_release_notes,
    latest_released_version,
    promote_unreleased,
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
    # Presence alone doesn't prove the merge preserved document order — a
    # reversed join would pass every assertion above. In the fixture, "first
    # fixed item" appears in the earlier ### Fixed block and "second fixed
    # item" in the later one, so their relative position in the merged
    # output pins that `_merge_duplicate_subheadings` concatenates chunks in
    # the order they appeared rather than, say, reversing them.
    assert notes.index("first fixed item") < notes.index("second fixed item")


def test_duplicate_subheadings_use_canonical_category_order():
    # First appearance in the fixture is Fixed, Changed, Added — deliberately
    # NOT canonical order, so this proves the output is reordered rather than
    # happening to already match. First-appearance order is itself
    # merge-order-dependent (identical entries merged in a different sequence
    # would produce a different release body); canonical order isn't.
    notes = extract_release_notes(_DUPLICATE_HEADINGS_SAMPLE, "Unreleased")
    headings = re.findall(r"^### (.+)$", notes, re.MULTILINE)
    assert headings == ["Added", "Changed", "Fixed"]


_UNKNOWN_HEADING_SAMPLE = """\
# Changelog

## [Unreleased]

### Fixed

- first fixed item

### Notes

- a contributor note that isn't a Keep a Changelog category

### Fixed

- second fixed item

### Added

- first added item

## [1.0.0] — 2026-01-01

### Added

- first release body
"""


def test_unrecognised_subheading_survives_the_merge():
    # A ### title outside the six canonical categories must not be dropped —
    # it's emitted after the known ones, in first-appearance order.
    notes = extract_release_notes(_UNKNOWN_HEADING_SAMPLE, "Unreleased")
    headings = re.findall(r"^### (.+)$", notes, re.MULTILINE)
    assert headings == ["Added", "Fixed", "Notes"]
    assert "a contributor note that isn't a Keep a Changelog category" in notes


def test_duplicate_subheadings_do_not_leak_into_other_releases():
    notes = extract_release_notes(_DUPLICATE_HEADINGS_SAMPLE, "1.0.0")
    assert notes == "### Added\n\n- first release body\n"


def test_no_duplicate_subheadings_leaves_body_untouched():
    # A section with one heading per category is returned byte-for-byte
    # unchanged — merging only kicks in when a title actually repeats.
    notes = extract_release_notes(_SAMPLE, "2.0.0")
    assert notes == "### Added\n\n- second release body\n- more of it\n"


def test_write_mode_is_a_noop_on_a_changelog_with_no_duplicate_headings(tmp_path):
    path = tmp_path / "CHANGELOG.md"
    path.write_text(_SAMPLE)

    result = _run_cli("--write", "--changelog", str(path))

    assert result.returncode == 0
    assert path.read_text() == _SAMPLE


def test_write_mode_collapses_duplicate_headings_in_unreleased_in_place(tmp_path):
    path = tmp_path / "CHANGELOG.md"
    path.write_text(_DUPLICATE_HEADINGS_SAMPLE)

    result = _run_cli("--write", "--changelog", str(path))

    assert result.returncode == 0
    written = path.read_text()
    assert written.count("### Fixed") == 1
    assert written.count("### Changed") == 1
    assert written.count("### Added") == 2  # one in Unreleased, one in 1.0.0
    assert "first fixed item" in written
    assert "second fixed item" in written
    # canonical order, on disk
    unreleased = extract_release_notes(written, "Unreleased")
    headings = re.findall(r"^### (.+)$", unreleased, re.MULTILINE)
    assert headings == ["Added", "Changed", "Fixed"]
    # the historical 1.0.0 section is untouched
    assert extract_release_notes(written, "1.0.0") == "### Added\n\n- first release body\n"


def test_write_mode_preserves_the_blank_line_before_the_next_heading(tmp_path):
    # `_merge_duplicate_subheadings` always ends its own return value with a
    # single `\n` (right for a standalone extracted release body) — --write
    # splices that merged text back into the file, so it must restore
    # whatever blank line originally separated Unreleased from what follows,
    # not the extractor's single newline.
    path = tmp_path / "CHANGELOG.md"
    path.write_text(_DUPLICATE_HEADINGS_SAMPLE)

    result = _run_cli("--write", "--changelog", str(path))

    assert result.returncode == 0
    written = path.read_text()
    before_next_heading, sep, _after = written.partition("## [1.0.0]")
    assert sep, "the 1.0.0 heading must survive --write unchanged"
    assert before_next_heading.endswith("\n\n")
    assert not before_next_heading.endswith("\n\n\n")


def test_write_mode_does_not_touch_historical_sections_even_with_duplicates(tmp_path):
    # ⛔ Cleaning up an already-published release body is a separate,
    # deliberate operator call — --write must never make it silently.
    dirty_historical = _DUPLICATE_HEADINGS_SAMPLE.replace(
        "## [1.0.0] — 2026-01-01\n\n### Added",
        "## [1.0.0] — 2026-01-01\n\n### Added\n\n- x\n\n### Added",
    )
    path = tmp_path / "CHANGELOG.md"
    path.write_text(dirty_historical)

    result = _run_cli("--write", "--changelog", str(path))

    assert result.returncode == 0
    written = path.read_text()
    historical_section = written.split("## [1.0.0]", 1)[1]
    assert historical_section.count("### Added") == 2


def test_cli_requires_version_unless_write_is_given():
    result = _run_cli()
    # argparse's `parser.error()` exits 2 and prints a usage line + the
    # message; a crash (e.g. `None.startswith` reached because the version
    # check was skipped) exits 1 and prints a traceback instead — pin both
    # the exit code and the exact message so a bypassed check reads red.
    assert result.returncode == 2
    assert "Traceback" not in result.stderr
    assert "version is required unless --write is given" in result.stderr


# --- changelog.d fragments: CMX-312, "move to per-PR fragment files" ----------------------
#
# CMX-308 and CMX-309 both went `CONFLICTING` on `CHANGELOG.md`'s shared `## [Unreleased]`
# section — GitHub's own mergeability check doesn't run `.gitattributes`' `merge=union`
# driver (CMX-241 only smooths over a *local* merge), so two open PRs each appending there
# get no CI at all until a human drops one side's entry. Fragment files under `changelog.d/`
# never collide because every PR writes a differently-named file.


def test_collect_fragments_is_empty_for_a_directory_with_no_fragments(tmp_path):
    assert collect_fragments(tmp_path / "changelog.d") == ""


def test_collect_fragments_ignores_readme(tmp_path):
    d = tmp_path / "changelog.d"
    d.mkdir()
    (d / "README.md").write_text("### Added\n\n- this must never be collected\n")

    assert collect_fragments(d) == ""


def test_collect_fragments_concatenates_in_filename_order(tmp_path):
    d = tmp_path / "changelog.d"
    d.mkdir()
    # Written out of order on disk; filename order must still win.
    (d / "CMX-320.md").write_text("### Added\n\n- second fragment\n")
    (d / "CMX-310.md").write_text("### Added\n\n- first fragment\n")

    notes = collect_fragments(d)

    assert notes.count("### Added") == 1
    assert notes.index("first fragment") < notes.index("second fragment")


def test_collect_fragments_merges_duplicate_categories_across_files(tmp_path):
    d = tmp_path / "changelog.d"
    d.mkdir()
    (d / "CMX-311.md").write_text("### Fixed\n\n- a fix\n")
    (d / "CMX-312.md").write_text("### Added\n\n- an addition\n")
    (d / "CMX-313.md").write_text("### Fixed\n\n- another fix\n")

    notes = collect_fragments(d)
    headings = re.findall(r"^### (.+)$", notes, re.MULTILINE)

    assert headings == ["Added", "Fixed"]  # canonical order, one heading each
    assert "a fix" in notes
    assert "another fix" in notes


def test_promote_unreleased_combines_existing_body_and_fragments(tmp_path):
    changelog = (
        "# Changelog\n\n## [Unreleased]\n\n### Added\n\n- already there\n\n"
        "## [0.6.0] — 2026-08-15\n\n### Fixed\n\n- old release\n"
    )
    d = tmp_path / "changelog.d"
    d.mkdir()
    (d / "CMX-312.md").write_text("### Fixed\n\n- from a fragment\n")

    rewritten = promote_unreleased(changelog, "0.7.0", "2026-08-18", d)

    new_release = extract_release_notes(rewritten, "0.7.0")
    assert "already there" in new_release
    assert "from a fragment" in new_release
    # the historical section is untouched
    assert extract_release_notes(rewritten, "0.6.0") == "### Fixed\n\n- old release\n"
    # ⛔ CMX-214/tests/test_version.py: a promotion that drops the heading entirely
    # silently breaks every PR merged afterwards, the exact `0.4.0` incident.
    assert re.search(r"(?m)^## \[Unreleased\]\s*$", rewritten)
    assert extract_release_notes(rewritten, "Unreleased") == "\n"
    # WIRING (DEFEAT_SHAPES #312 round 3): `extract_release_notes` finds a
    # `## [version]` heading anywhere in the text, so it can't tell newest-first
    # from appended-at-the-bottom. `latest_released_version` (which
    # tests/test_version.py's own invariant relies on) can, and a promotion that
    # appended instead of inserting directly below `## [Unreleased]` would make
    # this return the wrong version on the very next release.
    assert latest_released_version(rewritten) == "0.7.0"


def test_promote_unreleased_works_with_only_fragments_and_no_existing_body(tmp_path):
    changelog = "# Changelog\n\n## [Unreleased]\n\n## [0.6.0] — 2026-08-15\n\n### Fixed\n\n- x\n"
    d = tmp_path / "changelog.d"
    d.mkdir()
    (d / "CMX-312.md").write_text("### Added\n\n- fragment only\n")

    rewritten = promote_unreleased(changelog, "0.7.0", "2026-08-18", d)

    assert "fragment only" in extract_release_notes(rewritten, "0.7.0")


def test_promote_unreleased_works_with_no_fragments_at_all(tmp_path):
    changelog = "# Changelog\n\n## [Unreleased]\n\n### Added\n\n- already there\n"
    rewritten = promote_unreleased(changelog, "0.7.0", "2026-08-18", tmp_path / "changelog.d")

    assert "already there" in extract_release_notes(rewritten, "0.7.0")
    assert extract_release_notes(rewritten, "Unreleased") == "\n"


def test_promote_unreleased_raises_without_an_unreleased_heading():
    changelog = "# Changelog\n\n## [0.6.0] — 2026-08-15\n\n### Fixed\n\n- x\n"
    with pytest.raises(ReleaseNotFoundError):
        promote_unreleased(changelog, "0.7.0", "2026-08-18", Path("does-not-matter"))


def test_cli_release_writes_the_changelog_and_deletes_consumed_fragments(tmp_path):
    changelog_path = tmp_path / "CHANGELOG.md"
    changelog_path.write_text(
        "# Changelog\n\n## [Unreleased]\n\n### Added\n\n- already there\n\n"
        "## [0.6.0] — 2026-08-15\n\n### Fixed\n\n- old release\n"
    )
    d = tmp_path / "changelog.d"
    d.mkdir()
    (d / "CMX-312.md").write_text("### Fixed\n\n- from a fragment\n")
    (d / "README.md").write_text("convention doc — must survive")

    result = _run_cli(
        "--release", "0.7.0", "--date", "2026-08-18",
        "--changelog", str(changelog_path), "--changelog-d", str(d),
    )

    assert result.returncode == 0, result.stderr
    written = changelog_path.read_text()
    assert "from a fragment" in extract_release_notes(written, "0.7.0")
    # the fragment is consumed and gone, the README convention doc survives
    assert not (d / "CMX-312.md").exists()
    assert (d / "README.md").exists()


def test_cli_release_defaults_date_to_today(tmp_path):
    # WIRING: every other --release test above passes --date explicitly, so nothing
    # exercises `date = args.date or _date.today().isoformat()` in main() — unlike
    # `_default_changelog_d_path()` (DEFEAT_SHAPES #312), this default's consumer
    # (--release) is NOT destructive to run end-to-end here: the test above already
    # invokes it safely against a tmp_path CHANGELOG.md/changelog.d, it just always
    # supplies --date. Reuse that exact shape with --date omitted instead.
    changelog_path = tmp_path / "CHANGELOG.md"
    changelog_path.write_text("# Changelog\n\n## [Unreleased]\n\n### Added\n\n- already there\n")
    d = tmp_path / "changelog.d"
    d.mkdir()

    result = _run_cli(
        "--release", "0.7.0",
        "--changelog", str(changelog_path), "--changelog-d", str(d),
    )

    assert result.returncode == 0, result.stderr
    written = changelog_path.read_text()
    today = _date.today().isoformat()
    assert f"## [0.7.0] — {today}" in written


def test_cli_release_requires_version():
    result = _run_cli("--release")
    assert result.returncode == 2
    assert "version is required with --release" in result.stderr


def test_default_changelog_d_path_points_at_the_repos_own_changelog_d():
    # WIRING: every test above that exercises --changelog-d passes it explicitly, so
    # nothing else pins what `--changelog-d` defaults to. `_default_changelog_path()`
    # (CHANGELOG.md's default) has a sibling guard — `test_cli_prints_notes_for_a_real_version`
    # runs the CLI with no --changelog and asserts real repo content — but that test can't
    # be mirrored for --changelog-d as-is: --release is destructive (it writes CHANGELOG.md
    # and deletes consumed fragments), so running it against this repo's real files isn't
    # safe to do from a test. Pin the function's actual return value instead: this directly
    # exercises the code the documented no-flags invocation
    # (`python -m chela.release_notes --release X.Y.Z`) relies on, so a default silently
    # repointed at a directory that also happens to exist (e.g. a typo, or a renamed/moved
    # fragments dir) goes red here instead of shipping a release that silently collects zero
    # fragments.
    assert release_notes._default_changelog_d_path() == _REPO_ROOT / "changelog.d"


def test_changelog_d_argument_default_is_wired_to_the_resolver():
    # WIRING (DEFEAT_SHAPES #312 round 3): the test above pins what
    # `_default_changelog_d_path()` returns, but says nothing about whether
    # argparse's `--changelog-d` option actually USES it as its default — a
    # corruption that repoints only the `default=` kwarg (leaving the helper
    # itself untouched) reproduces the exact same silent-zero-fragments failure
    # and this suite would stay green without this. Build the real parser and
    # parse zero args, so both the helper and the wiring that consumes it are
    # pinned by one assertion.
    args = release_notes._build_parser().parse_args([])
    assert args.changelog_d == _REPO_ROOT / "changelog.d"
