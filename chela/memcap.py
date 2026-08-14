"""🧠🔒 The shared memory slice (CMX-264) — one cgroup slice bounding the SUM of every
dispatched agent's and judge's memory, so an operator no longer has to hand-tune
``concurrency.max`` down to whatever they hope fits in RAM and hold their breath.

``docs/RESOURCE_ISOLATION.md`` documents the incident this closes: on 2026-07-14 four
agents each launched under their OWN ``MEMCAP=6G`` ceiling authorised **24G on a 19G
box** — every one of them stayed under its individual cap, so no per-job limit ever
fired, and the kernel's global OOM killer went for tmux, PM2 and two Claude sessions
instead of the jobs that caused it. **A per-job ceiling does not bound the box. Only a
shared one does** — every process launched into the SAME slice is capped on its combined
total, not on N separate budgets that can each be individually fine and still overrun the
machine together.

Off by default (``CHELA_MEMORY_SLICE_BUDGET`` unset/0) — same posture as
``config.worktree_disk_budget_bytes``: nobody is forced onto a rail they haven't sized
for their own box. Linux + a working ``systemd --user`` session only; anywhere else
(macOS, a systemd-less container, a session with no D-Bus) this degrades to a no-op — see
:func:`wrap_launch_cmd` — rather than breaking agent launch.
"""
from __future__ import annotations

import logging
import os
import subprocess
from pathlib import Path

from chela import config

log = logging.getLogger(__name__)

# One name, owned by chela — deliberately NOT the ``memcap.slice`` name
# ``docs/RESOURCE_ISOLATION.md``'s personal wrapper uses, so turning this on never
# silently repurposes a slice an operator already hand-tuned for other heavy jobs
# (backtests, test fans) outside chela.
SLICE_NAME = "chela-agents.slice"

_UNIT_TEMPLATE = """\
[Unit]
Description=chela-agents.slice — the SHARED memory ceiling for dispatched agents and judges (CMX-264)

[Slice]
# THE POINT: this bounds the SUM of every agent/judge launched into it at once, not
# each one individually — see chela/memcap.py and docs/RESOURCE_ISOLATION.md.
MemoryMax={budget_bytes}
"""


def _systemd_user_dir() -> Path:
    xdg = os.environ.get("XDG_CONFIG_HOME", "").strip()
    base = Path(xdg) if xdg else Path.home() / ".config"
    return base / "systemd" / "user"


def _slice_unit_path() -> Path:
    return _systemd_user_dir() / SLICE_NAME


def available() -> bool:
    """True only when ``systemd-run`` is on ``PATH`` — the one thing every wrap needs.
    False on macOS, a systemd-less container, or any host without it; the feature then
    stays off no matter what the budget knob says."""
    import shutil
    return shutil.which("systemd-run") is not None


def ensure_slice(budget_bytes: int) -> bool:
    """(Re)write the shared slice's ``MemoryMax`` and confirm it actually works.

    Never raises — every step here is best-effort, and ANY failure (no write
    permission, no ``systemd --user`` session, no D-Bus, a stale daemon that needs a
    reload) just means "not ready", so the caller falls back to launching unwrapped
    rather than risking an agent that never starts. Returns whether the slice is ready
    to receive scopes right now.
    """
    if budget_bytes <= 0:
        return False
    try:
        unit_path = _slice_unit_path()
        desired = _UNIT_TEMPLATE.format(budget_bytes=budget_bytes)
        current = unit_path.read_text() if unit_path.exists() else None
        if current != desired:
            unit_path.parent.mkdir(parents=True, exist_ok=True)
            unit_path.write_text(desired)
            subprocess.run(
                ["systemctl", "--user", "daemon-reload"],
                check=True, capture_output=True, timeout=10,
            )
        # The health probe: a harmless, instant scope that actually exercises the
        # whole chain (the unit file parses, the user session is reachable, D-Bus is
        # up, the slice materialises). Writing the file is not enough on its own —
        # CMX-164's own disk-budget rail degrades to off on anything unreadable rather
        # than trusting an unread config, and this is the same discipline for a much
        # less certain dependency (a live systemd user session).
        subprocess.run(
            ["systemd-run", "--user", "--scope", "--collect",
             f"--slice={SLICE_NAME}", "--", "true"],
            check=True, capture_output=True, timeout=10,
        )
        return True
    except (OSError, subprocess.SubprocessError):
        log.warning(
            "memcap: the shared memory slice (%s) is not ready — dispatched agents and "
            "judges will launch UNWRAPPED this tick (no memory ceiling enforced). "
            "Confirm `systemctl --user status` works and CHELA_MEMORY_SLICE_BUDGET is a "
            "byte size chela can write to %s.",
            SLICE_NAME, _slice_unit_path(),
        )
        return False


