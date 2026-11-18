"""⚖️🪜 CMX-208: the merge gate's risk tiers (docs/ESCALATION_CONTRACT.md), from a PR's files.

PROSE (every file is docs, changelog or media) skips the judge round; HUMAN (any ALWAYS-HUMAN
path) needs ``chela merge --override`` even when judge-clean; CORE (any core path — the gate's
own machinery, or an agent-instruction file) gets today's full judge; STANDARD (CMX-288,
everything else: product and UI work) gets no judge round at all (CMX-296). ⛔ An unknown or empty file list is never prose
and never standard: it takes the CORE path.

CMX-396: the task's tracker risk level is a FLOOR on the tier. A ``risk:high`` task is at
least CORE whatever its files are; ``normal``/``low`` (or none) add no floor. The level comes
from the run row, copied from the tracker when the task was claimed — never from the diff.
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
                "chela/procguard.py",
                # CMX-396: trust boundaries — who a dashboard request is, and what a sandboxed
                # guest is granted. Their approval ROUTES live in the dashboard's app.py,
                # which is mostly UI; a task touching them carries `risk:high` instead.
                "chela/dashboard/identity.py", "chela/share_requests.py")

# CMX-396: the tracker risk level that floors the tier, and the tier it floors it to.
FLOOR_RISK, FLOOR_TIER = "high", CORE


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


def floor(tier: str, risk: object) -> tuple[str, str | None]:
    """⚖️🪜 CMX-396: ``(tier, why)`` after the risk floor. A ``risk:high`` task lifts a PROSE
    or STANDARD tier to CORE and says so (``why == "risk:high"``); every other case keeps
    ``tier`` and ``why`` is None — the floor never lowers a tier, nor touches HUMAN."""
    level = risk.strip().lower() if isinstance(risk, str) else None
    if level == FLOOR_RISK and tier in (PROSE, STANDARD):
        return FLOOR_TIER, f"risk:{FLOOR_RISK}"
    return tier, None


def describe(tier: str | None, why: str | None = None) -> str:
    """The tier as a person reads it: ``CORE (floor: risk:high)``, or just ``STANDARD``."""
    name = (tier or "unknown").upper()
    return f"{name} (floor: {why})" if why else name


def classify(files: list[str] | None, patterns: tuple[str, ...],
             core_patterns: tuple[str, ...] = DEFAULT_CORE,
             risk: object = None) -> tuple[str, str | None]:
    """``(tier, trigger)`` — ``trigger`` is the first always-human path, for a ``HUMAN``.
    ``risk`` (the run's tracker level) floors the tier: see :func:`floor`."""
    tier, trigger, _why = explain(files, patterns, core_patterns, risk)
    return tier, trigger


def explain(files: list[str] | None, patterns: tuple[str, ...],
            core_patterns: tuple[str, ...] = DEFAULT_CORE,
            risk: object = None) -> tuple[str, str | None, str | None]:
    """:func:`classify` plus ``why`` — ``"risk:high"`` when the risk floor set the tier."""
    tier, trigger = _by_files(files, patterns, core_patterns)
    tier, why = floor(tier, risk)
    return tier, trigger, why


def _by_files(files: list[str] | None, patterns: tuple[str, ...],
              core_patterns: tuple[str, ...]) -> tuple[str, str | None]:
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
