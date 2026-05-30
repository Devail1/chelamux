#!/usr/bin/env bash
# chela — Claude Code statusLine hook.
#
# Claude Code runs this after each assistant turn, piping a JSON status payload
# on stdin: context-window usage, the 5h/7d rate-limit blocks, session cost, and
# the model. None of that is persisted anywhere chela can read on its own (it is
# not in the JSONL transcript), so we cache the payload verbatim to
# $CHELA_DIR/context/<window>.json. The dashboard reads these to show each
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
mkdir -p "$CACHE_DIR"

INPUT=$(cat)

# The agent's display name is its tmux window name — the same key the dashboard
# maps cache files by. Outside tmux there is no window, so skip quietly.
WINDOW_NAME=$(tmux display-message -t "$TMUX_PANE" -p '#{window_name}' 2>/dev/null)
[ -z "$WINDOW_NAME" ] && exit 0

# Atomic write (tmp + mv) so the dashboard never reads a half-written file.
printf '%s' "$INPUT" > "${CACHE_DIR}/${WINDOW_NAME}.json.tmp"
mv "${CACHE_DIR}/${WINDOW_NAME}.json.tmp" "${CACHE_DIR}/${WINDOW_NAME}.json"
