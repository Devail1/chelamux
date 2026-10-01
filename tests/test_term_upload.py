"""CMX-412: drop/paste a file on an OWNER's Wall pane → ``<cwd>/uploads/<name>`` + ``@uploads/<name> ``.

Everything runs through the real Flask route (test client) against a temp workspace; tmux is
faked at ``subprocess.run`` so the exact keystrokes are asserted, never a live pane. The
guards (each corrupt→RED, listed in the PR's self-check experiments):

* ⭐ the ACCEPTED case — a normal owner upload saves the bytes and types exactly
  ``@uploads/<name> `` with no Enter;
* traversal (``../x``, an absolute name, a dotfile, a symlinked ``uploads/``, a planted
  symlink at the target name) is refused and nothing lands outside ``uploads/``;
* a name collision gets ``-1`` and the original is untouched;
* over the size cap ⇒ refused, nothing written;
* a share-guest request ⇒ refused, whatever ``share_typing`` says.
"""
from __future__ import annotations

import io
import os

import pytest

import chela.dashboard.app as app_mod
from chela import event_log, uploads

RELAY = "wss://relay.example.workers.dev"


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
def ws(tmp_path, monkeypatch):
    """A temp session workspace on window @1, with tmux faked and every cap reset."""
    cwd = tmp_path / "proj"
    cwd.mkdir()
    calls: list[list[str]] = []

    def fake_run(argv, *a, **k):
        calls.append(list(argv))

        class _P:
            returncode = 0
            stdout = ""
            stderr = ""
        return _P()

    monkeypatch.setattr(app_mod, "_require_terminals", lambda: None)
    monkeypatch.setattr(app_mod, "_terminals_port_map", lambda: {"@1": 7681})
    monkeypatch.setattr(app_mod.discovery, "get_window_cwd_by_id",
                        lambda wid: str(cwd) if wid == "@1" else None)
    monkeypatch.setattr(app_mod.subprocess, "run", fake_run)
    monkeypatch.setattr(app_mod.config, "COLLAB_RELAY", RELAY)
    monkeypatch.delenv("CHELA_FILE_DROP", raising=False)
    monkeypatch.delenv("CHELA_UPLOAD_MAX_MB", raising=False)
    monkeypatch.delenv("CHELA_UPLOAD_PER_MINUTE", raising=False)
    app_mod._UPLOAD_TIMES.clear()
    return {"cwd": cwd, "calls": calls, "outside": tmp_path}


def _post(name="notes.txt", data=b"hello", wid="@1", headers=None):
    body = {"agent": wid, "file": (io.BytesIO(data), name)}
    return app_mod.app.test_client().post(
        "/api/term/upload", data=body, content_type="multipart/form-data",
        headers=headers or {"Sec-Fetch-Site": "same-origin"})


def _events(kind):
    return [e for e in event_log.read()["events"] if e.get("type") == kind]


# ── ⭐ the case that must be ACCEPTED ─────────────────────────────────────────

def test_owner_upload_saves_the_file_and_types_exactly_the_mention_without_enter(ws):
    r = _post("notes.txt", b"hello world")
    assert r.status_code == 200, r.get_json()
    j = r.get_json()
    assert j["ok"] is True and j["name"] == "notes.txt" and j["path"] == "uploads/notes.txt"
    assert (ws["cwd"] / "uploads" / "notes.txt").read_bytes() == b"hello world"
    assert ws["calls"] == [["tmux", "send-keys", "-t", f"{app_mod.TMUX_SESSION}:@1",
                            "-l", "@uploads/notes.txt "]]
    flat = [a for c in ws["calls"] for a in c]
    assert "Enter" not in flat and "C-m" not in flat
    ev = _events("upload.saved")
    assert ev and ev[-1]["payload"] == {"window": "@1", "name": "notes.txt", "size": 11, "typed": True}


def test_a_name_with_spaces_is_cleaned_so_the_mention_stays_one_token(ws):
    r = _post("my screen shot.png", b"x")
    assert r.status_code == 200
    assert r.get_json()["name"] == "my_screen_shot.png"
    assert ws["calls"][-1][-1] == "@uploads/my_screen_shot.png "


