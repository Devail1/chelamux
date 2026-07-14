"""chela dashboard — Flask app + API routes.

ZERO built-in auth, by design. The dashboard binds 127.0.0.1 by default and the
embedded ttyd terminal wall is a *writable shell* — exposing it on an untrusted
network is remote code execution. For remote access, put it behind a tailnet
(`tailscale serve`), an SSH tunnel, or a reverse proxy that adds your own auth.
The tailnet is the trust boundary; there is intentionally no password here.

This is an OPTIONAL component: Flask is an extra (`chelamux[dashboard]`). The
core CLI never imports this module at top level.
"""
from __future__ import annotations

import atexit
import hashlib
import json
import logging
import os
import re
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

from flask import abort, Flask, jsonify, render_template, request, Response

from chela import config
from chela.config import DISPATCH_WORKFLOWS, CHELA_DIR, TMUX_SESSION, NOTIFY_INTERVAL
from chela import agent_manager, capabilities, collab, collab_stream, context, discovery, dispatcher, gateanswer, hooks, launcher, messenger, notify, okf, scheduler, starter, transcripts, userconfig
from chela.backlog import _BULLET_RE, parse_backlog
from chela.sources import get_source
from chela.sources.markdown import OPEN_RE
from chela.workflow import load_workflow, workflow_error, PROJECT_KEY_RE

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("dashboard")


app = Flask(
    __name__,
    template_folder=str(Path(__file__).parent / "templates"),
    static_folder=str(Path(__file__).parent / "static"),
)

# {agent: ttyd_port} map written by scripts/agent-terminals.sh on each poll.
TERMINALS_MAP = CHELA_DIR / "agent_terminals.json"

# WebSocket support for the embedded terminal wall. flask-sock is in the
# [dashboard] extra; guard the import so a dashboard install missing it still
# serves everything else (the wall iframes just won't connect).
try:
    from flask_sock import Sock
    # ttyd's browser client opens its WebSocket requesting the `tty`
    # subprotocol, and browsers abort the handshake (close 1006, no `onopen`)
    # if the server's 101 response doesn't echo an agreed subprotocol back.
    # flask-sock forwards these options to simple_websocket.Server, which then
    # selects+echoes `tty`. Without this every wall tile fails to connect and
    # shows ttyd's "Press ⏎ to Reconnect" (the black-tile symptom). Lenient
    # non-browser clients don't need it, so it's invisible outside the browser.
    # ping_interval keeps idle terminal sockets alive: with no traffic on a quiet
    # pane, a proxy in the path (Tailscale / Caddy) closes the WebSocket after its
    # idle timeout and ttyd shows its "reconnect" screen. 25s stays under the
    # common 60s cutoffs. (browser↔Flask hop; the Flask↔ttyd hop is pinged too.)
    app.config["SOCK_SERVER_OPTIONS"] = {"subprotocols": ["tty"], "ping_interval": 25}
    _sock = Sock(app)
except Exception:  # pragma: no cover - optional dep
    _sock = None


def require_auth(f):
    """No-op decorator — there is no built-in auth (see module docstring).

    Kept as a decorator so every route reads the same and a future deployment
    could reintroduce auth in one place. The security boundary is the network
    (loopback bind + tailnet), not this function.
    """
    return f


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# Shared status derivation lives in agent_manager so the agent-facing `chela
# peek` reads identical liveness/health off the same data layer as this endpoint.
_liveness = agent_manager.liveness


def _require_terminals() -> None:
    """abort(404) when the embedded terminals feature is disabled.

    Called at the top of every terminal-only endpoint (/api/term/*, spawn,
    kill) so they vanish — not just hide — when CHELA_TERMINALS_ENABLED is
    false.
    """
    if not config.TERMINALS_ENABLED:
        abort(404)


# ---------------------------------------------------------------------------
# Page route
# ---------------------------------------------------------------------------

@app.route("/")
@require_auth
def index():
    return render_template("index.html", terminals_enabled=config.TERMINALS_ENABLED)


# ---------------------------------------------------------------------------
# API: Agents
# ---------------------------------------------------------------------------

@app.route("/api/agents")
@require_auth
def api_agents():
    windows = discovery.get_all_windows()
    tasks = scheduler.list_tasks()

    # Build set of agents with enabled schedules + schedule summary
    scheduled_agents = {t.agent_name for t in tasks if t.enabled}
    # Per-agent: most recent last_run and soonest next_run across enabled tasks
    agent_schedule_summary = {}
    for t in tasks:
        if not t.enabled:
            continue
        prev = agent_schedule_summary.get(t.agent_name, {})
        # Latest last_run
        if t.last_run and (not prev.get("last_run") or t.last_run > prev["last_run"]):
            prev["last_run"] = t.last_run
        # Soonest next_run
        if t.next_run and (not prev.get("next_run") or t.next_run < prev["next_run"]):
            prev["next_run"] = t.next_run
        agent_schedule_summary[t.agent_name] = prev

    # Native busy/idle/waiting from `claude agents --json` (read once, keyed by pid).
    status_map = agent_manager.session_status_map()

    agents = []
    for name, window_id in windows.items():
        transcript = transcripts.agent_transcript_summary(name)

        # Map window -> child claude pid -> session status + cwd. No claude pid
        # means a plain shell (or a dead session): not running, never "thinking".
        cpid = agent_manager.claude_pid(window_id)
        claude_running = cpid is not None
        sess_status = status_map["by_pid"].get(cpid) if cpid is not None else None
        sess_cwd = status_map["cwd_by_pid"].get(cpid) if cpid is not None else None

        liveness, health = _liveness(claude_running, sess_status)
        win_type = agent_manager.window_type(window_id, claude_running)

        agents.append({
            "name": name,
            "online": True,
            "window_id": window_id,
            "shared": window_id in _SHARED,
            "window_type": win_type,
            "claude_running": claude_running,
            "thinking": sess_status == "busy",
            "session_status": sess_status,
            "liveness": liveness,
            "health": health,
            "status": sess_status,
            "cwd": sess_cwd,
            "has_schedules": name in scheduled_agents,
            "schedule_last_run": agent_schedule_summary.get(name, {}).get("last_run"),
            "schedule_next_run": agent_schedule_summary.get(name, {}).get("next_run"),
            "recap": transcript["recap"],
            "recap_ts": transcript["recap_ts"],
            "pr": transcript["pr"],
        })

    # Belt-and-braces share revocation on session end (see _reap_shares).
    _reap_shares({a["window_id"]: a["claude_running"] for a in agents})

    return jsonify(agents)


@app.route("/api/agents/msg", methods=["POST"])
@require_auth
def api_agents_msg():
    data = request.get_json(force=True)
    agent = data.get("agent", "")
    message = data.get("message", "")
    if not agent or not message:
        return jsonify({"error": "agent and message required"}), 400
    # One resolver for both branches — the same live-window authority /api/agents
    # itself reports from, so a window this API calls "busy" is always messageable.
    wid = messenger.resolve_window(agent)
    if not wid:
        return jsonify({"error": f"agent {agent} not found"}), 404
    # A slash command goes in raw (a "[sender] /foo" prefix would not be a command).
    ok = (messenger.send_tmux(wid, message) if message.startswith("/")
          else messenger.send_message("dashboard", wid, message))
    return jsonify({"sent": ok, "agent": agent})


# Mobile control bar: whitelisted key / scroll injection via tmux send-keys.
# Keys are delivered at the tmux layer (robust, ttyd/xterm-independent).
# The full C-<a..z> set is whitelisted so the mobile keybar's sticky-Ctrl layer
# can compose Ctrl with any letter (control codes — harmless), not just a fixed
# handful.
_TERM_KEYS = {
    "Up", "Down", "Left", "Right", "Escape", "Tab", "BTab", "Enter", "BSpace",
    "PageUp", "PageDown", "Home", "End",
} | {f"C-{c}" for c in "abcdefghijklmnopqrstuvwxyz"}

# Punctuation that's a chore to reach on a phone keyboard — sent literally
# (`send-keys -l` disables key-name lookup) so the mobile keybar can offer them
# as one-tap keys. Matches Moshi's terminal-keyboard special-character row.
_TERM_LITERAL = {"|", "/", "\\", "~", "-", "_"}


def _term_target(agent: str) -> str | None:
    """Resolve a terminal handle to a tmux target `<session>:<wid>`.

    The dashboard keys live terminals by stable window id (e.g. `@25`), so most
    calls arrive as a wid — used directly (rename-proof). A plain display name
    is still accepted (resolved via discovery) for compatibility / external
    callers. Returns None if a name can't be resolved to a live window."""
    if agent.startswith("@"):
        return f"{TMUX_SESSION}:{agent}"
    wid = discovery.get_window_id(agent)
    return f"{TMUX_SESSION}:{wid}" if wid else None


def _term_keyargv(target: str, key: str) -> list[str] | None:
    """Resolve a whitelisted key token to a full tmux argv, or None if not allowed."""
    if key == "scroll":               # enter copy-mode (prefix-independent)
        return ["tmux", "copy-mode", "-t", target]
    if key == "scroll-exit":          # leave copy-mode
        return ["tmux", "send-keys", "-t", target, "-X", "cancel"]
    if key in _TERM_LITERAL:          # punctuation — send the char, not a key name
        return ["tmux", "send-keys", "-t", target, "-l", key]
    if key in _TERM_KEYS:
        return ["tmux", "send-keys", "-t", target, key]
    return None


@app.route("/api/term/key", methods=["POST"])
@require_auth
def api_term_key():
    _require_terminals()
    data = request.get_json(force=True)
    agent = data.get("agent", "")
    key = data.get("key", "")
    target = _term_target(agent)
    if not target:
        return jsonify({"error": f"agent {agent} not found"}), 404
    argv = _term_keyargv(target, key)
    if argv is None:
        return jsonify({"error": "key not allowed"}), 400
    try:
        subprocess.run(argv, check=True, capture_output=True, timeout=5)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as e:
        return jsonify({"error": str(e)}), 500
    return jsonify({"sent": key, "agent": agent})


def _terminals_port_map() -> dict:
    """The {agent: ttyd_port} map the terminal wall routes against. File-read
    only; written by scripts/agent-terminals.sh on each ~12s poll. Missing/
    garbled file → empty map (no terminals ready yet)."""
    try:
        return json.loads(TERMINALS_MAP.read_text())
    except Exception:
        return {}


# ---------------------------------------------------------------------------
# Terminal wall: same-origin ttyd reverse proxy (/term/<wid>/...)
#
# AUTH-FREE by design (see module docstring): the dashboard binds 127.0.0.1 and
# the tailnet is the trust boundary. Each ttyd is spawned by
# scripts/agent-terminals.sh with `--base-path /term/<wid>`, so it emits its
# own asset/websocket URLs under that prefix. The proxy forwards `/term/<wid>/*`
# verbatim to 127.0.0.1:<port>/term/<wid>/* — HTTP passthrough for the page +
# assets, WebSocket upgrade for the live tty stream. No path rewriting: the
# base-path makes the upstream path identical to what the browser requested.
# ---------------------------------------------------------------------------

import urllib.error  # noqa: E402
import urllib.request  # noqa: E402

# Hop-by-hop / length headers we must not copy from the ttyd response (Flask
# recomputes them; copying breaks the connection or double-counts the body).
_PROXY_DROP_HEADERS = {
    "transfer-encoding", "content-encoding", "content-length",
    "connection", "keep-alive",
}

# Clipboard-image paste shim injected into ttyd's HTML (see term_http). xterm.js
# silently drops non-text clipboard items, so a screenshot (Win+Shift+S, "Copy
# image", etc.) never reaches Claude Code in a pane. We listen for paste in the
# capture phase before xterm.js: if a clipboard item is an image/* blob, POST it
# to /api/term/paste-image and type the returned file path into the pane via
# /api/term/paste — matching how Claude Code consumes pasted images on a native
# terminal (a file path, not inline bytes). Text pastes fall through untouched.
# The window id comes from the iframe path (/term/<wid>/), chela's routing key.
_TERM_PASTE_SHIM = (
    "<script>(function(){"
    "var m=location.pathname.match(/\\/term\\/([^\\/]+)/);"
    "if(!m)return;"
    "var wid=decodeURIComponent(m[1]);"
    "async function handle(e){"
    "var items=(e.clipboardData&&e.clipboardData.items)||[];"
    "var blob=null;"
    "for(var i=0;i<items.length;i++){"
    "var it=items[i];"
    "if(it.kind==='file'&&it.type&&it.type.indexOf('image/')===0){"
    "blob=it.getAsFile();break;}}"
    "if(!blob)return;"
    "e.preventDefault();e.stopPropagation();"
    "try{"
    "var fd=new FormData();fd.append('agent',wid);"
    "fd.append('image',blob,blob.name||'paste');"
    "var r=await fetch('/api/term/paste-image',"
    "{method:'POST',body:fd,credentials:'same-origin'});"
    "if(!r.ok)return;"
    "var j=await r.json();"
    "if(!j||!j.path)return;"
    "await fetch('/api/term/paste',"
    "{method:'POST',headers:{'Content-Type':'application/json'},"
    "credentials:'same-origin',"
    "body:JSON.stringify({agent:wid,text:j.path})});"
    "}catch(err){console.error('paste-image',err);}}"
    "document.addEventListener('paste',handle,true);"
    "})();</script>"
)

# Ctrl/Cmd+V paste shim injected into ttyd's HTML (see term_http). By terminal
# convention xterm.js sends a literal ^V on Ctrl+V and only pastes on
# Ctrl+Shift+V / right-click, which surprises people who expect Ctrl+V to paste.
# This catches plain Ctrl/Cmd+V in the capture phase (before xterm), swallows it
# so no ^V reaches the shell, reads the clipboard, and routes it through
# /api/term/paste (bracketed paste at the tmux layer). It also handles an image
# on the clipboard — via /api/term/paste-image, like the paste-event shim — so
# Ctrl+V never regresses image paste. Ctrl+Shift+V / right-click are left to the
# native path. wid comes from the iframe path (/term/<wid>/), as above.
_TERM_PASTE_KEY_SHIM = (
    "<script>(function(){"
    "var m=location.pathname.match(/\\/term\\/([^\\/]+)/);"
    "if(!m)return;"
    "var wid=decodeURIComponent(m[1]);"
    "function postJSON(p,b){return fetch(p,{method:'POST',"
    "headers:{'Content-Type':'application/json'},credentials:'same-origin',"
    "body:JSON.stringify(b)});}"
    "async function pasteText(t){if(t)await postJSON('/api/term/paste',{agent:wid,text:t});}"
    "async function pasteImage(blob){"
    "var fd=new FormData();fd.append('agent',wid);"
    "fd.append('image',blob,blob.name||'paste');"
    "var r=await fetch('/api/term/paste-image',{method:'POST',body:fd,"
    "credentials:'same-origin'});"
    "if(!r.ok)return;var j=await r.json();if(j&&j.path)await pasteText(j.path);}"
    "async function onKey(e){"
    "if(!(e.ctrlKey||e.metaKey)||e.shiftKey||e.altKey)return;"
    "if(e.key!=='v'&&e.key!=='V')return;"
    "if(!navigator.clipboard)return;"
    "e.preventDefault();e.stopImmediatePropagation();"
    "try{"
    "if(navigator.clipboard.read){"
    "var items=await navigator.clipboard.read();"
    "for(var i=0;i<items.length;i++){"
    "var t=(items[i].types||[]).filter(function(x){return x.indexOf('image/')===0;})[0];"
    "if(t){await pasteImage(await items[i].getType(t));return;}}"
    "for(var n=0;n<items.length;n++){"
    "if((items[n].types||[]).indexOf('text/plain')>=0){"
    "await pasteText(await (await items[n].getType('text/plain')).text());return;}}"
    "}else if(navigator.clipboard.readText){"
    "await pasteText(await navigator.clipboard.readText());}"
    "}catch(err){console.error('paste-key',err);}}"
    "document.addEventListener('keydown',onKey,true);"
    "})();</script>"
)

