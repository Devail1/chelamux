"""⚖️🧹 CMX-97 slice C2 — the composite GitHub Action (``action.yml`` + its run script, which
``exec``s ``chela judge sweep --github``). Throwaway repos; ``gh`` is always a fake."""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
ACTION_YML = REPO_ROOT / "action.yml"
ACTION_SH = REPO_ROOT / "scripts" / "judge-sweep-action.sh"
PY_BIN = str(Path(sys.executable).parent)
CALC = "def clamp(x, lo):\n    if x < lo:\n        return lo\n    return x\n"
BIG = CALC + '\n\ndef is_big(x):\n    if x > 100:\n        return "big"\n    return "small"\n'
FAKE_CHELA = "#!/usr/bin/env bash\nprintf '%s\\n' \"$@\" > \"$ARGV_LOG\"\nexit \"${FAKE_EXIT:-0}\"\n"


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), "-c", "user.email=t@t", "-c", "user.name=t",
                           *args], check=True, capture_output=True, text=True).stdout.strip()


def _repo(root: Path, feature: str = CALC) -> Path:
    """``main`` holds calc.py + an unrelated test; ``feature`` rewrites calc.py on top."""
    (root / "tests").mkdir(parents=True)
    (root / "calc.py").write_text(CALC)
    (root / "tests" / "test_calc.py").write_text(
        "from calc import clamp\n\n\ndef test_clamp():\n    assert clamp(-1, 0) == 0\n")
    _git(root, "init", "-q", "-b", "main")
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "base")
    _git(root, "checkout", "-qb", "feature")
    (root / "calc.py").write_text(feature)
    _git(root, "commit", "-qam", "feature", "--allow-empty")
    return root


def _env(tmp_path: Path, **extra: str) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(("CHELA_", "GITHUB_", "INPUT_", "PR_", "PYTEST_"))
           and k not in ("TMUX", "TMUX_PANE")}
    fake = tmp_path / "fake-chela"
    fake.write_text(FAKE_CHELA)
    fake.chmod(0o755)
    (tmp_path / "home").mkdir(exist_ok=True)
    env.update(HOME=str(tmp_path / "home"), PATH=f"{PY_BIN}:{env.get('PATH', '')}",
               ARGV_LOG=str(tmp_path / "argv"), CHELA_SWEEP_BIN=str(fake))
    return env | extra


def _run(tmp_path: Path, repo: Path, **env: str) -> subprocess.CompletedProcess:
    return subprocess.run(["bash", str(ACTION_SH)], cwd=str(repo), env=_env(tmp_path, **env),
                          capture_output=True, text=True, timeout=300)


# --- action.yml -------------------------------------------------------------------------------


def test_action_yml_installs_chela_into_a_runner_temp_venv_and_runs_the_script():
    action = yaml.safe_load(ACTION_YML.read_text())
    assert action["runs"]["using"] == "composite"
    install, run = action["runs"]["steps"]
    assert install["env"] == {"CHELA_PYTHON": "${{ inputs.python }}"}
    assert install["run"].splitlines() == [
        "set -euo pipefail",
        'venv="$RUNNER_TEMP/chela-judge-sweep"',
        '"$CHELA_PYTHON" -m venv "$venv"',
        '"$venv/bin/python" -m pip install --quiet --disable-pip-version-check '
        '"$GITHUB_ACTION_PATH"',
        'echo "CHELA_SWEEP_BIN=$venv/bin/chela" >> "$GITHUB_ENV"']
    assert run["id"] == "sweep" and run["shell"] == install["shell"] == "bash"
    assert run["run"] == 'bash "$GITHUB_ACTION_PATH/scripts/judge-sweep-action.sh"'
    assert run["env"] == {
        "GH_TOKEN": "${{ inputs.github-token }}", "INPUT_BASE": "${{ inputs.base }}",
        "INPUT_TEST_CMD": "${{ inputs.test-cmd }}", "INPUT_COMMENT": "${{ inputs.comment }}",
        "PR_BASE_SHA": "${{ github.event.pull_request.base.sha }}"}
    assert {k: v["default"] for k, v in action["inputs"].items()} == {
        "base": "", "test-cmd": "", "comment": "true", "github-token": "${{ github.token }}",
        "python": "python3"}
    assert not any(v["required"] for v in action["inputs"].values())
    assert action["outputs"] == {
        "verdict": {"description": "clean | survivors | unknown | cannot_verify | error",
                    "value": "${{ steps.sweep.outputs.verdict }}"},
        "survivors": {"description": "Number of surviving mutations.",
                      "value": "${{ steps.sweep.outputs.survivors }}"}}


