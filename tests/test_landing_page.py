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
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PAGE = ROOT / "landing" / "index.html"


def _html() -> str:
    return re.sub(r"<!--.*?-->", "", PAGE.read_text(encoding="utf-8"), flags=re.S)


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
