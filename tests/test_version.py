"""`chela.__version__` must be a single fact, not a second hand-maintained literal.

`pyproject.toml`'s `version` field is the only place the release number is written;
`chela/__init__.py` derives `__version__` from the installed package's own metadata
(`importlib.metadata`) instead of hardcoding a copy. Before this, the two numbers
were independent literals that could (and did, across the 0.2.0/0.3.0 releases)
drift apart silently.
"""
from __future__ import annotations

import re
from pathlib import Path

import chela


def _pyproject_version() -> str:
    pyproject = Path(__file__).resolve().parent.parent / "pyproject.toml"
    text = pyproject.read_text()
    match = re.search(r'(?m)^version = "([^"]+)"', text)
    assert match, "pyproject.toml must declare a [project] version"
    return match.group(1)


def test_version_matches_pyprojects_single_source_of_truth():
    assert chela.__version__ == _pyproject_version()
