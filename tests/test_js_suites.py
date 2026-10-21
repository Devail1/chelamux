"""Every ``*.test.mjs`` in the repo, run under Node inside pytest — BY DISCOVERY.

CMX-216: the dashboard suites directly in ``tests/`` run under **Vitest**
(``vitest.config.mjs``, one Vitest run per file, pool ``forks``), so a legacy module can
import a ported ``.ts`` one. ``chela/dashboard/static/collab/`` and ``tests/browser/`` stay
on ``node --test``. Every fence below applies to both runners.

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
* A Vitest run that finds no file fails (Vitest exits 1 on "No test files found"), and
  :func:`test_vitest_finds_exactly_the_discovered_dashboard_suites` fails if its include
  list and this glob disagree.
* A missing ``node`` SKIPS LOUDLY, naming every suite that did not run. Set
  ``CHELA_REQUIRE_JS_TESTS=1`` (CI does) and a missing ``node`` is a FAILURE instead —
  CI must never report green on a suite it never executed.
* Same rule for **Vitest** (the root ``package.json`` devDependency). See
  :func:`_vitest_or_skip`.
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

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from chela.procguard import run_contained
from test_frontend import _shown

ROOT = Path(__file__).resolve().parent.parent
_SKIP_DIRS = {"node_modules", "vendor", ".git"}
VITEST = ROOT / "node_modules" / "vitest" / "vitest.mjs"
VITEST_CONFIG = ROOT / "vitest.config.mjs"


def js_suites() -> list[Path]:
    """Every JS test file in the repo — the whole repo, not just ``tests/``."""
    return sorted(
        p for p in ROOT.rglob("*.test.mjs")
        if not _SKIP_DIRS.intersection(p.relative_to(ROOT).parts)
    )


def runs_under_vitest(suite: Path) -> bool:
    """The dashboard suites, directly in ``tests/``; not collab's, not ``tests/browser/``."""
    return suite.parent == ROOT / "tests"


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


def _did_not_run(msg: str) -> None:
    if os.environ.get("CHELA_REQUIRE_JS_TESTS"):
        pytest.fail(msg + " (CHELA_REQUIRE_JS_TESTS is set: a silent skip is not green)")
    pytest.skip(msg)


def _vitest_or_skip(suite: Path) -> None:
    """A dashboard suite without Vitest DID NOT RUN — the same rule as a missing ``node``."""
    if not VITEST.is_file():
        _did_not_run(f"vitest is not installed — {suite.relative_to(ROOT)} DID NOT RUN. "
                     f"Run `pnpm install` in {ROOT}.")


def _argv(node: str, suite: Path) -> list[str]:
    if runs_under_vitest(suite):
        return [node, str(VITEST), "run", "--config", str(VITEST_CONFIG),
                str(suite.relative_to(ROOT))]
    return [node, "--test", str(suite)]


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
    _did_not_run(f"jsdom is not installed — {suite.relative_to(ROOT)} (the real-DOM suite) "
                 f"DID NOT RUN. Run `pnpm install` in {ROOT}.")


# Printed by tests/browser/fixture.mjs (its DID_NOT_RUN) when no browser could launch.
_BROWSER_DID_NOT_RUN = "browser suite DID NOT RUN"


