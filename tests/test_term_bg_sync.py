"""The dashboard's `--term-bg` CSS token and the live ttyd terminal's own xterm.js
background MUST be the byte-identical color — two independent literals with nothing
in the codebase forcing them to agree.

Regression for CMX-122/123: both assumed xterm content is transparent, so
`.term-frame`'s own background "shows through" and can be anything. Measured live,
ttyd paints its OWN opaque background from `scripts/agent-terminals.sh`'s
`TERM_THEME` (`#0d1117`, deliberately chosen to match `style.css`'s `--bg`) — nothing
shows through. CMX-122 set `--term-bg: #000` on that wrong assumption, making the
dashboard's frame/bar visibly *blacker* than the terminal painted inside it: a real
seam, the opposite of the "seamless footer" CMX-122 intended.

This test reads both single-source-of-truth literals and asserts they match, so a
future edit to either one alone (a re-themed ttyd, a CSS palette tweak) fails loudly
instead of silently reintroducing the seam.
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STYLE_CSS = ROOT / "chela" / "dashboard" / "static" / "style.css"
AGENT_TERMINALS_SH = ROOT / "scripts" / "agent-terminals.sh"

HEX_COLOR = r"#[0-9a-fA-F]{3,6}"


def _term_bg_css() -> str:
    css = STYLE_CSS.read_text()
    m = re.search(r"--term-bg:\s*(" + HEX_COLOR + r")\s*;", css)
    assert m, "style.css must declare --term-bg as a hex color in :root"
    return m.group(1).lower()


def _term_theme_background_sh() -> str:
    sh = AGENT_TERMINALS_SH.read_text()
    m = re.search(r'"background"\s*:\s*"(' + HEX_COLOR + r')"', sh)
    assert m, "agent-terminals.sh must declare TERM_THEME's \"background\" as a hex color"
    return m.group(1).lower()


def test_term_bg_matches_live_ttyd_theme_background():
    css_color = _term_bg_css()
    sh_color = _term_theme_background_sh()
    assert css_color == sh_color, (
        f"style.css --term-bg ({css_color}) must match agent-terminals.sh's "
        f"TERM_THEME background ({sh_color}) — the dashboard's terminal-colored "
        "surfaces must always be the color ttyd actually paints, not a guess"
    )
