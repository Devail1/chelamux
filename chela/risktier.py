"""⚖️🪜 CMX-208: the merge gate's risk tiers (docs/ESCALATION_CONTRACT.md), from a PR's files.

PROSE (every file is docs, changelog or media) skips the judge round; HUMAN (any ALWAYS-HUMAN
path) needs ``chela merge --override`` even when judge-clean; CORE (any core path — the gate's
own machinery, or an agent-instruction file) gets today's full judge; STANDARD (CMX-288,
everything else: product and UI work) gets no judge round at all (CMX-296). ⛔ An unknown or empty file list is never prose
and never standard: it takes the CORE path.
"""
from __future__ import annotations

from fnmatch import fnmatchcase
from pathlib import PurePosixPath

PROSE, HUMAN, CORE, STANDARD = "prose", "human", "core", "standard"

_DOCS_SUFFIXES = {".md", ".mdx", ".rst", ".txt", ".jsonl"}
_MEDIA_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".mp4", ".webm", ".mov"}
_INSTRUCTION_PROSE = {"WORKFLOW.md", "CLAUDE.md", "AGENTS.md"}

# `dir/**` matches everything under dir; anything else is an `fnmatchcase` pattern.
DEFAULT_ALWAYS_HUMAN = ("WORKFLOW.md", ".github/workflows/**", "plugin/**", "chela/mergegate.py",
                        "chela/contract.py", "chela/risktier.py", "chela/release.py",
                        "scripts/release*")
DEFAULT_CORE = ("chela/dispatcher.py", "chela/judge*.py", "chela/workflow*.py", "chela/sources/**",
                "chela/context.py",
                # CMX-296: a STANDARD PR gets no judge round, so the rest of the merge and
                # containment machinery is core too — loosening it must meet the judge.
                "chela/automerge.py", "chela/overrides.py", "chela/gateanswer.py",
                "chela/reviewgate.py", "chela/sizegate.py", "chela/basesync.py",
                "chela/sweep*.py", "chela/toolconfig.py", "chela/sandbox*.py",
                "chela/procguard.py")


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


def _with_workflow(defaults: tuple[str, ...], workflow, key: str) -> tuple[str, ...]:
    """The defaults plus the workflow's own ``merge.<key>`` list. A workflow can ADD paths,
    never remove a default; a malformed value adds nothing."""
    extra = workflow.get("merge", key, default=None) if workflow else None
    extra = extra if isinstance(extra, list) else []
    return defaults + tuple(p.strip() for p in extra if isinstance(p, str) and p.strip())


def always_human(workflow) -> tuple[str, ...]:
    return _with_workflow(DEFAULT_ALWAYS_HUMAN, workflow, "always_human")


def core(workflow) -> tuple[str, ...]:
    """⚖️🪜 CMX-288: :data:`DEFAULT_CORE` plus the workflow's ``merge.core`` list."""
    return _with_workflow(DEFAULT_CORE, workflow, "core")


def classify(files: list[str] | None, patterns: tuple[str, ...],
             core_patterns: tuple[str, ...] = DEFAULT_CORE) -> tuple[str, str | None]:
    """``(tier, trigger)`` — ``trigger`` is the first always-human path, for a ``HUMAN``."""
    if not files:
        return CORE, None
    hit = next((f for f in files for p in patterns if _matches(f, p)), None)
    if hit is not None:
        return HUMAN, hit
    if all(is_prose(f) for f in files):
        return PROSE, None
    # CMX-296: agent instructions are never STANDARD — they steer every later run.
    if any(_matches(f, p) for f in files for p in core_patterns) \
            or any(PurePosixPath(f).name in _INSTRUCTION_PROSE for f in files):
        return CORE, None
    return STANDARD, None
