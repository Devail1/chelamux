"""The dashboard's `--term-bg` CSS token and the terminal's own xterm.js background
MUST be the byte-identical color — for EVERY theme — or a seam shows between the
frame/context bar and the terminal painted inside it.

Regression for CMX-122/123: both assumed xterm content is transparent, so
`.term-frame`'s own background "shows through" and can be anything. Measured live,
ttyd paints its OWN opaque background — nothing shows through. CMX-122 set
`--term-bg: #000` on that wrong assumption, making the dashboard's frame/bar
visibly *blacker* than the terminal painted inside it.

CMX-381 generalised this from the default theme only: each theme now carries its
own terminal palette (chela/dashboard/term_themes.py, applied live inside ttyd by
app.py's `_term_theme_shim`) and its own `--term-bg` in style.css. So the
single-source-of-truth pairs checked here are:

  * every theme: style.css `--term-bg` == term_themes.py `xterm.background`;
  * `dark` (the :root default): term_themes.py == agent-terminals.sh's launch
    `TERM_THEME`, whole ITheme — what ttyd paints before the shim runs;
  * the theme SETS agree: style.css blocks == term_themes.py == nav.js's picker.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from chela.dashboard.term_themes import DEFAULT_THEME, TERM_THEMES

ROOT = Path(__file__).resolve().parent.parent
STYLE_CSS = ROOT / "chela" / "dashboard" / "static" / "style.css"
NAV_JS = ROOT / "chela" / "dashboard" / "static" / "js" / "nav.js"
AGENT_TERMINALS_SH = ROOT / "scripts" / "agent-terminals.sh"

HEX_COLOR = r"#[0-9a-fA-F]{3,6}"
ANSI_16 = [
    "black", "red", "green", "yellow", "blue", "magenta", "cyan", "white",
    "brightBlack", "brightRed", "brightGreen", "brightYellow",
    "brightBlue", "brightMagenta", "brightCyan", "brightWhite",
]


def _css_blocks() -> dict[str, str]:
    """theme name -> the body of its palette block ('dark' = :root)."""
    css = STYLE_CSS.read_text()
    root = re.search(r"^:root\s*\{(.*?)^\}", css, re.S | re.M)
    assert root, "style.css must have a :root block"
    blocks = {DEFAULT_THEME: root.group(1)}
    for name, body in re.findall(r'^body\[data-theme="([a-z]+)"\]\s*\{(.*?)\}', css, re.M | re.S):
        blocks[name] = body
    return blocks


def _css_term_bg(block: str) -> str | None:
    m = re.search(r"--term-bg:\s*(" + HEX_COLOR + r")\s*;", block)
    return m.group(1).lower() if m else None


def _sh_term_theme() -> dict:
    sh = AGENT_TERMINALS_SH.read_text()
    m = re.search(r"^TERM_THEME='(\{.*\})'$", sh, re.M)
    assert m, "agent-terminals.sh must declare TERM_THEME as a single-quoted JSON literal"
    return json.loads(m.group(1))


def _nav_themes() -> list[str]:
    js = NAV_JS.read_text()
    m = re.search(r"const THEME_LABELS = \{(.*?)\};", js, re.S)
    assert m, "nav.js must declare THEME_LABELS"
    return re.findall(r"(\w+):\s*'", m.group(1))


@pytest.mark.parametrize("theme", sorted(TERM_THEMES))
def test_every_theme_term_bg_matches_its_terminal_background(theme):
    blocks = _css_blocks()
    assert theme in blocks, f"style.css has no palette block for theme {theme!r}"
    css_color = _css_term_bg(blocks[theme])
    assert css_color, f"style.css theme {theme!r} must declare its own --term-bg"
    xterm_bg = TERM_THEMES[theme]["xterm"]["background"].lower()
    assert css_color == xterm_bg, (
        f"theme {theme!r}: style.css --term-bg ({css_color}) must match its terminal "
        f"palette's background ({xterm_bg}) — else the frame/bar seams against the terminal"
    )


def test_theme_sets_agree_css_palettes_and_picker():
    css = set(_css_blocks())
    nav = _nav_themes()
    assert set(TERM_THEMES) == css, (css ^ set(TERM_THEMES))
    assert set(nav) == set(TERM_THEMES), (set(nav) ^ set(TERM_THEMES))
    assert "warm" in nav


@pytest.mark.parametrize("theme", sorted(TERM_THEMES))
def test_every_palette_is_a_full_itheme(theme):
    p = TERM_THEMES[theme]
    x = p["xterm"]
    for k in ["background", "foreground", "cursor", "cursorAccent", "selectionBackground", *ANSI_16]:
        assert re.fullmatch(HEX_COLOR, x.get(k, "")), f"{theme}.{k} missing/not hex"
    assert re.fullmatch(HEX_COLOR, p["scrollbar"]) and re.fullmatch(HEX_COLOR, p["scrollbarHover"])
    assert len({x[k].lower() for k in ANSI_16}) == 16, f"{theme}: ANSI colours must all differ"
    assert all(x[k].lower() != x["background"].lower() for k in ANSI_16), (
        f"{theme}: an ANSI colour equal to the background is invisible text")


def test_default_palette_is_the_ttyd_launch_theme():
    """ttyd paints its launch TERM_THEME before the shim runs (and for a viewer on
    the default theme, forever) — it must BE the dark palette, whole object."""
    assert _sh_term_theme() == TERM_THEMES[DEFAULT_THEME]["xterm"]


def test_warm_theme_palette():
    """The warm theme's chrome is the approved mockup's dark palette; status colours
    are Okabe-Ito (sky / orange / vermillion), never red-vs-green alone."""
    block = _css_blocks()["warm"]

    def var(n):
        m = re.search(r"--" + n + r":\s*(" + HEX_COLOR + r")\s*;", block)
        return m.group(1).lower() if m else None
    assert (var("bg"), var("surface"), var("border"), var("text"), var("text-dim"), var("accent")) == (
        "#262624", "#1f1e1d", "#35332f", "#eceae4", "#a39f95", "#e9e4d8")
    assert (var("green"), var("yellow"), var("red")) == ("#56b4e9", "#e69f00", "#d55e00")
    assert var("term-bg") == "#1c1b19"
