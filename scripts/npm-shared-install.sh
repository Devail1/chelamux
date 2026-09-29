#!/usr/bin/env bash
# npm-shared-install.sh — CMX-388 MIGRATION SHIM. The shared-npm install it used to be
# (CMX-151) is gone: the repo installs with pnpm, whose store hardlinks one copy of each
# package into every worktree. Delete this file once the running daemon has loaded a
# WORKFLOW.md whose `hooks.before_run` no longer names it (i.e. after `chela-daemon` is
# restarted on the CMX-388 merge).
#
# Why it still exists: the daemon runs `before_run` out of the WORKFLOW.md it LOADED, never
# the branch's copy — and pre-CMX-388 that line is `... && scripts/npm-shared-install.sh`,
# run with cwd = the worktree, so it executes THIS branch's copy of this file, `check=True`.
# Deleted, every launch of a pnpm branch (its judge included) would die in the hook. So it
# does what the new `before_run` does instead.
set -euo pipefail

# A worktree a pre-CMX-388 hook built has node_modules as a SYMLINK into the shared npm
# install other still-npm worktrees read; pnpm would rewrite that directory in place.
if [ -L node_modules ]; then rm node_modules; fi

if [ -f pnpm-lock.yaml ]; then
  exec pnpm install --frozen-lockfile
fi
