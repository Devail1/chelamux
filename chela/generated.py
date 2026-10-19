"""🏭 CMX-284: GENERATED files — rebuilt from source, never written by hand.

CMX-215 (P01) spent 190 of its sweep's 196 mutations on the committed Vite bundle
(``chela/dashboard/static/app/**``, CMX-177): minified JS no test can sensibly guard. Its guard
is CI's "committed build is fresh" step, which rebuilds it from ``src/``. So the judge, the
sweep and the hand-in size gate all read the ONE list here: a generated path is never a
mutation candidate and never counts toward a diff's size — and each says how many it skipped.

A pattern is matched against the repo-relative path: ``dir/**`` is everything under ``dir/``,
``**/<glob>`` is ``<glob>`` at any depth (the root included), anything else is ``fnmatch``.
"""
from __future__ import annotations

import fnmatch

PATTERNS = (
    "chela/dashboard/static/app/**",
    "uv.lock",
    "chela/dashboard/frontend/pnpm-lock.yaml",
    "**/*.min.js",
)


def _matches(rel: str, pattern: str) -> bool:
    if pattern.endswith("/**"):
        return rel.startswith(pattern[:-2])
    if pattern.startswith("**/"):
        return fnmatch.fnmatchcase(rel.rsplit("/", 1)[-1], pattern[3:])
    return fnmatch.fnmatchcase(rel, pattern)


def is_generated(rel: str) -> bool:
    rel = rel.strip().removeprefix("./")
    return any(_matches(rel, p) for p in PATTERNS)


def note(count: int) -> str:
    """The round's note for ``count`` skipped generated files ("" when none)."""
    if not count:
        return ""
    return (f"skipped {count} generated file(s) ({', '.join(PATTERNS)}) — rebuilt from "
            "source, so their guard is CI's freshness check, not a mutation")
