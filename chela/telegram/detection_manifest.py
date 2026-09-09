"""Loads the TOML pattern manifest that drives ``panescan.py``'s TUI detectors.

Issue #458: ``panescan.py`` used to hardcode every Claude-Code-TUI regex, so a chrome
change (a reworded prompt, a new banner) cost a code change + PR + judge round +
release + deploy to fix. This module is the **data out** half of that split — the
top/bottom marker regexes and the status-line glyph set now live in
:mod:`detection_manifest.toml` (or an operator override), while the scanning
*algorithm* in ``panescan.py`` stays code.

Resolution order, evaluated fresh on every :func:`load` call — highest wins:

1. ``$CHELA_DIR/agent-detection/claude-code.toml`` (:func:`override_manifest_path`) —
   a **complete** replacement manifest, not a merge. Reread whenever its mtime
   changes, so an operator's edit takes effect on the next pane scan with no
   ``chela-telegram`` restart.
2. The bundled default shipped next to this module (:data:`BUNDLED_MANIFEST_PATH`).

A missing override is the normal case (bundled applies). A *present but
malformed/unreadable* override is logged loudly and the bundled manifest is used
instead — detection must never silently degrade to "no patterns" (the CMX-337 /
issue #434 incident: a fail-closed scan returned ``None`` with no error at all).
"""
from __future__ import annotations

import logging
import re
import tomllib
from dataclasses import dataclass
from pathlib import Path

from chela import config

log = logging.getLogger(__name__)

_HERE = Path(__file__).resolve().parent

#: The manifest shipped in the package — always valid, or the package is broken.
BUNDLED_MANIFEST_PATH = _HERE / "detection_manifest.toml"


def override_manifest_path() -> Path:
    """``$CHELA_DIR/agent-detection/claude-code.toml`` — read fresh, not cached, so
    tests (and an operator changing ``CHELA_DIR``) see the current value."""
    return config.CHELA_DIR / "agent-detection" / "claude-code.toml"


class ManifestError(ValueError):
    """A manifest file is missing a required key or has an invalid value/regex."""


@dataclass(frozen=True)
class PatternSpec:
    """One top/bottom marker pair — the manifest's unit, mirrors ``panescan``'s
    former ``_UIPattern``. ``tables`` says which detector table(s) this entry feeds:
    ``"gate"`` (:func:`panescan.detect_permission_gate`) and/or ``"dialog"``
    (:func:`panescan.detect_dialog`)."""

    name: str
    top: tuple[re.Pattern[str], ...]
    bottom: tuple[re.Pattern[str], ...]
    min_gap: int
    tables: frozenset[str]


@dataclass(frozen=True)
class Manifest:
    """A fully resolved manifest: the ordered pattern list plus the status-line
    glyph set. ``gate_patterns``/``dialog_patterns`` filter :attr:`patterns` by
    table membership, preserving declaration order — the same list backs both,
    so a pattern tagged for both tables can never drift between them."""

    patterns: tuple[PatternSpec, ...]
    spinners: frozenset[str]
    active_marker: str
    source: Path

    @property
    def gate_patterns(self) -> tuple[PatternSpec, ...]:
        return tuple(p for p in self.patterns if "gate" in p.tables)

    @property
    def dialog_patterns(self) -> tuple[PatternSpec, ...]:
        return tuple(p for p in self.patterns if "dialog" in p.tables)

    def pattern(self, name: str) -> PatternSpec | None:
        """The first pattern with this ``name`` (declaration order), or ``None``."""
        return next((p for p in self.patterns if p.name == name), None)


def _compile_all(raw: object, *, pattern_name: str, field: str) -> tuple[re.Pattern[str], ...]:
    if not isinstance(raw, list):
        raise ManifestError(f"pattern {pattern_name!r}: {field!r} must be a list of strings")
    try:
        return tuple(re.compile(p) for p in raw)
    except re.error as e:
        raise ManifestError(f"pattern {pattern_name!r}: bad regex in {field!r}: {e}") from e


