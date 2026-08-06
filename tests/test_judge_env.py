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
import shutil
import subprocess
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


def _hook_text_including_scripts(before_run: str) -> str:
    """``before_run`` plus the text of any repo script it delegates to (CMX-151: the actual
    ``npm ci`` moved out of the inline hook and into ``scripts/npm-shared-install.sh``, so a
    literal search of the hook string alone would go blind to it). One level of indirection
    only — enough to follow the hook to the script that does the installing, not a general
    shell interpreter."""
    text = before_run
    for tok in re.findall(r"\S*scripts/\S+\.sh", before_run):
        script = ROOT / tok.lstrip("./")
        if script.is_file():
            text += "\n" + script.read_text()
    return text


def test_the_hook_that_builds_a_worktree_installs_what_the_suites_need():
    """⛔ THE LOAD-BEARING ONE. ``before_run`` is the JUDGE's environment too."""
    declared = _declared()
    if not declared:
        pytest.skip("package.json declares no npm dependencies — nothing to install")
    before_run = _wf().get("hooks", "before_run") or ""
    haystack = _hook_text_including_scripts(before_run)
    assert any(tok in haystack for tok in _INSTALLS), (
        f"package.json declares {sorted(declared)}, but WORKFLOW.md's `hooks.before_run` "
        f"({before_run!r}), including any script it delegates to, never installs it. That "
        f"hook builds every dispatched worktree AND every judge worktree, so the judge runs "
        f"`judge.test_cmd` against a worktree missing a dependency its own suites import: "
        f"baseline red, CANNOT VERIFY, forever (CMX-80)."
    )


def test_the_judge_provisions_its_own_worktree_and_does_not_trust_the_hook():
    """⛔ THE ROUND-2 ONE, and the reason the hook above is not enough on its own.

    ``_launch_agent`` runs ``hooks.before_run`` out of the WorkflowDef the DAEMON loaded —
    the WORKFLOW.md at the repo ROOT, on the default branch — never the copy on the PR
    branch under judgment. So the first fix for CMX-80 (which changed only that hook) was
    judged by a worktree built with the OLD hook, found jsdom missing, and reported CANNOT
    VERIFY on the very PR that fixed it. Config cannot fix what runs before it merges.
    ``provision_suite_env`` is in the JUDGED tree, so it takes effect the moment it is
    pushed — and it must be CALLED, before the baseline is measured.
    """
    src = (ROOT / "chela" / "judge.py").read_text()
    before_baseline = src.split("baseline = run_suite(", 1)[0]
    assert "provision_suite_env(worktree)" in before_baseline, (
        "run_experiments measures the baseline without provisioning the worktree first. A "
        "missing npm dependency then surfaces as `the suite is NOT GREEN` — an accusation "
        "against the PR for a fault of the box (CMX-80)."
    )


def test_provision_installs_a_declared_package_that_is_missing(tmp_path):
    """The real thing, offline: a worktree with a lockfile but no node_modules comes back
    with node_modules — which is exactly the state every judge worktree launches in."""
    if not shutil.which("npm"):
        pytest.skip("npm is not installed")
    for name in ("package.json", "package-lock.json"):
        shutil.copy(ROOT / name, tmp_path / name)
    assert not (tmp_path / "node_modules").exists()

    problem = judge.provision_suite_env(tmp_path)

    assert problem == "", f"provisioning failed: {problem}"
    for pkg in judge.declared_npm_packages(tmp_path):
        assert (tmp_path / "node_modules" / pkg).is_dir(), f"{pkg} still missing after npm ci"


def test_provision_is_a_no_op_when_there_is_nothing_to_install(tmp_path):
    """No package.json (most repos) — the judge must not go looking for npm at all."""
    assert judge.provision_suite_env(tmp_path) == ""


def test_provision_names_the_package_and_the_cwd_when_it_cannot_install(tmp_path):
    """⛔ The message is the deliverable. An unknown that does not name the missing package
    AND the directory it is missing from is a shrug: for three weeks the judge said only
    "exited 1" while the suite one pipe away was saying "jsdom is not installed"."""
    (tmp_path / "package.json").write_text(json.dumps({"devDependencies": {"jsdom": "^29"}}))
    # no package-lock.json → `npm ci` has nothing to install from, and cannot be run at all
    problem = judge.provision_suite_env(tmp_path)

    assert "jsdom" in problem
    assert str(tmp_path) in problem


