"""Per-theme terminal palettes (CMX-381).

A dashboard theme (`body[data-theme=…]` in static/style.css, picked in Settings >
Appearance) used to re-declare only the CHROME palette: the terminals kept the one
xterm theme ttyd was launched with (scripts/agent-terminals.sh `TERM_THEME`), so
Gruvbox chrome framed GitHub-dark `#0d1117` terminals — a visible seam.

This is the single table of terminal palettes, one per theme. app.py injects it
into every ttyd page (`_term_theme_shim`), where a shim picks the entry for the
viewer's `chela_theme` (localStorage, same-origin with the dashboard) and sets it
on `window.term` live — no ttyd restart. Per entry:

  xterm           a full xterm.js `ITheme` (bg/fg/cursor/selection + the 16 ANSI)
  scrollbar       the injected in-terminal scrollbar thumb (the theme's --border)
  scrollbarHover  its hover colour

Invariants guarded by tests/test_term_bg_sync.py:
  * every theme's `xterm.background` == its `--term-bg` in style.css (no seam);
  * `dark` == agent-terminals.sh's launch `TERM_THEME` (the palette ttyd paints
    before the shim runs, and what an unthemed viewer sees);
  * the keys here == the Settings picker's list in nav.js == style.css's blocks.

`CHELA_TERM_THEME` (a JSON ITheme, read by BOTH agent-terminals.sh and the
dashboard — both source chela.env) stays the operator override: when set, it
wins over every theme's palette here.
"""
from __future__ import annotations

import json
import os