# Command-palette key shim injected into ttyd's HTML (see term_http). When focus
# is inside a pane, keydown fires in the iframe document, so the dashboard's
# ⌘K / Ctrl+K palette handler never sees it and the fast jump-to is unreachable
# from a pane. This catches the same combo in the capture phase (before xterm),
# swallows it so no ^K reaches the shell, and calls the parent's openPalette()
# (same-origin — the iframe is proxied through the dashboard, so window.parent is
# accessible). Note: this shadows readline's Ctrl+K (kill-to-end-of-line) inside
# panes, which is the documented trade-off for a global palette hotkey.
_TERM_PALETTE_KEY_SHIM = (
    "<script>(function(){"
    "function onKey(e){"
    "if(!(e.ctrlKey||e.metaKey)||e.altKey||e.shiftKey)return;"
    "if(e.key!=='k'&&e.key!=='K')return;"
    "if(!window.parent||window.parent===window)return;"
    "e.preventDefault();e.stopImmediatePropagation();"
    # Stage 0: dashboard fns moved to the window.chela namespace under ES modules.
    "try{var c=window.parent.chela;if(c&&typeof c.openPalette==='function')c.openPalette();}"
    "catch(err){}}"
    "document.addEventListener('keydown',onKey,true);"
    "})();</script>"
)

# Touch-to-scroll shim injected into ttyd's HTML (see term_http). tmux mouse mode
# is on, so xterm.js already turns wheel events into scroll sequences (scrollback
# in a shell, forwarded to TUI apps like Claude Code) — that's why scrolling works
# with a trackpad/wheel on desktop. But a touch drag never emits a wheel event, so
# phones can't scroll the pane. This translates a vertical one-finger drag into
# synthetic line WheelEvents on the element under the finger; xterm.js does the
# rest. A tap (movement under the slop) falls through untouched, so it still
# focuses the pane / opens the keyboard. Desktop is unaffected (no touch events).
_TERM_SCROLL_SHIM = (
    "<script>(function(){"
    "var SLOP=8,LINE=18,acc=0,refY=0,active=false,moved=false;"
    "function onStart(e){"
    "if(e.touches.length!==1){active=false;return;}"
    "active=true;moved=false;acc=0;refY=e.touches[0].clientY;}"
    "function onMove(e){"
    "if(!active||e.touches.length!==1)return;"
    "var t=e.touches[0],y=t.clientY;"
    "if(!moved&&Math.abs(y-refY)<SLOP)return;"
    "moved=true;e.preventDefault();"
    "acc+=y-refY;refY=y;"
    "while(Math.abs(acc)>=LINE){"
    "var dir=acc>0?1:-1;acc-=dir*LINE;"
    "var el=document.elementFromPoint(t.clientX,t.clientY)||document.body;"
    "el.dispatchEvent(new WheelEvent('wheel',"
    "{deltaY:-dir,deltaMode:1,clientX:t.clientX,clientY:t.clientY,"
    "bubbles:true,cancelable:true}));}}"
    "function onEnd(){active=false;}"
    "var o={passive:false,capture:true};"
    "document.addEventListener('touchstart',onStart,o);"
    "document.addEventListener('touchmove',onMove,o);"
    "document.addEventListener('touchend',onEnd,o);"
    "document.addEventListener('touchcancel',onEnd,o);"
    "})();</script>"
)

# Scrollbar CSS injected into ttyd's HTML (see term_http). The dashboard's global
# `*{scrollbar-…}` rule can't cross the iframe boundary into the ttyd document, so
# the terminal panes would otherwise show the OS default scrollbar. This mirrors
# the dashboard's scrollbar (style.css :root --border #21262d, hover #30363d) so the
# wall's scrollbars match the rest of the UI. Literal hex — the ttyd page has no
# CSS vars.
# Bundled fonts, served from /static and injected as @font-face rules into the
# ttyd page so xterm.js resolves every glyph on ANY viewer — no device-side font
# install needed. URLs are same-origin absolute (/static/...) so they resolve
# against the dashboard, not the iframe's /term/<wid>/ base path.
#
# WHY bundle rather than rely on installed fonts: CSS font matching is PER-GLYPH,
# so the browser walks the whole family stack for every character. On a viewer
# without these fonts installed, a Latin letter would fall past everything and
# land on the Hebrew font — and a proportional Hebrew font there breaks monospace
# alignment for ENGLISH too. Bundling a real Latin monospace pins English on
# every device, leaving the Hebrew face to handle only Hebrew.
#
# The Settings > Terminal font picker chooses one Latin face × one Hebrew face
# (+ size); the injected _TERM_FONT_PREF_SHIM builds window.term's fontFamily
# from the selection. Every option is declared here, but only the SELECTED faces
# are actually downloaded — an unused @font-face never fetches. See
# static/fonts/README.md for per-font licenses (all OFL-1.1 except Miriam Mono
# CLM, GPL-2 — the only free MONOSPACE Hebrew font, hence its inclusion).
#
# Manifest: (family, filename, variable?, weight-if-static). Variable fonts get a
# 100–900 weight range in one face; static fonts get one @font-face per weight.
_TERM_FONTS = [
    # Icons — font-display:block avoids a box-flash before the icon font loads.
    ("Symbols Nerd Font", "SymbolsNerdFontMono-Regular.ttf", None, None),
    # English / Latin monospace (picker: English face)
    ("JetBrains Mono",  "JetBrainsMono.ttf",       True,  None),
    ("Fira Code",       "FiraCode.ttf",            True,  None),
    ("IBM Plex Mono",   "IBMPlexMono-Regular.ttf", False, "normal"),
    ("IBM Plex Mono",   "IBMPlexMono-Bold.ttf",    False, "bold"),
    ("Source Code Pro", "SourceCodePro.ttf",       True,  None),
    ("Cascadia Code",   "CascadiaCode.ttf",        True,  None),
    # Hebrew (picker: Hebrew face). Miriam is the only monospace one.
    ("Miriam Mono CLM", "MiriamMonoCLM-Book.ttf",  False, "normal"),
    ("Miriam Mono CLM", "MiriamMonoCLM-Bold.ttf",  False, "bold"),
    ("Noto Sans Hebrew", "NotoSansHebrew.ttf",     True,  None),
    ("Heebo",           "Heebo.ttf",               True,  None),
    ("Assistant",       "Assistant.ttf",           True,  None),
    ("Rubik",           "Rubik.ttf",               True,  None),
    ("Frank Ruhl Libre", "FrankRuhlLibre.ttf",     True,  None),
    ("David Libre",     "DavidLibre-Regular.ttf",  False, "normal"),
    ("David Libre",     "DavidLibre-Bold.ttf",     False, "bold"),
]


def _term_font_face(family, filename, variable, weight):
    if variable is None:  # Symbols icon font — block to avoid glyph-box flash
        disp = "font-display:block"
        wgt = ""
    else:
        disp = "font-display:swap"
        wgt = "font-weight:100 900;" if variable else "font-weight:%s;" % weight
    return ("@font-face{font-family:'%s';%s"
            "src:url('/static/fonts/%s') format('truetype');%s}"
            % (family, wgt, filename, disp))


_TERM_FONT_CSS = (
    "<style>" + "".join(_term_font_face(*f) for f in _TERM_FONTS) + "</style>"
)

_TERM_SCROLLBAR_CSS = (
    "<style>"
    "*{scrollbar-width:thin;scrollbar-color:#21262d transparent}"
    "*:hover{scrollbar-color:#30363d transparent}"
    "::-webkit-scrollbar{width:8px;height:8px}"
    "::-webkit-scrollbar-track{background:transparent}"
    "::-webkit-scrollbar-thumb{background:#21262d;border-radius:8px;"
    "border:2px solid transparent;background-clip:content-box}"
    "::-webkit-scrollbar-thumb:hover{background:#30363d;background-clip:content-box}"
    "::-webkit-scrollbar-corner{background:transparent}"
    "</style>"
)

# Font-preference shim. Two jobs in one:
#   (1) Apply the user's Settings > Terminal font choice (family + size) to this
#       ttyd's xterm live. ttyd exposes the Terminal as `window.term`; we set its
#       fontFamily/fontSize, then re-fit (fontSize changes the cell grid) and
#       refresh. The choice lives in localStorage (chela_term_latin /
#       chela_term_font / chela_term_fontsize), shared same-origin with the
#       dashboard, so a `storage` event fires here whenever the settings panel
#       changes it — live switching, no reload. `window.chelaApplyTermPrefs` is
#       also exposed so the parent frame can poke it directly for instant feedback.
#   (2) Fix the FOUT/atlas bug: xterm rasterises glyphs into a texture atlas ONCE
#       at first paint, using whatever font was ready then. Our @font-faces load
#       async (font-display:swap), so the first atlas uses the fallback and never
#       rebuilds — the real font only appeared on cells xterm re-drew (a text
#       selection/scroll), i.e. the font "changed" on select. apply() awaits the
#       chosen faces via the CSS Font Loading API, then clears the atlas
#       (clearTextureAtlas) so the next render rebuilds it correctly.
# Reconciles on a bounded interval (not a one-shot) because ttyd re-applies its
# launch client-options — including fontSize — when the WebSocket connects, which
# can land AFTER a single apply() and revert a pane to the default size. apply()
# early-returns once the terminal already matches, so the interval is a cheap
# no-op after it converges; it also re-applies on tab refocus and on storage
# changes. The interval also covers `window.term` not existing yet at first tick.
_TERM_FONT_PREF_SHIM = (
    "<script>(function(){"
    "var LAT={jetbrains:\"JetBrains Mono\",firacode:\"Fira Code\","
    "plex:\"IBM Plex Mono\",source:\"Source Code Pro\",cascadia:\"Cascadia Code\"};"
    "var HEB={miriam:\"Miriam Mono CLM\",noto:\"Noto Sans Hebrew\",heebo:\"Heebo\","
    "assistant:\"Assistant\",rubik:\"Rubik\",frankruhl:\"Frank Ruhl Libre\","
    "david:\"David Libre\"};"
    "function apply(){var t=window.term;if(!t)return;"
    "var lat=LAT[localStorage.getItem('chela_term_latin')]||LAT.jetbrains;"
    "var heb=HEB[localStorage.getItem('chela_term_font')]||HEB.miriam;"
    "var s=window.__CHELA_GRID_FONT__||(parseInt(localStorage.getItem('chela_term_fontsize'),10)||14);"
    "var fam=\"'\"+lat+\"','Symbols Nerd Font','\"+heb+\"',monospace\";"
    "if(t.options&&t.options.fontSize===s&&t.options.fontFamily===fam)return;"
    "var L=[s+\"px '\"+lat+\"'\",\"bold \"+s+\"px '\"+lat+\"'\",s+\"px '\"+heb+\"'\","
    "\"bold \"+s+\"px '\"+heb+\"'\",s+\"px 'Symbols Nerd Font'\"];"
    "var P=(document.fonts&&document.fonts.load)?"
    "Promise.all(L.map(function(f){return document.fonts.load(f).catch(function(){});}))"
    ":Promise.resolve();"
    "P.then(function(){try{"
    "if(t.options){t.options.fontFamily=fam;t.options.fontSize=s;}"
    "else if(t.setOption){t.setOption('fontFamily',fam);t.setOption('fontSize',s);}"
    "if(t.clearTextureAtlas)t.clearTextureAtlas();"
    "if(t.fit&&!window.__CHELA_GRID_FONT__)t.fit();"
    "if(t.refresh&&t.rows)t.refresh(0,t.rows-1);"
    "}catch(e){}});}"
    "window.chelaApplyTermPrefs=apply;"
    "var n=0,iv=setInterval(function(){apply();if(++n>60)clearInterval(iv);},500);"
    "document.addEventListener('visibilitychange',function(){"
    "if(!document.hidden)apply();});"
    "window.addEventListener('storage',function(e){if(!e.key||"
    "e.key==='chela_term_font'||e.key==='chela_term_latin'"
    "||e.key==='chela_term_fontsize')apply();});"
    "})();</script>"
)


# Owner-presence iframe shim (chela module): the IN-IFRAME half of the dashboard
# owner's presence surface, served as a static module and injected into the ttyd
# page. It SELF-GATES purely on this window's server "shared" flag so normal wall
# panes are untouched, AND so the host can truly revoke — un-sharing or a restart
# (which clears _SHARED) kills the link, with no client-side ?collab bypass.
#
# SECURITY BOUNDARY: the shim holds NO secret and NO relay socket — the pairing key
# and crypto live only in the parent dashboard client (static/collab/presence-owner
# .js). The shim just maps the owner's pointer over the grid → normalized coords →
# postMessage to the parent, and draws the peers the parent sends back. So only the
# non-secret {shared, wid} is injected here (the old relay/prefix/grid injection is
# gone — the parent derives all of that from the owner-only /share-info).
def _term_presence_shim(wid: str) -> str:
    """Per-wid shim config + client. Carries only this window's live "shared" flag
    and its wid; the shim gates solely on the flag and talks coordinates (never the
    secret) to the parent over postMessage."""
    cfg = json.dumps({
        "wid": wid,
        "shared": wid in _SHARED,
    })
    return ("<script>window.__CHELA_COLLAB__=" + cfg + ";</script>"
            '<script type="module" src="/static/collab/presence-shim.js"></script>')


@app.route("/term/<wid>/", defaults={"rest": ""}, methods=["GET", "POST"])
@app.route("/term/<wid>/<path:rest>", methods=["GET", "POST"])
@require_auth
def term_http(wid, rest):
    """HTTP passthrough for a ttyd's page + assets (the non-WebSocket half)."""
    _require_terminals()
    port = _terminals_port_map().get(wid)
    if not port:
        abort(404)
    upstream = f"http://127.0.0.1:{port}/term/{wid}/{rest}"
    if request.query_string:
        upstream += "?" + request.query_string.decode()
    data = request.get_data() or None
    req = urllib.request.Request(upstream, data=data, method=request.method)
    for h in ("Content-Type", "Accept", "Accept-Language"):
        v = request.headers.get(h)
        if v:
            req.add_header(h, v)
    try:
        resp = urllib.request.urlopen(req, timeout=15)  # noqa: S310 - loopback only
    except urllib.error.HTTPError as e:
        resp = e
    except Exception:
        abort(502)
    body = resp.read()
    status = getattr(resp, "status", None) or getattr(resp, "code", 200)
    headers = [
        (k, v) for k, v in resp.getheaders()
        if k.lower() not in _PROXY_DROP_HEADERS
    ]
    # Inject the clipboard-image paste shim into ttyd's HTML page. xterm.js drops
    # non-text clipboard items, so a pasted screenshot never reaches Claude Code;
    # the shim intercepts it and routes it through /api/term/paste-image. Only the
    # (small) HTML doc is rewritten; assets pass through untouched. content-length
    # is in _PROXY_DROP_HEADERS, so Flask recomputes it for the modified body.
    ctype = (resp.headers.get("Content-Type") or "")
    if "text/html" in ctype.lower():
        html = body.decode("utf-8", "replace")
        shims = (_TERM_FONT_CSS + _TERM_FONT_PREF_SHIM + _term_presence_shim(wid)
                 + _TERM_PASTE_SHIM + _TERM_PASTE_KEY_SHIM + _TERM_PALETTE_KEY_SHIM
                 + _TERM_SCROLL_SHIM + _TERM_SCROLLBAR_CSS)
        html = (html.replace("</head>", shims + "</head>", 1)
                if "</head>" in html else html + shims)
        body = html.encode("utf-8")
    return Response(body, status=status, headers=headers)


