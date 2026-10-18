"""CMX-181 — the Mod's status line reads the SAME data source as today's statusLine.

The mod's tests pin that, given ``usage``, it pipes ``payload`` (status-cases.ts) to the
hook; these drive it through the REAL hook into the REAL reader, against the native payload.
"""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from chela import context

REPO = Path(__file__).resolve().parent.parent
HOOK = REPO / "scripts" / "cache-statusline.sh"
STATUS = json.loads(
    (REPO / "mods" / "chela-hooks" / "hooks" / "status-cases.ts").read_text()
    .split("export const STATUS =", 1)[1].rsplit("as const", 1)[0])
# What Claude Code's own statusLine sends for the same figures: the extra fields only it has.
NATIVE = {**STATUS["payload"], "model": {"id": "claude-opus-5-5", "display_name": "Opus 5.5"},
          "context_window": {**STATUS["payload"]["context_window"],
                             "current_usage": {"input_tokens": 12, "cache_read_input_tokens": 83988}}}
# Every snapshot field both writers carry. `messages_k` is left out on purpose: it is the
# last call's UNCACHED input, which `$.session.usage()` does not split out.
SHARED = {"used_pct": 42, "total_k": 200.0, "used_k": 84.0, "free_pct": 58, "free_k": 116.0,
          "cost_usd": 1.25, "rate_limit_pct": 23.5, "rate_limit_resets_at": 1792238400,
          "weekly_rl_pct": 7, "weekly_rl_resets_at": 1792454400}


@pytest.fixture
def cache(tmp_path, monkeypatch):
    """Run the real hook (a fake ``tmux`` names the window), read it back the real way."""
    chela_dir = tmp_path / "chela"
    monkeypatch.setattr(context, "CONTEXT_CACHE_DIR", chela_dir / "context")
    monkeypatch.setattr(context, "DB_PATH", tmp_path / "scheduler.db")
    (tmp_path / "tmux").write_text("#!/bin/sh\necho cmx-181-mod\n")
    (tmp_path / "tmux").chmod(0o755)
    env = {**os.environ, "PATH": f"{tmp_path}{os.pathsep}{os.environ.get('PATH', '')}",
           "CHELA_DIR": str(chela_dir), "TMUX_PANE": "%1"}

    def run(payload: dict) -> dict | None:
        proc = subprocess.run(["bash", str(HOOK)], input=json.dumps(payload), env=env,
                              capture_output=True, text=True, timeout=30)
        assert proc.returncode == 0, proc.stderr
        return context._cache_snapshot("cmx-181-mod", payload["session_id"])
    return run


def test_a_mod_written_cache_reads_as_the_statuslines_own(cache):
    """🔴 GUARD: same file, same reader, same numbers as the native statusLine payload."""
    mod = cache(STATUS["payload"])
    assert mod is not None and mod["source"] == "statusline" and mod["model"] == "claude-opus-5-5"
    assert {k: mod[k] for k in SHARED} == SHARED
    native = cache(NATIVE)
    assert native is not None and native["model"] == "Opus 5.5"
    assert {k: native[k] for k in SHARED} == SHARED


def test_negative_control_no_percent_is_no_snapshot(cache):
    """A fresh or just-compacted session has no percent: the Mod leaves the field out
    (status.test.ts pins that), and the reader must then serve NO statusLine snapshot —
    the transcript estimate takes over — rather than a bar at 0%."""
    assert cache({**STATUS["payload"], "context_window": {"context_window_size": 200000}}) is None
