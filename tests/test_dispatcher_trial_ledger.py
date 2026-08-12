"""The dispatcher-owned trial ledger (CMX-105).

lean-alpha's honesty harness deflates a probe's Sharpe by N = trials run — a
fan-out that dispatches many probes and registers only the winner keeps N (and
the bar) artificially low. The fix: chela, not the agent, projects the `runs`
table onto a committed, git-visible, append-only ledger scoped to a workflow,
one line per dispatched task_id, that never shrinks — a died or abandoned
trial keeps its line.

These tests pin the pure merge (`reconcile_trial_ledger`), the outcome
classification (`_run_trial_outcome`), and the git guards shared with the
tracker strike (`_base_write_worktree` / `_base_write_commit_push`) — the
isolated, chela-owned checkout every unattended write lands through, never
the human's interactive checkout (CMX-174).
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from chela import config, dispatcher
from chela.workflow import WorkflowDef, resolve_workspace_root


# --- outcome classification (pure, no repo) ---------------------------------

def _row(**kw):
    base = {
        "task_id": "t1", "started_at": "2026-07-18T00:00:00Z",
        "status": "running", "attempt": 1, "pr_state": None,
    }
    base.update(kw)
    return base


def test_outcome_is_pending_while_in_flight():
    assert dispatcher._run_trial_outcome(_row(status="running")) is None
    assert dispatcher._run_trial_outcome(_row(status="claimed")) is None


def test_outcome_is_merged_once_the_pr_merges():
    assert dispatcher._run_trial_outcome(_row(status="awaiting_review", pr_state="merged")) == "merged"
    # Merged wins even over a status that hasn't caught up yet.
    assert dispatcher._run_trial_outcome(_row(status="running", pr_state="merged")) == "merged"


def test_outcome_is_died_only_once_retries_are_exhausted():
    assert dispatcher._run_trial_outcome(_row(status="failed", attempt=1)) is None
    assert dispatcher._run_trial_outcome(_row(status="failed", attempt=2)) is None
    assert dispatcher._run_trial_outcome(_row(status="failed", attempt=dispatcher.MAX_ATTEMPTS)) == "died"


def test_outcome_is_abandoned_when_done_without_a_merged_pr():
    assert dispatcher._run_trial_outcome(_row(status="done", pr_state=None)) == "abandoned"
    assert dispatcher._run_trial_outcome(_row(status="done", pr_state="open")) == "abandoned"


# --- run_is_terminal — the plain yes/no twin CMX-261's `chela.restore` fix consumes ------

def test_run_is_terminal_matches_every_case_run_trial_outcome_calls_terminal():
    """Same three conditions, asserted pairwise against `_run_trial_outcome` so the two
    can never silently drift apart."""
    rows = [
        _row(status="running"), _row(status="claimed"),
        _row(status="awaiting_review", pr_state="merged"),
        _row(status="running", pr_state="merged"),
        _row(status="failed", attempt=1), _row(status="failed", attempt=2),
        _row(status="failed", attempt=dispatcher.MAX_ATTEMPTS),
        _row(status="done", pr_state=None), _row(status="done", pr_state="open"),
    ]
    for row in rows:
        assert dispatcher.run_is_terminal(row) == (dispatcher._run_trial_outcome(row) is not None), row


def test_run_is_terminal_tolerates_a_row_missing_pr_state_and_attempt():
    """Unlike `_run_trial_outcome` (bracket access, a real `runs` row is guaranteed every
    column), this reads with `.get()` — a lighter-weight dict missing the columns
    entirely reads as still-pending rather than raising KeyError."""
    assert dispatcher.run_is_terminal({"status": "running"}) is False
    assert dispatcher.run_is_terminal({"status": "done"}) is True
    # `failed` with no `attempt` key at all must default to attempt 1 (still has
    # retries left), NOT to an already-exhausted count — a row missing the column
    # is still-pending, not silently dropped from the restore scan.
    assert dispatcher.run_is_terminal({"status": "failed"}) is False


# --- the pure merge -----------------------------------------------------------

def test_reconcile_appends_one_line_per_new_task_id():
    text, appended, resolved = dispatcher.reconcile_trial_ledger("", [_row(task_id="a"), _row(task_id="b")])
    lines = dispatcher._parse_trial_ledger(text)
    assert [e["task_id"] for e in lines] == ["a", "b"]
    assert all(e["outcome"] == "pending" for e in lines)
    assert appended == ["a", "b"]
    assert resolved == []


def test_reconcile_resolves_a_pending_line_in_place_without_moving_it():
    text, *_ = dispatcher.reconcile_trial_ledger("", [_row(task_id="a"), _row(task_id="b")])
    text2, appended, resolved = dispatcher.reconcile_trial_ledger(
        text, [_row(task_id="a", status="done", pr_state="merged"), _row(task_id="b")]
    )
    lines = dispatcher._parse_trial_ledger(text2)
    assert [e["task_id"] for e in lines] == ["a", "b"]  # order preserved
    assert lines[0]["outcome"] == "merged"
    assert lines[1]["outcome"] == "pending"
    assert appended == []
    assert resolved == ["a"]


def test_reconcile_never_touches_a_line_once_it_has_a_terminal_outcome():
    text, *_ = dispatcher.reconcile_trial_ledger("", [_row(task_id="a", status="done", pr_state="merged")])
    # A row that somehow looked "pending" again (should never happen, but the
    # merge must not un-resolve a line even so) leaves the line untouched.
    text2, appended, resolved = dispatcher.reconcile_trial_ledger(text, [_row(task_id="a", status="running")])
    assert text2 == text
    assert appended == [] and resolved == []


def test_reconcile_keeps_a_lines_for_a_task_id_no_longer_in_runs():
    # A pruned row (or a row from a different workflow this call never queried)
    # must not un-count a trial that already happened — the ledger only grows.
    text, *_ = dispatcher.reconcile_trial_ledger("", [_row(task_id="a", status="done", pr_state="merged")])
    text2, appended, resolved = dispatcher.reconcile_trial_ledger(text, [_row(task_id="b")])
    lines = dispatcher._parse_trial_ledger(text2)
    assert [e["task_id"] for e in lines] == ["a", "b"]
    assert appended == ["b"]


def test_reconcile_redispatch_of_the_same_task_id_never_adds_a_second_line():
    text, *_ = dispatcher.reconcile_trial_ledger("", [_row(task_id="a", status="failed", attempt=1)])
    # Same task_id retried (attempt bumped) — still pending, still one row.
    text2, appended, resolved = dispatcher.reconcile_trial_ledger(text, [_row(task_id="a", status="running", attempt=2)])
    assert dispatcher._parse_trial_ledger(text2) == dispatcher._parse_trial_ledger(text)
    assert appended == [] and resolved == []
    # Eventually dies — resolved in place, still one line.
    text3, appended, resolved = dispatcher.reconcile_trial_ledger(
        text2, [_row(task_id="a", status="failed", attempt=dispatcher.MAX_ATTEMPTS)]
    )
    lines = dispatcher._parse_trial_ledger(text3)
    assert len(lines) == 1
    assert lines[0]["outcome"] == "died"
    assert resolved == ["a"]


def test_reconcile_is_a_noop_when_nothing_changed():
    text, *_ = dispatcher.reconcile_trial_ledger("", [_row(task_id="a", status="done", pr_state="merged")])
    text2, appended, resolved = dispatcher.reconcile_trial_ledger(text, [_row(task_id="a", status="done", pr_state="merged")])
    assert text2 == text
    assert appended == [] and resolved == []


# --- config: opt-in only ------------------------------------------------------

def test_trial_ledger_rel_is_none_when_unconfigured(tmp_path):
    wf = WorkflowDef(path=tmp_path / "WORKFLOW.md", config={"project_key": "CMX"}, prompt_template="")
    assert dispatcher._trial_ledger_rel(wf) is None


def test_trial_ledger_rel_resolves_relative_to_the_repo(tmp_path):
    wf = WorkflowDef(
        path=tmp_path / "WORKFLOW.md",
        config={"project_key": "CMX", "trial_ledger": "TRIALS.jsonl"},
        prompt_template="",
    )
    assert dispatcher._trial_ledger_rel(wf) == "TRIALS.jsonl"


def test_write_trial_ledger_is_a_noop_when_unconfigured(tmp_path, monkeypatch):
    monkeypatch.setattr(dispatcher, "DB_PATH", tmp_path / "scheduler.db")
    wf = WorkflowDef(
        path=tmp_path / "WORKFLOW.md",
        config={"project_key": "CMX", "workspace": {"base_branch": "dev"}},
        prompt_template="",
    )
    with dispatcher._db() as conn:
        conn.execute(
            "INSERT INTO runs (task_id, workflow_path, title, status, attempt, pr_state) "
            "VALUES ('a', ?, 't', 'done', 1, 'merged')",
            (str(wf.path),),
        )
        assert dispatcher._write_trial_ledger(wf, conn) == 0
    assert list(tmp_path.iterdir()) == [tmp_path / "scheduler.db"] or not (tmp_path / "TRIALS.jsonl").exists()


# --- the git guards (an unattended writer on the base branch) ----------------

@pytest.fixture
def repo(tmp_path):
    """A real git repo on `dev` with an `origin` it can push to — same shape as
    the tracker-strike fixture, no tracker needed here."""
    origin = tmp_path / "origin.git"
    subprocess.run(["git", "init", "--bare", "-b", "dev", str(origin)], check=True, capture_output=True)
    work = tmp_path / "work"
    subprocess.run(["git", "clone", str(origin), str(work)], check=True, capture_output=True)
    for k, v in (("user.email", "t@example.com"), ("user.name", "T"), ("commit.gpgsign", "false")):
        subprocess.run(["git", "-C", str(work), "config", k, v], check=True, capture_output=True)
    (work / "README.md").write_text("seed\n")
    subprocess.run(["git", "-C", str(work), "add", "README.md"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(work), "commit", "-m", "seed"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(work), "push", "-u", "origin", "dev"], check=True, capture_output=True)
    return work


def _wf(repo: Path, ledger: str = "TRIALS.jsonl") -> WorkflowDef:
    return WorkflowDef(
        path=repo / "WORKFLOW.md",
        config={
            "project_key": "CMX",
            "trial_ledger": ledger,
            "workspace": {"root": str(config.CHELA_DIR / "worktrees"), "base_branch": "dev"},
        },
        prompt_template="",
    )


def _log(repo: Path) -> list[str]:
    out = subprocess.run(
        ["git", "-C", str(repo), "log", "--format=%s"], capture_output=True, text=True, check=True
    )
    return out.stdout.split("\n")


def _runs_conn(tmp_path, monkeypatch):
    monkeypatch.setattr(dispatcher, "DB_PATH", tmp_path / "scheduler.db")
    return dispatcher._db()


# The ledger lands through the isolated base-write worktree (see
# `dispatcher._base_write_worktree`), never `repo`'s own working tree — that is the whole
# point of the fix (CMX-174). So the ground truth these tests check is what actually
# landed on `origin`, not `repo`'s files.

def _origin(repo: Path) -> Path:
    return repo.parent / "origin.git"


def _origin_log(repo: Path, ref: str = "dev") -> list[str]:
    out = subprocess.run(
        ["git", "--git-dir", str(_origin(repo)), "log", "--format=%s", ref],
        capture_output=True, text=True, check=True,
    )
    return out.stdout.split("\n")


def _origin_show(repo: Path, rel: str, ref: str = "dev") -> str:
    out = subprocess.run(
        ["git", "--git-dir", str(_origin(repo)), "show", f"{ref}:{rel}"],
        capture_output=True, text=True, check=True,
    )
    return out.stdout


def _base_write_wt(repo: Path) -> Path:
    return resolve_workspace_root(_wf(repo)) / dispatcher.BASE_WRITE_DIRNAME


def test_write_trial_ledger_commits_and_pushes_a_new_trial(repo, tmp_path, monkeypatch):
    wf = _wf(repo)
    with _runs_conn(tmp_path, monkeypatch) as conn:
        conn.execute(
            "INSERT INTO runs (task_id, workflow_path, title, status, attempt, started_at) "
            "VALUES ('t1', ?, 'x', 'running', 1, ?)",
            (str(wf.path), dispatcher._now()),
        )
        assert dispatcher._write_trial_ledger(wf, conn) == 1

    lines = dispatcher._parse_trial_ledger(_origin_show(repo, "TRIALS.jsonl"))
    assert [e["task_id"] for e in lines] == ["t1"]
    assert lines[0]["outcome"] == "pending"
    assert "trial ledger" in _origin_log(repo)[0]  # actually pushed, not just committed
    assert not (repo / "TRIALS.jsonl").exists()  # the human's own checkout is untouched


def test_write_trial_ledger_resolves_an_existing_line_and_does_not_duplicate_it(repo, tmp_path, monkeypatch):
    wf = _wf(repo)
    with _runs_conn(tmp_path, monkeypatch) as conn:
        conn.execute(
            "INSERT INTO runs (task_id, workflow_path, title, status, attempt, started_at) "
            "VALUES ('t1', ?, 'x', 'running', 1, ?)",
            (str(wf.path), dispatcher._now()),
        )
        assert dispatcher._write_trial_ledger(wf, conn) == 1
        conn.execute("UPDATE runs SET status='done', pr_state='merged' WHERE task_id='t1'")
        # A second run for the SAME task_id (a retry) never adds a line either.
        assert dispatcher._write_trial_ledger(wf, conn) == 0  # 0 NEW lines — one resolved

    lines = dispatcher._parse_trial_ledger(_origin_show(repo, "TRIALS.jsonl"))
    assert len(lines) == 1
    assert lines[0]["outcome"] == "merged"


def test_write_trial_ledger_keeps_a_died_runs_line_even_after_it_is_pruned(repo, tmp_path, monkeypatch):
    """The whole point: a trial that dies must keep its ledger line even once the
    `runs` row itself is gone (`_prune_done_rows` only keeps recent `done` rows;
    a `failed`-at-cap row can be deleted by other cleanup paths too)."""
    wf = _wf(repo)
    with _runs_conn(tmp_path, monkeypatch) as conn:
        conn.execute(
            "INSERT INTO runs (task_id, workflow_path, title, status, attempt, started_at) "
            "VALUES ('dead', ?, 'x', 'failed', ?, ?)",
            (str(wf.path), dispatcher.MAX_ATTEMPTS, dispatcher._now()),
        )
        assert dispatcher._write_trial_ledger(wf, conn) == 1
        before = dispatcher._parse_trial_ledger(_origin_show(repo, "TRIALS.jsonl"))
        assert before[0]["outcome"] == "died"

        # The row is now deleted from `runs` entirely (simulating cleanup).
        conn.execute("DELETE FROM runs WHERE task_id='dead'")
        assert dispatcher._write_trial_ledger(wf, conn) == 0  # nothing to reconcile

    after = dispatcher._parse_trial_ledger(_origin_show(repo, "TRIALS.jsonl"))
    assert after == before  # the line survives, untouched


def test_write_trial_ledger_is_scoped_to_workflow_path(repo, tmp_path, monkeypatch):
    wf = _wf(repo)
    other_wf_path = repo / "OTHER.md"
    with _runs_conn(tmp_path, monkeypatch) as conn:
        conn.execute(
            "INSERT INTO runs (task_id, workflow_path, title, status, attempt, started_at) "
            "VALUES ('mine', ?, 'x', 'running', 1, ?)",
            (str(wf.path), dispatcher._now()),
        )
        conn.execute(
            "INSERT INTO runs (task_id, workflow_path, title, status, attempt, started_at) "
            "VALUES ('theirs', ?, 'x', 'running', 1, ?)",
            (str(other_wf_path), dispatcher._now()),
        )
        assert dispatcher._write_trial_ledger(wf, conn) == 1

    lines = dispatcher._parse_trial_ledger(_origin_show(repo, "TRIALS.jsonl"))
    assert [e["task_id"] for e in lines] == ["mine"]


def test_write_trial_ledger_lands_even_when_not_on_base_branch(repo, tmp_path, monkeypatch):
    """The headline bug (CMX-174): a human dogfooding in the shared checkout — a branch
    switch, an in-progress rebase — must never silently disable the unattended ledger
    write. lean-alpha's honesty harness depends on this: N must count every dispatched
    trial regardless of what the operator's own checkout happens to be doing."""
    subprocess.run(["git", "-C", str(repo), "checkout", "-b", "other"], check=True, capture_output=True)
    wf = _wf(repo)
    with _runs_conn(tmp_path, monkeypatch) as conn:
        conn.execute(
            "INSERT INTO runs (task_id, workflow_path, title, status, attempt, started_at) "
            "VALUES ('t1', ?, 'x', 'running', 1, ?)",
            (str(wf.path), dispatcher._now()),
        )
        assert dispatcher._write_trial_ledger(wf, conn) == 1

    lines = dispatcher._parse_trial_ledger(_origin_show(repo, "TRIALS.jsonl"))
    assert [e["task_id"] for e in lines] == ["t1"]
    # The human's own checkout is left exactly where they put it — no ledger file
    # ever appears there.
    head = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "--abbrev-ref", "HEAD"],
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    assert head == "other"
    assert not (repo / "TRIALS.jsonl").exists()