@pytest.mark.parametrize("suite", _SUITES, ids=_IDS)
def test_js_suite(suite: Path):
    node = _node_or_skip([suite])
    if runs_under_vitest(suite):
        _vitest_or_skip(suite)
    _jsdom_or_skip(suite)
    # CMX-151: contained — node's per-file children (Vitest's forks) die with this run, even
    # if pytest is SIGKILLed; a plain subprocess.run left 8 of them holding ~4 GB for 3.5 hours.
    proc = run_contained(
        _argv(node, suite), timeout=120, cwd=str(ROOT), env=_clean_env(),
        watch=_xdist_master(),
    )
    if proc.returncode != 0:
        pytest.fail(
            f"{suite.relative_to(ROOT)} failed:\n"
            f"{_shown(proc.stdout, 4000)}\n{_shown(proc.stderr, 1000)}"
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


_VITEST_SUITE = ROOT / "tests" / "approvals_wiring.test.mjs"
_NODE_SUITE = ROOT / "chela" / "dashboard" / "static" / "collab" / "fit.test.mjs"


@pytest.mark.parametrize("suite,argv", [
    (_VITEST_SUITE, lambda vt: ["/opt/node", str(vt), "run", "--config",
                                str(ROOT / "vitest.config.mjs"), "tests/approvals_wiring.test.mjs"]),
    (_NODE_SUITE, lambda vt: ["/opt/node", "--test", str(_NODE_SUITE)]),
], ids=["vitest", "node-test"])
def test_a_js_suite_runs_contained_with_its_full_call_pinned(monkeypatch, suite, argv):
    """CMX-151: the suite runs under ``run_contained`` — argv, cwd, scrubbed env, timeout and
    the xdist master to watch — never a bare ``subprocess.run`` whose children can outlive it.
    CMX-216: a dashboard suite runs through Vitest with the repo config, a collab one under
    ``node --test``."""
    calls = []
    monkeypatch.setattr(shutil, "which", lambda name: "/opt/node")
    monkeypatch.setattr(sys.modules[__name__], "VITEST", _NODE_SUITE)    # any file that exists
    monkeypatch.setenv("PYTEST_XDIST_WORKER", "gw3")
    monkeypatch.setenv("NODE_CHANNEL_FD", "9")
    monkeypatch.setattr(os, "getppid", lambda: 4242)
    monkeypatch.setattr(
        sys.modules[__name__], "run_contained",
        lambda argv, **kw: (calls.append((argv, kw)),
                            subprocess.CompletedProcess(argv, 0, b"ok", b""))[1],
    )
    test_js_suite(suite)
    [(got, kw)] = calls
    assert got == argv(_NODE_SUITE)
    assert set(kw) == {"timeout", "cwd", "env", "watch"}
    assert (kw["timeout"], kw["cwd"], kw["watch"]) == (120, str(ROOT), 4242)
    assert "NODE_CHANNEL_FD" not in kw["env"] and kw["env"]["PYTEST_XDIST_WORKER"] == "gw3"


@pytest.mark.parametrize("rel,expected", [
    ("tests/approvals_wiring.test.mjs", True),
    ("tests/browser/dashboard.test.mjs", False),
    ("chela/dashboard/static/collab/fit.test.mjs", False),
    ("tests/js_helpers/x.test.mjs", False),
])
def test_only_the_suites_directly_in_tests_run_under_vitest(rel, expected):
    assert runs_under_vitest(ROOT / rel) is expected


def test_the_vitest_and_node_test_split_is_the_one_the_plan_names():
    """CMX-216: 124+ dashboard suites moved to Vitest; collab and tests/browser/ did not."""
    vitest = [p for p in _SUITES if runs_under_vitest(p)]
    # CMX-228: the decisions and task-modal suites moved on into the frontend's own Vitest
    # (frontend/src/**/tests), as each CMX-212 port slice moves its own.
    # CMX-219: the safety float, readouts, launcher and agent detail suites followed them.
    assert len(vitest) >= 90
    assert {str(p.relative_to(ROOT).parent) for p in _SUITES if not runs_under_vitest(p)} == {
        "tests/browser", "chela/dashboard/static/collab"}


def test_each_vitest_filter_names_exactly_one_suite():
    """Vitest's file filter is a substring match: a filter that is part of another suite's
    path would run that suite too, under this one's pytest item."""
    paths = [str(p.relative_to(ROOT)) for p in _SUITES]
    for p in _SUITES:
        if runs_under_vitest(p):
            rel = str(p.relative_to(ROOT))
            assert [q for q in paths if rel in q] == [rel]


@pytest.mark.parametrize("required", [True, False], ids=["required-fails", "optional-skips"])
def test_a_missing_vitest_fails_under_require_and_skips_loudly_otherwise(monkeypatch, required):
    monkeypatch.setattr(shutil, "which", lambda _: "/opt/node")
    monkeypatch.setattr(sys.modules[__name__], "VITEST", ROOT / "no-such-vitest.mjs")
    monkeypatch.delenv("CHELA_REQUIRE_JS_TESTS", raising=False)
    if required:
        monkeypatch.setenv("CHELA_REQUIRE_JS_TESTS", "1")
    monkeypatch.setattr(sys.modules[__name__], "run_contained",
                        lambda *_a, **_k: pytest.fail("ran a suite that cannot run"))
    with pytest.raises((pytest.fail.Exception, pytest.skip.Exception),
                       match="vitest is not installed.*approvals_wiring.test.mjs DID NOT RUN") as out:
        test_js_suite(_VITEST_SUITE)
    assert out.type is (pytest.fail.Exception if required else pytest.skip.Exception)


def test_a_node_test_suite_does_not_need_vitest(monkeypatch):
    """The collab suite still runs under ``node --test`` when Vitest is absent."""
    calls = []
    monkeypatch.setattr(shutil, "which", lambda _: "/opt/node")
    monkeypatch.setattr(sys.modules[__name__], "VITEST", ROOT / "no-such-vitest.mjs")
    monkeypatch.setenv("CHELA_REQUIRE_JS_TESTS", "1")
    monkeypatch.setattr(
        sys.modules[__name__], "run_contained",
        lambda argv, **kw: (calls.append(argv), subprocess.CompletedProcess(argv, 0, b"", b""))[1],
    )
    test_js_suite(_NODE_SUITE)
    assert calls == [["/opt/node", "--test", str(_NODE_SUITE)]]


def test_a_failing_suite_fails_its_item_and_never_reads_as_a_load_error(monkeypatch):
    """A red suite is a pytest FAILURE naming the file; Vitest's "<N> error" count is rewritten so the
    judge does not count it as a test that failed to load (see ``test_frontend._shown``)."""
    # `{n}`, not a digit: pytest prints this source on failure, which must not read as one.
    n = 1
    out = f" FAIL  tests/approvals_wiring.test.mjs\n Tests  {n} failed\n Errors  {n} error\n".encode()
    err = f"{n + 1} errors".encode()
    monkeypatch.setattr(
        sys.modules[__name__], "run_contained",
        lambda *a, **k: subprocess.CompletedProcess(a, 1, out, err),
    )
    with pytest.raises(pytest.fail.Exception) as failed:
        test_js_suite(_VITEST_SUITE)
    msg = str(failed.value)
    # Booleans, not `in msg`: pytest would print the message, and its error count would be read
    # by the judge as the very load error this test rules out.
    names_the_file = msg.startswith("tests/approvals_wiring.test.mjs failed:\n FAIL  tests/approvals")
    rewritten = "1 \N{MULTIPLICATION SIGN} error" in msg and "2 \N{MULTIPLICATION SIGN} errors" in msg
    assert names_the_file
    assert rewritten


def _vitest(*args: str) -> subprocess.CompletedProcess:
    node = _node_or_skip([VITEST_CONFIG])
    _vitest_or_skip(VITEST_CONFIG)
    return run_contained([node, str(VITEST), *args, "--config", str(VITEST_CONFIG)],
                         timeout=120, cwd=str(ROOT), env=_clean_env())


def test_vitest_finds_exactly_the_discovered_dashboard_suites():
    """The config's include list and this file's glob must agree: an include that matches
    nothing (or drops a suite) is the CMX-65 bug again, one layer down."""
    proc = _vitest("list", "--filesOnly", "--json")
    assert proc.returncode == 0, _shown(proc.stdout + proc.stderr, 2000)
    found = {Path(f["file"]) for f in json.loads(proc.stdout)}
    assert found == {p for p in _SUITES if runs_under_vitest(p)}


def test_a_vitest_run_that_finds_no_file_fails():
    """A filter that matches no suite must be red, never a green run of nothing."""
    proc = _vitest("run", "tests/no_such_suite.test.mjs")
    assert proc.returncode != 0
    assert "No test files found" in proc.stdout.decode() + proc.stderr.decode()
