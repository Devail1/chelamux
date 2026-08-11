"""⚖️ CMX-176 — the judge's baseline must not be red because ``base_branch`` moved on.

A dispatched run's branch is cut at claim time and then sits — through review, rework
rounds, and CI — while ``base_branch`` moves underneath it. Judging that stale tip means a
fix that landed on base after the claim is absent from the mutation baseline: the
pre-mutation suite goes red for reasons that have nothing to do with the PR, and the judge
correctly (and uselessly) refuses every experiment with cannot_verify. **Observed live
2026-07-25 on cmx-174 (#222):** 14 commits behind ``dev``; a human ran ``gh pr
update-branch`` by hand and the baseline went green. These tests pin the automatic version
of that catch-up (``dispatcher._refresh_judge_worktree``) and its wiring into
``_spawn_judge``.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from chela import dispatcher
from chela.workflow import WorkflowDef


def _git(*args: str, cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(cwd), *args], check=True, capture_output=True, text=True,
    )


@pytest.fixture
def origin(tmp_path) -> Path:
    o = tmp_path / "origin.git"
    subprocess.run(["git", "init", "--bare", "-b", "dev", str(o)], check=True, capture_output=True)
    return o


@pytest.fixture
def repo(tmp_path, origin) -> Path:
    """The dispatcher's own checkout — clone of origin, on `dev`, with a seed commit pushed."""
    work = tmp_path / "repo"
    subprocess.run(["git", "clone", str(origin), str(work)], check=True, capture_output=True)
    for k, v in (("user.email", "t@example.com"), ("user.name", "T"), ("commit.gpgsign", "false")):
        _git("config", k, v, cwd=work)
    (work / "app.py").write_text("VALUE = 1\n")
    _git("add", "app.py", cwd=work)
    _git("commit", "-m", "seed", cwd=work)
    _git("push", "-u", "origin", "dev", cwd=work)
    return work


def _branch_from_head(repo: Path, name: str) -> str:
    """A PR branch cut from the repo's current `dev` tip. Returns its head sha."""
    _git("branch", name, cwd=repo)
    return _git("rev-parse", name, cwd=repo).stdout.strip()


def _advance_base(repo: Path, rel: str, contents: str, msg: str) -> None:
    """Simulate base_branch moving on after the PR was cut: commit on `dev`, push."""
    (repo / rel).write_text(contents)
    _git("add", rel, cwd=repo)
    _git("commit", "-m", msg, cwd=repo)
    _git("push", "origin", "dev", cwd=repo)


def _detached_worktree(repo: Path, ref: str, path: Path) -> Path:
    """Mirrors `worktree.detached_worktree` closely enough for these tests: a real detached
    checkout sharing the same object database, which is what `_spawn_judge` hands the judge."""
    _git("worktree", "add", "--detach", str(path), ref, cwd=repo)
    return path


def _wf(repo: Path, tmp_path: Path) -> WorkflowDef:
    return WorkflowDef(
        path=repo / "WORKFLOW.md",
        config={
            "project_key": "TEST",
            "tracker": {"kind": "markdown", "path": "TODO.md"},
            "workspace": {"root": str(tmp_path / "wts"), "base_branch": "dev"},
        },
        prompt_template="",
    )


# --- _refresh_judge_worktree: the pure git mechanics -------------------------------------


def test_a_branch_behind_base_is_merged_up_to_date(tmp_path, repo):
    """The observed-live shape: the PR branch was cut, then base moved. The refresh must
    pull base's new commit into the judge's copy so the baseline reflects current base."""
    _branch_from_head(repo, "pr-1")
    _advance_base(repo, "app.py", "VALUE = 2\n", "fix landed on base after the claim")
    wt = _detached_worktree(repo, "pr-1", tmp_path / "wt")

    detail = dispatcher._refresh_judge_worktree(repo, wt, "dev")

    assert detail == ""
    assert (wt / "app.py").read_text() == "VALUE = 2\n"           # base's fix is now present
    # the merge is a real commit — the tree is clean, exactly what _git_dirty demands
    status = subprocess.run(
        ["git", "-C", str(wt), "status", "--porcelain"], capture_output=True, text=True,
    )
    assert status.stdout.strip() == ""


def test_a_branch_already_current_is_left_alone(tmp_path, repo):
    """Not behind ⇒ no merge attempted at all — nothing to refresh, nothing to risk."""
    _branch_from_head(repo, "pr-1")
    wt = _detached_worktree(repo, "pr-1", tmp_path / "wt")
    before = subprocess.run(
        ["git", "-C", str(wt), "rev-parse", "HEAD"], capture_output=True, text=True,
    ).stdout.strip()

    detail = dispatcher._refresh_judge_worktree(repo, wt, "dev")

    assert detail == ""
    after = subprocess.run(
        ["git", "-C", str(wt), "rev-parse", "HEAD"], capture_output=True, text=True,
    ).stdout.strip()
    assert after == before


