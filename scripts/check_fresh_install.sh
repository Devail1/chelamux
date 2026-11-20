#!/usr/bin/env bash
# check_fresh_install.sh — CMX-92: run the README install block, verbatim, on a clean
# Ubuntu container, and time it.
#
# What "clean" means here: a stock ubuntu image with ONLY the documented prerequisites
# (git, tmux, curl, uv) installed, run as your own uid (`--user $(id -u):$(id -g)`, so
# nothing root-owned is left behind), with no `~/.chela`, no tmux socket and no port of
# this machine reachable. `claude` is a stub on PATH: the check needs a working wall, not a
# Claude login. The one substitution: the block's GitHub URL is replaced by a clone of the
# source given as $1 (default: this checkout's HEAD), so the check tests YOUR commit, not
# whatever is on GitHub. It is pinned to that commit's sha (`file:///src@<sha>`), and the
# install runs on Python $CHELA_FRESH_INSTALL_PYTHON (default 3.13).
#
# CMX-444: every network-bound phase runs under its own deadline, and each is timed:
#   apt           — the prereqs image's apt-get layer
#   uv installer  — the prereqs image's astral uv-installer layer
#   git install   — the block's `uv tool install` (until `chela` is on PATH)
#   dashboard up  — until the dashboard answers /api/agents with 200
# A phase that runs out its deadline exits 75 and NAMES the phase: "infra: did not
# complete" is not a pass, and not a code failure either. CHELA_FRESH_INSTALL_TIMEOUT=<s>
# sets every phase's deadline at once (the defaults are below).
#
# The prereqs image is tagged by a hash of its two Dockerfiles, and a run that finds that
# tag already present skips both build phases (CI restores it from a cache);
# `--image-tag` prints the tag and exits.
#
# Exit codes: 0 pass · 1 fail · 75 INFRA: a phase did not complete in time ·
# 77 SKIPPED (no usable docker/podman). A skip and a timeout each say so.
#
#   scripts/check_fresh_install.sh [source-repo]
#   scripts/check_fresh_install.sh --image-tag
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
GITHUB_URL="https://github.com/Devail1/chelamux"
PORT=5001

# The README install block, byte for byte. tests/test_check_fresh_install.py fails if it
# and README.md (or docs/GETTING_STARTED.md) drift apart.
# BEGIN INSTALL BLOCK
INSTALL_BLOCK='uv tool install "chelamux[dashboard] @ git+https://github.com/Devail1/chelamux"
tmux new-session -d -s chela -n researcher     # a window = an agent
chela run > ~/chela-run.log 2>&1 &             # the daemon (the engine)
chela dashboard                                # the wall: http://127.0.0.1:5001'
# END INSTALL BLOCK

