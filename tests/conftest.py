"""Suite-wide isolation from the developer's real chela install.

``chela/config.py`` sources ``$CHELA_DIR/chela.env`` at import — that is the point of the
env file: one place, read by every process. A test *is* one of those processes, so on a
machine that actually runs chela the suite would silently inherit that machine's session
name, dashboard port and terminal flags, and start passing (or failing) for reasons that
have nothing to do with the code. This repo has already shipped three bugs that were green
in CI and broken live; a suite that reads live state is how that keeps happening.

``CHELA_ENV_FILE=""`` turns the file off. It is set here, at conftest import — before any
test module imports ``chela.config`` — because the load happens at import time and no
fixture runs early enough to prevent it.

The same rule now covers Claude Code's own config directory: ``chela doctor`` reads the
INSTALLED plugin (``~/.claude/plugins/…`` — the manifest agents actually load), so on a
developer's machine the suite would read *that* machine's install. It already did: a
doctor test passed only because the real cache happened to agree with what the test
rendered. ``CLAUDE_CONFIG_DIR`` points every test at an empty directory of its own; a test
that wants an installed plugin puts one there itself.
"""
import os

import pytest

os.environ.setdefault("CHELA_ENV_FILE", "")


@pytest.fixture(autouse=True)
def _isolate_claude_config(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude-config"))
