"""`chela.__version__` must be a single fact, not a second hand-maintained literal.

`pyproject.toml`'s `version` field is the only place the release number is written;
`chela/__init__.py` derives `__version__` from the installed package's own metadata
(`importlib.metadata`) instead of hardcoding a copy. Before this, the two numbers
were independent literals that could (and did, across the 0.2.0/0.3.0 releases)
drift apart silently.

That first guard alone cannot fail in CI, though: CI runs `uv sync` before pytest,
which reinstalls the package from `pyproject.toml` every time, so the value
`importlib.metadata` reports and `pyproject.toml`'s literal are structurally always
equal there — the only drift it can catch (a stale developer venv) is exactly the
state CI can never be in. The second guard below closes the real gap: nothing
stopped `pyproject.toml`'s version from disagreeing with the CHANGELOG's own
newest dated release heading (bump one, forget the other, or vice versa).
"""
from __future__ import annotations

import re
from pathlib import Path

import chela
from chela.release_notes import latest_released_version


def _pyproject_version() -> str:
    pyproject = Path(__file__).resolve().parent.parent / "pyproject.toml"
    text = pyproject.read_text()
    match = re.search(r'(?m)^version = "([^"]+)"', text)
    assert match, "pyproject.toml must declare a [project] version"
    return match.group(1)


def _changelog_text() -> str:
    changelog = Path(__file__).resolve().parent.parent / "CHANGELOG.md"
    return changelog.read_text()


def test_version_matches_pyprojects_single_source_of_truth():
    assert chela.__version__ == _pyproject_version()


def test_pyproject_version_matches_newest_changelog_release():
    assert _pyproject_version() == latest_released_version(_changelog_text())