def test_a_failed_send_keys_still_saves_and_reports_typed_false(ws, monkeypatch):
    import subprocess

    def fail(argv, *a, **k):
        ws["calls"].append(list(argv))
        raise subprocess.CalledProcessError(1, argv)
    monkeypatch.setattr(app_mod.subprocess, "run", fail)
    r = _post("notes.txt", b"hi")
    assert r.status_code == 200
    j = r.get_json()
    assert j["ok"] is True and j["typed"] is False and j["path"] == "uploads/notes.txt"
    assert (ws["cwd"] / "uploads" / "notes.txt").read_bytes() == b"hi"
    assert _events("upload.saved")[-1]["payload"]["typed"] is False


# ── collisions ────────────────────────────────────────────────────────────────

def test_a_name_collision_gets_a_suffix_and_the_original_is_untouched(ws):
    up = ws["cwd"] / "uploads"
    up.mkdir()
    (up / "notes.txt").write_bytes(b"ORIGINAL")
    r = _post("notes.txt", b"new")
    assert r.status_code == 200
    assert r.get_json()["name"] == "notes-1.txt"
    assert (up / "notes.txt").read_bytes() == b"ORIGINAL"
    assert (up / "notes-1.txt").read_bytes() == b"new"
    assert ws["calls"][-1][-1] == "@uploads/notes-1.txt "
    assert _post("notes.txt", b"3rd").get_json()["name"] == "notes-2.txt"


# ── traversal ─────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("name", ["../x", "../../etc/passwd", "/etc/passwd", "a/b.txt",
                                  "a\\b.txt", "..", ".env", ".bashrc", "_.env", "###"])
def test_traversal_and_dotfile_names_are_refused_and_nothing_is_written(ws, name):
    r = _post(name, b"evil")
    assert r.status_code == 400, (name, r.get_json())
    assert r.get_json()["reason"] == "bad_name"
    assert ws["calls"] == []
    written = [p for p in ws["outside"].rglob("*")
               if p.is_file() and ".chela" not in p.relative_to(ws["outside"]).parts]
    assert written == [], written
    ev = _events("upload.refused")
    assert ev and ev[-1]["payload"]["reason"] == "bad_name"


def test_a_symlinked_uploads_dir_pointing_outside_is_refused(ws):
    elsewhere = ws["outside"] / "elsewhere"
    elsewhere.mkdir()
    os.symlink(elsewhere, ws["cwd"] / "uploads")
    r = _post("notes.txt", b"evil")
    assert r.status_code == 403, r.get_json()
    assert r.get_json()["reason"] == "outside_uploads"
    assert list(elsewhere.iterdir()) == []
    assert ws["calls"] == []


def test_a_symlinked_uploads_dir_is_refused_even_when_it_points_inside_the_workspace(ws):
    inner = ws["cwd"] / "data"
    inner.mkdir()
    os.symlink(inner, ws["cwd"] / "uploads")
    r = _post("notes.txt", b"x")
    assert r.status_code == 403
    assert list(inner.iterdir()) == []


def test_a_planted_symlink_at_the_target_name_is_not_written_through(ws):
    up = ws["cwd"] / "uploads"
    up.mkdir()
    victim = ws["outside"] / "victim.txt"
    os.symlink(victim, up / "notes.txt")
    r = _post("notes.txt", b"evil")
    assert r.status_code == 403, r.get_json()
    assert r.get_json()["reason"] == "outside_uploads"
    assert not victim.exists()
    assert sorted(p.name for p in up.iterdir()) == ["notes.txt"]


def test_contained_accepts_a_direct_child_only(tmp_path):
    up = tmp_path / "uploads"
    up.mkdir()
    assert uploads._contained(up / "a.txt", up) is True
    assert uploads._contained(up / ".." / "a.txt", up) is False
    assert uploads._contained(tmp_path / "a.txt", up) is False


def test_contained_refuses_a_nested_path_inside_uploads(tmp_path):
    """Direct child only: ``uploads/sub/a.txt`` is inside the tree but not a child of it."""
    up = tmp_path / "uploads"
    (up / "sub").mkdir(parents=True)
    assert uploads._contained(up / "sub" / "a.txt", up) is False


