"""CMX-176: the dashboard frontend's Vitest suite inside pytest (``judge.test_cmd``), so the
judge's TS mutations reach it. As in :mod:`tests.test_js_suites`, a suite that cannot run
skips loudly, or FAILS under ``CHELA_REQUIRE_JS_TESTS``."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from chela.procguard import run_contained

ROOT = Path(__file__).resolve().parent.parent
FRONTEND = ROOT / "chela" / "dashboard" / "frontend"
VITEST = FRONTEND / "node_modules" / "vitest" / "vitest.mjs"
# Budgets, from CI's standalone `frontend` job (33 green runs to 2026-10-26, idle runner):
# p95 typecheck 10 s, lint 13 s, lint:boundaries 3 s, the whole Vitest suite 112 s (max 115).
# Inside pytest the same commands share 4 cores with xdist's workers, so a full Vitest run gets
# well over p95 x 2: a 180 s budget timed it out twice in CI (CMX-352) and 300 never has.
GATE_TIMEOUT = 180      # the cheap gates: over 10x the slowest one's p95
VITEST_TIMEOUT = 300    # a whole Vitest run: 2.7x its idle p95
# Named by path so the judge's test selection (it reads test sources for the file names they
# mention) runs this file when the toolchain-proof component is mutated.
SAMPLE = FRONTEND / "src/features/version/lib/VersionBadge.tsx"


def _shown(out: bytes, n: int) -> str:
    """The last `n` chars of a tool's output, for a failure message. A tool's own "3 errors"
    is rewritten, because the judge counts `N errors` in the suite output as tests that
    failed to LOAD and would grade a guard that fired here as INVALID (CMX-176)."""
    return re.sub(r"(\d+) (errors?)\b", r"\1 × \2", out.decode(errors="replace")[-n:])


def _did_not_run(msg: str) -> None:
    if os.environ.get("CHELA_REQUIRE_JS_TESTS"):
        pytest.fail(msg + " (CHELA_REQUIRE_JS_TESTS is set: a silent skip is not green)")
    pytest.skip(msg)


def test_frontend_vitest_suite():
    node = shutil.which("node")
    if not node:
        _did_not_run("node is not installed — the frontend Vitest suite DID NOT RUN")
    if not VITEST.is_file():
        _did_not_run(f"vitest is not installed in {FRONTEND.relative_to(ROOT)} — the frontend "
                     f"suite DID NOT RUN. Run `pnpm install` in {ROOT}.")
    env = dict(os.environ)
    env.pop("NODE_CHANNEL_FD", None)    # CMX-252: a leaked IPC fd can abort node
    env.pop("NODE_CHANNEL_SERIALIZATION_MODE", None)
    proc = run_contained(
        # 300: the suite is ~4 CPU-minutes (CMX-221's Settings tests alone ~50s), and CI runs it
        # beside xdist's workers on 4 cores; 120 timed out on dev itself at 3d8e78d9.
        [str(node), str(VITEST), "run"], timeout=VITEST_TIMEOUT, cwd=str(FRONTEND), env=env,
        watch=os.getppid() if os.environ.get("PYTEST_XDIST_WORKER") else None,
    )
    if proc.returncode != 0:
        pytest.fail(f"the frontend Vitest suite failed:\n{_shown(proc.stdout, 4000)}\n"
                    f"{_shown(proc.stderr, 1000)}")


@pytest.mark.parametrize("missing", ["node", "vitest"])
@pytest.mark.parametrize("required", [True, False], ids=["required-fails", "optional-skips"])
def test_a_suite_that_cannot_run_fails_under_require_and_skips_otherwise(
    monkeypatch, missing, required,
):
    monkeypatch.setattr(shutil, "which", lambda _: None if missing == "node" else "/n")
    if missing == "vitest":
        monkeypatch.setattr(sys.modules[__name__], "VITEST", FRONTEND / "no-such-vitest.mjs")
    monkeypatch.delenv("CHELA_REQUIRE_JS_TESTS", raising=False)
    if required:
        monkeypatch.setenv("CHELA_REQUIRE_JS_TESTS", "1")
    monkeypatch.setattr(sys.modules[__name__], "run_contained",
                        lambda *_a, **_k: pytest.fail("ran a suite that cannot run"))
    # Catch both outcomes: a skip escaping a `raises(fail)` would mark this test SKIPPED, not red.
    with pytest.raises((pytest.fail.Exception, pytest.skip.Exception),
                       match=f"{missing} is not installed.*DID NOT RUN") as outcome:
        test_frontend_vitest_suite()
    assert outcome.type is (pytest.fail.Exception if required else pytest.skip.Exception)


@pytest.mark.parametrize("code,worker", [(0, "gw1"), (1, "gw1"), (0, None)])
def test_vitest_runs_contained_with_its_full_call_pinned(monkeypatch, code, worker):
    calls = []
    monkeypatch.setattr(shutil, "which", lambda _: "/opt/node")
    monkeypatch.setattr(sys.modules[__name__], "VITEST", SAMPLE)    # any file that exists
    monkeypatch.delenv("PYTEST_XDIST_WORKER", raising=False)
    if worker:
        monkeypatch.setenv("PYTEST_XDIST_WORKER", worker)
    monkeypatch.setenv("NODE_CHANNEL_FD", "9")
    monkeypatch.setenv("NODE_CHANNEL_SERIALIZATION_MODE", "json")
    monkeypatch.setattr(os, "getppid", lambda: 4242)
    monkeypatch.setattr(
        sys.modules[__name__], "run_contained",
        lambda argv, **kw: (calls.append((argv, kw)),
                            subprocess.CompletedProcess(argv, code, b"1 failed", b""))[1],
    )
    if code:
        with pytest.raises(pytest.fail.Exception, match="suite failed:\n1 failed"):
            test_frontend_vitest_suite()
    else:
        test_frontend_vitest_suite()
    [(argv, kw)] = calls
    assert argv == ["/opt/node", str(SAMPLE), "run"]
    assert set(kw) == {"timeout", "cwd", "env", "watch"}
    assert (kw["timeout"], kw["cwd"]) == (300, str(FRONTEND))
    assert kw["watch"] == (4242 if worker else None)
    assert not {"NODE_CHANNEL_FD", "NODE_CHANNEL_SERIALIZATION_MODE"} & set(kw["env"])
    assert "PATH" in kw["env"]


# The CI `frontend` job's gates, graded HERE by running the job's own commands
# (`pnpm <script>`), so the package.json scripts, the real tsconfig `include` and every config
# file are all under test (DEFEAT_SHAPES 176, 176b). In CI those gates only ever see the clean
# tree, where a weakened rule still passes. So each gate also runs over a copy of the frontend
# with its committed fixture overlaid (tests/fixtures/frontend_gates/<script>/, outside the
# real src tree so the normal build never sees it), and must exit non-zero with EXACTLY the
# expected findings: a rule that stops biting drops one, a rule that over-tightens adds one.
DEPCRUISE_CONFIG = FRONTEND / ".dependency-cruiser.cjs"
TSCONFIG = FRONTEND / "tsconfig.json"
ESLINT_CONFIG = FRONTEND / "eslint.config.js"
PACKAGE_JSON = FRONTEND / "package.json"
GATE_FIXTURES = ROOT / "tests" / "fixtures" / "frontend_gates"
# CMX-352: not `test`. Over the committed tree it is `vitest run` again, a second full suite in
# the same pytest run beside `test_frontend_vitest_suite`, and it was the one that timed out.
GATES = ["typecheck", "lint", "lint:boundaries"]


def _pnpm(cwd, script, *args, timeout=GATE_TIMEOUT):
    pnpm = shutil.which("pnpm")
    if not pnpm:
        _did_not_run(f"pnpm is not installed — the frontend `{script}` gate DID NOT RUN")
    if not (FRONTEND / "node_modules" / ".bin").is_dir():
        _did_not_run(f"{FRONTEND.relative_to(ROOT)}/node_modules is missing — the frontend "
                     f"`{script}` gate DID NOT RUN. Run `pnpm install` in {ROOT}.")
    env = {k: v for k, v in os.environ.items() if not k.startswith("NODE_CHANNEL")}
    # GitHub Actions makes vitest/tsc colour their output; the assertions below match plain text.
    env.pop("FORCE_COLOR", None)
    env["NO_COLOR"] = "1"
    proc = run_contained([pnpm, "--silent", "run", script, *args], timeout=timeout,
                         cwd=str(cwd), env=env)
    # (code, stdout, stderr), never the process: an assert on `proc.returncode` makes pytest
    # print the whole repr, unscrubbed output included (see `_shown`).
    return proc.returncode, proc.stdout, proc.stderr


def _violating_copy(tmp_path, script):
    """The frontend as committed, plus `script`'s fixture tree on top of it. Beside a copy of
    the legacy static/js (+ the collab modules it imports), which src/boot.ts imports
    (CMX-217), as in the repo."""
    tree = tmp_path / "dashboard" / "frontend"
    shutil.copytree(FRONTEND, tree, ignore=shutil.ignore_patterns("node_modules", "dist"))
    for d in ("js", "collab"):
        shutil.copytree(FRONTEND.parent / "static" / d, tmp_path / "dashboard" / "static" / d)
    # CMX-218: the UI kit's layout tests read the stylesheet the page ships.
    shutil.copy(FRONTEND.parent / "static" / "style.css", tmp_path / "dashboard" / "static")
    (tree / "node_modules").symlink_to(FRONTEND / "node_modules")
    shutil.copytree(GATE_FIXTURES / script.replace(":", "_"), tree, dirs_exist_ok=True)
    return tree


def test_shown_output_never_reads_as_a_load_error_to_the_judge():
    from chela.judge import _RE_ERRORS
    shown = _shown(b"x 14 dependency violations (14 errors, 0 warnings); Errors  1 error", 100)
    assert not _RE_ERRORS.search(shown) and "14 \u00d7 errors" in shown


@pytest.mark.parametrize("script", GATES)
def test_each_ci_gate_passes_the_committed_tree(script):
    code, out, err = _pnpm(FRONTEND, script)
    assert code == 0, _shown(out + err, 4000)


def test_the_gates_are_the_ci_frontend_job_s_less_the_vitest_run_graded_above():
    """CMX-352: every pnpm gate the CI `frontend` job runs is graded here, except `test`,
    which is `vitest run` and so already graded by `test_frontend_vitest_suite`."""
    import yaml
    steps = yaml.safe_load((ROOT / ".github/workflows/ci.yml").read_text())["jobs"]["frontend"]
    ci = [st["run"].removeprefix("pnpm -C chela/dashboard/frontend ") for st in steps["steps"]
          if st.get("run", "").startswith("pnpm -C chela/dashboard/frontend ")]
    assert ci == ["typecheck", "lint", "lint:boundaries", "test"]
    assert GATES == ["typecheck", "lint", "lint:boundaries"]
    assert json.loads(PACKAGE_JSON.read_text())["scripts"]["test"] == "vitest run"


# CI's standalone `frontend` job, p95 over 33 green runs to 2026-10-26 (seconds).
_MEASURED_P95 = {"typecheck": 10, "lint": 13, "lint:boundaries": 3, "test": 112}


@pytest.mark.parametrize("script", ["typecheck", "lint", "lint:boundaries", "test"])
def test_each_pnpm_run_gets_a_budget_of_at_least_twice_its_measured_p95(
    monkeypatch, tmp_path, script,
):
    calls = []

    def fake_run(argv, **kw):
        calls.append((argv, kw))
        out = [a for a in argv if a.startswith("--outputFile.json=")]
        if out:
            Path(out[0].split("=", 1)[1]).write_text('{"testResults": []}')
        return subprocess.CompletedProcess(argv, 0, b"", b"")

    monkeypatch.setattr(shutil, "which", lambda _: "/opt/pnpm")
    (tmp_path / "node_modules" / ".bin").mkdir(parents=True)
    monkeypatch.setattr(sys.modules[__name__], "FRONTEND", tmp_path)
    monkeypatch.setattr(sys.modules[__name__], "run_contained", fake_run)
    tree = tmp_path / "tree"
    if script == "test":    # the only way `test` runs now: over a fixture copy, with a report
        _run_with_report(tree)
    else:
        _pnpm(tree, script)
    [(argv, kw)] = calls
    assert argv[:4] == ["/opt/pnpm", "--silent", "run", script]
    assert (kw["cwd"], set(kw)) == (str(tree), {"timeout", "cwd", "env"})
    assert kw["timeout"] == (300 if script == "test" else 180)
    assert kw["timeout"] >= 2 * _MEASURED_P95[script]


def test_typecheck_is_strict_over_src_and_the_config_files_and_still_compiles_allowjs(tmp_path):
    tree = _violating_copy(tmp_path, "typecheck")
    with (tree / "vite.config.ts").open("a") as f:     # `include` must reach the root .ts too
        f.write("export const notANumber: number = 'x';\n")
    # ...and a legacy module converted to TypeScript in place (CMX-236: static/js/*.ts)
    (tree.parent / "static" / "js" / "converted.ts").write_text("export const n: number = 'x';\n")
    code, out, _ = _pnpm(tree, "typecheck", "--pretty", "false")
    assert code != 0, _shown(out, 4000)
    assert {line.split("(")[0] for line in out.decode().splitlines() if ": error TS" in line} == {
        "src/gates/implicit_any.ts",    # strict: noImplicitAny
        "src/gates/null_check.ts",      # strict: strictNullChecks
        "src/gates/index_access.ts",    # noUncheckedIndexedAccess
        "src/gates/type_import.ts",     # verbatimModuleSyntax
        "vite.config.ts",
        "../static/js/converted.ts",
    }, _shown(out, 4000)    # ...and src/gates/ok.tsx, which imports m.js (allowJs) and a .ts path, compiles


def test_lint_applies_strict_typescript_eslint_and_react_hooks_to_ts_and_tsx(tmp_path):
    code, out, err = _pnpm(_violating_copy(tmp_path, "lint"), "lint", "--format", "json")
    assert code != 0, _shown(err, 2000)
    got = {Path(f["filePath"]).name: sorted(m["ruleId"] for m in f["messages"])
           for f in json.loads(out) if f["messages"]}
    assert got == {
        "hooks.tsx": ["react-hooks/rules-of-hooks"],
        "any.ts": ["@typescript-eslint/no-explicit-any"],
        "non_null.ts": ["@typescript-eslint/no-non-null-assertion"],    # strict, not recommended
    }


def test_lint_boundaries_fails_on_exactly_the_imports_each_rule_forbids(tmp_path):
    tree = _violating_copy(tmp_path, "lint:boundaries")
    code, out, _ = _pnpm(tree, "lint:boundaries")
    assert code != 0, _shown(out, 2000)
    _, out, _ = _pnpm(tree, "lint:boundaries", "--output-type", "json")
    violations = json.loads(out)["summary"]["violations"]
    assert {v["rule"]["severity"] for v in violations} == {"error"}
    got = {(v["rule"]["name"], v["from"].removeprefix("src/"), v["to"].removeprefix("src/"))
           for v in violations}
    # Every fixture import NOT listed here is one the rules must allow.
    assert got == {
        ("entrypoint-boundary-from-app", "app.ts", "features/a/lib/x.ts"),
        ("entrypoint-boundary-from-app", "app.ts", "shared/s/lib/z.ts"),
        ("entrypoint-boundary-across-modules", "features/b/index.ts", "features/a/lib/y.ts"),
        ("entrypoint-boundary-across-modules", "features/b/types.ts", "features/a/lib/y.ts"),
        ("tests-through-entrypoints", "features/a/tests/a.ts", "features/a/lib/y.ts"),
        ("tests-through-entrypoints", "features/b/tests/b.ts", "features/a/tests/fixture.ts"),
        ("tests-folder-is-private", "features/a/index.ts", "features/a/tests/fixture.ts"),
        ("no-circular", "features/b/c1.ts", "features/b/c2.ts"),
    }


# Vitest's default `include`: what a spec file is named, wherever the config sends it.
SPEC_FILE = re.compile(r"\.(test|spec)\.[cm]?[jt]sx?$")
RED_SPEC = "src/features/gate/tests/red.spec.ts"    # the `test` gate fixture's one red spec


def _run_with_report(tree, *args):
    """`pnpm test` over `tree` with Vitest's JSON report beside its usual output."""
    report = tree.parent / "vitest-report.json"
    code, out, _ = _pnpm(tree, "test", *args, "--reporter=default", "--reporter=json",
                         f"--outputFile.json={report}", timeout=VITEST_TIMEOUT)
    if not report.is_file():
        pytest.fail(f"vitest wrote no JSON report:\n{_shown(out, 4000)}")
    return code, out, json.loads(report.read_text())


