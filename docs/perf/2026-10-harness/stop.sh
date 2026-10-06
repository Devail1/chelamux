# stop.sh RUN — SIGINT the profiled services (py-spy then writes its .raw), reap the ttyds.
. "$(dirname "$0")/env.sh"; D=$H/runs/$1
for f in dash daemon tg; do pkill -INT -f "prof_run.py $f " 2>/dev/null; done; sleep 4
for f in dash daemon tg; do pkill -TERM -f "prof_run.py $f " 2>/dev/null; done
kill "$(cat "$D/terms.pid")" 2>/dev/null; sleep 2
pkill -f "ttyd .*--port $(echo "$TERM_BASE" | cut -c1-2)[0-9][0-9]" 2>/dev/null; true
