"""scripts/smoke-fresh-install.sh — CMX-263: an adopter fresh-install smoke test.

Every install anyone has run `chela update`/`chela doctor` on predates months of changes
— "it worked when I set it up" is not evidence about current `dev`/`main`. Nobody had ever
proven, end to end, that a brand-new clone can `uv sync` and run both commands without
crashing. These tests run the real script against a real (local, offline) clone of this
checkout — no mocked git, no mocked `uv` — the same contract `tests/test_update.py` holds
for `chela.update` itself.

The isolation test pins a real bug hit while building this: the script's first draft only
redirected `CHELA_DIR`, so on a box that already runs a live chela install (this project's
own dev machine, notably) the "fresh" run still inherited the calling shell's
`CHELA_DISPATCH_WORKFLOWS` and printed that developer's actual dispatched-repo paths —
exactly the "reads live state" bug class `tests/test_isolation.py` exists to catch, one
level up (a subprocess's inherited environment rather than an unredirected `CHELA_DIR`).
"""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "smoke-fresh-install.sh"

pytestmark = pytest.mark.skipif(
    shutil.which("uv") is None or shutil.which("git") is None,
    reason="uv and git are both required to run a real fresh-install clone + sync",
)


def _run(*, env: dict) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", str(SCRIPT), str(ROOT)],
        capture_output=True, text=True, timeout=600, env=env,
    )


def test_passes_on_a_real_fresh_clone_of_this_checkout():
    """The load-bearing one: a genuinely fresh `git clone` + `uv sync --all-extras` +
    `chela doctor` + `chela update --check` + `chela update` must all run to completion
    without an uncaught exception — an install nobody has verified end to end since months
    of changes landed."""
    out = _run(env=dict(os.environ))

    assert "Traceback (most recent call last):" not in out.stdout, out.stdout
    assert out.returncode == 0, out.stdout + out.stderr
    assert "PASS: fresh-install smoke test" in out.stdout


def test_strips_inherited_chela_env_so_a_live_install_never_leaks_in():
    """A calling shell that already runs chela for real (CHELA_DISPATCH_WORKFLOWS pointing
    at real repos, as this project's own dev machine does) must not have any of that leak
    into what is supposed to simulate a brand-new adopter's empty environment. Corrupt this
    by deleting the script's `unset` loop and the sentinel path below reappears verbatim in
    `chela doctor`'s "dispatch workflow ... does not exist" finding."""
    sentinel = "/nonexistent-sentinel-repo-cmx263/WORKFLOW.md"
    env = dict(os.environ)
    env["CHELA_DISPATCH_WORKFLOWS"] = sentinel
    env["CHELA_TMUX_SESSION"] = "definitely-not-a-real-session-cmx263"

    out = _run(env=env)

    assert sentinel not in out.stdout, (
        "a CHELA_* var inherited from the calling shell leaked into the fresh-install run:\n"
        + out.stdout
    )
    assert "definitely-not-a-real-session-cmx263" not in out.stdout
