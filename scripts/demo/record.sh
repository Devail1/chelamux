#!/usr/bin/env bash
# Re-record the README / landing demos from a SYNTHETIC fleet. Run from anywhere:
#
#   scripts/demo/record.sh            # both clips
#   DEMO_ONLY=mobile scripts/demo/record.sh
#
# 1. fleet.py up    — a throwaway fleet on its own tmux server (`tmux -L chela-demo`),
#                     its own HOME + CHELA_DIR under a temp dir, its own dashboard on a
#                     free loopback port. Nothing of your real fleet is reachable.
# 2. record.mjs     — Playwright drives it: desktop 1440×900 (Wall → Work → Ctrl+,
#                     Settings), phone 390×844 (pill switcher → "+" New session → keybar).
# 3. ffmpeg         — MP4 for the landing page, palette GIF (≤ 8 MB) for the README,
#                     written over the old files at the same paths.
# 4. fleet.py down  — always, even on failure.
#
# Needs: the dashboard extra (`uv sync --all-extras`), `pnpm install` + Playwright's
# Chromium, ttyd, tmux and ffmpeg. ⛔ Review every frame before committing new media:
# the repo is public (`frames/` stills are kept under $OUT for exactly that).
set -euo pipefail

REPO="$(cd "$(dirname "$0")/../.." && pwd)"
PY="${REPO}/.venv/bin/python"
OUT="${OUT:-$(mktemp -d /tmp/chela-demo-rec-XXXXXX)}"
GIF_MAX=$((8 * 1024 * 1024))

cleanup() { "$PY" "$REPO/scripts/demo/fleet.py" down || true; }
trap cleanup EXIT

URL="$("$PY" "$REPO/scripts/demo/fleet.py" up)"
echo "demo fleet: $URL"
(cd "$REPO" && node scripts/demo/record.mjs "$URL" "$OUT")

encode() {  # encode <name> <gif-width> <mp4-dest> <gif-dest>
    local name="$1" gw="$2" mp4="$3" gif="$4" dir="$OUT/$1.frames"
    [[ -f "$dir/frames.txt" ]] || return 0
    ffmpeg -loglevel error -y -f concat -safe 0 -i "$dir/frames.txt" \
        -fps_mode cfr -r 30 -c:v libx264 -preset slow -crf 24 -pix_fmt yuv420p \
        -movflags +faststart -vf "pad=ceil(iw/2)*2:ceil(ih/2)*2" "$mp4"
    local fps=12
    while :; do
        ffmpeg -loglevel error -y -i "$mp4" -vf \
            "fps=${fps},scale=${gw}:-1:flags=lanczos,split[a][b];[a]palettegen=stats_mode=diff:max_colors=128[p];[b][p]paletteuse=dither=bayer:bayer_scale=4:diff_mode=rectangle" \
            "$gif"
        (( $(stat -c %s "$gif") <= GIF_MAX )) && break
        (( fps > 6 )) || { echo "$gif is still over 8 MB at ${fps}fps" >&2; exit 1; }
        fps=$((fps - 2))
    done
    # Stills for the privacy review: one frame per second.
    mkdir -p "$OUT/stills"
    ffmpeg -loglevel error -y -i "$mp4" -vf fps=1 "$OUT/stills/${name}-%02d.png"
    echo "$name: $(du -h "$mp4" | cut -f1) mp4, $(du -h "$gif" | cut -f1) gif (${fps}fps)"
}

encode desktop 960 "$REPO/landing/chela-demo-desktop.mp4" "$REPO/docs/img/chela-demo-desktop.gif"
encode mobile 390 "$REPO/landing/chela-demo-mobile.mp4" "$REPO/docs/img/chela-demo-mobile.gif"
echo "stills for review: $OUT/stills"
