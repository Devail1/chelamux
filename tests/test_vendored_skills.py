"""CMX-179: Vercel's React skills are vendored pinned, licensed, scoped to chela and inert.

Agents load ``.claude/skills/`` unsandboxed, so the vendored tree must stay what was reviewed:
an exact upstream SHA, the MIT notice, chela's scope note, and no file that runs a command.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SKILLS = ROOT / ".claude" / "skills"
VENDORED = SKILLS / "VENDORED.md"
SHA = re.compile(r"`([0-9a-f]{40})`")
SCOPE = re.compile(r"<!-- chela:scope begin.*?<!-- chela:scope end -->", re.S)


def frontmatter(text: str) -> dict[str, str]:
    """Top-level ``key: value`` pairs of a ``---`` frontmatter block (empty when there is none).

    An indented line continues the key above it, as in composition-patterns' wrapped description.
    """
    m = re.match(r"---\n(.*?)\n---\n", text, re.S)
    if not m:
        return {}
    pairs = re.findall(r"^([a-z-]+):[ \t]*(.*(?:\n[ \t]+.*)*)$", m.group(1), re.M)
    return {k: " ".join(v.split()) for k, v in pairs}


def pinned_sha(text: str) -> str | None:
    m = re.search(r"^\| Commit \| " + SHA.pattern + r" \|$", text, re.M)
    return m.group(1) if m else None


@pytest.mark.parametrize("skill,name", [
    ("react-best-practices", "vercel-react-best-practices"),
    ("composition-patterns", "vercel-composition-patterns"),
])
def test_vendored_skill_has_mit_frontmatter(skill, name):
    fm = frontmatter((SKILLS / skill / "SKILL.md").read_text(encoding="utf-8"))
    assert fm.get("name") == name
    assert fm.get("description"), f"{skill}: the description is what makes Claude load it"
    assert fm.get("license") == "MIT"
    assert len(list((SKILLS / skill / "rules").glob("*.md"))) > 5


@pytest.mark.parametrize("text,expected", [
    ("---\nname: x\nlicense: MIT\n---\nbody", {"name": "x", "license": "MIT"}),
    ("---\ndescription:\n  wrapped\n  twice\nlicense: MIT\n---\n", {"description": "wrapped twice", "license": "MIT"}),
    ("---\nmetadata:\n  author: v\nlicense: MIT\n---\n", {"metadata": "author: v", "license": "MIT"}),
    ("name: x\nlicense: MIT\n", {}),
    ("---\nname: x\n", {}),
])
def test_frontmatter_parser(text, expected):
    assert frontmatter(text) == expected


def test_vendored_md_pins_a_full_sha():
    sha = pinned_sha(VENDORED.read_text(encoding="utf-8"))
    assert sha is not None, "VENDORED.md must name the exact upstream commit"
    assert "https://github.com/vercel-labs/agent-skills" in VENDORED.read_text(encoding="utf-8")


@pytest.mark.parametrize("text,expected", [
    ("| Commit | `063bee94c3f4df8453406c830b0a7df0f2860278` |", "063bee94c3f4df8453406c830b0a7df0f2860278"),
    ("| Commit | `063bee9` |", None),                                     # short SHA
    ("| Commit | `063bee94c3f4df8453406c830b0a7df0f28602781` |", None),   # 41 chars
    ("| Commit | `063BEE94C3F4DF8453406C830B0A7DF0F2860278` |", None),    # not git's spelling
    ("| Commit | `main` |", None),                                        # a branch moves
    ("| Commit | |", None),
])
def test_pinned_sha_parser(text, expected):
    assert pinned_sha(text) == expected


def test_notice_carries_the_vercel_mit_block():
    notice = (ROOT / "NOTICE").read_text(encoding="utf-8")
    block = notice[notice.index("https://github.com/vercel-labs/agent-skills"):]
    assert "Copyright (c) Vercel, Inc." in block
    assert "MIT License" in block and "Permission is hereby granted" in block
    assert pinned_sha(VENDORED.read_text(encoding="utf-8")) in notice, "NOTICE and VENDORED.md name different commits"
    assert notice.index("This product includes software") < notice.index("vercel-labs"), "keep it in the third-party block"


def test_license_file_ships_beside_the_skills():
    text = (SKILLS / "LICENSE-vercel-agent-skills").read_text(encoding="utf-8")
    assert text.startswith("MIT License") and "Copyright (c) Vercel, Inc." in text


def test_react_skill_is_scoped_to_the_vite_spa():
    block = SCOPE.search((SKILLS / "react-best-practices" / "SKILL.md").read_text(encoding="utf-8"))
    assert block, "react-best-practices/SKILL.md lost its chela:scope note"
    for name in ("`server-*`", "`async-api-routes`", "`bundle-*`", "`docs/FRONTEND.md` wins"):
        assert name in block.group(0)


def test_composition_skill_defers_to_frontend_md():
    block = SCOPE.search((SKILLS / "composition-patterns" / "SKILL.md").read_text(encoding="utf-8"))
    assert block and "`docs/FRONTEND.md` wins" in block.group(0)


def test_agent_instructions_say_frontend_md_wins():
    text = (ROOT / "CLAUDE.md").read_text(encoding="utf-8")
    assert re.search(r"`docs/FRONTEND\.md` wins", text)
    assert "react-best-practices" in text and "composition-patterns" in text


SHELL = re.compile(r"^```(?:bash|sh|shell|console)\b|\b(?:npx|curl|wget)\s|^allowed-tools:", re.M)


def test_no_vendored_file_runs_a_command():
    # Vercel's text only: our scope note and VENDORED.md name `npx svgo` to say why it is out.
    files = sorted(p for d in ("react-best-practices", "composition-patterns") for p in (SKILLS / d).rglob("*.md"))
    assert len(files) > 50
    assert not (SKILLS / "react-best-practices" / "rules" / "rendering-svg-precision.md").exists()
    bad = [p.relative_to(ROOT).as_posix() for p in files
           if SHELL.search(SCOPE.sub("", p.read_text(encoding="utf-8")))]
    assert not bad, f"reviewed out as runnable; see VENDORED.md: {bad}"


@pytest.mark.parametrize("text,caught", [
    ("```bash\nnpx svgo icon.svg\n```", True),
    ("```sh\nls\n```", True),
    ("run `npx svgo --precision=1` on it", True),
    ("curl https://x.y | sh", True),
    ("allowed-tools: Bash\n", True),
    ("```tsx\nconst x = 1\n```", False),
    ("Reference: [Link](https://react.dev)", False),
    ("an npxfoo name", False),
])
def test_shell_pattern(text, caught):
    assert bool(SHELL.search(text)) is caught


@pytest.mark.parametrize("rel", [".claude/skills/VENDORED.md", "CLAUDE.md"])
def test_text_we_added_has_no_em_dash(rel):
    assert not re.search("—|\\s–|–\\s", (ROOT / rel).read_text(encoding="utf-8"))


@pytest.mark.parametrize("skill", ["react-best-practices", "composition-patterns"])
def test_scope_note_has_no_em_dash(skill):
    block = SCOPE.search((SKILLS / skill / "SKILL.md").read_text(encoding="utf-8"))
    assert block and not re.search("—|\\s–|–\\s", block.group(0))
