"""Dashboard-editable user preferences, persisted to ``~/.chela/config.json``.

Distinct from :mod:`chela.config`, which reads env/CLI at process start and owns
trust-boundary settings (bind host, terminal exposure, notify URL). This holds
only non-security preferences the dashboard may write at runtime — currently the
launcher's projects directory. Keeping them separate is deliberate: the
dashboard is loopback + no-auth, so it must never be able to rewrite the security
boundary, only user conveniences.

Reads are best-effort (a missing/corrupt file reads as empty), so a hand-edited
or absent config never breaks the dashboard.
"""

from __future__ import annotations

import json

from chela import config

_PATH = config.CHELA_DIR / "config.json"


def _load() -> dict:
    try:
        data = json.loads(_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (FileNotFoundError, ValueError, OSError):
        return {}


def _save(data: dict) -> None:
    _PATH.parent.mkdir(parents=True, exist_ok=True)
    _PATH.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def get(key: str, default=None):
    return _load().get(key, default)


def set_(key: str, value) -> dict:
    """Set (or, when ``value`` is None/empty, clear) a key. Returns the new dict."""
    data = _load()
    if value in (None, ""):
        data.pop(key, None)
    else:
        data[key] = value
    _save(data)
    return data
