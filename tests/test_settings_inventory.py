"""``docs/SETTINGS_UI_INVENTORY.md`` claims to enumerate every ``CHELA_*`` env var the
Python codebase reads (CMX-207). That claim rots the instant someone adds or removes a
knob and forgets the doc — the same drift ``docs/CONFIG.md``'s README table has hit twice
before. This re-runs the doc's own grep and diffs it against the doc's own table, so the
inventory is generated-and-checked rather than hand-maintained-and-trusted.
"""
from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
CHELA_PKG = REPO_ROOT / "chela"
INVENTORY_DOC = REPO_ROOT / "docs" / "SETTINGS_UI_INVENTORY.md"

# Mirrors the exact scan `docs/SETTINGS_UI_INVENTORY.md` documents as its methodology:
# literal os.environ.get("CHELA_…") / os.environ["CHELA_…"] / os.getenv("CHELA_…") reads.
_ENV_READ = re.compile(
    r"""os\.environ(?:\.get)?\(\s*["'](CHELA_[A-Z0-9_]+)["']"""
    r"""|os\.environ\[\s*["'](CHELA_[A-Z0-9_]+)["']\s*\]"""
    r"""|os\.getenv\(\s*["'](CHELA_[A-Z0-9_]+)["']"""
)

# First column of a doc table row: `| \`CHELA_FOO\` | ... |`. Prose mentions of a name
# elsewhere in the doc (e.g. the "adjacent, out of scope" call-outs) don't match — only an
# actual table entry counts as "documented".
_DOC_TABLE_ROW = re.compile(r"^\|\s*`(CHELA_[A-Z0-9_]+)`\s*\|", re.MULTILINE)


def _scan_env_reads() -> set[str]:
    names: set[str] = set()
    for path in CHELA_PKG.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        for match in _ENV_READ.finditer(text):
            names.add(next(g for g in match.groups() if g))
    return names


def _scan_documented_names() -> set[str]:
    text = INVENTORY_DOC.read_text(encoding="utf-8")
    return set(_DOC_TABLE_ROW.findall(text))


def test_inventory_matches_env_reads():
    code_names = _scan_env_reads()
    doc_names = _scan_documented_names()

    missing_from_doc = code_names - doc_names
    stale_in_doc = doc_names - code_names

    assert not missing_from_doc, (
        f"chela/ reads {sorted(missing_from_doc)} but "
        f"docs/SETTINGS_UI_INVENTORY.md's table doesn't list them — "
        "add a row (or, if it's a false positive, tighten the scan)."
    )
    assert not stale_in_doc, (
        f"docs/SETTINGS_UI_INVENTORY.md documents {sorted(stale_in_doc)} but chela/ no "
        "longer reads them from os.environ — remove the row or restore the read."
    )


def test_inventory_count_is_58():
    # The number CMX-207's ticket and the doc's own prose both cite. A change here without
    # a change to the doc's stated count is exactly the kind of drift this file exists to
    # catch — pinned as its own assertion so a future diff has to touch the prose too.
    assert len(_scan_env_reads()) == 58
