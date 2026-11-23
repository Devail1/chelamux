"""⚡ CMX-488: one GREEN baseline per tree sha, shared by self-check, sweep and the judge.

Every reader goes through ``judge.run_baseline``; these tests drive it on a real git repo
with a real pytest, and count the suite runs with a spy. A miss, an unreadable entry or a
red result must run the suite as before — unknown never reads as green — and reuse must
never cross tree shas.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from chela import baseline_cache as bc
from chela import judge
from chela import judge_select as js

TEST_CMD = f'"{sys.executable}" -m pytest -q -p no:cacheprovider'

FILES = {
    ".gitignore": "__pycache__/\n",
    "pkg/__init__.py": "",
    "pkg/widget.py": "def cue(x):\n    return x * 2\n",
    "test_widget.py": "from pkg.widget import cue\n\n\ndef test_cue():\n    assert cue(2) == 4\n",
}


def _git(root: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(root), "-c", "user.email=t@t", "-c", "user.name=t",
                           *args], capture_output=True, text=True, check=True).stdout.strip()


def _repo(root: Path, files: dict[str, str] = FILES) -> Path:
    for rel, text in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
    _git(root, "init", "-q", "-b", "main")
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "init")
    return root


@pytest.fixture
def runs(monkeypatch):
    """Every test_cmd ``run_suite`` was asked to run, in order (it still runs for real)."""
    seen: list[str] = []
    real = judge.run_suite

    def spy(test_cmd, cwd, timeout=judge.SUITE_TIMEOUT_SECONDS, extra_env=None):
        seen.append(test_cmd)
        return real(test_cmd, cwd, timeout, extra_env)

    monkeypatch.setattr(judge, "run_suite", spy)
    monkeypatch.setattr(judge, "_has_pytest_cov", lambda wt: False)
    return seen


def _baseline(root: Path, scratch: Path, cmd: str = TEST_CMD, **kw):
    scratch.mkdir(parents=True, exist_ok=True)
    return judge.run_baseline(root, cmd, 120, scratch, **kw)


def _entries() -> list[Path]:
    d = bc.cache_dir()
    return sorted(d.glob("*.json")) if d.is_dir() else []


# --- reuse: the saving ---------------------------------------------------------------------


def test_a_second_baseline_of_the_same_tree_reuses_the_first_and_runs_nothing(tmp_path, runs):
    """GUARD: the whole point — self-check, then sweep, then judge on ONE tree run the suite
    ONCE. The reuse hands back a green baseline and the same universe of tests."""
    root = _repo(tmp_path / "repo")
    b1, s1, why1 = _baseline(root, tmp_path / "s1")
    assert b1.green and s1 is not None and "reused" not in why1
    assert len(runs) == 1 and len(_entries()) == 1

    b2, s2, why2 = _baseline(root, tmp_path / "s2")
    assert len(runs) == 1                                   # nothing ran
    assert b2.green and (b2.passed, b2.failed, b2.errors) == (b1.passed, 0, 0)
    assert s2 is not None and [c.node for c in s2.cases] == [c.node for c in s1.cases]
    assert "baseline reused" in why2 and bc.tree_sha(root)[:12] in why2
    # the reader's OWN scratch holds the report (the sweep keeps it as evidence)
    assert js.parse_junit(root, tmp_path / "s2" / "baseline-junit.xml")


def test_a_clone_at_the_same_commit_reuses_it_from_another_worktree(tmp_path, runs):
    """GUARD: the judge measures in ITS worktree, not the agent's. Same tree ⇒ same key, so
    the judge's first round reuses the hand-in's baseline."""
    root = _repo(tmp_path / "repo")
    _baseline(root, tmp_path / "s1")
    other = tmp_path / "judge-wt"
    subprocess.run(["git", "clone", "-q", str(root), str(other)], check=True)
    b, s, why = _baseline(other, tmp_path / "s2")
    assert len(runs) == 1 and b.green and s is not None and "baseline reused" in why


# --- negative controls: reuse never crosses a tree sha ------------------------------------


def test_NEGATIVE_CONTROL_a_baseline_cached_for_another_sha_is_never_reused(tmp_path, runs):
    """GUARD (the brief's negative control): a new commit is a new tree; the baseline cached
    for the old one must not be read for it — the suite runs again."""
    root = _repo(tmp_path / "repo")
    _baseline(root, tmp_path / "s1")
    (root / "pkg/widget.py").write_text("def cue(x):\n    return x + x\n")
    _git(root, "commit", "-qam", "next")
    b, _, why = _baseline(root, tmp_path / "s2")
    assert len(runs) == 2 and b.green and "reused" not in why
    assert len(_entries()) == 2


def test_NEGATIVE_CONTROL_an_entry_planted_under_this_key_for_another_tree_is_a_miss(
    tmp_path, runs,
):
    """GUARD: the file name is a hash; the entry's OWN stored key is what is checked. An
    entry for tree A sitting at tree B's path (a collision, a copied file) is a miss."""
    root = _repo(tmp_path / "repo")
    _baseline(root, tmp_path / "s1")
    [old] = _entries()
    (root / "pkg/widget.py").write_text("def cue(x):\n    return x + x\n")
    _git(root, "commit", "-qam", "next")
    key = bc.key_for(root, TEST_CMD)
    assert key is not None and key.path() != old
    key.path().write_text(old.read_text())                  # tree A's entry at B's path
    assert bc.load(key) is None
    _, _, why = _baseline(root, tmp_path / "s2")
    assert len(runs) == 2 and "reused" not in why


def test_an_uncommitted_edit_is_part_of_the_tree(tmp_path, runs):
    """GUARD: a self-check runs on UNCOMMITTED edits. Its key is the working tree, not HEAD —
    so a judge of HEAD never reads a baseline of code that was never committed, and vice
    versa."""
    root = _repo(tmp_path / "repo")
    head_tree = _git(root, "rev-parse", "HEAD^{tree}")
    assert bc.tree_sha(root) == head_tree                   # clean ⇒ HEAD's tree exactly
    (root / "pkg/widget.py").write_text("def cue(x):\n    return x + x\n")
    dirty = bc.tree_sha(root)
    assert dirty and dirty != head_tree
    _baseline(root, tmp_path / "s1")                         # cached for the DIRTY tree
    _git(root, "checkout", "-q", "--", "pkg/widget.py")
    _, _, why = _baseline(root, tmp_path / "s2")             # HEAD's tree: a miss
    assert len(runs) == 2 and "reused" not in why
    # the real index was never touched by the throwaway one
    assert _git(root, "status", "--porcelain") == ""


def test_an_untracked_file_is_part_of_the_tree_and_an_ignored_one_is_not(tmp_path):
    """GUARD: an untracked test file changes what the suite runs, so it changes the key; an
    ignored one (``__pycache__``) does not, or no suite run could ever be stored."""
    root = _repo(tmp_path / "repo")
    clean = bc.tree_sha(root)
    (root / "__pycache__").mkdir()
    (root / "__pycache__" / "x.pyc").write_bytes(b"junk")
    assert bc.tree_sha(root) == clean
    (root / "test_extra.py").write_text("def test_x():\n    assert 1\n")
    assert bc.tree_sha(root) not in ("", clean)


# --- only a green, settled run is stored ---------------------------------------------------


def test_a_red_baseline_is_never_stored_and_never_reused(tmp_path, runs):
    """GUARD: red is never cached, so the next reader runs the suite and sees red itself."""
    root = _repo(tmp_path / "repo", {**FILES, "test_red.py": "def test_red():\n    assert 0\n"})
    b1, _, _ = _baseline(root, tmp_path / "s1")
    assert not b1.green and _entries() == []
    n = len(runs)
    b2, _, _ = _baseline(root, tmp_path / "s2")
    assert not b2.green and len(runs) > n


def test_a_baseline_green_only_through_a_flake_rerun_is_not_stored(tmp_path, runs):
    """GUARD: a flake's green is evidence about THAT run (the sweep deselects the flake from
    its own mutations); handing it to the next reader as a plain green would hide it."""
    marker = tmp_path / "flaked"
    flaky = (f"import pathlib\n\n\ndef test_flaky():\n    p = pathlib.Path({str(marker)!r})\n"
             "    if not p.exists():\n        p.write_text('x')\n        assert 0\n")
    root = _repo(tmp_path / "repo", {**FILES, "test_flaky.py": flaky})
    flakes: list[str] = []
    b, _, _ = _baseline(root, tmp_path / "s1", flakes=flakes)
    assert b.green and flakes and _entries() == []
    # ...and with no caller list the flake still blocks the store
    marker.unlink()
    b, _, _ = _baseline(root, tmp_path / "s2")
    assert b.green and _entries() == []


def test_a_tree_that_moves_during_the_run_stores_nothing(tmp_path, runs, monkeypatch):
    """GUARD: an edit lands while the suite runs (the agent kept typing). What ran is neither
    tree for sure, so nothing is stored."""
    root = _repo(tmp_path / "repo")
    real = judge.run_suite

    def editing(test_cmd, cwd, timeout=judge.SUITE_TIMEOUT_SECONDS, extra_env=None):
        out = real(test_cmd, cwd, timeout, extra_env)
        (root / "pkg/widget.py").write_text("def cue(x):\n    return x + x\n")
        return out

    monkeypatch.setattr(judge, "run_suite", editing)
    b, _, _ = _baseline(root, tmp_path / "s1")
    assert b.green and _entries() == []


def test_no_reuse_when_this_run_would_build_the_coverage_map(tmp_path, runs, monkeypatch):
    """GUARD: the cached entry carries no coverage map. A run that would build one must still
    run, or reuse would silently cost every later round its map."""
    root = _repo(tmp_path / "repo")
    _baseline(root, tmp_path / "s1")
    monkeypatch.setattr(judge, "_has_pytest_cov", lambda wt: True)
    monkeypatch.setattr(js, "coverage_args", lambda data_file: ([], {"FAKE_COV": "1"}))
    monkeypatch.setattr(js, "parse_coverage", lambda root, f: {})
    monkeypatch.setattr(js, "_cache_dir", lambda: tmp_path / "cov-cache")
    _, _, why = _baseline(root, tmp_path / "s2")
    assert len(runs) == 2 and "reused" not in why


# --- unknown never reads as green ----------------------------------------------------------


def _with_entry(tmp_path, runs, mutate) -> str:
    root = _repo(tmp_path / "repo")
    _baseline(root, tmp_path / "s1")
    [entry] = _entries()
    data = json.loads(entry.read_text())
    out = mutate(data)
    entry.write_text(out if isinstance(out, str) else json.dumps(data))
    _, _, why = _baseline(root, tmp_path / "s2")
    assert len(runs) == 2, "a bad entry must run the suite"
    return why


@pytest.mark.parametrize("mutate", [
    pytest.param(lambda d: "{not json", id="unparseable"),
    pytest.param(lambda d: "[]", id="not-a-dict"),
    pytest.param(lambda d: d.update(green=False), id="green-false"),
    pytest.param(lambda d: d.update(green="true"), id="green-truthy-string"),
    pytest.param(lambda d: d.update(exit_code=1), id="exit-code-1"),
    pytest.param(lambda d: d.update(failed=1), id="a-failure"),
    pytest.param(lambda d: d.update(errors=1), id="an-error"),
    pytest.param(lambda d: d.update(passed=0), id="ran-nothing"),
    pytest.param(lambda d: d.update(passed=True), id="bool-count"),
    pytest.param(lambda d: d.update(passed="3"), id="string-count"),
    pytest.param(lambda d: d.update(version=0), id="old-version"),
    pytest.param(lambda d: d.update(tree="0" * 40), id="other-tree"),
    pytest.param(lambda d: d.update(test_cmd="pytest -x"), id="other-cmd"),
    pytest.param(lambda d: d.update(deselects=["a::b"]), id="other-deselects"),
    pytest.param(lambda d: d.update(junit=""), id="empty-junit"),
    pytest.param(lambda d: d.update(junit=None), id="missing-junit"),
    pytest.param(lambda d: d.update(junit="<not xml"), id="unreadable-junit"),
    pytest.param(lambda d: d.update(tail=None), id="missing-tail"),
])
def test_a_bad_entry_is_a_miss_and_the_suite_runs(tmp_path, runs, mutate):
    assert "reused" not in _with_entry(tmp_path, runs, mutate)


def test_an_entry_that_cannot_be_written_back_is_a_miss(tmp_path, runs, monkeypatch):
    """GUARD (``except OSError`` in the reuse): the report cannot be written to this run's
    scratch ⇒ the suite runs, never a selector over nothing."""
    root = _repo(tmp_path / "repo")
    _baseline(root, tmp_path / "s1")
    real = Path.write_text

    def refuse(self, *a, **k):
        if self.name == "baseline-junit.xml":
            raise PermissionError("read-only scratch")
        return real(self, *a, **k)

    monkeypatch.setattr(Path, "write_text", refuse)
    _, _, why = _baseline(root, tmp_path / "s2")
    assert len(runs) == 2 and "reused" not in why


def test_no_tree_sha_means_no_key_and_no_reuse(tmp_path, runs, monkeypatch):
    root = _repo(tmp_path / "repo")
    _baseline(root, tmp_path / "s1")
    monkeypatch.setattr(bc, "tree_sha", lambda wt: "")
    assert bc.key_for(root, TEST_CMD) is None
    _, _, why = _baseline(root, tmp_path / "s2")
    assert len(runs) == 2 and "reused" not in why


def test_tree_sha_outside_a_repo_is_empty(tmp_path):
    assert bc.tree_sha(tmp_path) == ""


@pytest.mark.parametrize("exc", [OSError("no git"), subprocess.TimeoutExpired("git", 1)])
def test_a_git_that_cannot_run_gives_no_sha(tmp_path, monkeypatch, exc):
    """GUARD (``except (OSError, SubprocessError)``): a git that fails to run is no sha."""
    root = _repo(tmp_path / "repo")

    def boom(*a, **k):
        raise exc

    monkeypatch.setattr(bc.subprocess, "run", boom)
    assert bc.tree_sha(root) == ""


def test_a_failing_git_add_gives_no_sha(tmp_path, monkeypatch):
    root = _repo(tmp_path / "repo")
    real = bc._git
    monkeypatch.setattr(bc, "_git", lambda wt, *a, env: None if a[0] == "add" else
                        real(wt, *a, env=env))
    assert bc.tree_sha(root) == ""


def test_tree_sha_git_argv_and_throwaway_index(tmp_path, monkeypatch):
    """Pin every git call: argv, cwd via ``-C``, a timeout, and GIT_INDEX_FILE on the add and
    write-tree pointing at a throwaway index — never the worktree's own."""
    root = _repo(tmp_path / "repo")
    expected = _git(root, "rev-parse", "HEAD^{tree}")
    monkeypatch.setenv("GIT_DIR", str(tmp_path / "elsewhere"))     # must not leak into git
    calls: list[tuple[list[str], dict]] = []
    real = subprocess.run

    def spy(argv, **kw):
        calls.append((argv, kw))
        return real(argv, **kw)

    monkeypatch.setattr(bc.subprocess, "run", spy)
    sha = bc.tree_sha(root)
    assert sha == expected
    assert [c[0] for c in calls] == [
        ["git", "-C", str(root), "rev-parse", "--path-format=absolute", "--git-path", "index"],
        ["git", "-C", str(root), "add", "-A"],
        ["git", "-C", str(root), "write-tree"],
    ]
    real_index = str(root / ".git" / "index")
    for argv, kw in calls:
        assert kw["timeout"] == 120 and kw["capture_output"] is True
        assert "GIT_DIR" not in kw["env"]
    assert "GIT_INDEX_FILE" not in calls[0][1]["env"]
    for _, kw in calls[1:]:
        idx = kw["env"]["GIT_INDEX_FILE"]
        assert idx != real_index and Path(idx).name == "index"
        assert not Path(idx).exists()                       # thrown away afterwards


# --- the key -------------------------------------------------------------------------------


def test_the_cache_lives_under_CHELA_DIR_baselines(tmp_path, monkeypatch):
    monkeypatch.setenv("CHELA_DIR", str(tmp_path / "state"))
    assert bc.cache_dir() == tmp_path / "state" / "baselines"


def test_deselects_key_by_set_not_order_and_are_split_from_the_command():
    a = bc.split_deselects("pytest -q --deselect t.py::a --deselect=t.py::b")
    b = bc.split_deselects("pytest -q --deselect t.py::b --deselect t.py::a")
    assert a == b == ("pytest -q", ("t.py::a", "t.py::b"))
    assert bc.split_deselects("pytest -q") == ("pytest -q", ())
    assert bc.split_deselects("pytest 'unterminated") is None
    # a trailing bare --deselect has no value: kept as part of the command, not dropped
    assert bc.split_deselects("pytest --deselect") == ("pytest --deselect", ())


def test_a_different_deselect_set_is_a_different_entry(tmp_path, runs):
    root = _repo(tmp_path / "repo")
    _baseline(root, tmp_path / "s1")
    _, _, why = _baseline(root, tmp_path / "s2", cmd=f"{TEST_CMD} --deselect test_x.py::t")
    assert len(runs) == 2 and "reused" not in why
    _, _, why = _baseline(root, tmp_path / "s3", cmd=f"{TEST_CMD} --deselect=test_x.py::t")
    assert len(runs) == 2 and "baseline reused" in why       # same set, other spelling


def test_save_refuses_counts_that_are_not_green(tmp_path):
    key = bc.Key("t" * 40, "pytest", ())
    junit = tmp_path / "j.xml"
    junit.write_text("<testsuite/>")
    assert not bc.save(key, passed=0, failed=0, errors=0, tail="", junit=junit)
    assert not bc.save(key, passed=3, failed=1, errors=0, tail="", junit=junit)
    assert not bc.save(key, passed=3, failed=0, errors=1, tail="", junit=junit)
    assert not bc.save(None, passed=3, failed=0, errors=0, tail="", junit=junit)
    assert not bc.save(key, passed=3, failed=0, errors=0, tail="", junit=tmp_path / "missing")
    (tmp_path / "empty.xml").write_text("")
    assert not bc.save(key, passed=3, failed=0, errors=0, tail="", junit=tmp_path / "empty.xml")
    assert _entries() == []
    assert bc.save(key, passed=3, failed=0, errors=0, tail="ok", junit=junit)
    assert bc.load(key) == bc.Entry(3, 0, 0, "ok", "<testsuite/>")


def test_save_that_cannot_write_returns_false_and_leaves_no_entry(tmp_path, monkeypatch):
    monkeypatch.setenv("CHELA_DIR", str(tmp_path / "state"))
    (tmp_path / "state").mkdir()
    (tmp_path / "state" / "baselines").write_text("a file, not a dir")
    junit = tmp_path / "j.xml"
    junit.write_text("<testsuite/>")
    key = bc.Key("t" * 40, "pytest", ())
    assert not bc.save(key, passed=3, failed=0, errors=0, tail="", junit=junit)
    assert bc.load(key) is None


def test_the_entry_file_is_written_atomically(tmp_path, monkeypatch):
    """No temp file is left behind, and the entry is mode-independent JSON."""
    junit = tmp_path / "j.xml"
    junit.write_text("<testsuite/>")
    key = bc.Key("t" * 40, "pytest", ("a",))
    assert bc.save(key, passed=1, failed=0, errors=0, tail="", junit=junit)
    names = os.listdir(bc.cache_dir())
    assert names == [f"{key.digest}.json"]
    data = json.loads(key.path().read_text())
    assert (data["version"], data["tree"], data["test_cmd"], data["deselects"]) == \
        (1, "t" * 40, "pytest", ["a"])
