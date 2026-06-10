"""Tests for the launcher store (Recent + Favorites click-to-launch state).

Exercises the MRU recency cap + dedup, favorite pin/unpin idempotency, the
favorited-excluded-from-recent view rule, path normalisation as the identity
key, and the git-repo suggestion scan — all against a temp CHELA_DIR so no real
~/.chela state is touched.
"""
from __future__ import annotations

import importlib
import os

import pytest


@pytest.fixture()
def launcher(tmp_path, monkeypatch):
    """Reload the launcher module with CHELA_DIR pointed at a temp dir, so its
    module-level _STORE path picks up the override."""
    monkeypatch.setenv("CHELA_DIR", str(tmp_path / "chela"))
    monkeypatch.setenv("CHELA_PROJECTS_DIR", str(tmp_path / "projects"))
    import chela.config as config
    importlib.reload(config)
    import chela.launcher as launcher_mod
    importlib.reload(launcher_mod)
    return launcher_mod


def test_recent_is_mru_deduped_and_capped(launcher, tmp_path):
    dirs = []
    for i in range(15):
        d = tmp_path / f"d{i}"
        d.mkdir()
        dirs.append(str(d))
        launcher.record_recent(str(d))

    recent = launcher.view()["recent"]
    assert len(recent) == 12                      # capped at _MAX_RECENT
    assert recent[0]["path"] == launcher._norm(dirs[-1])   # newest first

    # Re-launching an older dir moves it to the front without duplicating.
    launcher.record_recent(dirs[5])
    recent = launcher.view()["recent"]
    assert recent[0]["path"] == launcher._norm(dirs[5])
    assert sum(1 for e in recent if e["path"] == launcher._norm(dirs[5])) == 1


def test_pin_is_idempotent_and_unpin_removes(launcher, tmp_path):
    d = tmp_path / "proj"
    d.mkdir()
    launcher.pin(str(d))
    launcher.pin(str(d))                          # second pin must not duplicate
    favs = launcher.view()["favorites"]
    assert len(favs) == 1
    assert favs[0]["label"] == "proj"             # label defaults to basename

    launcher.unpin(str(d))
    assert launcher.view()["favorites"] == []


def test_forget_recent_removes_one_entry(launcher, tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    a.mkdir(); b.mkdir()
    launcher.record_recent(str(a))
    launcher.record_recent(str(b))
    launcher.forget_recent(str(a))
    paths = [e["path"] for e in launcher.view()["recent"]]
    assert launcher._norm(str(a)) not in paths
    assert launcher._norm(str(b)) in paths


def test_favorited_dir_excluded_from_recent(launcher, tmp_path):
    d = tmp_path / "shared"
    d.mkdir()
    launcher.record_recent(str(d))
    assert any(e["path"] == launcher._norm(str(d)) for e in launcher.view()["recent"])

    launcher.pin(str(d))
    view = launcher.view()
    assert any(e["path"] == launcher._norm(str(d)) for e in view["favorites"])
    assert not any(e["path"] == launcher._norm(str(d)) for e in view["recent"])


def test_view_flags_missing_dirs(launcher, tmp_path):
    gone = tmp_path / "gone"
    gone.mkdir()
    launcher.pin(str(gone))
    os.rmdir(gone)
    fav = launcher.view()["favorites"][0]
    assert fav["exists"] is False


def test_path_normalisation_collapses_identity(launcher, tmp_path):
    d = tmp_path / "repo"
    d.mkdir()
    launcher.record_recent(str(d))
    launcher.record_recent(str(d) + "/")          # trailing slash → same dir
    launcher.record_recent(str(d / "sub" / ".."))  # ..-relative → same dir
    assert len(launcher.view()["recent"]) == 1


def test_suggest_scans_git_repos(launcher, tmp_path):
    base = tmp_path / "projects"
    (base / "a").mkdir(parents=True)
    (base / "a" / ".git").mkdir()                 # a git repo → suggested
    (base / "b").mkdir()                          # plain dir → skipped
    (base / "c").mkdir()
    (base / "c" / ".git").mkdir()
    launcher.pin(str(base / "c"))                 # already pinned → flagged

    sug = {s["label"]: s for s in launcher.suggest()}
    assert set(sug) == {"a", "c"}                 # only git repos
    assert sug["a"]["pinned"] is False
    assert sug["c"]["pinned"] is True


def test_suggest_empty_when_base_absent(launcher):
    assert launcher.suggest() == []               # no CHELA_PROJECTS_DIR on disk
