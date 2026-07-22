"""CMX-155: terminal glyphs stay tofu (fallback-font boxes) on macOS even though
the real webfont has finished loading. `_TERM_FONT_PREF_SHIM`
(chela/dashboard/app.py) is supposed to fix this by clearing xterm's texture
atlas once the webfont is ready — but xterm only caches ONE atlas per (terminal,
render-config) pair, and re-applying the SAME fontFamily/fontSize the terminal
already has is a cache HIT on the stale entry, not a rebuild. The reported bug:
for a viewer with no custom Settings > Terminal prefs (the common case), the
shim's target already equals what ttyd painted at mount — the old code's
`if (already matches) return;` guard fired on tick one, before the fix ever ran,
independent of whether `clearTextureAtlas` exists.

This runs the REAL shim source (not a reimplementation) inside Node, against a
miniature of xterm.js's own texture-atlas cache — `acquireTextureAtlas` /
`configEquals` from xterm@5.3.0's browser/renderer/shared/CharAtlasCache.ts +
CharAtlasUtils.ts (read directly from the published package while diagnosing
this). See tests/term_font_atlas_harness.mjs for the full model and citations.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from chela.dashboard.app import _TERM_FONT_PREF_SHIM

_HERE = Path(__file__).resolve().parent
_HARNESS = _HERE / "term_font_atlas_harness.mjs"
_ROOT = _HERE.parent


def _shim_js(tmp_path: Path) -> Path:
    assert _TERM_FONT_PREF_SHIM.startswith("<script>")
    assert _TERM_FONT_PREF_SHIM.endswith("</script>")
    js = _TERM_FONT_PREF_SHIM[len("<script>") : -len("</script>")]
    path = tmp_path / "term_font_pref_shim.js"
    path.write_text(js)
    return path


def _run(shim_path: Path, mode: str, prefs: dict) -> dict:
    node = shutil.which("node")
    if not node:
        pytest.skip("node not available for the terminal-font-atlas guard")
    proc = subprocess.run(
        [node, str(_HARNESS), str(shim_path), mode, json.dumps(prefs)],
        capture_output=True, timeout=30, cwd=str(_ROOT),
    )
    assert proc.returncode == 0, (
        f"harness failed:\n{proc.stdout.decode()}\n{proc.stderr.decode()}"
    )
    return json.loads(proc.stdout.decode())


def test_shim_is_valid_javascript(tmp_path):
    node = shutil.which("node")
    if not node:
        pytest.skip("node not available")
    shim_path = _shim_js(tmp_path)
    proc = subprocess.run([node, "--check", str(shim_path)], capture_output=True, timeout=10)
    assert proc.returncode == 0, proc.stderr.decode()


@pytest.mark.parametrize("clear_mode", ["no-clear", "with-clear"])
def test_atlas_rebuilds_with_default_prefs_and_no_prior_font_change(tmp_path, clear_mode):
    """The exact reported scenario: no custom Settings > Terminal prefs, so the
    shim's target fontFamily/fontSize already equal what ttyd painted at mount
    (before the webfont was ready). Must not stay tofu — regardless of whether
    this xterm build's clearTextureAtlas exists/works (`no-clear` reproduces the
    reported build; `with-clear` must keep working too)."""
    shim_path = _shim_js(tmp_path)
    result = _run(shim_path, clear_mode, {})
    assert result["tofu"] is False, result


def test_atlas_rebuilds_when_switching_to_a_custom_font_size(tmp_path):
    """A genuinely new target (a size never rendered before, e.g. a custom
    Settings > Terminal size) must also end up non-tofu, applied correctly."""
    shim_path = _shim_js(tmp_path)
    result = _run(shim_path, "no-clear", {"chela_term_fontsize": "16"})
    assert result["tofu"] is False, result
    assert result["fontSize"] == 16, result


def test_no_regression_guard_against_the_diagnosed_bug(tmp_path):
    """Directly reproduces CMX-155: feed the harness the shim source with the
    fix's atlas-eviction walk deleted (simulating a revert) and confirm the
    guard actually goes RED — proving test_atlas_rebuilds_with_default_prefs_*
    is not decoration."""
    shim_path = _shim_js(tmp_path)
    js = shim_path.read_text()
    assert "atlasFixed" in js, "expected the CMX-155 atlasFixed gate in the shim"
    # Corrupt: restore the old, provably-buggy gate that skips the atlas fix
    # whenever the target already matches, regardless of whether the one-time
    # walk ever ran.
    corrupted = js.replace(
        "if(atlasFixed&&getSize()===s&&getFam()===fam)return;",
        "if(getSize()===s&&getFam()===fam)return;",
    )
    assert corrupted != js, "corruption did not apply — guard text drifted"
    corrupted_path = tmp_path / "corrupted_shim.js"
    corrupted_path.write_text(corrupted)
    node = shutil.which("node")
    if not node:
        pytest.skip("node not available")
    check = subprocess.run([node, "--check", str(corrupted_path)], capture_output=True, timeout=10)
    assert check.returncode == 0, check.stderr.decode()
    result = _run(corrupted_path, "no-clear", {})
    assert result["tofu"] is True, (
        "corrupting the atlasFixed gate should reproduce the tofu bug — "
        f"got {result} instead, meaning the real guard doesn't actually test this"
    )
