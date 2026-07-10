"""The dashboard owner-presence surface (parent client + iframe shim) needs
``e2e.js`` and ``presence-core.js``, but it lives on the DASHBOARD origin — it
can't import them cross-origin from the relay Worker. So the two modules are
copied into ``chela/dashboard/static/collab/`` and MUST stay byte-identical to the
relay originals in ``chela/collab-relay/public/``.

The interop vector suite only exercises the relay copy (``public/e2e.js``), so
without this guard the dashboard copy could silently drift — a mismatched
``presenceKey``/envelope would let the owner and joiners derive different keys and
never see each other. This mirrors the e2e.py <-> e2e.js discipline: a plain file
diff, fast and dependency-free, that fails loudly on any drift.
"""

from __future__ import annotations

from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
_RELAY = _ROOT / "chela" / "collab-relay" / "public"
_DASH = _ROOT / "chela" / "dashboard" / "static" / "collab"

_SYNCED = ["e2e.js", "presence-core.js"]


@pytest.mark.parametrize("name", _SYNCED)
def test_dashboard_copy_is_byte_identical(name: str):
    src = (_RELAY / name).read_bytes()
    dst = (_DASH / name).read_bytes()
    assert src == dst, (
        f"{name}: dashboard copy has drifted from the relay original.\n"
        f"  relay:     {_RELAY / name}\n"
        f"  dashboard: {_DASH / name}\n"
        f"Re-copy: cp {_RELAY / name} {_DASH / name}"
    )