def _list_user_slices() -> list[str]:
    """Every currently-loaded ``systemd --user`` slice unit name. Best-effort — an empty
    list on any host without a working ``systemd --user`` session (no exception ever
    escapes), same posture as :func:`available`/:func:`ensure_slice`.
    """
    try:
        result = subprocess.run(
            ["systemctl", "--user", "list-units", "--type=slice", "--state=active",
             "--no-legend", "--plain", "--no-pager"],
            check=True, capture_output=True, text=True, timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return []
    names = []
    for line in result.stdout.splitlines():
        first = line.split(maxsplit=1)[:1]
        if first and first[0].endswith(".slice"):
            names.append(first[0])
    return names


def _slice_memory(unit: str) -> tuple[int | None, int | None]:
    """``(max_bytes, current_bytes)`` for one active slice, straight from ``systemctl
    show`` — the same property ``docs/RESOURCE_ISOLATION.md``'s own "Verify, don't
    assume" section checks by hand. ``max_bytes`` is ``None`` for ``infinity``/unset (no
    ceiling); ``current_bytes`` is ``None`` only when the property could not be read at
    all — never a guessed 0.
    """
    try:
        result = subprocess.run(
            ["systemctl", "--user", "show", unit, "-p", "MemoryMax", "-p", "MemoryCurrent"],
            check=True, capture_output=True, text=True, timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return None, None
    values = dict(line.partition("=")[::2] for line in result.stdout.splitlines() if "=" in line)

    def _finite(raw: str | None) -> int | None:
        try:
            return int(raw)
        except (TypeError, ValueError):
            return None    # "infinity", "[not set]", empty, or missing entirely

    return _finite(values.get("MemoryMax")), _finite(values.get("MemoryCurrent"))


def live_bound() -> dict | None:
    """The tightest memory ceiling any ACTIVE ``systemd --user`` slice is enforcing on
    this box right now, whoever created it — CMX-264's own ``chela-agents.slice``, or
    anything else sharing this ``systemd --user`` session (an operator's personal
    ``~/bin/memcap``-style wrapper, see ``docs/RESOURCE_ISOLATION.md``'s own
    ``memcap.slice`` convention).

    CMX-280: a slice bounding a completely SEPARATE set of jobs still eats into the
    SAME box's total RAM — the incident this closes measured a `memcap.slice` holding
    10 of 12 GiB (an operator's own heavy jobs, unrelated to chela), leaving ~1.2 GiB
    of headroom for chela's own dispatched agents even though
    ``CHELA_MEMORY_SLICE_BUDGET`` was unset the whole time. A dispatched agent is not
    literally inside that slice's cgroup — chela never wrapped it there — but the RAM
    it ate is RAM chela's agents cannot have either, on the same box. Reporting "off"
    in that state answers "did chela turn this on?", not "is a bound in force?"
    (CMX-280) — this answers the second question, so ``chela doctor``/the dashboard can
    say what an operator would otherwise have to run ``systemctl --user show
    memcap.slice`` by hand to find out.

    Best-effort, never raises — degrades to ``None`` on any host without a working
    ``systemd --user`` session. Returns ``None`` when no active slice sets a finite
    ``MemoryMax``. Otherwise picks the slice with the LEAST headroom (``max_bytes -
    current_bytes``, or ``max_bytes`` alone when occupancy is unreadable) — the one
    actually worth an operator's attention — as ``{"unit": str, "max_bytes": int,
    "current_bytes": int | None, "chela_owned": bool}``.
    """
    tightest: tuple[int, str, int, int | None] | None = None
    for unit in _list_user_slices():
        max_bytes, current_bytes = _slice_memory(unit)
        if max_bytes is None:
            continue
        headroom = max_bytes if current_bytes is None else max_bytes - current_bytes
        if tightest is None or headroom < tightest[0]:
            tightest = (headroom, unit, max_bytes, current_bytes)
    if tightest is None:
        return None
    _, unit, max_bytes, current_bytes = tightest
    return {
        "unit": unit,
        "max_bytes": max_bytes,
        "current_bytes": current_bytes,
        "chela_owned": unit == SLICE_NAME,
    }


def wrap_launch_cmd(cmd: str) -> str:
    """Prefix ``cmd`` so the pane's own shell launches it into the shared slice —
    ``exec`` REPLACES the shell in place (same pid, same parent/child shape the tmux
    correlation in ``agent_manager.claude_pid()`` depends on: it walks direct children
    of the pane's shell pid via ``pgrep -P``), and ``systemd-run --scope`` forks exactly
    once to realise the scope before exec'ing straight into ``cmd`` — no extra shell
    layer, so ``cmd`` still ends up the DIRECT child of that (former-shell) pid. Returns
    ``cmd`` completely unchanged whenever the rail is off, ``systemd-run`` is missing, or
    :func:`ensure_slice` could not confirm the slice is ready — never breaks a launch
    that would otherwise have worked.
    """
    budget = config.memory_slice_budget_bytes()
    if not budget:
        return cmd
    if not available():
        log.warning(
            "memcap: CHELA_MEMORY_SLICE_BUDGET is set but `systemd-run` is not on PATH "
            "— launching unwrapped, no memory ceiling enforced this tick."
        )
        return cmd
    if not ensure_slice(budget):
        return cmd
    return f"exec systemd-run --user --scope --collect --slice={SLICE_NAME} -- {cmd}"