def test_action_yml_never_interpolates_into_a_script_and_needs_only_github_token():
    text = ACTION_YML.read_text()
    for step in yaml.safe_load(text)["runs"]["steps"]:
        assert "${{" not in step["run"], step["name"]     # ⛔ script injection: env only
    exprs = set(re.findall(r"\$\{\{\s*([^}]*?)\s*\}\}", text))
    assert exprs == {"inputs.python", "inputs.github-token", "inputs.base", "inputs.test-cmd",
                     "inputs.comment", "github.event.pull_request.base.sha", "github.token",
                     "steps.sweep.outputs.verdict", "steps.sweep.outputs.survivors"}
    assert "secrets" not in text and "ANTHROPIC" not in text and "CLAUDE" not in text


# --- the script: argv + exit code -------------------------------------------------------------


@pytest.mark.parametrize("code", [0, 1, 2, 3])
def test_the_script_exits_with_the_sweeps_own_code(tmp_path, code):
    out = _run(tmp_path, _repo(tmp_path / "r"), PR_BASE_SHA="main", FAKE_EXIT=str(code))
    assert out.returncode == code, out.stderr


@pytest.mark.parametrize("env, extra", [
    ({"PR_BASE_SHA": "main"}, []),
    ({"PR_BASE_SHA": "main", "INPUT_BASE": ""}, []),
    ({"PR_BASE_SHA": "nope", "INPUT_BASE": "main~0"}, []),
    ({"INPUT_BASE": "main~0"}, []),
    ({"PR_BASE_SHA": "main", "INPUT_TEST_CMD": "make test; echo $HOME"},
     ["--test-cmd", "make test; echo $HOME"]),
    ({"PR_BASE_SHA": "main", "INPUT_COMMENT": "false"}, ["--no-comment"]),
    ({"PR_BASE_SHA": "main", "INPUT_COMMENT": "true"}, []),
    ({"PR_BASE_SHA": "main", "INPUT_TEST_CMD": "t", "INPUT_COMMENT": "no"},
     ["--test-cmd", "t", "--no-comment"]),
])
def test_the_script_argv(tmp_path, env, extra):
    out = _run(tmp_path, _repo(tmp_path / "r"), **env)
    assert out.returncode == 0, out.stderr
    base = env.get("INPUT_BASE") or env["PR_BASE_SHA"]
    assert (tmp_path / "argv").read_text().splitlines() == ["judge", "sweep", "--base", base, "--github", *extra]


def test_the_script_refuses_without_a_base(tmp_path):
    out = _run(tmp_path, _repo(tmp_path / "r"), INPUT_BASE="", PR_BASE_SHA="")
    assert out.returncode == 2
    assert "::error::chela judge sweep: no base to diff against" in out.stdout
    assert not (tmp_path / "argv").exists()


# --- the script: fetching the base -----------------------------------------------------------


def test_a_shallow_clone_is_deepened_and_a_missing_base_fetched(tmp_path):
    up = _repo(tmp_path / "up", BIG)
    base = _git(up, "rev-parse", "main")
    clone = tmp_path / "clone"
    subprocess.run(["git", "clone", "-q", "--depth", "1", "--branch", "feature",
                    f"file://{up}", str(clone)], check=True, capture_output=True)
    _git(up, "checkout", "-qb", "later", "main")     # a base the clone has never seen
    _git(up, "commit", "-qm", "later", "--allow-empty")
    later = _git(up, "rev-parse", "HEAD")
    assert _git(clone, "rev-parse", "--is-shallow-repository") == "true"
    for sha in (base, later):
        out = _run(tmp_path, clone, PR_BASE_SHA=sha)
        assert out.returncode == 0, out.stderr
        assert _git(clone, "cat-file", "-t", sha) == "commit"
    assert _git(clone, "rev-parse", "--is-shallow-repository") == "false"
    assert _git(clone, "merge-base", base, "HEAD") == base


