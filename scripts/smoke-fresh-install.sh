#!/usr/bin/env bash
# smoke-fresh-install.sh — CMX-263: prove the documented adopter path actually works,
# in an environment nothing else has touched.
#
# Every install anyone has run this on predates months of changes, so "it worked when I
# set it up" is not evidence about current `dev`/`main` — this exists so that claim can be
# checked instead of assumed. It clones a fresh checkout into an isolated temp dir, syncs
# it, and walks the documented adopter order (see README.md / skills/chela-setup): clean
# env -> plugin render -> first dashboard -> doctor -> update -> dispatch --dry-run ->
# teardown. Neither a command running nor a non-zero exit is enough on its own — an
# uncaught exception still exits non-zero same as a reported problem would, so this
# distinguishes "ran and reported findings" from "crashed" by scanning for a real Python
# traceback, not just the exit code (verified live: a malformed workflow file fed to
# `chela dispatch --dry-run` produces a genuine traceback, and this harness catches it).
#
# SCOPE BOUNDARY — stated, not fudged: this is the CREDENTIAL-FREE path only (install ->
# setup -> dashboard -> doctor -> update -> dry-run). NOT covered, because it needs live
# Claude Code credentials that must not be baked into a test: a real dispatched agent
# launch, a judge run, a real merge. The two documented plugin-install commands
# (`/plugin marketplace add …` / `/plugin install chela@chela`) are Claude Code REPL slash
# commands with no headless equivalent, so this exercises the scriptable half of that step
# instead — `chela plugin --dir`, the same renderer those slash commands install from —
# and does not claim to cover the interactive half. Do not fake either path to claim
# coverage; a test that pretends to cover what it does not is worse than one that admits
# it does not.
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
DASH_PID=""
HOG_PID=""
cleanup() {
    if [ -n "$DASH_PID" ] && kill -0 "$DASH_PID" 2>/dev/null; then
        kill "$DASH_PID" 2>/dev/null || true
        sleep 1
        kill -9 "$DASH_PID" 2>/dev/null || true
    fi
    if [ -n "$HOG_PID" ] && kill -0 "$HOG_PID" 2>/dev/null; then
        kill -9 "$HOG_PID" 2>/dev/null || true
    fi
    rm -rf "$WORK"
}
trap cleanup EXIT

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
# inherited `CHELA_*` var before setting the ones this script itself needs.
while IFS='=' read -r name _; do
    case "$name" in CHELA_*) unset "$name" ;; esac
done < <(env)

# CHELA_ENV_FILE="" turns off chela.env sourcing exactly like tests/conftest.py does, so
# this run can never read (or corrupt) a real adopter's config.
export CHELA_DIR="$WORK/chela-state"
export CLAUDE_CONFIG_DIR="$WORK/claude-config"
export CHELA_ENV_FILE=""

# Pin an isolated, guaranteed-nonexistent tmux session name — confirmed live this is NOT
# optional. Leaving CHELA_TMUX_SESSION merely unset is unsafe here: `config.current_session()`
# falls back to $TMUX_PANE when unset, and this script itself typically runs INSIDE a real
# chela-managed tmux pane (an autonomous dispatched agent's own window). On this project's
# own dev box that fallback resolves to a `webterm_chela__*` mirror session GROUPED with the
# live `chela` session (same window list) — so an unpinned run would have `chela dashboard`'s
# /api/agents answer with the box's REAL live fleet instead of a fresh adopter's empty one,
# exactly the mirror-session trap this project has hit before (see CLAUDE.md). A nonexistent
# session name makes `tmux list-windows` fail closed to an empty list instead — verified live.
export CHELA_TMUX_SESSION="smoke-fresh-install-$$-nonexistent"
mkdir -p "$CHELA_DIR" "$CLAUDE_CONFIG_DIR"

cd "$CLONE"

fail=0
declare -a not_covered=(
    "interactive plugin install (/plugin marketplace add, /plugin install chela@chela — Claude Code REPL slash commands, no headless equivalent)"
    "a live dispatched agent launch (needs real Claude Code credentials)"
    "a judge run"
    "a real merge"
)

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

# Step 1 (clean environment) is the isolation set up above: no ~/.chela, no
# ~/.claude/plugins entry, no chela tmux session — CHELA_DIR/CLAUDE_CONFIG_DIR both point
# into $WORK, which mktemp guarantees didn't exist before this run.

# Step 1b: prove the CHELA_TMUX_SESSION pin (set above) actually took — not merely that
# it was exported, but that `config.current_session()` resolves to the guaranteed-
# nonexistent name and NOT a real session (this box's live `chela` session, or whatever
# $TMUX_PANE would derive). `chela status` prints the resolved session name verbatim
# ("No windows found in tmux session '<name>'" / "Agents in tmux session '<name>':"), so
# tests/test_smoke_fresh_install.py can assert on it directly instead of trusting the export.
run_step "chela status (verifies the CHELA_TMUX_SESSION pin took effect)" status

