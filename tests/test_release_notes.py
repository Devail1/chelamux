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
    StaleFragmentError,
    UnrecognisedHeadingError,
    collect_fragments,
    extract_release_notes,
    latest_released_version,
    promote_unreleased,
    released_task_ids,
    stale_fragments,
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
    (d / "CMX-312.md").write_text("### Added\n\n- from a fragment\n")

    rewritten = promote_unreleased(changelog, "0.7.0", "2026-08-18", d)

    new_release = extract_release_notes(rewritten, "0.7.0")
    assert "already there" in new_release
    assert "from a fragment" in new_release
    # MUTATION (DEFEAT_SHAPES #312 round 4): the existing `## [Unreleased]` body
    # and the collected fragments share a `### Added` heading here — this is the
    # merge call site `_merge_duplicate_subheadings` guards at the boundary
    # between them, not the cross-fragment merge `collect_fragments` already
    # covers on its own. A pass-through that skips the merge still concatenates
    # both bullets under two separate `### Added` headings. Asserting through
    # `new_release` (i.e. `extract_release_notes`) can't catch that: it runs
    # `_merge_duplicate_subheadings` itself on the way out (see its own body,
    # line 129), silently re-merging what `promote_unreleased` failed to merge
    # and masking the very mutation this assertion exists to catch. Slice the
    # raw `rewritten` text instead, before it passes through any second merge.
    promoted_section = rewritten[rewritten.index("## [0.7.0]"):rewritten.index("## [0.6.0]")]
    assert re.findall(r"(?m)^### (.+)$", promoted_section) == ["Added"]
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


def test_promote_unreleased_writes_the_bare_heading_when_theres_nothing_to_promote(tmp_path):
    # MUTATION (DEFEAT_SHAPES #312 round 5): every other promote_unreleased fixture gives
    # `merged` truthy content, so the ternary's `else` arm — the bare `## [version] — date`
    # heading with nothing under it — never runs. That arm is what the docstring's central
    # claim ("the heading this function writes is always present, never a step a maintainer
    # can forget") is actually about: an empty `## [Unreleased]` with no changelog.d
    # fragments is the NORMAL steady state this PR creates between releases. Assert on the
    # raw `rewritten` text with a plain substring check first — routing through
    # `extract_release_notes`/`latest_released_version` would raise `ReleaseNotFoundError`
    # if the heading were silently dropped, masking the actual defect behind an exception.
    changelog = "# Changelog\n\n## [Unreleased]\n\n## [0.6.0] — 2026-08-15\n\n### Fixed\n\n- x\n"
    d = tmp_path / "changelog.d"
    d.mkdir()

    rewritten = promote_unreleased(changelog, "0.7.0", "2026-08-18", d)

    assert "## [0.7.0] — 2026-08-18" in rewritten
    assert latest_released_version(rewritten) == "0.7.0"
    assert extract_release_notes(rewritten, "0.7.0") == "\n"


def test_promote_unreleased_stops_at_a_footer_rule_that_precedes_the_next_heading(tmp_path):
    # MUTATION (DEFEAT_SHAPES #312 round 6): `promote_unreleased` takes
    # `body_end = min(candidates)` over `later_heading_starts + [footer]` — "whichever
    # comes FIRST". Every fixture above puts the next `## [...]` heading immediately
    # after `## [Unreleased]`'s body with no `\n---\n` rule anywhere before it, so
    # `min` and `max` pick the exact same candidate (the only one there is) and the
    # rule is never actually exercised in the direction where the footer is the
    # nearer delimiter. Here a `\n---\n` rule sits BETWEEN the Unreleased body and the
    # next dated heading — `min` must stop at the footer; the survived mutation
    # (`min` -> `max`) would swallow the footer, the stray note below it, and the
    # `## [0.6.0]` heading itself into the promoted 0.7.0 body.
    changelog = (
        "# Changelog\n\n## [Unreleased]\n\n### Changed\n\n- new work\n\n"
        "---\n\nStray divider before the next heading (edge case).\n\n"
        "## [0.6.0] — 2026-08-15\n\n### Fixed\n\n- old release\n"
    )
    d = tmp_path / "changelog.d"
    d.mkdir()
    (d / "CMX-312.md").write_text("### Added\n\n- from a fragment\n")

    rewritten = promote_unreleased(changelog, "0.7.0", "2026-08-18", d)

    # `extract_release_notes` re-derives its own boundary on the raw output, so this
    # is not a case of the round-3/4 pitfall (a reader silently undoing the write
    # side's corruption): a `min`-vs-`max` boundary mistake in `promote_unreleased`
    # changes what's embedded in the promoted body, which extract's own (unmutated,
    # correct) boundary detection then reads back differently.
    assert "from a fragment" in extract_release_notes(rewritten, "0.7.0")
    assert "Stray divider" not in extract_release_notes(rewritten, "0.7.0")
    assert extract_release_notes(rewritten, "0.6.0") == "### Fixed\n\n- old release\n"
    # Direct, position-based confirmation on the raw text: the fragment (appended
    # after `existing_body` inside `promote_unreleased`) only lands ahead of the
    # stray footer note when the boundary correctly stopped at the footer instead of
    # swallowing past it.
    assert rewritten.index("from a fragment") < rewritten.index("Stray divider")


