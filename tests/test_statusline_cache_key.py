"""CMX-30 — the statusLine cache must survive a ``/`` in the window name.

Dispatched windows are named ``<org>/cmx-N-<slug>``. The hook wrote
``context/<window name>.json``, so the ``/`` made the path a subdirectory that does not
exist: the write failed, statusLine stderr is never shown, and every dispatched agent and
judge cached nothing for six days. The key is now ONE mapping (``chela/cachekey.py``)
used by the writer (the hook script) and the reader (``chela.context``).

These tests drive the REAL hook script end-to-end (a fake ``tmux`` on PATH supplies the
window name) and read the result back through the REAL reader, so reverting the mapping
on EITHER side alone turns them red.
"""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from chela import cachekey, context

REPO = Path(__file__).resolve().parent.parent
HOOK = REPO / "scripts" / "cache-statusline.sh"
SLASHED = "org/cmx-1-x"

PAYLOAD = {
    "session_id": "0f0f0f0f-0000-4000-8000-000000000001",
    "session_name": "s-cmx-1",
    "context_window": {
        "used_percentage": 25.0,
        "context_window_size": 200000,
        "remaining_percentage": 75.0,
    },
    "model": {"display_name": "claude-sonnet-5"},
    "cost": {"total_cost_usd": 1.23},
}


def _run_hook(tmp_path: Path, window_name: str, chela_dir: Path) -> subprocess.CompletedProcess:
    """Run the real hook with ``tmux display-message`` faked to report ``window_name``."""
    bindir = tmp_path / "bin"
    bindir.mkdir(exist_ok=True)
    fake = bindir / "tmux"
    fake.write_text(f"#!/bin/sh\nprintf '%s\\n' {json.dumps(window_name)}\n")
    fake.chmod(0o755)
    env = {
        **os.environ,
        "PATH": f"{bindir}{os.pathsep}{os.environ.get('PATH', '')}",
        "CHELA_DIR": str(chela_dir),
        "TMUX_PANE": "%1",
    }
    return subprocess.run(
        ["bash", str(HOOK)], input=json.dumps(PAYLOAD), env=env,
        capture_output=True, text=True, timeout=30,
    )


@pytest.fixture
def chela_dir(tmp_path, monkeypatch):
    d = tmp_path / "chela"
    monkeypatch.setattr(context, "CONTEXT_CACHE_DIR", d / "context")
    monkeypatch.setattr(context, "DB_PATH", tmp_path / "scheduler.db")
    return d


def test_key_has_no_slash_and_round_trips():
    for name in (SLASHED, "cmx-12", "a b/c%d", "..", ".hidden", "üñí/ç"):
        key = cachekey.encode(name)
        assert "/" not in key and not key.startswith(".")
        assert cachekey.decode(key) == name
    # A plain name keys to itself — existing cache files keep their names.
    assert cachekey.encode("judge-cmx-438") == "judge-cmx-438"


def test_hook_writes_a_slashed_window_and_live_snapshot_reads_it(tmp_path, chela_dir):
    proc = _run_hook(tmp_path, SLASHED, chela_dir)
    assert proc.returncode == 0, proc.stderr
    # Writer: one flat, readable file — no subdirectory named after the org.
    files = list((chela_dir / "context").iterdir())
    assert [f.name for f in files] == [f"{cachekey.encode(SLASHED)}.json"]
    assert json.loads(files[0].read_text())["cost"]["total_cost_usd"] == 1.23
    # Reader: the same name resolves to that file, with cost AND context.
    snap = context.live_snapshot(SLASHED)
    assert snap is not None
    assert snap["source"] == "statusline"
    assert snap["cost_usd"] == 1.23
    assert snap["used_pct"] == 25.0
    assert snap["name"] == SLASHED


def test_capture_all_records_the_slashed_window_under_its_real_name(tmp_path, chela_dir):
    assert _run_hook(tmp_path, SLASHED, chela_dir).returncode == 0
    context.capture_all()
    agents = [r["agent"] for r in context.get_latest()]
    assert agents == [SLASHED]


def test_a_failed_write_exits_nonzero_and_logs(tmp_path, chela_dir):
    # Make the cache dir uncreatable: a regular FILE sits where the directory goes.
    chela_dir.mkdir(parents=True)
    (chela_dir / "context").write_text("not a directory")
    proc = _run_hook(tmp_path, SLASHED, chela_dir)
    assert proc.returncode != 0
    assert "cache-statusline" in proc.stderr
    log = (chela_dir / "statusline-errors.log").read_text()
    assert str(chela_dir / "context") in log


def test_an_unwritable_cache_dir_exits_nonzero_and_logs(tmp_path, chela_dir):
    # The directory exists but cannot be written: the tmp write / move must fail LOUD.
    cache = chela_dir / "context"
    cache.mkdir(parents=True)
    cache.chmod(0o555)
    try:
        if os.access(cache, os.W_OK):
            pytest.skip("running with privileges that ignore directory modes")
        proc = _run_hook(tmp_path, SLASHED, chela_dir)
    finally:
        cache.chmod(0o755)
    assert proc.returncode != 0
    log = (chela_dir / "statusline-errors.log").read_text()
    assert f"window '{SLASHED}'" in log
