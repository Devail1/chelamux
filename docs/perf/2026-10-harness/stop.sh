# stop.sh RUN — SIGINT the profiled services (py-spy then writes its .raw), reap the
# ttyd supervisor's WHOLE process group (supervisor + ttyds + naps), then the private server.
. "$(dirname "$0")/env.sh"; D=$H/runs/$1
for f in dash daemon tg; do pkill -INT -f "prof_run.py $f " 2>/dev/null; done; sleep 4
for f in dash daemon tg; do pkill -TERM -f "prof_run.py $f " 2>/dev/null; done
# start.sh ran the supervisor under setsid, so its pid IS its process group id. Killing
# only the pid is what left 3 orphaned supervisors alive on 2026-10-06 (CMX-21).
PG=$(cat "$D/terms.pid" 2>/dev/null)
if [ -n "$PG" ]; then kill -TERM -- "-$PG" 2>/dev/null; sleep 2; kill -KILL -- "-$PG" 2>/dev/null; fi
pkill -f "ttyd .*--port $(echo "$TERM_BASE" | cut -c1-2)[0-9][0-9]" 2>/dev/null
perf_kill_server 2>/dev/null; true
