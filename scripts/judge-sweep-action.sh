#!/usr/bin/env bash
# ⚖️🧹 The `chela judge sweep` GitHub Action's run step (action.yml, CMX-97).
#
# Inputs arrive as env, never interpolated. The exit code is the sweep's own (exec).
set -uo pipefail

base="${INPUT_BASE:-${PR_BASE_SHA:-}}"
if [ -z "$base" ]; then
  echo "::error::chela judge sweep: no base to diff against — run on pull_request, or set the 'base' input"
  exit 2
fi

# Deepen a shallow checkout, fetch a missing base. A failed fetch is not fatal: the sweep
# then reports the base as unresolvable (CANNOT VERIFY, exit 2), never a pass.
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
