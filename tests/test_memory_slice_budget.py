"""🧠🔒 The shared memory slice (CMX-264) — the `memcap` analog for memory,
docs/RESOURCE_ISOLATION.md's shared-slice mitigation built into chela rather than left to
a personal wrapper outside it.

On 2026-07-14 four agents each launched under their OWN 6G ceiling authorised 24G on a
19G box — every one stayed under its individual cap, so no per-job limit ever fired, and
the kernel's global OOM killer took tmux and the orchestrator with it instead of the jobs
that caused it. A per-job cap does not bound the box; only a SHARED one does.

These tests pin: the env-var parser (`config.memory_slice_budget_bytes`, same
`_cast_size` machinery `worktree_disk_budget_bytes` already proved), the capability
announcement, and `chela.memcap`'s own launch-time wrapping — every branch degrades to
launching UNWRAPPED rather than ever blocking or breaking a launch.
"""
from __future__ import annotations

import sqlite3
import subprocess
from types import SimpleNamespace

import pytest

from chela import capabilities, config, memcap

# --- config.memory_slice_budget_bytes: the parser (same _cast_size as disk budget) -------


def test_unset_is_off(monkeypatch):
    monkeypatch.delenv("CHELA_MEMORY_SLICE_BUDGET", raising=False)
    assert config.memory_slice_budget_bytes() == 0


def test_explicit_zero_is_off(monkeypatch):
    monkeypatch.setenv("CHELA_MEMORY_SLICE_BUDGET", "0")
    assert config.memory_slice_budget_bytes() == 0


def test_a_bare_integer_is_bytes(monkeypatch):
    monkeypatch.setenv("CHELA_MEMORY_SLICE_BUDGET", "12345")
    assert config.memory_slice_budget_bytes() == 12345


@pytest.mark.parametrize("raw,expected", [
    ("12G", 12 * 1024**3),
    ("500M", 500 * 1024**2),
    ("2K", 2 * 1024),
    ("1T", 1024**4),
    ("12g", 12 * 1024**3),          # case-insensitive
])
def test_size_suffixes(monkeypatch, raw, expected):
    monkeypatch.setenv("CHELA_MEMORY_SLICE_BUDGET", raw)
    assert config.memory_slice_budget_bytes() == expected


def test_garbage_degrades_to_off_not_a_crash(monkeypatch):
    monkeypatch.setenv("CHELA_MEMORY_SLICE_BUDGET", "not-a-size")
    assert config.memory_slice_budget_bytes() == 0


def test_a_negative_size_is_off(monkeypatch):
    monkeypatch.setenv("CHELA_MEMORY_SLICE_BUDGET", "-5G")
    assert config.memory_slice_budget_bytes() == 0


# --- capabilities.effective(): the announcement -------------------------------------------


def _cap(caps, key):
    return next(c for c in caps if c.key == key)


def test_capability_reports_off_by_default(monkeypatch):
    monkeypatch.delenv("CHELA_MEMORY_SLICE_BUDGET", raising=False)
    cap = _cap(capabilities.effective(), "memory_slice_budget")
    assert cap.on is False
    assert "unset/0" in cap.detail


def test_capability_reports_on_with_the_human_size_when_available(monkeypatch):
    monkeypatch.setenv("CHELA_MEMORY_SLICE_BUDGET", "12G")
    monkeypatch.setattr(memcap, "available", lambda: True)
    cap = _cap(capabilities.effective(), "memory_slice_budget")
    assert cap.on is True
    assert "12.0G" in cap.detail
    assert memcap.SLICE_NAME in cap.detail


def test_capability_reports_off_when_budget_set_but_systemd_run_missing(monkeypatch):
    monkeypatch.setenv("CHELA_MEMORY_SLICE_BUDGET", "12G")
    monkeypatch.setattr(memcap, "available", lambda: False)
    cap = _cap(capabilities.effective(), "memory_slice_budget")
    assert cap.on is False
    assert "systemd-run" in cap.detail


# --- capabilities.live(): a live_reload capability must not go stale ----------------------
#
# Measured 2026-08-13: the daemon published (boot-time) capabilities with the budget OFF,
# then an operator added CHELA_MEMORY_SLICE_BUDGET=12G to the env file with no restart —
# exactly what its own `fix` text promises works (memcap.wrap_launch_cmd re-reads the knob
# fresh on every dispatch). The 12G slice was actively bounding the box while `chela
# doctor`/the dashboard still read the stale "OFF" from daemon.json. `dispatch`, by
# contrast, IS restart_required (config.py's DISPATCH_KNOBS) — its boot snapshot is
# supposed to freeze until a restart, per test_capabilities.py's round-trip test.


