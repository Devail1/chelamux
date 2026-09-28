"""CMX-381: a dashboard theme must reach the TERMINALS, not stop at the chrome.

Before this, ttyd painted the one xterm theme it was launched with
(scripts/agent-terminals.sh TERM_THEME) whatever the viewer picked, so e.g.
Gruvbox chrome framed GitHub-dark terminals. Now every ttyd page `/term/<wid>/`
serves carries app.py's `_term_theme_shim`, which applies the palette for the
viewer's `chela_theme` (term_themes.py) to `window.term` live.

These tests take the shim out of the page the proxy ACTUALLY serves (Flask test
client, upstream ttyd faked) and run it in Node against a fake ttyd page — see
tests/term_theme_harness.mjs for the lifecycle it replays.
"""
from __future__ import annotations

import io
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

import chela.dashboard.app as app_mod
from chela.dashboard import term_themes
from chela.dashboard.term_themes import TERM_THEMES

_HERE = Path(__file__).resolve().parent
_HARNESS = _HERE / "term_theme_harness.mjs"
_ROOT = _HERE.parent
_SH = _ROOT / "scripts" / "agent-terminals.sh"

DARK = TERM_THEMES["dark"]["xterm"]


@pytest.fixture(autouse=True)
def _no_operator_override(monkeypatch):
    """An operator's real CHELA_TERM_THEME (chela.env) must not leak into these."""
    monkeypatch.delenv("CHELA_TERM_THEME", raising=False)


class _FakeTtydResponse:
    status = 200

    def __init__(self, body: bytes):
        self._body = io.BytesIO(body)
        self.headers = {"Content-Type": "text/html; charset=utf-8"}

    def read(self):
        return self._body.read()

    def getheaders(self):
        return list(self.headers.items())


@pytest.fixture
def served_page(monkeypatch):
    """GET /term/@1/ through the real proxy route, upstream ttyd faked."""
    monkeypatch.setattr(app_mod, "_require_terminals", lambda: None)
    monkeypatch.setattr(app_mod, "_terminals_port_map", lambda: {"@1": 7681})
    page = b"<!doctype html><html><head><title>ttyd</title></head><body></body></html>"
    monkeypatch.setattr(app_mod.urllib.request, "urlopen",
                        lambda *a, **k: _FakeTtydResponse(page))

    def get() -> str:
        resp = app_mod.app.test_client().get("/term/@1/")
        assert resp.status_code == 200
        return resp.get_data(as_text=True)
    return get


def _theme_shim_js(html: str) -> str:
    scripts = re.findall(r"<script>(.*?)</script>", html, re.S)
    mine = [s for s in scripts if "chelaApplyTermTheme" in s]
    assert len(mine) == 1, "the served ttyd page must carry exactly one terminal-theme shim"
    head = html.split("</head>", 1)[0]
    assert mine[0] in head, "the theme shim must be in <head> (runs before xterm mounts)"
    return mine[0]


def _run(tmp_path: Path, html: str, scenario: dict) -> dict:
    node = shutil.which("node")
    if not node:
        pytest.skip("node not available for the terminal-theme shim guard")
    shim = tmp_path / "term_theme_shim.js"
    shim.write_text(_theme_shim_js(html))
    scenario = {"launch": DARK, **scenario}
    proc = subprocess.run(
        [node, str(_HARNESS), str(shim), json.dumps(scenario)],
        capture_output=True, timeout=30, cwd=str(_ROOT),
    )
    assert proc.returncode == 0, f"harness failed:\n{proc.stdout.decode()}\n{proc.stderr.decode()}"
    return json.loads(proc.stdout.decode())


def test_new_pane_comes_up_in_the_current_theme(served_page, tmp_path):
    """A freshly opened pane must paint the viewer's theme, not the launch default —
    both the page behind the canvas (set before xterm exists) and xterm itself the
    moment ttyd assigns window.term."""
    gruv = TERM_THEMES["gruvbox"]
    out = _run(tmp_path, served_page(), {"store": {"chela_theme": "gruvbox"}})
    assert out["head"]["--chela-term-bg"] == gruv["xterm"]["background"]
    assert out["head"]["--chela-sb"] == gruv["scrollbar"]
    assert out["head"]["--chela-sb-hover"] == gruv["scrollbarHover"]
    assert out["onAssign"] == gruv["xterm"]
    assert out["sameTermBack"] is True


def test_theme_survives_ttyd_reapplying_its_launch_theme(served_page, tmp_path):
    """ttyd re-applies its launch client-options (theme included) on WS connect;
    within one frame the shim must put the viewer's palette back."""
    out = _run(tmp_path, served_page(), {"store": {"chela_theme": "warm"}})
    assert out["afterReapply"] == TERM_THEMES["warm"]["xterm"]


def test_live_switch_via_parent_poke_and_storage_event(served_page, tmp_path):
    out = _run(tmp_path, served_page(), {
        "store": {"chela_theme": "dark"}, "liveTo": "nord", "storageTo": "rose",
    })
    assert out["afterLive"] == TERM_THEMES["nord"]["xterm"]
    assert out["cssAfterLive"]["--chela-term-bg"] == TERM_THEMES["nord"]["xterm"]["background"]
    assert out["cssAfterLive"]["--chela-sb"] == TERM_THEMES["nord"]["scrollbar"]
    assert out["afterStorage"] == TERM_THEMES["rose"]["xterm"]


def test_unset_or_unknown_theme_falls_back_to_dark(served_page, tmp_path):
    out = _run(tmp_path, served_page(), {"store": {"chela_theme": "no-such-theme"}})
    assert out["onAssign"] == DARK
    out = _run(tmp_path, served_page(), {"store": {}})
    assert out["onAssign"] == DARK


def test_chela_term_theme_env_overrides_every_theme(served_page, tmp_path, monkeypatch):
    """CHELA_TERM_THEME is the operator's explicit override: it wins over the
    viewer's picked theme, on mount AND on a live switch."""
    custom = dict(DARK, background="#101010", foreground="#fafafa", red="#ff0000")
    monkeypatch.setenv("CHELA_TERM_THEME", json.dumps(custom))
    out = _run(tmp_path, served_page(), {"store": {"chela_theme": "gruvbox"}, "liveTo": "warm"})
    assert out["onAssign"] == custom
    assert out["afterReapply"] == custom
    assert out["afterLive"] == custom
    assert out["head"]["--chela-term-bg"] == "#101010"


@pytest.mark.parametrize("raw", ["", "   ", "not json", "[1,2]", "{}"])
def test_invalid_override_is_ignored(monkeypatch, raw):
    monkeypatch.setenv("CHELA_TERM_THEME", raw)
    assert term_themes.operator_override() is None


def test_agent_terminals_still_honours_chela_term_theme():
    """The launch-side half of the override: ttyd's own theme comes from the env."""
    sh = _SH.read_text()
    assert 'TERM_THEME="${CHELA_TERM_THEME:-$TERM_THEME}"' in sh
    assert "--client-option" in sh and 'theme=${TERM_THEME}' in sh


def test_theme_shim_is_valid_javascript(served_page, tmp_path):
    node = shutil.which("node")
    if not node:
        pytest.skip("node not available")
    shim = tmp_path / "s.js"
    shim.write_text(_theme_shim_js(served_page()))
    proc = subprocess.run([node, "--check", str(shim)], capture_output=True, timeout=10)
    assert proc.returncode == 0, proc.stderr.decode()
