#!/usr/bin/env bash
# ⚖️🧹 The `chela judge sweep` GitHub Action's run step (action.yml, CMX-97).
#
# Inputs arrive as env (never interpolated into this script): INPUT_BASE, INPUT_TEST_CMD,
# INPUT_COMMENT, PR_BASE_SHA, CHELA_SWEEP_BIN. The exit code is the sweep's own:
# 0 clean · 1 survivors · 2 cannot verify · 3 unknown (unsupported language changed).
set -uo pipefail

base="${INPUT_BASE:-${PR_BASE_SHA:-}}"
if [ -z "$base" ]; then
  echo "::error::chela judge sweep: no base to diff against — run on pull_request, or set the 'base' input"
  exit 2
fi

# The sweep diffs against the merge base, so it needs history: a shallow checkout is
# deepened and a missing base fetched. A fetch that fails is not fatal here — the sweep
# itself then reports the base as unresolvable (CANNOT VERIFY, exit 2), never a pass.
if [ "$(git rev-parse --is-shallow-repository 2>/dev/null)" = "true" ]; then
  git fetch --quiet --no-tags --unshallow origin || true
fi
if ! git cat-file -e "${base}^{commit}" 2>/dev/null; then
  git fetch --quiet --no-tags origin "$base" || true
fi

args=(judge sweep --base "$base" --github)
if [ -n "${INPUT_TEST_CMD:-}" ]; then
  args+=(--test-cmd "$INPUT_TEST_CMD")
fi
if [ "${INPUT_COMMENT:-true}" != "true" ]; then
  args+=(--no-comment)
fi
exec "${CHELA_SWEEP_BIN:-chela}" "${args[@]}"