if _sock is not None:
    @_sock.route("/term/<wid>/ws")
    def term_ws(ws, wid):
        """Bridge the browser's ttyd WebSocket to the upstream ttyd on 127.0.0.1.

        ttyd requires the `tty` subprotocol, so the upstream client offers it
        (the browser already does). Two directions are pumped: a daemon thread
        copies upstream→browser while this handler thread copies browser→
        upstream; either side closing tears both down.
        """
        if not config.TERMINALS_ENABLED:
            return
        port = _terminals_port_map().get(wid)
        if not port:
            return
        import simple_websocket
        import threading
        try:
            upstream = simple_websocket.Client(
                f"ws://127.0.0.1:{port}/term/{wid}/ws", subprotocols=["tty"],
                ping_interval=25,   # keepalive on the Flask↔ttyd hop (see SOCK_SERVER_OPTIONS)
            )
        except Exception:
            return

        def _pump_upstream_to_browser():
            try:
                while True:
                    data = upstream.receive()
                    if data is None:
                        break
                    ws.send(data)
            except Exception:
                pass
            finally:
                try:
                    ws.close()
                except Exception:
                    pass

        t = threading.Thread(target=_pump_upstream_to_browser, daemon=True)
        t.start()
        try:
            while True:
                data = ws.receive()
                if data is None:
                    break
                upstream.send(data)
        except Exception:
            pass
        finally:
            try:
                upstream.close()
            except Exception:
                pass


@app.route("/api/term/ready")
@require_auth
def api_term_ready():
    """Cheap readiness probe for a freshly-spawned terminal. The /term/<agent>/
    iframe 404s until agent-terminals.sh assigns a port, so the frontend polls
    this before swapping a placeholder for the real iframe. `ready` = the agent
    is present in the port map with a truthy port. No network call to ttyd —
    just a file read of the same map the wall proxy uses."""
    _require_terminals()
    agent = request.args.get("agent", "")
    port = _terminals_port_map().get(agent)
    return jsonify({"ready": bool(port), "port": port if port else None})


def _webterm_session(wid: str) -> str:
    """The grouped tmux session name a wid's ttyd attaches to. Mirrors
    scripts/agent-terminals.sh: WEBTERM_PREFIX = webterm_<sanitized session>_,
    grp = WEBTERM_PREFIX<sanitized wid>, where sanitize = tr -c 'A-Za-z0-9_' '_'.
    Kept in lockstep with that script — both must agree for the count to be
    meaningful (a mismatch just yields 0, which the wall treats as "no contention,
    don't tear down")."""
    def san(s):
        return re.sub(r"[^A-Za-z0-9_]", "_", s)
    return f"webterm_{san(TMUX_SESSION)}_{san(wid)}"


@app.route("/api/term/clients")
@require_auth
def api_term_clients():
    """Per-wid ttyd client count: how many browser connections currently share
    each pane's grouped tmux session. The wall reads this so a backgrounded tab
    only releases panes that actually have >1 viewer — with a single viewer there
    is no window-size contention to resolve (a tmux window has one size shared by
    all its clients), so tearing it down would churn the connection for nothing.
    One `tmux list-clients` call, counted by session; never touches ttyd."""
    _require_terminals()
    wids = list(_terminals_port_map().keys())
    counts = {w: 0 for w in wids}
    try:
        out = subprocess.run(
            ["tmux", "list-clients", "-F", "#{client_session}"],
            capture_output=True, text=True, timeout=5,
        )
        if out.returncode == 0:
            per_session: dict = {}
            for line in out.stdout.splitlines():
                s = line.strip()
                if s:
                    per_session[s] = per_session.get(s, 0) + 1
            for w in wids:
                counts[w] = per_session.get(_webterm_session(w), 0)
    except Exception:
        pass  # tmux hiccup → all-zero counts → wall skips teardown (safe)
    return jsonify(counts)


# Per-wid share state (in-memory; presence-only, no auth): wid -> {"cols","rows"},
# the PRESENTER (master/host) pane dims. When set, term_http injects
# "shared":true + these dims into __CHELA_COLLAB__ so presence.js activates on a
# clean /term/<wid>/ link (no ?collab) and every client pins to the master's grid
# — the master fills their pane, joiners letterbox to it. Dict writes are atomic
# under CPython's GIL, so no lock needed.
_SHARED: dict[str, dict] = {}

# Reaper bookkeeping: wid -> monotonic time first seen dead, so a brief blip
# (agent restart, pid-detection race) doesn't revoke a live share.
_share_dead_since: dict[str, float] = {}
_SHARE_REAP_GRACE = 8.0  # s a shared wid may be gone/agentless before revoke


def _reap_shares(claude_running_by_wid: dict[str, bool]) -> None:
    """Deferred half of "no share outlives its session": each /api/agents tick,
    reconcile _SHARED against reality and auto-revoke any shared wid whose window
    is gone or whose agent has ended (claude process exited). A short grace rides
    out restart blips. Complements the bridge's own fail-closed (which catches the
    ttyd-reaped case even when nobody is polling)."""
    port_map = _terminals_port_map()
    now = time.monotonic()
    for wid in list(_SHARED):
        alive = wid in port_map and claude_running_by_wid.get(wid, False)
        if alive:
            _share_dead_since.pop(wid, None)
            continue
        first = _share_dead_since.setdefault(wid, now)
        if now - first >= _SHARE_REAP_GRACE:
            log.info("share reaper: revoking %s (window gone or agent ended)", wid)
            _revoke_share(wid)


def _clamp_dim(v, lo, hi, default):
    try:
        return max(lo, min(hi, int(v)))
    except (TypeError, ValueError):
        return default


def _dims_for(wid: str):
    d = _SHARED.get(wid)
    return (d["cols"], d["rows"]) if d else (config.TERM_COLS, config.TERM_ROWS)


def _pin_grid(wid: str, cols: int, rows: int) -> None:
    """Pin a window to exact cols x rows (window-size manual). The presenter's
    dims — so all viewers share one PTY size; each letterboxes it client-side."""
    subprocess.run(["tmux", "set-window-option", "-t", wid, "window-size", "manual"],
                   check=True, timeout=5)
    subprocess.run(["tmux", "resize-window", "-t", wid, "-x", str(cols), "-y", str(rows)],
                   check=True, timeout=5)


def _unpin_grid(wid: str) -> None:
    """Restore dynamic viewport-fit (window-size largest)."""
    subprocess.run(["tmux", "set-window-option", "-t", wid, "window-size", "largest"],
                   check=True, timeout=5)
    subprocess.run(["tmux", "set-window-option", "-t", wid, "aggressive-resize", "on"],
                   check=True, timeout=5)


@app.route("/api/term/<wid>/grid", methods=["POST"])
@require_auth
def api_term_grid(wid):
    """Set the shared grid for a window. The PRESENTER posts its live pane dims
    ({cols,rows}) once 2+ peers are present → pin the window there so joiners
    adopt them; posting {peers:1} (solo) restores dynamic viewport-fit. Only the
    presenter posts; joiners read the dims via awareness / the injected config."""
    _require_terminals()
    if wid not in _terminals_port_map():
        abort(404)  # only real terminal windows; also blocks tmux target injection
    data = request.get_json(force=True) or {}
    try:
        if "cols" in data and "rows" in data:
            cols = _clamp_dim(data.get("cols"), 20, 500, config.TERM_COLS)
            rows = _clamp_dim(data.get("rows"), 6, 300, config.TERM_ROWS)
            if wid in _SHARED:
                _SHARED[wid] = {"cols": cols, "rows": rows}  # remember the latest master dims
            _pin_grid(wid, cols, rows)
            return jsonify({"ok": True, "mode": "fixed", "cols": cols, "rows": rows})
        peers = int(data.get("peers", 1))
        if peers >= 2:
            cols, rows = _dims_for(wid)
            _pin_grid(wid, cols, rows)
            return jsonify({"ok": True, "mode": "fixed", "cols": cols, "rows": rows})
        _unpin_grid(wid)
        return jsonify({"ok": True, "mode": "dynamic"})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# Owner-only share info (join URL + base32 pairing code), kept OUT of _SHARED and
# every broadcast report (/api/agents, /api/term/shared) — the pairing code is the
# capability, so only the authed owner sees it, via api_term_share_info.
_share_info: dict[str, dict] = {}


def _revoke_share(wid: str) -> None:
    """Fully revoke a share: drop the flag + owner info and stop the E2E bridge
    (abandoning its relay room). Called on manual un-share, the reaper, and the
    bridge's own fail-closed hook — all idempotent. Touches NO tmux window state:
    the share never resized the owner's window, so there is nothing to restore (the
    tailnet presenter-grid path owns its own pin/unpin via /api/term/<wid>/grid)."""
    _SHARED.pop(wid, None)
    _share_info.pop(wid, None)
    _share_dead_since.pop(wid, None)
    collab_stream.stop_bridge(wid)


@app.route("/api/term/<wid>/share", methods=["POST"])
@require_auth
def api_term_share(wid):
    """Turn sharing on/off. On: remember the presenter's pane dims, mint an E2E
    bridge (pumps the terminal, encrypted, into a per-share relay room) and return
    its join URL + base32 pairing code — the joiner pastes the code to derive keys.
    Off: fully revoke — stop the bridge, abandon the room, restore the grid. The
    pane's iframe is reloaded client-side to re-serve the page with the new flag."""
    _require_terminals()
    if wid not in _terminals_port_map():
        abort(404)
    data = request.get_json(force=True) or {}
    on = bool(data.get("on", True))
    if not on:
        _revoke_share(wid)
        return jsonify({"ok": True, "shared": False})
    # Single grid authority for the E2E path: the LIVE tmux window size — the exact
    # dims the bridge streams (collab_stream._window_dims). Seed _SHARED with THAT,
    # not the posted pane dims or the 120x30 default, so the advertised grid matches
    # the stream instead of drifting (e.g. advertising 120x30 while streaming 110-
    # wide). The joiner then follows the live size via T_META. Sharing NEVER resizes
    # the owner's window — a live workflow must stream undisturbed.
    cols, rows = collab_stream._window_dims(wid)
    _SHARED[wid] = {"cols": cols, "rows": rows}
    # Start the E2E stream bridge; on_revoke fires if it fails closed on session
    # death, so a share can never outlive its terminal (see collab_stream).
    code = collab_stream.start_bridge(wid, on_revoke=_revoke_share)
    # Full access: a paired joiner (they hold the code) can type + scroll — no grant.
    info = {"pairing_code": code, "join_url": collab_stream.join_url(wid)} if code else {}
    _share_info[wid] = info
    return jsonify({"ok": True, "shared": True, **info})


@app.route("/api/term/<wid>/share-info")
@require_auth
def api_term_share_info(wid):
    """Owner-only: the join URL + pairing code for a currently-shared wid, so the
    share popover can reopen without re-sharing (which would rotate the code)."""
    _require_terminals()
    return jsonify(_share_info.get(wid, {}))


@app.route("/api/term/shared")
@require_auth
def api_term_shared():
    """Currently-shared window ids → their master dims, so the wall restores
    share-button state on load / across reloads. Pairing codes are deliberately
    NOT here — they live only in _share_info (owner-only)."""
    _require_terminals()
    return jsonify(_SHARED)


_TERM_PASTE_MAX = 64 * 1024  # reject pastes larger than 64 KB


@app.route("/api/term/paste", methods=["POST"])
@require_auth
def api_term_paste():
    """Inject clipboard text (read browser-side) into an agent's pane.

    Clipboard data lives on the *client* device, so the browser reads it and
    ships the text here; we deliver it at the tmux layer via a dedicated
    buffer + bracketed paste (`-p`) so Claude Code / shells treat it as a
    paste rather than executing it line-by-line. `-d` discards the buffer.
    """
    _require_terminals()
    data = request.get_json(force=True)
    agent = data.get("agent", "")
    text = data.get("text", "")
    if not isinstance(text, str) or not text:
        return jsonify({"error": "empty paste"}), 400
    if len(text.encode("utf-8")) > _TERM_PASTE_MAX:
        return jsonify({"error": "paste too large (max 64 KB)"}), 413
    target = _term_target(agent)
    if not target:
        return jsonify({"error": f"agent {agent} not found"}), 404
    try:
        subprocess.run(
            ["tmux", "load-buffer", "-b", "chela_paste", "-"],
            input=text.encode("utf-8"), check=True, capture_output=True, timeout=5,
        )
        subprocess.run(
            ["tmux", "paste-buffer", "-t", target, "-b", "chela_paste", "-p", "-d"],
            check=True, capture_output=True, timeout=5,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as e:
        return jsonify({"error": str(e)}), 500
    return jsonify({"pasted": len(text), "agent": agent})


# Clipboard image paste. xterm.js drops non-text clipboard items, so a JS shim
# injected into ttyd's HTML intercepts paste events with an image/* blob and
# POSTs the bytes here. We persist by sha256 — same
# image pasted twice reuses the file — and return the path so the shim can type
# it into the pane as a regular text paste, the same shape Claude Code expects
# for pasted images on a native terminal.
_PASTE_IMAGE_DIR = Path("/tmp/chela-paste-images")
_PASTE_IMAGE_MAX_BYTES = 10 * 1024 * 1024  # 10 MB
_PASTE_IMAGE_TTL_SECONDS = 24 * 3600
_PASTE_IMAGE_MIME_EXT = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/webp": ".webp",
    "image/gif": ".gif",
}


def _prune_paste_images() -> None:
    """Drop paste-image files older than 24h. Best-effort, never raises."""
    if not _PASTE_IMAGE_DIR.exists():
        return
    cutoff = time.time() - _PASTE_IMAGE_TTL_SECONDS
    for p in _PASTE_IMAGE_DIR.iterdir():
        try:
            if p.is_file() and p.stat().st_mtime < cutoff:
                p.unlink()
        except OSError:
            pass


try:
    _PASTE_IMAGE_DIR.mkdir(parents=True, exist_ok=True)
    _prune_paste_images()
except OSError as _e:
    log.warning("paste-image dir setup failed: %s", _e)


@app.route("/api/term/paste-image", methods=["POST"])
@require_auth
def api_term_paste_image():
    """Accept an image blob pasted from the browser clipboard.

    Multipart form: `agent` (str) + `image` (file). MIME is validated against a
    PNG/JPEG/WebP/GIF allowlist and the byte count is capped at 10 MB. The file
    is written under /tmp by its sha256, and the absolute path is returned so
    the JS shim can type it into the agent's pane via /api/term/paste.
    """
    _require_terminals()
    agent = (request.form.get("agent") or "").strip()
    f = request.files.get("image")
    if not agent or f is None:
        return jsonify({"error": "agent and image required"}), 400
    mime = (f.mimetype or "").lower()
    ext = _PASTE_IMAGE_MIME_EXT.get(mime)
    if not ext:
        return jsonify({"error": f"mime not allowed: {mime}"}), 415
    # Read up to the cap + 1 byte: anything over the cap is rejected without
    # buffering the rest. Werkzeug streams the upload, so this stays bounded.
    data = f.stream.read(_PASTE_IMAGE_MAX_BYTES + 1)
    if not data:
        return jsonify({"error": "empty image"}), 400
    if len(data) > _PASTE_IMAGE_MAX_BYTES:
        return jsonify({"error": "image too large (max 10 MB)"}), 413
    try:
        _PASTE_IMAGE_DIR.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        return jsonify({"error": f"paste dir unavailable: {e}"}), 500
    _prune_paste_images()
    digest = hashlib.sha256(data).hexdigest()
    out = _PASTE_IMAGE_DIR / f"{digest}{ext}"
    if not out.exists():
        try:
            out.write_bytes(data)
        except OSError as e:
            return jsonify({"error": f"write failed: {e}"}), 500
    log.info(
        "paste-image agent=%s sha256=%s size=%d mime=%s path=%s",
        agent, digest, len(data), mime, out,
    )
    return jsonify({"path": str(out), "sha256": digest, "bytes": len(data)})


