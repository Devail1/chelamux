"""``/api/agents/<wid>/diff`` + ``/api/agents/<wid>/diff/patch`` — the per-session
CHANGED-FILES / DIFF surface's HTTP half (CMX-299).

The git plumbing itself (chela.diffsurface) is exercised against real repos in
tests/test_diffsurface.py; this file is only about the route layer — resolving
``wid`` to a live window's cwd, 404ing an unknown wid, and passing the query
param through — so window discovery is mocked and diffsurface's real functions
run against a real tmp repo underneath.
"""
from __future__ import annotations

import subprocess
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

import pytest

from chela.dashboard import app as dash

WINDOWS_BY_ID = {"@9": "cmx-76", "@10": "shell-1"}


@pytest.fixture
def client():
    return dash.app.test_client()


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    repo_path = tmp_path / "repo"
    subprocess.run(["git", "init", "-q", "-b", "main", str(repo_path)], check=True, capture_output=True)
    for k, v in (("user.email", "t@example.com"), ("user.name", "T"), ("commit.gpgsign", "false")):
        subprocess.run(["git", "-C", str(repo_path), "config", k, v], check=True, capture_output=True)
    (repo_path / "tracked.txt").write_text("one\ntwo\n")
    subprocess.run(["git", "-C", str(repo_path), "add", "tracked.txt"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(repo_path), "commit", "-q", "-m", "seed"], check=True, capture_output=True)
    return repo_path


@contextmanager
def _windows(cwd_by_id: dict):
    with (
        patch("chela.discovery.get_windows_by_id", return_value=dict(WINDOWS_BY_ID)),
        patch("chela.discovery.get_window_cwd_by_id", side_effect=lambda wid: cwd_by_id.get(wid)),
    ):
        yield


def test_diff_unknown_wid_404s(client):
    # 🔴 GUARD: dropping the `wid not in get_windows_by_id()` check would let a
    # caller shell out `git diff` against a cwd of their choosing for ANY wid,
    # live window or not.
    with _windows({}):
        resp = client.get("/api/agents/@999/diff")
    assert resp.status_code == 404


def test_diff_patch_unknown_wid_404s(client):
    with _windows({}):
        resp = client.get("/api/agents/@999/diff/patch?path=x.txt")
    assert resp.status_code == 404


def test_diff_patch_missing_path_400s(client, repo: Path):
    with _windows({"@9": str(repo)}):
        resp = client.get("/api/agents/@9/diff/patch")
    assert resp.status_code == 400


def test_diff_reports_a_real_change_in_the_window_s_cwd(client, repo: Path):
    (repo / "tracked.txt").write_text("one\ntwo\nthree\n")
    with _windows({"@9": str(repo)}):
        resp = client.get("/api/agents/@9/diff")
    body = resp.get_json()
    assert body["is_git"] is True
    assert [f["path"] for f in body["files"]] == ["tracked.txt"]
    assert body["files"][0]["additions"] == 1


def test_diff_patch_returns_the_file_s_unified_diff(client, repo: Path):
    (repo / "tracked.txt").write_text("one\ntwo\nthree\n")
    with _windows({"@9": str(repo)}):
        resp = client.get("/api/agents/@9/diff/patch?path=tracked.txt")
    body = resp.get_json()
    assert body["ok"] is True
    assert "+three" in body["patch"]


def test_diff_no_resolvable_cwd_degrades_to_empty(client):
    # A window whose pane cwd can't be read (race between discovery and the
    # window closing) — no 500, just the same empty shape as "not a git repo".
    with _windows({"@9": None}):
        resp = client.get("/api/agents/@9/diff")
    assert resp.get_json() == {"is_git": False, "has_head": False, "files": [], "additions": 0, "deletions": 0}
