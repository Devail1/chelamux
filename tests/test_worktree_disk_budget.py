"""🧹💽 The disk-budget rail (CMX-164) — the `memcap` analog for disk.

`~/.chela/worktrees/` is the largest per-agent resource chela consumes, and the one that
scales with agent count: a live audit found 1.3 GB of worktrees on disk with ZERO active
runs, every one an orphan. An adopter with a heavier repo (a Rust `target/`, an ML venv, a
Node monorepo — 1-10 GB *per worktree*) can run the box out of disk with nothing to stop
it — worse than an OOM, because it takes git, sqlite, tmux and the daemon down together.

These tests pin: the env-var parser (`config.worktree_disk_budget_bytes`), the capability
announcement, and the one place it actually gates something — the fresh-claim path in
`dispatcher.tick`. A rework or a judge never fork a brand-new worktree (they attach to or
reuse one already on disk), so neither is gated here.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from chela import capabilities, config, dispatcher
from chela.sources.markdown import MarkdownSource
from chela.workflow import WorkflowDef

# --- config.worktree_disk_budget_bytes: the parser ---------------------------------------


def test_unset_is_off(monkeypatch):
    monkeypatch.delenv("CHELA_WORKTREE_DISK_BUDGET", raising=False)
    assert config.worktree_disk_budget_bytes() == 0


def test_explicit_zero_is_off(monkeypatch):
    monkeypatch.setenv("CHELA_WORKTREE_DISK_BUDGET", "0")
    assert config.worktree_disk_budget_bytes() == 0


def test_a_bare_integer_is_bytes(monkeypatch):
    monkeypatch.setenv("CHELA_WORKTREE_DISK_BUDGET", "12345")
    assert config.worktree_disk_budget_bytes() == 12345


@pytest.mark.parametrize("raw,expected", [
    ("20G", 20 * 1024**3),
    ("500M", 500 * 1024**2),
    ("2K", 2 * 1024),
    ("1T", 1024**4),
    ("20g", 20 * 1024**3),          # case-insensitive
])
def test_size_suffixes(monkeypatch, raw, expected):
    monkeypatch.setenv("CHELA_WORKTREE_DISK_BUDGET", raw)
    assert config.worktree_disk_budget_bytes() == expected


def test_garbage_degrades_to_off_not_a_crash(monkeypatch):
    monkeypatch.setenv("CHELA_WORKTREE_DISK_BUDGET", "not-a-size")
    assert config.worktree_disk_budget_bytes() == 0


def test_a_negative_size_is_off(monkeypatch):
    monkeypatch.setenv("CHELA_WORKTREE_DISK_BUDGET", "-5G")
    assert config.worktree_disk_budget_bytes() == 0


# --- capabilities.effective(): the announcement ------------------------------------------


def _cap(caps, key):
    return next(c for c in caps if c.key == "worktree_disk_budget") if key is None else \
        next(c for c in caps if c.key == key)


def test_capability_reports_off_by_default(monkeypatch):
    monkeypatch.delenv("CHELA_WORKTREE_DISK_BUDGET", raising=False)
    cap = _cap(capabilities.effective(), "worktree_disk_budget")
    assert cap.on is False
    assert "unset/0" in cap.detail


def test_capability_reports_on_with_the_human_size(monkeypatch):
    monkeypatch.setenv("CHELA_WORKTREE_DISK_BUDGET", "20G")
    cap = _cap(capabilities.effective(), "worktree_disk_budget")
    assert cap.on is True
    assert "20.0G" in cap.detail


# --- capabilities.live(): a live_reload capability must not go stale ----------------------
#
# CMX-280 gave memory_slice_budget the same live_reload=True treatment this capability
# already had, and in doing so proved this rail had NO test pinning that live_reload
# actually does anything: `effective()` (above) always reads config fresh regardless of
# the flag, so a test that only calls `effective()` cannot tell live_reload=True from
# live_reload=False. Only `capabilities.live()` branches on the flag (it is what decides
# whether a published boot snapshot gets reconciled against current config or returned
# stale) — see test_memory_slice_budget.py's sibling test, which this mirrors.


def test_worktree_disk_budget_reflects_a_post_boot_env_change_not_the_boot_snapshot(
        monkeypatch):
    monkeypatch.delenv("CHELA_WORKTREE_DISK_BUDGET", raising=False)
    capabilities.publish(capabilities.effective(), boot_id="b1")
    assert capabilities.live_capability("worktree_disk_budget")["on"] is False

    # No restart — dispatcher.py reads worktree_disk_budget_bytes() fresh on every tick,
    # which is the whole point of NOT marking this knob restart_required.
    monkeypatch.setenv("CHELA_WORKTREE_DISK_BUDGET", "20G")
    cap = capabilities.live_capability("worktree_disk_budget")
    assert cap["on"] is True
    assert "20.0G" in cap["detail"]


# --- the dispatcher gate: refuses a FRESH claim, nothing else ----------------------------


@pytest.fixture
def repo(tmp_path):
    origin = tmp_path / "origin.git"
    subprocess.run(["git", "init", "--bare", "-b", "dev", str(origin)], check=True, capture_output=True)
    work = tmp_path / "work"
    subprocess.run(["git", "clone", str(origin), str(work)], check=True, capture_output=True)
    for k, v in (("user.email", "t@example.com"), ("user.name", "T"), ("commit.gpgsign", "false")):
        subprocess.run(["git", "-C", str(work), "config", k, v], check=True, capture_output=True)
    (work / "TODO.md").write_text("- [ ] alpha\n")
    subprocess.run(["git", "-C", str(work), "add", "TODO.md"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(work), "commit", "-m", "seed"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(work), "push", "-u", "origin", "dev"], check=True, capture_output=True)
    return work


WORKFLOW = """---
project_key: CMX
tracker:
  kind: markdown
  path: TODO.md
