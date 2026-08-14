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
    monkeypatch.setattr(memcap, "live_bound", lambda: None)
    cap = _cap(capabilities.effective(), "memory_slice_budget")
    assert cap.on is False
    assert "unset/0" in cap.detail


# --- CMX-280: a bound already in force that chela did not set (live_bound) ----------------
#
# Measured 2026-08-13: this box's bound was enforced by an operator's own `~/bin/memcap`
# wrapper (predating CMX-264), which chela's own knob has no way to see — `chela doctor`
# kept saying "OFF" while a 12G ceiling at 83% occupancy was actually holding the machine
# together. Reporting OFF here is not neutral, it is wrong: whoever sizes
# `judge_max_concurrent` off that line gets it wrong in either direction.


def test_capability_reports_an_external_bound_as_on(monkeypatch):
    monkeypatch.delenv("CHELA_MEMORY_SLICE_BUDGET", raising=False)
    monkeypatch.setattr(memcap, "live_bound", lambda: {
        "unit": "memcap.slice", "max_bytes": 12 * 1024**3,
        "current_bytes": 10 * 1024**3, "chela_owned": False,
    })
    cap = _cap(capabilities.effective(), "memory_slice_budget")
    assert cap.on is True
    assert "memcap.slice" in cap.detail
    assert "12.0G" in cap.detail
    assert "83%" in cap.detail
    assert "chela did not set this ceiling" in cap.detail
    # CMX-280 rework round 3: "12.0G" (the ceiling) also appears once headroom is
    # rendered, so a mutation collapsing headroom onto the ceiling value (`max_bytes`
    # instead of `max_bytes - current`) was invisible to the assertions above — both
    # numbers were "12.0G" in that case, and "12.0G" was already asserted. Pin the two
    # rendered quantities by their own distinct values instead: 10G used out of 12G
    # leaves exactly 2G headroom, and only the correct subtraction produces that.
    assert "currently using 10.0G" in cap.detail
    assert "~2.0G headroom" in cap.detail


def test_capability_reports_an_external_bound_with_unreadable_occupancy(monkeypatch):
    monkeypatch.delenv("CHELA_MEMORY_SLICE_BUDGET", raising=False)
    monkeypatch.setattr(memcap, "live_bound", lambda: {
        "unit": "memcap.slice", "max_bytes": 12 * 1024**3,
        "current_bytes": None, "chela_owned": False,
    })
    cap = _cap(capabilities.effective(), "memory_slice_budget")
    assert cap.on is True
    assert "currently using" not in cap.detail


def test_capability_stays_off_when_live_bound_finds_nothing(monkeypatch):
    monkeypatch.delenv("CHELA_MEMORY_SLICE_BUDGET", raising=False)
    monkeypatch.setattr(memcap, "live_bound", lambda: None)
    cap = _cap(capabilities.effective(), "memory_slice_budget")
    assert cap.on is False


def test_capability_treats_a_chela_owned_bound_as_still_off_when_the_knob_is_off(
        monkeypatch):
    """A leftover ``chela-agents.slice`` ceiling on this process's own ancestry (e.g. a
    stale unit from a previous boot) with CHELA_MEMORY_SLICE_BUDGET now unset/0 must NOT
    be reported as an "external" bound — chela_owned is exactly the flag that keeps
    live_bound() from claiming credit for (or blaming an operator for) chela's own
    leftover state."""
    monkeypatch.delenv("CHELA_MEMORY_SLICE_BUDGET", raising=False)
    monkeypatch.setattr(memcap, "live_bound", lambda: {
        "unit": memcap.SLICE_NAME, "max_bytes": 12 * 1024**3,
        "current_bytes": 1024, "chela_owned": True,
    })
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
    monkeypatch.setattr(memcap, "live_bound", lambda: None)
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

    monkeypatch.delenv("CHELA_MEMORY_SLICE_BUDGET", raising=False)
    monkeypatch.setattr(memcap, "live_bound", lambda: None)
    monkeypatch.setattr(config, "DISPATCH_WORKFLOWS", [])
    capabilities.publish(capabilities.effective(), boot_id="b1")
    assert capabilities.live_capability("dispatch")["on"] is False

    monkeypatch.setattr(config, "DISPATCH_WORKFLOWS", [Path("/repo/WORKFLOW.md")])
    assert capabilities.live_capability("dispatch")["on"] is False


