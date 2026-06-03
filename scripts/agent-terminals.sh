#!/usr/bin/env bash
# Per-agent ttyd supervisor. One localhost ttyd per discovered chela window,
# each attached to that agent's tmux window. Writes the wid->port map to
# ~/.chela/agent_terminals.json so the dashboard can route /term/<wid>.
#
# Keyed by the stable tmux window id (@N), NOT the window name: a window rename
# (e.g. chela's reconcile_window_names relabelling shell-N -> cwd basename)
# leaves the ttyd, grouped session, and port untouched, so the embedded iframe
# never reloads. Names change; window ids don't (for the window's lifetime).
#
# Localhost-bound only (--interface 127.0.0.1). The ttyds are writable shells
# and carry NO auth of their own — the trust boundary is the network (loopback
# + a tailnet / SSH tunnel in front of the dashboard). Never expose these ports
# directly on an untrusted network.
#
# Dynamic supervisor: polls the live window set every CHELA_TERM_POLL seconds
# and spawns/reaps ttyds + rewrites the map as agents come and go. A new tmux
# window appears in the dashboard wall with no restart. We only read the live
# window list and mirror via grouped tmux sessions.
# Run it alongside `chela run` (e.g. under a process manager).

# NB: no `set -u` — bash errors on empty associative-array expansion
# (${#arr[@]} / ${arr[$k]:-}) under nounset; all optional vars below use
# explicit ${VAR:-default} defaults instead.
set -o pipefail

cd "$(dirname "$0")/.."
if [[ -n "${PYTHON:-}" ]]; then :;
elif [[ -x .venv/bin/python ]]; then PYTHON=".venv/bin/python";
else PYTHON="python3"; fi

TTYD="${TTYD:-ttyd}"
TMUX_SESSION="${CHELA_TMUX_SESSION:-chela}"
BASE_PORT="${CHELA_TERM_BASE:-5101}"
POLL="${CHELA_TERM_POLL:-12}"
# Each ttyd allows this many concurrent browser connections (= tmux clients on
# its grouped session). Generous headroom: a dropped/backgrounded tab leaves a
# client attached until reaped (see CLIENT_IDLE_MAX), and a tight cap would lock
# the tile out of new connections once a few stale ones pile up.
MAX_CLIENTS="${CHELA_TERM_MAX_CLIENTS:-10}"
# Detach a wall ttyd client after this many seconds idle, so an abandoned tab
# can't hold a --max-clients slot forever. Scoped to webterm_* clients only — the
# user's own interactive tmux session is never reaped.
CLIENT_IDLE_MAX="${CHELA_TERM_CLIENT_IDLE_MAX:-3600}"
MAP_FILE="${CHELA_DIR:-${HOME}/.chela}/agent_terminals.json"
# Ensure the state dir exists — a fresh install has no ~/.chela yet, and the
# atomic map write (write_map: printf > "$MAP_FILE.tmp" && mv) silently fails
# if the parent is missing, leaving the dashboard with no port map (404 ->
# black wall tiles on first run).
mkdir -p "$(dirname "${MAP_FILE}")"

# xterm.js font stack: use whatever Nerd Font the viewer has installed
# (powerline / dev glyphs for lazygit, yazi, starship, eza), else plain
# monospace — safe fallback, no glyphs if no Nerd Font present.
# No inner quotes: ttyd JSON-parses a value that starts with `"`, which
# would truncate the list. Unquoted multi-word family names are valid CSS
# (a sequence of space-separated identifiers).
FONT_FAMILY='JetBrainsMono Nerd Font, FiraCode Nerd Font, Hack Nerd Font, Symbols Nerd Font, monospace'
FONT_FAMILY="${CHELA_TERM_FONT:-$FONT_FAMILY}"

# xterm colour theme (an ITheme JSON object) passed to ttyd via --client-option,
# matching the dashboard palette (style.css :root — GitHub Dark): bg #0d1117 /
# text #c9d1d9 / accent #58a6ff cursor, with the same green (#3fb950) and yellow
# (#d29922) the wall uses for status dots, so the embedded panes read as native
# to the wall instead of the OS default xterm colours. ttyd's documented theme
# syntax is `--client-option theme={...}` (value starts with `{` → JSON-parsed).
# Override the whole object via CHELA_TERM_THEME (a JSON ITheme string).
TERM_THEME='{"background":"#0d1117","foreground":"#c9d1d9","cursor":"#58a6ff","cursorAccent":"#0d1117","selectionBackground":"#264f78","black":"#484f58","red":"#ff7b72","green":"#3fb950","yellow":"#d29922","blue":"#58a6ff","magenta":"#bc8cff","cyan":"#39c5cf","white":"#b1bac4","brightBlack":"#6e7681","brightRed":"#ffa198","brightGreen":"#56d364","brightYellow":"#e3b341","brightBlue":"#79c0ff","brightMagenta":"#d2a8ff","brightCyan":"#56d4dd","brightWhite":"#f0f6fc"}'
TERM_THEME="${CHELA_TERM_THEME:-$TERM_THEME}"