@app.route("/api/agents/trigger", methods=["POST"])
@require_auth
def api_agents_trigger():
    """Trigger an agent's scheduled cycle immediately."""
    data = request.get_json(force=True)
    agent = data.get("agent", "")
    if not agent:
        return jsonify({"error": "agent required"}), 400
    tasks = scheduler.list_tasks()
    task = next((t for t in tasks if t.agent_name == agent and t.enabled), None)
    prompt = task.prompt if task else "Please run your work cycle now."
    ok = messenger.send_message("dashboard", agent, prompt)
    return jsonify({"sent": ok, "agent": agent, "prompt_source": "schedule" if task else "default"})


@app.route("/api/agents/broadcast", methods=["POST"])
@require_auth
def api_agents_broadcast():
    data = request.get_json(force=True)
    message = data.get("message", "")
    priority = data.get("priority", "normal")
    if not message:
        return jsonify({"error": "message required"}), 400
    results = messenger.broadcast("dashboard", message, priority)
    return jsonify(results)


# ---------------------------------------------------------------------------
# API: Agent lifecycle
# ---------------------------------------------------------------------------

@app.route("/api/agents/stop", methods=["POST"])
@require_auth
def api_agents_stop():
    data = request.get_json(force=True)
    agent = data.get("agent", "")
    if not agent:
        return jsonify({"error": "agent required"}), 400
    return jsonify(agent_manager.stop_agent(agent))


@app.route("/api/agents/start", methods=["POST"])
@require_auth
def api_agents_start():
    data = request.get_json(force=True)
    agent = data.get("agent", "")
    if not agent:
        return jsonify({"error": "agent required"}), 400
    return jsonify(agent_manager.start_agent(agent))


@app.route("/api/agents/restart", methods=["POST"])
@require_auth
def api_agents_restart():
    data = request.get_json(force=True)
    agent = data.get("agent", "")
    if not agent:
        return jsonify({"error": "agent required"}), 400
    return jsonify(agent_manager.restart_agent(agent))


@app.route("/api/agents/rediscover", methods=["POST"])
@require_auth
def api_agents_rediscover():
    return jsonify(agent_manager.rediscover())


# tmux reserves ':' (window index) and '.' (pane index) in target specs, so a
# window name containing either is unaddressable. Our generated shell-N names
# never hit this, but validate defensively before shelling out.
_WINDOW_NAME_RE = re.compile(r"^[A-Za-z0-9_-]+$")
# Only `claude` (optionally with args) may be auto-launched into a fresh window.
# A plain shell is the no-command default; this keeps the spawn endpoint's reach
# intentional ("launch claude or a shell") rather than an arbitrary-exec route.
_LAUNCH_CMD_RE = re.compile(r"^claude(\s.+)?$")


@app.route("/api/agents/<wid>/rename", methods=["POST"])
@require_auth
def api_agents_rename(wid):
    """Rename a window. The tmux window name is the SINGLE SOURCE OF TRUTH.

    Renaming the tmux window IS the rename — there is no per-client label to keep
    in sync. Everything that shows an agent's name reads it back from tmux, so one
    rename lands everywhere: wall panes, agent cards, nav, tmux itself, and the
    bound Telegram topic (the telegram daemon's reconcile tick renames the forum
    topic to match — see chela.telegram.reconcile.reconcile_bindings). This
    replaced a localStorage-only label that never left the browser it was typed in.

    Keyed by WINDOW ID, never by name: ids are stable across renames and unique,
    while names collide (two repos with the same basename). Body: ``{"name": ...}``.

    The name must survive a reconcile tick, which it does because it isn't generic
    (agent_manager.is_generic_name) — the auto-namers only fill in blanks. We also
    lock the window against tmux's own renamers, so a shell-out can't clobber it.
    """
    body = request.get_json(silent=True) or {}
    name = (body.get("name") or "").strip()
    if not _WINDOW_NAME_RE.match(name):
        return jsonify({"ok": False,
                        "error": "name must be letters, digits, '-' or '_'"}), 400

    windows = discovery.get_windows_by_id()   # {wid: name}
    if wid not in windows:
        return jsonify({"ok": False, "error": f"no such window: {wid}"}), 404
    # Names are an identity users read AND that name→id lookups resolve on, so keep
    # them unique: a duplicate would make two tiles indistinguishable and could send
    # a by-name lookup to the wrong window.
    if any(n == name and w != wid for w, n in windows.items()):
        return jsonify({"ok": False, "error": f"another window is already named {name}"}), 409

    target = f"{TMUX_SESSION}:{wid}"
    try:
        proc = subprocess.run(["tmux", "rename-window", "-t", target, name],
                              capture_output=True, text=True, timeout=10)
    except (FileNotFoundError, subprocess.TimeoutExpired) as e:
        return jsonify({"ok": False, "error": str(e)}), 500
    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout or "tmux rename-window failed").strip()
        return jsonify({"ok": False, "error": err}), 500

    agent_manager.lock_window_name(target)
    log.info("renamed %s: %s -> %s", wid, windows[wid], name)
    return jsonify({"ok": True, "wid": wid, "name": name})


def _next_shell_name(existing: set[str]) -> str:
    """Smallest ``shell-N`` (N >= 1) not already a live window name."""
    n = 1
    while f"shell-{n}" in existing:
        n += 1
    return f"shell-{n}"


@app.route("/api/agents/spawn", methods=["POST"])
@require_auth
def api_agents_spawn():
    """Spawn a tmux window in the chela session, optionally launching `claude`.

    Body (JSON, all optional):
      cwd     — directory to open the window in (default $HOME); must exist.
      command — a command to run once the shell is up. Only `claude` (with
                optional args) is accepted; omit it for a bare interactive shell.

    The ttyd supervisor (scripts/agent-terminals.sh) discovers the new window on
    its own poll and assigns a port within ~12s; until then the pane's iframe
    404s (known latency). When an explicit cwd is given, it's recorded in the
    launcher's Recent list so the sidebar can offer it back as a one-click target.
    """
    _require_terminals()
    body = request.get_json(silent=True) or {}

    cwd_arg = (body.get("cwd") or "").strip()
    launched_dir = None
    if cwd_arg:
        cwd = os.path.realpath(os.path.expanduser(cwd_arg))
        if not os.path.isdir(cwd):
            return jsonify({"ok": False, "error": f"no such directory: {cwd_arg}"}), 400
        launched_dir = cwd
    else:
        cwd = str(Path.home())

    command = (body.get("command") or "").strip()
    if command and not _LAUNCH_CMD_RE.match(command):
        return jsonify({"ok": False, "error": "only `claude` may be auto-launched"}), 400

    # The session may not exist yet (fresh boot, or a `wsl --shutdown` that took the
    # tmux server with it). It's chela's own session, so create it rather than fail
    # the spawn with a raw "error connecting to /tmp/tmux-1000/default" the user can
    # do nothing about. Idempotent + race-safe; only tmux being unreachable fails.
    if not discovery.ensure_session():
        return jsonify({"ok": False,
                        "error": "tmux is unreachable — cannot create the chela session"}), 500

    existing = set(discovery.get_all_windows())
    name = _next_shell_name(existing)
    if not _WINDOW_NAME_RE.match(name):
        return jsonify({"ok": False, "error": f"invalid window name: {name}"}), 500
    try:
        # Trailing ':' forces session resolution. A bare session name is
        # ambiguous to tmux when a *window* shares that name (e.g. a Claude
        # window opened in a dir whose basename == the session name); tmux then
        # targets that window's index and fails with "index N in use".
        proc = subprocess.run(
            ["tmux", "new-window", "-t", f"{TMUX_SESSION}:", "-n", name, "-c", cwd,
             "-P", "-F", "#{window_id}"],
            capture_output=True, text=True, timeout=10,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as e:
        return jsonify({"ok": False, "error": str(e)}), 500
    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout or "tmux new-window failed").strip()
        return jsonify({"ok": False, "error": err}), 500

    # Export CHELA_WID into the fresh shell so the agent knows its own window id
    # (self-identity for peek/read/drive), whether or not a command follows.
    new_wid = (proc.stdout or "").strip()
    target = f"{TMUX_SESSION}:{name}"
    # Pin the name against tmux's automatic-rename (command-follow) and
    # allow-rename (OSC) so a claude spawned here never flickers to the
    # subcommand name mid-shell-out. `new-window -n` already disables
    # automatic-rename; assert both explicitly so the invariant can't drift.
    agent_manager.lock_window_name(new_wid if re.fullmatch(r"@\d+", new_wid) else target)
    if re.fullmatch(r"@\d+", new_wid):
        try:
            subprocess.run(["tmux", "send-keys", "-t", target, "-l",
                            f"export CHELA_WID={new_wid}"],
                           capture_output=True, text=True, timeout=10)
            subprocess.run(["tmux", "send-keys", "-t", target, "Enter"],
                           capture_output=True, text=True, timeout=10)
        except (FileNotFoundError, subprocess.TimeoutExpired) as e:
            log.warning("spawned %s but could not export CHELA_WID: %s", name, e)

    if command:
        # Send the command literally (-l, no key-name lookup) then Enter. tmux
        # buffers send-keys into the pty, so this lands even before the shell has
        # finished drawing its prompt. We start a shell and *send* `claude` rather
        # than running it as the window command so the pane survives claude exiting.
        try:
            subprocess.run(["tmux", "send-keys", "-t", target, "-l", command],
                           capture_output=True, text=True, timeout=10)
            subprocess.run(["tmux", "send-keys", "-t", target, "Enter"],
                           capture_output=True, text=True, timeout=10)
        except (FileNotFoundError, subprocess.TimeoutExpired) as e:
            log.warning("spawned %s but could not send %r: %s", name, command, e)

    if launched_dir:
        try:
            launcher.record_recent(launched_dir)
        except Exception:  # noqa: BLE001 — a store hiccup must never fail the spawn
            log.warning("launcher.record_recent failed for %s", launched_dir, exc_info=True)

    log.info("spawned window %s in %s%s", name, cwd,
             f" running {command!r}" if command else "")
    return jsonify({"ok": True, "name": name, "cwd": cwd})


# ---------------------------------------------------------------------------
# API: Launcher (Recent + Favorites click-to-launch targets)
# ---------------------------------------------------------------------------

@app.route("/api/launcher")
@require_auth
def api_launcher():
    """Recent + Favorites launch directories. Server-side, so the list is shared
    across every device viewing this chela instance."""
    return jsonify(launcher.view())


@app.route("/api/launcher/suggest")
@require_auth
def api_launcher_suggest():
    """Git-repo subdirs of CHELA_PROJECTS_DIR offered as favorite candidates."""
    return jsonify(launcher.suggest())


def _agent_cmd_overrides() -> list[dict]:
    """Discovered workflows that pin ``agent.cmd`` — i.e. that SHADOW the Settings
    permission mode (see dispatcher.resolve_agent_cmd). Surfaced so the drawer can
    say which source is actually winning instead of implying the setting always
    applies. Best-effort: an unreadable workflow is skipped, not fatal."""
    out: list[dict] = []
    try:
        paths = _discover_dispatch_workflows(dispatcher.list_runs())
    except Exception:
        return out
    for p in paths:
        try:
            cmd = load_workflow(p).get("agent", "cmd", default=None)
        except Exception:
            continue
        if isinstance(cmd, str) and cmd.strip():
            out.append({"workflow": p.name, "path": str(p), "cmd": cmd.strip()})
    return out


@app.route("/api/config", methods=["GET", "POST"])
@require_auth
def api_config():
    """Dashboard-editable user prefs (userconfig.json). GET reports the stored
    projects_dir plus the effective dir the launcher will scan (after env/default
    fallback), and the dispatcher's agent permission mode (stored + effective +
    the closed enum of valid modes + any WORKFLOW.md that overrides it). POST
    {projects_dir} and/or {agent_permission_mode} sets or (empty) clears them.

    ``agent_permission_mode`` is validated against dispatcher.PERMISSION_MODES
    HERE, server-side — the UI's <select> is a convenience, not the gate. An
    unknown value is rejected 400 and the stored mode is left untouched (fail
    closed): the mode is interpolated into the shell command that spawns an
    agent, so only the enum may ever reach it. There is deliberately no endpoint
    to set the command itself."""
    if request.method == "POST":
        data = request.get_json(silent=True) or {}
        if "agent_permission_mode" in data:
            mode = (data.get("agent_permission_mode") or "").strip()
            if mode and mode not in dispatcher.PERMISSION_MODES:
                return jsonify({
                    "error": "invalid permission mode",
                    "valid": list(dispatcher.PERMISSION_MODES),
                }), 400
            userconfig.set_(dispatcher.PERMISSION_MODE_KEY, mode)
        if "projects_dir" in data:
            userconfig.set_("projects_dir", (data.get("projects_dir") or "").strip())
    stored_mode = dispatcher.settings_permission_mode()
    return jsonify({
        "projects_dir": userconfig.get("projects_dir", ""),
        "projects_dir_effective": str(launcher._projects_dir()),
        "collab_relay": config.COLLAB_RELAY,
        "agent_permission_mode": stored_mode or "",
        "agent_permission_mode_effective": stored_mode or dispatcher.DEFAULT_PERMISSION_MODE,
        "agent_permission_mode_default": dispatcher.DEFAULT_PERMISSION_MODE,
        "agent_permission_modes": list(dispatcher.PERMISSION_MODES),
        "agent_cmd_overrides": _agent_cmd_overrides(),
    })


def _notify_host(url: str) -> str:
    """Host of the notify URL for display — never the path/query, which for a
    Telegram sendMessage URL carries the bot token. Status surface, not secrets."""
    try:
        return urllib.parse.urlparse(url).netloc or "configured"
    except Exception:
        return "configured"


def _telegram_bridge_running() -> bool:
    """True if a ``chela telegram`` bridge daemon is running.

    Detected by process (``pgrep``), NOT env vars — the dashboard process does
    not carry the bridge's credentials (they live in ``~/.chela/telegram.env``,
    sourced only by the telegram wrapper), so an env check would false-negative.
    """
    try:
        return subprocess.run(
            ["pgrep", "-f", "chela telegram"], capture_output=True, timeout=3
        ).returncode == 0
    except Exception:
        return False


