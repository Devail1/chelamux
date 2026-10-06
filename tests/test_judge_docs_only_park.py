"""⚖️🧱 CMX-19 — a cannot_verify no re-run can change parks ONCE, with ONE notice.

Seen 2026-10-07 on CMX-15 (#600, docs/ + changelog only): the judge returned
``run_judge_cannot_verify`` four times in ~3 minutes before the run settled in
``needs_human``, each attempt its own inbox notice. CMX-81's bounded retry re-fires a
same-sha ``cannot_verify`` because it MIGHT be a flake; a docs-only diff is not one. These
tests pin both halves of the fix:

* the dispatcher never spawns a judge on a docs-only PR (``judge.docs_only_diff``, an
  explicit path set) — it parks the run in ``needs_human`` once, atomically;
* a judge that DOES run and reports a final unknown (``Report.cannot_verify_final``) parks
  the same way, and the trigger never re-selects a final unknown on the same commit.

⛔ And that neither is a bypass: code+docs is judged normally, a ``.py`` under ``docs/`` is
code, and a parked run is ``needs_human`` — not mergeable without a human.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from chela import dispatcher, inbox, judge
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
    work = tmp_path / "repo"
    subprocess.run(["git", "clone", str(origin), str(work)], check=True, capture_output=True)
    for k, v in (("user.email", "t@example.com"), ("user.name", "T"), ("commit.gpgsign", "false")):
        _git("config", k, v, cwd=work)
    (work / "app.py").write_text("VALUE = 1\n")
    (work / "docs").mkdir()
    (work / "docs" / "guide.md").write_text("# guide\n")
    _git("add", "app.py", "docs/guide.md", cwd=work)
    _git("commit", "-m", "seed", cwd=work)
    _git("push", "-u", "origin", "dev", cwd=work)
    return work


@pytest.fixture(autouse=True)
def _own_runs_db(tmp_path, monkeypatch):
    monkeypatch.setattr(dispatcher, "DB_PATH", tmp_path / "scheduler.db")


def _pr(repo: Path, files: dict[str, str], *, remove: tuple[str, ...] = ()) -> str:
    """Commit ``files`` on a fresh branch ``pr-1`` cut from ``dev``; return its head sha."""
    _git("checkout", "-b", "pr-1", cwd=repo)
    for rel, body in files.items():
        (repo / rel).parent.mkdir(parents=True, exist_ok=True)
        (repo / rel).write_text(body)
        _git("add", rel, cwd=repo)
    for rel in remove:
        _git("rm", "-q", rel, cwd=repo)
    _git("commit", "-m", "the PR", cwd=repo)
    sha = _git("rev-parse", "HEAD", cwd=repo).stdout.strip()
    _git("checkout", "dev", cwd=repo)
    return sha


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


def _run_row(conn, repo: Path, sha: str, task_id="abc123", **over):
    fields = {
        "task_id": task_id, "workflow_path": str(repo / "WORKFLOW.md"), "title": "do a thing",
        "status": "awaiting_review", "branch_name": "pr-1", "task_number": 1,
        "pr_url": "https://github.com/o/r/pull/1", "pr_state": "open",
        "pr_checks": dispatcher.CI_PASSING, "pr_head_sha": sha,
    }
    fields.update(over)
    conn.execute(
        f"INSERT INTO runs ({', '.join(fields)}) VALUES ({', '.join('?' * len(fields))})",
        tuple(fields.values()),
    )
    conn.commit()


def _spawn(tmp_path, repo, sha, monkeypatch) -> tuple[bool, list]:
    """`_spawn_judge` for real (real git worktree, real diff), with the agent launch faked."""
    launched: list = []
    monkeypatch.setattr(dispatcher, "_launch_agent", lambda *a, **k: launched.append(1))
    wf = _wf(repo, tmp_path)
    with dispatcher._db() as conn:
        _run_row(conn, repo, sha)
        row = conn.execute("SELECT * FROM runs WHERE task_id=?", ("abc123",)).fetchone()
        spawned = dispatcher._spawn_judge(wf, row, sha, conn)
    return spawned, launched


def _notices(samples: int = 3) -> list[dict]:
    """Every inbox event the row produces across ``samples`` consecutive polls."""
    seen: dict[str, str] = {}
    events: list[dict] = []
    for _ in range(samples):
        out, seen = inbox.run_events([dispatcher.resolve_run("abc123")], seen)
        events.extend(out)
    return events


# --- the explicit docs path set -----------------------------------------------------------


@pytest.mark.parametrize("name", [
    "docs/guide.md",
    "docs/deep/nested/page.rst",
    "docs/notes.txt",
    "changelog.d/CMX-19.md",
    "README.md",
    "CONTRIBUTING.md",
    "LICENSE",
])
def test_is_docs_path_true_for_documentation(name):
    assert judge._is_docs_path(name) is True


@pytest.mark.parametrize("name", [
    "docs/helper.py",            # ⛔ the mislabelled-code case: a module under docs/
    "docs/run",                  # an extensionless script under docs/
    "changelog.d/build.sh",
    "app.py",
    "chela/README.md",           # prose INSIDE the package — normal judge path
    "skills/x/SKILL.md",         # agent instructions
    "WORKFLOW.md",               # the dispatcher's own config/prompt
    "CLAUDE.md",
    "requirements.txt",          # a top-level .txt is dependency config, not prose
])
def test_is_docs_path_false_for_anything_that_can_execute_or_instruct(name):
    assert judge._is_docs_path(name) is False


# --- docs_only_diff: the git mechanics ----------------------------------------------------


def _worktree_at(repo: Path, sha: str, tmp_path: Path) -> Path:
    wt = tmp_path / "wt"
    _git("worktree", "add", "--detach", str(wt), sha, cwd=repo)
    return wt


def test_docs_only_diff_true_for_docs_and_changelog(tmp_path, repo):
    sha = _pr(repo, {"docs/guide.md": "# guide\n\nmore\n", "changelog.d/CMX-19.md": "- x\n"})
    assert judge.docs_only_diff(_worktree_at(repo, sha, tmp_path), "dev") is True


def test_docs_only_diff_false_when_code_rides_along(tmp_path, repo):
    sha = _pr(repo, {"docs/guide.md": "# guide\n\nmore\n", "app.py": "VALUE = 2\n"})
    assert judge.docs_only_diff(_worktree_at(repo, sha, tmp_path), "dev") is False


def test_docs_only_diff_false_for_a_py_under_docs(tmp_path, repo):
    sha = _pr(repo, {"docs/helper.py": "VALUE = 3\n"})
    assert judge.docs_only_diff(_worktree_at(repo, sha, tmp_path), "dev") is False


def test_docs_only_diff_sees_the_code_side_of_a_rename(tmp_path, repo):
    """⛔ `app.py` → `docs/app.md` must not hide the code it removed behind the prose path
    it added (plain `--name-only` with rename detection lists only the new name)."""
    _git("checkout", "-b", "pr-1", cwd=repo)
    _git("mv", "app.py", "docs/app.md", cwd=repo)
    _git("commit", "-m", "rename", cwd=repo)
    sha = _git("rev-parse", "HEAD", cwd=repo).stdout.strip()
    _git("checkout", "dev", cwd=repo)
    assert judge.docs_only_diff(_worktree_at(repo, sha, tmp_path), "dev") is False


def test_docs_only_diff_unknown_without_a_resolvable_base(tmp_path, repo):
    sha = _pr(repo, {"docs/guide.md": "# guide\n\nmore\n"})
    assert judge.docs_only_diff(_worktree_at(repo, sha, tmp_path), "nope") is None


# --- _spawn_judge: docs-only parks ONCE, code is judged ----------------------------------


def test_docs_only_pr_parks_once_in_needs_human_with_one_notice(tmp_path, repo, monkeypatch):
    sha = _pr(repo, {"docs/guide.md": "# guide\n\nmore\n", "changelog.d/CMX-19.md": "- x\n"})

    spawned, launched = _spawn(tmp_path, repo, sha, monkeypatch)

    assert spawned is False
    assert not launched, "a docs-only PR must not spawn a judge agent"
    run = dispatcher.resolve_run("abc123")
    assert run["status"] == "needs_human"
    assert run["judge_state"] == judge.J_CANNOT_VERIFY
    assert run["judge_unknown_final"] == 1
    assert "docs-only — nothing to verify" in run["judge_detail"]
    assert "chela merge --override" in run["last_error"]
    assert not (tmp_path / "wts" / "judge-abc123").exists(), "the judge worktree is reaped"

    events = _notices()
    assert [e["kind"] for e in events] == ["run_needs_human"], events

    # ...and the trigger never comes back for it on this commit.
    with dispatcher._db() as conn:
        assert dispatcher._judge_candidates(conn, _wf(repo, tmp_path)) == []


def test_code_plus_docs_pr_is_judged_normally(tmp_path, repo, monkeypatch):
    sha = _pr(repo, {"docs/guide.md": "# guide\n\nmore\n", "app.py": "VALUE = 2\n"})

    spawned, launched = _spawn(tmp_path, repo, sha, monkeypatch)

    assert launched == [1], "code alongside docs must take the normal judge path"
    assert spawned is True
    run = dispatcher.resolve_run("abc123")
    assert run["status"] == "awaiting_review"
    assert run["judge_state"] == judge.J_RUNNING


def test_a_py_under_docs_is_not_treated_as_docs(tmp_path, repo, monkeypatch):
    """⛔ The mislabelled case: a module the package imports, parked under docs/."""
    sha = _pr(repo, {"docs/helper.py": "VALUE = 3\n",
                     "app.py": "from docs.helper import VALUE\n"})

    spawned, launched = _spawn(tmp_path, repo, sha, monkeypatch)

    assert launched == [1] and spawned is True
    assert dispatcher.resolve_run("abc123")["status"] == "awaiting_review"


# --- the loop itself: a final unknown is never re-tried -----------------------------------


def test_trigger_skips_a_final_unknown_but_retries_an_ordinary_one(tmp_path, repo):
    """A `chela reopen` can put a parked run back in `awaiting_review` on the same commit;
    the trigger must not re-judge a final unknown there. Negative control: an ordinary
    (possibly flaky) unknown with budget left IS still retried — CMX-81 is not undone."""
    wf = _wf(repo, tmp_path)
    common = dict(judge_state=judge.J_CANNOT_VERIFY, judge_cannot_verify_tries=0)
    with dispatcher._db() as conn:
        _run_row(conn, repo, "s1", task_id="final", judge_sha="s1", judge_unknown_final=1,
                 **common)
        _run_row(conn, repo, "s2", task_id="flaky", judge_sha="s2", judge_unknown_final=0,
                 **common)
        picked = [r["task_id"] for r in dispatcher._judge_candidates(conn, wf)]
    assert picked == ["flaky"]


def test_a_new_head_on_a_parked_run_is_judged_afresh(tmp_path, repo):
    wf = _wf(repo, tmp_path)
    with dispatcher._db() as conn:
        _run_row(conn, repo, "new-sha", judge_sha="old-sha", judge_unknown_final=1,
                 judge_state=judge.J_CANNOT_VERIFY)
        assert [r["task_id"] for r in dispatcher._judge_candidates(conn, wf)] == ["abc123"]


def test_spawn_judge_clears_the_final_flag(tmp_path, repo, monkeypatch):
    sha = _pr(repo, {"app.py": "VALUE = 2\n"})
    monkeypatch.setattr(dispatcher, "_launch_agent", lambda *a, **k: None)
    with dispatcher._db() as conn:
        _run_row(conn, repo, sha, judge_sha="old", judge_unknown_final=1)
        row = conn.execute("SELECT * FROM runs WHERE task_id='abc123'").fetchone()
        assert dispatcher._spawn_judge(_wf(repo, tmp_path), row, sha, conn) is True
    assert dispatcher.resolve_run("abc123")["judge_unknown_final"] == 0


def test_park_final_unknown_never_resurrects_a_run_that_moved_on(tmp_path, repo):
    with dispatcher._db() as conn:
        _run_row(conn, repo, "s1", status="done", pr_state="merged")
    assert dispatcher.park_final_unknown("abc123", "nothing to verify") is False
    run = dispatcher.resolve_run("abc123")
    assert run["status"] == "done"
    assert run["judge_state"] == judge.J_CANNOT_VERIFY


def test_an_ordinary_cannot_verify_still_announces_without_parking(tmp_path, repo):
    """Negative control for the notice: a retryable unknown stays `awaiting_review` and is
    announced as `run_judge_cannot_verify`, exactly as before."""
    with dispatcher._db() as conn:
        _run_row(conn, repo, "s1")
    dispatcher.set_judge_state("abc123", judge.J_CANNOT_VERIFY, "a flake")
    run = dispatcher.resolve_run("abc123")
    assert run["status"] == "awaiting_review" and not run["judge_unknown_final"]
    assert [e["kind"] for e in _notices()] == ["run_judge_cannot_verify"]


# --- the judge that DID run: Report.cannot_verify_final → park, not retry ----------------


TEST_CMD = f'"{sys.executable}" -m pytest -q'


def test_zero_experiments_on_docs_only_is_final_but_on_code_is_not(tmp_path, repo):
    sha = _pr(repo, {"docs/guide.md": "# guide\n\nmore\n"})
    wt = _worktree_at(repo, sha, tmp_path)
    report = judge.run_experiments(wt, TEST_CMD, {"experiments": []}, timeout=60,
                                   base_branch="dev")
    assert report.cannot_verify and report.cannot_verify_final is True

    # Negative control: a code PR with no experiments may get some on a re-run — retryable.
    _git("branch", "-D", "pr-1", cwd=repo)
    sha2 = _pr(repo, {"app.py": "VALUE = 2\n"})
    wt2 = tmp_path / "wt2"
    _git("worktree", "add", "--detach", str(wt2), sha2, cwd=repo)
    report2 = judge.run_experiments(wt2, TEST_CMD, {"experiments": []}, timeout=60,
                                    base_branch="dev")
    assert report2.cannot_verify and report2.cannot_verify_final is False


@pytest.mark.parametrize("final, status, kinds", [
    (True, "needs_human", ["run_needs_human"]),
    (False, "awaiting_review", ["run_judge_cannot_verify"]),     # negative control
])
def test_judge_run_parks_a_final_unknown_and_only_a_final_one(
    tmp_path, monkeypatch, final, status, kinds,
):
    from tests.test_judge import REAL_GUARD_TEST, _git_workflow_repo
    from tests.test_judge import _run_row as judge_run_row

    monkeypatch.setattr(dispatcher, "_kill_windows_named", lambda name: None)
    repo, head_sha = _git_workflow_repo(tmp_path, "abc123", REAL_GUARD_TEST)
    with dispatcher._db() as conn:
        judge_run_row(conn, repo, "abc123", pr_head_sha=head_sha)
    monkeypatch.setattr(
        judge, "run_experiments",
        lambda *a, **k: judge.Report(cannot_verify="nothing to verify",
                                     cannot_verify_final=final),
    )
    monkeypatch.setattr(dispatcher, "pr_live_head_sha", lambda *a, **k: None)
    exp = tmp_path / "experiments.json"
    exp.write_text(json.dumps({"experiments": []}))
    with patch.object(dispatcher, "_post_pr_comment", side_effect=lambda u, d, b: (True, "")):
        judge.judge_run("abc123", exp, cleanup=False)

    run = dispatcher.resolve_run("abc123")
    assert run["status"] == status
    assert bool(run["judge_unknown_final"]) is final
    assert [e["kind"] for e in _notices()] == kinds


def test_no_test_cmd_is_a_final_unknown(tmp_path, monkeypatch):
    """No `judge.test_cmd` on the workflow: no re-run of this commit can find a suite."""
    from tests.test_judge import REAL_GUARD_TEST, _git_workflow_repo
    from tests.test_judge import _run_row as judge_run_row

    monkeypatch.setattr(dispatcher, "_kill_windows_named", lambda name: None)
    repo, head_sha = _git_workflow_repo(tmp_path, "abc123", REAL_GUARD_TEST)
    wf_md = repo / "WORKFLOW.md"
    wf_md.write_text("\n".join(line for line in wf_md.read_text().splitlines()
                               if "test_cmd" not in line) + "\n")
    with dispatcher._db() as conn:
        judge_run_row(conn, repo, "abc123", pr_head_sha=head_sha)
    monkeypatch.setattr(dispatcher, "pr_live_head_sha", lambda *a, **k: None)
    exp = tmp_path / "experiments.json"
    exp.write_text(json.dumps({"experiments": []}))
    with patch.object(dispatcher, "_post_pr_comment", side_effect=lambda u, d, b: (True, "")):
        judge.judge_run("abc123", exp, cleanup=False)

    run = dispatcher.resolve_run("abc123")
    assert "test_cmd" in run["judge_detail"]
    assert run["status"] == "needs_human" and run["judge_unknown_final"] == 1