def test_the_write_itself_never_follows_a_planted_symlink(ws, monkeypatch):
    """O_NOFOLLOW is the race guard: a symlink planted AFTER the containment check (simulated
    by stubbing the check open) must still not be written through."""
    up = ws["cwd"] / "uploads"
    up.mkdir()
    victim = ws["outside"] / "victim.txt"
    victim.write_bytes(b"VICTIM")
    os.symlink(victim, up / "notes.txt")
    monkeypatch.setattr(uploads, "_contained", lambda p, u: True)
    r = _post("notes.txt", b"evil")
    assert victim.read_bytes() == b"VICTIM"
    assert r.status_code in (403, 500) or r.get_json()["name"] != "notes.txt", r.get_json()


def test_an_extensionless_collision_gets_a_plain_suffix(ws):
    up = ws["cwd"] / "uploads"
    up.mkdir()
    (up / "README").write_bytes(b"ORIGINAL")
    r = _post("README", b"new")
    assert r.get_json()["name"] == "README-1"
    assert (up / "README").read_bytes() == b"ORIGINAL"
    assert (up / "README-1").read_bytes() == b"new"


def test_a_long_name_is_truncated_and_keeps_its_extension(ws):
    r = _post("a" * 300 + ".png", b"x")
    assert r.status_code == 200
    name = r.get_json()["name"]
    assert uploads._MAX_NAME == 120
    assert len(name) == 120 and name.endswith(".png")
    assert (ws["cwd"] / "uploads" / name).read_bytes() == b"x"


def test_a_full_suffix_range_is_refused_not_overwritten(ws, monkeypatch):
    monkeypatch.setattr(uploads, "_MAX_SUFFIX", 2)
    up = ws["cwd"] / "uploads"
    up.mkdir()
    for n in ("a.txt", "a-1.txt", "a-2.txt"):
        (up / n).write_bytes(b"KEEP")
    r = _post("a.txt", b"new")
    assert r.status_code == 409 and r.get_json()["reason"] == "name_taken"
    assert all((up / n).read_bytes() == b"KEEP" for n in ("a.txt", "a-1.txt", "a-2.txt"))
    assert ws["calls"] == []


# ── size / rate caps ──────────────────────────────────────────────────────────

def test_over_the_size_cap_is_refused_and_nothing_is_written(ws, monkeypatch):
    monkeypatch.setenv("CHELA_UPLOAD_MAX_MB", "1")
    r = _post("big.bin", b"\0" * (1024 * 1024 + 10))
    assert r.status_code == 413, r.get_json()
    assert r.get_json()["reason"] == "too_large"
    up = ws["cwd"] / "uploads"
    assert not up.exists() or list(up.iterdir()) == []
    assert ws["calls"] == []
    # Negative control: exactly at the cap is accepted.
    assert _post("ok.bin", b"\0" * (1024 * 1024)).status_code == 200


def test_an_oversized_request_is_refused_before_the_body_is_read(ws, monkeypatch):
    """The Content-Length pre-check: refused without ever calling the writer."""
    monkeypatch.setenv("CHELA_UPLOAD_MAX_MB", "1")

    def boom(*a, **k):
        raise AssertionError("uploads.save must not run for an oversized request")
    monkeypatch.setattr(uploads, "save", boom)
    r = _post("big.bin", b"\0" * (1024 * 1024 + 128 * 1024))
    assert r.status_code == 413 and r.get_json()["reason"] == "too_large"
    assert ws["calls"] == []


def test_the_size_cap_defaults_to_25_mb(monkeypatch):
    monkeypatch.delenv("CHELA_UPLOAD_MAX_MB", raising=False)
    assert app_mod.config.upload_max_bytes() == 25 * 1024 * 1024


def test_the_per_minute_count_cap_refuses_the_next_upload(ws, monkeypatch):
    monkeypatch.setenv("CHELA_UPLOAD_PER_MINUTE", "2")
    assert _post("a.txt").status_code == 200
    assert _post("b.txt").status_code == 200
    r = _post("c.txt")
    assert r.status_code == 429 and r.get_json()["reason"] == "rate_limited"
    assert not (ws["cwd"] / "uploads" / "c.txt").exists()


