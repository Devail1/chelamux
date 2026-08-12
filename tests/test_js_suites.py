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
* Same rule for **jsdom**: ``tests/wall.test.mjs`` runs the real dashboard JS in a real
  DOM, and jsdom is the repo's only npm dependency (dev-only; nothing is bundled or
  shipped). No ``npm ci`` -> the suite cannot run -> loud skip, or a FAILURE under
  ``CHELA_REQUIRE_JS_TESTS``. See :func:`_jsdom_or_skip`.
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


def _clean_env() -> dict[str, str]:
    """CMX-252: strip ``NODE_CHANNEL_FD`` (and its sibling) before spawning ``node``.

    A leaked IPC-channel fd number (tmux/pm2's own fork ancestry, not this process's) can
    make ``node --test`` abort with SIGABRT before running a single test if that fd number
    happens to resolve to something open-but-wrong in the child (stdin reproduces it: see
    ``chela/judge.py``'s ``_no_color_env``). This suite must not depend on the caller having
    scrubbed it — it is run directly by plain ``uv run pytest -q`` too, not only via the
    judge's ``test_cmd``. ``NODE_CHANNEL_SERIALIZATION_MODE`` is popped alongside it for the
    same reason ``_no_color_env`` pops both: inert alone, but leaving it behind means a
    fixture that only checks the fd can't tell a full scrub from a partial one.
    """
    env = dict(os.environ)
    env.pop("NODE_CHANNEL_FD", None)
    env.pop("NODE_CHANNEL_SERIALIZATION_MODE", None)
    return env


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


def _jsdom_or_skip(suite: Path) -> None:
    """A DOM suite without jsdom DID NOT RUN — the same rule as a missing ``node``.

    ``tests/wall.test.mjs`` runs the real dashboard JS in a real DOM, which is the only
    thing in this repo that needs an npm install (``npm ci``; jsdom is the one dep, and
    it is dev-only). If it is absent the suite cannot run, and a suite that cannot run
    must never be reported as green — CI sets ``CHELA_REQUIRE_JS_TESTS`` and gets a
    failure; a laptop gets a skip that says exactly what to type.
    """
    if "from 'jsdom'" not in suite.read_text():
        return
    if (ROOT / "node_modules" / "jsdom").is_dir():
        return
    msg = (f"jsdom is not installed — {suite.relative_to(ROOT)} (the real-DOM suite) "
           f"DID NOT RUN. Run `npm ci` in {ROOT}.")
    if os.environ.get("CHELA_REQUIRE_JS_TESTS"):
        pytest.fail(msg + " (CHELA_REQUIRE_JS_TESTS is set: a silent skip is not green)")
    pytest.skip(msg)


@pytest.mark.parametrize("suite", _SUITES, ids=_IDS)
def test_js_suite(suite: Path):
    node = _node_or_skip([suite])
    _jsdom_or_skip(suite)
    proc = subprocess.run(
        [node, "--test", str(suite)], capture_output=True, timeout=120, cwd=str(ROOT),
        env=_clean_env(),
    )
    if proc.returncode != 0:
        pytest.fail(
            f"{suite.relative_to(ROOT)} failed:\n"
            f"{proc.stdout.decode()[-4000:]}\n{proc.stderr.decode()[-1000:]}"
        )