workspace:
  root: {root}
  base_branch: dev
---
seed
"""


@pytest.fixture
def ticking(repo, tmp_path, monkeypatch):
    (repo / "WORKFLOW.md").write_text(WORKFLOW.format(root=tmp_path / ".chela" / "worktrees"))
    monkeypatch.setattr(dispatcher, "DB_PATH", tmp_path / "scheduler.db")
    monkeypatch.setattr(dispatcher, "_tmux_windows", lambda: set())
    monkeypatch.setattr(dispatcher, "_kill_window", lambda name: None)
    monkeypatch.setattr(dispatcher, "_fire_after_done", lambda wf: None)
    return repo


def _source(repo: Path) -> MarkdownSource:
    wf = WorkflowDef(
        path=repo / "WORKFLOW.md",
        config={
            "tracker": {"kind": "markdown", "path": "TODO.md"},
            "workspace": {"root": str(config.CHELA_DIR / "worktrees"), "base_branch": "dev"},
        },
        prompt_template="",
    )
    return MarkdownSource(wf)


def test_over_budget_refuses_the_claim_and_logs_loudly(ticking, tmp_path, monkeypatch, caplog):
    repo = ticking
    wf_path = repo / "WORKFLOW.md"
    worktrees_root = tmp_path / ".chela" / "worktrees"
    worktrees_root.mkdir(parents=True)
    (worktrees_root / "junk.bin").write_bytes(b"x" * 1000)
    monkeypatch.setenv("CHELA_WORKTREE_DISK_BUDGET", "100")  # 1000 bytes on disk > 100-byte budget

    import logging
    with caplog.at_level(logging.WARNING):
        summary = dispatcher.tick(wf_path)

    assert summary["disk_budget_exceeded"] is True
    assert summary["dispatched"] == 0
    assert "REFUSED" in caplog.text
    assert str(worktrees_root) in caplog.text or "CHELA_WORKTREE_DISK_BUDGET" in caplog.text


def test_under_budget_dispatches_normally(ticking, tmp_path, monkeypatch):
    repo = ticking
    wf_path = repo / "WORKFLOW.md"
    monkeypatch.setattr(dispatcher, "_launch_agent", lambda *a, **kw: None)
    monkeypatch.setattr(dispatcher, "_run_critic", lambda *a, **kw: None)
    monkeypatch.setenv("CHELA_WORKTREE_DISK_BUDGET", "999999999999")  # effectively unlimited

    summary = dispatcher.tick(wf_path)

    assert summary["disk_budget_exceeded"] is False
    assert summary["dispatched"] == 1


def test_budget_unset_dispatches_normally(ticking, tmp_path, monkeypatch):
    repo = ticking
    wf_path = repo / "WORKFLOW.md"
    monkeypatch.setattr(dispatcher, "_launch_agent", lambda *a, **kw: None)
    monkeypatch.setattr(dispatcher, "_run_critic", lambda *a, **kw: None)
    monkeypatch.delenv("CHELA_WORKTREE_DISK_BUDGET", raising=False)

    summary = dispatcher.tick(wf_path)

    assert summary["disk_budget_exceeded"] is False
    assert summary["dispatched"] == 1
