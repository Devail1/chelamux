"""Tests for the dashboard-editable user config + the launcher's projects-dir
resolution precedence (CMX-217: CHELA_PROJECTS_DIR env > config.json > ~/projects
— env wins, same order every dashboard-writable knob now follows).

Everything runs against a temp CHELA_DIR so no real ~/.chela state is touched.
"""
from __future__ import annotations

import importlib

import pytest


@pytest.fixture()
def mods(tmp_path, monkeypatch):
    """Reload config + userconfig + launcher against a temp CHELA_DIR so their
    module-level paths pick up the override."""
    monkeypatch.setenv("CHELA_DIR", str(tmp_path / "chela"))
    monkeypatch.delenv("CHELA_PROJECTS_DIR", raising=False)
    import chela.config as config
    importlib.reload(config)
    import chela.userconfig as userconfig
    importlib.reload(userconfig)
    import chela.launcher as launcher
    importlib.reload(launcher)
    return config, userconfig, launcher


def test_get_set_clear_roundtrip(mods):
    _, userconfig, _ = mods
    assert userconfig.get("projects_dir", "") == ""
    userconfig.set_("projects_dir", "/tmp/x")
    assert userconfig.get("projects_dir") == "/tmp/x"
    userconfig.set_("projects_dir", "")          # empty clears
    assert userconfig.get("projects_dir", "") == ""


def test_missing_file_reads_empty(mods):
    _, userconfig, _ = mods
    assert userconfig._load() == {}


def test_projects_dir_precedence_env_beats_config_beats_default(mods, monkeypatch):
    config, userconfig, launcher = mods

    # Default when nothing is set.
    assert launcher._projects_dir().name == "projects"

    # Config beats default when env is unset.
    userconfig.set_("projects_dir", "/tmp/cfg-projects")
    assert str(launcher._projects_dir()) == "/tmp/cfg-projects"

    # Env beats config.
    monkeypatch.setenv("CHELA_PROJECTS_DIR", "/tmp/env-projects")
    assert str(launcher._projects_dir()) == "/tmp/env-projects"

    # Clearing env falls back to config again.
    monkeypatch.delenv("CHELA_PROJECTS_DIR", raising=False)
    assert str(launcher._projects_dir()) == "/tmp/cfg-projects"