def _not_run_specs(tree, report):
    """Problems with a run's coverage of `tree`: a spec file under src/ the run never
    collected, a file it ran that is not one, a file with no test in it, a skipped or todo
    test. CMX-324: this replaces a pinned total that every spec-adding PR had to edit; it
    still catches Vitest silently running fewer specs (a glob or config change that drops
    files), by comparing against the files on disk instead of a number."""
    tree = tree.resolve()     # Vitest reports real paths
    on_disk = {p.relative_to(tree).as_posix() for p in (tree / "src").rglob("*")
               if p.is_file() and SPEC_FILE.search(p.name)}
    ran = {Path(f["name"]).resolve().relative_to(tree).as_posix(): f for f in report["testResults"]}
    problems = [f"never collected: {f}" for f in sorted(on_disk - set(ran))]
    problems += [f"not a spec file: {f}" for f in sorted(set(ran) - on_disk)]
    problems += [f"no tests in: {f}" for f, r in sorted(ran.items()) if not r["assertionResults"]]
    if report["numPendingTests"] or report["numTodoTests"]:
        problems.append(f"{report['numPendingTests']} skipped, {report['numTodoTests']} todo")
    return problems


@pytest.mark.parametrize("pending,todo,ok", [(0, 0, True), (1, 0, False), (0, 1, False)])
def test_coverage_check_flags_strays_empty_files_and_skips(tmp_path, pending, todo, ok):
    for f in ("src/a.test.ts", "src/b.spec.tsx", "src/c/d.test.mjs", "src/setup.ts", "x.test.ts"):
        (tmp_path / f).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / f).write_text("")
    one = [{"status": "passed"}]
    report = {"numPendingTests": pending, "numTodoTests": todo, "testResults": [
        {"name": str(tmp_path / "src/a.test.ts"), "assertionResults": one},
        {"name": str(tmp_path / "src/b.spec.tsx"), "assertionResults": []},
        {"name": str(tmp_path / "src/setup.ts"), "assertionResults": one},
    ]}
    assert _not_run_specs(tmp_path, report) == [
        "never collected: src/c/d.test.mjs",    # x.test.ts sits outside src/: not expected
        "not a spec file: src/setup.ts",
        "no tests in: src/b.spec.tsx",
    ] + ([] if ok else [f"{pending} skipped, {todo} todo"])


