"""chela.diffsurface — the per-session CHANGED-FILES / DIFF surface (CMX-299).

Real git repos, real `git diff`/`git status` subprocess calls (same style as
tests/test_worktree.py) — this module IS the git plumbing, so a mock would just
re-assert the mock's own behaviour instead of testing anything real.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from chela import diffsurface


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    repo_path = tmp_path / "repo"
    subprocess.run(["git", "init", "-q", "-b", "main", str(repo_path)], check=True, capture_output=True)
    for k, v in (("user.email", "t@example.com"), ("user.name", "T"), ("commit.gpgsign", "false")):
        _git(repo_path, "config", k, v)
    (repo_path / "tracked.txt").write_text("one\ntwo\nthree\n")
    (repo_path / "to_delete.txt").write_text("bye\n")
    _git(repo_path, "add", "tracked.txt", "to_delete.txt")
    _git(repo_path, "commit", "-q", "-m", "seed")
    return repo_path


# --- is_git_repo / not-a-repo degradation -----------------------------------

def test_changed_files_not_a_directory(tmp_path: Path):
    result = diffsurface.changed_files(tmp_path / "does-not-exist")
    assert result == {"is_git": False, "has_head": False, "files": [], "additions": 0, "deletions": 0}


def test_changed_files_plain_directory_not_a_repo(tmp_path: Path):
    plain = tmp_path / "plain"
    plain.mkdir()
    result = diffsurface.changed_files(plain)
    assert result["is_git"] is False
    assert result["files"] == []


def test_changed_files_repo_with_no_commits(tmp_path: Path):
    empty_repo = tmp_path / "empty"
    subprocess.run(["git", "init", "-q", str(empty_repo)], check=True, capture_output=True)
    result = diffsurface.changed_files(empty_repo)
    assert result["is_git"] is True
    assert result["has_head"] is False
    assert result["files"] == []


def test_changed_files_bare_repo_is_not_a_work_tree(tmp_path: Path):
    # 🔴 GUARD: `git rev-parse --is-inside-work-tree` EXITS 0 in a bare repo
    # (verified: `git -C <bare.git> rev-parse --is-inside-work-tree` prints
    # "false" and returns 0) — is_git_repo must read that printed answer, not
    # just the exit code, or a bare repo (or any cwd inside a `.git` dir)
    # would report is_git=True with an empty file list instead of is_git=False,
    # which is exactly the "No changes" mis-report diffpanelmodel.js's
    # summaryLabel comment says must never happen for a non-repo cwd.
    bare = tmp_path / "bare.git"
    subprocess.run(["git", "init", "-q", "--bare", str(bare)], check=True, capture_output=True)
    assert diffsurface.is_git_repo(bare) is False
    result = diffsurface.changed_files(bare)
    assert result == {"is_git": False, "has_head": False, "files": [], "additions": 0, "deletions": 0}


# --- the merged file list ----------------------------------------------------

def test_changed_files_clean_worktree(repo: Path):
    result = diffsurface.changed_files(repo)
    assert result == {"is_git": True, "has_head": True, "files": [], "additions": 0, "deletions": 0}


def test_changed_files_reports_modified_added_deleted_and_untracked(repo: Path):
    # 🔴 GUARD: this is the actual union changed_files exists to build — drop any
    # one of the four git calls it makes (name-status, numstat, ls-files) and one
    # of these four rows silently disappears from the result.
    (repo / "tracked.txt").write_text("one\ntwo\nthree\nfour\n")   # modified: +1 line
    (repo / "to_delete.txt").unlink()                               # deleted
    (repo / "new_tracked.txt").write_text("brand new\n")
    _git(repo, "add", "new_tracked.txt")                            # staged add
    (repo / "scratch.txt").write_text("a\nb\n")                     # untracked, never added

    result = diffsurface.changed_files(repo)
    by_path = {f["path"]: f for f in result["files"]}

    assert by_path["tracked.txt"]["status"] == "modified"
    assert by_path["tracked.txt"]["additions"] == 1
    assert by_path["tracked.txt"]["deletions"] == 0

    assert by_path["to_delete.txt"]["status"] == "deleted"
    assert by_path["to_delete.txt"]["deletions"] == 1

    assert by_path["new_tracked.txt"]["status"] == "added"
    assert by_path["new_tracked.txt"]["additions"] == 1

    assert by_path["scratch.txt"]["status"] == "untracked"
    assert by_path["scratch.txt"]["additions"] == 2  # best-effort line count

    assert set(by_path) == {"tracked.txt", "to_delete.txt", "new_tracked.txt", "scratch.txt"}
    assert result["additions"] == sum(f["additions"] for f in result["files"])
    assert result["deletions"] == sum(f["deletions"] for f in result["files"])


def test_changed_files_type_change_reports_as_modified(repo: Path):
    # 🔴 GUARD: `_STATUS_NAMES["T"]` maps git's type-change code (file -> symlink,
    # or vice versa) to "modified" per its own comment — but no other fixture in
    # this suite ever produces a real "T" row, so a mutation reassigning that
    # entry to any other status name (e.g. "deleted") would slip through every
    # other test unnoticed while the file, still present on disk, rendered the
    # UI's delete chip. Verified live (git 2.43): replacing a tracked regular
    # file with a symlink is exactly what makes
    # `git diff HEAD --no-renames --name-status` emit a `T` row.
    (repo / "tracked.txt").unlink()
    (repo / "tracked.txt").symlink_to(repo / "to_delete.txt")

    result = diffsurface.changed_files(repo)
    by_path = {f["path"]: f for f in result["files"]}
    assert by_path["tracked.txt"]["status"] == "modified"


def test_changed_files_order_is_tracked_first_then_untracked_not_reversed(repo: Path):
    # 🔴 GUARD: the module docstring promises the merged list comes back
    # "ordered as git reports it (tracked changes first, then untracked)" —
    # every assertion above collapses the result into a by-path dict or a
    # set, which can't tell a correctly-ordered list from
    # `list(reversed(...))`. Two tracked files (git reports diff paths in
    # tree/lexicographic order, so "m_middle.txt" before "z_last.txt") plus
    # one untracked path that sorts alphabetically BEFORE both of them
    # ("a_untracked.txt") pins both halves at once: a reversal would put the
    # untracked path first and swap the two tracked paths.
    (repo / "m_middle.txt").write_text("m\n")
    (repo / "z_last.txt").write_text("z\n")
    _git(repo, "add", "m_middle.txt", "z_last.txt")
    _git(repo, "commit", "-q", "-m", "seed more tracked files")
    (repo / "m_middle.txt").write_text("m\nmodified\n")
    (repo / "z_last.txt").write_text("z\nmodified\n")
    (repo / "a_untracked.txt").write_text("new\n")

    result = diffsurface.changed_files(repo)
    paths = [f["path"] for f in result["files"]]
    assert paths == ["m_middle.txt", "z_last.txt", "a_untracked.txt"], paths


def test_changed_files_untracked_file_without_trailing_newline_still_counts_its_last_line(repo: Path):
    # 🔴 GUARD: a file whose last line has no trailing "\n" (the common case
    # for a file someone is still mid-edit on) must still count that line —
    # dropping the `+ (0 if ... endswith(b"\n") else 1)` half of _count_lines
    # silently undercounts every such file's additions estimate by exactly 1.
    # The clean-trailing-newline case above (scratch.txt, "a\nb\n") cannot
    # catch this: `data.count(b"\n")` alone already gives the right answer
    # when the file DOES end in a newline.
    (repo / "no_trailing_newline.txt").write_bytes(b"one\ntwo")
    result = diffsurface.changed_files(repo)
    by_path = {f["path"]: f for f in result["files"]}
    assert by_path["no_trailing_newline.txt"]["status"] == "untracked"
    assert by_path["no_trailing_newline.txt"]["additions"] == 2


def test_all_git_subprocess_calls_are_bounded_by_git_timeout(repo: Path, monkeypatch):
    # 🔴 GUARD: _GIT_TIMEOUT is what turns a wedged git process (e.g. a
    # pane's cwd on a stalled network mount) into a bounded failure instead
    # of a permanent hang behind /api/agents/<wid>/diff. Every call this
    # module makes goes through the single `_run` helper — spying on
    # `subprocess.run` itself (not `_run`) means a future call that bypasses
    # `_run` would also show up unbound here, not just a dropped kwarg on
    # the one call site a narrower mock would target.
    calls = []
    real_run = subprocess.run

    def spy(cmd, **kwargs):
        calls.append(kwargs.get("timeout"))
        return real_run(cmd, **kwargs)

    monkeypatch.setattr(diffsurface.subprocess, "run", spy)

    (repo / "tracked.txt").write_text("one\ntwo\nthree\nfour\n")
    diffsurface.changed_files(repo)
    diffsurface.file_patch(repo, "tracked.txt")

    assert calls, "no git subprocess calls were recorded — the spy is not wired in"
    assert all(t == diffsurface._GIT_TIMEOUT for t in calls), calls


def test_changed_files_untracked_gitignored_path_is_excluded(repo: Path):
    # 🔴 GUARD: `--exclude-standard` is the ONLY thing keeping every
    # .gitignore'd path (node_modules/, .venv/, *.pyc, build output) out of
    # this list — drop the flag and `git ls-files --others` reports EVERY
    # untracked path, ignored or not. Every other untracked-file fixture in
    # this suite is a fresh repo with no .gitignore, so the flag sits on a
    # default where it changes nothing; only a repo that actually HAS a
    # gitignored path present on disk can tell the flag's effect from its
    # own absence.
    (repo / ".gitignore").write_text("node_modules/\n")
    _git(repo, "add", ".gitignore")
    _git(repo, "commit", "-q", "-m", "add gitignore")
    (repo / "node_modules").mkdir()
    (repo / "node_modules" / "x.js").write_text("module.exports = 1;\n")
    (repo / "scratch.txt").write_text("a\n")

    result = diffsurface.changed_files(repo)
    paths = {f["path"] for f in result["files"]}
    assert "node_modules/x.js" not in paths, (
        "a gitignored untracked path leaked into the changed-files list — "
        "--exclude-standard is missing from the ls-files call"
    )
    assert "scratch.txt" in paths


def test_changed_files_untracked_empty_file_reports_zero_additions(repo: Path):
    # 🔴 GUARD: _count_lines' empty-file early return. Without it, a ZERO-BYTE
    # untracked file (the everyday result of `touch newfile.py`) falls through
    # to `data.count(b"\n") + (0 if data.endswith(b"\n") else 1)`, which for
    # `b""` is `0 + 1 == 1` — wrong by one. The clean-trailing-newline and
    # no-trailing-newline cases elsewhere in this suite both start from
    # non-empty content, so neither can catch the early return being narrowed
    # from `if not data` to `if data is None` (`b"" is not None`, so the early
    # return would stop firing for exactly this file).
    (repo / "empty.txt").write_bytes(b"")
    result = diffsurface.changed_files(repo)
    by_path = {f["path"]: f for f in result["files"]}
    assert by_path["empty.txt"]["status"] == "untracked"
    assert by_path["empty.txt"]["additions"] == 0


def test_changed_files_partially_staged_edit_sums_both_halves(repo: Path):
    # Stage one change, then make a second unstaged edit on top — `git diff HEAD`
    # (not `--cached` alone) is what makes both halves land in one row.
    (repo / "tracked.txt").write_text("one\ntwo\nthree\nSTAGED\n")
    _git(repo, "add", "tracked.txt")
    (repo / "tracked.txt").write_text("one\ntwo\nthree\nSTAGED\nUNSTAGED\n")

    result = diffsurface.changed_files(repo)
    assert len(result["files"]) == 1
    assert result["files"][0]["additions"] == 2


# --- file_patch: gated to changed_files' own list ----------------------------

def test_file_patch_returns_the_unified_diff_for_a_modified_file(repo: Path):
    (repo / "tracked.txt").write_text("one\ntwo\nthree\nfour\n")
    result = diffsurface.file_patch(repo, "tracked.txt")
    assert result["ok"] is True
    assert "+four" in result["patch"]


def test_file_patch_returns_full_content_for_an_untracked_file(repo: Path):
    (repo / "scratch.txt").write_text("hello\n")
    result = diffsurface.file_patch(repo, "scratch.txt")
    assert result["ok"] is True
    assert "+hello" in result["patch"]


def test_file_patch_returns_the_diff_for_a_fully_staged_file(repo: Path):
    # 🔴 GUARD: file_patch's tracked-file diff must be against HEAD, not the
    # index — `git diff -- <path>` (no `HEAD`) compares the index to the
    # worktree, which is EMPTY for a file that is staged and has no further
    # unstaged edits on top. Every other file_patch test in this suite edits
    # the worktree WITHOUT staging, so the drill-down half is only ever
    # exercised against the unstaged case; a fully-staged edit is the sibling
    # input that would come back with `patch == ""` under that mutation.
    (repo / "tracked.txt").write_text("one\ntwo\nthree\nfour\n")
    _git(repo, "add", "tracked.txt")
    result = diffsurface.file_patch(repo, "tracked.txt")
    assert result["ok"] is True
    assert "+four" in result["patch"], (
        "file_patch returned no diff text for a fully-staged file — "
        "it is diffing the index against the worktree instead of HEAD"
    )


def test_file_patch_handles_a_file_containing_invalid_utf8_bytes(repo: Path):
    # 🔴 GUARD: `errors="replace"` on `_run` is what keeps one non-UTF-8 byte
    # in a file's diff from raising UnicodeDecodeError straight out of
    # file_patch (and 500ing /api/agents/<wid>/diff/patch). Every other
    # fixture in this suite is pure ASCII, so the decode policy on git's
    # stdout is otherwise unmeasured. A latin-1 source file in an agent's
    # worktree is ordinary, not exotic.
    (repo / "latin.txt").write_bytes(b"cafe\n")
    _git(repo, "add", "latin.txt")
    _git(repo, "commit", "-q", "-m", "seed latin file")
    (repo / "latin.txt").write_bytes(b"caf\xe9\n")  # \xe9 = 'e' with acute in latin-1, invalid utf-8

    result = diffsurface.file_patch(repo, "latin.txt")
    assert result["ok"] is True


def test_file_patch_rejects_a_path_this_session_never_changed(repo: Path):
    # 🔴 GUARD: this is the ONLY thing standing between an arbitrary caller-
    # supplied `path` and a bare `git diff HEAD -- <path>` — dropping the
    # membership check would let /api/agents/<wid>/diff/patch read any path
    # git can resolve, not just what changed_files itself just reported.
    result = diffsurface.file_patch(repo, "tracked.txt")  # unchanged — nothing to diff
    assert result == {"ok": False, "error": "not a changed file in this session"}


def test_file_patch_not_a_git_repo(tmp_path: Path):
    plain = tmp_path / "plain"
    plain.mkdir()
    result = diffsurface.file_patch(plain, "whatever.txt")
    assert result == {"ok": False, "error": "not a git repository"}


# --- defensive bounds: _UNTRACKED_READ_CAP / _count_lines / numstat "-" -----

def test_count_lines_is_bounded_by_untracked_read_cap(tmp_path: Path, monkeypatch):
    # 🔴 GUARD: _UNTRACKED_READ_CAP is what keeps a stray multi-GB file dropped
    # in the worktree from making the endpoint stall on a full read — drop the
    # cap off the `fh.read(...)` call and the line count grows to match the
    # file's true (uncapped) content instead of stopping at the cap.
    monkeypatch.setattr(diffsurface, "_UNTRACKED_READ_CAP", 10)
    big = tmp_path / "big.txt"
    big.write_text("a\n" * 100)  # 200 bytes uncapped -> 100 lines; first 10 bytes -> 5 lines
    assert diffsurface._count_lines(big) == 5


def test_changed_files_survives_an_untracked_dangling_symlink(repo: Path):
    # 🔴 GUARD: _count_lines documents "never raises" for an unreadable/vanished
    # path — a dangling symlink's target doesn't exist, so opening it raises
    # FileNotFoundError (an OSError). changed_files must still return normally
    # rather than the whole per-session diff response blowing up over one
    # broken untracked path.
    dangling = repo / "dangling_link.txt"
    dangling.symlink_to(repo / "does-not-exist.txt")
    result = diffsurface.changed_files(repo)
    by_path = {f["path"]: f for f in result["files"]}
    assert by_path["dangling_link.txt"]["status"] == "untracked"
    assert by_path["dangling_link.txt"]["additions"] == 0


def test_changed_files_binary_file_numstat_sentinel_does_not_crash_int_parse(repo: Path):
    # 🔴 GUARD: git's --numstat reports "-\t-\tpath" for a binary file (no
    # line-level signal) — the `added_s != "-"` check exists so int(added_s)
    # is never handed that sentinel. Narrowing the check (e.g. to `!= ""`)
    # leaves "-" != "" true and crashes changed_files with a ValueError on any
    # modified binary file instead of leaving it at 0/0.
    (repo / "blob.bin").write_bytes(b"\x00\x01\x02binary")
    _git(repo, "add", "blob.bin")
    _git(repo, "commit", "-q", "-m", "add binary")
    (repo / "blob.bin").write_bytes(b"\x00\x01\x02binary-changed\xffmore")

    result = diffsurface.changed_files(repo)
    by_path = {f["path"]: f for f in result["files"]}
    assert by_path["blob.bin"]["status"] == "modified"
    assert by_path["blob.bin"]["additions"] == 0
    assert by_path["blob.bin"]["deletions"] == 0


def test_changed_files_reports_a_rename_as_a_clean_delete_plus_add_pair(repo: Path):
    # 🔴 GUARD: the module docstring states renames are DELIBERATELY reported
    # as a delete+add pair (`--no-renames`) rather than one row — git's own
    # rename detection has defaulted to ON since 2.9, so `--no-renames` is
    # actively suppressing that default, not restating it, which makes it
    # exactly the kind of flag a later reader mistakes for redundant and
    # deletes (or swaps for `--find-renames`, which does the opposite).
    # With detection on, `git diff --name-status` emits a single
    # "R100\told\tnew" line; this module's parse does
    # `line.split("\t", 1)`, so that ONE line comes back as a single entry
    # whose "path" itself contains a raw tab and matches no numstat row —
    # not two clean rows.
    _git(repo, "mv", "tracked.txt", "renamed.txt")

    result = diffsurface.changed_files(repo)
    by_path = {f["path"]: f for f in result["files"]}

    assert set(by_path) == {"tracked.txt", "renamed.txt"}
    assert by_path["tracked.txt"]["status"] == "deleted"
    assert by_path["renamed.txt"]["status"] == "added"
    assert "\t" not in by_path["tracked.txt"]["path"]
    assert "\t" not in by_path["renamed.txt"]["path"]
    assert by_path["tracked.txt"]["deletions"] == 3
    assert by_path["renamed.txt"]["additions"] == 3