def _settings_status() -> dict:
    """READ-ONLY aggregation for the Settings drawer's "Connections & Status"
    surface. Every probe is best-effort and independently guarded: one failing
    source (e.g. tmux not running) degrades that single row to "Unknown" rather
    than blanking the whole panel. Nothing here mutates state.

    Each item is ``{label, on, state, detail}`` — ``on`` drives a colorblind-safe
    ●/○ SHAPE badge in the drawer (never colour alone) and ``state`` is its text
    label (e.g. "Connected" / "Off"). Grouped into sections the drawer renders
    in order."""
    session = config.current_session()

    # Connections — live/external things the dashboard depends on.
    try:
        windows = discovery.get_windows_by_id()
        session_on = True
        session_state = "Connected"
        n = len(windows)
        session_detail = f"{session} · {n} window{'' if n == 1 else 's'}"
    except Exception:
        session_on, session_state, session_detail = False, "Unknown", session

    collab_on = bool(config.COLLAB_RELAY) and config.COLLAB_PRESENCE
    if not config.COLLAB_PRESENCE:
        collab_state, collab_detail = "Off", "presence disabled (CHELA_COLLAB=false)"
    elif config.COLLAB_RELAY:
        collab_state, collab_detail = "Configured", config.COLLAB_RELAY
    else:
        collab_state, collab_detail = "Off", "set CHELA_COLLAB_RELAY to enable"

    notify_on = notify.enabled()
    if notify_on:
        notify_state = notify._detect_kind(config.NOTIFY_URL)
        notify_detail = _notify_host(config.NOTIFY_URL)
    else:
        notify_state, notify_detail = "Off", "set CHELA_NOTIFY_URL to enable"

    # Telegram bridge — remote control of the fleet over Telegram forum topics.
    # Detected by the RUNNING daemon (pgrep), NOT env vars: the dashboard process
    # does not carry the bridge's credentials (they live in ~/.chela/telegram.env,
    # sourced only by the telegram wrapper), so an env check would read "Off" while
    # it runs. The token/chat_id are secrets and never ride along in the detail.
    tg_on = _telegram_bridge_running()
    if tg_on:
        tg_state = "Connected"
        try:
            from chela.telegram.bindings import BindingRegistry
            n_bind = len(BindingRegistry.load())
            tg_detail = f"{n_bind} agent{'' if n_bind == 1 else 's'} bound" if n_bind else "no agents bound yet"
        except Exception:
            tg_detail = "running"
    else:
        tg_state, tg_detail = "Off", "start `chela telegram` to enable"

    # The daemon (`chela run`) — the process that ticks the scheduler, the dispatcher and
    # reconciliation. Read from what it publishes at startup ($CHELA_DIR/daemon.json,
    # pid-checked), never from this process's config: the dashboard is a DIFFERENT process
    # and its env is not evidence about the daemon's. Nothing published = nothing running,
    # and that is worth a row of its own — the fleet looks identical either way.
    daemon_live = capabilities.live()
    if daemon_live:
        n_on = sum(1 for c in daemon_live["capabilities"] if c.get("on"))
        n_all = len(daemon_live["capabilities"])
        daemon_state = "Running"
        daemon_detail = f"pid {daemon_live.get('pid')} · {n_on}/{n_all} capabilities on"
    else:
        daemon_state, daemon_detail = "Off", "not running — start it with `chela run`"

    connections = [
        {"label": "tmux session", "on": session_on, "state": session_state, "detail": session_detail},
        {"label": "Daemon", "on": bool(daemon_live), "state": daemon_state, "detail": daemon_detail},
        {"label": "Telegram bridge", "on": tg_on, "state": tg_state, "detail": tg_detail},
        {"label": "Collaboration relay", "on": collab_on, "state": collab_state, "detail": collab_detail},
        {"label": "Needs-input notifications", "on": notify_on, "state": notify_state, "detail": notify_detail},
    ]

    # Features — feature toggles / scheduled work inside the daemon.
    wall_detail = "loopback-served" if config.TERMINALS_ENABLED else "CHELA_TERMINALS_ENABLED=false"
    if config.TERMINALS_ENABLED and config.TERMINALS_EXPOSE:
        wall_detail = "exposed on non-loopback binds (CHELA_TERMINALS_EXPOSE)"

    # Match /api/dispatcher: count AUTO-DISCOVERED workflows (explicit config +
    # repo-root WORKFLOW.md + any workflow that has runs) — not the raw
    # CHELA_DISPATCH_WORKFLOWS env, which CMX-3's auto-discovery made obsolete
    # (env-only would read "Off" while the Kanban shows live runs).
    # A workflow that no longer parses is the one dispatcher fault an operator
    # MUST see: the daemon keeps reconciling on its last known-good config, but
    # it will not start new work until the file is fixed, and a log line nobody
    # reads is not a notification. The check is stat-gated (see
    # workflow.load_workflow_cached) — polling it from the drawer is cheap, and
    # the error is a property of the file on disk, so this process observes the
    # same fault the daemon does without sharing memory with it.
    wf_errors: list[dict] = []
    try:
        wf_paths = _discover_dispatch_workflows(dispatcher.list_runs())
        n_wf = len(wf_paths)
        for p in wf_paths:
            err = workflow_error(p)
            if err:
                wf_errors.append({"workflow": p.name, "path": str(p), "error": err})
    except Exception:
        n_wf = len(config.DISPATCH_WORKFLOWS)
    dispatch_on = n_wf > 0 and not wf_errors
    dispatch_state = f"{n_wf} workflow{'' if n_wf == 1 else 's'}" if n_wf else "Off"
    dispatch_detail = (f"every {config.DISPATCH_TICK_INTERVAL}s · auto-discovered" if n_wf
                       else "no workflows yet — run `chela dispatch`")
    if wf_errors:
        dispatch_state = "Blocked"
        dispatch_detail = ("new dispatches paused, still reconciling on the last good config — "
                           + "; ".join(f"{e['workflow']}: {e['error']}" for e in wf_errors))

    # ...and the fault that beats every one of the above: the daemon that would DO the
    # dispatching has it turned off. Auto-discovery finds a WORKFLOW.md on disk and runs
    # show in the Kanban, so this row read "1 workflow · On" for nine hours while the
    # daemon dispatched nothing and reconciled nothing. What the file system says is not
    # what the daemon does — so ask the daemon (it publishes its effective capabilities),
    # and let that answer win. `None` = no daemon has published: don't guess, don't lie.
    daemon_dispatch = next(
        (c for c in (daemon_live["capabilities"] if daemon_live else [])
         if c.get("key") == "dispatch"),
        None,
    )
    if daemon_dispatch is not None and not daemon_dispatch.get("on"):
        dispatch_on = False
        dispatch_state = "Off"
        dispatch_detail = ("the RUNNING daemon has dispatch AND reconcile off — "
                           "CHELA_DISPATCH_WORKFLOWS is empty in its environment")
    elif daemon_live is None and n_wf:
        dispatch_detail += " · no daemon running (`chela run`)"

    try:
        n_tasks = len(scheduler.list_tasks())
        sched_detail = f"every {config.SCHEDULER_POLL_INTERVAL}s · {n_tasks} task{'' if n_tasks == 1 else 's'}"
    except Exception:
        sched_detail = f"every {config.SCHEDULER_POLL_INTERVAL}s"

    features = [
        {"label": "Terminal wall", "on": config.TERMINALS_ENABLED,
         "state": "Enabled" if config.TERMINALS_ENABLED else "Off", "detail": wall_detail},
        {"label": "Work dispatcher", "on": dispatch_on, "state": dispatch_state, "detail": dispatch_detail},
        {"label": "Scheduler", "on": True, "state": "Polling", "detail": sched_detail},
        {"label": "Tool-call relay", "on": config.SHOW_TOOL_CALLS,
         "state": "On" if config.SHOW_TOOL_CALLS else "Hidden",
         "detail": "every tool_use/tool_result relayed" if config.SHOW_TOOL_CALLS
                   else "text + interactive prompts only (CHELA_SHOW_TOOL_CALLS)"},
    ]

    return {
        "sections": [
            {"title": "Connections", "items": connections},
            {"title": "Features", "items": features},
        ],
        # Machine-readable twin of the "Work dispatcher" row above, for anything
        # that wants to act on a broken workflow rather than render a sentence.
        "workflow_errors": wf_errors,
    }


@app.route("/api/settings")
@require_auth
def api_settings():
    """READ-ONLY status aggregation for the Settings drawer. Reports live
    connection + feature status (see ``_settings_status``); mutates nothing.

    Also the operator-visible surface for a WORKFLOW.md that no longer parses:
    the "Work dispatcher" row reads *Blocked*, and ``workflow_errors`` carries
    the parse error per workflow."""
    return jsonify(_settings_status())


@app.route("/api/launcher/pin", methods=["POST"])
@require_auth
def api_launcher_pin():
    """Pin a directory to Favorites. Returns the refreshed launcher view."""
    data = request.get_json(silent=True) or {}
    path = (data.get("path") or "").strip()
    if not path:
        return jsonify({"ok": False, "error": "path required"}), 400
    resolved = os.path.realpath(os.path.expanduser(path))
    if not os.path.isdir(resolved):
        return jsonify({"ok": False, "error": f"no such directory: {path}"}), 400
    label = (data.get("label") or "").strip() or None
    return jsonify({"ok": True, **launcher.pin(resolved, label)})


@app.route("/api/launcher/unpin", methods=["POST"])
@require_auth
def api_launcher_unpin():
    """Remove a directory from Favorites. Returns the refreshed launcher view."""
    data = request.get_json(silent=True) or {}
    path = (data.get("path") or "").strip()
    if not path:
        return jsonify({"ok": False, "error": "path required"}), 400
    resolved = os.path.realpath(os.path.expanduser(path))
    return jsonify({"ok": True, **launcher.unpin(resolved)})


@app.route("/api/launcher/forget", methods=["POST"])
@require_auth
def api_launcher_forget():
    """Drop a directory from Recent (the × on a Recent row). Returns the view."""
    data = request.get_json(silent=True) or {}
    path = (data.get("path") or "").strip()
    if not path:
        return jsonify({"ok": False, "error": "path required"}), 400
    resolved = os.path.realpath(os.path.expanduser(path))
    return jsonify({"ok": True, **launcher.forget_recent(resolved)})


@app.route("/api/agents/kill", methods=["POST"])
@require_auth
def api_agents_kill():
    """Kill an agent's tmux window (× button on the terminal wall).

    We target the resolved window_id (`@N`) rather than the name so a stale
    display-name collision can't kill the wrong window.
    """
    _require_terminals()
    data = request.get_json(force=True)
    agent = data.get("agent", "")
    if not agent:
        return jsonify({"ok": False, "error": "agent required"}), 400
    # The wall keys panes by stable wid (@N); accept that or a display name.
    windows = discovery.get_all_windows()
    if agent.startswith("@"):
        wid = agent
    else:
        wid = windows.get(agent)
    if not wid:
        return jsonify({"ok": False, "error": f"agent {agent} not found"}), 404
    try:
        # _kill_window builds `<session>:<arg>`; passing the @N window id targets
        # the exact window (kill-window -t <session>:@N is valid tmux).
        dispatcher._kill_window(wid)
    except Exception as e:  # noqa: BLE001 — surface any tmux/exec failure to the caller
        return jsonify({"ok": False, "error": str(e)}), 500
    log.info("killed non-managed window %s (%s)", agent, wid)
    return jsonify({"ok": True, "name": agent})


# ---------------------------------------------------------------------------
# Hooks — Claude Code hooks -> the event log, and (for a question) back again.
# ---------------------------------------------------------------------------

@app.route("/hooks/<event>", methods=["POST"])
@require_auth
def api_hooks(event):
    """Receive one Claude Code hook: append it to the event log, and — for exactly one
    event — hand the agent an answer back.

    The plugin (``plugin/hooks/hooks.json``, rendered by :func:`chela.hooks.hooks_spec`)
    POSTs each event here as an ``http`` hook, so there is no shell script and no process
    spawn per tool call. Correlation to a window is off the session's origin, not the pane.

    **Every event but one returns ``{}``.** An agent is *blocked* on this request and
    Claude Code reads what comes back, so a ``permissionDecision`` or a
    ``hookSpecificOutput`` here is chela answering a prompt on the human's behalf. That is
    now a thing chela deliberately does — for ``PermissionRequest`` on an
    ``AskUserQuestion``, and only when a human has actually tapped an answer on the bound
    Telegram topic within the wait budget (:func:`chela.gateanswer.answer_permission_request`).
    It is the ONLY safe way to answer a multi-question / ``multiSelect`` picker: the
    keystroke path has no cursor to read for those shapes, and the one time it guessed it
    silently answered the wrong option (CMX-32). Nothing else here decides anything.

    **A blocked request is bounded and fails OPEN.** The wait is at most
    ``CHELA_GATE_WAIT_S`` and the number of simultaneously-held gates is capped; past
    either, the response is ``{}`` — which is not a deny, it is *no answer*: the picker is
    still on the pane, still answerable in tmux, and the run is no worse off than it was
    before this feature existed.

    It never fails the caller. A malformed payload, an unparseable body, a full disk: 200
    and an empty object, every time. The agent's tool call is not ours to break, and the
    daemon being down at all is already a fail-OPEN path (the connection is refused, the
    agent proceeds, the event is lost).
    """
    if event not in hooks.HOOK_EVENTS:
        abort(404)
    if (request.content_length or 0) > hooks.MAX_BODY:
        log.warning("hooks: %s body over %d bytes — not read", event, hooks.MAX_BODY)
        return jsonify({})
    body = request.get_json(force=True, silent=True)
    hooks.ingest(event, body)
    if event == "PostToolUse" and isinstance(body, dict):
        # The gate is over — whoever answered it. A ⏎ driven into the mirrored pane answers
        # the TUI directly, so a hook we are holding for that same call would otherwise wait
        # out its whole budget for an answer that is never coming, holding a wait slot the
        # next gate needs (CMX-54). This is the one signal that fires on BOTH answer routes.
        gateanswer.gate_resolved(body.get("tool_use_id"))
    if event == "PermissionRequest" and isinstance(body, dict):
        try:
            answer = gateanswer.answer_permission_request(body)
        except Exception:  # noqa: BLE001 — a bug in OUR code must not wedge a live agent
            log.exception("gateanswer: answering %s failed — failing open", event)
            answer = None
        if answer is not None:
            return jsonify(answer)
    return jsonify({})


# ---------------------------------------------------------------------------
# API: Context Usage
# ---------------------------------------------------------------------------

def _fmt_k(v):
    """Format a thousands-of-tokens value as a compact counter: 147.5 -> "147.5K",
    1000 -> "1M". Returns None for falsy input so the UI can skip it."""
    if not v:
        return None
    if v >= 1000:
        return f"{v / 1000:g}M"
    return f"{v:g}K"


@app.route("/api/agents/context")
@require_auth
def api_agents_context():
    # Live, per discovered agent: a fresh statusLine cache file (authoritative —
    # context %, 5h/7d rate limits, cost) when present, else a transcript-derived
    # context estimate. No dependency on the snapshot DB being populated.
    agent_name = request.args.get("agent")
    windows = discovery.get_all_windows()  # {name: window_id}
    names = [agent_name] if agent_name else list(windows.keys())

    results = []
    for name in names:
        s = context.live_snapshot(name)
        if not s:
            continue
        results.append({
            "name": s["name"],
            "window_id": windows.get(s["name"]),
            "used": _fmt_k(s.get("used_k")),
            "total": _fmt_k(s.get("total_k")),
            "used_pct": s.get("used_pct"),
            "messages_tokens": _fmt_k(s.get("messages_k")),
            "messages_pct": s.get("messages_pct"),
            "free": _fmt_k(s.get("free_k")),
            "free_pct": s.get("free_pct"),
            "model": s.get("model"),
            "cost_usd": round(s["cost_usd"], 2) if s.get("cost_usd") else None,
            "rate_limit_pct": s.get("rate_limit_pct"),
            "rate_limit_resets_at": s.get("rate_limit_resets_at"),
            "weekly_rl_pct": s.get("weekly_rl_pct"),
            "weekly_rl_resets_at": s.get("weekly_rl_resets_at"),
            "session_name": s.get("session_name"),
            "branch": s.get("branch"),
            "source": s.get("source"),
            "estimated": s.get("estimated", False),
            "ts": s.get("ts"),
        })
    return jsonify(results)


# ---------------------------------------------------------------------------
# API: Schedules
# ---------------------------------------------------------------------------

@app.route("/api/schedules")
@require_auth
def api_schedules():
    tasks = scheduler.list_tasks()
    return jsonify([
        {
            "id": t.id,
            "agent_name": t.agent_name,
            "schedule_type": t.schedule_type,
            "schedule_value": t.schedule_value,
            "prompt": t.prompt,
            "enabled": t.enabled,
            "last_run": t.last_run,
            "next_run": t.next_run,
        }
        for t in tasks
    ])


@app.route("/api/schedules", methods=["POST"])
@require_auth
def api_schedules_add():
    data = request.get_json(force=True)
    try:
        task_id = scheduler.add_task(
            data["agent_name"],
            data["schedule_type"],
            data["schedule_value"],
            data["prompt"],
        )
        return jsonify({"id": task_id})
    except (KeyError, ValueError) as e:
        return jsonify({"error": str(e)}), 400


