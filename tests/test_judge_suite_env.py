"""🧯 CMX-391 — the suite chela runs cannot reach live state or act as the live window.

The judge, `chela judge self-check` and `chela task-finished --self-check-experiments` all
run a repo's suite through one funnel, :func:`chela.judge.run_suite`. It is spawned from a
dispatched agent's shell or the daemon's, and both carry the LIVE `CHELA_DIR` plus the
agent's `CHELA_WID`. These tests read the env handed to `subprocess.run` itself — and run a
real child that reports what it sees — so the isolation is a property of the call site, not
of whatever conftest the suite under test happens to ship.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from chela import judge

LIVE_IDENTITY = {
    "CHELA_WID": "@83",
    "CHELA_ORCHESTRATOR_WID": "@1",
    "CHELA_TASK_ID": "3fd68a669748",
    "CHELA_ACTOR": "orchestrator",
    "CHELA_NOTIFY_URL": "https://ntfy.invalid/topic",
    "CHELA_DISPATCH_WORKFLOWS": "/repo/WORKFLOW.md",
    "TMUX": "/tmp/tmux-1000/default,1,0",
    "TMUX_PANE": "%83",
}


def _live_shell(monkeypatch, tmp_path) -> Path:
    """This process, dressed as a dispatched agent's shell: a live CHELA_DIR + identity."""
    live = tmp_path / "live-chela"
    live.mkdir()
    monkeypatch.setenv("CHELA_DIR", str(live))
    for key, value in LIVE_IDENTITY.items():
        monkeypatch.setenv(key, value)
    return live


def test_the_suite_subprocess_gets_a_fresh_chela_dir_and_no_identity(monkeypatch, tmp_path):
    """🔴 GUARD — the env handed to `subprocess.run`. Corrupt by passing `os.environ` (or
    `_no_color_env()` alone) through ⇒ `CHELA_DIR` is the live dir and `CHELA_WID` is there
    ⇒ RED."""
    live = _live_shell(monkeypatch, tmp_path)
    seen: dict = {}

    class _Done:
        returncode = 0
        stdout = "1 passed"
        stderr = ""

    def fake_run(cmd, **kw):
        env = kw["env"]
        seen["env"] = dict(env)
        seen["dir_existed"] = Path(env["CHELA_DIR"]).is_dir()
        return _Done()

    monkeypatch.setattr(judge.subprocess, "run", fake_run)
    judge.run_suite("pytest -q", tmp_path)

    env = seen["env"]
    assert Path(env["CHELA_DIR"]).resolve() != live.resolve()
    assert seen["dir_existed"], "CHELA_DIR must be a real, fresh directory while the suite runs"
    assert not Path(env["CHELA_DIR"]).exists(), "…and removed once it has finished"
    leaked = sorted(k for k in LIVE_IDENTITY if k in env)
    assert not leaked, f"chela identity leaked into the suite: {leaked}"
    assert not [k for k in env if k.startswith("CHELA_") and k != "CHELA_DIR"]
    # Hygiene the funnel already did must survive the rewrite.
    assert env["NO_COLOR"] == "1" and env["PYTHONDONTWRITEBYTECODE"] == "1"
    assert env["PATH"] == os.environ["PATH"]


def test_a_real_child_sees_the_isolated_env(monkeypatch, tmp_path):
    """The same property end to end: a real child process reports its own env."""
    live = _live_shell(monkeypatch, tmp_path)
    probe = ("import json, os; print(json.dumps({k: os.environ.get(k) for k in "
             f"{sorted(['CHELA_DIR', *LIVE_IDENTITY])!r}}}))")
    result = judge.run_suite(f"{sys.executable} -c {json.dumps(probe)}", tmp_path, timeout=60)
    assert result.ok and result.exit_code == 0, result.tail
    seen = json.loads(result.tail.strip().splitlines()[-1])
    assert seen.pop("CHELA_DIR") not in (None, str(live))
    assert seen == {k: None for k in LIVE_IDENTITY}
