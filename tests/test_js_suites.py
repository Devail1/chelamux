"""Every ``*.test.mjs`` in the repo, run under Node inside pytest — BY DISCOVERY.

A test that exists but is never executed is not a test; it is a comment that costs CI
nothing to ignore. On 2026-07-14 ``tests/views.test.mjs`` had been RED on ``dev`` while
``uv run pytest -q`` reported 980 passed — because the wiring was a set of hand-written
per-file wrappers (``test_feed_js.py``, ``test_keys.py``, ``test_presence_core.py``),
each naming ONE file, and nobody wrote a fourth. Two suites were unrun: ``views`` (red)
and ``runtoast`` (green, but unwatched); a third, ``chela/dashboard/static/collab/
fit.test.mjs``, was outside ``tests/`` and unrun by anything at all.

So the list is gone. This globs the repo and runs what it FINDS — one pytest item per
file, so a failure names the file. Adding a ``.test.mjs`` anywhere wires it in by the
act of creating it; there is no second list to forget.

Two fences, because the failure mode here is a check that *silently* does nothing:

* :func:`test_js_suites_are_discovered` fails if the glob finds nothing — a discovery
  that quietly matches zero files is this exact bug, one layer down.
* A missing ``node`` SKIPS LOUDLY, naming every suite that did not run. Set
  ``CHELA_REQUIRE_JS_TESTS=1`` (CI does) and a missing ``node`` is a FAILURE instead —
  CI must never report green on a suite it never executed.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
_SKIP_DIRS = {"node_modules", "vendor", ".git"}


def js_suites() -> list[Path]:
    """Every JS test file in the repo — the whole repo, not just ``tests/``."""
    return sorted(
        p for p in ROOT.rglob("*.test.mjs")
        if not _SKIP_DIRS.intersection(p.relative_to(ROOT).parts)
    )


_SUITES = js_suites()
_IDS = [str(p.relative_to(ROOT)) for p in _SUITES]


def _node_or_skip(suites: list[Path]) -> str:
    node = shutil.which("node")
    if node:
        return node
    names = ", ".join(str(p.relative_to(ROOT)) for p in suites)
    msg = f"node is not installed — {len(suites)} JS suite(s) DID NOT RUN: {names}"
    if os.environ.get("CHELA_REQUIRE_JS_TESTS"):
        pytest.fail(msg + " (CHELA_REQUIRE_JS_TESTS is set: a silent skip is not green)")
    pytest.skip(msg)


def test_js_suites_are_discovered():
    """A glob that matches nothing would report green by running nothing."""
    assert _SUITES, f"no *.test.mjs found under {ROOT} — the JS suites are not being run"


@pytest.mark.parametrize("suite", _SUITES, ids=_IDS)
def test_js_suite(suite: Path):
    node = _node_or_skip([suite])
    proc = subprocess.run(
        [node, "--test", str(suite)], capture_output=True, timeout=120, cwd=str(ROOT)
    )
    if proc.returncode != 0:
        pytest.fail(
            f"{suite.relative_to(ROOT)} failed:\n"
            f"{proc.stdout.decode()[-4000:]}\n{proc.stderr.decode()[-1000:]}"
        )
