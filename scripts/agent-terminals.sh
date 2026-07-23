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

# Config comes from $CHELA_DIR/chela.env, like every other chela process — never from a
# PM2 `env:` block (that second copy is what drifted). An exported value still wins.
# shellcheck source=scripts/chela-env.sh
. scripts/chela-env.sh
if [[ -n "${PYTHON:-}" ]]; then :;
elif [[ -x .venv/bin/python ]]; then PYTHON=".venv/bin/python";
else PYTHON="python3"; fi

TTYD="${TTYD:-ttyd}"
TMUX_SESSION="${CHELA_TMUX_SESSION:-chela}"
BASE_PORT="${CHELA_TERM_BASE:-5101}"
POLL="${CHELA_TERM_POLL:-12}"
# Each ttyd allows this many concurrent browser connections (= tmux clients on
# its grouped session). Generous headroom: a dropped/backgrounded tab can leave a
# client attached until its socket is torn down, and a tight cap would lock the
# tile out of new connections once a few stale ones pile up. We deliberately do
# NOT time-reap quiet clients — see the WHY-NO-REAPER note above the poll loop.
MAX_CLIENTS="${CHELA_TERM_MAX_CLIENTS:-10}"
MAP_FILE="${CHELA_DIR:-${HOME}/.chela}/agent_terminals.json"
# Ensure the state dir exists — a fresh install has no ~/.chela yet, and the
# atomic map write (write_map: printf > "$MAP_FILE.tmp" && mv) silently fails
# if the parent is missing, leaving the dashboard with no port map (404 ->
# black wall tiles on first run).
mkdir -p "$(dirname "${MAP_FILE}")"

# xterm.js font stack. CSS font matching is PER-GLYPH, so the browser walks this
# whole list for every character. Order matters:
#   JetBrainsMono Nerd Font  - if the viewer has it installed: Latin + powerline/
#                              dev icons (lazygit, yazi, starship, eza) in one font
#   JetBrains Mono           - BUNDLED @font-face (see app.py _TERM_FONT_CSS): the
#                              Latin monospace body, guaranteed on any device. This
#                              MUST come before the Hebrew font, else on a viewer
#                              without the Nerd Fonts, Latin letters fall through to
#                              the Hebrew font, changing the English look (Miriam's
#                              plainer Nimbus Mono Latin instead of JetBrains Mono).
#   Symbols Nerd Font        - BUNDLED @font-face: icon-only PUA glyphs, for viewers
#                              without a Nerd Font installed.
#   Miriam Mono CLM          - BUNDLED @font-face: monospace Hebrew glyphs (no Nerd/
#                              Latin font covers Hebrew). Only reached for Hebrew
#                              codepoints; monospace so it aligns on the grid.
#   monospace                - final safety net.
# No inner quotes: ttyd JSON-parses a value that starts with `"`, which
# would truncate the list. Unquoted multi-word family names are valid CSS
# (a sequence of space-separated identifiers).
FONT_FAMILY='JetBrainsMono Nerd Font, JetBrains Mono, Symbols Nerd Font, Miriam Mono CLM, monospace'
FONT_FAMILY="${CHELA_TERM_FONT:-$FONT_FAMILY}"

