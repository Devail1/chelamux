"""A reaped worktree must not leave its tmux window behind as a live-looking agent
(CMX-329, issue #403). `_reap_terminal_windows` is the self-healing sweep: every tick,
for THIS workflow, kill any tmux window still alive for a run that has already gone
`done`/`closed` — but ONLY when the window's recorded id+epoch prove chela still owns it.

Observed three times on 2026-08-24 with no special trigger (three ordinary merges, three
survivors): the in-line kill fired at the moment of transition is fire-and-forget and can
silently fail, so nothing ever revisited a straggler. This sweep is the second chance.
"""
from __future__ import annotations

import subprocess

import pytest

from chela import dispatcher

WORKFLOW_PATH = "/repo/WORKFLOW.md"
CURRENT_EPOCH = "1234-5678"
OTHER_EPOCH = "9999-0000"

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
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(dispatcher, "DB_PATH", tmp_path / "scheduler.db")
    monkeypatch.setattr(dispatcher.epoch, "current", lambda: CURRENT_EPOCH)
    return dispatcher


@pytest.fixture
def repo(tmp_path):
    """A real git repo on `dev` with a tracker and an `origin` it can push to —
    the minimum `tick()` needs to run its full pass for real."""
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


@pytest.fixture
def ticking(repo, tmp_path, monkeypatch):
    """A repo whose WORKFLOW.md drives a real `tick()`, with tmux/spawn stubbed except
    for the two hooks this file's wiring test needs to observe directly."""
    (repo / "WORKFLOW.md").write_text(WORKFLOW.format(root=tmp_path / ".chela" / "worktrees"))
    monkeypatch.setattr(dispatcher, "DB_PATH", tmp_path / "scheduler.db")
    monkeypatch.setattr(dispatcher, "_spawn", lambda *a, **kw: False)
    monkeypatch.setattr(dispatcher.epoch, "current", lambda: CURRENT_EPOCH)
    return repo


def _seed(conn, task_id: str, status: str, window_id: str | None, window_epoch: str | None):
    conn.execute(
        "INSERT INTO runs (task_id, workflow_path, title, status, window_id, window_epoch, "
        "started_at, attempt) VALUES (?,?,?,?,?,?,?,?)",
        (task_id, WORKFLOW_PATH, "t", status, window_id, window_epoch, dispatcher._now(), 1),
    )


def _live_ids(*ids):
    return lambda: set(ids)


def test_reaps_a_terminal_owned_window_still_alive(db, monkeypatch):
    """⭐ GUARD: `done` + matching epoch + still-live window → killed."""
    killed = []
    monkeypatch.setattr(dispatcher, "_kill_window", lambda wid: killed.append(wid))
    monkeypatch.setattr(dispatcher, "_tmux_window_ids", _live_ids("@45"))
    with dispatcher._db() as conn:
        _seed(conn, "cmx-1", "done", "@45", CURRENT_EPOCH)
        conn.commit()
        reaped = dispatcher._reap_terminal_windows(conn, WORKFLOW_PATH)

    assert reaped == 1
    assert killed == ["@45"]


def test_never_kills_a_window_for_a_non_terminal_run(db, monkeypatch):
    """⛔ NEGATIVE CONTROL — the other half of the guard. Every status that is NOT
    `done`/`closed` is equally out of scope, not just the two review-lane ones: a
    LIVE, mid-work run (`claimed`, `running`) is the worst case (its window is doing
    real work right now), and a rework-eligible or dead-but-not-yet-terminal run
    (`changes_requested`, `needs_human`, `failed`) may still be re-spawned into its
    window. Same owned, live window as the positive tests — only the status differs —
    so a broadened WHERE clause (e.g. dropping the `status IN (...)` filter, or adding
    just one more status to it) would turn this red for the reason it exists."""
    killed = []
    monkeypatch.setattr(dispatcher, "_kill_window", lambda wid: killed.append(wid))
    non_terminal = ("claimed", "running", "changes_requested", "needs_human",
                     "awaiting_review", "failed")
    ids = [f"@{50 + i}" for i in range(len(non_terminal))]
    monkeypatch.setattr(dispatcher, "_tmux_window_ids", _live_ids(*ids))
    with dispatcher._db() as conn:
        for i, status in enumerate(non_terminal):
            _seed(conn, f"cmx-nt-{i}", status, ids[i], CURRENT_EPOCH)
        conn.commit()
        reaped = dispatcher._reap_terminal_windows(conn, WORKFLOW_PATH)

    assert reaped == 0
    assert killed == []


def test_reaps_a_closed_owned_window_still_alive(db, monkeypatch):
    """⭐ GUARD: `closed` is the other half of the terminal set — a run whose PR was
    closed without merging (CMX-265) gets exactly the same second-chance reap as
    `done`. No fixture anywhere else in this file ever seeds `closed`; without this
    one, narrowing the WHERE clause down to `status='done'` alone is invisible to
    the suite even though the docstring calls `closed` out by name."""
    killed = []
    monkeypatch.setattr(dispatcher, "_kill_window", lambda wid: killed.append(wid))
    monkeypatch.setattr(dispatcher, "_tmux_window_ids", _live_ids("@60"))
    with dispatcher._db() as conn:
        _seed(conn, "cmx-closed", "closed", "@60", CURRENT_EPOCH)
        conn.commit()
        reaped = dispatcher._reap_terminal_windows(conn, WORKFLOW_PATH)

    assert reaped == 1
    assert killed == ["@60"]


