"""CMX-187: chela ships only its own skills; the generic planning ones were retired.

``blindspot-pass``, ``interview-me`` and ``implementation-plan`` were take-offs of widely
used skills, so they were removed in favour of ``mattpocock/skills`` (see "Recommended
skills" in docs/GETTING_STARTED.md). Three guards keep it that way:

* the removed skill directories stay gone;
* no tracked file names them (a dangling ``/blindspot-pass`` in a doc or a brief template
  sends a reader to a skill that does not exist), except the changelog, which is history;
* ``skills/README.md`` lists exactly the kept set, and the kept set is exactly what is on disk.
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent

REMOVED = ("blindspot-pass", "interview-me", "implementation-plan")
KEPT = {"chela-setup", "orchestrate", "handoff", "telegram-setup", "telegram-send"}

# History may name a retired skill; so does this guard.
ALLOWED = {"CHANGELOG.md", "tests/test_skill_set.py"}
ALLOWED_DIRS = ("changelog.d/",)

REMOVED_RE = re.compile(r"(?<![\w-])(?:blindspot-pass|interview-me|implementation-plan)(?![\w-])")
# A skill row in a README table: `| **name** | what it does |`.
ROW_RE = re.compile(r"^\|\s*\*\*([a-z][a-z-]*)\*\*\s*\|", re.M)


def tracked_files() -> list[str]:
    out = subprocess.run(["git", "ls-files", "-z"], cwd=ROOT, capture_output=True,
                         check=True, timeout=30).stdout
    return [p for p in out.decode().split("\0") if p]


def allowed(rel: str) -> bool:
    return rel in ALLOWED or rel.startswith(ALLOWED_DIRS)


def references(files: dict[str, str]) -> list[str]:
    """``path:line`` for every mention of a removed skill outside the allowlist."""
    return [f"{rel}:{n}" for rel, text in files.items() if not allowed(rel)
            for n, line in enumerate(text.splitlines(), 1) if REMOVED_RE.search(line)]


def listed_skills(readme: str) -> set[str]:
    return set(ROW_RE.findall(readme))


@pytest.mark.parametrize("name", REMOVED)
def test_removed_skill_dir_is_gone(name):
    assert not (ROOT / "skills" / name).exists(), f"skills/{name} was retired (CMX-187)"


def test_no_tracked_file_references_a_removed_skill():
    rels = tracked_files()
    assert len(rels) > 100, "git ls-files returned almost nothing; cannot vouch for the repo"
    files = {}
    for rel in rels:
        path = ROOT / rel
        if path.is_file():
            files[rel] = path.read_bytes().decode("utf-8", errors="ignore")
    bad = references(files)
    assert not bad, f"repoint or remove these references to retired skills: {bad[:10]}"


def test_readme_lists_exactly_the_kept_skills():
    readme = (ROOT / "skills" / "README.md").read_text(encoding="utf-8")
    assert listed_skills(readme) == KEPT


def test_skills_on_disk_are_exactly_the_kept_set():
    on_disk = {p.parent.name for p in (ROOT / "skills").glob("*/SKILL.md")}
    assert on_disk == KEPT


@pytest.mark.parametrize("rel,text,hit", [
    ("README.md", "run `/blindspot-pass` first", True),
    ("docs/x.md", "see skills/interview-me/SKILL.md", True),
    ("WORKFLOW.md", "implementation-plan", True),
    ("CHANGELOG.md", "removed blindspot-pass", False),
    ("changelog.d/CMX-187.md", "removed interview-me", False),
    ("docs/x.md", "write an implementation plan", False),
    ("docs/x.md", "my-interview-me-notes", False),
    ("docs/x.md", "blindspot-passes", False),
    ("docs/x.md", "pre-blindspot-pass", False),
    ("docs/x.md", "my_interview-me", False),
])
def test_reference_scan(rel, text, hit):
    assert bool(references({rel: text})) is hit


@pytest.mark.parametrize("readme,expected", [
    ("| **a** | x |\n| **b-c** | y |\n", {"a", "b-c"}),
    ("| Skill | What |\n|---|---|\nnot **bold** in a row\n", set()),
])
def test_listed_skills(readme, expected):
    assert listed_skills(readme) == expected
