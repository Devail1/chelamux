"""CMX-176: the dashboard frontend's Vitest suite inside pytest (``judge.test_cmd``), so the
judge's TS mutations reach it. As in :mod:`tests.test_js_suites`, a suite that cannot run
skips loudly, or FAILS under ``CHELA_REQUIRE_JS_TESTS``."""

from __future__ import annotations

import json
import os
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
SAMPLE = FRONTEND / "src/features/hello/lib/Hello.tsx"


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
        pytest.fail(f"the frontend Vitest suite failed:\n{proc.stdout.decode()[-4000:]}\n"
                    f"{proc.stderr.decode()[-1000:]}")


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


# The frontend job's other gates (depcruise, tsc, eslint) run in CI only over the clean tree,
# where a weakened rule still passes. Each config is graded here against a throwaway tree with
# one violation per rule, so a rule that stops biting goes red (DEFEAT_SHAPES 176).
DEPCRUISE_CONFIG = FRONTEND / ".dependency-cruiser.cjs"
TSCONFIG = FRONTEND / "tsconfig.json"
ESLINT_CONFIG = FRONTEND / "eslint.config.js"
BINS = {"depcruise": "dependency-cruiser/bin/dependency-cruise.mjs",
        "tsc": "typescript/bin/tsc", "eslint": "eslint/bin/eslint.js"}


def _tool(tmp_path, tool, *args, files):
    node = shutil.which("node")
    if not node:
        _did_not_run(f"node is not installed — the {tool} config guard DID NOT RUN")
    bin_ = FRONTEND / "node_modules" / BINS[tool]
    if not bin_.is_file():
        _did_not_run(f"{tool} is not installed in {FRONTEND.relative_to(ROOT)} — its config "
                     f"guard DID NOT RUN. Run `pnpm install` in {ROOT}.")
    (tmp_path / "node_modules").symlink_to(FRONTEND / "node_modules")
    (tmp_path / "tsconfig.json").write_text(f'{{"extends": "{TSCONFIG}", "include": ["src"]}}')
    for rel, text in files.items():
        (tmp_path / "src" / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / "src" / rel).write_text(text + "\n")
    env = {k: v for k, v in os.environ.items() if not k.startswith("NODE_CHANNEL")}
    return run_contained([node, str(bin_), *args], timeout=120, cwd=str(tmp_path), env=env)


def test_depcruise_flags_exactly_the_imports_each_boundary_rule_forbids(tmp_path):
    files = {   # every import that is NOT in the expected set below is one the rules must allow
        "app.ts": "import './features/a/index.ts'; import './features/a/lib/x.ts';\n"
                  "import './shared/s/index.ts'; import './shared/s/lib/z.ts';",
        "features/a/index.ts": "import './lib/x.ts'; import './tests/fixture.ts';",
        "features/a/lib/x.ts": "import './y.ts';",
        "features/a/lib/y.ts": "export {};",
        "features/a/tests/fixture.ts": "export {};",
        "features/a/tests/a.ts": "import '../index.ts'; import './fixture.ts'; import '../lib/y.ts';",
        "features/b/index.ts": "import '../a/index.ts'; import '../a/lib/y.ts'; import './c1.ts';",
        "features/b/c1.ts": "import './c2.ts';",
        "features/b/c2.ts": "import './c1.ts';",
        "features/b/tests/b.ts": "import '../index.ts'; import '../../a/tests/fixture.ts';",
        "shared/s/index.ts": "import './lib/z.ts';",
        "shared/s/lib/z.ts": "export {};",
    }
    proc = _tool(tmp_path, "depcruise", "--config", str(DEPCRUISE_CONFIG),
                 "--output-type", "json", "src", files=files)
    assert proc.returncode == 0, proc.stderr.decode()[-2000:]
    got = {(v["rule"]["name"], v["from"].removeprefix("src/"), v["to"].removeprefix("src/"))
           for v in json.loads(proc.stdout)["summary"]["violations"]}
    assert got == {
        ("entrypoint-boundary-from-app", "app.ts", "features/a/lib/x.ts"),
        ("entrypoint-boundary-from-app", "app.ts", "shared/s/lib/z.ts"),
        ("entrypoint-boundary-across-modules", "features/b/index.ts", "features/a/lib/y.ts"),
        ("tests-through-entrypoints", "features/a/tests/a.ts", "features/a/lib/y.ts"),
        ("tests-through-entrypoints", "features/b/tests/b.ts", "features/a/tests/fixture.ts"),
        ("tests-folder-is-private", "features/a/index.ts", "features/a/tests/fixture.ts"),
        ("no-circular", "features/b/c1.ts", "features/b/c2.ts"),
    }


def test_tsc_is_strict_and_still_compiles_allowjs_imports(tmp_path):
    bad = {"implicit_any.ts": "export function f(x) { return x; }",
           "null_check.ts": "export const s: string = null;",
           "index_access.ts": "const a: number[] = []; export const n: number = a[0];"}
    ok = {"m.js": "export const m = 1;",
          "ok.tsx": "import { m } from './m.js'; export const k: number = m;\n"
                    "export const el = <p>{k}</p>;"}
    proc = _tool(tmp_path, "tsc", "--noEmit", "-p", "tsconfig.json", files={**bad, **ok})
    out = proc.stdout.decode()
    assert proc.returncode != 0, out
    assert {line.split("(")[0] for line in out.splitlines() if ": error TS" in line} \
        == {f"src/{name}" for name in bad}, out


def test_eslint_flags_a_conditional_hook_and_an_explicit_any(tmp_path):
    files = {"bad.tsx": "import { useState } from 'react';\n"
                        "export function C({ on }: { on: boolean }) {\n"
                        "  if (on) { useState(0); }\n  const x: any = 1;\n  return <p>{x}</p>;\n}",
             "ok.tsx": "export const el = <p>ok</p>;"}
    proc = _tool(tmp_path, "eslint", "-c", str(ESLINT_CONFIG), "--format", "json", "src",
                 files=files)
    got = {Path(f["filePath"]).name: sorted(m["ruleId"] for m in f["messages"])
           for f in json.loads(proc.stdout)}
    assert got == {"bad.tsx": ["@typescript-eslint/no-explicit-any", "react-hooks/rules-of-hooks"],
                   "ok.tsx": []}, proc.stderr.decode()[-2000:]


def test_the_frontend_is_a_pnpm_workspace_member_with_exact_pins():
    assert "  - chela/dashboard/frontend\n" in (ROOT / "pnpm-workspace.yaml").read_text()
    pkg = json.loads((FRONTEND / "package.json").read_text())
    pins = {**pkg["dependencies"], **pkg["devDependencies"]}
    assert [d for d, v in pins.items() if not v[0].isdigit()] == []