TERM_THEMES: dict[str, dict] = {
    # GitHub Dark — byte-identical to agent-terminals.sh's launch TERM_THEME.
    "dark": {
        "xterm": {
            "background": "#0d1117", "foreground": "#c9d1d9", "cursor": "#58a6ff",
            "cursorAccent": "#0d1117", "selectionBackground": "#264f78",
            "black": "#484f58", "red": "#ff7b72", "green": "#3fb950", "yellow": "#d29922",
            "blue": "#58a6ff", "magenta": "#bc8cff", "cyan": "#39c5cf", "white": "#b1bac4",
            "brightBlack": "#6e7681", "brightRed": "#ffa198", "brightGreen": "#56d364",
            "brightYellow": "#e3b341", "brightBlue": "#79c0ff", "brightMagenta": "#d2a8ff",
            "brightCyan": "#56d4dd", "brightWhite": "#f0f6fc",
        },
        "scrollbar": "#21262d", "scrollbarHover": "#30363d",
    },
    # GitHub Dark Dimmed
    "dim": {
        "xterm": {
            "background": "#1c2128", "foreground": "#adbac7", "cursor": "#539bf5",
            "cursorAccent": "#1c2128", "selectionBackground": "#2e4c77",
            "black": "#545d68", "red": "#f47067", "green": "#57ab5a", "yellow": "#c69026",
            "blue": "#539bf5", "magenta": "#b083f0", "cyan": "#39c5cf", "white": "#909dab",
            "brightBlack": "#636e7b", "brightRed": "#ff938a", "brightGreen": "#6bc46d",
            "brightYellow": "#daaa3f", "brightBlue": "#6cb6ff", "brightMagenta": "#dcbdfb",
            "brightCyan": "#56d4dd", "brightWhite": "#cdd9e5",
        },
        "scrollbar": "#373e47", "scrollbarHover": "#444c56",
    },
    # Deep navy/indigo (Tokyo Night family)
    "midnight": {
        "xterm": {
            "background": "#0b1021", "foreground": "#c4ccf0", "cursor": "#7aa2f7",
            "cursorAccent": "#0b1021", "selectionBackground": "#283457",
            "black": "#414868", "red": "#f7768e", "green": "#9ece6a", "yellow": "#e0af68",
            "blue": "#7aa2f7", "magenta": "#bb9af7", "cyan": "#7dcfff", "white": "#a9b1d6",
            "brightBlack": "#565f89", "brightRed": "#ff899d", "brightGreen": "#b3e07c",
            "brightYellow": "#f0c383", "brightBlue": "#8db0ff", "brightMagenta": "#c7a9ff",
            "brightCyan": "#a4daff", "brightWhite": "#c0caf5",
        },
        "scrollbar": "#28304f", "scrollbarHover": "#353f66",
    },
    # Nord
    "nord": {
        "xterm": {
            "background": "#2e3440", "foreground": "#d8dee9", "cursor": "#88c0d0",
            "cursorAccent": "#2e3440", "selectionBackground": "#434c5e",
            "black": "#3b4252", "red": "#bf616a", "green": "#a3be8c", "yellow": "#ebcb8b",
            "blue": "#81a1c1", "magenta": "#b48ead", "cyan": "#88c0d0", "white": "#e5e9f0",
            "brightBlack": "#4c566a", "brightRed": "#d08770", "brightGreen": "#b4d19f",
            "brightYellow": "#f0d9a8", "brightBlue": "#9ab5d1", "brightMagenta": "#c6a4c0",
            "brightCyan": "#8fbcbb", "brightWhite": "#eceff4",
        },
        "scrollbar": "#434c5e", "scrollbarHover": "#4c566a",
    },
    # Gruvbox dark (black lifted off the bg so it stays visible)
    "gruvbox": {
        "xterm": {
            "background": "#282828", "foreground": "#ebdbb2", "cursor": "#ebdbb2",
            "cursorAccent": "#282828", "selectionBackground": "#504945",
            "black": "#3c3836", "red": "#cc241d", "green": "#98971a", "yellow": "#d79921",
            "blue": "#458588", "magenta": "#b16286", "cyan": "#689d6a", "white": "#a89984",
            "brightBlack": "#928374", "brightRed": "#fb4934", "brightGreen": "#b8bb26",
            "brightYellow": "#fabd2f", "brightBlue": "#83a598", "brightMagenta": "#d3869b",
            "brightCyan": "#8ec07c", "brightWhite": "#ebdbb2",
        },
        "scrollbar": "#504945", "scrollbarHover": "#665c54",
    },
    # Solarized Dark (brights re-hued: canonical Solarized maps them to base
    # greys, and its brightBlack IS the background — invisible text)
    "solarized": {
        "xterm": {
            "background": "#002b36", "foreground": "#93a1a1", "cursor": "#93a1a1",
            "cursorAccent": "#002b36", "selectionBackground": "#0e4b59",
            "black": "#073642", "red": "#dc322f", "green": "#859900", "yellow": "#b58900",
            "blue": "#268bd2", "magenta": "#d33682", "cyan": "#2aa198", "white": "#eee8d5",
            "brightBlack": "#586e75", "brightRed": "#f0605d", "brightGreen": "#a3b800",
            "brightYellow": "#d7a800", "brightBlue": "#4aa3e8", "brightMagenta": "#6c71c4",
            "brightCyan": "#3fc1b7", "brightWhite": "#fdf6e3",
        },
        "scrollbar": "#0e4b59", "scrollbarHover": "#16606f",
    },
    # Rosé Pine Moon
    "rose": {
        "xterm": {
            "background": "#232136", "foreground": "#e0def4", "cursor": "#c4a7e7",
            "cursorAccent": "#232136", "selectionBackground": "#44415a",
            "black": "#393552", "red": "#eb6f92", "green": "#3e8fb0", "yellow": "#f6c177",
            "blue": "#9ccfd8", "magenta": "#c4a7e7", "cyan": "#ea9a97", "white": "#e0def4",
            "brightBlack": "#6e6a86", "brightRed": "#f08aa8", "brightGreen": "#5aa6c4",
            "brightYellow": "#f9d19b", "brightBlue": "#b5dce3", "brightMagenta": "#d4bef0",
            "brightCyan": "#f0b3b0", "brightWhite": "#f2f0fb",
        },
        "scrollbar": "#393552", "scrollbarHover": "#44415a",
    },
    # Warm (CMX-381, from the 2026-09-28 restyle mockup): warm greys, off-white
    # cursor. The 16 ANSI are desaturated warm hues, but each keeps its own hue
    # family so red/green/yellow/blue/magenta/cyan stay distinguishable (and
    # brights sit visibly above their normals). Status in this theme's CHROME is
    # Okabe-Ito sky/orange — see style.css — never red-vs-green alone.
    "warm": {
        "xterm": {
            "background": "#1c1b19", "foreground": "#d9d5cc", "cursor": "#e9e4d8",
            "cursorAccent": "#1c1b19", "selectionBackground": "#4a4640",
            "black": "#3b3935", "red": "#d9795f", "green": "#a8b86b", "yellow": "#e0b15c",
            "blue": "#7fa8c9", "magenta": "#c18fb0", "cyan": "#84b8ac", "white": "#d9d5cc",
            "brightBlack": "#77736a", "brightRed": "#eb9278", "brightGreen": "#bfcc85",
            "brightYellow": "#f0c878", "brightBlue": "#9cc0dc", "brightMagenta": "#d6a8c6",
            "brightCyan": "#a0cfc3", "brightWhite": "#f2efe8",
        },
        "scrollbar": "#35332f", "scrollbarHover": "#4a4640",
    },
}

DEFAULT_THEME = "dark"


def operator_override() -> dict | None:
    """`CHELA_TERM_THEME` as a parsed ITheme, or None when unset/invalid. An
    invalid value is ignored here exactly as ttyd would choke on it — the
    per-theme palette is the safer fallback than a broken terminal."""
    raw = os.environ.get("CHELA_TERM_THEME", "").strip()
    if not raw:
        return None
    try:
        val = json.loads(raw)
    except ValueError:
        return None
    return val if isinstance(val, dict) and val else None
