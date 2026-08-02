"""Extract one release's notes from CHANGELOG.md — the single place a release's
body is written, so a GitHub Release's `--notes-file` is never a second hand-typed
copy that can drift from the file `git log` and readers actually see.

`.github/workflows/release.yml` calls `python -m chela.release_notes VERSION`
(never inline `sed`/`awk` in the workflow YAML) so extraction is a real, unit-tested
function — see `tests/test_release_notes.py`.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

# `## [X.Y.Z] — YYYY-MM-DD` or `## [Unreleased]` — see CHANGELOG.md. The suffix
# is captured whole (not restricted to a separator) so a heading is never
# invisible to this parser; `_iter_headings` below is what actually validates
# the separator and raises loudly on one it doesn't recognise.
_HEADING = re.compile(r"(?m)^## \[(?P<version>[^\]]+)\](?P<suffix>.*)$")

# Keep a Changelog (which CHANGELOG.md cites) dates headings with an ASCII
# hyphen; this file's own headings use an em dash. Accept both, plus an en
# dash and no date at all (e.g. `## [Unreleased]`).
_KNOWN_SEPARATOR = re.compile(r"^(?:\s*[-–—].*)?$")


class ReleaseNotFoundError(ValueError):
    """No matching `## [version]` section exists in the changelog."""


class UnrecognisedHeadingError(ValueError):
    """A `## [version]` heading's suffix uses a separator this parser doesn't know."""


def _iter_headings(changelog_text: str):
    """Yield every `## [version]` heading match, raising loudly instead of
    silently dropping one whose suffix uses a separator we don't recognise.
    """
    for match in _HEADING.finditer(changelog_text):
        suffix = match.group("suffix")
        if not _KNOWN_SEPARATOR.match(suffix):
            raise UnrecognisedHeadingError(
                f"heading '## [{match.group('version')}]{suffix}' uses a "
                "separator this parser doesn't recognise (expected '-', "
                "'–', '—', or nothing after the version)"
            )
        yield match


def extract_release_notes(changelog_text: str, version: str) -> str:
    """Return the body of the `## [version]` section, heading excluded, trimmed.

    The body runs until whichever comes first: the next `## [...]` heading, or
    the `---` rule that separates release sections from this file's trailing
    process note (see the bottom of CHANGELOG.md).
    """
    headings = list(_iter_headings(changelog_text))
    start = next((m for m in headings if m.group("version") == version), None)
    if start is None:
        raise ReleaseNotFoundError(f"no '## [{version}]' section in this changelog")

    body_start = start.end()
    later_heading_starts = [m.start() for m in headings if m.start() > start.start()]
    footer = changelog_text.find("\n---\n", body_start)
    candidates = later_heading_starts + ([footer] if footer != -1 else [])
    body_end = min(candidates) if candidates else len(changelog_text)

    return changelog_text[body_start:body_end].strip() + "\n"


def latest_released_version(changelog_text: str) -> str:
    """Return the version of the newest dated (non-`Unreleased`) heading.

    Sections are newest-first by this changelog's own convention (Keep a
    Changelog), so the first non-`Unreleased` heading found is the latest release.
    """
    for match in _iter_headings(changelog_text):
        version = match.group("version")
        if version != "Unreleased":
            return version
    raise ReleaseNotFoundError("no dated release section in this changelog")


def _default_changelog_path() -> Path:
    return Path(__file__).resolve().parent.parent / "CHANGELOG.md"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Print a release's CHANGELOG.md section body, for "
        "`gh release create --notes-file`.",
    )
    parser.add_argument("version", help="release version, e.g. 0.3.0 (a leading 'v' is stripped)")
    parser.add_argument(
        "--changelog",
        type=Path,
        default=_default_changelog_path(),
        help="path to CHANGELOG.md (default: repo root)",
    )
    args = parser.parse_args(argv)
    version = args.version[1:] if args.version.startswith("v") else args.version

    try:
        notes = extract_release_notes(args.changelog.read_text(), version)
    except ReleaseNotFoundError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    sys.stdout.write(notes)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
