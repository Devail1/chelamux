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
# whatever is on GitHub.
#
# Exit codes: 0 pass · 1 fail · 77 SKIPPED (no usable docker/podman). A skip says so.
#
#   scripts/check_fresh_install.sh [source-repo]
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SOURCE="${1:-$ROOT}"
GITHUB_URL="https://github.com/Devail1/chelamux"
IMAGE="chela-fresh-install-prereqs"
PORT=5001

# The README install block, byte for byte. tests/test_check_fresh_install.py fails if it
# and README.md (or docs/GETTING_STARTED.md) drift apart.
# BEGIN INSTALL BLOCK
INSTALL_BLOCK='uv tool install "chelamux[dashboard] @ git+https://github.com/Devail1/chelamux"
tmux new-session -d -s chela -n researcher     # a window = an agent
chela run > ~/chela-run.log 2>&1 &             # the daemon (the engine)
chela dashboard                                # the wall: http://127.0.0.1:5001'
# END INSTALL BLOCK

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

WORK="$(mktemp -d)"
NAME="chela-fresh-install-$$"
cleanup() {
    "$RUNTIME" rm -f "$NAME" >/dev/null 2>&1 || true
    rm -rf "$WORK"
}
trap cleanup EXIT

# Only the documented prerequisites. Built once and cached; its time is reported apart
# from the install, since a visitor may already have them.
t0=$(date +%s)
"$RUNTIME" build -q -t "$IMAGE" - >/dev/null <<'EOF'
FROM ubuntu:24.04
RUN apt-get update && apt-get install -y --no-install-recommends git tmux curl ca-certificates \
 && rm -rf /var/lib/apt/lists/*
RUN curl -LsSf https://astral.sh/uv/install.sh | env UV_INSTALL_DIR=/usr/local/bin UV_NO_MODIFY_PATH=1 sh
EOF
t_prereqs=$(( $(date +%s) - t0 ))

git clone --quiet "$SOURCE" "$WORK/src"
BLOCK="${INSTALL_BLOCK//"$GITHUB_URL"/file:///src}"

"$RUNTIME" run -d --name "$NAME" --user "$(id -u):$(id -g)" \
    -e HOME=/tmp/home -e PATH=/tmp/home/.local/bin:/usr/local/bin:/usr/bin:/bin \
    -v "$WORK/src:/src:ro" "$IMAGE" sleep infinity >/dev/null
in_box() { "$RUNTIME" exec "$NAME" bash -c "$1"; }
in_box 'mkdir -p ~/.local/bin && printf "#!/bin/sh\nexit 0\n" > ~/.local/bin/claude && chmod +x ~/.local/bin/claude'

echo "==> running the README install block ($(grep -c . <<<"$INSTALL_BLOCK") commands)"
t0=$(date +%s)
"$RUNTIME" exec "$NAME" bash -c "$BLOCK" >"$WORK/block.log" 2>&1 &
up=0
for _ in $(seq 1 600); do
    if [ "$(in_box "curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:$PORT/api/agents" 2>/dev/null)" = "200" ]; then
        up=1
        break
    fi
    sleep 1
done
t_install=$(( $(date +%s) - t0 ))
if [ "$up" -ne 1 ]; then
    echo "FAIL: the dashboard never answered /api/agents with 200" >&2
    cat "$WORK/block.log" >&2
    exit 1
fi

echo "==> chela doctor"
if ! in_box 'chela doctor'; then
    echo "FAIL: chela doctor reported a required check failing" >&2
    exit 1
fi

echo
echo "prerequisites: ${t_prereqs}s (cached after the first run)"
echo "install -> dashboard up: ${t_install}s"
echo "commands a human types: $(grep -c . <<<"$INSTALL_BLOCK")"
echo "PASS: fresh install on $RUNTIME ubuntu:24.04"