# Step 1c (test-only, SMOKE_BREAK_HOLD_TTL=1, unset in the normal adopter path): a genuine
# `chela dispatch --pause --ttl <garbage>` — hold.parse_ttl() raises ValueError, cmd_dispatch_hold
# catches it and does `raise SystemExit(2)`, so this is real production code cleanly exiting
# 2 with NO traceback (verified live: `error: --ttl not a duration: ...`, rc=2). This is the
# other half of run_step()'s "ran vs. crashed" contract: the traceback scan above catches an
# uncaught exception, and this exercises the `rc -gt 1` branch right next to it — a command
# that ran to completion, didn't crash, but still reported more than "findings" (rc 1) or
# "clean" (rc 0). tests/test_smoke_fresh_install.py uses this to prove that branch actually
# fails the run instead of merely existing.
if [ "${SMOKE_BREAK_HOLD_TTL:-0}" = "1" ]; then
    run_step "chela dispatch --pause (bad --ttl, exercises rc>1)" dispatch --pause --ttl not-a-duration
fi

# Step 2: plugin render — the scriptable half of "plugin install by the documented path"
# (see the SCOPE BOUNDARY note above for the interactive half this cannot cover).
run_step "chela plugin --dir (documented offline-render path)" plugin --dir "$WORK/plugin"

# Step 3: first `chela dashboard` — does it start, and does /api/agents answer 200?
DASH_PORT=$(( 20000 + (RANDOM % 20000) ))

# Step 3 fixture (test-only, SMOKE_BREAK_DASHBOARD=1, unset in the normal adopter path):
# genuinely occupies $DASH_PORT *before* `chela dashboard` tries to bind it, so Flask's
# dev server hits a real `OSError: Address already in use`, prints a real traceback to
# dashboard.log, and the process actually dies — it never answers anything, let alone a
# 200. This is the readiness loop's own equivalent of what SMOKE_BREAK_HOLD_TTL /
# SMOKE_BREAK_DISPATCH_WORKFLOW do for run_step()'s two branches: a genuine failure that
# only a correct `= "200"` comparison catches. A neutered comparison (e.g. `!=
# "<sentinel>"`, true for whatever curl prints on a refused connection, such as "000")
# would treat the very first failed probe as success and wrongly report PASS.
# tests/test_smoke_fresh_install.py uses this to prove the comparison actually requires a
# literal 200, not merely "curl produced some output".
if [ "${SMOKE_BREAK_DASHBOARD:-0}" = "1" ]; then
    # Must actually listen(), not just bind(): on Linux, SO_REUSEADDR (which werkzeug's
    # dev server sets too) lets a SECOND bind() to the same port succeed as long as the
    # first socket never called listen() — verified live, an unlistened bind alone does
    # NOT make `chela dashboard`'s own bind() fail. Once this fixture listens(), the real
    # dashboard's bind() gets a genuine EADDRINUSE. Every accepted connection (including
    # curl's, in the readiness loop below) is closed immediately without a reply, so a
    # probe gets an instant, real "connection refused" / empty-reply — never a hang.
    #
    # $DASH_PORT is a random pick out of a 20000-wide range, not a reservation — some
    # other process on the box can already hold it (hit live in CI: the fixture's own
    # bind() raised a genuine `OSError: Address already in use` before it ever got to
    # listen()). That collision is froth on the port picker, not the thing this fixture
    # is trying to prove, so retry with a fresh random port a few times before treating
    # it as this fixture's own failure to bind.
    hog_bound=0
    for hog_attempt in $(seq 1 5); do
        rm -f "$WORK/dashboard-hog-bound"
        python3 -c "
import socket
s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
s.bind(('127.0.0.1', $DASH_PORT))
s.listen(5)
open('$WORK/dashboard-hog-bound', 'w').close()
s.settimeout(1)
while True:
    try:
        conn, _ = s.accept()
        conn.close()
    except socket.timeout:
        continue
" &
        HOG_PID=$!
        for _ in $(seq 1 50); do
            if ! kill -0 "$HOG_PID" 2>/dev/null; then
                break
            fi
            if [ -f "$WORK/dashboard-hog-bound" ]; then
                hog_bound=1
                break
            fi
            sleep 0.1
        done
        if [ "$hog_bound" -eq 1 ]; then
            break
        fi
        DASH_PORT=$(( 20000 + (RANDOM % 20000) ))
    done
    if [ "$hog_bound" -ne 1 ]; then
        echo "FAIL: SMOKE_BREAK_DASHBOARD fixture could not bind a free port after 5 attempts" >&2
        exit 1
    fi
fi

