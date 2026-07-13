"""WORKFLOW.md is hot-reloaded, and a bad edit degrades instead of killing the daemon.

Before this, `chela run` parsed the workflow at boot: changing `concurrency.max`
or the prompt body meant restarting the daemon, and a YAML typo meant a broken
one. Symphony SPEC 6.2/6.3 makes both a MUST — detect and re-apply without a
restart; on an invalid reload keep the last known-good config, stay up, and emit
an operator-visible error.

These tests pin the three halves of that: the reload itself (and the stat gate
that keeps it from re-parsing on every 30s tick, forever), the degrade path
(last-good config in force, reconciliation continues, NEW dispatches blocked),
and the operator-visible surface (the Settings drawer, not just a log line).
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from chela import dispatcher, workflow
from chela.workflow import (
    WorkflowDef,
    load_workflow_cached,
    poll_interval_seconds,
    workflow_error,
)


WF = """---
project_key: CMX
tracker:
  kind: markdown
  path: TODO.md
workspace:
  root: {root}
  base_branch: dev
concurrency:
  max: {max}
---
seed prompt v{v}
"""


def _write(p: Path, text: str) -> None:
    """Write, then force a distinct mtime.

    The reload gate is (mtime_ns, size), and a test rewriting a same-size file
    microseconds later can land inside a coarse filesystem timestamp — which
    would make these tests flaky about the *filesystem*, not about the code.
    """
    p.write_text(text)
    st = p.stat()
    bump = st.st_mtime_ns + 1_000_000_000
    os.utime(p, ns=(bump, bump))


@pytest.fixture(autouse=True)
def _clean_cache():
    workflow.reset_cache()
    yield
    workflow.reset_cache()


@pytest.fixture
def wf_file(tmp_path) -> Path:
    p = tmp_path / "WORKFLOW.md"
    _write(p, WF.format(root=tmp_path / "wt", max=1, v=1))
    return p


# --- detect + re-apply, without a restart ----------------------------------

def test_a_changed_workflow_is_reapplied_without_a_restart(wf_file, tmp_path):
    first = load_workflow_cached(wf_file)
    assert first.ok
    assert first.workflow.get("concurrency", "max") == 1
    assert first.workflow.prompt_template == "seed prompt v1"

    _write(wf_file, WF.format(root=tmp_path / "wt", max=3, v=2))

    # Same process, same object graph — no restart anywhere in this test.
    second = load_workflow_cached(wf_file)
    assert second.ok
    assert second.workflow.get("concurrency", "max") == 3   # config re-applied...
    assert second.workflow.prompt_template == "seed prompt v2"  # ...and so is the template
    assert second.reloads == 1


def test_an_unchanged_workflow_is_not_re_parsed_on_every_tick(wf_file, monkeypatch):
    """The gate matters: this runs every 30s, forever."""
    parses = []
    real = workflow.parse_workflow
    monkeypatch.setattr(workflow, "parse_workflow",
                        lambda p, text: (parses.append(p), real(p, text))[1])

    for _ in range(20):
        assert load_workflow_cached(wf_file).ok
    assert len(parses) == 1   # the first load; the other 19 were a stat and nothing else


def test_a_touched_but_identical_file_is_not_re_parsed(wf_file, monkeypatch):
    load_workflow_cached(wf_file)
    parses = []
    real = workflow.parse_workflow
    monkeypatch.setattr(workflow, "parse_workflow",
                        lambda p, text: (parses.append(p), real(p, text))[1])

    text = wf_file.read_text()
    _write(wf_file, text)          # rewritten byte-identically → new mtime, same content
    assert load_workflow_cached(wf_file).ok
    assert parses == []            # the content hash caught it


# --- degrade, don't die -----------------------------------------------------

def test_a_broken_edit_keeps_the_last_known_good_config(wf_file, tmp_path, caplog):
    good = load_workflow_cached(wf_file).workflow
    assert good.get("concurrency", "max") == 1

    _write(wf_file, "---\nproject_key: CMX\nconcurrency:\n  max: [unclosed\n---\nbody\n")

    status = load_workflow_cached(wf_file)
    assert not status.ok
    assert status.error                       # ...and it says what is wrong
    assert status.workflow is good            # the LAST-GOOD config, still in force
    assert status.workflow.get("concurrency", "max") == 1
    assert "keeping the last known-good config" in caplog.text

    # And it recovers on its own once the file parses again — no restart.
    _write(wf_file, WF.format(root=tmp_path / "wt", max=2, v=3))
    healed = load_workflow_cached(wf_file)
    assert healed.ok
    assert healed.workflow.get("concurrency", "max") == 2


def test_a_half_written_file_is_treated_as_keep_last_good_not_as_empty(wf_file):
    """An editor saving mid-write leaves truncated bytes. That is not "no workflow"."""
    good = load_workflow_cached(wf_file).workflow
    _write(wf_file, "---\nproject_key: CMX\ntracker:\n  kind: mark")  # cut off mid-save

    status = load_workflow_cached(wf_file)
    assert not status.ok
    assert status.workflow is good


def test_a_missing_or_never_valid_workflow_reports_instead_of_raising(tmp_path):
    missing = tmp_path / "gone" / "WORKFLOW.md"
    status = load_workflow_cached(missing)     # must not raise: it runs in the daemon loop
    assert status.workflow is None
    assert status.error and "FileNotFoundError" in status.error

    bad = tmp_path / "WORKFLOW.md"
    _write(bad, "---\nproject_key: nope\n---\nbody\n")   # invalid project_key
    status = load_workflow_cached(bad)
    assert status.workflow is None             # nothing has ever parsed → no last-good
    assert status.error


def test_workflow_error_is_the_cheap_probe_the_dashboard_polls(wf_file):
    assert workflow_error(wf_file) is None
    _write(wf_file, "---\n: :\n---\n")
    assert workflow_error(wf_file)


# --- the effective poll interval -------------------------------------------

def _wf_cfg(config: dict) -> WorkflowDef:
    return WorkflowDef(path=Path("/w/WORKFLOW.md"), config=config, prompt_template="")


def test_poll_interval_comes_from_the_workflow_and_falls_back_to_the_default():
    assert poll_interval_seconds(_wf_cfg({}), 60) == 60
    assert poll_interval_seconds(None, 60) == 60
    assert poll_interval_seconds(_wf_cfg({"polling": {"interval_ms": 15000}}), 60) == 15
    # A garbage value must not fail the tick — it falls back.
    assert poll_interval_seconds(_wf_cfg({"polling": {"interval_ms": "soon"}}), 60) == 60
    # ...and a typo'd tiny interval is clamped, not turned into a spin loop.
    assert poll_interval_seconds(_wf_cfg({"polling": {"interval_ms": 10}}), 60) == 5


def test_poll_interval_is_re_read_when_the_file_changes(wf_file, tmp_path):
    assert dispatcher.poll_interval(wf_file, default=60) == 60
    _write(wf_file, WF.format(root=tmp_path / "wt", max=1, v=1).replace(
        "project_key: CMX", "project_key: CMX\npolling:\n  interval_ms: 20000"))
    assert dispatcher.poll_interval(wf_file, default=60) == 20


# --- the tick: reconcile keeps running, new dispatch is blocked -------------

@pytest.fixture
def repo(tmp_path):
    """A git repo on `dev` with a tracker and an `origin`, driving a real tick()."""
    origin = tmp_path / "origin.git"
    subprocess.run(["git", "init", "--bare", "-b", "dev", str(origin)], check=True, capture_output=True)
    work = tmp_path / "work"
    subprocess.run(["git", "clone", str(origin), str(work)], check=True, capture_output=True)
    for k, v in (("user.email", "t@example.com"), ("user.name", "T"), ("commit.gpgsign", "false")):
        subprocess.run(["git", "-C", str(work), "config", k, v], check=True, capture_output=True)
    (work / "TODO.md").write_text("- [ ] alpha\n- [ ] beta\n")
    subprocess.run(["git", "-C", str(work), "add", "TODO.md"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(work), "commit", "-m", "seed"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(work), "push", "-u", "origin", "dev"], check=True, capture_output=True)
    return work


@pytest.fixture
def ticking(repo, tmp_path, monkeypatch):
    _write(repo / "WORKFLOW.md", WF.format(root=tmp_path / "wt", max=1, v=1))
    monkeypatch.setattr(dispatcher, "DB_PATH", tmp_path / "scheduler.db")
    monkeypatch.setattr(dispatcher, "_tmux_windows", lambda: set())
    monkeypatch.setattr(dispatcher, "_kill_window", lambda name: None)
    monkeypatch.setattr(dispatcher, "_fire_after_done", lambda wf: None)
    return repo


def _spawn_counter(monkeypatch) -> list:
    spawned: list = []
    monkeypatch.setattr(dispatcher, "_spawn",
                        lambda wf, task, attempt, conn: (spawned.append(task.id), True)[1])
    return spawned


def _seed_merged_run(wf_path: Path, task_id: str) -> None:
    with dispatcher._db() as conn:
        conn.execute(
            "INSERT INTO runs (task_id, workflow_path, title, status, window_name, "
            "started_at, attempt, pr_url, pr_state) "
            "VALUES (?,?,?,'awaiting_review',?,?,?,?,'open')",
            (task_id, str(wf_path), "t", "@9", dispatcher._now(), 1,
             "https://github.com/o/r/pull/1"),
        )
        conn.commit()


def test_tick_reapplies_a_changed_concurrency_without_a_restart(ticking, tmp_path, monkeypatch):
    wf_path = ticking / "WORKFLOW.md"
    spawned = _spawn_counter(monkeypatch)

    # Two tasks are open, but the lane is one wide.
    assert dispatcher.tick(wf_path)["dispatched"] == 1   # concurrency.max: 1
    assert len(spawned) == 1

    # Widen the lane. No restart — the very next tick honors it.
    _write(wf_path, WF.format(root=tmp_path / "wt", max=2, v=1))
    assert dispatcher.tick(wf_path)["dispatched"] == 2   # concurrency.max: 2


def test_a_broken_workflow_blocks_dispatch_but_keeps_reconciling(ticking, monkeypatch, caplog):
    repo = ticking
    wf_path = repo / "WORKFLOW.md"
    good = load_workflow_cached(wf_path).workflow
    from chela.sources import get_source
    alpha = next(t.id for t in get_source(good).list_open_tasks() if t.title == "alpha")
    _seed_merged_run(wf_path, alpha)
    monkeypatch.setattr(dispatcher, "_read_pr_status", lambda url, d: ("merged", "MERGEABLE"))
    spawned = _spawn_counter(monkeypatch)

    _write(wf_path, "---\nproject_key: CMX\ntracker: [oops\n---\n")

    summary = dispatcher.tick(wf_path)

    # Blocked, and it SAYS so — the daemon and the drawer both read this.
    assert summary["blocked"] is True
    assert summary["error"]
    assert summary["dispatched"] == 0
    assert spawned == []                       # not one new agent launched
    # ...while everything the last-good config already knew how to finish, finished.
    assert summary["reconciled_done"] == 1
    assert summary["tracker_struck"] == 1
    assert (repo / "TODO.md").read_text() == "- [x] alpha\n- [ ] beta\n"
    assert "Dispatch paused" in caplog.text

    # Fix the file: dispatch resumes on the next tick, still no restart.
    _write(wf_path, WF.format(root=repo.parent / "wt", max=1, v=1))
    healed = dispatcher.tick(wf_path)
    assert healed["blocked"] is False
    assert healed["error"] is None
    assert healed["dispatched"] == 1


def test_tick_on_a_never_valid_workflow_returns_blocked_instead_of_raising(tmp_path, monkeypatch):
    monkeypatch.setattr(dispatcher, "DB_PATH", tmp_path / "scheduler.db")
    bad = tmp_path / "WORKFLOW.md"
    _write(bad, "---\nnot: a workflow\n---\n")

    summary = dispatcher.tick(bad)   # the daemon loop must survive this

    assert summary["blocked"] is True
    assert summary["error"]
    assert summary["dispatched"] == 0
