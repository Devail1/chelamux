"""Runs the presence-core JS logic tests (tests/presence_core.test.mjs) under
Node's built-in test runner, so the coordinate invariant + PeerStore/palette logic
stay in the pytest suite. Skips if Node is unavailable (mirrors the interop suite)."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

_TEST = Path(__file__).resolve().parent / "presence_core.test.mjs"


def test_presence_core_js_logic():
    node = shutil.which("node")
    if not node:
        pytest.skip("node not available for presence-core JS tests")
    proc = subprocess.run([node, "--test", str(_TEST)], capture_output=True, timeout=60)
    if proc.returncode != 0:
        pytest.fail(f"presence-core JS tests failed:\n{proc.stdout.decode()[-2000:]}\n{proc.stderr.decode()[-1000:]}")
