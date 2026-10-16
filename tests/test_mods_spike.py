"""CMX-157 — the Claude Code Mod spike (``mods/chela-hooks``) against the shell plugin.

``mods/chela-hooks/hooks/cases.ts`` is ONE fixture read by both suites: the mod's own
``claude plugin test`` cases check the mod hands each command to the gate and relays its
answer, and these check that the answer is right — every ``gate`` case through the REAL
``plugin/hooks/mergegate.py``, fed the exact payload the mod builds — and that every event
the mod posts is one :func:`chela.hooks.ingest` files with the right type and summary.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from chela import event_log, hooks
from tests.test_mergegate import world  # noqa: F401 — the fixture, reused verbatim

REPO = Path(__file__).resolve().parent.parent
MOD = REPO / "mods" / "chela-hooks"
GATE = REPO / "plugin" / "hooks" / "mergegate.py"
CASES = json.loads(
    (MOD / "hooks" / "cases.ts").read_text().split("export const CASES =", 1)[1]
    .rsplit("as const", 1)[0])


def _mod_payload(command: str, cwd: Path) -> dict:
    """What the mod's ``tool.call`` hook pipes to the gate (register.test.ts pins it)."""
    return {"session_id": "s-1", "transcript_path": "", "cwd": str(cwd),
            "hook_event_name": "PreToolUse", "tool_name": "Bash",
            "tool_input": {"command": command}, "tool_use_id": "tu-1"}


def test_the_shared_cases_cover_both_sides_of_the_gate():
    """A fixture with no near-miss (or no deny) proves nothing about the gate."""
    denies = [c["deny"] for c in CASES["gate"]]
    assert True in denies and False in denies


@pytest.mark.parametrize("case", CASES["gate"], ids=lambda c: c["command"][:40])
def test_the_real_gate_agrees_with_every_shared_case(world, case):  # noqa: F811
    """🔴 GUARD: the mod's tests ASSUME these verdicts; this proves them on the real gate,
    run exactly as the mod runs it (``python3 -I -S <script>``, payload on stdin)."""
    env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": str(world["tmp"]),
           "CHELA_DIR": str(world["state"])}
    proc = subprocess.run(["python3", "-I", "-S", str(GATE)],
                          input=json.dumps(_mod_payload(case["command"], world["chela"])),
                          capture_output=True, text=True, env=env, timeout=30)
    assert proc.returncode == 0, proc.stderr
    out = json.loads(proc.stdout)["hookSpecificOutput"] if proc.stdout.strip() else {}
    assert (out.get("permissionDecision") == "deny") is case["deny"], case["command"]


@pytest.fixture
def log_file(tmp_path, monkeypatch):
    monkeypatch.setenv("CHELA_EVENTS_FILE", str(tmp_path / "events.jsonl"))
    monkeypatch.setattr(hooks, "_panes", lambda force=False: {})
    monkeypatch.setattr(hooks, "_current_epoch", lambda: None)


@pytest.mark.parametrize("body", [*CASES["events"], CASES["pre_tool_use"]],
                         ids=lambda b: b["hook_event_name"])
def test_ingest_files_every_payload_the_mod_posts(log_file, body):
    """🔴 GUARD: the mod posts these bodies to ``/hooks/<hook_event_name>`` (its tests pin
    that); ingest must file each under its own type with the summary a human reads."""
    event = body["hook_event_name"]
    record = hooks.ingest(event, dict(body))
    assert record is not None
    assert record["type"] == "hook." + re.sub(r"(?<!^)(?=[A-Z])", "_", event).lower()
    assert record["summary"] == CASES["summaries"][event]
    assert record["session_id"] == body["session_id"]
    assert event_log.read()["events"][-1]["seq"] == record["seq"]


def _claude_with_mods() -> str | None:
    exe = shutil.which("claude")
    if not exe:
        return None
    try:
        out = subprocess.run([exe, "--version"], capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return None
    m = re.match(r"(\d+)\.(\d+)\.(\d+)", out.stdout.strip())
    return exe if m and tuple(map(int, m.groups())) >= (2, 1, 301) else None


@pytest.mark.skipif(_claude_with_mods() is None, reason="needs Claude Code >= 2.1.301")
def test_the_mods_own_suite_passes():
    """The mod's ``*.test.ts`` run against the engine itself. Skipped without a Claude
    Code that has Mods (CI has none); the judge's host does."""
    proc = subprocess.run([_claude_with_mods(), "plugin", "test", str(MOD)],
                          capture_output=True, text=True, timeout=300)
    assert proc.returncode == 0, proc.stdout[-4000:] + proc.stderr[-2000:]
    assert re.search(r"\b0 fail\b", proc.stdout + proc.stderr)