@app.route("/api/schedules/<int:task_id>", methods=["DELETE"])
@require_auth
def api_schedules_delete(task_id):
    ok = scheduler.remove_task(task_id)
    return jsonify({"deleted": ok})


@app.route("/api/schedules/<int:task_id>", methods=["PATCH"])
@require_auth
def api_schedules_toggle(task_id):
    data = request.get_json(force=True)
    try:
        scheduler.set_enabled(task_id, bool(data.get("enabled", True)))
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ---------------------------------------------------------------------------
# API: Knowledge (OKF viewer — read-only)
#
# Serves the embedded "Knowledge" view from an on-disk OKF bundle. The bundle is
# PRIVATE fleet data (docs/OKF.md → Security): these routes inherit the
# dashboard's loopback bind (tailnet-only remote) and add no new listener. The
# producer (okf.export_bundle) and consumer (okf.read_*) live in chela.okf; this
# layer is a thin jsonify wrapper plus an export-on-demand cache.
# ---------------------------------------------------------------------------

def _knowledge_dir() -> Path:
    """The bundle the embedded viewer reads — the default export dir
    (~/.chela/knowledge), shared with `chela knowledge export` (no --out)."""
    return okf.DEFAULT_OUT


def _ensure_bundle(force: bool = False) -> Path:
    """Export the bundle if it's missing (or ``force``), then return its dir.

    First view auto-exports so the page isn't empty; thereafter it's served from
    disk and the UI's Refresh button forces a re-export. Export reads chela's own
    state only (never home-root private memory)."""
    root = _knowledge_dir()
    if force or not (root / "index.md").exists():
        okf.export_bundle(root)
    return root


@app.route("/api/knowledge/tree")
@require_auth
def api_knowledge_tree():
    """Browse + glance payload: concepts by directory, counts by type, log."""
    return jsonify(okf.read_tree(_ensure_bundle()))


@app.route("/api/knowledge/concept")
@require_auth
def api_knowledge_concept():
    """One concept: frontmatter + raw body + outbound links + computed backlinks."""
    rel = request.args.get("path", "")
    try:
        return jsonify(okf.read_concept(_knowledge_dir(), rel))
    except ValueError:
        abort(400)
    except (FileNotFoundError, OSError):
        abort(404)


@app.route("/api/knowledge/search")
@require_auth
def api_knowledge_search():
    """Substring search over frontmatter + body, filterable by type / tag."""
    return jsonify(okf.read_search(
        _knowledge_dir(),
        request.args.get("q", ""),
        request.args.get("type", ""),
        request.args.get("tag", ""),
    ))


@app.route("/api/knowledge/graph")
@require_auth
def api_knowledge_graph():
    """Concepts as nodes, markdown links as edges."""
    return jsonify(okf.read_graph(_ensure_bundle()))


@app.route("/api/knowledge/export", methods=["POST"])
@require_auth
def api_knowledge_export():
    """Force a re-export (the Knowledge view's Refresh), returning fresh tree."""
    return jsonify(okf.read_tree(_ensure_bundle(force=True)))


# ---------------------------------------------------------------------------
# API: System cron (read-only)
# ---------------------------------------------------------------------------

def _cron_project(command: str) -> str | None:
    """Best-effort friendly label for a cron line — the project it runs in."""
    m = re.search(r"/projects/([^/\s]+)", command)
    if m:
        return m.group(1)
    m = re.search(r"\bcd\s+(\S+)", command)
    if m:
        return m.group(1).rstrip("/").split("/")[-1]
    m = re.search(r"(\S+)\.py", command)
    if m:
        parts = m.group(1).rsplit("/", 2)
        if len(parts) >= 2:
            return parts[-2]
    return None


@app.route("/api/cron")
@require_auth
def api_cron():
    """Read-only view of the user's system crontab, parsed with next-run times.

    The dashboard never edits cron — this is a visibility companion to the
    chela scheduler. Honors CRON_TZ lines: entries below one are evaluated in
    that timezone, earlier entries in system-local time.
    """
    try:
        from zoneinfo import ZoneInfo
    except ImportError:
        ZoneInfo = None
    try:
        from croniter import croniter
    except ImportError:
        croniter = None

    try:
        proc = subprocess.run(["crontab", "-l"], capture_output=True, text=True, timeout=5)
    except Exception:
        return jsonify({"ok": False, "jobs": [], "error": "crontab unavailable"})
    if proc.returncode != 0:
        return jsonify({"ok": True, "jobs": []})  # "no crontab for <user>" → empty, not an error

    jobs = []
    section_tz = None  # None → system local
    for raw in proc.stdout.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        env = re.match(r"^([A-Za-z_][A-Za-z0-9_]*)=(.*)$", line)
        if env:
            if env.group(1) == "CRON_TZ":
                section_tz = env.group(2).strip()
            continue
        if line.startswith("@"):
            head, _, command = line.partition(" ")
            expr, command = head, command.strip()
        else:
            parts = line.split(None, 5)
            if len(parts) < 6:
                continue
            expr, command = " ".join(parts[:5]), parts[5]
        next_run = None
        if croniter is not None and croniter.is_valid(expr):
            try:
                base = (datetime.now(ZoneInfo(section_tz)) if section_tz and ZoneInfo
                        else datetime.now().astimezone())
                next_run = croniter(expr, base).get_next(datetime).astimezone(timezone.utc).isoformat()
            except Exception:
                next_run = None
        jobs.append({
            "schedule": expr,
            "command": command,
            "project": _cron_project(command),
            "next_run": next_run,
            "tz": section_tz or "local",
        })
    return jsonify({"ok": True, "jobs": jobs})


# ---------------------------------------------------------------------------
# API: Dispatcher
# ---------------------------------------------------------------------------

def _runs_for_workflow(
    all_runs: list[dict], wf_path: str
) -> tuple[list[dict], list[dict], list[dict]]:
    """Split runs into (active, awaiting_review, recent_completed) for a workflow.

    Match is by resolved-path equality so different env spellings still align
    with the path stored by dispatcher.tick() (which writes str(wf.path) where
    wf.path is already resolved). Returns at most 10 awaiting / completed.
    """
    try:
        target = str(Path(wf_path).expanduser().resolve())
    except OSError:
        target = wf_path
    matching = [r for r in all_runs if r.get("workflow_path") == target]
    active = [r for r in matching if r.get("status") in ("claimed", "running")]
    awaiting = [r for r in matching if r.get("status") == "awaiting_review"][:10]
    recent = [r for r in matching if r.get("status") in ("done", "failed")][:10]
    return active, awaiting, recent


def _repo_root_workflow() -> Path | None:
    """Auto-discover a ``WORKFLOW.md`` at the repo root, if present.

    The dashboard package lives at ``<root>/chela/dashboard/app.py``, so the
    repo root is two parents up. Dogfooding chelamux seeds a ``WORKFLOW.md``
    there, so this surfaces the project's own dispatcher with zero env config.
    Returns ``None`` when the file is absent (e.g. an installed wheel with no
    repo checkout, or a repo that never seeded a workflow).
    """
    try:
        candidate = Path(__file__).resolve().parents[2] / "WORKFLOW.md"
    except IndexError:
        return None
    return candidate if candidate.is_file() else None


def _discover_dispatch_workflows(all_runs: list[dict]) -> list[Path]:
    """Ordered, de-duplicated workflow paths to surface in the Dispatcher view.

    Union of three session-independent sources, config first:
      1. explicit ``CHELA_DISPATCH_WORKFLOWS`` config (kept, never replaced);
      2. an auto-discovered repo-root ``WORKFLOW.md``;
      3. every distinct ``workflow_path`` recorded in the runs DB — so dogfood
         dispatch runs appear regardless of which tmux session ran them (runs
         are session-independent; the daemon on this host need never have been
         pointed at that workflow).

    De-dup is by resolved-path string, matching how ``dispatcher.tick()`` stores
    ``workflow_path`` and how ``_runs_for_workflow()`` matches it.
    """
    ordered: list[Path] = []
    seen: set[str] = set()

    def add(p: Path) -> None:
        try:
            resolved = p.expanduser().resolve()
        except OSError:
            resolved = p
        key = str(resolved)
        if key not in seen:
            seen.add(key)
            ordered.append(resolved)

    for wf in DISPATCH_WORKFLOWS:
        add(wf)
    repo_wf = _repo_root_workflow()
    if repo_wf is not None:
        add(repo_wf)
    for run in all_runs:
        wf_path = run.get("workflow_path")
        if wf_path:
            add(Path(wf_path))
    return ordered


def _project_key_from_runs(*run_groups: list[dict]) -> str | None:
    """Best-effort ``project_key`` derived from a run's branch name.

    Dispatched branches are ``{project_key.lower()}-{task_number}`` (see
    ``dispatcher._spawn``), so the prefix recovers the key when the workflow
    file itself is unavailable (a run-discovered workflow whose file no longer
    exists in this checkout). Pre-migration ``dogfood/<sha>`` branches don't
    match and yield ``None``, which the frontend already handles.
    """
    for group in run_groups:
        for r in group:
            branch = (r.get("branch_name") or "").strip()
            if "-" not in branch:
                continue
            prefix = branch.rsplit("-", 1)[0].upper()
            if PROJECT_KEY_RE.match(prefix):
                return prefix
    return None


@app.route("/api/dispatcher/init", methods=["POST"])
@require_auth
def api_dispatcher_init():
    """Seed a starter WORKFLOW.md + TODO.md into a repo (never overwriting)."""
    data = request.get_json(silent=True) or {}
    path = (data.get("path") or "").strip()
    if not path:
        return jsonify({"ok": False, "error": "no path given"}), 400
    try:
        result = starter.seed_repo(path)
    except ValueError as e:
        return jsonify({"ok": False, "error": str(e)}), 400
    except OSError as e:
        return jsonify({"ok": False, "error": f"write failed: {e}"}), 400
    return jsonify(result)


@app.route("/api/dispatcher")
@require_auth
def api_dispatcher():
    """Per-workflow view: open tasks + active runs + recent completed runs.

    Workflows come from three unioned, session-independent sources (see
    ``_discover_dispatch_workflows``): the explicit ``CHELA_DISPATCH_WORKFLOWS``
    config, an auto-discovered repo-root ``WORKFLOW.md``, and every workflow
    that has runs recorded in the runs DB. That last source is why dogfood
    dispatch runs surface here even when this dashboard's daemon was never
    pointed at the workflow — runs are keyed by file path, not tmux session.
    """
    workflows_payload = []
    all_runs = dispatcher.list_runs()

    for wf_path in _discover_dispatch_workflows(all_runs):
        exists = wf_path.exists()
        entry: dict = {
            "path": str(wf_path),
            "exists": exists,
            "project_key": None,
            "open_tasks": [],
            "backlog_items": [],
            "active_runs": [],
            "awaiting_review_runs": [],
            "recent_runs": [],
            "error": None,
        }

        active, awaiting, recent = _runs_for_workflow(all_runs, str(wf_path))
        # Hide tasks from Open if they already have an in-flight run, so a
        # single TODO line never shows two cards. The strike on master only
        # lands when the PR merges, so without this filter an awaiting_review
        # task also appears as Open. Failed runs are excluded too; the
        # attempt cap blocks re-dispatch and editing the line mints a new id.
        in_flight_ids = (
            {r.get("task_id") for r in active}
            | {r.get("task_id") for r in awaiting}
            | {r.get("task_id") for r in recent if r.get("status") == "failed"}
        )
        project_key: str | None = None

        if exists:
            try:
                wf = load_workflow(wf_path)
                project_key = wf.project_key
                source = get_source(wf)
                open_tasks = source.list_open_tasks()
                entry["open_tasks"] = [
                    {
                        "id": t.id,
                        "title": t.title,
                        "file": t.file,
                        "line_number": t.line_number,
                    }
                    for t in open_tasks
                    if t.id not in in_flight_ids
                ]
                backlog_path = (wf.path.parent / "BACKLOG.md").resolve()
                entry["backlog_items"] = [
                    {"section": item.section, "text": item.text, "file": str(backlog_path)}
                    for item in parse_backlog(backlog_path)
                ]
            except Exception as e:
                entry["error"] = f"{type(e).__name__}: {e}"
        else:
            # Run-discovered (or configured-but-missing) workflow whose file is
            # absent in this checkout. We still surface its runs; only the
            # open-task / backlog columns are unavailable.
            entry["error"] = "workflow file not found"

        # When the file couldn't supply a project_key, recover it from a run
        # branch so run-discovered workflows still group + label correctly.
        if project_key is None:
            project_key = _project_key_from_runs(active, awaiting, recent)
        entry["project_key"] = project_key

        # Stamp project_key onto each run dict — task_number already comes from
        # the row (column added via idempotent migration); pre-migration rows
        # carry task_number=None, which the frontend uses as the signal to fall
        # back to the legacy `dogfood/<sha>` branch_name display. pr_mergeable
        # already rides along via list_runs()'s SELECT *; normalize it to None
        # for pre-migration rows so the frontend can rely on the key existing.
        for r in (*active, *awaiting, *recent):
            r["project_key"] = project_key
            r.setdefault("pr_mergeable", None)
        entry["active_runs"] = active
        entry["awaiting_review_runs"] = awaiting
        entry["recent_runs"] = recent
        workflows_payload.append(entry)

    return jsonify({
        # True whenever there's anything to show — explicit config, a discovered
        # repo-root workflow, or workflows with recorded runs. The frontend
        # gates its empty state on this, so run-only discovery must flip it on.
        "configured": bool(workflows_payload),
        "workflows": workflows_payload,
    })


def _resolve_dispatch_workflow(wf_path: str) -> Path | None:
    """Match a client-supplied workflow path against ``DISPATCH_WORKFLOWS``.

    Returns the configured ``Path`` on match, ``None`` otherwise. Comparison
    uses fully-resolved paths so different spellings (relative, ``~``, symlink)
    still align with what the daemon registered. Refusing unknown paths keeps
    the endpoint from mutating files in arbitrary repos.
    """
    try:
        target = Path(wf_path).expanduser().resolve()
    except OSError:
        return None
    for wf in DISPATCH_WORKFLOWS:
        if wf == target:
            return wf
    return None


def _insert_into_open_section(todo_text: str, bullet: str) -> str | None:
    """Insert ``bullet`` directly below ``## Open`` in ``TODO.md`` text.

    Matches the layout of recent queue commits (e.g. ``e62fb45``): a blank
    line under the header, then the new bullet, then a blank line, then the
    existing items. Returns ``None`` if no ``## Open`` header is found.
    """
    keep_trailing_nl = todo_text.endswith("\n")
    lines = todo_text.splitlines()
    open_idx: int | None = None
    for i, raw in enumerate(lines):
        if raw.strip() == "## Open":
            open_idx = i
            break
    if open_idx is None:
        return None
    insert_at = open_idx + 1
    if insert_at < len(lines) and lines[insert_at].strip() == "":
        insert_at += 1
    lines[insert_at:insert_at] = [bullet, ""]
    out = "\n".join(lines)
    if keep_trailing_nl:
        out += "\n"
    return out


def _remove_backlog_bullet(backlog_text: str, text: str) -> tuple[str | None, int]:
    """Drop the bullet whose extracted text exactly matches ``text``.

    Returns ``(new_text, match_count)``. ``match_count`` is the number of
    bullet lines that extracted to ``text`` — callers refuse 0 (not found) and
    >1 (ambiguous) without modifying any file.
    """
    keep_trailing_nl = backlog_text.endswith("\n")
    lines = backlog_text.splitlines()
    matches: list[int] = []
    for i, raw in enumerate(lines):
        m = _BULLET_RE.match(raw)
        if not m:
            continue
        if m.group(1).strip() == text:
            matches.append(i)
    if len(matches) != 1:
        return None, len(matches)
    del lines[matches[0]]
    out = "\n".join(lines)
    if keep_trailing_nl:
        out += "\n"
    return out, 1


