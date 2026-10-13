"""CMX-99 — the landing page keeps the dev-tool conventions it adopted.

* the hero H1 is the README's gate-first pitch, word for word (CMX-91 owns the line;
  the landing reuses it, so the two cannot drift apart);
* exactly one H1;
* no release-pinned "New in X.Y" heading — the page links GitHub Releases instead, so it
  does not go stale every release;
* the install copy button ships ``hidden`` and is revealed by script, and the stylesheet
  does not override ``[hidden]`` — with JS off the command is plain selectable text.
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PAGE = ROOT / "landing" / "index.html"


def _html() -> str:
    return PAGE.read_text(encoding="utf-8")


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
    assert "https://github.com/Devail1/chelamux/releases" in html


def test_install_copy_button_degrades_without_js():
    html = _html()
    buttons = re.findall(r"<button\b[^>]*\bclass=\"copy\"[^>]*>", html)
    assert buttons and all(re.search(r"\bhidden\b", b) for b in buttons), buttons
    assert re.search(r"\.copy\[hidden\]\{display:none\}", html), \
        ".copy sets display, so it must re-hide [hidden] or the dead button shows with JS off"
    assert "btn.hidden = false" in html
    # the command itself is real text in a <pre>, not injected by script
    pre = re.search(r'<pre id="install-cmd">(.*?)</pre>', html, flags=re.S)
    assert pre and "uv sync --all-extras" in _prose(pre.group(1))