def test_promote_unreleased_stops_at_the_next_heading_that_precedes_a_footer_rule(tmp_path):
    # MUTATION (DEFEAT_SHAPES #312 round 6): the mirror image of the fixture above —
    # here the next `## [...]` heading comes BEFORE the trailing `\n---\n` rule, the
    # realistic shape of this repo's own CHANGELOG.md (multiple dated sections, then
    # a footer at the very bottom). `min` must stop at the heading; the survived
    # mutation (`min` -> `max`) would swallow the entire `## [0.6.0]` section AND the
    # footer note into the promoted 0.7.0 body — a strictly worse version of the
    # 0.4.0 incident this module exists to close.
    changelog = (
        "# Changelog\n\n## [Unreleased]\n\n### Changed\n\n- new work\n\n"
        "## [0.6.0] — 2026-08-15\n\n### Fixed\n\n- old release\n\n"
        "---\n\nFooter note, not part of any release.\n"
    )
    d = tmp_path / "changelog.d"
    d.mkdir()
    (d / "CMX-312.md").write_text("### Added\n\n- from a fragment\n")

    rewritten = promote_unreleased(changelog, "0.7.0", "2026-08-18", d)

    # Slice the raw output directly (DEFEAT_SHAPES #312 round 4's lesson): the
    # fragment is appended right after `existing_body`, so whether it lands before
    # or after the reappearing `## [0.6.0]` heading is a direct read of where the
    # boundary actually landed.
    promoted_section = rewritten[rewritten.index("## [0.7.0]") : rewritten.index("## [0.6.0]")]
    assert "from a fragment" in promoted_section
    assert "old release" not in promoted_section
    assert extract_release_notes(rewritten, "0.6.0") == "### Fixed\n\n- old release\n"
    assert latest_released_version(rewritten) == "0.7.0"


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


# ---------------------------------------------------------------------------
# CMX-315 — a fragment that already shipped must never be collected twice
# ---------------------------------------------------------------------------

_RELEASED_SAMPLE = """\
# Changelog

## [Unreleased]

### Added

- pending, not published (CMX-400)

## [0.8.0] — 2026-08-20

### Fixed

- something that shipped (CMX-309, #385)
"""


def _fragment_dir(tmp_path, **files):
    d = tmp_path / "changelog.d"
    d.mkdir()
    (d / "README.md").write_text("convention doc — never a fragment")
    for name, body in files.items():
        (d / name).write_text(body)
    return d


def test_released_task_ids_reads_dated_sections_only():
    ids = released_task_ids(_RELEASED_SAMPLE)
    assert "309" in ids, "a marker in the dated 0.8.0 section is published"
    # MUST BE ACCEPTED: `## [Unreleased]` is pending, not published. Counting it would
    # call a fragment stale for duplicating an entry it is allowed to duplicate —
    # `_merge_duplicate_subheadings` exists precisely to merge those.
    assert "400" not in ids, "an Unreleased marker has not shipped and is not released"


def test_released_task_ids_treats_no_dated_section_as_nothing_released():
    """A repo before its first release has ONLY `## [Unreleased]` — no dated section
    exists at all. `released_task_ids`'s `dated is None` branch handles that case
    through code that never runs when a dated section exists, so the branch above
    it (exercised by every other test in this file, all of which mount a dated
    `## [0.8.0]`) never reaches it. Nothing but this test exercises it.
    """
    changelog = "# Changelog\n\n## [Unreleased]\n\n### Added\n\n- brand new (CMX-1)\n"
    assert released_task_ids(changelog) == set(), (
        "with no dated section, nothing has shipped yet — an Unreleased marker "
        "must not be counted as released"
    )


