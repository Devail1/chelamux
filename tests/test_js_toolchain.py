"""CMX-388 — the repo's JS tooling is pnpm, pinned, and the hook that builds every worktree
installs with it.

``package.json`` declares two dev-only deps (jsdom, playwright). They used to install with
``npm ci`` into ONE shared, symlinked ``node_modules`` (``scripts/npm-shared-install.sh``,
CMX-151) — a hand-built copy of what pnpm's content-addressed store does natively (hardlink
one copy of each package into every worktree), and the source of one judge-blocking outage
(#508). This file pins the pieces of the move that nothing else would notice drifting:

* ``packageManager`` names a **10.x** pnpm — pnpm 11 crashes on the Node 20 CI and this
  machine pin (``ERR_VM_DYNAMIC_IMPORT_CALLBACK_MISSING``), and CI's ``pnpm/action-setup``
  reads its version from this one field;
* ``pnpm-lock.yaml`` is the ONLY lockfile, and it still pins playwright to exactly the
  version whose Chromium revision the browser suite relies on;
* ``hooks.before_run`` — EXECUTED here against fake ``uv``/``pnpm``, not grepped — runs
  ``pnpm install --frozen-lockfile`` on a pnpm tree, never writes through a legacy shared
  symlink, still provisions a pre-CMX-388 npm branch, and aborts on a failing step.
"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

import pytest
import yaml

from chela import workflow

ROOT = Path(__file__).resolve().parent.parent
PACKAGE_JSON = ROOT / "package.json"


def _pkg() -> dict:
    return json.loads(PACKAGE_JSON.read_text())


def test_package_manager_pins_a_10x_pnpm():
    pm = _pkg().get("packageManager", "")
    assert re.fullmatch(r"pnpm@10\.\d+\.\d+(\+sha(256|512)\.[0-9a-f]+)?", pm), (
        f"package.json's packageManager is {pm!r} — it must pin an exact pnpm 10.x. pnpm 11 "
        "crashes on Node 20 (the version CI and this repo pin), and CI's pnpm/action-setup "
        "installs whatever this field names."
    )


def test_the_license_field_is_the_repos_license():
    assert _pkg()["license"] == "AGPL-3.0-or-later"


def test_pnpm_lock_is_the_only_lockfile_and_pins_playwright_exactly():
    assert not (ROOT / "package-lock.json").exists(), (
        "package-lock.json is back next to pnpm-lock.yaml — two lockfiles drift, and the judge "
        "prefers pnpm-lock.yaml, so the npm one would silently stop meaning anything"
    )
    lock = yaml.safe_load((ROOT / "pnpm-lock.yaml").read_text())
    dev = lock["importers"]["."]["devDependencies"]
    assert set(dev) == set(_pkg()["devDependencies"])
    assert dev["playwright"]["version"] == "1.63.0", (
        "playwright pins the Chromium revision the browser suite and ~/.cache/ms-playwright "
        "rely on — it moves only on purpose"
    )


# --- hooks.before_run, executed ------------------------------------------------------------

_FAKE = """#!/bin/sh
echo "{name} $*" >> "$CALLS"
exit ${{FAIL_{upper}:-0}}
"""


def _fake_bin(tmp_path: Path) -> Path:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name in ("uv", "pnpm", "npm"):
        exe = bin_dir / name
        exe.write_text(_FAKE.format(name=name, upper=name.upper()))
        exe.chmod(0o755)
    return bin_dir


def _run_hook(script: str, cwd: Path, tmp_path: Path, **env_over) -> tuple[int, list[str]]:
    calls = tmp_path / "calls.log"
    calls.touch()
    env = {"PATH": f"{_fake_bin(tmp_path)}:/usr/bin:/bin", "CALLS": str(calls), **env_over}
    proc = subprocess.run(script, shell=True, cwd=cwd, env=env,
                          capture_output=True, text=True, timeout=60)
    return proc.returncode, calls.read_text().splitlines()


def _before_run() -> str:
    return workflow.load_workflow(ROOT / "WORKFLOW.md").get("hooks", "before_run") or ""


def _tree(root: Path, *lockfiles: str) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / "package.json").write_text(json.dumps({"devDependencies": {"jsdom": "^29"}}))
    for lf in lockfiles:
        (root / lf).write_text("# lockfile\n")
    return root


def test_before_run_installs_a_pnpm_tree_with_a_frozen_lockfile(tmp_path):
    wt = _tree(tmp_path / "wt", "pnpm-lock.yaml")
    rc, calls = _run_hook(_before_run(), wt, tmp_path)
    assert rc == 0
    assert "pnpm install --frozen-lockfile" in calls, calls
    assert not any(c.startswith("npm ") for c in calls), calls


def test_before_run_never_writes_through_a_legacy_shared_symlink(tmp_path):
    shared = tmp_path / ".npm-shared" / "node_modules"
    (shared / "jsdom").mkdir(parents=True)
    wt = _tree(tmp_path / "wt", "pnpm-lock.yaml")
    (wt / "node_modules").symlink_to(shared)

    rc, calls = _run_hook(_before_run(), wt, tmp_path)

    assert rc == 0 and "pnpm install --frozen-lockfile" in calls, calls
    assert not (wt / "node_modules").is_symlink(), (
        "pnpm ran with node_modules still symlinked into the SHARED npm install — it would "
        "rewrite the directory every still-npm worktree reads"
    )
    assert (shared / "jsdom").is_dir(), "the shared install's contents were deleted"


def test_before_run_still_provisions_a_pre_migration_npm_branch(tmp_path):
    """A rework of a branch forked before CMX-388: no pnpm-lock.yaml, its OWN copy of the
    old shared-npm script. ``pnpm install --frozen-lockfile`` would fail the hook there."""
    wt = _tree(tmp_path / "wt", "package-lock.json")
    (wt / "scripts").mkdir()
    legacy = wt / "scripts" / "npm-shared-install.sh"
    legacy.write_text('#!/bin/sh\necho "legacy-npm-shared-install" >> "$CALLS"\n')
    legacy.chmod(0o755)

    rc, calls = _run_hook(_before_run(), wt, tmp_path)

    assert rc == 0, calls
    assert "legacy-npm-shared-install" in calls
    assert not any(c.startswith("pnpm ") for c in calls), calls


@pytest.mark.parametrize("failing", ["uv", "pnpm"])
def test_before_run_fails_the_launch_when_a_step_fails(tmp_path, failing):
    """The hook is multi-line; without ``set -e`` only its LAST command's status reaches
    ``check=True``, and a failed ``uv sync`` would launch an agent into a broken venv."""
    wt = _tree(tmp_path / "wt", "pnpm-lock.yaml")
    rc, _ = _run_hook(_before_run(), wt, tmp_path, **{f"FAIL_{failing.upper()}": "1"})
    assert rc != 0, f"a failing `{failing}` did not fail before_run"


def test_the_migration_shim_installs_with_pnpm(tmp_path):
    """The pre-CMX-388 daemon's hook still calls ``scripts/npm-shared-install.sh`` — the
    branch's OWN copy — on every launch of this branch (its judge included) until the daemon
    is restarted on the merge. That copy must do the pnpm install, not fail or no-op."""
    wt = _tree(tmp_path / "wt", "pnpm-lock.yaml")
    shared = tmp_path / ".npm-shared" / "node_modules"
    shared.mkdir(parents=True)
    (wt / "node_modules").symlink_to(shared)
    rc, calls = _run_hook(str(ROOT / "scripts" / "npm-shared-install.sh"), wt, tmp_path)

    assert rc == 0
    assert calls == ["pnpm install --frozen-lockfile"]
    assert not (wt / "node_modules").is_symlink()
