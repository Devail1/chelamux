"""Text on a solid `--accent` fill reads `--on-accent`, and that ink is legible in
EVERY theme (CMX-37).

The sidebar gear menu painted `.popover-item:hover { background: var(--accent);
color: #fff }`. #fff is under WCAG AA on every accent we ship (1.3:1 on warm's
off-white accent, where "Settings" vanished; 2.0-3.7:1 elsewhere). A one-off warm
override patched ONE selector (`.pane-overflow-menu`) and left the shape everywhere
else. The fix is a per-theme `--on-accent` token, so the guards here are:

  * every theme in the Settings picker declares its OWN `--on-accent`, and it meets
    4.5:1 against that theme's `--accent` (computed, WCAG 2.x relative luminance);
  * no rule in style.css — or inline style in the dashboard JS — paints a solid
    `--accent` background under a literal white, or under any ink but the token;
  * the gear/overflow menu hover rows (the reported defect) read the token.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
STATIC = ROOT / "chela" / "dashboard" / "static"
STYLE_CSS = STATIC / "style.css"
NAV_JS = STATIC / "js" / "nav.js"

AA = 4.5
WHITE = r"(?:#fff\b|#ffffff\b|white\b)"


def _strip_comments(css: str) -> str:
    return re.sub(r"/\*.*?\*/", "", css, flags=re.S)


def _theme_vars() -> dict[str, dict[str, str]]:
    """theme -> {custom property: value}, 'dark' being the :root default."""
    css = _strip_comments(STYLE_CSS.read_text())
    root = re.search(r"^:root\s*\{(.*?)^\}", css, re.S | re.M)
    assert root, "style.css must have a :root block"
    blocks = {"dark": root.group(1)}
    for name, body in re.findall(r'^body\[data-theme="([a-z]+)"\]\s*\{(.*?)\}', css, re.M | re.S):
        blocks[name] = body
    return {t: dict(re.findall(r"(--[\w-]+):\s*([^;]+?)\s*;", b)) for t, b in blocks.items()}


def _nav_themes() -> list[str]:
    m = re.search(r"const THEME_LABELS = \{(.*?)\};", NAV_JS.read_text(), re.S)
    assert m, "nav.js must declare THEME_LABELS"
    return re.findall(r"(\w+):\s*'", m.group(1))


def _luminance(hex_color: str) -> float:
    h = hex_color.lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    assert re.fullmatch(r"[0-9a-fA-F]{6}", h), f"not a hex colour: {hex_color!r}"
    chans = [int(h[i:i + 2], 16) / 255 for i in (0, 2, 4)]
    lin = [c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4 for c in chans]
    return 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2]


def contrast(a: str, b: str) -> float:
    hi, lo = sorted((_luminance(a), _luminance(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


def test_contrast_math_matches_wcag_reference_points():
    assert contrast("#000000", "#ffffff") == pytest.approx(21.0)
    assert contrast("#777777", "#ffffff") == pytest.approx(4.48, abs=0.01)
    assert contrast("#fff", "#fff") == pytest.approx(1.0)


def test_theme_lists_agree():
    assert sorted(_theme_vars()) == sorted(_nav_themes())


@pytest.mark.parametrize("theme", _nav_themes())
def test_on_accent_meets_aa_against_accent(theme):
    v = _theme_vars()[theme]
    # Each theme states its own ink — inheriting :root's would only be right by luck.
    assert "--accent" in v, f"theme {theme!r} must declare --accent"
    assert "--on-accent" in v, f"theme {theme!r} must declare its own --on-accent"
    ratio = contrast(v["--on-accent"], v["--accent"])
    assert ratio >= AA, (
        f"theme {theme!r}: --on-accent {v['--on-accent']} on --accent {v['--accent']} "
        f"is {ratio:.2f}:1, below WCAG AA {AA}:1"
    )


def _rules(css: str):
    """(selector, declarations) for every leaf rule, @media bodies included."""
    for sel, body in re.findall(r"([^{}]+)\{([^{}]*)\}", _strip_comments(css)):
        yield " ".join(sel.split()), body


SOLID_ACCENT_BG = re.compile(r"background(?:-color)?\s*:\s*var\(--accent\)\s*(?:;|$)")
COLOR_DECL = re.compile(r"(?<![-\w])color\s*:\s*([^;]+)")


def _offending_rules(css: str) -> list[str]:
    bad = []
    for sel, body in _rules(css):
        if not SOLID_ACCENT_BG.search(body):
            continue
        for value in COLOR_DECL.findall(body):
            if re.search(WHITE, value) or value.strip() != "var(--on-accent)":
                bad.append(f"{sel} {{ color: {value.strip()} }}")
    return bad


def test_no_css_rule_pairs_an_accent_fill_with_white_or_a_literal_ink():
    bad = _offending_rules(STYLE_CSS.read_text())
    assert not bad, "text on a solid --accent fill must read var(--on-accent):\n" + "\n".join(bad)


def test_the_guard_catches_the_original_defect():
    assert _offending_rules(".popover-item:hover { background: var(--accent); color: #fff; }")
    assert _offending_rules(".x { background: var(--accent); color: white }")
    assert not _offending_rules(".x { background: var(--accent); color: var(--on-accent); }")
    # a tinted (color-mix) accent is not a solid fill — the token is not required there
    assert not _offending_rules(".x { background: color-mix(in srgb, var(--accent) 14%, transparent); color: var(--text); }")


def test_no_inline_js_style_pairs_an_accent_fill_with_white():
    bad = []
    for js in sorted((STATIC / "js").rglob("*.js")):
        for i, line in enumerate(js.read_text().splitlines(), 1):
            if re.search(r"background(?:-color)?\s*:\s*var\(--accent\)", line) and re.search(
                r"(?<![-\w])color\s*:\s*" + WHITE, line
            ):
                bad.append(f"{js.relative_to(ROOT)}:{i}: {line.strip()}")
    assert not bad, "inline style pairs an --accent fill with white:\n" + "\n".join(bad)


@pytest.mark.parametrize(
    "selector",
    [
        ".popover-item:hover",
        ".overflow-menu .ov-item:hover .ov-ic",
        ".pane-overflow-menu button.popover-item:hover",
        ".pane-overflow-menu button.popover-item:hover .ov-ic",
    ],
)
def test_menu_hover_rows_read_the_token(selector):
    # The icon rule has no background of its own (it rides its row's accent fill),
    # so the fill-pairing guard above cannot see it — pin these by name.
    colors = [
        COLOR_DECL.findall(body) for sel, body in _rules(STYLE_CSS.read_text()) if sel == selector
    ]
    assert colors, f"style.css has no {selector!r} rule"
    assert all(c.strip() == "var(--on-accent)" for cs in colors for c in cs) and any(colors), (
        f"{selector!r} must set color: var(--on-accent), got {colors}"
    )


def test_no_theme_scoped_on_accent_one_offs_remain():
    # The token replaces per-theme selector patches like the old warm
    # `.pane-overflow-menu ... :hover { color: #1c1b19 }`.
    css = _strip_comments(STYLE_CSS.read_text())
    assert not re.search(r'body\[data-theme="[a-z]+"\]\s+[.#\w]', css)
