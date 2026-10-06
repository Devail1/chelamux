# main_run.sh RUN N MINUTES [tabs|none] [server] — N windows, real-browser load, /proc sampled.
set -e; HERE=$(cd "$(dirname "$0")" && pwd); cd "$HERE"; . ./env.sh
RUN=$1; N=$2; MIN=$3; TABS=${4:-wall,work,phone}
bash mkwin.sh "$N"; bash start.sh "$RUN" "${5:-werkzeug}"; sleep 15; D=$H/runs/$RUN
pid() { for p in $(pgrep -f "prof_run.py $1 "); do
  case "$(tr '\0' ' ' < /proc/$p/cmdline)" in *py-spy*) ;; *) echo "$p"; break;; esac; done; }
python3 -I live_sampler.py $((MIN*60)) dash="$(pid dash)" daemon="$(pid daemon)" tg="$(pid tg)" > "$D/proc.json" &
SP=$!
if [ "$TABS" != none ]; then PW=${PW:-$REPO/node_modules/playwright} node load.cjs "$PORT" "$MIN" "$TABS" > "$D/load.log" 2>&1
else sleep $((MIN*60)); fi
wait $SP; bash stop.sh "$RUN"; python3 analyze.py "$D"
