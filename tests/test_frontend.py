"""CMX-176: the dashboard frontend's Vitest suite inside pytest (``judge.test_cmd``), so the
judge's TS mutations reach it. As in :mod:`tests.test_js_suites`, a suite that cannot run
skips loudly, or FAILS under ``CHELA_REQUIRE_JS_TESTS``."""

from __future__ import annotations

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
    exc = pytest.fail.Exception if required else pytest.skip.Exception
    with pytest.raises(exc, match=f"{missing} is not installed.*DID NOT RUN"):
        test_frontend_vitest_suite()


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
