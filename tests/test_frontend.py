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
        [str(node), str(VITEST), "run"], timeout=120, cwd=str(FRONTEND), env=env,
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
    assert (kw["timeout"], kw["cwd"]) == (120, str(FRONTEND))
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
GATES = ["typecheck", "lint", "lint:boundaries", "test"]


def _pnpm(cwd, script, *args):
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
    proc = run_contained([pnpm, "--silent", "run", script, *args], timeout=180,
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


def test_typecheck_is_strict_over_src_and_the_config_files_and_still_compiles_allowjs(tmp_path):
    tree = _violating_copy(tmp_path, "typecheck")
    with (tree / "vite.config.ts").open("a") as f:     # `include` must reach the root .ts too
        f.write("export const notANumber: number = 'x';\n")
    code, out, _ = _pnpm(tree, "typecheck", "--pretty", "false")
    assert code != 0, _shown(out, 4000)
    assert {line.split("(")[0] for line in out.decode().splitlines() if ": error TS" in line} == {
        "src/gates/implicit_any.ts",    # strict: noImplicitAny
        "src/gates/null_check.ts",      # strict: strictNullChecks
        "src/gates/index_access.ts",    # noUncheckedIndexedAccess
        "src/gates/type_import.ts",     # verbatimModuleSyntax
        "vite.config.ts",
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


def test_test_fails_on_a_failing_spec_and_still_runs_the_real_ones(tmp_path):
    code, out, _ = _pnpm(_violating_copy(tmp_path, "test"), "test")
    assert code != 0, _shown(out, 4000)
    plain = re.sub(r"\x1b\[[0-9;]*m", "", out.decode())   # CI colours vitest's summary
    assert re.search(r"Tests\s+1 failed \| 35 passed \(36\)", plain), _shown(out, 4000)


def test_the_frontend_is_a_pnpm_workspace_member_with_exact_pins():
    assert "  - chela/dashboard/frontend\n" in (ROOT / "pnpm-workspace.yaml").read_text()
    pkg = json.loads((FRONTEND / "package.json").read_text())
    pins = {**pkg["dependencies"], **pkg["devDependencies"]}
    assert [d for d, v in pins.items() if not v[0].isdigit()] == []