def test_memory_slice_budget_reflects_a_post_boot_env_change_not_the_boot_snapshot(
        monkeypatch):
    monkeypatch.delenv("CHELA_MEMORY_SLICE_BUDGET", raising=False)
    monkeypatch.setattr(memcap, "available", lambda: True)
    capabilities.publish(capabilities.effective(), boot_id="b1")
    assert capabilities.live_capability("memory_slice_budget")["on"] is False

    # No restart — the operator only edited the env file, which is the whole point of
    # NOT marking this knob restart_required.
    monkeypatch.setenv("CHELA_MEMORY_SLICE_BUDGET", "12G")
    cap = capabilities.live_capability("memory_slice_budget")
    assert cap["on"] is True
    assert "12.0G" in cap["detail"]


def test_memory_slice_budget_off_going_on_live_does_not_move_a_boot_latched_capability(
        monkeypatch):
    """The contrast case: `dispatch` really is frozen until a restart (config.py marks
    CHELA_DISPATCH_WORKFLOWS restart_required=True) — a live_reload fix must not make
    every capability chase live config, only the ones that are actually live-reread."""
    from pathlib import Path

    monkeypatch.setattr(config, "DISPATCH_WORKFLOWS", [])
    capabilities.publish(capabilities.effective(), boot_id="b1")
    assert capabilities.live_capability("dispatch")["on"] is False

    monkeypatch.setattr(config, "DISPATCH_WORKFLOWS", [Path("/repo/WORKFLOW.md")])
    assert capabilities.live_capability("dispatch")["on"] is False


# --- chela.memcap: available() --------------------------------------------------------


def test_available_true_when_systemd_run_on_path(monkeypatch):
    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/systemd-run")
    assert memcap.available() is True


def test_available_false_when_systemd_run_missing(monkeypatch):
    monkeypatch.setattr("shutil.which", lambda name: None)
    assert memcap.available() is False


# --- chela.memcap: ensure_slice() -------------------------------------------------------


def test_ensure_slice_false_for_a_non_positive_budget():
    assert memcap.ensure_slice(0) is False
    assert memcap.ensure_slice(-5) is False


def test_ensure_slice_writes_the_unit_and_probes_it(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr(subprocess, "run", fake_run)

    assert memcap.ensure_slice(12 * 1024**3) is True

    unit_path = tmp_path / "systemd" / "user" / memcap.SLICE_NAME
    assert unit_path.exists()
    assert "MemoryMax=12884901888" in unit_path.read_text()
    assert any(c[:2] == ["systemctl", "--user"] for c in calls)
    assert any(c[:2] == ["systemd-run", "--user"] for c in calls)


def test_ensure_slice_skips_the_rewrite_when_unchanged(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.setattr(
        subprocess, "run",
        lambda cmd, **kw: subprocess.CompletedProcess(cmd, 0),
    )
    assert memcap.ensure_slice(1024) is True
    unit_path = tmp_path / "systemd" / "user" / memcap.SLICE_NAME
    written_at = unit_path.stat().st_mtime_ns

    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert memcap.ensure_slice(1024) is True
    assert unit_path.stat().st_mtime_ns == written_at
    assert not any(c[:2] == ["systemctl", "--user"] for c in calls)
    # the health probe still runs every call — a stale daemon reload is not the only
    # way for the slice to stop being ready.
    assert any(c[:2] == ["systemd-run", "--user"] for c in calls)


@pytest.mark.parametrize("failure", [
    subprocess.CalledProcessError(1, ["systemctl"]),
    subprocess.TimeoutExpired(["systemctl"], 10),
    OSError("no such session"),
])
def test_ensure_slice_never_raises_degrades_to_false(tmp_path, monkeypatch, failure):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))

    def raising_run(cmd, **kwargs):
        raise failure

    monkeypatch.setattr(subprocess, "run", raising_run)
    assert memcap.ensure_slice(1024) is False


# --- chela.memcap: wrap_launch_cmd() ----------------------------------------------------


def test_wrap_returns_cmd_unchanged_when_budget_is_off(monkeypatch):
    monkeypatch.delenv("CHELA_MEMORY_SLICE_BUDGET", raising=False)
    assert memcap.wrap_launch_cmd("claude --permission-mode auto") == "claude --permission-mode auto"


