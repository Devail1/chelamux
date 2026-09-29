"""CMX-402 — the README, the landing page and their demo media are PUBLIC.

Three guards:

* the README and landing pages must not describe a dashboard view that no longer
  exists (the nav is Wall + Work; Feed, Knowledge, Personas and Cost were removed);
* nothing under ``docs/img/`` or ``landing/`` may carry the operator's user name, a
  private window name, or a ``/home/`` path — checked on the raw BYTES of every file,
  so a path baked into a GIF comment or MP4 metadata is caught as well as one in HTML;
* the demo recorder (``scripts/demo/fleet.py``) must build its fleet on its own tmux
  server (``tmux -L``) with a temp ``HOME`` + ``CHELA_DIR`` and a from-scratch env —
  the property that keeps the operator's real fleet out of the recordings.
"""
from __future__ import annotations

import importlib.util
import os
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
PUBLIC_PAGES = [ROOT / "README.md", ROOT / "landing" / "index.html", ROOT / "landing" / "docs.html"]

# Case-sensitive on purpose: these are view NAMES. "zero-knowledge relay" and
# "feed" as a verb are ordinary words, not a nav view.
REMOVED_VIEWS = ["Feed", "Knowledge", "Personas", "Cost"]

PRIVATE_NEEDLES = [b"liavedunix", b"tradeplan", b"/home/"]


def _text(path: Path) -> str:
    """The page's prose: HTML tags and comments dropped, so an attribute or a
    comment cannot hide or fake a match."""
    raw = path.read_text(encoding="utf-8")
    raw = re.sub(r"<!--.*?-->", " ", raw, flags=re.S)
    return re.sub(r"<[^>]+>", " ", raw)


@pytest.mark.parametrize("page", PUBLIC_PAGES, ids=lambda p: str(p.relative_to(ROOT)))
def test_public_pages_name_no_removed_view(page):
    text = _text(page)
    hits = [v for v in REMOVED_VIEWS if re.search(rf"\b{v}\b", text)]
    assert not hits, f"{page.relative_to(ROOT)} still names removed view(s): {hits}"


def _public_files() -> list[Path]:
    files = [p for d in ("docs/img", "landing") for p in (ROOT / d).rglob("*") if p.is_file()]
    assert files, "no files found under docs/img or landing — the guard would check nothing"
    return files


def test_public_media_and_pages_carry_no_private_strings():
    leaks = []
    for path in _public_files():
        data = path.read_bytes()
        leaks += [f"{path.relative_to(ROOT)}: {n.decode()}" for n in PRIVATE_NEEDLES if n in data]
    assert not leaks, "private strings in public files:\n" + "\n".join(leaks)


def test_demo_media_exists_at_the_published_paths():
    for rel in ("docs/img/chela-demo-desktop.gif", "docs/img/chela-demo-mobile.gif",
                "landing/chela-demo-desktop.mp4", "landing/chela-demo-mobile.mp4"):
        p = ROOT / rel
        assert p.is_file(), rel
        if p.suffix == ".gif":
            assert p.stat().st_size <= 8 * 1024 * 1024, f"{rel} is over the 8 MB README budget"


# --- the recorder's isolation ------------------------------------------------

def _fleet():
    spec = importlib.util.spec_from_file_location("chela_demo_fleet", ROOT / "scripts" / "demo" / "fleet.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_demo_fleet_tmux_shim_pins_its_own_server(tmp_path):
    fleet = _fleet()
    fleet.write_shims(tmp_path)
    shim = (tmp_path / "bin" / "tmux").read_text()
    assert f"-L {fleet.TMUX_SOCKET}" in shim
    assert fleet.TMUX_SOCKET and fleet.TMUX_SOCKET != "default"


def test_demo_fleet_env_is_temp_and_from_scratch(tmp_path, monkeypatch):
    monkeypatch.setenv("TMUX", "/tmp/tmux-1000/default,1,0")
    monkeypatch.setenv("TMUX_PANE", "%1")
    monkeypatch.setenv("CHELA_SECRET_PROBE", "must-not-leak")
    fleet = _fleet()
    env = fleet.demo_env(tmp_path, 5999, 6400)

    for key in ("CHELA_DIR", "HOME", "CHELA_DEMO_STATUS_DIR", "PYTHONPATH"):
        assert Path(env[key]).is_relative_to(tmp_path), f"{key}={env[key]} escapes the demo root"
    real_chela = Path(os.path.expanduser("~")) / ".chela"
    assert Path(env["CHELA_DIR"]) != real_chela
    # The shim dir comes first, so every `tmux` the demo runs is the pinned one.
    assert env["PATH"].split(":")[0] == str(tmp_path / "bin")
    assert "TMUX" not in env and "TMUX_PANE" not in env
    assert "CHELA_SECRET_PROBE" not in env


def test_demo_fleet_root_is_a_fresh_temp_dir():
    fleet = _fleet()
    root = fleet.make_root()
    try:
        assert root.name.startswith("chela-demo-")
        assert str(root).startswith("/tmp/")
    finally:
        root.rmdir()
