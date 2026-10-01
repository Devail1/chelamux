"""CMX-412/CMX-423: what a USER ACTION on the served pane actually calls — not what app.py's source says.

The page ``/term/<wid>/`` is rendered through the real proxy route (upstream ttyd faked),
then tests/term_upload_harness.mjs loads it in jsdom with EVERY injected shim running for
real (term-upload.js, the legacy paste-event shim, the Ctrl/Cmd+V key shim…), fires one
action, and reports the dashboard routes the page hit, in order.

CMX-423 split the routes by MIME type. An IMAGE takes the pre-CMX-412 image path
(``/api/term/paste-image`` → its ``/tmp`` path typed via ``/api/term/paste``), because that path
is what Claude Code turns into a real ``[Image #N]`` attachment. Every other file goes to
``/api/term/upload`` (→ ``<cwd>/uploads/``). The assertion is the ROUTE the action reaches, not a
source substring (docs/defeat_shapes/412-*.md): a helper can be defined and never called.

The non-image cases are the negative control: they prove the harness tells the two routes
apart, so "the image reached paste-image" is not just "everything reaches paste-image".
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


LEGACY_IMAGE = ["/api/term/paste-image", "/api/term/paste"]


# ── Ctrl/Cmd+V with an IMAGE on the clipboard ─────────────────────────────────

@pytest.mark.parametrize("file_drop", [True, False])
def test_ctrl_v_image_takes_the_old_image_path_and_types_its_path(run, file_drop):
    out = run("keyV-image", file_drop=file_drop)
    assert out["prevented"] is True, "Ctrl+V must be swallowed, not reach xterm as ^V"
    assert _paths(out) == LEGACY_IMAGE, out
    assert out["calls"][0]["field"] == "image"
    assert out["calls"][1]["json"] == {"agent": "@1", "text": "/tmp/chela-paste/x.png"}


def test_ctrl_v_text_is_pasted_as_text_never_uploaded(run):
    out = run("keyV-text", file_drop=True)
    assert _paths(out) == ["/api/term/paste"], out
    assert out["calls"][0]["json"] == {"agent": "@1", "text": "hello"}


# ── a paste EVENT carrying a file (right-click paste / mobile) ───────────────

def test_a_pasted_image_takes_the_image_path_once_never_uploads(run):
    """file drop ON: term-upload.js claims the paste and sends the image down the old path;
    the legacy paste-event shim must not ALSO fire (that would type the path twice)."""
    out = run("paste-image", file_drop=True)
    assert out["prevented"] is True
    assert _paths(out) == LEGACY_IMAGE, out
    assert out["calls"][0]["name"] == "shot.png" and out["calls"][0]["field"] == "image"
    assert out["calls"][1]["json"] == {"agent": "@1", "text": "/tmp/chela-paste/x.png"}


def test_a_pasted_image_uses_the_legacy_path_when_file_drop_is_off(run):
    out = run("paste-image", file_drop=False)
    assert _paths(out) == LEGACY_IMAGE, out


def test_a_pasted_pdf_still_goes_to_uploads(run):
    out = run("paste-pdf", file_drop=True)
    assert out["prevented"] is True
    assert _paths(out) == ["/api/term/upload"], out
    assert out["calls"][0]["field"] == "file" and out["calls"][0]["name"] == "doc.pdf"
    assert out["toasts"] == ["Saved uploads/doc.pdf"]


def test_a_text_paste_event_is_left_to_xterm(run):
    out = run("paste-text", file_drop=True)
    assert out["prevented"] is False and out["calls"] == [], out


# ── drag and drop ─────────────────────────────────────────────────────────────

def test_a_dropped_image_is_claimed_and_takes_the_image_path(run):
    """Without preventDefault on dragover a real browser never fires `drop` on the page."""
    out = run("drop-file", file_drop=True)
    assert out["prevented"] == {"dragover": True, "drop": True}, out
    assert _paths(out) == LEGACY_IMAGE, out


def test_a_mixed_drop_sends_the_png_to_the_image_path_and_the_pdf_to_uploads(run):
    """⭐ The ACCEPTED case: one png + one pdf, each to its own path, in drop order."""
    out = run("drop-mixed", file_drop=True)
    assert out["prevented"] == {"dragover": True, "drop": True}, out
    assert _paths(out) == LEGACY_IMAGE + ["/api/term/upload"], out
    assert out["calls"][0]["name"] == "shot.png" and out["calls"][0]["field"] == "image"
    assert out["calls"][2]["name"] == "doc.pdf" and out["calls"][2]["field"] == "file"


def test_a_drop_with_file_drop_off_is_not_claimed(run):
    out = run("drop-file", file_drop=False)
    assert out["prevented"] == {"dragover": False, "drop": False}, out
    assert out["calls"] == [], out