@app.route("/api/dispatcher/backlog/promote", methods=["POST"])
@require_auth
def api_dispatcher_backlog_promote():
    """Move a backlog bullet into TODO.md's Open section + push to master.

    The dispatcher only picks up TODO lines from master, so a local-only
    commit isn't enough — the push is part of the contract. All failure
    modes (BACKLOG missing, bullet not found / ambiguous, push failure)
    return a JSON error and leave the repo in its pre-call state: we
    capture the pre-call HEAD up front and ``git reset --hard`` to it on
    any post-mutation failure so the call is either fully applied or
    fully rolled back.
    """
    data = request.get_json(force=True) or {}
    wf_path = data.get("workflow_path", "")
    text = (data.get("text", "") or "").strip()
    if not wf_path or not text:
        return jsonify({"ok": False, "error": "workflow_path and text required"}), 400

    wf_resolved = _resolve_dispatch_workflow(wf_path)
    if wf_resolved is None:
        return jsonify({"ok": False, "error": f"unknown workflow: {wf_path}"}), 400

    try:
        wf = load_workflow(wf_resolved)
    except Exception as e:
        return jsonify({"ok": False, "error": f"failed to load workflow: {e}"}), 500
    repo_dir = wf.path.parent
    backlog_path = repo_dir / "BACKLOG.md"
    source = get_source(wf)
    todo_path = source.path

    if not backlog_path.exists():
        return jsonify({"ok": False, "error": f"BACKLOG.md not found at {backlog_path}"}), 404
    if not todo_path.exists():
        return jsonify({"ok": False, "error": f"TODO.md not found at {todo_path}"}), 404

    backlog_text = backlog_path.read_text()
    new_backlog, n = _remove_backlog_bullet(backlog_text, text)
    if n == 0:
        return jsonify({"ok": False, "error": "bullet not found in BACKLOG.md"}), 404
    if n > 1:
        return jsonify({"ok": False, "error": f"bullet text matches {n} lines in BACKLOG.md (ambiguous)"}), 409

    todo_text = todo_path.read_text()
    new_todo = _insert_into_open_section(todo_text, f"- [ ] {text}")
    if new_todo is None:
        return jsonify({"ok": False, "error": "## Open section not found in TODO.md"}), 500

    try:
        head = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=str(repo_dir), capture_output=True, text=True, timeout=10,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as e:
        return jsonify({"ok": False, "error": f"git rev-parse failed: {e}"}), 500
    if head.returncode != 0:
        return jsonify({"ok": False, "error": (head.stderr or "git rev-parse HEAD failed").strip()}), 500
    original_sha = head.stdout.strip()

    def _rollback():
        subprocess.run(
            ["git", "reset", "--hard", original_sha],
            cwd=str(repo_dir), capture_output=True, text=True, timeout=15,
        )

    backlog_path.write_text(new_backlog)
    todo_path.write_text(new_todo)

    truncated = text if len(text) <= 50 else text[:47].rstrip() + "..."
    commit_msg = f'backlog: promote "{truncated}" to TODO'

    try:
        add = subprocess.run(
            ["git", "add", backlog_path.name, todo_path.name],
            cwd=str(repo_dir), capture_output=True, text=True, timeout=15,
        )
        if add.returncode != 0:
            _rollback()
            return jsonify({"ok": False, "error": (add.stderr or "git add failed").strip()}), 500
        commit = subprocess.run(
            ["git", "commit", "-m", commit_msg],
            cwd=str(repo_dir), capture_output=True, text=True, timeout=20,
        )
        if commit.returncode != 0:
            _rollback()
            return jsonify({"ok": False, "error": (commit.stderr or commit.stdout or "git commit failed").strip()}), 500
        push = subprocess.run(
            ["git", "push"],
            cwd=str(repo_dir), capture_output=True, text=True, timeout=60,
        )
        if push.returncode != 0:
            _rollback()
            err = (push.stderr or push.stdout or "git push failed").strip()
            return jsonify({"ok": False, "error": err}), 502
    except (FileNotFoundError, subprocess.TimeoutExpired) as e:
        _rollback()
        return jsonify({"ok": False, "error": f"git operation failed: {e}"}), 500

    return jsonify({"ok": True, "commit_msg": commit_msg})
_PR_NUMBER_RE = re.compile(r"/pull/(\d+)(?:[/?#]|$)")


def _best_effort(task_id: str, label: str, argv: list[str], cwd: str, timeout: int) -> None:
    """Run a cleanup command best-effort: log non-zero/errors, never raise."""
    try:
        proc = subprocess.run(argv, cwd=cwd, capture_output=True, text=True, timeout=timeout)
    except (FileNotFoundError, subprocess.TimeoutExpired) as e:
        log.warning("merge cleanup %s failed for task %s: %s", label, task_id, e)
        return
    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout or f"{label} failed").strip()
        log.warning("merge cleanup %s failed for task %s: %s", label, task_id, err)


_DONE_RE = re.compile(r"^\s*-\s*\[[xX]\]\s*(.+?)\s*$")


def _pr_mergeable(pr_number: str, repo_dir: str) -> str | None:
    """Return GitHub's mergeable verdict (MERGEABLE / CONFLICTING / UNKNOWN) or None."""
    try:
        proc = subprocess.run(
            ["gh", "pr", "view", pr_number, "--json", "mergeable", "-q", ".mergeable"],
            cwd=repo_dir, capture_output=True, text=True, timeout=15,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0:
        return None
    return (proc.stdout or "").strip() or None


def _struck_titles(worktree_path: str, base_ref: str) -> list[str] | None:
    """Titles this branch struck: `- [ ] X` at the merge-base, `- [x] X` at HEAD.

    Returns the list of titles, or None if the merge-base / file lookups fail.
    """
    def _show(ref: str) -> str | None:
        try:
            proc = subprocess.run(
                ["git", "show", f"{ref}:TODO.md"],
                cwd=worktree_path, capture_output=True, text=True, timeout=15,
            )
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return None
        return proc.stdout if proc.returncode == 0 else None

    try:
        mb = subprocess.run(
            ["git", "merge-base", "HEAD", base_ref],
            cwd=worktree_path, capture_output=True, text=True, timeout=15,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    if mb.returncode != 0:
        return None
    merge_base = (mb.stdout or "").strip()
    if not merge_base:
        return None

    base_todo = _show(merge_base)
    head_todo = _show("HEAD")
    if base_todo is None or head_todo is None:
        return None

    base_open = {m.group(1).strip() for ln in base_todo.splitlines()
                 if (m := OPEN_RE.match(ln))}
    head_done = {m.group(1).strip() for ln in head_todo.splitlines()
                 if (m := _DONE_RE.match(ln))}
    return sorted(base_open & head_done)


def _restrike_master_todo(worktree_path: str, titles: list[str]) -> int:
    """In the worktree's TODO.md, flip `- [ ] X` -> `- [x] X` for each title in *titles*.

    Returns the number of lines actually flipped (so the caller can assert it
    matches the expected struck-line count before trusting the resolve).
    """
    todo = Path(worktree_path) / "TODO.md"
    content = todo.read_text()
    trailing_nl = content.endswith("\n")
    want = set(titles)
    out: list[str] = []
    flipped = 0
    for line in content.splitlines():
        m = OPEN_RE.match(line)
        if m and m.group(1).strip() in want:
            out.append(line.replace("[ ]", "[x]", 1))
            flipped += 1
        else:
            out.append(line)
    todo.write_text("\n".join(out) + ("\n" if trailing_nl else ""))
    return flipped


def _auto_resolve_todo_conflict(
    task_id: str, pr_number: str, worktree_path: str, branch_name: str, base_branch: str,
) -> dict:
    """Resolve a TODO.md-ONLY merge conflict in the run's worktree, then push.

    Strict guards — anything outside "only TODO.md, only the expected strike
    lines" aborts the merge and falls back to manual resolution:
      - the conflicted set must be exactly {TODO.md} (never auto-resolve code);
      - the branch must have struck >= 1 line, and master's TODO.md must still
        carry exactly those lines as `- [ ]` (flip count must match).

    Returns {"ok": True} on a clean resolve+push, else {"ok": False, "error": ...}.
    """
    base_ref = f"origin/{base_branch}"

    def _git(argv: list[str], timeout: int = 30) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["git", *argv], cwd=worktree_path,
            capture_output=True, text=True, timeout=timeout,
        )

    def _abort_manual(reason: str) -> dict:
        _best_effort(task_id, "merge-abort", ["git", "merge", "--abort"], worktree_path, 15)
        return {"ok": False, "error": reason}

    try:
        _git(["fetch", "origin"], timeout=60)
        # Let it conflict — we inspect the unmerged set rather than trusting exit code.
        _git(["merge", "--no-commit", "--no-ff", base_ref], timeout=60)
        conflicted = _git(["diff", "--name-only", "--diff-filter=U"], timeout=15)
        files = {f for f in conflicted.stdout.split("\n") if f.strip()}
        if files != {"TODO.md"}:
            return _abort_manual(
                f"merge conflict touches files other than TODO.md ({sorted(files)}); "
                "resolve this PR by hand"
            )

        titles = _struck_titles(worktree_path, base_ref)
        if not titles:
            return _abort_manual(
                "could not determine which TODO line this branch struck; resolve by hand"
            )

        # Resolve to master's TODO.md, then re-strike exactly the branch's line(s).
        co = _git(["checkout", base_ref, "--", "TODO.md"], timeout=15)
        if co.returncode != 0:
            return _abort_manual(
                (co.stderr or "git checkout of master TODO.md failed").strip()
            )
        flipped = _restrike_master_todo(worktree_path, titles)
        if flipped != len(titles):
            return _abort_manual(
                f"expected to re-strike {len(titles)} line(s) in master's TODO.md but "
                f"flipped {flipped} (master may have already struck or removed them); "
                "resolve by hand"
            )

        log.info(
            "auto-resolve TODO.md conflict task=%s pr=%s lines=%r",
            task_id, pr_number, titles,
        )

        _git(["add", "TODO.md"], timeout=15)
        commit = _git(["commit", "--no-edit"], timeout=20)
        if commit.returncode != 0:
            return _abort_manual(
                (commit.stderr or commit.stdout or "git commit of resolution failed").strip()
            )
        push = _git(["push", "origin", f"HEAD:{branch_name}"], timeout=60)
        if push.returncode != 0:
            # Already committed locally; nothing to abort. Surface the push error.
            return {"ok": False, "error": (push.stderr or push.stdout or "git push failed").strip()}
    except (FileNotFoundError, subprocess.TimeoutExpired) as e:
        return _abort_manual(f"git operation failed during auto-resolve: {e}")

    # Wait for GitHub to recompute mergeability after the push.
    for _ in range(5):
        if _pr_mergeable(pr_number, worktree_path) == "MERGEABLE":
            break
        time.sleep(2)

    return {"ok": True, "struck": titles}


def _merge_one(row: dict) -> dict:
    """Squash-merge one run's PR, then clean up the local worktree and branch.

    Shared by the single-card merge endpoint and the batch merge-all endpoint.
    Returns a result dict: on success ``{"ok": True, "merge_commit_sha": ...}``;
    on failure ``{"ok": False, "error": ..., "status": <http-code>}`` where
    ``status`` is the HTTP code the single-card endpoint should surface (the
    batch endpoint ignores it and just records the error). Never raises —
    gh/subprocess failures are captured into the error string.

    Squash — NOT rebase: rebase-merge silently drops the post-PR strike commit
    on master, after which the dispatcher would redispatch the already-merged
    task (PR #12 incident).

    Cleanup is done by us, not by `gh pr merge --delete-branch`: gh's
    `--delete-branch` exits non-zero when the local branch is checked out in a
    worktree (the dogfood case), surfacing a noisy "cannot delete branch ...
    used by worktree at ..." error even though the remote merge succeeded.
    Instead we run `gh pr merge --squash` cleanly, then best-effort remove the
    worktree, the local branch, and the remote branch ourselves.
    """
    task_id = row.get("task_id")
    pr_url = row.get("pr_url")
    if not pr_url:
        return {"ok": False, "error": "run has no pr_url", "status": 400}
    m = _PR_NUMBER_RE.search(str(pr_url))
    if not m:
        return {"ok": False, "error": f"could not parse PR number from {pr_url}", "status": 400}
    pr_number = m.group(1)
    wf_path = row.get("workflow_path") or ""
    repo_dir = Path(wf_path).parent if wf_path else None
    if not repo_dir or not repo_dir.is_dir():
        return {"ok": False, "error": f"workflow repo dir not found: {wf_path}", "status": 400}

    # Pre-merge: if GitHub reports CONFLICTING, attempt a strictly-guarded
    # auto-resolve of a TODO.md-ONLY bookkeeping conflict in the run's
    # worktree. Anything beyond TODO.md (or an ambiguous strike) aborts to
    # manual. The batch merge-all path pre-filters to MERGEABLE, so this only
    # fires for a single-card Merge on a conflicting PR.
    if _pr_mergeable(pr_number, str(repo_dir)) == "CONFLICTING":
        worktree_path_pre = row.get("worktree_path")
        branch_name_pre = row.get("branch_name")
        if not worktree_path_pre or not Path(worktree_path_pre).is_dir():
            return {"ok": False, "error": "PR is conflicting but the run's worktree is gone; resolve by hand", "status": 409}
        if not branch_name_pre:
            return {"ok": False, "error": "PR is conflicting but the run has no branch_name; resolve by hand", "status": 409}
        try:
            _wf = load_workflow(Path(wf_path))
            base_branch = _wf.get("workspace", "base_branch", default="master")
        except Exception:
            base_branch = "master"
        resolved = _auto_resolve_todo_conflict(task_id, pr_number, worktree_path_pre, branch_name_pre, base_branch)
        if not resolved.get("ok"):
            return {"ok": False, "error": resolved.get("error", "auto-resolve failed"), "status": 409}

    try:
        merge = subprocess.run(
            ["gh", "pr", "merge", pr_number, "--squash"],
            cwd=str(repo_dir), capture_output=True, text=True, timeout=60,
        )
    except FileNotFoundError:
        return {"ok": False, "error": "gh CLI not found on PATH", "status": 500}
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "gh pr merge timed out", "status": 504}
    if merge.returncode != 0:
        err = (merge.stderr or merge.stdout or "gh pr merge failed").strip()
        return {"ok": False, "error": err, "status": 502}

    merge_sha = None
    try:
        sha_proc = subprocess.run(
            ["gh", "pr", "view", pr_number, "--json", "mergeCommit", "-q", ".mergeCommit.oid"],
            cwd=str(repo_dir), capture_output=True, text=True, timeout=15,
        )
        if sha_proc.returncode == 0:
            merge_sha = sha_proc.stdout.strip() or None
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass

    repo_cwd = str(repo_dir)
    worktree_path = row.get("worktree_path")
    if worktree_path and Path(worktree_path).exists():
        _best_effort(
            task_id, "worktree-remove",
            ["git", "worktree", "remove", "--force", worktree_path],
            repo_cwd, 30,
        )
    # Read branch_name from the runs row so this works regardless of naming
    # scheme (dogfood/<id>, <project_key>-<N>, etc.).
    branch_name = row.get("branch_name")
    if branch_name:
        _best_effort(
            task_id, "branch-delete",
            ["git", "branch", "-D", branch_name],
            repo_cwd, 15,
        )
        _best_effort(
            task_id, "remote-branch-delete",
            ["git", "push", "origin", "--delete", branch_name],
            repo_cwd, 30,
        )

    return {"ok": True, "merge_commit_sha": merge_sha}


