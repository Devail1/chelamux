"""CMX-412: what a USER ACTION on the served pane actually calls — not what app.py's source says.

The page ``/term/<wid>/`` is rendered through the real proxy route (upstream ttyd faked),
then tests/term_upload_harness.mjs loads it in jsdom with EVERY injected shim running for
real (term-upload.js, the legacy paste-event shim, the Ctrl/Cmd+V key shim…), fires one
action, and reports the dashboard routes the page hit, in order.

Why this exists: the Ctrl/Cmd+V key shim reads the clipboard itself and must hand an image
to term-upload.js (→ ``/api/term/upload`` → ``<cwd>/uploads/``). A source-substring check
(``"window.__chelaUpload" in _TERM_PASTE_KEY_SHIM``) stayed green when the call site was
pointed back at the legacy ``pasteImage`` (→ ``/tmp`` via ``/api/term/paste-image``), because
the helper that mentions ``__chelaUpload`` was still defined — just never called
(docs/defeat_shapes/412-*.md). Here the assertion is the ROUTE the keypress reaches.

Each action runs with the Settings switch ON and OFF: OFF must keep the legacy path, which
is the negative control proving the harness can tell the two routes apart.
"""
from __future__ import annotations

import io
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

import chela.dashboard.app as app_mod

_HERE = Path(__file__).resolve().parent
_ROOT = _HERE.parent
_HARNESS = _HERE / "term_upload_harness.mjs"


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
def run(monkeypatch, tmp_path):
    node = shutil.which("node")
    if not node or not (_ROOT / "node_modules" / "jsdom").is_dir():
        msg = "node/jsdom missing — the served-page upload harness DID NOT RUN (pnpm install)"
        if os.environ.get("CHELA_REQUIRE_JS_TESTS"):
            pytest.fail(msg)
        pytest.skip(msg)
    monkeypatch.setattr(app_mod, "_require_terminals", lambda: None)
    monkeypatch.setattr(app_mod, "_terminals_port_map", lambda: {"@1": 7681})
    page = b"<!doctype html><html><head><title>ttyd</title></head><body></body></html>"
    monkeypatch.setattr(app_mod.urllib.request, "urlopen",
                        lambda *a, **k: _FakeTtydResponse(page))

    def go(action: str, *, file_drop: bool) -> dict:
        monkeypatch.setenv("CHELA_FILE_DROP", "1" if file_drop else "0")
        resp = app_mod.app.test_client().get("/term/@1/")
        assert resp.status_code == 200
        html = tmp_path / f"served-{action}-{file_drop}.html"
        html.write_text(resp.get_data(as_text=True))
        env = {k: v for k, v in os.environ.items()
               if k not in ("NODE_CHANNEL_FD", "NODE_CHANNEL_SERIALIZATION_MODE")}
        proc = subprocess.run([node, str(_HARNESS), str(html), action], capture_output=True,
                              text=True, timeout=60, cwd=str(_ROOT), env=env)
        assert proc.returncode == 0, proc.stderr
        return json.loads(proc.stdout.strip().splitlines()[-1])
    return go


def _paths(out: dict) -> list[str]:
    return [c["path"] for c in out["calls"]]


# ── Ctrl/Cmd+V with an IMAGE on the clipboard ─────────────────────────────────

def test_ctrl_v_image_goes_to_term_upload_when_file_drop_is_on(run):
    out = run("keyV-image", file_drop=True)
    assert out["prevented"] is True, "Ctrl+V must be swallowed, not reach xterm as ^V"
    assert _paths(out) == ["/api/term/upload"], out
    up = out["calls"][0]
    assert up["agent"] == "@1" and up["field"] == "file"
    assert up["name"].startswith("paste-") and up["name"].endswith(".png")
    assert out["toasts"] == [f"Saved uploads/{up['name']}"]


def test_ctrl_v_image_keeps_the_legacy_tmp_path_when_file_drop_is_off(run):
    """Negative control: the harness sees the legacy route when it IS the right one."""
    out = run("keyV-image", file_drop=False)
    assert _paths(out) == ["/api/term/paste-image", "/api/term/paste"], out
    assert out["calls"][1]["json"] == {"agent": "@1", "text": "/tmp/chela-paste/x.png"}


def test_ctrl_v_text_is_pasted_as_text_never_uploaded(run):
    out = run("keyV-text", file_drop=True)
    assert _paths(out) == ["/api/term/paste"], out
    assert out["calls"][0]["json"] == {"agent": "@1", "text": "hello"}


# ── a paste EVENT carrying an image (right-click paste / mobile) ─────────────

def test_a_pasted_image_is_uploaded_once_and_the_legacy_shim_does_not_also_fire(run):
    out = run("paste-image", file_drop=True)
    assert out["prevented"] is True
    assert _paths(out) == ["/api/term/upload"], out
    assert out["calls"][0]["name"] == "shot.png"


def test_a_pasted_image_uses_the_legacy_path_when_file_drop_is_off(run):
    out = run("paste-image", file_drop=False)
    assert _paths(out) == ["/api/term/paste-image", "/api/term/paste"], out


def test_a_text_paste_event_is_left_to_xterm(run):
    out = run("paste-text", file_drop=True)
    assert out["prevented"] is False and out["calls"] == [], out


# ── drag and drop ─────────────────────────────────────────────────────────────

def test_a_dropped_file_is_uploaded_and_dragover_is_claimed(run):
    """Without preventDefault on dragover a real browser never fires `drop` on the page."""
    out = run("drop-file", file_drop=True)
    assert out["prevented"] == {"dragover": True, "drop": True}, out
    assert _paths(out) == ["/api/term/upload"], out


def test_a_drop_with_file_drop_off_is_not_claimed(run):
    out = run("drop-file", file_drop=False)
    assert out["prevented"] == {"dragover": False, "drop": False}, out
    assert out["calls"] == [], out