def _parse(raw: dict, source: Path) -> Manifest:
    try:
        status = raw["status"]
        spinner_list = status["spinners"]
        active_marker = status["active_marker"]
        entries = raw["pattern"]
    except KeyError as e:
        raise ManifestError(f"{source}: missing required key {e}") from e

    if not isinstance(spinner_list, list) or not spinner_list:
        raise ManifestError(f"{source}: status.spinners must be a non-empty list")
    spinners = frozenset(spinner_list)
    if not isinstance(active_marker, str) or not active_marker:
        raise ManifestError(f"{source}: status.active_marker must be a non-empty string")
    if not isinstance(entries, list) or not entries:
        raise ManifestError(f"{source}: at least one [[pattern]] entry is required")

    patterns: list[PatternSpec] = []
    for i, entry in enumerate(entries):
        try:
            name = entry["name"]
            tables = frozenset(entry["tables"])
        except KeyError as e:
            raise ManifestError(f"{source}: pattern[{i}] missing required key {e}") from e
        if not isinstance(name, str) or not name:
            raise ManifestError(f"{source}: pattern[{i}].name must be a non-empty string")
        if not tables or not tables.issubset({"gate", "dialog"}):
            raise ManifestError(
                f"{source}: pattern[{i}] {name!r}.tables must be a non-empty subset of "
                f'["gate", "dialog"], got {sorted(entry.get("tables", []))}'
            )
        top = _compile_all(entry.get("top", []), pattern_name=name, field="top")
        if not top:
            raise ManifestError(f"{source}: pattern[{i}] {name!r} has no top markers")
        bottom = _compile_all(entry.get("bottom", []), pattern_name=name, field="bottom")
        min_gap = entry.get("min_gap", 2)
        if not isinstance(min_gap, int) or isinstance(min_gap, bool) or min_gap < 0:
            raise ManifestError(f"{source}: pattern[{i}] {name!r}.min_gap must be a non-negative integer")
        patterns.append(PatternSpec(name=name, top=top, bottom=bottom, min_gap=min_gap, tables=tables))

    return Manifest(patterns=tuple(patterns), spinners=spinners, active_marker=active_marker, source=source)


def load_file(path: Path) -> Manifest:
    """Parse and validate one manifest file. Raises :class:`ManifestError` on a bad
    schema/regex, ``OSError``/``UnicodeDecodeError`` if it cannot be read, and
    ``tomllib.TOMLDecodeError`` (a ``ValueError`` subclass) on bad TOML syntax."""
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    return _parse(data, path)


def load_bundled() -> Manifest:
    """The manifest shipped with the package. Not expected to ever fail — a failure
    here is a packaging bug, not an operator error, and is left to propagate."""
    return load_file(BUNDLED_MANIFEST_PATH)


# (override path, its mtime-or-None, resolved Manifest) — the whole cache is this one
# slot, invalidated the instant the override PATH (e.g. ``CHELA_DIR`` changed — every
# test gets its own) or its mtime (or presence) changes.
_cache: tuple[Path, float | None, Manifest] | None = None


def load() -> Manifest:
    """The active manifest: local override if present and valid, else bundled.

    Cheap on the hot path (one ``stat()``) and self-invalidating: an operator
    editing/adding/removing the override file is picked up on the very next call,
    with no ``chela-telegram`` restart. A present override that fails to parse is
    logged at ERROR and the bundled manifest is served instead of raising — a
    dialog is always classifiable, never a silent ``None`` (issue #434).
    """
    global _cache

    override = override_manifest_path()
    try:
        mtime: float | None = override.stat().st_mtime if override.is_file() else None
    except OSError:
        mtime = None

    if _cache is not None and _cache[0] == override and _cache[1] == mtime:
        return _cache[2]

    if mtime is not None:
        try:
            manifest = load_file(override)
            log.info("agent-detection: loaded override manifest %s", override)
        except (OSError, UnicodeDecodeError, ValueError) as e:
            log.error(
                "agent-detection: override manifest %s is unreadable or malformed (%s) — "
                "falling back to the bundled default",
                override,
                e,
            )
            manifest = load_bundled()
    else:
        manifest = load_bundled()

    _cache = (override, mtime, manifest)
    return manifest


def _reset_cache_for_tests() -> None:
    """Test-only: forget the cached manifest so the next :func:`load` re-stats and
    re-parses, even if the override's mtime happens to collide with the cached one
    (a scratch dir's file can be rewritten twice within the same mtime tick)."""
    global _cache
    _cache = None
