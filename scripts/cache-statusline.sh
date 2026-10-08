#!/usr/bin/env bash
# chela — Claude Code statusLine hook.
#
# Claude Code runs this after each assistant turn, piping a JSON status payload
# on stdin: context-window usage, the 5h/7d rate-limit blocks, session cost, and
# the model. None of that is persisted anywhere chela can read on its own (it is
# not in the JSONL transcript), so we cache the payload verbatim to
# $CHELA_DIR/context/<key>.json (<key> = the window name, made filesystem-safe by
# chela/cachekey.py) and $CHELA_DIR/context/by-session/<session_id>.json (CMX-29 —
# the second is the one a window's bar trusts). The dashboard reads these to show each
# agent's context bar and the account-wide rate-limit pills without interrupting
# the agent.
#
# Wire it in (one of):
#   chela install-statusline                       # writes ~/.claude/settings.json
#   # …or by hand, in ~/.claude/settings.json or a repo's .claude/settings.json:
#   { "statusLine": { "type": "command", "command": "/abs/path/cache-statusline.sh" } }
#
# Emits nothing on stdout (an invisible status line) — only the caching side
# effect. chela works without it; the context bar then falls back to a coarser
# estimate derived from the transcript, and the rate-limit pills stay hidden.

CHELA_DIR="${CHELA_DIR:-$HOME/.chela}"
CACHE_DIR="$CHELA_DIR/context"

# A failed cache write must be LOUD (CMX-30). Claude Code never shows a
# statusLine's stderr, so a write that failed used to vanish without a trace —
# for six days every dispatched agent cached nothing and nothing said so. Every
# failure below is appended to $CHELA_DIR/statusline-errors.log, echoed to
# stderr, and exits non-zero.
fail() {
    local msg
    msg="$(date -u +%Y-%m-%dT%H:%M:%SZ) cache-statusline: $*"
    printf '%s\n' "$msg" >&2
    mkdir -p "$CHELA_DIR" 2>/dev/null
    printf '%s\n' "$msg" >> "$CHELA_DIR/statusline-errors.log" 2>/dev/null
    exit 1
}

mkdir -p "$CACHE_DIR" || fail "cannot create $CACHE_DIR"

INPUT=$(cat)

# The agent's display name is its tmux window name — the same key the dashboard
# maps cache files by. Outside tmux there is no window, so skip quietly.
WINDOW_NAME=$(tmux display-message -t "$TMUX_PANE" -p '#{window_name}' 2>/dev/null)
[ -z "$WINDOW_NAME" ] && exit 0

# Claude Code's payload carries cwd/model/context/cost/rate-limits but NOT the
# git branch — so we compute it here (once per turn, from the payload's cwd) and
# splice a top-level "branch" key in. This keeps the dashboard server free of any
# per-request git calls; branch rides the same cache→server→browser path as the
# rest. python3 is always present (chela runs on it); on any failure we fall back
# to the verbatim payload so the context bar never breaks.
ENRICHED=$(printf '%s' "$INPUT" | python3 -c '
import sys, json, subprocess
raw = sys.stdin.read()
try:
    d = json.loads(raw)
    cwd = (d.get("workspace") or {}).get("current_dir") or d.get("cwd")
    if cwd:
        r = subprocess.run(["git", "-C", cwd, "rev-parse", "--abbrev-ref", "HEAD"],
                           capture_output=True, text=True, timeout=2)
        br = r.stdout.strip()
        if br and br != "HEAD":
            d["branch"] = br
    sys.stdout.write(json.dumps(d))
except Exception:
    sys.stdout.write(raw)
' 2>/dev/null)
[ -z "$ENRICHED" ] && ENRICHED="$INPUT"

# The file is keyed by chela/cachekey.py — the SAME mapping chela.context reads
# it back with — never by the raw window name (CMX-30): dispatched windows are
# named <org>/cmx-N-<slug>, and a raw "/" made the path a subdirectory that
# does not exist. Run with -I so nothing next to the module can shadow stdlib.
SCRIPT_DIR=$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")" && pwd)
KEY=$(python3 -I "$SCRIPT_DIR/../chela/cachekey.py" "$WINDOW_NAME") \
    || fail "could not compute the cache key for window '$WINDOW_NAME'"
[ -n "$KEY" ] || fail "empty cache key for window '$WINDOW_NAME'"

# Atomic write (tmp + mv) so the dashboard never reads a half-written file.
# The tmp name carries our pid: two sessions writing one window's file at once
# (see below) must not share a tmp file.
printf '%s' "$ENRICHED" > "${CACHE_DIR}/${KEY}.json.$$.tmp" \
    || fail "cannot write ${CACHE_DIR}/${KEY}.json.$$.tmp (window '$WINDOW_NAME')"
mv "${CACHE_DIR}/${KEY}.json.$$.tmp" "${CACHE_DIR}/${KEY}.json" \
    || fail "cannot move cache file into place: ${CACHE_DIR}/${KEY}.json (window '$WINDOW_NAME')"

# CMX-29: ALSO cache by the payload's own session_id. $TMUX_PANE is inherited by
# every process launched from a pane — a Claude Code background session, a
# subprocess — so <window>.json above is written by whichever of them ran last,
# and a window's bar showed another session's numbers. by-session/<sid>.json can
# only ever hold that session's payload; the dashboard reads it for the session
# the window's own claude process is running. The id is validated before it
# becomes a path component — the same shape chela.sessions.SESSION_RE accepts on
# the read side (keep the two in step), and one cachekey.encode() maps to itself.
SESSION_ID=$(printf '%s' "$INPUT" | python3 -c '
import sys, json, re
try:
    sid = json.loads(sys.stdin.read()).get("session_id")
except Exception:
    sid = None
if isinstance(sid, str) and re.match(r"^[0-9a-fA-F][0-9a-fA-F-]{7,63}$", sid):
    sys.stdout.write(sid)
' 2>/dev/null)
if [ -n "$SESSION_ID" ]; then
    SESSION_DIR="$CACHE_DIR/by-session"
    mkdir -p "$SESSION_DIR" || fail "cannot create $SESSION_DIR"
    printf '%s' "$ENRICHED" > "${SESSION_DIR}/${SESSION_ID}.json.$$.tmp" \
        || fail "cannot write ${SESSION_DIR}/${SESSION_ID}.json.$$.tmp"
    mv "${SESSION_DIR}/${SESSION_ID}.json.$$.tmp" "${SESSION_DIR}/${SESSION_ID}.json" \
        || fail "cannot move cache file into place: ${SESSION_DIR}/${SESSION_ID}.json"
fi
