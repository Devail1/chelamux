# ab_run.sh RUN werkzeug|waitress MINUTES REQLOG [sse_clients] — dashboard alone, replayed
# HTTP mix from a recorded run. waitress must be importable (PYTHONPATH=<dir with waitress>).
set -e; HERE=$(cd "$(dirname "$0")" && pwd); . "$HERE/env.sh"; D=$H/runs/$1; mkdir -p "$D"
export PERF_OUT=$D PERF_SERVER=$2 PYTHONDONTWRITEBYTECODE=1; P=$((PORT+1))
cd "$REPO"; nohup "$PY" "$HERE/prof_run.py" dash dashboard --host 127.0.0.1 --port $P > "$D/dash.log" 2>&1 &
DP=$!; sleep 8; cd "$HERE"
python3 -I live_sampler.py $(($3*60)) dash=$DP > "$D/proc.json" & SP=$!
python3 replay.py "$4" $P "$3" "$D/client.json" "${5:-0}"
wait $SP; kill -INT $DP; sleep 3; kill $DP 2>/dev/null; true
