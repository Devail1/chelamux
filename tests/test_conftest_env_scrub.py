"""CMX-355: the suite never sees a CHELA_* var its runner inherited from a live chela.env."""
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _run_canary(extra_env):
    env = {"HOME": os.environ["HOME"], "PATH": os.environ["PATH"], **extra_env}
    probe = ("import os, conftest, json; print(json.dumps({k: os.environ.get(k) for k in "
             "('CHELA_AUTO_MERGE', 'CHELA_JUDGE_MAX_CONCURRENT', 'CHELA_REQUIRE_JS_TESTS')}))")
    out = subprocess.run([sys.executable, "-c", probe], cwd=ROOT / "tests", env=env,
                         capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr[-2000:]
    import json
    return json.loads(out.stdout.strip().splitlines()[-1])


def test_live_chela_env_is_scrubbed_but_the_runner_allowlist_survives():
    seen = _run_canary({"CHELA_AUTO_MERGE": "true", "CHELA_JUDGE_MAX_CONCURRENT": "2",
                        "CHELA_REQUIRE_JS_TESTS": "1"})
    assert seen == {"CHELA_AUTO_MERGE": None, "CHELA_JUDGE_MAX_CONCURRENT": None,
                    "CHELA_REQUIRE_JS_TESTS": "1"}


def test_automerge_default_holds_under_a_live_env():
    # The exact failure that made every nightly-sweep baseline red (CMX-341).
    env = {"HOME": os.environ["HOME"], "PATH": os.environ["PATH"], "CHELA_AUTO_MERGE": "true"}
    out = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "-n", "0",
                          "tests/test_automerge.py::test_disabled_by_default"],
                         cwd=ROOT, env=env, capture_output=True, text=True, timeout=120)
    assert out.returncode == 0, out.stdout[-2000:]
