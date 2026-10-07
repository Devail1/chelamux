# Sourced by every harness script: a scrubbed env pointed at a COPY of $CHELA_DIR and a
# PRIVATE tmux server. Nothing here can reach the live services, the live tmux socket,
# Telegram, ntfy, Linear or the collab relay.
#
# 🧯 CMX-21: the private server is a NAMED socket (`-L $PERF_SOCK`) passed on EVERY tmux
# call, never `TMUX_TMPDIR`. TMUX_TMPDIR does not isolate: `$TMUX` (set inside any tmux
# pane) overrides it, and once its dir is deleted tmux falls back to the DEFAULT socket —
# on 2026-10-06 that let an orphaned supervisor from this harness re-create the live server
# with this file's env. chela's own bare `tmux` calls (the services, agent-terminals.sh)
# reach the private server through the `$H/bin/tmux` shim below, which injects the flag.
HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
export REPO=${REPO:-$(cd "$HERE/../../.." && pwd)}
export H=${PERF_HOME:-${TMPDIR:-/tmp}/chela-perf}          # all harness state lives here
export CHELA_DIR=$H/chela CHELA_ENV_FILE=$H/chela/chela.env
export PERF_SOCK=${PERF_TMUX_SOCKET:-chela-perf}
case "$PERF_SOCK" in ""|default) echo "env.sh: PERF_TMUX_SOCKET must name a private socket" >&2; exit 1;; esac
unset TMUX TMUX_PANE TMUX_TMPDIR CHELA_NOTIFY_URL CHELA_COLLAB_RELAY LINEAR_API_KEY \
      TELEGRAM_BOT_TOKEN TELEGRAM_CHAT_ID CHELA_DISPATCH_WORKFLOWS CHELA_ACTOR \
      CLAUDECODE CLAUDE_CODE_SESSION_ID CLAUDE_CODE_CHILD_SESSION CLAUDE_CODE_ENTRYPOINT \
      CLAUDE_CODE_MESSAGING_SOCKET CLAUDE_PID AI_AGENT
# The real tmux, resolved past our own shim (this file may be sourced more than once).
REAL_TMUX=$(PATH=$(printf '%s' "$PATH" | tr ':' '\n' | grep -vxF "$H/bin" | paste -sd: -) command -v tmux)
mkdir -p "$H/bin"
printf '#!/bin/sh\nexec %s -L %s "$@"\n' "$REAL_TMUX" "$PERF_SOCK" > "$H/bin/tmux"
chmod 755 "$H/bin/tmux"
case ":$PATH:" in *":$H/bin:"*) ;; *) export PATH="$H/bin:$PATH";; esac
export CHELA_TMUX_SESSION=chela
# Any egress (Telegram Bot API, update checks) dies on a closed local port.
export https_proxy=http://127.0.0.1:9 HTTPS_PROXY=http://127.0.0.1:9 \
       http_proxy=http://127.0.0.1:9 no_proxy=127.0.0.1,localhost
export PY=${PY:-$REPO/.venv/bin/python} SPY_BIN=${SPY_BIN:-py-spy}
export PORT=${PERF_PORT:-5911} TERM_BASE=${PERF_TERM_BASE:-5951}

# kill-server on the PRIVATE server only: refuse if the socket ever resolves to the live one.
perf_kill_server() {
  local sp
  sp=$("$REAL_TMUX" -L "$PERF_SOCK" display -p '#{socket_path}' 2>/dev/null) || return 0
  if [ "$sp" = "/tmp/tmux-$(id -u)/default" ] || [ "$(basename "$sp")" = default ]; then
    echo "REFUSING kill-server: $sp is the live tmux server" >&2; return 1
  fi
  "$REAL_TMUX" -L "$PERF_SOCK" kill-server
}