def test_write_trial_ledger_lands_even_when_a_human_has_the_ledger_dirty(repo, tmp_path, monkeypatch):
    seed = json.dumps({"task_id": "x", "dispatched_at": "", "outcome": "pending"}) + "\n"
    (repo / "TRIALS.jsonl").write_text(seed)
    subprocess.run(["git", "-C", str(repo), "add", "TRIALS.jsonl"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-m", "seed ledger"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(repo), "push"], check=True, capture_output=True)
    # Valid JSON (a human editing a line by hand, not corrupting the format), left
    # uncommitted in the shared checkout — must not block the isolated writer.
    dirty = seed + json.dumps({"task_id": "human-added", "dispatched_at": "", "outcome": "pending"}) + "\n"
    (repo / "TRIALS.jsonl").write_text(dirty)

    wf = _wf(repo)
    with _runs_conn(tmp_path, monkeypatch) as conn:
        conn.execute(
            "INSERT INTO runs (task_id, workflow_path, title, status, attempt, started_at) "
            "VALUES ('t1', ?, 'x', 'running', 1, ?)",
            (str(wf.path), dispatcher._now()),
        )
        assert dispatcher._write_trial_ledger(wf, conn) == 1

    lines = dispatcher._parse_trial_ledger(_origin_show(repo, "TRIALS.jsonl"))
    assert [e["task_id"] for e in lines] == ["x", "t1"]
    assert (repo / "TRIALS.jsonl").read_text() == dirty  # the human's edit, untouched


def test_write_trial_ledger_degrades_instead_of_crashing_on_a_corrupt_ledger(repo, tmp_path, monkeypatch, caplog):
    (repo / "TRIALS.jsonl").write_text("not json at all\n")
    subprocess.run(["git", "-C", str(repo), "add", "TRIALS.jsonl"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-m", "corrupt ledger"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(repo), "push"], check=True, capture_output=True)

    wf = _wf(repo)
    origin_before = _origin_log(repo)
    with _runs_conn(tmp_path, monkeypatch) as conn:
        conn.execute(
            "INSERT INTO runs (task_id, workflow_path, title, status, attempt, started_at) "
            "VALUES ('t1', ?, 'x', 'running', 1, ?)",
            (str(wf.path), dispatcher._now()),
        )
        with caplog.at_level("WARNING"):
            assert dispatcher._write_trial_ledger(wf, conn) == 0  # degrades, does not raise
    assert "not valid" in caplog.text
    assert _origin_log(repo) == origin_before  # nothing landed on top of the corrupt commit


def test_write_trial_ledger_warns_and_skips_when_the_ledger_path_is_gitignored(repo, tmp_path, monkeypatch, caplog):
    """A `trial_ledger:` opt-in is a committed artifact by definition (CMX-174 round 1):
    if the configured path is gitignored it can never land on origin/<base>, and an
    honesty ledger that only ever exists on one box is worse than no ledger at all — so
    this warns and writes nothing, rather than silently keeping a local-only copy."""
    (repo / ".gitignore").write_text("TRIALS.jsonl\n")
    subprocess.run(["git", "-C", str(repo), "add", ".gitignore"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-m", "ignore the ledger"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(repo), "push"], check=True, capture_output=True)
    origin_before = _origin_log(repo)

    wf = _wf(repo)
    with _runs_conn(tmp_path, monkeypatch) as conn:
        conn.execute(
            "INSERT INTO runs (task_id, workflow_path, title, status, attempt, started_at) "
            "VALUES ('t1', ?, 'x', 'running', 1, ?)",
            (str(wf.path), dispatcher._now()),
        )
        with caplog.at_level("WARNING"):
            assert dispatcher._write_trial_ledger(wf, conn) == 0

    assert "gitignored" in caplog.text
    assert _origin_log(repo) == origin_before  # nothing committed
    assert not (repo / "TRIALS.jsonl").exists()


def test_write_trial_ledger_rolls_back_its_commit_when_the_push_is_rejected(repo, tmp_path, monkeypatch):
    wf = _wf(repo)
    with _runs_conn(tmp_path, monkeypatch) as conn:
        conn.execute(
            "INSERT INTO runs (task_id, workflow_path, title, status, attempt, started_at) "
            "VALUES ('t1', ?, 'x', 'running', 1, ?)",
            (str(wf.path), dispatcher._now()),
        )
        origin_before = _origin_log(repo)
        with pytest.MonkeyPatch.context() as mp:
            real_git = dispatcher._git

            def fake_git(repo_path, *args, **kwargs):
                if args and args[0] == "push":
                    class _CP:
                        returncode = 1
                        stdout = ""
                        stderr = "rejected"
                    return _CP()
                return real_git(repo_path, *args, **kwargs)

            mp.setattr(dispatcher, "_git", fake_git)
            assert dispatcher._write_trial_ledger(wf, conn) == 0

    assert _origin_log(repo) == origin_before  # commit rolled back, nothing left dangling
    assert not (repo / "TRIALS.jsonl").exists()
    status = subprocess.run(
        ["git", "-C", str(_base_write_wt(repo)), "status", "--porcelain"],
        capture_output=True, text=True, check=True,
    ).stdout
    assert status.strip() == ""


# --- tick() integration: opt-out is byte-unchanged behavior -------------------

WORKFLOW_NO_LEDGER = """---
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

WORKFLOW_WITH_LEDGER = """---
project_key: CMX
tracker:
  kind: markdown
  path: TODO.md
trial_ledger: TRIALS.jsonl
workspace:
  root: {root}
  base_branch: dev
---
seed
"""


@pytest.fixture
def tracker_repo(repo):
    (repo / "TODO.md").write_text("- [ ] alpha\n")
    subprocess.run(["git", "-C", str(repo), "add", "TODO.md"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-m", "tracker"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(repo), "push", "origin", "dev"], check=True, capture_output=True)
    return repo


def _tick_env(repo: Path, tmp_path, monkeypatch, workflow_text: str):
    (repo / "WORKFLOW.md").write_text(workflow_text.format(root=tmp_path / ".chela" / "worktrees"))
    monkeypatch.setattr(dispatcher, "DB_PATH", tmp_path / "scheduler.db")
    monkeypatch.setattr(dispatcher, "_tmux_windows", lambda: set())
    monkeypatch.setattr(dispatcher, "_kill_window", lambda name: None)
    monkeypatch.setattr(dispatcher, "_fire_after_done", lambda wf: None)
    monkeypatch.setattr(dispatcher, "_spawn", lambda *a, **kw: False)


def test_tick_writes_no_ledger_for_a_workflow_that_never_opted_in(tracker_repo, tmp_path, monkeypatch):
    repo = tracker_repo
    _tick_env(repo, tmp_path, monkeypatch, WORKFLOW_NO_LEDGER)
    wf_path = repo / "WORKFLOW.md"
    with dispatcher._db() as conn:
        conn.execute(
            "INSERT INTO runs (task_id, workflow_path, title, status, attempt, started_at, pr_state) "
            "VALUES ('t1', ?, 'x', 'done', 1, ?, 'merged')",
            (str(wf_path), dispatcher._now()),
        )
    before = repo.rglob("*")
    before_files = {p for p in before if p.is_file() and ".git" not in p.parts}

    summary = dispatcher.tick(wf_path)

    after_files = {p for p in repo.rglob("*") if p.is_file() and ".git" not in p.parts}
    assert summary["trial_ledger"] == 0
    assert after_files == before_files  # byte-unchanged: not even a new file appeared
    assert not (repo / "TRIALS.jsonl").exists()


def test_tick_appends_a_trial_line_for_an_opted_in_workflow(tracker_repo, tmp_path, monkeypatch):
    repo = tracker_repo
    _tick_env(repo, tmp_path, monkeypatch, WORKFLOW_WITH_LEDGER)
    wf_path = repo / "WORKFLOW.md"
    with dispatcher._db() as conn:
        conn.execute(
            "INSERT INTO runs (task_id, workflow_path, title, status, attempt, started_at) "
            "VALUES ('t1', ?, 'x', 'running', 1, ?)",
            (str(wf_path), dispatcher._now()),
        )

    summary = dispatcher.tick(wf_path)

    assert summary["trial_ledger"] == 1
    lines = dispatcher._parse_trial_ledger(_origin_show(repo, "TRIALS.jsonl"))
    assert [e["task_id"] for e in lines] == ["t1"]
    assert lines[0]["outcome"] == "pending"
    assert not (repo / "TRIALS.jsonl").exists()  # the human's own checkout is untouched
