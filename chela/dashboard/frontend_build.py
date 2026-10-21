"""CMX-177: the dashboard frontend's build is COMMITTED, so users never need Node.

``vite build`` writes hashed files plus ``manifest.json`` to ``static/app/``, which Flask
already serves under ``static/v/<ASSET_VERSION>/app/…`` with the same cache headers as every
other asset. The output is committed (not built only at release time) because a dev-from-
source install (``git clone`` + ``uv sync``) must serve it with no Node either; a wheel and
an sdist carry it like any other tracked file. What keeps it honest is :func:`check`: build
``src/`` into a temp dir and compare it byte for byte with what is committed. pytest and CI
run it, and ``chela release`` rebuilds before it commits.

Stdlib only, so CI's frontend job runs it as a script: ``python3 chela/dashboard/frontend_build.py``.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

FRONTEND = Path(__file__).parent / "frontend"
BUILT = Path(__file__).parent / "static" / "app"
VITE = Path("node_modules") / "vite" / "bin" / "vite.js"


def build(out_dir: Path, *, frontend: Path = FRONTEND, timeout: float = 300) -> subprocess.CompletedProcess:
    """``vite build`` of ``frontend`` into ``out_dir`` — never raises; no node is a failure."""
    node = shutil.which("node")
    argv = [node or "node", str(frontend / VITE), "build", "--outDir", str(out_dir),
            "--emptyOutDir"]
    if not node:
        return subprocess.CompletedProcess(argv, 127, "", "node is not on PATH")
    env = dict(os.environ)
    env.pop("NODE_CHANNEL_FD", None)    # CMX-252: a leaked IPC fd can abort node
    try:
        return subprocess.run(argv, cwd=frontend, capture_output=True, text=True,
                              timeout=timeout, env=env)
    except (OSError, subprocess.TimeoutExpired) as e:
        return subprocess.CompletedProcess(argv, 1, "", str(e))


def _files(root: Path) -> dict[str, bytes]:
    return {p.relative_to(root).as_posix(): p.read_bytes()
            for p in root.rglob("*") if p.is_file()} if root.is_dir() else {}


def stale(built: Path, fresh: Path) -> list[str]:
    """Every path whose bytes differ between the two trees, or that only one of them has."""
    a, b = _files(built), _files(fresh)
    return sorted(p for p in a.keys() | b.keys() if a.get(p) != b.get(p))


def check(*, frontend: Path = FRONTEND, built: Path = BUILT) -> list[str]:
    """What is stale in ``built`` against a fresh build of ``frontend`` — ``[]`` when current.
    A build that fails or writes nothing is a problem, never "fresh"."""
    with tempfile.TemporaryDirectory() as tmp:
        fresh = Path(tmp) / "app"
        proc = build(fresh, frontend=frontend)
        if proc.returncode != 0:
            return [f"vite build failed ({proc.returncode}): {(proc.stderr or proc.stdout)[-800:]}"]
        if not (fresh / "manifest.json").is_file():
            return ["vite build wrote no manifest.json"]
        return stale(built, fresh)


def main() -> int:
    problems = check()
    for p in problems:
        print(f"stale: {p}")
    if problems:
        print(f"{BUILT} does not match a fresh build of {FRONTEND / 'src'}. Rebuild and "
              "commit it: pnpm -C chela/dashboard/frontend build")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
