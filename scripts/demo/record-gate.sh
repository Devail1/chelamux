#!/usr/bin/env bash
# Re-record the merge-gate demo (judge catches a weak test → merge refused → rework → merged,
# or one Telegram tap). Run from anywhere:
#
#   scripts/demo/record-gate.sh
#
# 1. gate.py   — a throwaway repo with a deliberately weak test; chela's REAL judge, merge
#                gate and Telegram card formatter run against it (GitHub faked) → scenes.json.
# 2. gate.mjs  — Playwright renders each scene to frames, on the script's clock.
# 3. ffmpeg    — a few seconds of the Wall (the head of landing/chela-demo-desktop.mp4, so
#                re-run record.sh first after a Wall change), then the gate:
#                landing/chela-demo-gate.mp4 (+ its poster) and a palette GIF (≤ 8 MB) for
#                the README.
#
# Needs: `uv sync --all-extras`, `pnpm install` + Playwright's Chromium, ffmpeg.
# ⛔ Review the stills under $OUT/stills before committing new media: the repo is public.
set -euo pipefail

REPO="$(cd "$(dirname "$0")/../.." && pwd)"
PY="${REPO}/.venv/bin/python"
OUT="${OUT:-$(mktemp -d /tmp/chela-demo-gate-XXXXXX)}"
GIF_MAX=$((8 * 1024 * 1024))
MP4="$REPO/landing/chela-demo-gate.mp4"
GIF="$REPO/docs/img/chela-demo-gate.gif"

"$PY" "$REPO/scripts/demo/gate.py" "$OUT"
(cd "$REPO" && node scripts/demo/gate.mjs "$OUT/scenes.json" "$OUT")

ffmpeg -loglevel error -y -t 5 -i "$REPO/landing/chela-demo-desktop.mp4" \
    -f concat -safe 0 -i "$OUT/gate.frames/frames.txt" -filter_complex \
    "[0:v]scale=1440:900,fps=30,setsar=1[a];[1:v]fps=30,setsar=1[b];[a][b]concat=n=2:v=1[v]" \
    -map "[v]" -c:v libx264 -preset slow -crf 24 -pix_fmt yuv420p -movflags +faststart "$MP4"

fps=10
while :; do
    ffmpeg -loglevel error -y -i "$MP4" -vf \
        "fps=${fps},scale=960:-1:flags=lanczos,split[a][b];[a]palettegen=stats_mode=diff:max_colors=128[p];[b][p]paletteuse=dither=bayer:bayer_scale=4:diff_mode=rectangle" \
        "$GIF"
    (( $(stat -c %s "$GIF") <= GIF_MAX )) && break
    (( fps > 4 )) || { echo "$GIF is still over 8 MB at ${fps}fps" >&2; exit 1; }
    fps=$((fps - 2))
done
# The landing poster: the judge's verdict, the frame the clip is about.
ffmpeg -loglevel error -y -ss 14 -i "$MP4" -frames:v 1 -q:v 4 "$REPO/landing/img/poster-gate.jpg"
mkdir -p "$OUT/stills"
ffmpeg -loglevel error -y -i "$MP4" -vf fps=1 "$OUT/stills/gate-%02d.png"
echo "gate: $(du -h "$MP4" | cut -f1) mp4, $(du -h "$GIF" | cut -f1) gif (${fps}fps); stills for review: $OUT/stills"
