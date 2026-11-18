"""🔀 CMX-437 — a merge of the base into an AGENT's worktree rebuilds the generated bundle.

Against a real bare origin + clones. The fake build names the bundle after a hash of
``src/`` and writes a large shared body, so git's rename detection pairs the old and new
assets exactly as it does on the real Vite output: the conflicts are rename/delete and
rename/rename, the ones no ``.gitattributes`` merge driver is ever handed.
"""
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from chela import basemerge, basesync, dispatcher, generated, handinmove
from tests.test_judge_sweep import _run_row

APP = "chela/dashboard/static/app"
REAL_BASE_SYNC = True
SHARED = "".join(f"export const v{i} = {i};\n" for i in range(200))   # rename-detectable


def _g(cwd, *argv) -> str:
    return subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.com",
                           *argv], cwd=cwd, check=True, capture_output=True,
                          text=True).stdout.strip()


def _bundle_of(src: dict[str, str]) -> dict[str, str]:
    """What the fake build writes for ``src``: one hash-named asset and the manifest."""
    body = "".join(f"// {k}\n{v}" for k, v in sorted(src.items()))
    name = f"main-{hashlib.sha256(body.encode()).hexdigest()[:8]}.js"
    return {f"{APP}/manifest.json": json.dumps({"src/main.tsx": {"file": f"assets/{name}"}}),
            f"{APP}/assets/{name}": SHARED + body}


def _src(wt: Path) -> dict[str, str]:
    return {p.relative_to(wt).as_posix(): p.read_text()
            for p in sorted((wt / "src").rglob("*")) if p.is_file()}


def _build(wt: Path) -> None:
    """The fake ``pnpm build``: the bundle dir replaced by a fresh build of ``src/``."""
    for p in (wt / APP).rglob("*") if (wt / APP).exists() else ():
        if p.is_file():
            p.unlink()
    for rel, text in _bundle_of(_src(wt)).items():
        (wt / rel).parent.mkdir(parents=True, exist_ok=True)
        (wt / rel).write_text(text)


def _edit_and_build(wt: Path, rel: str, text: str, msg: str) -> str:
    (wt / rel).write_text(text)
    _build(wt)
    _g(wt, "add", "-A")
    _g(wt, "commit", "-qm", msg)
    return _g(wt, "rev-parse", "HEAD")


@pytest.fixture
def steps(monkeypatch):
    """The rebuild steps, faked: recorded; the build step runs :func:`_build`."""
    calls = []

    def step(wt, argv, timeout):
        calls.append(argv)
        if argv[-1] == "build":
            _build(Path(wt))
        return 0, ""
    monkeypatch.setattr(basemerge, "_step", step)
    return calls


@pytest.fixture
def two(tmp_path):
    """origin ``dev`` with a built bundle; PR A's and PR B's worktrees, each off ``dev``, each
    touching a DIFFERENT src file and rebuilding; then A merges to ``dev``."""
    origin, seed = tmp_path / "origin.git", tmp_path / "seed"
    _g(tmp_path, "init", "-q", "--bare", "-b", "dev", str(origin))
    _g(tmp_path, "clone", "-q", str(origin), str(seed))
    _g(seed, "checkout", "-qb", "dev")
    (seed / "src").mkdir()
    for name in ("a", "b", "c"):
        (seed / "src" / f"{name}.ts").write_text(f"{name}\n")
    _build(seed)
    _g(seed, "add", "-A")
    _g(seed, "commit", "-qm", "base")
    _g(seed, "push", "-q", "origin", "dev")
    wts = {}
    for pr, rel in (("A", "src/a.ts"), ("B", "src/b.ts")):
        wt = tmp_path / pr
        _g(tmp_path, "clone", "-q", "-b", "dev", str(origin), str(wt))
        _g(wt, "checkout", "-qb", f"pr-{pr}")
        _edit_and_build(wt, rel, f"{pr} changed this\n", f"PR {pr}")
        _g(wt, "push", "-q", "origin", f"pr-{pr}")
        wts[pr] = wt
    _g(seed, "fetch", "-q", "origin")
    _g(seed, "merge", "-q", "--no-ff", "--no-edit", "origin/pr-A")   # PR A merges first
    _g(seed, "push", "-q", "origin", "dev")
    return seed, wts["A"], wts["B"]