def test_a_real_conflict_is_named_not_silently_swallowed_and_leaves_a_clean_tree(tmp_path, repo):
    """⛔ Do NOT relax the judge's red-baseline refusal — when base and the PR branch touch
    the same line differently, refuse LOUDLY, name the cause, and leave the worktree clean
    (no merge-in-progress) rather than judging a tree that does not even resolve."""
    _branch_from_head(repo, "pr-1")
    _git("checkout", "pr-1", cwd=repo)
    (repo / "app.py").write_text("VALUE = 99\n")     # the PR's own conflicting edit
    _git("commit", "-am", "PR changes the same line", cwd=repo)
    _git("checkout", "dev", cwd=repo)
    _advance_base(repo, "app.py", "VALUE = 2\n", "base changes the same line")
    wt = _detached_worktree(repo, "pr-1", tmp_path / "wt")

    detail = dispatcher._refresh_judge_worktree(repo, wt, "dev")

    assert detail != ""
    assert "commit(s) behind" in detail
    assert "origin/dev" in detail
    # ⛔ the merge must have been aborted — a merge left in progress would corrupt every
    # later mutation experiment (the same failure mode judge.py's own restore-or-abandon
    # logic exists to prevent).
    status = subprocess.run(
        ["git", "-C", str(wt), "status", "--porcelain"], capture_output=True, text=True,
    )
    assert "UU" not in status.stdout
    merge_head = subprocess.run(
        ["git", "-C", str(wt), "rev-parse", "-q", "--verify", "MERGE_HEAD"],
        capture_output=True, text=True,
    )
    assert merge_head.returncode != 0                # no merge in progress


def test_concurrent_changelog_entries_merge_without_conflict(tmp_path, repo):
    """⛔ CMX-241. Two branches that each add their own entry to the top of the same
    CHANGELOG.md `## [Unreleased]` section must not conflict when the judge's refresh pulls
    a moved-on base back in — the collision is never semantic (both entries belong), only
    textual. The repo's `.gitattributes` marks `CHANGELOG.md merge=union` for exactly this:
    keep both sides' lines on a conflicting hunk instead of stopping the merge."""
    seed = "# Changelog\n\n## [Unreleased]\n\n### Fixed\n\n- existing entry\n"
    (repo / ".gitattributes").write_text("CHANGELOG.md merge=union\n")
    (repo / "CHANGELOG.md").write_text(seed)
    _git("add", ".gitattributes", "CHANGELOG.md", cwd=repo)
    _git("commit", "-m", "seed changelog", cwd=repo)
    _git("push", "origin", "dev", cwd=repo)

    _branch_from_head(repo, "pr-1")
    _git("checkout", "pr-1", cwd=repo)
    (repo / "CHANGELOG.md").write_text(
        "# Changelog\n\n## [Unreleased]\n\n### Fixed\n\n- entry from this PR\n- existing entry\n"
    )
    _git("commit", "-am", "this PR's own changelog entry", cwd=repo)
    sha = _git("rev-parse", "pr-1", cwd=repo).stdout.strip()
    _git("checkout", "dev", cwd=repo)

    _advance_base(
        repo, "CHANGELOG.md",
        "# Changelog\n\n## [Unreleased]\n\n### Fixed\n\n- entry from a concurrent PR\n- existing entry\n",
        "a concurrent PR's changelog entry lands on dev first",
    )
    wt = _detached_worktree(repo, sha, tmp_path / "wt")

    detail = dispatcher._refresh_judge_worktree(repo, wt, "dev")

    assert detail == ""                                    # no cannot_verify over a changelog line
    merged = (wt / "CHANGELOG.md").read_text()
    assert "entry from this PR" in merged
    assert "entry from a concurrent PR" in merged
    assert "existing entry" in merged


def test_no_origin_remote_degrades_to_a_no_op_never_blocks(tmp_path):
    """Degrades, never blocks: a repo this is not wired to a remote at all (or the fetch
    fails for any reason) must leave the worktree exactly as detached_worktree left it,
    same as before this existed — never a hard failure."""
    solo = tmp_path / "solo"
    solo.mkdir()
    subprocess.run(["git", "init", "-b", "dev", str(solo)], check=True, capture_output=True)
    for k, v in (("user.email", "t@example.com"), ("user.name", "T")):
        subprocess.run(["git", "-C", str(solo), "config", k, v], check=True, capture_output=True)
    (solo / "a.txt").write_text("x\n")
    subprocess.run(["git", "-C", str(solo), "add", "a.txt"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(solo), "commit", "-m", "seed"], check=True, capture_output=True)

    detail = dispatcher._refresh_judge_worktree(solo, tmp_path / "nonexistent-wt", "dev")

    assert detail == ""


