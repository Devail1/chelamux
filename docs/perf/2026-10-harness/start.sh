# start.sh RUN [werkzeug|waitress] — dashboard + daemon + telegram (+ ttyd supervisor),
# each under py-spy, against the copy. Telegram gets a fake token and every fake window bound.
set -e; . "$(dirname "$0")/env.sh"; RUN=$1
export PERF_OUT=$H/runs/$RUN PERF_SERVER=${2:-werkzeug} PYTHONDONTWRITEBYTECODE=1
mkdir -p "$PERF_OUT"; cd "$REPO"
tmux -L "$PERF_SOCK" list-windows -t chela -F '#{window_id} #{window_name}' | python3 -c '
import json, os, sys
w = [l.split()[0] for l in sys.stdin if "__main__" not in l]
json.dump({"chat_id": "-100", "bindings": {x: str(1000 + i) for i, x in enumerate(w)},
           "topic_names": {x: x for x in w}}, open(os.environ["CHELA_DIR"] + "/telegram-bindings.json", "w"))'
rm -f "$CHELA_DIR/agent_terminals.json"
# 🧯 CMX-21: the supervisor starts from `env -i` + an allowlist — never this shell's proxies,
# PERF_*, H or a parent Claude session's markers, which a server it heal-creates would hand
# every window — and under setsid, so stop.sh can reap its whole process group.
env -i PATH="$PATH" HOME="$HOME" USER="${USER:-}" LANG="${LANG:-C.UTF-8}" \
  CHELA_DIR="$CHELA_DIR" CHELA_ENV_FILE="$CHELA_ENV_FILE" CHELA_TMUX_SESSION=chela \
  CHELA_TERM_BASE="$TERM_BASE" PYTHON="$PY" \
  setsid nohup bash scripts/agent-terminals.sh > "$PERF_OUT/terms.log" 2>&1 < /dev/null &
echo $! > "$PERF_OUT/terms.pid"
SPY="$SPY_BIN record --format raw --rate 50 --nonblocking"
nohup $SPY -o "$PERF_OUT/dash.raw" -- "$PY" "$HERE/prof_run.py" dash dashboard --host 127.0.0.1 --port "$PORT" > "$PERF_OUT/dash.log" 2>&1 &
nohup $SPY -o "$PERF_OUT/daemon.raw" -- "$PY" "$HERE/prof_run.py" daemon run > "$PERF_OUT/daemon.log" 2>&1 &
TELEGRAM_BOT_TOKEN=000:fake TELEGRAM_CHAT_ID=-100 nohup $SPY -o "$PERF_OUT/tg.raw" -- \
  "$PY" "$HERE/prof_run.py" tg telegram --no-inbound --auto-topics > "$PERF_OUT/tg.log" 2>&1 &
sleep 6
