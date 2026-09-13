"""scripts/npm-shared-install.sh — ONE shared node_modules across worktrees (CMX-151).

`npm ci` always unpacks real files (no hardlink-from-cache path like `uv sync` has), so
running it once per worktree meant N concurrent worktrees each paying to unpack the same
27M of jsdom. This script installs once into a directory shared by every worktree and
symlinks each worktree's `node_modules` to it, reinstalling only when the lockfile the
worktree carries no longer matches the one the shared install was built from.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "npm-shared-install.sh"


def _make_worktree(parent: Path, name: str) -> Path:
    wt = parent / name
    wt.mkdir()
    shutil.copy(ROOT / "package.json", wt / "package.json")
    shutil.copy(ROOT / "package-lock.json", wt / "package-lock.json")
    return wt


def _no_venv_env() -> dict[str, str]:
    """`uv run pytest` puts THIS repo's `.venv/bin` ahead of everything else on PATH, so the
    script's bare `python3` call resolves to the venv interpreter — which has `chela` already
    on it via the editable install, importable regardless of what REPO_ROOT the script
    computes. That masks the one thing `resolves_every_declared_package` depends on
    (`sys.path.insert(0, repo_root)` actually landing on a checkout that carries `chela/`).
    Stripping `.venv` entries forces the script's `python3` back to a system interpreter that
    has no ambient route to `chela`, matching how the script is actually invoked from
    `hooks.before_run` — so REPO_ROOT correctness is the only thing that can make the import
    succeed."""
    env = dict(os.environ)
    parts = [p for p in env.get("PATH", "").split(os.pathsep) if ".venv" not in p]
    env["PATH"] = os.pathsep.join(parts)
    return env


def _run(worktree: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [str(SCRIPT)],
        cwd=worktree,
        capture_output=True,
        text=True,
        timeout=300,
        env=_no_venv_env(),
    )


def test_no_op_without_a_lockfile(tmp_path):
    """No package-lock.json — nothing declared, nothing to share, and no ~/.npm-shared
    litter left behind for a repo that has none."""
    root = tmp_path / "worktrees"
    root.mkdir()
    wt = root / "wt"
    wt.mkdir()

    out = _run(wt)

    assert out.returncode == 0, out.stderr
    assert not (wt / "node_modules").exists()
    assert not (root / ".npm-shared").exists()


@pytest.mark.skipif(not shutil.which("npm"), reason="npm is not installed")
def test_symlinks_into_one_shared_install_and_reuses_it(tmp_path):
    """⛔ THE LOAD-BEARING ONE. Two worktrees with the SAME lockfile must end up pointing at
    the SAME install, and the second one must not trigger another `npm ci` — `npm ci`
    deletes node_modules before it reinstalls, so a sentinel file surviving the second
    worktree's run is proof no second install happened."""
    root = tmp_path / "worktrees"
    root.mkdir()
    wt1 = _make_worktree(root, "wt1")

    out1 = _run(wt1)
    assert out1.returncode == 0, out1.stderr

    node_modules = wt1 / "node_modules"
    assert node_modules.is_symlink(), "node_modules must be a symlink into the shared install"
    shared = node_modules.resolve()
    assert (shared / "jsdom").is_dir()

    sentinel = shared / ".sentinel-untouched"
    sentinel.write_text("still here")

    wt2 = _make_worktree(root, "wt2")
    out2 = _run(wt2)
    assert out2.returncode == 0, out2.stderr

    node_modules2 = wt2 / "node_modules"
    assert node_modules2.is_symlink()
    assert node_modules2.resolve() == shared, "second worktree must share the SAME install"
    assert sentinel.exists(), "shared install was reinstalled even though the lockfile didn't change"


@pytest.mark.skipif(not shutil.which("npm"), reason="npm is not installed")
def test_reinstalls_when_shared_node_modules_exists_but_is_empty(tmp_path):
    """#508: a present-but-empty shared node_modules (e.g. an npm ci that never ran or died
    mid-install) satisfies neither the lockfile-diff arm nor a plain `-d` existence check, so
    the old freshness test read it as "already installed" and exited 0 having installed
    nothing — every worktree then symlinked to a permanently-empty node_modules."""
    root = tmp_path / "worktrees"
    root.mkdir()
    wt = _make_worktree(root, "wt")

    shared = root / ".npm-shared"
    shared.mkdir()
    shutil.copy(wt / "package.json", shared / "package.json")
    shutil.copy(wt / "package-lock.json", shared / "package-lock.json")
    (shared / "node_modules").mkdir()  # exists, but empty — the bug state

    out = _run(wt)

    assert out.returncode == 0, out.stderr
    node_modules = wt / "node_modules"
    assert node_modules.is_symlink()
    assert (node_modules.resolve() / "jsdom").is_dir(), "empty shared node_modules was never repopulated"


@pytest.mark.skipif(not shutil.which("npm"), reason="npm is not installed")
def test_reinstalls_when_shared_node_modules_is_partially_unpacked(tmp_path):
    """#510: a present-and-NON-empty shared node_modules that is still missing the declared
    package (e.g. an `npm ci` interrupted mid-unpack — #508's own issue names this as the
    actual cause) satisfied the old `-z "$(ls -A ...)"` freshness check, since non-empty read
    as installed even though jsdom itself was never unpacked."""
    root = tmp_path / "worktrees"
    root.mkdir()
    wt = _make_worktree(root, "wt")

    shared = root / ".npm-shared"
    shared.mkdir()
    shutil.copy(wt / "package.json", shared / "package.json")
    shutil.copy(wt / "package-lock.json", shared / "package-lock.json")
    node_modules = shared / "node_modules"
    node_modules.mkdir()
    (node_modules / "whatwg-url").mkdir()  # some OTHER package unpacked, jsdom is not

    out = _run(wt)

    assert out.returncode == 0, out.stderr
    node_modules_wt = wt / "node_modules"
    assert node_modules_wt.is_symlink()
    assert (node_modules_wt.resolve() / "jsdom").is_dir(), (
        "partially-unpacked shared node_modules was never repopulated"
    )


@pytest.mark.skipif(not shutil.which("npm"), reason="npm is not installed")
def test_reinstalls_when_the_lockfile_actually_changes(tmp_path):
    """The staleness check has to actually work in the other direction too — a byte-different
    lockfile MUST invalidate the shared install, not reuse it forever."""
    root = tmp_path / "worktrees"
    root.mkdir()
    wt1 = _make_worktree(root, "wt1")
    assert _run(wt1).returncode == 0

    shared = (wt1 / "node_modules").resolve()
    sentinel = shared / ".sentinel-stale"
    sentinel.write_text("stale")

    wt2 = _make_worktree(root, "wt2")
    lock = wt2 / "package-lock.json"
    lock.write_text(lock.read_text() + "\n")  # byte-different, same declared deps

    out2 = _run(wt2)

    assert out2.returncode == 0, out2.stderr
    assert not sentinel.exists(), "a changed lockfile did not trigger a reinstall of the shared install"
    assert (shared / "jsdom").is_dir()