# CMX-280 rework round 3 (DEFEAT_SHAPES #22): `_memory_slice_capability` declares
# `live_reload=True` on FOUR separate `return`s (capabilities.py:161, :168, :186, :192),
# but the only test above that drives a boot snapshot through `capabilities.live()`
# publishes while the knob is OFF (the :192 branch) — so only THAT branch's own
# `live_reload=True` was ever proven load-bearing. The judge flipped the memcap-available
# ON branch's flag (:161) to False in a throwaway checkout and the whole suite, including
# every test above, stayed green: nothing ever published a boot snapshot FROM that branch
# to see whether it un-latches. Closed by publishing from each of the other three branches
# in turn and flipping live config to something else with no restart, mirroring the
# OFF->ON test above in the opposite direction (and, for the two "on" branches, sideways).


def test_memory_slice_budget_on_going_off_live_does_not_stay_latched(monkeypatch):
    """Boot-publish from the memcap-available ON branch (capabilities.py:161) — the exact
    branch the judge's surviving mutation targeted — then remove the knob live and confirm
    `live()` reconciles to OFF instead of returning the stale ON snapshot."""
    monkeypatch.setenv("CHELA_MEMORY_SLICE_BUDGET", "12G")
    monkeypatch.setattr(memcap, "available", lambda: True)
    capabilities.publish(capabilities.effective(), boot_id="b1")
    assert capabilities.live_capability("memory_slice_budget")["on"] is True

    # No restart — operator removed the knob from the env file live, which is exactly
    # what this rail's `fix` text promises works without one.
    monkeypatch.delenv("CHELA_MEMORY_SLICE_BUDGET", raising=False)
    monkeypatch.setattr(memcap, "live_bound", lambda: None)
    cap = capabilities.live_capability("memory_slice_budget")
    assert cap["on"] is False
    assert "unset/0" in cap["detail"]


def test_memory_slice_budget_set_but_unwrapped_live_reflects_systemd_run_appearing(
        monkeypatch):
    """Boot-publish from the set-but-no-systemd-run branch (capabilities.py:168, `on`
    False) then have `systemd-run` appear on PATH live — with no restart, `live()` must
    reconcile to ON rather than keep reporting the boot-time OFF."""
    monkeypatch.setenv("CHELA_MEMORY_SLICE_BUDGET", "12G")
    monkeypatch.setattr(memcap, "available", lambda: False)
    capabilities.publish(capabilities.effective(), boot_id="b1")
    cap = capabilities.live_capability("memory_slice_budget")
    assert cap["on"] is False
    assert "systemd-run" in cap["detail"]

    monkeypatch.setattr(memcap, "available", lambda: True)
    cap = capabilities.live_capability("memory_slice_budget")
    assert cap["on"] is True
    assert "12.0G" in cap["detail"]


def test_memory_slice_budget_external_bound_live_reflects_the_bound_disappearing(
        monkeypatch):
    """Boot-publish from the external-bound branch (capabilities.py:186, `on` True with
    the knob itself unset) then have that outside bound go away live — `live()` must
    reconcile to OFF, not keep reporting the boot-time external-bound ON."""
    monkeypatch.delenv("CHELA_MEMORY_SLICE_BUDGET", raising=False)
    monkeypatch.setattr(memcap, "live_bound", lambda: {
        "unit": "memcap.slice", "max_bytes": 12 * 1024**3,
        "current_bytes": 10 * 1024**3, "chela_owned": False,
    })
    capabilities.publish(capabilities.effective(), boot_id="b1")
    cap = capabilities.live_capability("memory_slice_budget")
    assert cap["on"] is True
    assert "chela did not set this ceiling" in cap["detail"]

    monkeypatch.setattr(memcap, "live_bound", lambda: None)
    cap = capabilities.live_capability("memory_slice_budget")
    assert cap["on"] is False


# --- chela.memcap: available() --------------------------------------------------------


def test_available_true_when_systemd_run_on_path(monkeypatch):
    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/systemd-run")
    assert memcap.available() is True


def test_available_false_when_systemd_run_missing(monkeypatch):
    monkeypatch.setattr("shutil.which", lambda name: None)
    assert memcap.available() is False


# --- chela.memcap: live_bound() (CMX-280) -----------------------------------------------
#
# MEASURED on the actual box this ticket was filed against, not assumed: neither the
# chela daemon (PM2, cgroup system.slice/pm2-<user>.service) nor the tmux session that
# hosts dispatched agents is nested INSIDE the operator's personal `memcap.slice` —
# they're siblings. So "walk this process's own cgroup ancestry" (an earlier version of
# this fix) finds nothing and still reports OFF; it does not reproduce the bug. What
# actually drains the box is a SEPARATE slice eating the SAME machine's total RAM,
# which `systemctl --user show <unit> -p MemoryMax` sees directly, without caring who
# is (or isn't) a member of that cgroup — matching how the ticket's own investigation
# found it (`systemctl --user show memcap.slice`).


