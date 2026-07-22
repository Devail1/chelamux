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
# node_modules to it. Re-installs only when package-lock.json actually changed.
set -euo pipefail

if [ ! -f package-lock.json ]; then
  exit 0    # no lockfile to install from — nothing declared, nothing to share (CMX-151)
fi

WORKTREE_DIR="$(pwd)"
SHARED_ROOT="$(dirname "$WORKTREE_DIR")/.npm-shared"
LOCK_FILE="$SHARED_ROOT/.install.lock"

mkdir -p "$SHARED_ROOT"

# flock serializes concurrent worktrees hitting this at once (dispatcher concurrency > 1) —
# without it, two agents launched together could both see "no shared node_modules yet" and
# run `npm ci` into the same target directory simultaneously.
(
  flock -w 300 9 || { echo "npm-shared-install: timed out waiting for $LOCK_FILE" >&2; exit 1; }

  if ! cmp -s package-lock.json "$SHARED_ROOT/package-lock.json" 2>/dev/null \
     || [ ! -d "$SHARED_ROOT/node_modules" ]; then
    cp package.json package-lock.json "$SHARED_ROOT/"
    ( cd "$SHARED_ROOT" && npm ci --no-audit --no-fund --silent )
  fi
) 9>"$LOCK_FILE"

# Not `npm ci --prefix "$SHARED_ROOT"` run from the worktree: npm still writes its OWN
# node_modules next to the package.json it resolves against (the worktree's), prefix or
# not. A symlink is what makes node resolve straight into the shared install.
rm -rf node_modules
ln -s "$SHARED_ROOT/node_modules" node_modules
