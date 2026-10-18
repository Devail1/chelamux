"""⚖️🪜 CMX-208: the merge gate's risk tiers (docs/ESCALATION_CONTRACT.md), from a PR's files.

PROSE (every file is docs, changelog or media) skips the judge round; HUMAN (any ALWAYS-HUMAN
path) needs ``chela merge --override`` even when judge-clean; JUDGE is everything else. ⛔ An
unknown or empty file list is never prose: it takes the judge path.
"""
from __future__ import annotations

from fnmatch import fnmatchcase
from pathlib import PurePosixPath

PROSE, HUMAN, JUDGE = "prose", "human", "judge"

_DOCS_SUFFIXES = {".md", ".mdx", ".rst", ".txt", ".jsonl"}
_MEDIA_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".mp4", ".webm", ".mov"}
_INSTRUCTION_PROSE = {"WORKFLOW.md", "CLAUDE.md", "AGENTS.md"}

# `dir/**` matches everything under dir; anything else is an `fnmatchcase` pattern.
DEFAULT_ALWAYS_HUMAN = ("WORKFLOW.md", ".github/workflows/**", "plugin/**", "chela/mergegate.py",
                        "chela/contract.py", "chela/risktier.py", "chela/release.py",
                        "scripts/release*")


def is_prose(path: str) -> bool:
    p = PurePosixPath(path)
    suffix = p.suffix.lower()
    if p.name in _INSTRUCTION_PROSE:     # agent instructions at any depth, never prose
        return False
    if suffix in _MEDIA_SUFFIXES:
        return True
    if len(p.parts) == 1:
        return suffix == ".md"
    if p.parts[0] == "changelog.d":
        return len(p.parts) == 2
    return p.parts[0] == "docs" and suffix in _DOCS_SUFFIXES


def _matches(path: str, pattern: str) -> bool:
    return (path.startswith(pattern[:-2]) if pattern.endswith("/**")
            else fnmatchcase(path, pattern))


def always_human(workflow) -> tuple[str, ...]:
    """The defaults plus the workflow's own ``merge.always_human`` list. A workflow can ADD
    paths, never remove a default; a malformed value adds nothing."""
    extra = workflow.get("merge", "always_human", default=None) if workflow else None
    extra = extra if isinstance(extra, list) else []
    return DEFAULT_ALWAYS_HUMAN + tuple(p.strip() for p in extra if isinstance(p, str) and p.strip())


def classify(files: list[str] | None, patterns: tuple[str, ...]) -> tuple[str, str | None]:
    """``(tier, trigger)`` — ``trigger`` is the first always-human path, for a ``HUMAN``."""
    if not files:
        return JUDGE, None
    hit = next((f for f in files for p in patterns if _matches(f, p)), None)
    if hit is not None:
        return HUMAN, hit
    if all(is_prose(f) for f in files):
        return PROSE, None
    return JUDGE, None