def test_stale_fragments_accepts_everything_before_the_first_release(tmp_path):
    """Mirrors test_released_task_ids_treats_no_dated_section_as_nothing_released at
    the fragment-matching level: a repo with no dated section yet (no release has
    ever happened) cannot possibly have a stale fragment, no matter what task ids
    sit under `## [Unreleased]`.
    """
    changelog = "# Changelog\n\n## [Unreleased]\n\n### Added\n\n- brand new (CMX-1)\n"
    d = _fragment_dir(tmp_path, **{"CMX-1.md": "### Added\n\n- brand new (CMX-1)\n"})
    assert stale_fragments(changelog, d) == []


def test_released_task_ids_ignores_a_bare_mention_in_dated_prose():
    """A dated entry routinely cites a sibling task id in prose — CMX-315's own
    fragment cites CMX-312 this way. Matching any bare `CMX-N` (instead of only the
    parenthesised `(CMX-N)` trailer that actually marks what shipped) would mark
    that cited sibling as released too, even though it never got its own entry.
    """
    changelog = (
        "# Changelog\n\n## [Unreleased]\n\n## [0.8.0] — 2026-08-20\n\n"
        "### Fixed\n\n- follows up on CMX-309, but only this ships (CMX-999)\n"
    )
    ids = released_task_ids(changelog)
    assert "999" in ids, "the parenthesised trailer marks CMX-999 as published"
    assert "309" not in ids, "a bare CMX-309 mention in prose is not a published marker"


def test_stale_fragments_ignores_a_bare_mention_in_dated_prose(tmp_path):
    """Mirrors test_released_task_ids_ignores_a_bare_mention_in_dated_prose at the
    fragment-matching level: a fresh CMX-309 fragment must not be called stale just
    because some other dated entry mentions CMX-309 in prose without shipping it.
    """
    changelog = (
        "# Changelog\n\n## [Unreleased]\n\n## [0.8.0] — 2026-08-20\n\n"
        "### Fixed\n\n- follows up on CMX-309, but only this ships (CMX-999)\n"
    )
    d = _fragment_dir(tmp_path, **{"CMX-309.md": "### Added\n\n- brand new (CMX-309)\n"})
    assert stale_fragments(changelog, d) == []


def test_released_task_ids_reads_every_dated_section_not_just_the_newest():
    """The docstring is explicit: "Dated" means everything from the first non-`Unreleased`
    heading down. Every other fixture in this file mounts exactly one dated section, so
    "from the first dated heading down" and "only the first dated section" are the same
    string for all of them — a reader that stopped at the next heading would pass every
    other test here too. This fixture mounts TWO dated sections and puts its marker in the
    OLDER (second) one, the case that actually distinguishes the two readings: the incident
    this guard exists for is a fragment that survived TWO skipped back-merges (CONTRIBUTING's
    "two promotions' worth of drift"), so it shipped under an old version, not the newest one.
    """
    changelog = (
        "# Changelog\n\n## [Unreleased]\n\n## [0.9.0] — 2026-08-22\n\n"
        "### Fixed\n\n- newest release, unrelated (CMX-500)\n\n"
        "## [0.8.0] — 2026-08-20\n\n"
        "### Fixed\n\n- older release (CMX-309, #385)\n"
    )
    ids = released_task_ids(changelog)
    assert "500" in ids, "a marker in the newest dated section is published"
    assert "309" in ids, (
        "a marker in an OLDER dated section (not the newest) is published too — "
        "'dated' means every dated section, not just the first one found"
    )


def test_stale_fragments_flags_a_fragment_published_in_an_older_dated_section(tmp_path):
    """Mirrors test_released_task_ids_reads_every_dated_section_not_just_the_newest at the
    fragment-matching level: a fragment that shipped under an OLD version, two releases back,
    must still be caught — not just one that shipped in the newest dated section.
    """
    changelog = (
        "# Changelog\n\n## [Unreleased]\n\n## [0.9.0] — 2026-08-22\n\n"
        "### Fixed\n\n- newest release, unrelated (CMX-500)\n\n"
        "## [0.8.0] — 2026-08-20\n\n"
        "### Fixed\n\n- older release (CMX-309, #385)\n"
    )
    d = _fragment_dir(tmp_path, **{"CMX-309.md": "### Fixed\n\n- older release (CMX-309, #385)\n"})
    assert [p.name for p in stale_fragments(changelog, d)] == ["CMX-309.md"]


