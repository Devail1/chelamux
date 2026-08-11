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

# `### Added` / `### Changed` / `### Fixed` / ... — the Keep a Changelog
# category headings a release body is organised under.
_SUBHEADING = re.compile(r"(?m)^### (.+)$")

# Keep a Changelog's canonical category order. Emitting merged sections in
# this fixed order (rather than first-appearance order) makes the output a
# pure function of the content: two branches carrying identical entries that
# merge in a different sequence still produce byte-identical release bodies.
# First-appearance order would preserve that nondeterminism in a subtler form.
_CANONICAL_CATEGORY_ORDER = ("Added", "Changed", "Deprecated", "Removed", "Fixed", "Security")


class ReleaseNotFoundError(ValueError):
    """No matching `## [version]` section exists in the changelog."""


class UnrecognisedHeadingError(ValueError):
    """A `## [version]` heading's suffix uses a separator this parser doesn't know."""


def _merge_duplicate_subheadings(body: str) -> str:
    """Collapse repeated `### <Category>` headings in a release body into one
    block per category, content concatenated in the order it appeared, and
    emit the resulting blocks in Keep a Changelog's canonical category order
    (`_CANONICAL_CATEGORY_ORDER`) rather than first-appearance order. A title
    that isn't one of those six categories is not dropped — it's emitted
    after the known ones, in first-appearance order among themselves.

    Parallel worktree agents each append their own `### Added`/`### Changed`/
    `### Fixed` subsection under `## [Unreleased]`, blind to each other's
    concurrent edits — no single agent's diff can know another already added
    a section with the same title, so the same category heading ends up
    duplicated in the file. A GitHub Release built straight from the raw
    section would ship those duplicates verbatim; this runs at extraction,
    the one place every release body is assembled, so it fixes every release
    regardless of how many agents' edits landed in it.

    A section with no duplicate titles is returned byte-for-byte unchanged.
    """
    matches = list(_SUBHEADING.finditer(body))
    titles = [m.group(1).strip() for m in matches]
    if len(set(titles)) == len(titles):
        return body

    first_seen: list[str] = []
    chunks: dict[str, list[str]] = {}
    for i, match in enumerate(matches):
        title = titles[i]
        content_end = matches[i + 1].start() if i + 1 < len(matches) else len(body)
        content = body[match.end() : content_end].strip("\n")
        if title not in chunks:
            chunks[title] = []
            first_seen.append(title)
        if content:
            chunks[title].append(content)

    order = [title for title in _CANONICAL_CATEGORY_ORDER if title in chunks]
    order += [title for title in first_seen if title not in _CANONICAL_CATEGORY_ORDER]

    preamble = body[: matches[0].start()]
    sections = []
    for title in order:
        merged = "\n\n".join(chunks[title])
        sections.append(f"### {title}\n\n{merged}" if merged else f"### {title}")

    return preamble + "\n\n".join(sections) + "\n"


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

    body = _merge_duplicate_subheadings(changelog_text[body_start:body_end])
    return body.strip() + "\n"


def coalesce_unreleased_section(changelog_text: str) -> str:
    """Return `changelog_text` with duplicate `### <Category>` headings in its
    `## [Unreleased]` section collapsed, via `_merge_duplicate_subheadings`.

    Only `## [Unreleased]` is touched — the section parallel worktree agents
    actually append to. Dated release sections are left alone on purpose:
    cleaning up an already-published release body is a separate, deliberate
    call by the operator, not something this automation does silently. If
    `## [Unreleased]` is absent, or has no duplicate headings, the text comes
    back byte-for-byte unchanged.
    """
    headings = list(_iter_headings(changelog_text))
    start = next((m for m in headings if m.group("version") == "Unreleased"), None)
    if start is None:
        return changelog_text

    body_start = start.end()
    later_heading_starts = [m.start() for m in headings if m.start() > start.start()]
    footer = changelog_text.find("\n---\n", body_start)
    candidates = later_heading_starts + ([footer] if footer != -1 else [])
    body_end = min(candidates) if candidates else len(changelog_text)

    body = changelog_text[body_start:body_end]
    merged = _merge_duplicate_subheadings(body)
    if merged == body:
        return changelog_text

    # `_merge_duplicate_subheadings` always ends its output with exactly one
    # `\n`, which is right for a standalone extracted release body but wrong
    # here: `body` itself may have ended with more than that (e.g. the blank
    # line that separates it from the next `## [...]` heading), and that
    # trailing whitespace is otherwise dropped, splicing the merged section
    # directly onto the next heading with no blank line between them.
    trailing_ws = re.search(r"\s*\Z", body).group()
    return changelog_text[:body_start] + merged.rstrip("\n") + trailing_ws + changelog_text[body_end:]


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
    parser.add_argument(
        "version",
        nargs="?",
        help="release version, e.g. 0.3.0 (a leading 'v' is stripped); ignored with --write",
    )
    parser.add_argument(
        "--changelog",
        type=Path,
        default=_default_changelog_path(),
        help="path to CHANGELOG.md (default: repo root)",
    )
    parser.add_argument(
        "--write",
        action="store_true",
        help="coalesce duplicate ### headings in --changelog's ## [Unreleased] "
        "section in place, instead of printing one release's notes",
    )
    args = parser.parse_args(argv)

    if args.write:
        original = args.changelog.read_text()
        rewritten = coalesce_unreleased_section(original)
        if rewritten != original:
            args.changelog.write_text(rewritten)
        return 0

    if not args.version:
        parser.error("version is required unless --write is given")
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
