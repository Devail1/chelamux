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
  shipped). No ``pnpm install`` -> the suite cannot run -> loud skip, or a FAILURE under
  ``CHELA_REQUIRE_JS_TESTS``. See :func:`_jsdom_or_skip`.
* Same rule for **Chromium** (CMX-383): ``tests/browser/*.test.mjs`` drive a real
  headless browser through playwright. The suite itself decides whether it could launch
  one (``tests/browser/fixture.mjs`` ``launchChromium``): under ``CHELA_REQUIRE_JS_TESTS``
  it FAILS; otherwise node skips every test quietly and prints :data:`_BROWSER_DID_NOT_RUN`,
  which :func:`test_js_suite` turns into a loud pytest skip instead of a green pass.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from chela.procguard import run_contained

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


def _xdist_master() -> int | None:
    """CMX-151: under pytest-xdist the run is the MASTER (this worker's parent): a worker
    notices a SIGKILLed master only once its current test ends, so node watches it too."""
    return os.getppid() if os.environ.get("PYTEST_XDIST_WORKER") else None


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
    thing in this repo that needs an npm install (``pnpm install``; jsdom is the one dep, and
    it is dev-only). If it is absent the suite cannot run, and a suite that cannot run
    must never be reported as green — CI sets ``CHELA_REQUIRE_JS_TESTS`` and gets a
    failure; a laptop gets a skip that says exactly what to type.
    """
    if "from 'jsdom'" not in suite.read_text():
        return
    if (ROOT / "node_modules" / "jsdom").is_dir():
        return
    msg = (f"jsdom is not installed — {suite.relative_to(ROOT)} (the real-DOM suite) "
           f"DID NOT RUN. Run `pnpm install` in {ROOT}.")
    if os.environ.get("CHELA_REQUIRE_JS_TESTS"):
        pytest.fail(msg + " (CHELA_REQUIRE_JS_TESTS is set: a silent skip is not green)")
    pytest.skip(msg)


# Printed by tests/browser/fixture.mjs (its DID_NOT_RUN) when no browser could launch.
_BROWSER_DID_NOT_RUN = "browser suite DID NOT RUN"


@pytest.mark.parametrize("suite", _SUITES, ids=_IDS)
def test_js_suite(suite: Path):
    node = _node_or_skip([suite])
    _jsdom_or_skip(suite)
    # CMX-151: contained — node's per-file children die with this run, even if pytest is
    # SIGKILLed; a plain subprocess.run left 8 of them holding ~4 GB for 3.5 hours.
    proc = run_contained(
        [node, "--test", str(suite)], timeout=120, cwd=str(ROOT), env=_clean_env(),
        watch=_xdist_master(),
    )
    if proc.returncode != 0:
        pytest.fail(
            f"{suite.relative_to(ROOT)} failed:\n"
            f"{proc.stdout.decode()[-4000:]}\n{proc.stderr.decode()[-1000:]}"
        )
    out = proc.stdout.decode()
    if _BROWSER_DID_NOT_RUN in out:
        line = next(ln for ln in out.splitlines() if _BROWSER_DID_NOT_RUN in ln)
        pytest.skip(f"{suite.relative_to(ROOT)}: {line.lstrip('# ')}")


def test_a_browser_suite_that_could_not_launch_is_a_loud_skip_not_a_pass(monkeypatch):
    """CMX-383: without a browser, node skips every browser test and EXITS 0 — which, read
    by exit code alone, is a green pytest item for a suite that never ran. The suite prints
    :data:`_BROWSER_DID_NOT_RUN`; that line must become a skip naming the cause."""
    suite = ROOT / "tests" / "browser" / "dashboard.test.mjs"
    out = f"{_BROWSER_DID_NOT_RUN}: Chromium could not launch\n# SKIP\n".encode()
    monkeypatch.setattr(
        sys.modules[__name__], "run_contained",
        lambda *a, **k: subprocess.CompletedProcess(a, 0, out, b""),
    )
    with pytest.raises(pytest.skip.Exception, match="DID NOT RUN: Chromium could not launch"):
        test_js_suite(suite)


@pytest.mark.parametrize("required", [True, False], ids=["required-fails", "optional-skips"])
def test_a_missing_browser_fails_under_require_and_skips_loudly_otherwise(tmp_path, required):
    """CMX-383: the browser suite's own launch contract, exercised with a browser that
    cannot exist (``PLAYWRIGHT_BROWSERS_PATH`` at an empty dir). Under
    ``CHELA_REQUIRE_JS_TESTS`` ``launchChromium`` must THROW — the suite file fails, never a
    silent green; without it, it must return the :data:`_BROWSER_DID_NOT_RUN` reason that
    the loud pytest skip above keys on."""
    node = _node_or_skip([ROOT / "tests" / "browser" / "fixture.mjs"])
    if not (ROOT / "node_modules" / "playwright").is_dir():
        pytest.skip("playwright is not installed — run `pnpm install`")
    env = _clean_env()
    env["PLAYWRIGHT_BROWSERS_PATH"] = str(tmp_path)
    env.pop("CHELA_REQUIRE_JS_TESTS", None)
    if required:
        env["CHELA_REQUIRE_JS_TESTS"] = "1"
    script = (
        "import('./tests/browser/fixture.mjs').then(m => m.launchChromium())"
        ".then(r => { console.log('RESULT ' + JSON.stringify(r)); })"
    )
    proc = subprocess.run(
        [node, "--input-type=module", "-e", script], capture_output=True, text=True,
        timeout=60, cwd=str(ROOT), env=env,
    )
    if required:
        assert proc.returncode != 0, proc.stdout
        assert _BROWSER_DID_NOT_RUN in proc.stderr and "CHELA_REQUIRE_JS_TESTS" in proc.stderr
    else:
        assert proc.returncode == 0, proc.stderr
        assert "RESULT " in proc.stdout and _BROWSER_DID_NOT_RUN in proc.stdout
        assert '"browser"' not in proc.stdout


@pytest.mark.parametrize("worker,expected", [("gw0", "parent"), ("", None), (None, None)])
def test_an_xdist_worker_watches_its_master_and_a_plain_run_watches_nothing(
    monkeypatch, worker, expected,
):
    """CMX-151: the master is the run — a worker outlives its SIGKILL until the test ends."""
    if worker is None:
        monkeypatch.delenv("PYTEST_XDIST_WORKER", raising=False)
    else:
        monkeypatch.setenv("PYTEST_XDIST_WORKER", worker)
    monkeypatch.setattr(os, "getppid", lambda: 4242)
    assert _xdist_master() == (4242 if expected else None)


def test_a_js_suite_runs_contained_with_its_full_call_pinned(monkeypatch):
    """CMX-151: node runs under ``run_contained`` — argv, cwd, scrubbed env, timeout and the
    xdist master to watch — never a bare ``subprocess.run`` whose children can outlive it."""
    suite = ROOT / "tests" / "approvals.test.mjs"
    calls = []
    monkeypatch.setattr(shutil, "which", lambda name: "/opt/node")
    monkeypatch.setenv("PYTEST_XDIST_WORKER", "gw3")
    monkeypatch.setenv("NODE_CHANNEL_FD", "9")
    monkeypatch.setattr(os, "getppid", lambda: 4242)
    monkeypatch.setattr(
        sys.modules[__name__], "run_contained",
        lambda argv, **kw: (calls.append((argv, kw)),
                            subprocess.CompletedProcess(argv, 0, b"ok", b""))[1],
    )
    test_js_suite(suite)
    [(argv, kw)] = calls
    assert argv == ["/opt/node", "--test", str(suite)]
    assert set(kw) == {"timeout", "cwd", "env", "watch"}
    assert (kw["timeout"], kw["cwd"], kw["watch"]) == (120, str(ROOT), 4242)
    assert "NODE_CHANNEL_FD" not in kw["env"] and kw["env"]["PYTEST_XDIST_WORKER"] == "gw3"