def test_stale_fragments_flags_one_already_published_in_a_dated_section(tmp_path):
    d = _fragment_dir(tmp_path, **{"CMX-309.md": "### Fixed\n\n- shipped (CMX-309, #385)\n"})
    assert [p.name for p in stale_fragments(_RELEASED_SAMPLE, d)] == ["CMX-309.md"]


def test_stale_fragments_accepts_a_fresh_fragment(tmp_path):
    """MUST BE ACCEPTED — the guard is worthless if it cannot pass the normal case."""
    d = _fragment_dir(tmp_path, **{"CMX-999.md": "### Added\n\n- brand new (CMX-999)\n"})
    assert stale_fragments(_RELEASED_SAMPLE, d) == []


def test_stale_fragments_accepts_a_fragment_still_pending_under_unreleased(tmp_path):
    """Mirrors test_released_task_ids_reads_dated_sections_only at the fragment-matching
    level. `_RELEASED_SAMPLE` carries CMX-400 only under `## [Unreleased]` — pending, not
    published — alongside a dated section where CMX-309 genuinely shipped. Every other
    stale_fragments fixture in this file stages a fragment whose id is either genuinely
    published in a dated section or absent from the changelog altogether; none stages one
    that is mentioned ONLY under Unreleased while a dated section (with something else
    published) also exists — so nothing here catches `stale_fragments` comparing against
    every marker in the whole document instead of only the dated ones.
    """
    d = _fragment_dir(tmp_path, **{"CMX-400.md": "### Added\n\n- pending, not published (CMX-400)\n"})
    assert stale_fragments(_RELEASED_SAMPLE, d) == []


def test_stale_fragments_flags_every_already_published_fragment_not_just_the_first(tmp_path):
    """The motivating incident had THREE stale fragments surviving a skipped
    back-merge at once — CMX-309, CMX-312 and CMX-314, all still on `dev` with
    their text already published in `## [0.8.0]`. A guard that reports only the
    first would send a maintainer through repeated failed --release runs, fixing
    one at a time, never told how many are actually sitting in the tree.
    """
    changelog = (
        "# Changelog\n\n## [Unreleased]\n\n## [0.8.0] — 2026-08-20\n\n"
        "### Fixed\n\n- one (CMX-309)\n- two (CMX-312)\n- three (CMX-314)\n"
    )
    d = _fragment_dir(tmp_path, **{
        "CMX-309.md": "### Fixed\n\n- one (CMX-309)\n",
        "CMX-312.md": "### Fixed\n\n- two (CMX-312)\n",
        "CMX-314.md": "### Fixed\n\n- three (CMX-314)\n",
    })
    assert [p.name for p in stale_fragments(changelog, d)] == [
        "CMX-309.md", "CMX-312.md", "CMX-314.md",
    ]


def test_stale_fragments_judges_by_filename_not_by_cited_prose(tmp_path):
    """A fragment routinely cites sibling task ids in its body — CMX-315's own cites
    CMX-312. Matching on prose would call every such fragment stale the moment any
    task it mentions shipped, which is the guard firing on correct work.
    """
    d = _fragment_dir(tmp_path, **{
        "CMX-999.md": "### Changed\n\n- follows up on CMX-309 (CMX-309 shipped) (CMX-999)\n",
    })
    assert stale_fragments(_RELEASED_SAMPLE, d) == []


def test_stale_fragments_matches_a_filename_id_shorter_than_three_digits(tmp_path):
    """`_FRAGMENT_NAME`'s `\\d+` must match a task id of ANY length. Every other
    stale_fragments fixture in this file that compares against a non-empty released set
    uses a three-digit id (309, 312, 314, 400, 999) — this repo's own changelog.d/ is also
    all three-digit (315/316/317/320/321) — so a regex narrowed to exactly three digits
    (e.g. `\\d{3}\\d*`) would pass every one of them while silently dropping shorter ids
    from the guard entirely: a stale `CMX-9.md` would republish, no refusal. Use a
    single-digit id here, the case that actually distinguishes `\\d+` from a
    three-digit-minimum pattern.
    """
    changelog = (
        "# Changelog\n\n## [Unreleased]\n\n## [0.8.0] — 2026-08-20\n\n"
        "### Fixed\n\n- short id (CMX-9, #385)\n"
    )
    d = _fragment_dir(tmp_path, **{"CMX-9.md": "### Fixed\n\n- short id (CMX-9, #385)\n"})
    assert [p.name for p in stale_fragments(changelog, d)] == ["CMX-9.md"]