def test_test_fails_on_a_failing_spec_and_still_runs_the_real_ones(tmp_path):
    tree = _violating_copy(tmp_path, "test").resolve()     # Vitest reports real paths
    code, out, report = _run_with_report(tree)
    assert code != 0, _shown(out, 4000)
    assert _not_run_specs(tree, report) == [], _shown(out, 4000)
    failed = [(Path(f["name"]).resolve().relative_to(tree).as_posix(), a["title"])
              for f in report["testResults"] for a in f["assertionResults"]
              if a["status"] != "passed"]
    assert failed == [(RED_SPEC, "gate fixture: this test must fail")], _shown(out, 4000)


def test_a_config_that_drops_spec_files_fails_the_coverage_check(tmp_path):
    # Negative control (CMX-324): narrow Vitest's `include` to the gate fixture alone, as a
    # careless glob would, and the run must be reported as missing every other spec file.
    tree = _violating_copy(tmp_path, "test").resolve()
    config = tree / "vite.config.ts"
    narrowed = config.read_text().replace(
        "test: { environment: 'jsdom',", f"test: {{ include: ['{RED_SPEC}'], environment: 'jsdom',")
    assert narrowed != config.read_text()
    config.write_text(narrowed)
    _, out, report = _run_with_report(tree)
    ran = [Path(r["name"]).resolve() for r in report["testResults"]]
    assert ran == [tree / RED_SPEC], _shown(out, 4000)
    on_disk = sorted(p.relative_to(tree).as_posix() for p in (tree / "src").rglob("*")
                     if SPEC_FILE.search(p.name) and p != tree / RED_SPEC)
    assert on_disk, "no spec files to drop: the control proves nothing"
    assert _not_run_specs(tree, report) == [f"never collected: {f}" for f in on_disk]


def test_the_frontend_is_a_pnpm_workspace_member_with_exact_pins():
    assert "  - chela/dashboard/frontend\n" in (ROOT / "pnpm-workspace.yaml").read_text()
    pkg = json.loads((FRONTEND / "package.json").read_text())
    pins = {**pkg["dependencies"], **pkg["devDependencies"]}
    assert [d for d, v in pins.items() if not v[0].isdigit()] == []