def test_two_frontend_prs_merge_one_after_the_other_with_no_conflict(two, steps):
    seed, _, b = two
    assert basesync.check(b, "dev")["conflicts"]          # CONFLICTING before the merge step

    res = basemerge.merge_into(b, "dev")

    assert res["outcome"] == "rebuilt" and res["files"]
    assert all(generated.is_rebuilt(f) for f in res["files"])
    assert basesync.check(b, "dev") == {"behind": 0, "conflicts": []}
    head = _g(b, "rev-parse", "HEAD")
    assert res["merge_sha"] == head
    assert _g(b, "log", "-1", "--format=%s") == "Merge origin/dev; rebuild generated bundle"
    assert _g(b, "rev-parse", "HEAD^2") == res["base_sha"] == _g(seed, "rev-parse", "HEAD")
    assert _g(b, "status", "--porcelain") == ""
    # both PRs' source is in, and the bundle is exactly a fresh build of it
    assert (b / "src/a.ts").read_text() == "A changed this\n"
    assert (b / "src/b.ts").read_text() == "B changed this\n"
    want = _bundle_of(_src(b))
    assert sorted(_g(b, "ls-files", APP).splitlines()) == sorted(want)
    assert all((b / rel).read_text() == text for rel, text in want.items())
    assert steps == [s[0] for s in basemerge.REBUILD_STEPS]
    # and the second PR now merges into dev cleanly
    _g(b, "push", "-q", "origin", "pr-B")
    _g(seed, "fetch", "-q", "origin")
    out = subprocess.run(["git", "merge-tree", "--write-tree", "HEAD", "origin/pr-B"], cwd=seed,
                         capture_output=True, text=True)
    assert out.returncode == 0, out.stdout


def test_negative_control_without_the_rebuild_the_second_pr_conflicts(two, steps, monkeypatch):
    _, _, b = two
    monkeypatch.setattr(generated, "REBUILT", ())        # the step disabled
    head = _g(b, "rev-parse", "HEAD")

    res = basemerge.merge_into(b, "dev")

    assert res["outcome"] == "conflict" and res["merge_sha"] is None
    assert res["files"] and all(f.startswith(APP) for f in res["files"])
    assert steps == []
    assert _g(b, "rev-parse", "HEAD") == head
    basemerge.abort(b)


def test_a_real_src_conflict_still_surfaces_and_only_it_is_left(two, steps):
    seed, _, b = two
    _g(b, "fetch", "-q", "origin")
    _edit_and_build(b, "src/a.ts", "B also changed a\n", "PR B touches a.ts too")
    head = _g(b, "rev-parse", "HEAD")

    res = basemerge.merge_into(b, "dev")

    assert (res["outcome"], res["files"], res["merge_sha"]) == ("conflict", ["src/a.ts"], None)
    assert steps == []                                    # nothing rebuilt on conflict markers
    unmerged = _g(b, "diff", "--name-only", "--diff-filter=U").splitlines()
    assert unmerged == ["src/a.ts"]                       # the bundle is no longer in conflict
    assert (b / ".git" / "MERGE_HEAD").is_file()          # left in progress, for the agent
    assert _g(b, "rev-parse", "HEAD") == head
    # the bundle was set to this branch's, whole: no stray renamed-in asset from the base
    assert sorted(_g(b, "ls-files", APP).splitlines()) == sorted(
        _g(b, "ls-tree", "-r", "--name-only", "HEAD", APP).splitlines())
    assert _g(b, "diff", "--cached", "--name-only", "HEAD", "--", APP) == ""
    hint = basemerge.resume_hint("dev", res["files"])
    assert hint.startswith("origin/dev is merged in, with 1 source conflict(s) left for you:\n"
                           "     src/a.ts\n")
    assert "pnpm -C chela/dashboard/frontend build" in hint and "STALE" in hint


