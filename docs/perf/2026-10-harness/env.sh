# Sourced by every harness script: a scrubbed env pointed at a COPY of $CHELA_DIR and a
# PRIVATE tmux server. Nothing here can reach the live services, the live tmux socket,
# Telegram, ntfy, Linear or the collab relay.
HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
export REPO=${REPO:-$(cd "$HERE/../../.." && pwd)}
export H=${PERF_HOME:-${TMPDIR:-/tmp}/chela-perf}          # all harness state lives here
export CHELA_DIR=$H/chela CHELA_ENV_FILE=$H/chela/chela.env
# tmux socket paths are capped at ~104 bytes, so keep this one short.
export TMUX_TMPDIR=${PERF_TMUX_TMPDIR:-/tmp/chela-perf-tmux}
unset TMUX TMUX_PANE CHELA_NOTIFY_URL CHELA_COLLAB_RELAY LINEAR_API_KEY \
      TELEGRAM_BOT_TOKEN TELEGRAM_CHAT_ID CHELA_DISPATCH_WORKFLOWS CHELA_ACTOR
export CHELA_TMUX_SESSION=chela
# Any egress (Telegram Bot API, update checks) dies on a closed local port.
export https_proxy=http://127.0.0.1:9 HTTPS_PROXY=http://127.0.0.1:9 \
       http_proxy=http://127.0.0.1:9 no_proxy=127.0.0.1,localhost
export PY=${PY:-$REPO/.venv/bin/python} SPY_BIN=${SPY_BIN:-py-spy}
export PORT=${PERF_PORT:-5911} TERM_BASE=${PERF_TERM_BASE:-5951}
