#!/usr/bin/env bash
# smoke-fresh-install.sh — CMX-263: prove `chela update` and `chela doctor` actually work
# for a brand-new adopter, in an environment nothing else has touched.
#
# Every install anyone has run this on predates months of changes, so "it worked when I
# set it up" is not evidence about current `dev`/`main` — this exists so that claim can be
# checked instead of assumed. It clones a fresh checkout into an isolated temp dir, syncs
# it, and runs the two commands an adopter reaches for first: `chela doctor` (is my install
# healthy?) and `chela update` (bring it up to date). Neither command running is enough —
# an uncaught exception still exits nonzero same as a reported problem would, so this
# distinguishes "ran and reported findings" from "crashed" by scanning for a real Python
# traceback, not just the exit code.
#
# Usage:
#   scripts/smoke-fresh-install.sh                          # clones the real GitHub repo
#   scripts/smoke-fresh-install.sh /path/to/local/checkout   # clones a local path instead
#                                                             # (offline — what the pytest
#                                                             # wrapper in
#                                                             # tests/test_smoke_fresh_install.py
#                                                             # uses, so CI never needs
#                                                             # network for this)
set -euo pipefail

SOURCE="${1:-https://github.com/Devail1/chelamux}"

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

CLONE="$WORK/chelamux"
echo "==> cloning $SOURCE -> $CLONE"
git clone --quiet "$SOURCE" "$CLONE"

# Isolate the app-level state an install reads/writes — NOT $HOME wholesale: leaving $HOME
# alone lets `uv sync` reuse the real machine's package cache (hardlink, not re-download —
# see scripts/npm-shared-install.sh's header for why that matters), and lets `git` keep
# using the real user's config.
#
# On a box that already runs a live chela install (e.g. this project's own dev machine),
# the calling shell carries its OWN `CHELA_*` vars (CHELA_DISPATCH_WORKFLOWS,
# CHELA_TMUX_SESSION, CHELA_DASHBOARD_PORT, …) — confirmed live: an early version of this
# script that only overrode CHELA_DIR still had `chela doctor` read the real
# CHELA_DISPATCH_WORKFLOWS and print this developer's actual dispatched-repo paths. A
# fresh adopter's shell has none of these set, so this run must not either — strip every
# inherited `CHELA_*` var before setting the three this script itself needs.
while IFS='=' read -r name _; do
    case "$name" in CHELA_*) unset "$name" ;; esac
done < <(env)

# CHELA_ENV_FILE="" turns off chela.env sourcing exactly like tests/conftest.py does, so
# this run can never read (or corrupt) a real adopter's config.
export CHELA_DIR="$WORK/chela-state"
export CLAUDE_CONFIG_DIR="$WORK/claude-config"
export CHELA_ENV_FILE=""
mkdir -p "$CHELA_DIR" "$CLAUDE_CONFIG_DIR"

cd "$CLONE"

fail=0

echo "==> uv sync --all-extras"
if ! uv sync --all-extras --quiet; then
    echo "FAIL: uv sync did not succeed on a fresh clone" >&2
    exit 1
fi

# Runs one `chela` subcommand and enforces the "ran vs. crashed" distinction described
# above. Prints the command's own output either way so a real failure is diagnosable from
# this script's own log, not just a bare non-zero exit.
run_step() {
    local label="$1"
    shift
    echo "==> $label"
    local out
    local rc=0
    out="$(uv run chela "$@" 2>&1)" || rc=$?
    echo "$out"
    if grep -q "Traceback (most recent call last):" <<<"$out"; then
        echo "FAIL: $label crashed (traceback above) instead of reporting cleanly" >&2
        fail=1
        return
    fi
    if [ "$rc" -gt 1 ]; then
        echo "FAIL: $label exited $rc — only 0 (clean) or 1 (findings/refusal reported) is expected" >&2
        fail=1
    fi
}

run_step "chela doctor" doctor
run_step "chela update --check" update --check
run_step "chela update" update

if [ "$fail" -ne 0 ]; then
    echo "FAIL: fresh-install smoke test found a problem above" >&2
    exit 1
fi

echo "PASS: fresh-install smoke test — doctor + update both ran cleanly against a fresh clone"
