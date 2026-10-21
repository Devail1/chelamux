"""🏭 CMX-284: GENERATED files — rebuilt from source, never written by hand.

CMX-215 (P01) spent 190 of its sweep's 196 mutations on the committed Vite bundle
(``chela/dashboard/static/app/**``, CMX-177): minified JS no test can sensibly guard. Its guard
is CI's "committed build is fresh" step, which rebuilds it from ``src/``. So the judge, the
sweep and the hand-in size gate all read the ONE list here: a generated path is never a
mutation candidate and never counts toward a diff's size, and the sweep and the judge each
say how many they skipped.

A pattern is ``fnmatch``-ed against the repo-relative path (its ``*`` crosses ``/``, so
``dir/**`` is everything under ``dir/``); ``**/<glob>`` matches ``<glob>`` against the basename,
so at any depth, the root included.
"""
from __future__ import annotations

import fnmatch
import posixpath

PATTERNS = (
    "chela/dashboard/static/app/**",
    "uv.lock",
    "chela/dashboard/frontend/pnpm-lock.yaml",
    "**/*.min.js",
)


def _matches(rel: str, pattern: str) -> bool:
    if pattern.startswith("**/"):
        return fnmatch.fnmatchcase(rel.rsplit("/", 1)[-1], pattern[3:])
    return fnmatch.fnmatchcase(rel, pattern)


def is_generated(rel: str) -> bool:
    rel = posixpath.normpath(rel.strip())     # `./x`, `a/./b`, `a/../a/b` — one spelling
    return any(_matches(rel, p) for p in PATTERNS)


# 🔀 CMX-300: the generated paths a base merge REBUILDS instead of sending back
# (:func:`chela.basemerge.attempt`) — only the Vite bundle, which a build regenerates whole and
# CI's freshness check verifies. Not the lockfiles: both sides of a lockfile conflict changed a
# dependency, and taking one side would silently drop the other's. Not ``*.min.js``: vendored,
# nothing here rebuilds it.
REBUILT = ("chela/dashboard/static/app/**",)


def is_rebuilt(rel: str) -> bool:
    rel = posixpath.normpath(rel.strip())
    return any(_matches(rel, p) for p in REBUILT)


def note(count: int) -> str:
    """The round's note for ``count`` skipped generated files ("" when none)."""
    if not count:
        return ""
    return (f"skipped {count} generated file(s) ({', '.join(PATTERNS)}) — rebuilt from "
            "source, so their guard is CI's freshness check, not a mutation")