def test_a_src_only_conflict_leaves_the_bundle_alone(tmp_path, steps):
    origin, seed, wt = tmp_path / "o.git", tmp_path / "s", tmp_path / "w"
    _g(tmp_path, "init", "-q", "--bare", "-b", "dev", str(origin))
    _g(tmp_path, "clone", "-q", str(origin), str(seed))
    _g(seed, "checkout", "-qb", "dev")
    (seed / "x.py").write_text("x = 0\n")
    _g(seed, "add", "-A")
    _g(seed, "commit", "-qm", "base")
    _g(seed, "push", "-q", "origin", "dev")
    _g(tmp_path, "clone", "-q", "-b", "dev", str(origin), str(wt))
    (wt / "x.py").write_text("x = 1\n")
    _g(wt, "commit", "-qam", "pr")
    (seed / "x.py").write_text("x = 2\n")
    _g(seed, "commit", "-qam", "dev")
    _g(seed, "push", "-q", "origin", "dev")
    res = basemerge.merge_into(wt, "dev")
    assert (res["outcome"], res["files"], steps) == ("conflict", ["x.py"], [])
    assert _g(wt, "diff", "--name-only", "--diff-filter=U") == "x.py"


def test_a_failed_rebuild_aborts_the_merge_and_leaves_the_worktree_as_it_was(two, monkeypatch):
    _, _, b = two
    monkeypatch.setattr(basemerge, "_step", lambda wt, argv, timeout: (
        (2, "vite: syntax error") if argv[-1] == "build" else (0, "")))
    head = _g(b, "rev-parse", "HEAD")

    res = basemerge.merge_into(b, "dev")

    assert res["outcome"] == "build_failed"
    assert res["detail"] == "`pnpm -C chela/dashboard/frontend build` exited 2:\nvite: syntax error"
    assert _g(b, "rev-parse", "HEAD") == head
    assert not (b / ".git" / "MERGE_HEAD").exists()
    assert _g(b, "status", "--porcelain") == ""


def test_an_agents_untracked_notes_are_not_strays_but_a_build_stray_is(two, monkeypatch):
    _, _, b = two
    (b / "notes.json").write_text("{}")                   # there before the merge

    def step(wt, argv, timeout):
        if argv[-1] == "build":
            _build(Path(wt))
            if stray:
                (Path(wt) / "stray.txt").write_text("x")
        return 0, ""
    monkeypatch.setattr(basemerge, "_step", step)
    stray = True
    res = basemerge.merge_into(b, "dev")
    assert res["outcome"] == "build_failed" and res["detail"].endswith(
        "changed outside the bundle:\nstray.txt")
    (b / "stray.txt").unlink()
    stray = False
    assert basemerge.merge_into(b, "dev")["outcome"] == "rebuilt"
    assert (b / "notes.json").read_text() == "{}"


def test_uncommitted_changes_are_refused_before_anything_is_fetched_or_merged(two, steps):
    _, _, b = two
    head = _g(b, "rev-parse", "HEAD")
    (b / "src/c.ts").write_text("half-typed\n")
    res = basemerge.merge_into(b, "dev")
    assert (res["outcome"], res["files"], res["base_sha"]) == ("dirty", ["src/c.ts"], "")
    assert _g(b, "rev-parse", "HEAD") == head and steps == []
    assert (b / "src/c.ts").read_text() == "half-typed\n"


def test_a_clean_merge_and_a_current_branch(two, steps):
    seed, a, _ = two
    # PR A's branch is already in dev (merged with --no-ff): one merge commit behind, clean
    res = basemerge.merge_into(a, "dev")
    assert res["outcome"] == "merged" and res["merge_sha"] == _g(a, "rev-parse", "HEAD")
    assert res["merge_sha"] == _g(seed, "rev-parse", "HEAD")      # fast-forwarded
    again = basemerge.merge_into(a, "dev")
    assert (again["outcome"], again["merge_sha"]) == ("current", res["merge_sha"])
    assert steps == []


