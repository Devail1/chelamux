# sse_exp.sh — dashboard alone (10 windows), k idle /api/events clients, 90 s per k.
HERE=$(cd "$(dirname "$0")" && pwd); . "$HERE/env.sh"; D=$H/runs/sse; mkdir -p "$D"
bash "$HERE/mkwin.sh" 10 >/dev/null; export PERF_OUT=$D PYTHONDONTWRITEBYTECODE=1; P=$((PORT+2))
cd "$REPO"; nohup "$PY" "$HERE/prof_run.py" dash dashboard --host 127.0.0.1 --port $P > "$D/dash.log" 2>&1 &
DP=$!; sleep 10
for k in 0 1 3 6 12; do pids=""
  for i in $(seq 1 $k); do curl -sN "http://127.0.0.1:$P/api/events" > /dev/null & pids="$pids $!"; done
  sleep 5; python3 -I "$HERE/live_sampler.py" 90 dash=$DP > "$D/k$k.json"; kill $pids 2>/dev/null; sleep 3
done
kill -INT $DP; sleep 2; kill $DP 2>/dev/null; true