def test_stale_fragments_does_not_truncate_a_four_digit_filename_id(tmp_path):
    """Mirrors the short-id case above from the other end: a filename regex pinned to
    exactly three digits with a trailing `\\d*` (`\\d{3}\\d*`) still MATCHES a four-digit
    filename — it just truncates the captured id to its first three digits, so
    `CMX-3155.md` is judged as task `315`. Stage a released set that ships `315` but NOT
    `3155`: a fragment for the genuinely unreleased task `3155` must be accepted, not
    refused for a release it never shipped in.
    """
    changelog = (
        "# Changelog\n\n## [Unreleased]\n\n## [0.8.0] — 2026-08-20\n\n"
        "### Fixed\n\n- unrelated task (CMX-315, #385)\n"
    )
    d = _fragment_dir(tmp_path, **{"CMX-3155.md": "### Fixed\n\n- distinct task (CMX-3155)\n"})
    assert stale_fragments(changelog, d) == []


def test_stale_fragments_accepts_an_unidentifiable_fragment_that_cites_a_shipped_id_in_prose(tmp_path):
    """Sibling to test_stale_fragments_accepts_an_unidentifiable_fragment_name_even_when_something_shipped,
    which stages a body with NO `(CMX-N)` marker at all — so on that fixture, a prose
    fallback added behind the filename match and filename-only matching are
    indistinguishable; the fallback would never be exercised. This fragment's name still
    doesn't parse (`hotfix.md`, the exact off-convention name
    tests/test_judge_changelog_note.py stages as legitimate), but its BODY cites an
    already-shipped task in the trailing `(CMX-N)` form — exactly what CMX-315's own
    fragment does when it cites CMX-312 in prose. `_FRAGMENT_NAME`'s docstring is explicit:
    a fragment is judged by filename, NEVER by prose. Must stay accepted.
    """
    d = _fragment_dir(tmp_path, **{
        "hotfix.md": "### Fixed\n\n- follow-up on already-shipped work (CMX-309)\n",
    })
    assert stale_fragments(_RELEASED_SAMPLE, d) == []


def test_stale_fragments_ignores_the_readme(tmp_path):
    d = _fragment_dir(tmp_path)
    (d / "README.md").write_text("mentions (CMX-309) in prose")
    assert stale_fragments(_RELEASED_SAMPLE, d) == []


def test_stale_fragments_accepts_an_unidentifiable_fragment_name_even_when_something_shipped(tmp_path):
    """`_FRAGMENT_NAME` exists so that only `CMX-<id>.md` is judged — a fragment whose name
    carries no task id has nothing to compare against `released` and must fall through as
    fresh, exactly as tests/test_judge_changelog_note.py stages `changelog.d/hotfix.md` as a
    legitimate fragment for the sibling guard. Every other stale_fragments fixture in this
    file stages only `CMX-<id>.md` names (plus the README, which `_fragment_paths` drops
    before this function ever sees it) alongside a NON-EMPTY released set, so the `m is None`
    fall-through — the branch that keeps an unidentifiable name out of the refusal — is never
    independently exercised. `_RELEASED_SAMPLE` already has CMX-309 published, which is the
    case that matters here: an unidentifiable name must be accepted even though `released` is
    non-empty, not just when there is nothing to compare it to.
    """
    d = _fragment_dir(tmp_path, **{"hotfix.md": "### Fixed\n\n- no CMX id in this filename\n"})
    assert stale_fragments(_RELEASED_SAMPLE, d) == []


def test_promote_unreleased_refuses_a_stale_fragment(tmp_path):
    """Mounts THREE stale fragments — the motivating incident's own count (CMX-309,
    CMX-312, CMX-314) — because the claim this guard makes ('a guard that reports
    only the first would send a maintainer through repeated failed --release runs')
    is about what the RAISED ERROR tells the maintainer, not just what
    stale_fragments()'s return list contains. A guard that named only the first
    stale fragment here would still pass a test mounting just one.
    """
    changelog = (
        "# Changelog\n\n## [Unreleased]\n\n## [0.8.0] — 2026-08-20\n\n"
        "### Fixed\n\n- one (CMX-309)\n- two (CMX-312)\n- three (CMX-314)\n"
    )
    d = _fragment_dir(tmp_path, **{
        "CMX-309.md": "### Fixed\n\n- one (CMX-309)\n",
        "CMX-312.md": "### Fixed\n\n- two (CMX-312)\n",
        "CMX-314.md": "### Fixed\n\n- three (CMX-314)\n",
    })
    with pytest.raises(StaleFragmentError) as exc:
        promote_unreleased(changelog, "0.9.0", "2026-08-21", d)
    message = str(exc.value)
    assert "CMX-309.md" in message, "the first stale fragment must be named"
    assert "CMX-312.md" in message, "the second stale fragment must be named too"
    assert "CMX-314.md" in message, "and the third — not just the first of three"
    assert "back-merge" in message.lower(), (
        "the error must name the cause (a skipped main -> dev back-merge), not just "
        "the symptom — the maintainer reading it has to know what to DO"
    )
    # MUST BE ACCEPTED: the word "back-merge" also appears in the DIAGNOSIS sentence
    # ("this is what a skipped `main` -> `dev` back-merge looks like"), so the assert
    # above alone is satisfied even if the ACTIONABLE instruction below it is deleted
    # whole. Pin the instruction text itself so that half can't be dropped silently.
    assert "or delete them if you are sure they shipped" in message, (
        "the error must also tell the maintainer what to DO about it, not just "
        "diagnose the cause — this is the actionable half, distinct from the "
        "diagnostic 'back-merge' sentence checked above"
    )


