"""Parse a project's BACKLOG.md into ``(section, text)`` items.

BACKLOG.md sits next to WORKFLOW.md in a project repo and holds plain
markdown bullets that aren't dispatch-ready. The Kanban surfaces these in a
read-only leftmost column so they're visible without being claimable; the
``markdown`` tracker source only reads TODO.md, so backlog items can never be
auto-picked.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

# A markdown bullet: `- something`. Excludes checkbox bullets (`- [ ]` /
# `- [x]`), which live in TODO.md.
_BULLET_RE = re.compile(r"^\s*-\s+(?!\[[ xX]\])(.+?)\s*$")
_SECTION_RE = re.compile(r"^\s*##\s+(.+?)\s*$")


@dataclass
class BacklogItem:
    section: str | None
    text: str


def parse_backlog(path: Path) -> list[BacklogItem]:
    """Return all non-checkbox bullets from ``path``, tagged with their ``##`` section."""
    if not path.exists():
        return []
    items: list[BacklogItem] = []
    section: str | None = None
    for raw in path.read_text().splitlines():
        sm = _SECTION_RE.match(raw)
        if sm:
            section = sm.group(1).strip() or None
            continue
        bm = _BULLET_RE.match(raw)
        if not bm:
            continue
        text = bm.group(1).strip()
        if not text:
            continue
        items.append(BacklogItem(section=section, text=text))
    return items
