"""CMX-399: ``chela watch --if-unclaimed`` fills an empty or dead orchestrator slot, never a live one.

A bare ``chela watch`` in a SessionStart hook moved the orchestrator pin to a restarted peer
session (CMX-394). ``--if-unclaimed`` is the form such a hook may run: it registers the caller
exactly like ``chela watch`` when the pin is empty, undeliverable (a dead window or a stale tmux
epoch — :data:`chela.inbox.UNDELIVERABLE`) or already the caller's, and otherwise changes nothing.

Every case runs the real CLI in a SUBPROCESS against a temp ``CHELA_DIR`` with an explicit env
(no ``TMUX``/``TMUX_PANE``, a private ``TMUX_TMPDIR``, an impossible session name). The only
seams faked in that child are the tmux/claude READS — the window list, the epoch and the status
map — so the live inbox and the live tmux socket are never touched.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
NOW = "epoch-now"
OLD = "epoch-before-reboot"

# The child: fake the tmux/claude reads, then run the real `chela` entry point.
_DRIVER = """
import json, os, sys
from chela import discovery, epoch, inbox, main, sessions
world = json.loads(os.environ["T_WORLD"])
discovery.get_windows_by_id = lambda *a, **k: dict(world["windows"])
epoch.current = lambda *a, **k: world["epoch"]
inbox.status_snapshot = lambda: dict(world["statuses"])
sessions.session_of_window = lambda wid: None
sys.argv = ["chela"] + json.loads(os.environ["T_ARGV"])
main.main()
"""


def _run(tmp_path: Path, argv: list[str], *, store: dict | None, statuses: dict,
         caller: str = "@B") -> tuple[subprocess.CompletedProcess, dict]:
    chela_dir = tmp_path / "chela"
    chela_dir.mkdir(exist_ok=True)
    inbox_file = chela_dir / "inbox.json"
    if store is not None:
        inbox_file.write_text(json.dumps(store))
    world = {"windows": {"@A": "orchestrator", "@B": "peer"}, "epoch": NOW,
             "statuses": statuses}
    env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": str(tmp_path),
        "CHELA_DIR": str(chela_dir),
        "CHELA_INBOX_FILE": str(inbox_file),
        "CHELA_TMUX_SESSION": "chela-tests-no-such-session",
        "TMUX_TMPDIR": str(tmp_path / "tmux"),
        "CHELA_WID": caller,
        "PYTHONPATH": str(REPO),
        "PYTHONDONTWRITEBYTECODE": "1",
        "T_WORLD": json.dumps(world),
        "T_ARGV": json.dumps(argv),
    }
    proc = subprocess.run([sys.executable, "-c", _DRIVER], env=env, cwd=str(tmp_path),
                          capture_output=True, text=True, timeout=60)
    after = json.loads(inbox_file.read_text()) if inbox_file.exists() else {}
    return proc, after


def _pinned(wid: str, stamp: str | None = NOW) -> dict:
    return {"orchestrator": wid, "orchestrator_epoch": stamp, "orchestrator_session": None,
            "orchestrator_name": "orchestrator", "orchestrator_peer": None, "watches": {},
            "queue": []}


BOTH_LIVE = {"@A": "idle", "@B": "busy"}


def test_live_orchestrator_keeps_the_pin(tmp_path):
    """⛔ The CMX-394 regression: a live @A is never displaced by @B's hook."""
    proc, after = _run(tmp_path, ["watch", "--if-unclaimed"], store=_pinned("@A"),
                       statuses=BOTH_LIVE)
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == "orchestrator is @A (live) — not claiming"
    assert after["orchestrator"] == "@A"
    assert after["orchestrator_epoch"] == NOW


def test_empty_pin_registers_caller(tmp_path):
    proc, after = _run(tmp_path, ["watch", "--if-unclaimed"], store=None, statuses=BOTH_LIVE)
    assert proc.returncode == 0, proc.stderr
    assert "registered @B as the orchestrator" in proc.stdout
    assert after["orchestrator"] == "@B"
    assert after["orchestrator_epoch"] == NOW


def test_dead_window_pin_registers_caller(tmp_path):
    """@A's window is still listed, but no claude is running in it — ADDR_GONE."""
    proc, after = _run(tmp_path, ["watch", "--if-unclaimed"], store=_pinned("@A"),
                       statuses={"@B": "busy"})
    assert proc.returncode == 0, proc.stderr
    assert "registered @B as the orchestrator" in proc.stdout
    assert after["orchestrator"] == "@B"


def test_stale_epoch_pin_registers_caller(tmp_path):
    """@A was issued by the tmux server before a reboot — ADDR_DANGLING, even though an @A
    exists and runs claude now (it is somebody else)."""
    proc, after = _run(tmp_path, ["watch", "--if-unclaimed"], store=_pinned("@A", OLD),
                       statuses=BOTH_LIVE)
    assert proc.returncode == 0, proc.stderr
    assert "registered @B as the orchestrator" in proc.stdout
    assert after["orchestrator"] == "@B"
    assert after["orchestrator_epoch"] == NOW


def test_pin_already_on_caller_is_a_successful_noop(tmp_path):
    proc, after = _run(tmp_path, ["watch", "--if-unclaimed"], store=_pinned("@B"),
                       statuses=BOTH_LIVE)
    assert proc.returncode == 0, proc.stderr
    assert "not claiming" not in proc.stdout
    assert after["orchestrator"] == "@B"
    assert after["orchestrator_epoch"] == NOW


def test_empty_status_map_is_not_evidence_of_death(tmp_path):
    """A `claude agents` hiccup (empty map) must read as live, exactly as it does for delivery —
    no second liveness rule that a flaky read could turn into a steal."""
    proc, after = _run(tmp_path, ["watch", "--if-unclaimed"], store=_pinned("@A"), statuses={})
    assert proc.returncode == 0, proc.stderr
    assert "not claiming" in proc.stdout
    assert after["orchestrator"] == "@A"


def test_plain_watch_still_takes_the_pin(tmp_path):
    """Unchanged semantics: a bare `chela watch` from @B moves a live @A's pin."""
    proc, after = _run(tmp_path, ["watch"], store=_pinned("@A"), statuses=BOTH_LIVE)
    assert proc.returncode == 0, proc.stderr
    assert "registered @B as the orchestrator" in proc.stdout
    assert after["orchestrator"] == "@B"


def test_if_unclaimed_refuses_a_window_argument(tmp_path):
    proc, after = _run(tmp_path, ["watch", "@A", "--if-unclaimed"], store=_pinned("@A"),
                       statuses=BOTH_LIVE)
    assert proc.returncode == 2
    assert after["orchestrator"] == "@A"


@pytest.mark.parametrize("state_pin, statuses, expect", [
    (None, BOTH_LIVE, None),
    ("@A", BOTH_LIVE, "@A"),
    ("@B", BOTH_LIVE, None),
    ("@A", {"@B": "busy"}, None),
])
def test_live_holder_predicate(state_pin, statuses, expect, monkeypatch):
    monkeypatch.delenv("CHELA_ORCHESTRATOR_WID", raising=False)
    from chela import inbox
    store = _pinned(state_pin) if state_pin else {"orchestrator": None}
    assert inbox.live_holder(store, statuses, NOW, "@B") == expect