def test_wrap_returns_cmd_unchanged_when_systemd_run_missing(monkeypatch):
    monkeypatch.setenv("CHELA_MEMORY_SLICE_BUDGET", "12G")
    monkeypatch.setattr(memcap, "available", lambda: False)
    assert memcap.wrap_launch_cmd("claude foo") == "claude foo"


def test_wrap_returns_cmd_unchanged_when_slice_is_not_ready(monkeypatch):
    monkeypatch.setenv("CHELA_MEMORY_SLICE_BUDGET", "12G")
    monkeypatch.setattr(memcap, "available", lambda: True)
    monkeypatch.setattr(memcap, "ensure_slice", lambda budget: False)
    assert memcap.wrap_launch_cmd("claude foo") == "claude foo"


def test_wrap_prefixes_with_exec_systemd_run_into_the_shared_slice(monkeypatch):
    monkeypatch.setenv("CHELA_MEMORY_SLICE_BUDGET", "12G")
    monkeypatch.setattr(memcap, "available", lambda: True)
    monkeypatch.setattr(memcap, "ensure_slice", lambda budget: True)
    wrapped = memcap.wrap_launch_cmd("claude --permission-mode auto")
    assert wrapped == (
        f"exec systemd-run --user --scope --collect --slice={memcap.SLICE_NAME} "
        "-- claude --permission-mode auto"
    )


# --- dispatcher._launch_agent: the actual launch-path wiring ----------------------------
# Same fixture shape as tests/test_agent_env_strip.py's _capture_send_keys — _launch_agent
# is THE spawn path shared by first dispatch, rework, and the judge, so exercising it
# directly here (rather than mocking it away, like tests/test_worktree_disk_budget.py's
# `ticking` fixture does) is what actually pins the one-line wiring at its call site,
# not just chela.memcap's own unit behaviour.


def _wf(tmp_path):
    from chela.workflow import WorkflowDef
    return WorkflowDef(
        path=tmp_path / "WORKFLOW.md",
        config={"project_key": "CMX", "agent": {}},
        prompt_template="go {{workspace_path}}",
    )


def _capture_send_keys(monkeypatch, dispatcher):
    sent: list[str] = []

    def fake_run(argv, *a, **k):
        if argv[:2] == ["tmux", "new-window"]:
            return SimpleNamespace(stdout="@100\n", returncode=0)
        if argv[:2] == ["tmux", "send-keys"] and len(argv) > 4:
            sent.append(argv[4])
        return SimpleNamespace(stdout="", returncode=0)

    monkeypatch.setattr(dispatcher.subprocess, "run", fake_run)
    return sent


def _launch(monkeypatch, dispatcher, tmp_path):
    monkeypatch.setattr(dispatcher, "_kill_windows_named", lambda *_a, **_k: None)
    monkeypatch.setattr(dispatcher, "_wait_for_ready", lambda *a, **k: True)
    monkeypatch.setattr(dispatcher, "_send_seed", lambda *a, **k: True)
    sent = _capture_send_keys(monkeypatch, dispatcher)
    wf = _wf(tmp_path)
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn = dispatcher.ensure_schema(conn)
    dispatcher._launch_agent(
        wf, "t1", "cmx-1", tmp_path / "wt", "go", conn,
        hook_vars={}, fresh_worktree=False,
    )
    return sent


def test_launch_agent_wraps_the_command_when_the_slice_is_ready(monkeypatch, tmp_path):
    import chela.dispatcher as dispatcher
    monkeypatch.setenv("CHELA_MEMORY_SLICE_BUDGET", "12G")
    monkeypatch.setattr(dispatcher.memcap, "available", lambda: True)
    monkeypatch.setattr(dispatcher.memcap, "ensure_slice", lambda budget: True)

    sent = _launch(monkeypatch, dispatcher, tmp_path)

    claude_line = next(line for line in sent if "claude" in line)
    assert claude_line.startswith(
        f"exec systemd-run --user --scope --collect --slice={memcap.SLICE_NAME} --"
    )


def test_launch_agent_launches_unwrapped_when_the_budget_is_off(monkeypatch, tmp_path):
    import chela.dispatcher as dispatcher
    monkeypatch.delenv("CHELA_MEMORY_SLICE_BUDGET", raising=False)

    sent = _launch(monkeypatch, dispatcher, tmp_path)

    claude_line = next(line for line in sent if "claude" in line)
    assert not claude_line.startswith("exec systemd-run")