declare -A PORT_OF PID_OF              # wid -> port / ttyd pid
LAST_MAP=""

# "wid\tname" per live chela window (wid is the key; name is for logging only)
list_windows() {
    "${PYTHON}" -c "
from chela import discovery
for name, wid in sorted(discovery.get_all_windows().items()):
    print(f'{wid}\t{name}')
" 2>/dev/null
}

sanitize() { printf '%s' "$1" | tr -c 'A-Za-z0-9_' '_'; }

# Grouped-session prefix namespaced by the tmux session this supervisor serves,
# so two chela instances (or chela co-resident with another webterm tool) never
# spawn into, nor reap, each other's sessions. cleanup() only kills sessions
# under THIS prefix. The dashboard proxies by port (the wid->port map), never by
# session name, so this name is purely internal — safe to namespace freely.
WEBTERM_PREFIX="webterm_$(sanitize "${TMUX_SESSION}")_"

# lowest free port >= BASE_PORT not already assigned
free_port() {
    local p="${BASE_PORT}" v used
    while :; do
        used=0
        if (( ${#PORT_OF[@]} )); then
            for v in "${PORT_OF[@]}"; do [[ "$v" == "$p" ]] && { used=1; break; }; done
        fi
        [[ $used -eq 0 ]] && { echo "$p"; return; }
        p=$((p + 1))
    done
}

# spawn(wid, port): each ttyd gets its OWN grouped session, keyed by the stable
# window id. `new-session -t` shares the chela session's windows/panes but keeps
# an independent current-window pointer, so each wall tile shows its own pane
# (plain `attach -t <session>` would mirror one shared active window across every
# client). -A reattaches the same grouped session on reconnect.
#
# window-size largest + aggressive-resize on: the grouped sessions SHARE the real
# windows, and a tmux window has exactly one size. With the default `latest`
# policy, whichever client resized most recently (often a small phone tile or a
# stale ttyd connection — ttyd allows up to --max-clients) shrinks the shared
# window for everyone, so other tiles render undersized with dead space. `largest`
# sizes each window to its biggest current viewer, so a small/stale client can't
# shrink the active wall; `aggressive-resize` only counts clients actually viewing
# the window. Set globally on the server (idempotent) so it survives restarts.
spawn() {
    local wid="$1" port="$2" grp
    grp="${WEBTERM_PREFIX}$(sanitize "$wid")"
    # --base-path /term/<wid>: ttyd serves its page, assets, and /ws under that
    # prefix so the same-origin dashboard proxy (app.py term_http / term_ws) can
    # forward /term/<wid>/* verbatim with no path rewriting. The iframe src in
    # terminals.js is /term/<wid>/, which matches this base-path exactly.
    "${TTYD}" --interface 127.0.0.1 --port "${port}" --writable --max-clients "${MAX_CLIENTS}" \
        --base-path "/term/${wid}" \
        --terminal-type xterm-256color \
        --client-option fontSize=14 \
        --client-option "fontFamily=${FONT_FAMILY}" \
        --client-option "theme=${TERM_THEME}" \
        tmux new-session -A -s "${grp}" -t "${TMUX_SESSION}" ';' \
                 set-option destroy-unattached off ';' \
                 set-option status off ';' \
                 set-option -g window-size largest ';' \
                 set-window-option -g aggressive-resize on ';' \
                 select-window -t "${wid}" \
        >/dev/null 2>&1 &
    PID_OF["$wid"]=$!
    PORT_OF["$wid"]=$port
}

# Detach wall ttyd clients idle longer than CLIENT_IDLE_MAX. ttyd keeps one tmux
# client per WebSocket; a dropped or backgrounded browser tab can leave its client
# attached indefinitely, consuming a --max-clients slot until the grouped session
# refuses new connections. Filtered to WEBTERM_PREFIX sessions so the user's own
# interactive `${TMUX_SESSION}` client is never touched.
reap_stale_clients() {
    local now act tty sess
    now="$(date +%s)"
    while IFS='|' read -r act tty sess; do
        [[ -z "$tty" ]] && continue
        [[ "$sess" == "${WEBTERM_PREFIX}"* ]] || continue
        if (( now - act > CLIENT_IDLE_MAX )); then
            tmux detach-client -t "$tty" 2>/dev/null \
                && echo "agent-terminals: reaped idle client ${tty} (${sess}, idle $((now-act))s)"
        fi
    done < <(tmux list-clients -F '#{client_activity}|#{client_name}|#{client_session}' 2>/dev/null)
}

despawn() {
    local wid="$1"
    [[ -n "${PID_OF[$wid]:-}" ]] && kill "${PID_OF[$wid]}" 2>/dev/null
    tmux kill-session -t "${WEBTERM_PREFIX}$(sanitize "$wid")" 2>/dev/null
    unset 'PID_OF[$wid]' 'PORT_OF[$wid]'
}

write_map() {
    local json="{" first=1 wid
    if (( ${#PORT_OF[@]} )); then
        for wid in "${!PORT_OF[@]}"; do
            [[ $first -eq 0 ]] && json+=","
            json+="\"${wid}\":${PORT_OF[$wid]}"
            first=0
        done
    fi
    json+="}"
    [[ "$json" == "$LAST_MAP" ]] && return
    ( umask 022; printf '%s\n' "$json" > "${MAP_FILE}.tmp" && mv "${MAP_FILE}.tmp" "${MAP_FILE}" )
    LAST_MAP="$json"
    echo "agent-terminals: map -> ${json}"
}

cleanup() {
    if (( ${#PID_OF[@]} )); then
        for wid in "${!PID_OF[@]}"; do kill "${PID_OF[$wid]}" 2>/dev/null; done
    fi
    rm -f "${MAP_FILE}"
    # drop ONLY our own grouped viewer sessions (scoped to this supervisor's
    # prefix — never touch another instance's / another tool's webterm_*).
    tmux list-sessions -F '#{session_name}' 2>/dev/null \
        | grep "^${WEBTERM_PREFIX}" | xargs -r -n1 tmux kill-session -t 2>/dev/null
}
# cleanup runs once on real exit. INT/TERM must EXIT (not just run a handler and
# resume) — bash with a non-exiting TERM handler would swallow the signal, run
# cleanup, then continue the poll loop, leaving the supervisor un-killable by
# SIGTERM (what PM2 and `kill` send) and respawning the ttyds cleanup just
# reaped. Exiting here fires the EXIT trap, so cleanup still runs exactly once.
trap cleanup EXIT
trap 'exit 143' TERM   # 128+15
trap 'exit 130' INT    # 128+2

# Feature flag (mirrors chela/config.py TERMINALS_ENABLED). When disabled we
# spawn NO ttyds: write one empty map so the dashboard sees "no terminals",
# then idle forever. Checked BEFORE the tmux-session guard so a disabled
# process never `exit`s — under a process manager with autorestart, any exit
# would crash-loop; idling keeps it alive and trivially flippable back on by
# setting the env true and restarting.
TERMINALS_ENABLED="${CHELA_TERMINALS_ENABLED:-true}"
case "$(printf '%s' "${TERMINALS_ENABLED}" | tr '[:upper:]' '[:lower:]')" in
    false|0|no|off)
        echo "agent-terminals: CHELA_TERMINALS_ENABLED=${TERMINALS_ENABLED} — terminals disabled, spawning no ttyds"
        write_map           # empty {} map so consumers see no terminals
        while true; do sleep 3600; done
        ;;
esac

if ! tmux has-session -t "${TMUX_SESSION}" 2>/dev/null; then
    echo "error: tmux session '${TMUX_SESSION}' not found." >&2
    exit 1
fi

echo "agent-terminals: dynamic supervisor (poll ${POLL}s, base port ${BASE_PORT})"

write_map               # write an (empty) map even before any agent is seen

declare -A CUR NAME_OF
while :; do
    # snapshot the live window set, keyed by stable window id
    CUR=()
    NAME_OF=()
    while IFS=$'\t' read -r wid name; do
        [[ -z "$wid" ]] && continue
        CUR["$wid"]=1
        NAME_OF["$wid"]="$name"
    done < <(list_windows)

    # Guard: an empty result while we have running ttyds is almost certainly a
    # transient discovery hiccup (python import / tmux mid-write), not
    # "every agent vanished" — skip this tick rather than nuke everything.
    if (( ${#CUR[@]} == 0 && ${#PID_OF[@]} > 0 )); then
        echo "agent-terminals: discovery returned empty; keeping current set"
        sleep "${POLL}"; continue
    fi

    changed=0

    # additions / dead-ttyd respawns. A rename keeps the same wid, so it never
    # lands here — the ttyd and its grouped session ride straight through.
    for wid in "${!CUR[@]}"; do
        if [[ -z "${PID_OF[$wid]:-}" ]]; then
            port="$(free_port)"; spawn "$wid" "$port"
            echo "agent-terminals: + ${NAME_OF[$wid]} (${wid}) -> :${port}"; changed=1
        elif ! kill -0 "${PID_OF[$wid]}" 2>/dev/null; then
            echo "agent-terminals: ! ${NAME_OF[$wid]:-$wid} (${wid}) ttyd died, respawn"
            spawn "$wid" "${PORT_OF[$wid]}"; changed=1
        fi
    done

    # removals: window gone
    if (( ${#PID_OF[@]} )); then
        for wid in "${!PID_OF[@]}"; do
            if [[ -z "${CUR[$wid]:-}" ]]; then
                echo "agent-terminals: - ${wid} (window gone)"
                despawn "$wid"; changed=1
            fi
        done
    fi

    (( changed )) && write_map
    reap_stale_clients      # free --max-clients slots held by abandoned tabs
    sleep "${POLL}"
done
