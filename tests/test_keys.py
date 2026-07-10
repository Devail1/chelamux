"""Runs the joiner keys-line JS logic tests (tests/keys.test.mjs) under Node's
built-in test runner, so the escape-sequence map + swipe→wheel mapping stay in the
pytest suite. Skips if Node is unavailable (mirrors the interop / presence-core
suites)."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

_TEST = Path(__file__).resolve().parent / "keys.test.mjs"


def test_keys_js_logic():
    node = shutil.which("node")
    if not node:
        pytest.skip("node not available for keys JS tests")
    proc = subprocess.run([node, "--test", str(_TEST)], capture_output=True, timeout=60)
    if proc.returncode != 0:
        pytest.fail(f"keys JS tests failed:\n{proc.stdout.decode()[-2000:]}\n{proc.stderr.decode()[-1000:]}")