@app.route("/api/dispatcher/runs/<task_id>/merge", methods=["POST"])
@require_auth
def api_dispatcher_run_merge(task_id: str):
    """Squash-merge a single run's PR + clean up. See ``_merge_one`` for detail."""
    row = next((r for r in dispatcher.list_runs() if r.get("task_id") == task_id), None)
    if not row:
        return jsonify({"ok": False, "error": f"run {task_id} not found"}), 404
    result = _merge_one(row)
    status = result.pop("status", 200 if result.get("ok") else 502)
    return jsonify(result), status


@app.route("/api/dispatcher/merge-all", methods=["POST"])
@require_auth
def api_dispatcher_merge_all():
    """Batch squash-merge every awaiting_review run whose PR is MERGEABLE.

    Optional ``{workflow_path}`` filter restricts to one workflow — the Kanban
    passes the active filter unless it's "all". A run is eligible only when
    ``status == 'awaiting_review'`` AND ``pr_state in ('open', None)`` AND
    ``pr_mergeable == 'MERGEABLE'``; anything CONFLICTING / UNKNOWN / non-open
    lands under ``skipped`` and is never merged. Each eligible run goes through
    the shared ``_merge_one`` helper, so each merge gets the same cleanup as the
    single-card button.

    Returns ``{ok, merged: [task_id...], skipped: [{task_id, reason}],
    failed: [{task_id, error}]}``.
    """
    data = request.get_json(silent=True) or {}
    wf_filter = (data.get("workflow_path") or "").strip()
    target: str | None = None
    if wf_filter:
        resolved = _resolve_dispatch_workflow(wf_filter)
        if resolved is None:
            return jsonify({"ok": False, "error": f"unknown workflow: {wf_filter}"}), 400
        target = str(resolved)

    merged: list = []
    skipped: list = []
    failed: list = []
    for row in dispatcher.list_runs():
        if row.get("status") != "awaiting_review":
            continue
        if target is not None and row.get("workflow_path") != target:
            continue
        task_id = row.get("task_id")
        pr_state = row.get("pr_state")
        if pr_state not in ("open", None):
            skipped.append({"task_id": task_id, "reason": f"pr_state={pr_state}"})
            continue
        if row.get("pr_mergeable") != "MERGEABLE":
            skipped.append({"task_id": task_id, "reason": f"mergeable={row.get('pr_mergeable')}"})
            continue
        result = _merge_one(row)
        if result.get("ok"):
            merged.append(task_id)
        else:
            failed.append({"task_id": task_id, "error": result.get("error")})

    return jsonify({"ok": True, "merged": merged, "skipped": skipped, "failed": failed})


def _allowed_source_files() -> set[str]:
    """Resolve every TODO.md / BACKLOG.md path the configured workflows know about.

    The delete endpoint refuses to touch any file outside this set so the
    "source-line" kind can't be coerced into rewriting arbitrary paths.
    """
    allowed: set[str] = set()
    for wf_path in DISPATCH_WORKFLOWS:
        if not wf_path.exists():
            continue
        try:
            wf = load_workflow(wf_path)
            source = get_source(wf)
            allowed.add(str(source.path))
            allowed.add(str((wf.path.parent / "BACKLOG.md").resolve()))
        except Exception:
            continue
    return allowed


def _delete_source_line(file_path: Path, text: str) -> dict:
    """Remove the first bullet whose title equals ``text``. Idempotent."""
    if not file_path.exists():
        return {"ok": True, "deleted": False, "reason": "file missing"}
    content = file_path.read_text()
    trailing_nl = content.endswith("\n")
    lines = content.splitlines()
    new_lines: list[str] = []
    deleted = False
    for line in lines:
        if not deleted:
            m_open = OPEN_RE.match(line)
            m_bullet = _BULLET_RE.match(line)
            if (m_open and m_open.group(1).strip() == text) or \
               (m_bullet and m_bullet.group(1).strip() == text):
                deleted = True
                continue
        new_lines.append(line)
    if not deleted:
        return {"ok": True, "deleted": False, "reason": "no match"}
    new_content = "\n".join(new_lines) + ("\n" if trailing_nl else "")
    file_path.write_text(new_content)
    return {"ok": True, "deleted": True}


@app.route("/api/dispatcher/delete", methods=["POST"])
@require_auth
def api_dispatcher_delete():
    """Delete a Kanban card / Dispatcher row.

    Payload: ``{kind: "run", task_id}`` for runs-table rows;
    ``{kind: "source-line", file, text}`` for Backlog / Open cards backed by a
    markdown bullet. PRs are never touched — done/awaiting_review just drops
    the row; the user closes the PR on GitHub if they want.
    """
    data = request.get_json(force=True) or {}
    kind = data.get("kind")
    if kind == "run":
        task_id = data.get("task_id") or ""
        if not task_id:
            return jsonify({"ok": False, "error": "task_id required"}), 400
        try:
            return jsonify(dispatcher.delete_run(task_id))
        except Exception as e:
            log.exception("delete_run failed for %s", task_id)
            return jsonify({"ok": False, "error": f"{type(e).__name__}: {e}"}), 500
    if kind == "source-line":
        file_arg = data.get("file") or ""
        text = data.get("text") or ""
        if not file_arg or not text:
            return jsonify({"ok": False, "error": "file and text required"}), 400
        try:
            resolved = str(Path(file_arg).expanduser().resolve())
        except OSError:
            return jsonify({"ok": False, "error": "could not resolve file path"}), 400
        if resolved not in _allowed_source_files():
            return jsonify({"ok": False, "error": "file not in any configured workflow"}), 403
        try:
            return jsonify(_delete_source_line(Path(resolved), text))
        except Exception as e:
            log.exception("delete_source_line failed for %s", resolved)
            return jsonify({"ok": False, "error": f"{type(e).__name__}: {e}"}), 500
    return jsonify({"ok": False, "error": f"unknown kind {kind!r}"}), 400


# ---------------------------------------------------------------------------
# API: Summary (header bar)
# ---------------------------------------------------------------------------

@app.route("/api/summary")
@require_auth
def api_summary():
    windows = discovery.get_all_windows()
    tasks = scheduler.list_tasks()

    # Find soonest next_run per agent
    next_runs = {}
    for t in tasks:
        if t.enabled and t.next_run:
            if t.agent_name not in next_runs or t.next_run < next_runs[t.agent_name]:
                next_runs[t.agent_name] = t.next_run

    return jsonify({
        "agents_online": len(windows),
        "agents_total": len(windows),
        "windows_total": len(windows),
        "schedules_active": sum(1 for t in tasks if t.enabled),
        "schedules_total": len(tasks),
        "next_runs": next_runs,
    })


# ---------------------------------------------------------------------------
# API: Server-Sent Events (reactive UI accelerator)
# ---------------------------------------------------------------------------
#
# Pushes coarse "something changed" deltas so the dashboard reacts within ~1s
# instead of waiting on the per-tab polling timers. This is purely additive:
# every polling timer in app.js stays as a fallback, so if this stream never
# connects or drops, the UI behaves exactly as it did before SSE existed.
#
# The generator holds the previous snapshot in its own local scope, polls the
# same sources the REST endpoints use every ~1s, and yields a named frame only
# when a relevant field changed (window added/removed, run status/pr_state/
# pr_mergeable, or heartbeat status). Payloads stay tiny — the client uses the
# event as a trigger to re-run its existing render/refresh path (which refetches
# the full shape from /api/agents or /api/dispatcher), so no new DOM path is
# introduced. A ': keepalive' comment every ~15s stops idle proxies from
# dropping the connection.

SSE_POLL_INTERVAL = 1.0          # seconds between snapshot diffs
SSE_KEEPALIVE_INTERVAL = 15.0    # seconds between idle keepalive comments


def _sse_windows_snapshot() -> dict:
    try:
        return dict(discovery.get_all_windows())
    except Exception:
        log.exception("SSE: get_all_windows failed")
        return {}


def _sse_run_label(r: dict) -> str:
    """Human display id for a run — mirrors the frontend's ``_runDisplayId``:
    ``PROJECT_KEY-N`` when derivable, else the raw task id. Carried in the SSE
    frame so the run-state toast names the run the viewer recognizes."""
    key = _project_key_from_runs([r])
    task_number = r.get("task_number")
    if key and task_number is not None:
        return f"{key}-{task_number}"
    return r.get("task_id") or ""


def _sse_runs_snapshot() -> dict:
    """Per-run view diffed by the SSE loop. Values carry ``status`` + PR fields
    (change-detection) plus ``pr_url`` + ``label`` so the ``runs`` frame can name
    the run and link its PR without the client issuing a second fetch. Mirrors
    the same source (``dispatcher.list_runs``) the REST ``/api/dispatcher`` uses."""
    try:
        snap: dict = {}
        for r in dispatcher.list_runs():
            tid = r.get("task_id")
            if not tid:
                continue
            snap[tid] = {
                "status": r.get("status"),
                "pr_state": r.get("pr_state"),
                "pr_mergeable": r.get("pr_mergeable"),
                "pr_url": r.get("pr_url"),
                "label": _sse_run_label(r),
            }
        return snap
    except Exception:
        log.exception("SSE: list_runs failed")
        return {}


def _sse_terms_snapshot() -> set:
    """Set of agents with a live ttyd port — diffed to push a `term-ready` event
    so a pending pane swaps to its iframe without waiting for the next poll. The
    ~1.5s client poll stays the reliable default; this is a pure accelerator."""
    try:
        return {a for a, p in _terminals_port_map().items() if p}
    except Exception:
        log.exception("SSE: terms snapshot failed")
        return set()


def _sse_stream():
    """Generator yielding SSE frames on relevant state change. Never raises out;
    a disconnected client surfaces as GeneratorExit on the next yield, which
    cleanly tears the loop down."""
    # Prime the baseline from the current state without emitting — the client
    # has already done its initial full fetch on load, so we only push changes
    # from here on.
    prev_windows = _sse_windows_snapshot()
    prev_runs = _sse_runs_snapshot()
    prev_terms = _sse_terms_snapshot()

    # An initial 'hello' lets the client confirm the stream is live (it may
    # optionally lengthen its poll timers; default behavior leaves them as-is).
    # It also carries the current per-run status baseline so the client's
    # run-state toasts stay edge-triggered across reconnects: a run already in
    # awaiting_review is recorded here (no toast), so only a later transition
    # INTO a review state fires one.
    hello = {
        "runs": [{"task_id": tid, "status": v["status"]} for tid, v in prev_runs.items()]
    }
    yield f"event: hello\ndata: {json.dumps(hello)}\n\n"

    last_sent = time.monotonic()
    while True:
        time.sleep(SSE_POLL_INTERVAL)

        cur_windows = _sse_windows_snapshot()
        added = sorted(set(cur_windows) - set(prev_windows))
        removed = sorted(set(prev_windows) - set(cur_windows))
        if added or removed:
            payload = json.dumps({"added": added, "removed": removed})
            yield f"event: windows\ndata: {payload}\n\n"
            last_sent = time.monotonic()
        prev_windows = cur_windows

        cur_runs = _sse_runs_snapshot()
        if cur_runs != prev_runs:
            # Present-and-changed runs ride along with status + pr_url + label so
            # the client can toast a → awaiting_review transition (and link its
            # PR) without a second fetch. Removed runs need no payload — any diff
            # still re-renders the board via the client's existing refresh path.
            changed = [
                {
                    "task_id": tid,
                    "status": v["status"],
                    "pr_url": v["pr_url"],
                    "label": v["label"],
                }
                for tid, v in cur_runs.items()
                if prev_runs.get(tid) != v
            ]
            payload = json.dumps({"changed": len(changed), "runs": changed})
            yield f"event: runs\ndata: {payload}\n\n"
            last_sent = time.monotonic()
        prev_runs = cur_runs

        cur_terms = _sse_terms_snapshot()
        newly_ready = sorted(cur_terms - prev_terms)
        if newly_ready:
            payload = json.dumps({"ready": newly_ready})
            yield f"event: term-ready\ndata: {payload}\n\n"
            last_sent = time.monotonic()
        prev_terms = cur_terms

        now = time.monotonic()
        if now - last_sent >= SSE_KEEPALIVE_INTERVAL:
            yield ": keepalive\n\n"
            last_sent = now


@app.route("/api/events")
@require_auth
def api_events():
    resp = Response(_sse_stream(), mimetype="text/event-stream")
    # no-cache + no proxy buffering so frames reach the browser immediately
    # (a fronting reverse proxy must also stream text/event-stream unbuffered).
    resp.headers["Cache-Control"] = "no-cache"
    resp.headers["X-Accel-Buffering"] = "no"
    resp.headers["Connection"] = "keep-alive"
    return resp


# ---------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------

def _start_notifier():
    """Run needs-input push notifications from inside the dashboard process.

    ``notify.check_waiting`` is normally driven by the ``chela run`` daemon, but
    the dashboard is the always-on process in most deployments — so when
    ``CHELA_NOTIFY_URL`` is set we run the same edge-triggered scan here in a
    single daemon thread (every ``CHELA_NOTIFY_INTERVAL`` seconds) rather than
    requiring a second long-lived process. No-op when notifications are off."""
    if not notify.enabled():
        return
    import threading

    def _loop():
        waiting_seen: set[str] = set()
        while True:
            try:
                waiting_seen = notify.check_waiting(waiting_seen)
            except Exception:
                log.exception("notify: check_waiting failed")
            time.sleep(NOTIFY_INTERVAL)

    threading.Thread(target=_loop, name="chela-notifier", daemon=True).start()
    log.info("Needs-input notifications enabled (every %ds)", NOTIFY_INTERVAL)


def main():
    # threaded=True is required: the SSE generator at /api/events holds its
    # request thread open for the life of the connection, so a single-threaded
    # dev server would block every other request behind it.
    # debug=False on purpose: the Werkzeug auto-reloader respawns a child
    # process, and the interactive debugger is an RCE vector if the port is
    # ever exposed.
    #
    # Binds 127.0.0.1 by default — ZERO auth (see module docstring); put it
    # behind a tailnet / SSH tunnel for remote access. Override host/port with
    # CHELA_DASH_HOST / CHELA_DASHBOARD_PORT.
    host = config.dashboard_host()
    port = config.dashboard_port()

    # Loopback guard for the writable terminal wall. The wall is ON by default,
    # but it serves unauthenticated, writable shells — so if we're binding a
    # non-loopback interface and the operator hasn't explicitly opted into
    # exposing it, disable the wall (its routes 404 and its UI is hidden). This
    # makes a public bind safe by default; loopback binds (fronted by a tailnet /
    # SSH tunnel) are unaffected.
    if config.TERMINALS_ENABLED and not config.is_loopback_host(host) and not config.TERMINALS_EXPOSE:
        config.TERMINALS_ENABLED = False
        log.warning(
            "terminal wall disabled: bound to %s (non-loopback) without "
            "CHELA_TERMINALS_EXPOSE=true. The wall serves unauthenticated, "
            "writable shells — keep the dashboard on 127.0.0.1 behind a "
            "tailnet/SSH tunnel, or set CHELA_TERMINALS_EXPOSE=true to override "
            "(remote-code-execution risk).", host,
        )

    scheduler.init()  # open the WAL scheduler DB + init schema once, before serving
    _start_notifier()
    collab.start()  # P3: publish running agents as presence peers (to shared viewers)

    # Write down the port we are really binding, so another process (`chela plugin`,
    # `chela doctor`) can address us without guessing. A hook `url` is a literal baked
    # into the plugin manifest at render time — get this wrong and every hook POSTs into
    # a closed socket, fails open, and the feature does nothing at all, quietly.
    config.publish_dashboard_port(port, host)
    atexit.register(config.clear_dashboard_port)
    try:
        app.run(host=host, port=port, debug=False, threaded=True)
    finally:
        config.clear_dashboard_port()


if __name__ == "__main__":
    main()
