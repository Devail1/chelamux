"""⚖️ THE JUDGE'S ENVIRONMENT — the hook that builds a worktree must build one the suite can be GREEN in.

⛔ THE BUG THIS FILE EXISTS FOR (measured 2026-07-15, CMX-80). The judge shipped, ran on
every PR that reached ``awaiting_review``, and verified NOTHING: it returned CANNOT VERIFY
every single time. Not one mutation was ever applied. The cause was not in ``judge.py`` at
all — it was two lines of ``WORKFLOW.md`` that disagreed with each other:

* ``judge.test_cmd`` sets ``CHELA_REQUIRE_JS_TESTS=1``, which (deliberately, and this is the
  right call) turns a JS suite that CANNOT RUN from a silent skip into a FAILURE;
* ``hooks.before_run`` synced the venv and never ran ``npm ci``, so jsdom was absent from
  every fresh worktree and the two real-DOM suites could not run.

So the judge's BASELINE — the suite as the PR ships it, before any mutation — was red in
every judge worktree, and a red baseline is CANNOT VERIFY by design (every SURVIVED verdict
means "the suite passed under the mutation", which is worthless if the suite was already
failing). The judge was correct, honest, and completely inert.

⛔ The class, not the instance. CI installing jsdom did not save the judge, because CI's
environment and the dispatcher's are TWO LISTS, and only one of them was updated. Any new
external import in a ``.test.mjs`` re-opens the same hole. So this file checks the two
halves against each other mechanically:

* every module a JS suite imports from outside the repo is DECLARED in ``package.json``;
* whatever ``package.json`` declares, ``hooks.before_run`` INSTALLS — in the same worktree
  the judge will run ``judge.test_cmd`` in;
* and ``judge.test_cmd`` still makes a suite that could not run a FAILURE, because the other
  way to make this file green is to delete the env var, and that "fix" buys a judge that
  mutates the dashboard's JS, watches a suite that never ran report green, and sends a GOOD
  PR back.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from chela import judge, workflow

ROOT = Path(__file__).resolve().parent.parent
WORKFLOW_MD = ROOT / "WORKFLOW.md"
PACKAGE_JSON = ROOT / "package.json"

# The specifier of a real import — a STATEMENT, not the word "from" wherever it appears (a
# test name reading "…wired to nothing" is not a dependency, and a looser regex says it is).
_IMPORTS = (
    # import x from 'y' / export … from 'y', braces possibly spanning lines
    re.compile(r"""^[ \t]*(?:import|export)\b[^'"]*?\bfrom\s*['"]([^'"]+)['"]""", re.M),
    re.compile(r"""^[ \t]*import\s*['"]([^'"]+)['"]""", re.M),        # side-effect import
    re.compile(r"""\bimport\(\s*['"]([^'"]+)['"]"""),                 # await import('y')
)

# A bare specifier that is neither relative nor a node builtin is an npm package: it exists
# only if something installed it. (Node's builtins are importable as `node:fs` and, for the
# older ones, bare — hence both spellings.)
_NODE_BUILTINS = {
    "assert", "buffer", "child_process", "crypto", "events", "fs", "http", "https",
    "module", "os", "path", "process", "stream", "test", "timers", "url", "util", "worker_threads",
}

# Whatever installs the declared dependencies. `npm ci` is the one the repo (and CI) uses;
# an equivalent install is fine — an absent one is the bug.
_INSTALLS = ("npm ci", "npm install", "npm i ", "pnpm install", "yarn install")


def _wf():
    return workflow.load_workflow(WORKFLOW_MD)


def _declared() -> set[str]:
    pkg = json.loads(PACKAGE_JSON.read_text())
    return set(pkg.get("dependencies", {})) | set(pkg.get("devDependencies", {}))


def _external_imports(text: str) -> set[str]:
    out = set()
    for spec in {s for rx in _IMPORTS for s in rx.findall(text)}:
        if spec.startswith((".", "/")) or spec.startswith("node:"):
            continue
        if spec in _NODE_BUILTINS:
            continue
        # `jsdom/lib/…` is still the `jsdom` package; `@scope/pkg/x` is still `@scope/pkg`.
        parts = spec.split("/")
        out.add("/".join(parts[:2]) if spec.startswith("@") else parts[0])
    return out


def _js_suites() -> list[Path]:
    from test_js_suites import js_suites                # ONE discovery, not a second list
    return js_suites()


def test_every_npm_package_a_js_suite_imports_is_declared():
    """An import nothing installs is a suite that cannot run — and under
    CHELA_REQUIRE_JS_TESTS that is a red baseline, i.e. a judge that verifies nothing."""
    declared = _declared()
    for suite in _js_suites():
        missing = _external_imports(suite.read_text()) - declared
        assert not missing, (
            f"{suite.relative_to(ROOT)} imports {sorted(missing)}, which package.json does "
            f"not declare — nothing installs it, so the suite cannot run, so the judge's "
            f"baseline is red and every PR comes back CANNOT VERIFY (CMX-80)"
        )


def test_the_hook_that_builds_a_worktree_installs_what_the_suites_need():
    """⛔ THE LOAD-BEARING ONE. ``before_run`` is the JUDGE's environment too."""
    declared = _declared()
    if not declared:
        pytest.skip("package.json declares no npm dependencies — nothing to install")
    before_run = _wf().get("hooks", "before_run") or ""
    assert any(tok in before_run for tok in _INSTALLS), (
        f"package.json declares {sorted(declared)}, but WORKFLOW.md's `hooks.before_run` "
        f"({before_run!r}) never installs it. That hook builds every dispatched worktree AND "
        f"every judge worktree, so the judge runs `judge.test_cmd` against a worktree missing "
        f"a dependency its own suites import: baseline red, CANNOT VERIFY, forever (CMX-80)."
    )


def test_the_judges_suite_still_makes_an_unrunnable_js_suite_a_failure():
    """The other way to green the test above is to stop demanding the JS suites run. ⛔ That
    is not a fix: it hands the judge a suite that can quietly do nothing, and a suite that
    cannot fail makes every mutation SURVIVE — a false BLOCK on a good PR."""
    test_cmd = judge.judge_test_cmd(_wf())
    assert "CHELA_REQUIRE_JS_TESTS" in test_cmd, (
        f"WORKFLOW.md's `judge.test_cmd` ({test_cmd!r}) no longer sets CHELA_REQUIRE_JS_TESTS, "
        "so a missing `node` or a missing `npm ci` makes the .mjs suites SKIP — silently, and "
        "green. The judge would then measure its mutations against a suite that never ran."
    )