def _real_chela(tmp_path: Path) -> tuple[Path, Path]:
    """A ``chela`` that runs this checkout's CLI and a ``gh`` that logs argv + stdin."""
    bindir = tmp_path / "bin"
    bindir.mkdir()
    gh_log = tmp_path / "gh.jsonl"
    (bindir / "gh").write_text(
        f"#!{sys.executable}\nimport json, sys\nopen({str(gh_log)!r}, 'a').write(json.dumps("
        "{'argv': sys.argv[1:], 'stdin': sys.stdin.read()}) + '\\n')\n")
    (bindir / "chela").write_text(
        f'#!/usr/bin/env bash\nexec "{sys.executable}" -c "import sys; '
        "from chela.main import main; sys.argv[0]='chela'; main()\" \"$@\"\n")
    for f in ("gh", "chela"):
        (bindir / f).chmod(0o755)
    return bindir, gh_log


def _gh_env(tmp_path: Path, bindir: Path) -> dict[str, str]:
    (tmp_path / "event.json").write_text(json.dumps({"pull_request": {"number": 7}}))
    return {"PATH": f"{bindir}:{PY_BIN}:{os.environ['PATH']}",
            "CHELA_SWEEP_BIN": str(bindir / "chela"), "GITHUB_REPOSITORY": "o/r",
            "GITHUB_EVENT_PATH": str(tmp_path / "event.json"),
            "GITHUB_OUTPUT": str(tmp_path / "output"),
            "GITHUB_STEP_SUMMARY": str(tmp_path / "summary.md"), "PYTHONPATH": str(REPO_ROOT)}


def test_a_base_that_cannot_be_fetched_is_cannot_verify_never_a_pass(tmp_path):
    repo = _repo(tmp_path / "r", BIG)
    _git(repo, "remote", "add", "origin", f"file://{tmp_path / 'gone'}")
    bindir, _ = _real_chela(tmp_path)
    out = _run(tmp_path, repo, PR_BASE_SHA="f" * 40, INPUT_COMMENT="false",
               **_gh_env(tmp_path, bindir))
    assert out.returncode == 2, out.stdout + out.stderr
    assert f"{tmp_path / 'gone'}" in out.stderr            # the fetch WAS tried, and failed
    assert f"⚠ CANNOT VERIFY — `{'f' * 40}` does not resolve to a merge base" in out.stdout
    assert "verdict=cannot_verify\n" in (tmp_path / "output").read_text()


def test_end_to_end_survivors_post_one_comment_and_fail_the_job(tmp_path):
    repo = _repo(tmp_path / "r", BIG)
    bindir, gh_log = _real_chela(tmp_path)
    out = _run(tmp_path, repo, PR_BASE_SHA="main", INPUT_COMMENT="true",
               **_gh_env(tmp_path, bindir))
    assert out.returncode == 1, out.stdout + out.stderr
    calls = [json.loads(line) for line in gh_log.read_text().splitlines()]
    assert [c["argv"][:4] for c in calls] == [
        ["api", "--paginate", "repos/o/r/issues/7/comments", "--jq"],
        ["api", "--method", "POST", "repos/o/r/issues/7/comments"]]
    body = json.loads(calls[1]["stdin"])["body"]
    assert body.startswith("<!-- chela-judge-sweep -->\n### ⚖️🧹 chela judge sweep — ❌")
    assert "`calc.py:8`" in body
    assert "verdict=survivors\n" in (tmp_path / "output").read_text()
    assert _git(repo, "status", "--porcelain") == ""
