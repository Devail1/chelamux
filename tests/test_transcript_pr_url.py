"""🧯 CMX-391 — a test fixture's fake PR, read out of the agent's transcript, closed a live run.

On 2026-09-29 CMX-389's run row flipped to `pr_url=https://github.com/o/r/pull/5`, the daemon
saw that URL "merged", marked the run done, struck its task and deleted its worktree. The real
PR was a different one on the run's own repo.

Nothing in the suite wrote that row. Re-running the whole suite against a seeded temp
`CHELA_DIR` with the agent's `CHELA_WID` left the row byte-identical. The writer was the
daemon's `mark_awaiting_review`. It reads the latest `pr-link` record from the agent's
transcript, and Claude Code writes one whenever a PR URL shows up in the session. CMX-389's
transcript held 23 `pr-link` records for `o/r/pull/5`, echoed by its own smoke tests and
pytest runs of `tests/test_mergegate.py`, and no real one: the real PR is opened
daemon-side by `request-push`, so the agent's session never sees it. The transcript value
then OUTRANKED the URL already on the row.

These tests drive the real transcript reader over a JSONL file holding that exact record.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from chela import dispatcher, transcripts

from tests.test_dispatcher_rework import _row

REAL_PR = "https://github.com/acme/widgets/pull/541"
FAKE_PR = "https://github.com/o/r/pull/5"


@pytest.fixture(autouse=True)
def _own_runs_db(tmp_path, monkeypatch):
    monkeypatch.setattr(dispatcher, "DB_PATH", tmp_path / "scheduler.db")


@pytest.fixture
def repo(tmp_path) -> Path:
    """The run's repo: a `git init` whose only remote is `acme/widgets` (never contacted)."""
    path = tmp_path / "repo"
    path.mkdir()
    subprocess.run(["git", "init", "-q", str(path)], check=True)
    subprocess.run(["git", "-C", str(path), "remote", "add", "origin",
                    "git@github.com:acme/widgets.git"], check=True)
    (path / "WORKFLOW.md").write_text("---\nproject_key: T\n---\n")
    return path


def _transcript(tmp_path: Path, *urls: str) -> Path:
    """A transcript whose `pr-link` records are the shape Claude Code writes."""
    path = tmp_path / "session.jsonl"
    lines = []
    for url in urls:
        owner, name, _, number = url.split("github.com/")[1].split("/")
        lines.append(json.dumps({"type": "pr-link", "sessionId": "s1", "prNumber": int(number),
                                 "prUrl": url, "prRepository": f"{owner}/{name}",
                                 "timestamp": "2026-09-28T21:52:05.280Z"}))
    path.write_text("\n".join(lines) + "\n")
    return path


def _finish(tmp_path, repo, *, row_pr_url, transcript_urls) -> tuple[dict, list]:
    """Seed a running row, point the transcript reader at a real JSONL, run the daemon's
    completion hop. Returns the row after, and every URL the hop asked GitHub about."""
    with dispatcher._db() as conn:
        _row(conn, task_id="t1", status="running", workflow_path=str(repo / "WORKFLOW.md"),
             window_name="cmx-389", pr_url=row_pr_url, pr_state=None)
    asked: list = []

    def fake_status(url, repo_dir):
        asked.append(url)
        return ("merged" if url == FAKE_PR else "open"), None

    with patch.object(transcripts, "_resolve_agent_transcript",
                      return_value=_transcript(tmp_path, *transcript_urls)), \
         patch.object(dispatcher, "_read_pr_status", side_effect=fake_status), \
         patch.object(dispatcher, "_kill_window"):
        result = dispatcher.mark_awaiting_review("t1")
    assert result["ok"], result
    return dispatcher.resolve_run("t1"), asked


def test_the_incident_a_fixture_pr_in_the_transcript_never_replaces_the_recorded_one(
        tmp_path, repo):
    """🔴 GUARD — the incident, replayed: the row already carries the real PR (recorded by
    `request-push`), the transcript's latest `pr-link` is the fixture's `o/r/pull/5`.
    Before the fix the row came out with `o/r/pull/5` and `pr_state=merged` — the state the
    daemon then closed the run from. Corrupt by letting the transcript win again
    (`pr_url or row["pr_url"]`) ⇒ RED."""
    row, asked = _finish(tmp_path, repo, row_pr_url=REAL_PR, transcript_urls=[FAKE_PR])
    assert row["pr_url"] == REAL_PR
    assert row["pr_state"] == "open"
    assert FAKE_PR not in asked


