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
import sys
from importlib.metadata import PackageNotFoundError
from pathlib import Path

import pytest

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


def test_changelog_always_carries_an_unreleased_section():
    """CONTRIBUTING.md's release step turns `## [Unreleased]` into a dated
    section but does not by itself put a fresh one back — `02c653c` (Release
    0.4.0) followed that step literally and left 11 later merges with no
    section to append to. Guard the invariant directly so a future release
    that forgets the same step fails CI immediately, not a full release cycle
    later when the notes ship empty.
    """
    assert re.search(r"(?m)^## \[Unreleased\]\s*$", _changelog_text()), (
        "CHANGELOG.md must always carry a `## [Unreleased]` heading — see "
        "CONTRIBUTING.md's 'Releasing' step 1"
    )


def test_package_version_reads_installed_metadata(monkeypatch):
    seen = []

    def fake_version(dist):
        seen.append(dist)
        return "9.8.7"

    monkeypatch.setattr(chela, "version", fake_version)
    assert chela._package_version() == "9.8.7"
    assert seen == ["chelamux"]


def test_package_version_falls_back_without_installed_metadata(monkeypatch):
    """A source tree with no installed dist-info still yields a version, never a crash."""
    def missing(dist):
        raise PackageNotFoundError(dist)

    monkeypatch.setattr(chela, "version", missing)
    assert chela._package_version() == "0.0.0+unknown"


@pytest.mark.parametrize("prog", ["chela", "chelamux"])
def test_cli_version_flag_prints_chela_and_the_release(monkeypatch, capsys, prog):
    """CMX-368: `chela --version` / `chelamux --version` used to exit 2 (unrecognized)."""
    from chela import main as chela_main

    monkeypatch.setattr(sys, "argv", [prog, "--version"])
    with pytest.raises(SystemExit) as exc:
        chela_main.main()
    assert exc.value.code == 0
    assert capsys.readouterr().out == f"chela {_pyproject_version()}\n"