_SHOW_PROPS = ["-p", "MemoryMax", "-p", "MemoryCurrent"]
_LIST_UNITS_ARGS = ["--type=slice", "--state=active", "--no-legend", "--plain", "--no-pager"]


def _fake_show(monkeypatch, per_unit):
    """`systemctl --user show <unit> -p MemoryMax -p MemoryCurrent` → each unit's lines
    from `per_unit[unit]`, a dict of MemoryMax/MemoryCurrent raw strings.

    Asserts the trailing `-p` flags are exactly `MemoryMax`/`MemoryCurrent`, not just
    that `cmd[:3]` looks like a `show` call — DEFEAT_SHAPES #24: a fake that dispatches
    on an argv prefix and then fabricates per-unit data from a table keyed on the unit
    name alone hands back MemoryMax= lines whether or not production actually asked for
    MemoryMax, so a swap to a different real property (e.g. MemoryHigh) is invisible to
    it.
    """
    def fake_run(cmd, **kwargs):
        if cmd[:3] == ["systemctl", "--user", "show"]:
            unit = cmd[3]
            assert cmd[4:] == _SHOW_PROPS, (
                f"expected {_SHOW_PROPS} after the unit, got {cmd[4:]}")
            props = per_unit.get(unit, {"MemoryMax": "infinity", "MemoryCurrent": "0"})
            stdout = "\n".join(f"{k}={v}" for k, v in props.items())
            return subprocess.CompletedProcess(cmd, 0, stdout=stdout)
        raise AssertionError(f"unexpected subprocess.run call: {cmd}")
    monkeypatch.setattr(subprocess, "run", fake_run)


def _fake_list_and_show(monkeypatch, units, per_unit):
    """As :func:`_fake_show`, plus the `list-units` discovery call — asserts the FULL
    trailing argv, not just that `--type=slice` appears somewhere in it (DEFEAT_SHAPES
    #24: a fake that only checks membership of one flag can't see `--state=active` flip
    to `--state=inactive`, which makes discovery enumerate slices that are NOT running —
    `live_bound()` then returns `None` on every real box)."""
    def fake_run(cmd, **kwargs):
        if cmd[:3] == ["systemctl", "--user", "list-units"]:
            assert cmd[3:] == _LIST_UNITS_ARGS, (
                f"expected {_LIST_UNITS_ARGS} after list-units, got {cmd[3:]}")
            lines = "\n".join(f"{u}  loaded active active {u}" for u in units)
            return subprocess.CompletedProcess(cmd, 0, stdout=lines)
        if cmd[:3] == ["systemctl", "--user", "show"]:
            unit = cmd[3]
            assert cmd[4:] == _SHOW_PROPS, (
                f"expected {_SHOW_PROPS} after the unit, got {cmd[4:]}")
            props = per_unit.get(unit, {"MemoryMax": "infinity", "MemoryCurrent": "0"})
            stdout = "\n".join(f"{k}={v}" for k, v in props.items())
            return subprocess.CompletedProcess(cmd, 0, stdout=stdout)
        raise AssertionError(f"unexpected subprocess.run call: {cmd}")
    monkeypatch.setattr(subprocess, "run", fake_run)


def test_list_user_slices_parses_unit_names(monkeypatch):
    monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: subprocess.CompletedProcess(
        cmd, 0,
        stdout=("-.slice        loaded active active Root Slice\n"
                "memcap.slice   loaded active active memcap — heavy jobs\n"),
    ))
    assert memcap._list_user_slices() == ["-.slice", "memcap.slice"]


def test_list_user_slices_empty_when_systemctl_missing(monkeypatch):
    def raising_run(cmd, **kwargs):
        raise FileNotFoundError("no systemctl")
    monkeypatch.setattr(subprocess, "run", raising_run)
    assert memcap._list_user_slices() == []


def test_slice_memory_parses_finite_values(monkeypatch):
    _fake_show(monkeypatch, {"memcap.slice": {
        "MemoryMax": str(12 * 1024**3), "MemoryCurrent": str(10 * 1024**3),
    }})
    assert memcap._slice_memory("memcap.slice") == (12 * 1024**3, 10 * 1024**3)


def test_slice_memory_infinity_is_none(monkeypatch):
    _fake_show(monkeypatch, {"app.slice": {
        "MemoryMax": "infinity", "MemoryCurrent": str(6 * 1024**3),
    }})
    assert memcap._slice_memory("app.slice") == (None, 6 * 1024**3)


