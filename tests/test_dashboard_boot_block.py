"""CMX-217: the page's boot config is ONE JSON block, ``#chela-boot``, read by the bundle
(``frontend/src/shared/api`` ``readBoot``). It replaced three inline globals, of which
``TERMINALS_ENABLED`` was only ever rendered as ``true``: a terminals-off deployment read as on.
Each flag must reach the page as rendered, both ways."""
from __future__ import annotations

import json
import re

import pytest

from chela.dashboard import app as dash

_BLOCK = re.compile(r'<script type="application/json" id="chela-boot">(.*?)</script>', re.S)


def _boot(monkeypatch, terminals: bool, wall_tile: bool, dispatch_off: bool = False) -> dict:
    monkeypatch.setattr(dash.config, "TERMINALS_ENABLED", terminals)
    monkeypatch.setattr(dash.config, "WALL_TILE_DISPATCHED", wall_tile)
    monkeypatch.setattr(dash.config, "DISPATCH_OFF_BY_CHOICE", dispatch_off)
    html = dash.app.test_client().get("/").get_data(as_text=True)
    blocks = _BLOCK.findall(html)
    assert len(blocks) == 1, f"expected one boot block, found {len(blocks)}"
    return json.loads(blocks[0])


@pytest.mark.parametrize("terminals,wall_tile", [(True, False), (False, True), (False, False), (True, True)])
def test_the_boot_block_carries_each_flag_as_configured(monkeypatch, terminals, wall_tile):
    assert _boot(monkeypatch, terminals, wall_tile) == {
        "assetVersion": dash.ASSET_VERSION,
        "terminalsEnabled": terminals,
        "wallTileDispatched": wall_tile,
        "dispatchOffByChoice": False,
    }


@pytest.mark.parametrize("dispatch_off", [True, False])
def test_the_boot_block_carries_dispatch_off_by_choice_both_ways(monkeypatch, dispatch_off):
    """CMX-388: CHELA_DISPATCH=off reaches the page as a real boolean, and so does its absence."""
    assert _boot(monkeypatch, True, False, dispatch_off)["dispatchOffByChoice"] is dispatch_off


def test_the_old_inline_globals_are_gone(monkeypatch):
    _boot(monkeypatch, True, True)
    html = dash.app.test_client().get("/").get_data(as_text=True)
    for name in ("window.CHELA_ASSET_VERSION", "window.TERMINALS_ENABLED", "window.WALL_TILE_DISPATCHED"):
        assert name not in html, f"{name} is rendered again beside the boot block"
