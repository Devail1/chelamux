"""CMX-100 — the demo fleet's fake ``claude`` answers ``claude --resume <id>``.

The desktop demo archives a session (chela closes its window) and unarchives it, which
sends ``claude --resume <id>`` into a fresh window. In the synthetic fleet that must
re-run the SAME scripted session (``claude_demo_agent.py`` records its own arguments
under its session id) — and nothing else: an unknown id, or a malformed one, exits 2
without running anything.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

DEMO = Path(__file__).resolve().parent.parent / "scripts" / "demo"
SID = "8ff813c1-a83b-4fae-891b-6874e966cf0c"


def _load(name: str):
    spec = importlib.util.spec_from_file_location(f"chela_demo_{name}", DEMO / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def status_dir(tmp_path, monkeypatch):
    d = tmp_path / "chela-demo" / "status"
    d.mkdir(parents=True)
    monkeypatch.setenv("CHELA_DEMO_STATUS_DIR", str(d))
    return d


def test_resume_reruns_the_recorded_session(status_dir, monkeypatch):
    agent, fake = _load("claude_demo_agent"), _load("fake_claude")
    argv = ["--scene", "done", "--status-dir", str(status_dir), "--cwd", "/tmp/demo/worker",
            "--cwd-label", "~/worker", "--session-id", SID]
    agent.record_session(str(status_dir), SID, argv)
    assert json.loads((status_dir.parent / "sessions" / f"{SID}.json").read_text()) == {"argv": argv}

    calls = []
    monkeypatch.setattr(fake.os, "execv", lambda path, args: calls.append((path, args)))
    fake.main(["--resume", SID])
    assert calls == [(sys.executable, [sys.executable, str(DEMO / "claude_demo_agent.py"), *argv])]


def test_resume_of_an_unknown_or_malformed_id_runs_nothing(status_dir, monkeypatch):
    fake = _load("fake_claude")
    calls = []
    monkeypatch.setattr(fake.os, "execv", lambda *a: calls.append(a))
    assert fake.main(["--resume", SID]) == 2
    # A record DOES exist where a path-shaped id would reach (sessions/../planted.json):
    # only the session-id check stands between it and being run.
    (status_dir.parent / "sessions").mkdir()
    (status_dir.parent / "planted.json").write_text(json.dumps({"argv": ["--scene", "idle"]}))
    assert fake.main(["--resume", "../planted"]) == 2
    assert fake.main(["--resume"]) == 2
    assert calls == []