def test_the_recorded_pr_outranks_even_a_same_repo_transcript_pr(tmp_path, repo):
    """🔴 GUARD — the ORDER, on its own: a session can see another PR of its own repo (it
    read one, a smoke test printed one), and that is no more its PR than a fixture's.
    The URL on the row was recorded daemon-side from the PR chela itself opened. Corrupt
    by letting the transcript win (`_read_pr_url(...) or row["pr_url"]`) ⇒ RED — the slug
    check cannot mask it here, the URL is in-repo."""
    other = "https://github.com/acme/widgets/pull/7"
    row, asked = _finish(tmp_path, repo, row_pr_url=REAL_PR, transcript_urls=[other])
    assert row["pr_url"] == REAL_PR
    assert asked == [REAL_PR]


def test_a_pr_from_another_repo_is_not_adopted_even_when_the_row_has_none(tmp_path, repo):
    """🔴 GUARD — the other ordering: the completion marker is applied before the push
    marker, so the row has no PR yet and the transcript is the only source. A PR outside
    the run repo's own remotes is still never this run's PR. Corrupt the slug check
    (accept any URL) ⇒ RED."""
    row, asked = _finish(tmp_path, repo, row_pr_url=None, transcript_urls=[FAKE_PR])
    assert row["pr_url"] is None
    assert FAKE_PR not in asked


def test_a_pr_of_the_runs_own_repo_is_still_read_from_the_transcript(tmp_path, repo):
    """⭐ The accept case — a fence that drops every transcript URL is broken too: an agent
    that opened its own PR (pre-`request-push` flows, a human-driven session) still gets
    it recorded. Case-insensitive, as GitHub slugs are."""
    url = "https://github.com/Acme/Widgets/pull/7"
    row, asked = _finish(tmp_path, repo, row_pr_url=None, transcript_urls=[FAKE_PR, url])
    assert row["pr_url"] == url
    assert asked == [url]


def test_no_repo_means_no_transcript_pr(tmp_path):
    """No repo dir, or one with no GitHub remote, cannot vouch for any URL — fail closed."""
    assert not dispatcher._pr_url_in_repo(REAL_PR, None)
    bare = tmp_path / "bare"
    bare.mkdir()
    subprocess.run(["git", "init", "-q", str(bare)], check=True)
    assert not dispatcher._pr_url_in_repo(REAL_PR, str(bare))
    assert not dispatcher._pr_url_in_repo("not a url", str(bare))


def _gh_like(local_slug: str, merged: set[str]):
    """`gh pr view N [--repo o/r] --json …`, with gh's own resolution: no `--repo` means the
    cwd's repo; a repo that does not exist is an error."""
    asked: list[list[str]] = []

    def run(argv, **kw):
        asked.append(list(argv))
        repo = argv[argv.index("--repo") + 1] if "--repo" in argv else local_slug

        class R:
            returncode = 0
            stderr = ""
            stdout = ""

        if repo != local_slug:
            R.returncode = 1
            R.stderr = f"Could not resolve to a Repository with the name '{repo}'."
        else:
            R.stdout = json.dumps({"state": "MERGED" if f"{repo}#{argv[3]}" in merged
                                   else "OPEN", "mergeable": "UNKNOWN"})
        return R()

    return run, asked


def test_pr_status_asks_about_the_repo_the_url_names(tmp_path, repo, monkeypatch):
    """🔴 GUARD — the second half of the incident. `o/r/pull/5` was read as merged because
    `gh pr view 5` resolves the NUMBER in the cwd's repo, and that repo's own PR #5 was
    merged long ago. Corrupt by dropping `--repo` ⇒ the fake PR reads `merged` ⇒ RED."""
    run, asked = _gh_like("acme/widgets", merged={"acme/widgets#5"})
    monkeypatch.setattr(dispatcher.subprocess, "run", run)
    assert dispatcher._read_pr_status(FAKE_PR, str(repo)) == (None, None)
    assert asked[0][asked[0].index("--repo") + 1] == "o/r"


def test_pr_status_still_reads_the_runs_own_pr(tmp_path, repo, monkeypatch):
    """⭐ The accept case: the run's own PR, in its own repo, still reads through."""
    run, _ = _gh_like("acme/widgets", merged={"acme/widgets#541"})
    monkeypatch.setattr(dispatcher.subprocess, "run", run)
    assert dispatcher._read_pr_status(REAL_PR, str(repo)) == ("merged", "UNKNOWN")
