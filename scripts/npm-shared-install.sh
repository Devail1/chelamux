#!/usr/bin/env bash
# npm-shared-install.sh — ONE shared node_modules for every worktree instead of one npm ci
# PER worktree.
#
# `npm ci` unpacks tarballs from npm's cache into node_modules on every run — there is no
# hardlink-from-cache path like uv's (which is why `uv sync` in hooks.before_run is cheap
# even N-worktrees-wide, and npm needed this script). So N concurrent worktrees running
# `npm ci` each pay the full unpack cost for jsdom, the repo's one npm dep (dev-only,
# package.json) — 27M of IDENTICAL files, N times over. Not worth a pnpm migration (a
# lockfile + packageManager + CI + machine-install change) for one dev-only test dep — this
# script gets the same result with a symlink.
#
# Called from hooks.before_run in WORKFLOW.md, cwd = the worktree being prepared. Installs
# ONCE into a directory that outlives any single worktree (a sibling of every worktree, so
# it isn't deleted when a worktree is torn down), then symlinks this worktree's
# node_modules to it. Re-installs only when package-lock.json actually changed, or the
# packages package.json declares don't actually resolve out of the shared install.
set -euo pipefail

if [ ! -f package-lock.json ]; then
  exit 0    # no lockfile to install from — nothing declared, nothing to share (CMX-151)
fi

WORKTREE_DIR="$(pwd)"
SHARED_ROOT="$(dirname "$WORKTREE_DIR")/.npm-shared"
LOCK_FILE="$SHARED_ROOT/.install.lock"
# The chela checkout this script itself ships in — `../chela` next to wherever `$0` lives —
# not $WORKTREE_DIR: a unit test invokes this script by its ROOT path with a throwaway
# fixture dir as cwd, and that fixture never carries its own chela/ package.
REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"

mkdir -p "$SHARED_ROOT"

# #508 was "present-but-empty reads as installed forever". Its own fix — `-z "$(ls -A ...)"`
# instead of a plain `-d` — still asks "is the directory empty", and a PARTIALLY unpacked
# shared node_modules (#508's own cited cause: an interrupted `npm ci`) is non-empty, so it
# still read as installed while the package actually needed was never unpacked. The real
# question was always "do the packages package.json declares resolve", which is exactly what
# chela/judge.py's provision_suite_env asks before running the suite — reuse its two helpers
# (stdlib-only: json + pathlib, so no venv/PYTHONPATH dance needed) instead of a second,
# shell-side reimplementation of "what does package.json declare".
resolves_every_declared_package() {
  local worktree="$1" shared_root="$2" repo_root="$3"
  python3 - "$worktree" "$shared_root" "$repo_root" <<'PYEOF'
import sys
from pathlib import Path

worktree, shared_root, repo_root = (Path(p) for p in sys.argv[1:4])
sys.path.insert(0, str(repo_root))
from chela.judge import declared_npm_packages, _unresolvable

names = declared_npm_packages(worktree)
missing = _unresolvable(shared_root, names)
sys.exit(1 if missing else 0)
PYEOF
}

# flock serializes concurrent worktrees hitting this at once (dispatcher concurrency > 1) —
# without it, two agents launched together could both see "no shared node_modules yet" and
# run `npm ci` into the same target directory simultaneously.
(
  flock -w 300 9 || { echo "npm-shared-install: timed out waiting for $LOCK_FILE" >&2; exit 1; }

  if ! cmp -s package-lock.json "$SHARED_ROOT/package-lock.json" 2>/dev/null \
     || ! resolves_every_declared_package "$WORKTREE_DIR" "$SHARED_ROOT" "$REPO_ROOT"; then
    cp package.json package-lock.json "$SHARED_ROOT/"
    ( cd "$SHARED_ROOT" && npm ci --no-audit --no-fund --silent )
  fi
) 9>"$LOCK_FILE"

# Not `npm ci --prefix "$SHARED_ROOT"` run from the worktree: npm still writes its OWN
# node_modules next to the package.json it resolves against (the worktree's), prefix or
# not. A symlink is what makes node resolve straight into the shared install.
rm -rf node_modules
ln -s "$SHARED_ROOT/node_modules" node_modules