echo "==> chela dashboard (background, isolated port $DASH_PORT)"
CHELA_DASH_HOST=127.0.0.1 CHELA_DASHBOARD_PORT="$DASH_PORT" \
    uv run chela dashboard >"$WORK/dashboard.log" 2>&1 &
DASH_PID=$!
dash_ready=0
for _ in $(seq 1 30); do
    if [ "$(curl -s -o /dev/null -w '%{http_code}' "http://127.0.0.1:$DASH_PORT/api/agents" 2>/dev/null)" = "200" ]; then
        dash_ready=1
        break
    fi
    if ! kill -0 "$DASH_PID" 2>/dev/null; then
        break
    fi
    sleep 1
done
if [ "$dash_ready" -eq 1 ]; then
    echo "chela dashboard: started, /api/agents -> 200"
else
    echo "FAIL: chela dashboard did not answer /api/agents with 200 within 30s" >&2
    echo "--- dashboard.log ---" >&2
    cat "$WORK/dashboard.log" >&2 || true
    fail=1
fi
if [ -n "$DASH_PID" ] && kill -0 "$DASH_PID" 2>/dev/null; then
    kill "$DASH_PID" 2>/dev/null || true
    sleep 1
    kill -9 "$DASH_PID" 2>/dev/null || true
fi
DASH_PID=""
if [ -n "$HOG_PID" ] && kill -0 "$HOG_PID" 2>/dev/null; then
    kill -9 "$HOG_PID" 2>/dev/null || true
fi
HOG_PID=""

run_step "chela doctor" doctor
run_step "chela update --check" update --check
run_step "chela update" update

# Step 6: `chela dispatch --dry-run` against a small fixture tracker — a self-contained
# WORKFLOW.md + TODO.md this script writes into $WORK, never the real tracker. dry_run()
# never touches tmux, hooks, or git (see chela/dispatcher.py::dry_run docstring), so this
# is safe to run against the isolated $CHELA_DIR set up above.
mkdir -p "$WORK/fixture"
cat >"$WORK/fixture/TODO.md" <<'EOF'
# smoke-fresh-install.sh fixture tracker — not a real work queue.

- [ ] **Fixture task — smoke-fresh-install.sh dispatch --dry-run coverage only.**
EOF
# SMOKE_BREAK_DISPATCH_WORKFLOW=1 (test-only, unset in the normal adopter path) writes a
# WORKFLOW.md missing the required `project_key` instead of a valid one, so
# `chela.workflow.load_workflow` raises an uncaught ValueError and `chela dispatch
# --dry-run` produces a genuine Python traceback — exactly the "crashed" case the header
# comment says this harness catches. tests/test_smoke_fresh_install.py uses this to prove
# the traceback scan in run_step() actually fails the run instead of merely existing.
if [ "${SMOKE_BREAK_DISPATCH_WORKFLOW:-0}" = "1" ]; then
    cat >"$WORK/fixture/WORKFLOW.md" <<EOF
---
tracker:
  kind: markdown
  path: TODO.md
workspace:
  root: $WORK/fixture-workspace
  base_branch: main
concurrency:
  max: 1
agent:
  cmd: claude --permission-mode auto
---
Fixture prompt for {{task_title}}.
EOF
else
    cat >"$WORK/fixture/WORKFLOW.md" <<EOF
---
project_key: SMOKE
tracker:
  kind: markdown
  path: TODO.md
workspace:
  root: $WORK/fixture-workspace
  base_branch: main
concurrency:
  max: 1
agent:
  cmd: claude --permission-mode auto
---
Fixture prompt for {{task_title}}.
EOF
fi
run_step "chela dispatch --dry-run (fixture tracker)" dispatch "$WORK/fixture/WORKFLOW.md" --dry-run

# Step 7: teardown — assert nothing root-owned is left behind. Nothing this script runs
# invokes Docker (the adopter-fatal case the documented step guards against), so this is
# necessarily a vacuous pass today; it stays here so a future step that DOES shell out to
# Docker inherits the assertion instead of silently skipping it.
root_owned="$(find "$WORK" \! -user "$(id -u)" 2>/dev/null || true)"
if [ -n "$root_owned" ]; then
    echo "FAIL: root-owned (or other-uid) remnants left behind:" >&2
    echo "$root_owned" >&2
    fail=1
else
    echo "==> teardown: no root-owned remnants (no step here shells out to Docker)"
fi

echo
echo "==> NOT COVERED by this smoke test (see SCOPE BOUNDARY at the top of this file):"
for item in "${not_covered[@]}"; do
    echo "  - $item"
done

if [ "$fail" -ne 0 ]; then
    echo
    echo "FAIL: fresh-install smoke test found a problem above" >&2
    exit 1
fi

echo
echo "PASS: fresh-install smoke test — plugin render, dashboard, doctor, update, and dispatch --dry-run all ran cleanly against a fresh clone"
