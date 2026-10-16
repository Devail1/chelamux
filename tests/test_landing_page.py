"""CMX-99 — the landing page keeps the dev-tool conventions it adopted.

* the hero H1 is the README's gate-first pitch, word for word (CMX-91 owns the line;
  the landing reuses it, so the two cannot drift apart);
* exactly one H1;
* no release-pinned "New in X.Y" heading — the page links GitHub Releases instead, so it
  does not go stale every release;
* the install copy button ships ``hidden`` and the stylesheet re-hides ``.copy[hidden]``
  in a rule that actually applies (not commented out, not parked in an ``@media`` block) —
  with JS off the command is plain selectable text. That the script REVEALS the button
  is proven by running it: ``tests/landing_copy_button.test.mjs`` (DEFEAT_SHAPES 99).

Every check reads the page with HTML and CSS comments stripped, so a guarded construct
that is merely commented out does not still satisfy it.
"""
from __future__ import annotations

import re
from html.parser import HTMLParser
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
PAGE = ROOT / "landing" / "index.html"
DOCS = ROOT / "landing" / "docs.html"


def _html(page: Path = PAGE) -> str:
    return re.sub(r"<!--.*?-->", "", page.read_text(encoding="utf-8"), flags=re.S)


def _top_level_css() -> str:
    """The page's <style> text, comments removed and every ``@``-block dropped."""
    css = " ".join(re.findall(r"<style\b[^>]*>(.*?)</style>", _html(), flags=re.S))
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    out, depth, i = [], 0, 0
    while i < len(css):
        if depth == 0 and css[i] == "@":
            j = css.find("{", i)
            if j < 0:
                break
            k, d = j + 1, 1
            while k < len(css) and d:
                d += {"{": 1, "}": -1}.get(css[k], 0)
                k += 1
            out.append("}")  # keep a rule boundary where the block was
            i = k
            continue
        depth += {"{": 1, "}": -1}.get(css[i], 0)
        out.append(css[i])
        i += 1
    return "".join(out)


def _prose(fragment: str) -> str:
    return " ".join(re.sub(r"<[^>]+>", " ", fragment).split())


def test_landing_h1_is_the_readme_pitch_verbatim():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    pitch = re.search(r"^# chelamux\n\n\*\*(.+?)\*\*", readme, flags=re.S | re.M)
    assert pitch, "README lost its bold pitch line under the title"
    h1 = re.findall(r"<h1\b[^>]*>(.*?)</h1>", _html(), flags=re.S)
    assert len(h1) == 1, f"the landing must have exactly one <h1>, found {len(h1)}"
    assert _prose(h1[0]) == " ".join(pitch.group(1).split())


def test_landing_has_no_release_pinned_heading_and_links_releases():
    html = _html()
    assert not re.search(r"<h[1-6][^>]*>\s*New in \d", html), "a 'New in X.Y' heading goes stale"
    assert re.search(r'<a\b[^>]*\bhref="https://github\.com/Devail1/chelamux/releases(?:/latest)?/?"', html), \
        "the 'Latest release' line must be a real link to GitHub Releases"


def test_install_copy_button_degrades_without_js():
    html = _html()
    buttons = re.findall(r"<button\b[^>]*\bclass=\"copy\"[^>]*>", html)
    assert buttons and all(re.search(r"\bhidden\b", b) for b in buttons), buttons
    css = _top_level_css()
    assert re.search(r"\.copy\s*\{[^}]*\bdisplay\s*:", css), "the .copy rule no longer sets display"
    assert re.search(
        r"(?:^|[};])\s*\.copy\[hidden\]\s*\{[^}]*\bdisplay\s*:\s*none\b", css
    ), ".copy sets display, so a top-level .copy[hidden]{display:none} must re-hide it, " \
       "or the dead button shows with JS off"
    # the command itself is real text in a <pre>, not injected by script
    pre = re.search(r'<pre id="install-cmd">(.*?)</pre>', html, flags=re.S)
    assert pre and "uv sync --all-extras" in _prose(pre.group(1))


# A colour emoji (📥) or a ``FE0F`` emoji-presentation selector (⚖️) renders in the OS's
# emoji font, never in the accent colour; text glyphs (◷ ▦) depend on the font too.
_EMOJI = re.compile("[\U0001F000-\U0001FFFF\uFE0F]")
_VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "track", "wbr"}