# ── ⛔ share guests ───────────────────────────────────────────────────────────

@pytest.mark.parametrize("headers", [
    {"Origin": "https://relay.example.workers.dev"},
    {"Referer": "https://relay.example.workers.dev/j/room-tty"},
    {"Sec-Fetch-Site": "cross-site"},
])
def test_a_share_guest_request_is_refused_even_with_share_typing_on(ws, monkeypatch, headers):
    monkeypatch.setenv("CHELA_SHARE_TYPING", "1")
    r = _post("notes.txt", b"guest", headers=headers)
    assert r.status_code == 403, r.get_json()
    assert r.get_json()["reason"] == "share_guest"
    assert not (ws["cwd"] / "uploads").exists()
    assert ws["calls"] == []
    ev = _events("upload.refused")
    assert ev and ev[-1]["payload"]["reason"] == "share_guest"


def test_the_owners_same_origin_pane_is_not_mistaken_for_a_guest(ws):
    """Negative control for the guest check: the owner's iframe is same-origin."""
    r = _post("notes.txt", headers={"Origin": "https://dash.tailnet.example",
                                    "Sec-Fetch-Site": "same-origin"})
    assert r.status_code == 200, r.get_json()


# ── Settings switch + unknown window ──────────────────────────────────────────

def test_file_drop_switched_off_refuses(ws, monkeypatch):
    monkeypatch.setenv("CHELA_FILE_DROP", "0")
    r = _post("notes.txt")
    assert r.status_code == 403 and r.get_json()["reason"] == "disabled"
    assert not (ws["cwd"] / "uploads").exists()


def test_an_unknown_window_is_refused(ws):
    r = _post("notes.txt", wid="@99")
    assert r.status_code == 404 and r.get_json()["reason"] == "unknown_window"


def test_config_reports_file_drop_on_by_default_and_saves_a_toggle(monkeypatch):
    monkeypatch.delenv("CHELA_FILE_DROP", raising=False)
    c = app_mod.app.test_client()
    j = c.get("/api/config").get_json()
    assert j["file_drop"] is True and j["file_drop_source"] == "default"
    assert j["upload_max_mb"] == 25
    try:
        j = c.post("/api/config", json={"file_drop": False}).get_json()
        assert j["file_drop"] is False and j["file_drop_source"] == "dashboard"
        assert app_mod.config.file_drop_enabled() is False
        assert c.post("/api/config", json={"file_drop": "maybe"}).status_code == 400
    finally:
        c.post("/api/config", json={"file_drop": None})
    assert app_mod.config.file_drop_setting() == (True, "default")


# ── the shim is actually served ───────────────────────────────────────────────

def _served(monkeypatch) -> str:
    monkeypatch.setattr(app_mod, "_require_terminals", lambda: None)
    monkeypatch.setattr(app_mod, "_terminals_port_map", lambda: {"@1": 7681})
    page = b"<!doctype html><html><head><title>ttyd</title></head><body></body></html>"
    monkeypatch.setattr(app_mod.urllib.request, "urlopen",
                        lambda *a, **k: _FakeTtydResponse(page))
    return app_mod.app.test_client().get("/term/@1/").get_data(as_text=True)


def test_the_served_pane_loads_the_upload_shim_before_the_legacy_paste_shim(monkeypatch):
    monkeypatch.delenv("CHELA_FILE_DROP", raising=False)
    html = _served(monkeypatch)
    head = html.split("</head>", 1)[0]
    tag = '<script src="/static/term-upload.js"></script>'
    assert head.count(tag) == 1
    assert "window.__CHELA_FILE_DROP__=true;" in head
    assert head.index(tag) < head.index(app_mod._TERM_PASTE_SHIM)
    assert "window.__chelaUpload" in app_mod._TERM_PASTE_KEY_SHIM


def test_the_served_pane_carries_the_switch_off(monkeypatch):
    monkeypatch.setenv("CHELA_FILE_DROP", "off")
    assert "window.__CHELA_FILE_DROP__=false;" in _served(monkeypatch)