# --- wired into _spawn_judge: refresh before the agent ever sees the tree ----------------


def _run_row(conn, repo: Path, sha: str, task_id="abc123"):
    fields = {
        "task_id": task_id, "workflow_path": str(repo / "WORKFLOW.md"), "title": "do a thing",
        "status": "awaiting_review", "branch_name": "pr-1", "task_number": 1,
        "pr_url": "https://github.com/o/r/pull/1", "pr_state": "open",
        "pr_checks": dispatcher.CI_PASSING, "pr_head_sha": sha,
    }
    conn.execute(
        f"INSERT INTO runs ({', '.join(fields)}) VALUES ({', '.join('?' * len(fields))})",
        tuple(fields.values()),
    )
    conn.commit()


@pytest.fixture(autouse=True)
def _own_runs_db(tmp_path, monkeypatch):
    monkeypatch.setattr(dispatcher, "DB_PATH", tmp_path / "scheduler.db")


def test_spawn_judge_refreshes_the_worktree_before_launching_the_agent(tmp_path, repo, monkeypatch):
    """End to end: `_spawn_judge` must call the refresh on the worktree it just created,
    BEFORE the judge agent (and the baseline it will run) ever sees the tree."""
    sha = _branch_from_head(repo, "pr-1")
    _advance_base(repo, "app.py", "VALUE = 2\n", "fix landed on base after the claim")

    monkeypatch.setattr(dispatcher, "_kill_windows_named", lambda *_a, **_k: None)
    monkeypatch.setattr(dispatcher, "_wait_for_ready", lambda *a, **k: True)
    monkeypatch.setattr(dispatcher, "_send_seed", lambda *a, **k: True)

    # ⛔ `dispatcher.subprocess` IS the `subprocess` module (module singletons), so patching
    # `.run` on it is GLOBAL — it would also swallow the real `git worktree add`/fetch/merge
    # calls this test needs to actually happen. Fake tmux only; let every git call through.
    real_run = subprocess.run

    def fake_run(argv, *a, **k):
        from types import SimpleNamespace
        if argv[:1] == ["tmux"]:
            if argv[1:2] == ["new-window"]:
                return SimpleNamespace(stdout="@100\n", returncode=0)
            return SimpleNamespace(stdout="", returncode=0)
        return real_run(argv, *a, **k)
    monkeypatch.setattr(dispatcher.subprocess, "run", fake_run)

    wf = _wf(repo, tmp_path)
    with dispatcher._db() as conn:
        _run_row(conn, repo, sha)
        row = conn.execute("SELECT * FROM runs WHERE task_id=?", ("abc123",)).fetchone()
        assert dispatcher._spawn_judge(wf, row, sha, conn) is True

    worktree = tmp_path / "wts" / "judge-abc123"
    assert (worktree / "app.py").read_text() == "VALUE = 2\n"

    run = dispatcher.resolve_run("abc123")
    assert run["judge_state"] == dispatcher.judge.J_RUNNING     # not knocked to cannot_verify


def test_spawn_judge_refuses_on_a_real_conflict_and_names_it(tmp_path, repo, monkeypatch):
    """A branch that cannot be refreshed automatically must not silently judge a stale (or
    unresolved) tree — it reports CANNOT VERIFY with the staleness named, and spawns no
    agent at all."""
    _branch_from_head(repo, "pr-1")
    _git("checkout", "pr-1", cwd=repo)
    (repo / "app.py").write_text("VALUE = 99\n")
    _git("commit", "-am", "PR changes the same line", cwd=repo)
    _git("checkout", "dev", cwd=repo)
    _advance_base(repo, "app.py", "VALUE = 2\n", "base changes the same line")
    sha = _git("rev-parse", "pr-1", cwd=repo).stdout.strip()

    launched = []
    monkeypatch.setattr(dispatcher, "_launch_agent", lambda *a, **k: launched.append(1))

    wf = _wf(repo, tmp_path)
    with dispatcher._db() as conn:
        _run_row(conn, repo, sha)
        row = conn.execute("SELECT * FROM runs WHERE task_id=?", ("abc123",)).fetchone()
        assert dispatcher._spawn_judge(wf, row, sha, conn) is False

    assert not launched                              # no agent wasted on a doomed tree
    run = dispatcher.resolve_run("abc123")
    assert run["judge_state"] == dispatcher.judge.J_CANNOT_VERIFY
    assert "commit(s) behind" in run["judge_detail"]