# xterm.js font size (px). Override with CHELA_TERM_FONTSIZE to fit more columns
# per tile on a dense wall (or larger for readability on a single big pane).
FONT_SIZE="${CHELA_TERM_FONTSIZE:-14}"

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
#
# disableLeaveAlert=true is LOAD-BEARING, not cosmetic. ttyd registers a
# `beforeunload` handler that pops the browser's "Leave site? Changes may not be
# saved" modal whenever its iframe navigates away. The wall's background-teardown
# (_teardownTermFrames) releases a stale tile's ttyd client by setting its iframe
# src to about:blank — which fires exactly that modal. Worse, if the user dismisses
# it with Cancel the iframe never unloads, the stale tmux client never drops, and
# `window-size largest` stays pinned to that ghost's dimensions — so the pane
# "grows but won't shrink". Disabling the alert removes the modal AND lets the
# teardown actually complete, resolving the size contention. disableResizeOverlay
# suppresses ttyd's per-resize WxH overlay flash so layout changes don't strobe.
spawn() {
    local wid="$1" port="$2" grp
    grp="${WEBTERM_PREFIX}$(sanitize "$wid")"
    # `tmux -u` is NOT cosmetic. This tmux client is spawned by ttyd, which PM2 starts
    # with a bare environment — no LANG, no LC_ALL, no LC_CTYPE — so tmux's own locale
    # check marks the client non-UTF-8 (`#{client_utf8}` = 0) and it then substitutes an
    # ASCII `_` for EVERY non-ASCII character before sending it to the browser. Claude
    # Code's TUI markers (`✻` `⏺` `✅` `❌`) arrive at xterm as literal 0x5F underscores:
    # the pane itself is fine (tmux's own `capture-pane` shows the real glyph), and the
    # web terminal shows `_ Baked for 10s`.
    #
    # ⛔ This is NOT a font problem, and no font can fix it — the character is replaced
    # two layers upstream of the browser, so the web terminal's bundled faces (Symbols
    # Nerd Font, the Symbola coverage fallback — CMX-155/159) never get to see it. Those
    # fixed real tofu on the `/screenshot` and collab-viewer paths, which look identical
    # and are a different bug. `-u` forces UTF-8 output regardless of the environment
    # check, which is exactly the documented case for it.
    #
    # --base-path /term/<wid>: ttyd serves its page, assets, and /ws under that
    # prefix so the same-origin dashboard proxy (app.py term_http / term_ws) can
    # forward /term/<wid>/* verbatim with no path rewriting. The iframe src in
    # terminals.js is /term/<wid>/, which matches this base-path exactly.
    "${TTYD}" --interface 127.0.0.1 --port "${port}" --writable --max-clients "${MAX_CLIENTS}" \
        --base-path "/term/${wid}" \
        --terminal-type xterm-256color \
        --client-option "fontSize=${FONT_SIZE}" \
        --client-option "fontFamily=${FONT_FAMILY}" \
        --client-option "theme=${TERM_THEME}" \
        --client-option disableLeaveAlert=true \
        --client-option disableResizeOverlay=true \
        tmux -u new-session -A -s "${grp}" -t "${TMUX_SESSION}" ';' \
                 set-option destroy-unattached off ';' \
                 set-option status off ';' \
                 set-option -g window-size largest ';' \
                 set-window-option -g aggressive-resize on ';' \
                 select-window -t "${wid}" \
        >/dev/null 2>&1 &
    PID_OF["$wid"]=$!
    PORT_OF["$wid"]=$port
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

# Interruptible sleep. A bare `sleep N` is a FOREGROUND child, and bash defers trap
# handlers until the current foreground child exits — so a plain `sleep` swallows
# SIGTERM for its whole duration. With `sleep 3600` (the disabled-wall idle below)
# that meant pm2's stop hung until its kill-timeout and then SIGKILLed us, so the
# EXIT trap never ran and cleanup() never reaped the ttyds or the webterm_* mirror
# sessions. Backgrounding the sleep and `wait`-ing on it makes the signal land at
# once: `wait` is interruptible, the trap fires, cleanup runs. Every sleep in this
# script must go through here.
nap() { sleep "$1" & wait $!; }

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
        while true; do nap 3600; done
        ;;
esac

# SELF-HEAL, NEVER EXIT. This used to be `has-session || { echo error; exit 1; }`,
# which made a missing session FATAL. It isn't: the session is chela's own, nothing
# outside chela recreates it, and after a reboot (or `wsl --shutdown`, which takes the
# whole tmux server with it) we simply come up before it exists. Under a process
# manager with autorestart, exiting instantly means being restarted instantly — on
# 2026-07-13 that hot-looped 14,501 times in a few hours, spamming ~14.7k identical
# error lines and burning CPU, while the dashboard just showed "Spawn failed: error
# connecting to /tmp/tmux-1000/default". A recoverable boot-ordering condition must
# never be a crash-loop source, so:
#   - session missing  -> CREATE it (idempotent, race-safe) and carry on;
#   - tmux unreachable -> WAIT with capped exponential backoff, still never exiting.
# Mirrors chela.discovery.ensure_session() (the dashboard's spawn path uses that).
BACKOFF_MAX="${CHELA_TERM_BACKOFF_MAX:-30}"
ANCHOR_WINDOW="shell-1"   # a session needs >=1 window; match the wall's shell-N scheme

# CREATE-ONLY, NEVER CLOBBER. This function runs every poll tick against the session
# the user's live agents are sitting in, so it must be impossible for it to destroy
# one. Two rules keep it that way:
#
#   1. `-A` (attach-or-create) is the ONLY safe create primitive. Plain `new-session
#      -s X` on a live X fails with "duplicate session"; `-A` makes it a genuine
#      no-op instead (verified headless: session id and window list unchanged). So
#      even if the has-session gate below false-negatives — a transient client error,
#      a busy server — the worst case is a no-op, never a recreate. Belt AND braces:
#      the gate is an optimisation, `-A` is the guarantee.
#   2. We touch NOTHING that already exists. No set-window-option, no kill, no rename
#      of a window we didn't just create: `-n` alone already turns automatic-rename
#      off for the anchor window (tmux does this whenever the name is given), so the
#      tile stays pinned without us ever reaching into a live session.
#
# `-A -d` on an EXISTING session exits NONZERO in a daemon ("open terminal failed:
# not a terminal" — with -A, tmux treats -d as attach-session's -d and there is no
# tty to attach). That is a success, not a failure, so the exit code is deliberately
# ignored: has-session alone decides. That also makes the create race-safe — whoever
# wins, we agree the session is there.
ensure_session() {
    tmux has-session -t "${TMUX_SESSION}" 2>/dev/null && return 0
    tmux new-session -A -d -s "${TMUX_SESSION}" -n "${ANCHOR_WINDOW}" </dev/null >/dev/null 2>&1
    if tmux has-session -t "${TMUX_SESSION}" 2>/dev/null; then
        echo "agent-terminals: tmux session '${TMUX_SESSION}' was missing — created it"
        return 0
    fi
    return 1   # tmux itself is unreachable (no binary / unwritable socket dir)
}

# Block until the session is there. Backs off 1s -> BACKOFF_MAX so an unreachable
# tmux costs one log line per 30s instead of a hot loop, and returns the instant it
# heals. Called on startup AND on every poll tick, so a `tmux kill-server` under a
# running supervisor is recovered from too (not just a cold boot).
wait_for_session() {
    local delay=1
    until ensure_session; do
        echo "agent-terminals: tmux unreachable, cannot create session '${TMUX_SESSION}'; retrying in ${delay}s" >&2
        nap "${delay}"
        delay=$(( delay * 2 ))
        (( delay > BACKOFF_MAX )) && delay="${BACKOFF_MAX}"
    done
}

wait_for_session

echo "agent-terminals: dynamic supervisor (poll ${POLL}s, base port ${BASE_PORT})"

write_map               # write an (empty) map even before any agent is seen

# WHY-NO-REAPER: we used to detach wall ttyd clients idle past a timeout to free
# --max-clients slots held by abandoned tabs. That was wrong: the only idle signal
# tmux exposes is `client_activity`, which advances on terminal I/O — NOT on the
# WebSocket keepalive pings (app.py ping_interval=25) that keep a quiet pane's
# socket warm. An agent terminal that simply produces no output (the normal case
# for a wall you're watching) thus looked "idle" and got force-detached after the
# timeout, dropping a perfectly live tile to ttyd's reconnect screen. tmux cannot
# tell an abandoned tab from a live-but-quiet one, so any activity-based reaper
# kills real sessions. Genuinely dead sockets are already torn down by the
# keepalive: a failed ping raises in the proxy pumps, ttyd drops the client, and
# tmux detaches it — which frees the slot. --max-clients headroom absorbs the rest.
declare -A CUR NAME_OF
while :; do
    # The session can vanish under us (tmux kill-server, a crashed server). Heal it
    # BEFORE discovery, so the tick that follows sees the fresh session rather than
    # an empty window set — otherwise the "discovery returned empty" guard below
    # would hold the stale ttyd set forever and the wall would never come back.
    wait_for_session

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
        nap "${POLL}"; continue
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
    nap "${POLL}"
done
