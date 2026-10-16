"""CMX-160: the visitor-facing launch set carries no em dash and no spaced en dash in prose.

To a Hacker News reader an em dash reads as AI-written prose, so the launch pages were
rewritten sentence by sentence (a period, a colon, a comma, parentheses), not by swapping
the character. This guard keeps them that way: put one back and it goes RED.

Code is not prose, so ``<code>``/``<pre>`` elements, fenced blocks and inline backticks are
stripped before the check. A number range such as ``3–5`` (an UNSPACED en dash) is fine.

The file list is explicit on purpose: phase 2 (the rest of ``docs/*.md``) extends it.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent

LAUNCH_SET = [
    "landing/index.html",
    "landing/docs.html",
    "README.md",
    "action.yml",
    "skills/README.md",
    "skills/blindspot-pass/SKILL.md",
    "skills/chela-setup/SKILL.md",
    "skills/handoff/SKILL.md",
    "skills/implementation-plan/SKILL.md",
    "skills/interview-me/SKILL.md",
    "skills/orchestrate/SKILL.md",
    "skills/telegram-send/SKILL.md",
    "skills/telegram-setup/SKILL.md",
    "docs/GETTING_STARTED.md",
    "docs/CONFIG.md",
    "docs/HOOKS.md",
    "docs/TRUST_AND_APPROVAL_POSTURE.md",
    "docs/RISK_LEVELS.md",
]

# An em dash anywhere, or an en dash with whitespace on either side (a dash used as one).
DASH = re.compile("—|\\s–|–\\s")


def prose(text: str) -> str:
    """``text`` with every code region removed: HTML ``<code>``/``<pre>``, fenced and inline code."""
    text = re.sub(r"<(pre|code)\b[^>]*>.*?</\1>", " ", text, flags=re.S | re.I)
    text = re.sub(r"^(```|~~~).*?^\1", " ", text, flags=re.S | re.M)
    return re.sub(r"`[^`\n]*`", " ", text)


def offending_lines(text: str) -> list[str]:
    return [line.strip() for line in prose(text).splitlines() if DASH.search(line)]


@pytest.mark.parametrize("rel", LAUNCH_SET)
def test_launch_page_has_no_em_dash_in_prose(rel):
    path = ROOT / rel
    assert path.is_file(), f"{rel} is in the launch set but does not exist"
    bad = offending_lines(path.read_text(encoding="utf-8"))
    assert not bad, f"{rel}: rewrite the sentence (period, colon, comma, parentheses): {bad[:5]}"


DASHBOARD = ROOT / "chela" / "dashboard"

# A lone em dash that IS the whole string or text node means "no value" in a cell or a stat,
# and an en dash OPENING a string is a status glyph beside ✓ ✗ ● ? (`'– no ci'`). Both are
# glyphs, not a dash in a sentence.
PLACEHOLDER = re.compile("'—'|>—<|Context: —|(?<=['`])– ")


def dashboard_copy(text: str) -> str:
    """Dashboard source without its comments (HTML, block and line), placeholders blanked."""
    text = re.sub(r"<!--.*?-->|/\*.*?\*/", " ", text, flags=re.S)
    text = re.sub(r"(^|\s)//.*$", " ", text, flags=re.M)
    return PLACEHOLDER.sub(" ", text)


def test_dashboard_copy_has_no_em_dash():
    files = [DASHBOARD / "templates" / "index.html", *sorted((DASHBOARD / "static" / "js").glob("*.js"))]
    assert len(files) > 20, "the dashboard sources moved; point this guard at them"
    bad = [f"{p.name}: {line.strip()[:100]}" for p in files
           for line in dashboard_copy(p.read_text(encoding="utf-8")).splitlines() if DASH.search(line)]
    assert not bad, f"rewrite the visible copy (period, colon, comma, parentheses): {bad[:5]}"


@pytest.mark.parametrize("text,caught", [
    ("bar.title = 'Waiting — approve it';", True),
    ('<p title="a — b">x</p>', True),
    ("`<div>none — yet</div>`", True),
    ("x = 1; // a comment — fine", False),
    ("/* block\n — comment */", False),
    ("<!-- html — comment -->", False),
    ("return '—';", False),
    ("`<div class=\"e\">—</div>`", False),
    ("bar.title = 'Context: —';", False),
    ("url = 'https://x.y/z — not a comment';", True),
    ("none: { label: '– no ci' },", False),
    ("...skipped.map(s => `– ${s.id}`),", False),
    ("label: 'no – ci'", True),
])
def test_dashboard_copy_rule(text, caught):
    assert bool(DASH.search(dashboard_copy(text))) is caught


def test_launch_set_covers_every_skill():
    on_disk = {p.relative_to(ROOT).as_posix() for p in ROOT.glob("skills/*/SKILL.md")}
    assert on_disk <= set(LAUNCH_SET), f"a skill is missing from the launch set: {on_disk - set(LAUNCH_SET)}"


@pytest.mark.parametrize("text", [
    "Run agents unattended — chela merges.",
    "no spaces—still an em dash",
    "a spaced – en dash",
    "trailing en dash –",
    "<p>prose</p> — after a tag",
    "`code` then — prose",
    "```\ncode\n```\nprose — after the fence",
])
def test_dash_in_prose_is_caught(text):
    assert offending_lines(text)


@pytest.mark.parametrize("text", [
    "3–5 minutes",                         # a number range is not a dash in prose
    "run `chela x — y` here",              # inline code
    "<code>a — b</code> and <pre>c — d</pre>",
    "<PRE class=\"x\">\nmulti\n— line\n</PRE>",
    "```text\nsweep — output\n```",
    "~~~\nalso — fenced\n~~~",
    "use --flags and a - hyphen",
])
def test_code_and_ranges_are_not_prose(text):
    assert not offending_lines(text)