# Only the documented prerequisites, in two layers so a hang names which fetch hung.
APT_DOCKERFILE='FROM ubuntu:24.04
RUN apt-get update && apt-get install -y --no-install-recommends git tmux curl ca-certificates \
 && rm -rf /var/lib/apt/lists/*'
UV_DOCKERFILE='ARG BASE
FROM $BASE
RUN curl -LsSf https://astral.sh/uv/install.sh | env UV_INSTALL_DIR=/usr/local/bin UV_NO_MODIFY_PATH=1 sh'
# Computed only when needed: the no-runtime SKIP below must run with nothing on PATH.
tag_images() {
    local hash
    hash="$(printf '%s\n--\n%s\n' "$APT_DOCKERFILE" "$UV_DOCKERFILE" | sha256sum | cut -c1-12)"
    APT_IMAGE="chela-fresh-install-apt:$hash"
    IMAGE="chela-fresh-install-prereqs:$hash"
}

if [ "${1:-}" = "--image-tag" ]; then
    tag_images
    echo "$IMAGE"
    exit 0
fi
SOURCE="${1:-$ROOT}"

# Per-phase deadlines, in seconds. Their sum (900) is the old whole-check budget.
T_APT="${CHELA_FRESH_INSTALL_TIMEOUT:-240}"
T_UV="${CHELA_FRESH_INSTALL_TIMEOUT:-120}"
T_GIT="${CHELA_FRESH_INSTALL_TIMEOUT:-420}"
T_DASH="${CHELA_FRESH_INSTALL_TIMEOUT:-120}"

# The Python the install runs on: the newest one CI's unit suite tests, not whatever uv
# finds newest that day (it once picked an untested cpython 3.15).
PYTHON="${CHELA_FRESH_INSTALL_PYTHON:-3.13}"

RUNTIME="${CHELA_CONTAINER_RUNTIME:-}"
if [ -z "$RUNTIME" ]; then
    for candidate in docker podman; do
        if command -v "$candidate" >/dev/null && "$candidate" info >/dev/null 2>&1; then
            RUNTIME="$candidate"
            break
        fi
    done
fi
if [ -z "$RUNTIME" ]; then
    echo "SKIP: no usable container runtime (docker or podman) — the fresh-install check did NOT run"
    exit 77
fi

tag_images
WORK="$(mktemp -d)"
NAME="chela-fresh-install-$$"
BLOCK_PID=""
cleanup() {
    if [ -n "$BLOCK_PID" ]; then kill "$BLOCK_PID" >/dev/null 2>&1 || true; fi
    "$RUNTIME" rm -f "$NAME" >/dev/null 2>&1 || true
    rm -rf "$WORK"
}
trap cleanup EXIT

TIMINGS=""
record() { TIMINGS="${TIMINGS}phase ${1}: ${2}s"$'\n'; }

timed_out() {  # <phase> <limit>
    echo "TIMEOUT: phase '$1' did not complete within ${2}s — infra: did not complete (NOT a pass)" >&2
    printf '%s' "$TIMINGS" >&2
    if [ -s "$WORK/build.log" ]; then cat "$WORK/build.log" >&2; fi
    if [ -s "$WORK/block.log" ]; then cat "$WORK/block.log" >&2; fi
    exit 75
}

build_phase() {  # <phase> <limit> <tag> <dockerfile> [build args...]
    local phase="$1" limit="$2" tag="$3" dockerfile="$4" start=$SECONDS rc=0
    shift 4
    timeout -k 10 "$limit" "$RUNTIME" build -q -t "$tag" "$@" - >"$WORK/build.log" 2>&1 \
        <<<"$dockerfile" || rc=$?
    if [ "$rc" -eq 124 ] || [ "$rc" -eq 137 ]; then
        timed_out "$phase" "$limit"
    fi
    if [ "$rc" -ne 0 ]; then
        echo "FAIL: phase '$phase' — the image build failed (exit $rc)" >&2
        cat "$WORK/build.log" >&2
        exit 1
    fi
    record "$phase" $(( SECONDS - start ))
}

wait_for() {  # <phase> <limit> <probe command...> — polls until the probe succeeds
    local phase="$1" limit="$2" start=$SECONDS
    shift 2
    until "$@"; do
        if ! kill -0 "$BLOCK_PID" 2>/dev/null; then
            echo "FAIL: the install block exited during phase '$phase'" >&2
            cat "$WORK/block.log" >&2
            exit 1
        fi
        if [ $(( SECONDS - start )) -ge "$limit" ]; then
            timed_out "$phase" "$limit"
        fi
        sleep 1
    done
    record "$phase" $(( SECONDS - start ))
}

if "$RUNTIME" image inspect "$IMAGE" >/dev/null 2>&1; then
    echo "==> prerequisites: cached image $IMAGE"
    prereqs="cached"
else
    build_phase "apt" "$T_APT" "$APT_IMAGE" "$APT_DOCKERFILE"
    build_phase "uv installer" "$T_UV" "$IMAGE" "$UV_DOCKERFILE" --build-arg "BASE=$APT_IMAGE"
    prereqs="built"
fi

git clone --quiet "$SOURCE" "$WORK/src"
# Pin the install to the cloned commit. Unpinned, uv resolves the repo's default branch via
# `origin/HEAD`, and a clone of CI's checkout (shallow, detached, no branch) has none:
# "failed to find default branch `HEAD`".
SHA="$(git -C "$WORK/src" rev-parse HEAD)"
BLOCK="${INSTALL_BLOCK//"$GITHUB_URL"/file:///src@$SHA}"

"$RUNTIME" run -d --name "$NAME" --user "$(id -u):$(id -g)" \
    -e HOME=/tmp/home -e PATH=/tmp/home/.local/bin:/usr/local/bin:/usr/bin:/bin \
    -e UV_PYTHON="$PYTHON" \
    -v "$WORK/src:/src:ro" "$IMAGE" sleep infinity >/dev/null
in_box() { "$RUNTIME" exec "$NAME" bash -c "$1"; }
in_box 'mkdir -p ~/.local/bin && printf "#!/bin/sh\nexit 0\n" > ~/.local/bin/claude && chmod +x ~/.local/bin/claude'

# Each probe has its own short deadline: a probe that never returns (a stalled `exec`)
# would otherwise hold `wait_for` past its phase deadline.
T_PROBE="${CHELA_FRESH_INSTALL_PROBE_TIMEOUT:-10}"
probe() { timeout -k 5 "$T_PROBE" "$RUNTIME" exec "$NAME" bash -c "$1"; }
chela_installed() { probe 'command -v chela' >/dev/null 2>&1; }
dashboard_up() {
    [ "$(probe "curl -s --max-time 5 -o /dev/null -w '%{http_code}' http://127.0.0.1:$PORT/api/agents" 2>/dev/null)" = "200" ]
}

echo "==> running the README install block ($(grep -c . <<<"$INSTALL_BLOCK") commands)"
t0=$SECONDS
"$RUNTIME" exec "$NAME" bash -c "$BLOCK" >"$WORK/block.log" 2>&1 &
BLOCK_PID=$!
wait_for "git install" "$T_GIT" chela_installed
wait_for "dashboard up" "$T_DASH" dashboard_up
t_install=$(( SECONDS - t0 ))

echo "==> chela doctor"
if ! in_box 'chela doctor'; then
    echo "FAIL: chela doctor reported a required check failing" >&2
    exit 1
fi

echo
printf '%s' "$TIMINGS"
echo "prerequisites: ${prereqs} ($IMAGE)"
echo "install -> dashboard up: ${t_install}s"
echo "commands a human types: $(grep -c . <<<"$INSTALL_BLOCK")"
echo "PASS: fresh install on $RUNTIME ubuntu:24.04"