def test_a_merge_that_fails_naming_no_file_raises_and_is_aborted(two, steps, monkeypatch):
    _, _, b = two
    real = basemerge._git
    aborted = []

    def git(wt, *argv, **kw):
        if argv[:1] == ("merge",) and argv[1:2] != ("--abort",):
            raise RuntimeError("`git merge` failed: refusing")
        if argv[:2] == ("merge", "--abort"):
            aborted.append(wt)
        return real(wt, *argv, **kw)
    monkeypatch.setattr(basemerge, "_git", git)
    with pytest.raises(RuntimeError, match="failed naming no file"):
        basemerge.merge_into(b, "dev")
    assert aborted == [Path(b)]


def test_abort_with_no_merge_in_progress_is_not_an_error(tmp_path):
    _g(tmp_path, "init", "-q")
    basemerge.abort(tmp_path)             # no raise


def test_keep_ours_with_no_bundle_on_head_just_drops_it(tmp_path):
    _g(tmp_path, "init", "-q")
    (tmp_path / "x").write_text("x")
    _g(tmp_path, "add", "x")
    _g(tmp_path, "commit", "-qm", "x")
    (tmp_path / APP).mkdir(parents=True)
    (tmp_path / APP / "manifest.json").write_text("{}")
    _g(tmp_path, "add", APP)
    basemerge._keep_ours(tmp_path)
    assert _g(tmp_path, "ls-files", APP) == "" and not (tmp_path / APP / "manifest.json").exists()


# --- wiring: chela task-finished's base-sync gate, chela merge-base, the mid-check move -----

@pytest.fixture
def run_b(two, tmp_path, monkeypatch):
    monkeypatch.setattr(dispatcher, "DB_PATH", tmp_path / "scheduler.db")
    _run_row(tmp_path, two[2])
    return two


def test_task_finished_gate_rebuilds_a_bundle_only_conflict_and_records_it(run_b, steps,
                                                                             capsys):
    seed, _, b = run_b
    out = dispatcher.check_base_sync("cmx-88")
    assert out["ok"] is True, out
    merge = _g(b, "rev-parse", "HEAD")
    assert out["since"] == _g(seed, "rev-parse", "HEAD")
    assert out["record"] == {"base": "dev", "rebuilt": {
        "base_sha": out["since"][:12], "merge_sha": merge[:12]}}
    assert f"rebuilt from the merged source and committed ({merge[:12]})" in \
        capsys.readouterr().out
    line = basesync.render(out["record"])
    assert line == (f"🔀 Conflicted with origin/dev only in the generated bundle: merged "
                    f"({out['since'][:12]}) and rebuilt from the merged source at hand-in "
                    f"({merge[:12]}).")


def test_task_finished_gate_refuses_when_the_rebuild_fails(run_b, monkeypatch):
    _, _, b = run_b
    head = _g(b, "rev-parse", "HEAD")
    monkeypatch.setattr(basemerge, "_step", lambda wt, argv, timeout: (
        (1, "boom") if argv[-1] == "build" else (0, "")))
    out = dispatcher.check_base_sync("cmx-88", True)
    assert out["ok"] is False
    assert "CONFLICTS with origin/dev" in out["error"]
    assert ("It conflicts only in the generated bundle, but merging and rebuilding it here "
            "failed: the rebuild failed (merge aborted):\n`pnpm -C chela/dashboard/frontend "
            "build` exited 1:\nboom") in out["error"]
    assert _g(b, "rev-parse", "HEAD") == head and not (b / ".git" / "MERGE_HEAD").exists()


def test_task_finished_gate_names_uncommitted_changes(run_b, steps):
    _, _, b = run_b
    (b / "src/c.ts").write_text("wip\n")
    out = dispatcher.check_base_sync("cmx-88")
    assert out["ok"] is False and out["error"].endswith(
        "failed: the worktree has uncommitted changes: src/c.ts")
    assert steps == []


