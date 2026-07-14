"""Runs the Feed's agent-lane logic tests (tests/feed.test.mjs) under Node's built-in
test runner, so the lane model — attention sort, dead-agent lanes, never-guess
attribution, and the "nothing is hidden silently" rule — stays inside the pytest suite.
Skips if Node is unavailable (mirrors tests/test_keys.py and the interop suites)."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

_TEST = Path(__file__).resolve().parent / "feed.test.mjs"


def test_feed_lane_model_js():
    node = shutil.which("node")
    if not node:
        pytest.skip("node not available for feed JS tests")
    proc = subprocess.run([node, "--test", str(_TEST)], capture_output=True, timeout=60)
    if proc.returncode != 0:
        pytest.fail(f"feed JS tests failed:\n{proc.stdout.decode()[-3000:]}\n"
                    f"{proc.stderr.decode()[-1000:]}")