def test_provision_python_is_a_no_op_when_there_is_no_pyproject(tmp_path):
    """No pyproject.toml (a pure-JS or docs repo) — the judge must not go looking for a
    Python venv at all."""
    assert judge._provision_python_env(tmp_path) == ""


def test_provision_python_is_a_no_op_when_the_venv_already_works(tmp_path, monkeypatch):
    """⭐ COUNTERWEIGHT. A worktree that already HAS a working `.venv` must return "" without
    ever shelling out — otherwise a judge that ALWAYS runs `uv sync` (and always reports a
    problem if that happens to fail for unrelated reasons) would pass this file's other guards
    just as easily as the real, conditional fix."""
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "t"\n')
    exe = judge._venv_python(tmp_path)
    exe.parent.mkdir(parents=True)
    exe.write_text("#!/usr/bin/env python\n")
    monkeypatch.setattr(judge.subprocess, "run", lambda *a, **k: pytest.fail(
        "uv sync was invoked against a worktree that already has a working .venv"))

    assert judge._provision_python_env(tmp_path) == ""
    assert judge.provision_suite_env(tmp_path) == ""


def test_provision_python_reports_missing_uv_on_path(tmp_path, monkeypatch):
    """No `.venv`, and no `uv` to build one with — an honest unknown, not a crash and not a
    silent "" that sends the baseline in to fail for want of a cause."""
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "t"\n')
    monkeypatch.setattr(judge.shutil, "which", lambda _name: None)

    problem = judge._provision_python_env(tmp_path)

    assert ".venv" in problem
    assert "uv" in problem
    assert str(tmp_path) in problem


def test_provision_python_sync_uses_all_extras(tmp_path, monkeypatch):
    """⛔ CMX-218 mutation-round finding: `test_provision_python_syncs_a_missing_venv` below
    exercises a pyproject with NO extras declared at all, so it cannot tell `uv sync
    --all-extras` apart from a bare `uv sync` — the judge fed a mutant that dropped
    `--all-extras` and the whole suite stayed green. The docstring's entire claim is that
    `--all-extras` is what makes this match `hooks.before_run` and avoid the CMX-21 trap
    (dashboard/telegram tests false-failing on a default-only sync); pin the argv directly."""
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "t"\n')
    monkeypatch.setattr(judge.shutil, "which", lambda _name: "/usr/bin/uv")
    calls = []

    def _fake_run(cmd, **kwargs):
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(judge.subprocess, "run", _fake_run)

    judge._provision_python_env(tmp_path)

    assert len(calls) == 1
    assert calls[0] == ["uv", "sync", "--all-extras", "--quiet"]


def test_provision_python_syncs_a_missing_venv(tmp_path):
    """⛔ CMX-218, the real thing. Offline-safe: a minimal pyproject.toml with no third-party
    dependencies, so `uv sync` needs nothing but the interpreter `uv` already manages — the
    exact incident this closes was a judge worktree with no `.venv` at all."""
    if not shutil.which("uv"):
        pytest.skip("uv is not installed")
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "t"\nversion = "0.0.0"\nrequires-python = ">=3.11"\n'
    )
    assert not judge._venv_python(tmp_path).is_file()

    problem = judge.provision_suite_env(tmp_path)

    assert problem == "", f"provisioning failed: {problem}"
    assert judge._venv_python(tmp_path).is_file()


def test_an_unprovisionable_worktree_is_an_environment_unknown_not_a_red_suite(tmp_path, monkeypatch):
    """CANNOT VERIFY, and the report must blame the BOX, not the PR. The suite is never even
    run: a baseline nobody could provision measures nothing about the code."""
    monkeypatch.setattr(judge, "_git_dirty", lambda _wt: False)
    monkeypatch.setattr(judge, "provision_suite_env", lambda _wt, **_kw: "jsdom is not installed")
    monkeypatch.setattr(judge, "run_suite", lambda *a, **k: pytest.fail(
        "the suite was RUN against a worktree the judge knew it could not provision"))

    report = judge.run_experiments(
        tmp_path, "pytest -q",
        {"experiments": [{"file": "a.py", "before": "x", "after": "y", "why": "w"}]},
    )

    assert report.state == judge.J_CANNOT_VERIFY
    assert "could not be PROVISIONED" in report.cannot_verify
    assert "jsdom is not installed" in report.cannot_verify
    assert "NOT GREEN" not in report.cannot_verify        # ⛔ never blame the PR for the box


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