def test_task_finished_gate_aborts_when_the_base_moved_on_to_a_source_conflict(run_b, steps,
                                                                               monkeypatch):
    seed, _, b = run_b
    head = _g(b, "rev-parse", "HEAD")
    real = basesync.check
    seen = []

    def check(wt, base):
        seen.append(1)
        if len(seen) == 1:                 # dev moves to a source conflict after the gate looked
            _g(seed, "fetch", "-q", "origin")
            (seed / "src/b.ts").write_text("dev changed b\n")
            _build(seed)
            _g(seed, "add", "-A")
            _g(seed, "commit", "-qm", "dev b")
            _g(seed, "push", "-q", "origin", "dev")
            return {"behind": 1, "conflicts": [f"{APP}/manifest.json"]}
        return real(wt, base)
    monkeypatch.setattr(basesync, "check", check)
    out = dispatcher.check_base_sync("cmx-88")
    assert out["ok"] is False and out["error"].endswith("the merge came out `conflict`")
    assert _g(b, "rev-parse", "HEAD") == head
    assert not (b / ".git" / "MERGE_HEAD").exists()
    assert _g(b, "status", "--porcelain") == ""


def test_task_finished_gate_still_refuses_a_mixed_conflict_with_the_merge_base_hint(run_b, steps):
    _, _, b = run_b
    _edit_and_build(b, "src/a.ts", "B also changed a\n", "B touches a.ts")
    out = dispatcher.check_base_sync("cmx-88")
    assert out["ok"] is False and "src/a.ts" in out["error"]
    assert "run `chela merge-base <task-id>` instead of `git merge`" in out["error"]
    assert steps == []


def test_the_merge_base_hint_is_only_for_a_bundle_conflict():
    assert "chela merge-base" not in basesync.refusal("dev", 1, ["mod.py"])
    assert "chela merge-base" in basesync.refusal("dev", 1, ["mod.py", f"{APP}/manifest.json"])


def _cli(*argv) -> int | None:
    from chela import main
    with patch.object(sys, "argv", ["chela", *argv]):
        try:
            main.main()
        except SystemExit as e:
            return e.code
    return None


def test_cli_merge_base_rebuilds(run_b, steps, capsys):
    _, _, b = run_b
    assert _cli("merge-base", "cmx-88") is None
    said = capsys.readouterr().out
    assert "conflicted only in the generated bundle, rebuilt from the merged source and " \
           f"committed → {_g(b, 'rev-parse', 'HEAD')[:12]}." in said
    assert _cli("merge-base", "cmx-88") is None
    assert "merge-base: already contains origin/dev" in capsys.readouterr().out


@pytest.mark.parametrize("setup, said", [
    ("conflict", "origin/dev is merged in, with 1 source conflict(s) left for you:"),
    ("dirty", "merge-base: commit your changes first; uncommitted: src/c.ts"),
    ("build", "the rebuild FAILED — the merge was aborted, nothing changed:\n`pnpm"),
    ("norun", "merge-base: no run with a worktree on record for cmx-9"),
])
def test_cli_merge_base_exits_1_and_says_why(run_b, steps, monkeypatch, capsys, setup, said):
    _, _, b = run_b
    task = "cmx-88"
    if setup == "conflict":
        _edit_and_build(b, "src/a.ts", "B also changed a\n", "B touches a.ts")
    elif setup == "dirty":
        (b / "src/c.ts").write_text("wip\n")
    elif setup == "build":
        monkeypatch.setattr(basemerge, "_step", lambda wt, argv, timeout: (1, "x"))
    else:
        task = "cmx-9"
    assert _cli("merge-base", task) == 1
    assert said in capsys.readouterr().out