def test_does_not_kill_a_dead_window(db, monkeypatch):
    """A terminal row whose window is already gone (the normal, expected case — killed
    at transition time) must not trip a phantom kill-window call."""
    killed = []
    monkeypatch.setattr(dispatcher, "_kill_window", lambda wid: killed.append(wid))
    monkeypatch.setattr(dispatcher, "_tmux_window_ids", _live_ids())  # nothing live
    with dispatcher._db() as conn:
        _seed(conn, "cmx-4", "done", "@48", CURRENT_EPOCH)
        conn.commit()
        reaped = dispatcher._reap_terminal_windows(conn, WORKFLOW_PATH)

    assert reaped == 0
    assert killed == []


def test_never_kills_a_window_whose_epoch_does_not_match(db, monkeypatch):
    """⛔ CMX-48: an id stamped under a dead tmux server is a COINCIDENCE if it matches
    something live now, not this run's window — the server was restarted (renumbered
    from `@0`) and `@45` may belong to an unrelated agent today. Only same-epoch ids are
    ever acted on."""
    killed = []
    monkeypatch.setattr(dispatcher, "_kill_window", lambda wid: killed.append(wid))
    monkeypatch.setattr(dispatcher, "_tmux_window_ids", _live_ids("@45"))
    with dispatcher._db() as conn:
        _seed(conn, "cmx-5", "done", "@45", OTHER_EPOCH)  # stale epoch
        conn.commit()
        reaped = dispatcher._reap_terminal_windows(conn, WORKFLOW_PATH)

    assert reaped == 0
    assert killed == []


def test_never_kills_a_window_with_no_recorded_id(db, monkeypatch):
    """A pre-CMX-77 row (or one that never made it past the claim before dying) has no
    `window_id` to verify ownership with at all — never a name-based fallback kill."""
    killed = []
    monkeypatch.setattr(dispatcher, "_kill_window", lambda wid: killed.append(wid))
    monkeypatch.setattr(dispatcher, "_tmux_window_ids", _live_ids("@45"))
    with dispatcher._db() as conn:
        _seed(conn, "cmx-6", "done", None, None)
        conn.commit()
        reaped = dispatcher._reap_terminal_windows(conn, WORKFLOW_PATH)

    assert reaped == 0
    assert killed == []


def test_unreadable_current_epoch_verifies_nothing(db, monkeypatch):
    """No tmux, no server: an unknown current epoch can never be compared equal to a
    stamp, so nothing is killed (the same fail-closed shape as `epoch.is_dangling`)."""
    killed = []
    monkeypatch.setattr(dispatcher.epoch, "current", lambda: None)
    monkeypatch.setattr(dispatcher, "_kill_window", lambda wid: killed.append(wid))
    monkeypatch.setattr(dispatcher, "_tmux_window_ids", _live_ids("@45"))
    with dispatcher._db() as conn:
        _seed(conn, "cmx-7", "done", "@45", CURRENT_EPOCH)
        conn.commit()
        reaped = dispatcher._reap_terminal_windows(conn, WORKFLOW_PATH)

    assert reaped == 0
    assert killed == []


def test_tick_wires_the_reap_sweep_into_the_real_call_site(ticking, monkeypatch):
    """⭐ WIRING GUARD: every other test in this file calls `_reap_terminal_windows` /
    `_tmux_window_ids` directly — none of them would notice if the production call site
    at the `tick()` level (`summary["window_reaped"] = _reap_terminal_windows(...)`)
    were ripped out entirely, since the helper itself would still work in isolation.
    This one drives a REAL `tick()` end to end and asserts both that `_kill_window` was
    actually invoked and that the summary field a real caller reads back reflects it.
    Delete or no-op the call site (`summary["window_reaped"] = 0`) and this goes red
    while every other test in this file stays green."""
    repo = ticking
    wf_path = repo / "WORKFLOW.md"
    killed = []
    monkeypatch.setattr(dispatcher, "_kill_window", lambda wid: killed.append(wid))
    monkeypatch.setattr(dispatcher, "_tmux_window_ids", _live_ids("@70"))
    with dispatcher._db() as conn:
        conn.execute(
            "INSERT INTO runs (task_id, workflow_path, title, status, window_id, "
            "window_epoch, started_at, attempt) VALUES (?,?,?,?,?,?,?,?)",
            ("cmx-wired", str(wf_path), "t", "done", "@70", CURRENT_EPOCH, dispatcher._now(), 1),
        )
        conn.commit()

    summary = dispatcher.tick(wf_path)

    assert summary["window_reaped"] == 1
    assert killed == ["@70"]


def test_tmux_window_ids_reads_the_real_ids_column(monkeypatch):
    """`_tmux_window_ids` must ask tmux for `#{window_id}` (an `@N` address), not
    `#W` (the name) — the reap sweep verifies ownership by id, and a name carries no
    epoch to check."""
    captured = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd

        class R:
            returncode = 0
            stdout = "@1\n@2\n"

        return R()

    monkeypatch.setattr(subprocess, "run", fake_run)
    ids = dispatcher._tmux_window_ids()

    assert ids == {"@1", "@2"}
    assert "#{window_id}" in captured["cmd"]