def test_promote_unreleased_succeeds_with_a_fresh_fragment_after_something_shipped(tmp_path):
    """`stale_fragments` itself is proven not to flag a fresh fragment against a non-empty
    released set (test_stale_fragments_accepts_a_fresh_fragment), but `promote_unreleased`'s
    OWN refusal — the code that actually decides whether a release is blocked — has never
    been driven with a non-empty released set in the ACCEPT direction. Every promote/--release
    fixture with fresh fragments elsewhere in this file mounts a changelog with ZERO
    `(CMX-N)` trailers, so `released_task_ids` is empty in every one of them; the only
    fixtures with something already shipped are the ones that expect a refusal. Use
    `_RELEASED_SAMPLE` (CMX-309 already published) with a brand-new fragment and assert the
    release actually goes through instead of being wrongly refused.
    """
    d = _fragment_dir(tmp_path, **{"CMX-999.md": "### Added\n\n- brand new (CMX-999)\n"})
    result = promote_unreleased(_RELEASED_SAMPLE, "0.9.0", "2026-08-21", d)
    assert "## [0.9.0] — 2026-08-21" in result
    assert "brand new (CMX-999)" in result


def test_cli_release_refuses_a_stale_fragment_without_touching_anything(tmp_path):
    """The refusal must be TOTAL. `main()` writes the changelog and then unlinks every
    fragment; a guard that raised after the write would leave a half-released tree.
    """
    changelog_path = tmp_path / "CHANGELOG.md"
    changelog_path.write_text(_RELEASED_SAMPLE)
    d = _fragment_dir(tmp_path, **{"CMX-309.md": "### Fixed\n\n- shipped (CMX-309, #385)\n"})

    result = _run_cli(
        "--release", "0.9.0", "--date", "2026-08-21",
        "--changelog", str(changelog_path), "--changelog-d", str(d),
    )

    assert result.returncode == 1, result.stdout
    # A crash (e.g. the refusal's except clause silently dropping StaleFragmentError
    # so it propagates as an uncaught exception) also exits 1 and also puts the
    # fragment's name in stderr via the traceback — pin the absence of a traceback
    # too, the same way test_cli_requires_version_unless_write_is_given does, so a
    # crash can't be mistaken for a clean refusal.
    assert "Traceback" not in result.stderr
    assert "CMX-309.md" in result.stderr
    assert changelog_path.read_text() == _RELEASED_SAMPLE, "the changelog was modified"
    assert (d / "CMX-309.md").exists(), "the fragment was consumed despite the refusal"


def test_this_repo_carries_no_already_released_fragment():
    """Repo hygiene, and the reason this guard is not merely theoretical.

    On 2026-08-21 — one day after 0.8.0 — `dev` still carried `CMX-309.md`,
    `CMX-312.md` and `CMX-314.md`, whose entries were already published in
    `## [0.8.0]` on `main`. The release deletes fragments on `main`; nothing carried
    those deletions back to `dev`; the next release would have republished all three.
    Checked against THIS branch's own CHANGELOG.md, so it fires the moment a
    promotion merge brings a released section and a surviving fragment together.
    """
    d = release_notes._default_changelog_d_path()
    stale = stale_fragments(_CHANGELOG.read_text(), d)
    assert stale == [], (
        f"{[p.name for p in stale]} already appear in a dated CHANGELOG.md section — "
        "back-merge `main` into `dev` so the release's fragment deletions reach this "
        "branch, or delete them by hand if you are certain they shipped"
    )