def test_cli_merge_base_a_clean_merge(run_b, steps, capsys):
    seed, _, b = run_b
    assert basemerge.merge_into(b, "dev")["outcome"] == "rebuilt"   # in sync…
    steps.clear()
    (seed / "src/c.ts").write_text("dev changed c\n")                # …then dev moves, src only
    _g(seed, "commit", "-qam", "dev c")
    _g(seed, "push", "-q", "origin", "dev")
    assert _cli("merge-base", "cmx-88") is None
    said = capsys.readouterr().out
    assert said.startswith(f"merge-base: merged origin/dev ({_g(seed, 'rev-parse', 'HEAD')[:12]})"
                           f" cleanly → {_g(b, 'rev-parse', 'HEAD')[:12]}.")
    assert steps == []


def test_merge_base_reports_git_that_cannot_say(run_b, monkeypatch):
    monkeypatch.setattr(basemerge, "merge_into", lambda wt, base: (_ for _ in ()).throw(
        RuntimeError("no remote")))
    assert dispatcher.merge_base("cmx-88") == {
        "ok": False, "error": "could not merge origin/dev: no remote"}


def test_the_conflict_rework_body_points_at_merge_base_only_for_a_bundle_conflict():
    res = {"base_sha": "a" * 40, "detail": "", "outcome": "conflict"}
    assert "chela merge-base" not in basemerge.rework_body({**res, "files": ["mod.py"]}, "dev")
    assert "chela merge-base" in basemerge.rework_body(
        {**res, "files": ["mod.py", f"{APP}/manifest.json"]}, "dev")


# --- the base moving DURING the hand-in's checks (CMX-334) ---------------------------------

def test_reconcile_merges_a_bundle_only_move_by_rebuilding(two, steps, monkeypatch):
    seed, _, b = two
    since = _g(seed, "rev-parse", "HEAD~1")              # dev before PR A merged
    monkeypatch.setattr(handinmove, "related_tests", lambda wt, files: [])
    wf = type("W", (), {"get": lambda self, *a, default=None: default})()
    out = handinmove.reconcile(b, wf, "W.md", "dev", since, None)
    assert out["ok"] is True, out
    assert _g(b, "log", "-1", "--format=%s") == "Merge origin/dev; rebuild generated bundle"
    assert basesync.check(b, "dev") == {"behind": 0, "conflicts": []}


def test_reconcile_refuses_when_the_bundle_rebuild_fails(two, monkeypatch):
    seed, _, b = two
    head = _g(b, "rev-parse", "HEAD")
    monkeypatch.setattr(basemerge, "_step", lambda wt, argv, timeout: (1, "boom"))
    out = handinmove.reconcile(b, None, "W.md", "dev", _g(seed, "rev-parse", "HEAD~1"), None)
    assert out["ok"] is False
    assert out["error"].startswith("origin/dev moved during the checks and conflicts only in "
                                   "the generated bundle, but merging and rebuilding it "
                                   "failed (build_failed): `pnpm install --frozen-lockfile` "
                                   "exited 1:\nboom")
    assert _g(b, "rev-parse", "HEAD") == head


def test_reconcile_aborts_a_merge_that_turned_into_a_source_conflict(two, monkeypatch):
    seed, _, b = two
    monkeypatch.setattr(basemerge, "merge_into", lambda wt, base: {
        "outcome": "conflict", "files": ["src/a.ts"], "detail": ""})
    aborted = []
    monkeypatch.setattr(basemerge, "abort", lambda wt: aborted.append(wt))
    out = handinmove.reconcile(b, None, "W.md", "dev", _g(seed, "rev-parse", "HEAD~1"), None)
    assert out["ok"] is False and out["error"].endswith("failed (conflict): src/a.ts")
    assert aborted == [b]


def test_reconcile_names_a_merge_that_could_not_run(two, monkeypatch):
    seed, _, b = two
    monkeypatch.setattr(basemerge, "merge_into", lambda wt, base: (_ for _ in ()).throw(
        OSError("no git")))
    out = handinmove.reconcile(b, None, "W.md", "dev", _g(seed, "rev-parse", "HEAD~1"), None)
    assert out["ok"] is False and out["error"].endswith("but the merge could not run: no git")
