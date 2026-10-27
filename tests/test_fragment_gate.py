"""📝 CMX-356 — task-finished refuses a bare ``changelog.d/CMX-<id>.md`` whose id already
shipped (issue numbering restarted, so a new task reuses an old, released number)."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from chela import dispatcher
from tests.test_judge_sweep import _run_row, _task_finished

REAL_FRAGMENT_GATE = True

RELEASED = ("# Changelog\n\n## [Unreleased]\n\n- pending (CMX-700)\n\n"
            "## [0.8.0] — 2026-08-20\n\n### Added\n\n- **Old keeper.** shipped (CMX-346, #12)\n")
SHIPPED_BULLET = "- **Old keeper.** shipped (CMX-346, #12)"
HINT = ("changelog.d/CMX-346.md: an older, released task has the same number — rename it to "
        "`changelog.d/CMX-<id>-<slug>.md` (slug from your branch name), e.g. "
        "`git mv changelog.d/CMX-346.md changelog.d/CMX-346-<slug>.md`.")
SHIPPED = ("{}: already published in a dated CHANGELOG.md section — back-merge `main` so the "
           "release's fragment deletions arrive, or delete it if you are certain it shipped. "
           "Do not rename it: a renamed copy still republishes.")


def _wt(tmp_path, monkeypatch, fragments: tuple[str, ...], changelog: str | None = RELEASED,
        shipped: tuple[str, ...] = ()) -> Path:
    monkeypatch.setattr(dispatcher, "DB_PATH", tmp_path / "scheduler.db")
    wt = tmp_path / "wt"
    (wt / "changelog.d").mkdir(parents=True)
    (wt / "changelog.d" / "README.md").write_text("# Changelog fragments\n")
    for name in fragments:
        (wt / "changelog.d" / name).write_text("### Added\n\n- **New.** text (CMX-346)\n")
    for name in shipped:                # a consumed fragment a skipped back-merge kept alive
        (wt / "changelog.d" / name).write_text(f"### Added\n\n  {SHIPPED_BULLET}\n")
    if changelog is not None:
        (wt / "CHANGELOG.md").write_text(changelog)
    _run_row(tmp_path, wt)
    return wt


def test_a_bare_fragment_with_a_released_id_is_refused_with_a_rename_hint(tmp_path, monkeypatch):
    _wt(tmp_path, monkeypatch, ("CMX-346.md",))
    assert dispatcher.check_changelog_fragments("cmx-88") == {"ok": False, "error": HINT}


@pytest.mark.parametrize("fragments", [
    ("CMX-346-keeper-digest.md",),      # slugged: the released id no longer matches
    ("CMX-701.md",),                    # bare, but its id never shipped
    ("CMX-700.md",),                    # only in [Unreleased], which is pending, not shipped
    ()])
def test_an_unreleased_or_slugged_fragment_is_accepted(tmp_path, monkeypatch, fragments):
    _wt(tmp_path, monkeypatch, fragments)
    assert dispatcher.check_changelog_fragments("cmx-88") == {"ok": True}


def test_every_colliding_fragment_is_named(tmp_path, monkeypatch):
    _wt(tmp_path, monkeypatch, ("CMX-12.md", "CMX-346.md", "CMX-346-ok.md"),
        RELEASED.replace("(CMX-346, #12)", "(CMX-346, #12) (CMX-12)"))
    error = dispatcher.check_changelog_fragments("cmx-88")["error"]
    assert error.startswith("changelog.d/CMX-12.md, changelog.d/CMX-346.md: an older")
    assert "`git mv changelog.d/CMX-12.md changelog.d/CMX-12-<slug>.md`" in error


@pytest.mark.parametrize("name", ["CMX-346.md", "CMX-346-keeper.md", "notes.md"])
def test_a_fragment_whose_text_shipped_is_told_to_go_not_to_rename(tmp_path, monkeypatch, name):
    """A bare OR slugged fragment a release already consumed: renaming would hide it."""
    _wt(tmp_path, monkeypatch, (), shipped=(name,))
    assert dispatcher.check_changelog_fragments("cmx-88") == {
        "ok": False, "error": SHIPPED.format(f"changelog.d/{name}")}


def test_both_kinds_get_their_own_fix(tmp_path, monkeypatch):
    _wt(tmp_path, monkeypatch, ("CMX-346.md",), shipped=("CMX-5-old.md", "CMX-6-old.md"))
    assert dispatcher.check_changelog_fragments("cmx-88")["error"] == (
        SHIPPED.format("changelog.d/CMX-5-old.md, changelog.d/CMX-6-old.md") + " " + HINT)


def test_a_shipped_bullet_only_counts_below_the_first_dated_heading(tmp_path, monkeypatch):
    _wt(tmp_path, monkeypatch, (), shipped=("CMX-5-new.md",),
        changelog=f"# Changelog\n\n## [Unreleased]\n\n{SHIPPED_BULLET}\n")
    assert dispatcher.check_changelog_fragments("cmx-88") == {"ok": True}


def test_a_repo_without_a_changelog_has_nothing_released(tmp_path, monkeypatch):
    _wt(tmp_path, monkeypatch, ("CMX-346.md",), changelog=None)
    assert dispatcher.check_changelog_fragments("cmx-88") == {"ok": True}


def test_the_worktree_comes_from_the_run_record_not_the_cwd(tmp_path, monkeypatch):
    _wt(tmp_path, monkeypatch, ("CMX-346.md",))
    monkeypatch.chdir(tmp_path)
    assert dispatcher.check_changelog_fragments("cmx-88")["ok"] is False
    assert dispatcher.check_changelog_fragments("cmx-1") == {
        "ok": False, "error": "no run with a worktree on record for cmx-1"}


@pytest.mark.parametrize("break_it", ["dir", "bytes"])
def test_an_unreadable_changelog_fails_closed(tmp_path, monkeypatch, break_it):
    wt = _wt(tmp_path, monkeypatch, ("CMX-701.md",), changelog=None)
    if break_it == "dir":                       # IsADirectoryError — an OSError subclass
        (wt / "CHANGELOG.md").mkdir()
    else:                                       # UnicodeDecodeError — a ValueError subclass
        (wt / "CHANGELOG.md").write_bytes(b"## [0.8.0] \xff\xfe (CMX-701)\n")
    out = dispatcher.check_changelog_fragments("cmx-88")
    assert out["ok"] is False
    assert out["error"].startswith("could not read this branch's changelog — ")
    assert out["error"].endswith(". Unknown is not checked.")


def test_cli_refuses_before_any_other_gate_or_suite(tmp_path, monkeypatch, capsys):
    _wt(tmp_path, monkeypatch, ("CMX-346.md",))
    with patch.object(dispatcher, "check_hand_in_size") as size, \
            patch.object(dispatcher, "verify_sweep") as swept, \
            patch.object(dispatcher, "request_task_finished") as req:
        assert _task_finished() == 1
    assert f"task-finished: 📝 {HINT}\n" in capsys.readouterr().out
    assert not size.called and not swept.called and not req.called


def test_cli_lets_a_slugged_fragment_through(tmp_path, monkeypatch, capsys):
    _wt(tmp_path, monkeypatch, ("CMX-346-keeper.md",))
    with patch.object(dispatcher, "check_hand_in_size",
                      return_value={"ok": False, "error": "size-gate-reached"}):
        assert _task_finished() == 1
    out = capsys.readouterr().out
    assert "size-gate-reached" in out and "📝" not in out


def test_a_fragment_without_a_bullet_is_never_called_shipped(tmp_path, monkeypatch):
    wt = _wt(tmp_path, monkeypatch, ())
    (wt / "changelog.d" / "CMX-5-x.md").write_text("### Added\n")      # in the dated text too
    assert dispatcher.check_changelog_fragments("cmx-88") == {"ok": True}


@pytest.mark.parametrize("changelog", [RELEASED, None, "# Changelog\n\n## [Unreleased]\n"])
def test_an_unreadable_fragment_fails_closed_even_before_any_release(tmp_path, monkeypatch,
                                                                     changelog):
    wt = _wt(tmp_path, monkeypatch, (), changelog=changelog)
    (wt / "changelog.d" / "CMX-5-x.md").write_bytes(b"### Added\n\n- \xff\xfe bad\n")
    out = dispatcher.check_changelog_fragments("cmx-88")
    assert out["ok"] is False
    assert out["error"].startswith("could not read this branch's changelog — ")