def test_slice_memory_degrades_to_none_none_on_failure(monkeypatch):
    def raising_run(cmd, **kwargs):
        raise subprocess.TimeoutExpired(cmd, 10)
    monkeypatch.setattr(subprocess, "run", raising_run)
    assert memcap._slice_memory("memcap.slice") == (None, None)


def test_live_bound_none_when_no_slice_has_a_finite_ceiling(monkeypatch):
    _fake_list_and_show(monkeypatch, ["-.slice", "app.slice"], {})
    assert memcap.live_bound() is None


def test_live_bound_finds_an_external_bound(monkeypatch):
    _fake_list_and_show(monkeypatch, ["-.slice", "app.slice", "memcap.slice"], {
        "memcap.slice": {"MemoryMax": str(12 * 1024**3),
                          "MemoryCurrent": str(10 * 1024**3)},
    })

    bound = memcap.live_bound()

    assert bound == {
        "unit": "memcap.slice", "max_bytes": 12 * 1024**3,
        "current_bytes": 10 * 1024**3, "chela_owned": False,
    }


def test_live_bound_marks_chelas_own_slice_as_chela_owned(monkeypatch):
    _fake_list_and_show(monkeypatch, [memcap.SLICE_NAME], {
        memcap.SLICE_NAME: {"MemoryMax": str(6 * 1024**3),
                             "MemoryCurrent": str(1024)},
    })

    bound = memcap.live_bound()

    assert bound["unit"] == memcap.SLICE_NAME
    assert bound["chela_owned"] is True


def test_live_bound_picks_the_slice_with_the_least_headroom(monkeypatch):
    # memcap.slice: 12G cap, 10G used -> 2G headroom. chela-agents.slice: 6G cap,
    # nothing used yet -> 6G headroom. memcap.slice is the one an operator needs to
    # see, even though its own ceiling is the LARGER of the two.
    _fake_list_and_show(monkeypatch, ["memcap.slice", memcap.SLICE_NAME], {
        "memcap.slice": {"MemoryMax": str(12 * 1024**3),
                          "MemoryCurrent": str(10 * 1024**3)},
        memcap.SLICE_NAME: {"MemoryMax": str(6 * 1024**3), "MemoryCurrent": "0"},
    })

    bound = memcap.live_bound()

    assert bound["unit"] == "memcap.slice"


def test_live_bound_tie_break_treats_unreadable_occupancy_as_full_headroom(monkeypatch):
    # huge.slice: 100G cap, occupancy UNREADABLE (no MemoryCurrent line at all).
    # tight.slice: 6G cap, 5G used -> 1G headroom, the real bound an operator needs to
    # see. Correct behaviour treats huge.slice's headroom as its own 100G ceiling (the
    # docstring's "max_bytes alone when occupancy is unreadable"), so tight.slice's 1G
    # wins. Collapsing the unreadable case to a 0-headroom guess instead would make
    # huge.slice always look tighter than any real, measured headroom and hide the
    # actual bound behind a slice chela knows nothing about.
    _fake_list_and_show(monkeypatch, ["huge.slice", "tight.slice"], {
        "huge.slice": {"MemoryMax": str(100 * 1024**3)},  # MemoryCurrent omitted
        "tight.slice": {"MemoryMax": str(6 * 1024**3), "MemoryCurrent": str(5 * 1024**3)},
    })

    bound = memcap.live_bound()

    assert bound["unit"] == "tight.slice"


def test_live_bound_current_bytes_none_when_unreadable(monkeypatch):
    def fake_run(cmd, **kwargs):
        if cmd[:3] == ["systemctl", "--user", "list-units"]:
            return subprocess.CompletedProcess(
                cmd, 0, stdout="memcap.slice  loaded active active memcap\n")
        if cmd[:3] == ["systemctl", "--user", "show"]:
            # MemoryCurrent line missing entirely — unreadable, not zero
            return subprocess.CompletedProcess(cmd, 0, stdout="MemoryMax=12884901888\n")
        raise AssertionError(cmd)
    monkeypatch.setattr(subprocess, "run", fake_run)

    bound = memcap.live_bound()

    assert bound["max_bytes"] == 12884901888
    assert bound["current_bytes"] is None


def test_live_bound_ignores_slices_with_no_ceiling(monkeypatch):
    _fake_list_and_show(monkeypatch, ["app.slice", "session.slice"], {
        "app.slice": {"MemoryMax": "infinity", "MemoryCurrent": str(6 * 1024**3)},
        "session.slice": {"MemoryMax": "infinity", "MemoryCurrent": "0"},
    })
    assert memcap.live_bound() is None


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