class _IconScan(HTMLParser):
    """Collect every ``.ic`` element: its tag, whether it holds an <svg>, and its text
    outside any <svg>."""

    def __init__(self) -> None:
        super().__init__()
        self.icons: list[dict] = []
        self._open: list[tuple[str, dict | None]] = []
        self._svg = 0

    def _cur(self) -> dict | None:
        return next((ic for _, ic in reversed(self._open) if ic is not None), None)

    def handle_starttag(self, tag, attrs):
        classes = (dict(attrs).get("class") or "").split()
        ic = None
        if "ic" in classes and self._cur() is None:
            ic = {"tag": tag, "svg": tag == "svg", "text": ""}
            self.icons.append(ic)
        elif tag == "svg" and self._cur() is not None:
            self._cur()["svg"] = True
        if tag == "svg":
            self._svg += 1
        if tag not in _VOID:
            self._open.append((tag, ic))

    def handle_endtag(self, tag):
        if tag == "svg":
            self._svg -= 1
        while self._open:
            if self._open.pop()[0] == tag:
                break

    def handle_data(self, data):
        if self._svg == 0 and self._cur() is not None:
            self._cur()["text"] += data


def _icons(page: Path) -> list[dict]:
    scan = _IconScan()
    scan.feed(_html(page))
    return scan.icons


@pytest.mark.parametrize("page", [PAGE, DOCS], ids=["index", "docs"])
def test_every_ic_icon_is_an_inline_svg_not_a_glyph(page):
    """CMX-159: every ``.ic`` is (or wraps) an inline Lucide SVG coloured by currentColor;
    no text glyph or emoji stands in for one."""
    icons = _icons(page)
    assert icons, f"{page.name} has no .ic icons — the scan is not reading the page"
    bad = [ic for ic in icons if not ic["svg"] or ic["text"].strip()]
    assert not bad, f"{page.name}: .ic elements that are not a bare <svg>: {bad}"


def test_feature_cards_each_carry_an_svg_icon():
    features = re.search(r'<section id="features".*?</section>', _html(), flags=re.S)
    assert features, "the #features section is gone"
    icons = _icons_in(features.group(0))
    assert len(icons) == 4 and all(ic["svg"] and not ic["text"].strip() for ic in icons), icons


def _icons_in(fragment: str) -> list[dict]:
    scan = _IconScan()
    scan.feed(fragment)
    return scan.icons


@pytest.mark.parametrize("page", [PAGE, DOCS], ids=["index", "docs"])
def test_landing_pages_carry_no_emoji(page):
    """Outside a verbatim sample (``<pre>``/``<code>`` — e.g. the inbox line chela really
    types, which starts with 📥) and comments, no emoji is used as decoration."""
    html = re.sub(r"/\*.*?\*/", "", _html(page), flags=re.S)
    html = re.sub(r"<(pre|code)\b[^>]*>.*?</\1>", "", html, flags=re.S)
    hits = sorted({m.group(0) for m in _EMOJI.finditer(html)})
    assert not hits, f"{page.name} carries emoji {hits!r} — use an inline Lucide SVG"


def test_lucide_isc_notice_is_shipped():
    notice = (ROOT / "NOTICE").read_text(encoding="utf-8")
    # the licence text itself (heading, copyright, grant), not just a mention of its name
    assert re.search(r"^\s*ISC License\s*$", notice, flags=re.M), "NOTICE lacks the ISC licence text"
    assert "held by Lucide\n  Contributors 2022" in notice
    assert "provided that the above\n  copyright notice and this permission notice appear in all copies" in notice
    for page in (PAGE, DOCS):
        assert re.search(r'<a\b[^>]*href="[^"]*/NOTICE"[^>]*>[^<]*Lucide', _html(page)), page.name


def test_hero_demo_column_is_wider_than_the_pitch_and_not_height_pinned():
    """CMX-159: the 16:10 demo grows to span the pitch column by width (the split + a wider
    hero band), never by a fixed height that would stretch or crop the clip."""
    css = _top_level_css()
    m = re.search(r"(?:^|[};])\s*\.hero\s*\{[^}]*grid-template-columns\s*:\s*"
                  r"minmax\(0,\s*([\d.]+)fr\)\s+minmax\(0,\s*([\d.]+)fr\)", css)
    assert m, "the desktop hero is no longer a two-column fr split"
    assert float(m.group(2)) / float(m.group(1)) >= 1.3, m.group(0)
    band = re.search(r"(?:^|[};])\s*nav\s*,\s*\.hero\s*\{[^}]*max-width\s*:\s*(\d+)px", css)
    assert band and int(band.group(1)) > 1080, "the hero band must run wider than the 1080px sections"
    video = re.search(r"\.demo video\s*\{([^}]*)\}", css)
    assert video and re.search(r"\bheight\s*:\s*auto\b", video.group(1)), video
