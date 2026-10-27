"""CMX-363: what `uvx chelamux` installs is a wheel built from the sdist, never a checkout.

So everything chela reads at runtime from its own package directory has to be inside that
wheel: the committed dashboard bundle (`chela/dashboard/static/app/**`), the ttyd page
shims, the templates, the merge-gate script the plugin ships (`chela/mergegate.py`), and the
statusLine hook (`scripts/cache-statusline.sh`, force-included as `chela/scripts/`). A file
missing from it only shows up on an adopter's machine, so this builds both artifacts the
way the release does (`uv build`: sdist first, then the wheel FROM the sdist) and reads them.
"""
from __future__ import annotations

import argparse
import email
import stat
import subprocess
import tarfile
import zipfile
from pathlib import Path

import pytest

from chela import main

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(scope="module")
def dist(tmp_path_factory) -> Path:
    out = tmp_path_factory.mktemp("dist")
    result = subprocess.run(["uv", "build", "--out-dir", str(out), str(ROOT)],
                            capture_output=True, text=True, timeout=600)
    assert result.returncode == 0, result.stderr
    return out


@pytest.fixture(scope="module")
def wheel(dist) -> zipfile.ZipFile:
    (path,) = dist.glob("chelamux-*-py3-none-any.whl")
    return zipfile.ZipFile(path)


@pytest.fixture(scope="module")
def sdist_names(dist) -> list[str]:
    (path,) = dist.glob("chelamux-*.tar.gz")
    with tarfile.open(path) as tar:
        # Drop the `chelamux-<version>/` root every member sits under.
        return [n.split("/", 1)[1] for n in tar.getnames() if "/" in n]


def _tracked(*paths: str) -> list[str]:
    out = subprocess.run(["git", "ls-files", "--", *paths], cwd=ROOT, capture_output=True,
                         text=True, timeout=60, check=True).stdout.split()
    assert out, f"git ls-files found nothing under {paths} — cannot check the wheel against it"
    return out


def _dist_info(wheel: zipfile.ZipFile, name: str) -> str:
    (member,) = [n for n in wheel.namelist() if n.endswith(f".dist-info/{name}")]
    return wheel.read(member).decode()


def test_the_wheel_carries_every_tracked_runtime_file(wheel):
    """Every git-tracked file under `chela/` except the React source (its build output,
    `static/app`, is what ships). This is the dashboard bundle, the ttyd shims, the
    templates, the fonts and the plugin's merge-gate script in one assertion."""
    expected = {p for p in _tracked("chela") if not p.startswith("chela/dashboard/frontend/")}
    missing = sorted(expected - set(wheel.namelist()))
    assert not missing, f"tracked files missing from the wheel: {missing[:20]}"


@pytest.mark.parametrize("path", [
    "chela/dashboard/static/app/manifest.json",   # the committed dashboard bundle
    "chela/dashboard/templates/index.html",
    "chela/dashboard/static/term-reconnect.js",   # ttyd page shims
    "chela/dashboard/static/term-upload.js",
    "chela/dashboard/static/term-touch.css",
    "chela/mergegate.py",                         # copied into the plugin's hooks/
    "chela/scripts/cache-statusline.sh",          # the statusLine hook
])
def test_the_wheel_carries_the_named_runtime_files(wheel, path):
    """The same claim with literal paths, so an empty `git ls-files` can't make the
    whole-tree check above vacuous."""
    assert path in wheel.namelist()


def test_the_dashboard_bundle_files_its_manifest_names_are_in_the_wheel(wheel):
    """index.html loads the hashed assets the manifest names; a missing one is a blank wall."""
    names = set(wheel.namelist())
    manifest = wheel.read("chela/dashboard/static/app/manifest.json").decode()
    assets = [p for p in _tracked("chela/dashboard/static/app/assets")
              if Path(p).name in manifest]
    assert assets, "the manifest names none of the tracked bundle assets"
    assert all(a in names for a in assets)


def test_the_statusline_hook_is_the_scripts_copy_and_executable(wheel):
    """Claude Code runs the statusLine `command` directly, so it must keep its +x bit."""
    info = wheel.getinfo("chela/scripts/cache-statusline.sh")
    assert stat.S_IMODE(info.external_attr >> 16) & stat.S_IXUSR
    assert wheel.read(info) == (ROOT / "scripts" / "cache-statusline.sh").read_bytes()


def test_both_console_scripts_run_main(wheel):
    entry_points = _dist_info(wheel, "entry_points.txt")
    assert "chela = chela.main:main" in entry_points.splitlines()
    assert "chelamux = chela.main:main" in entry_points.splitlines()


@pytest.mark.parametrize("requirement", ["flask>=3.0", "flask-sock>=0.7", "cryptography>=42"])
def test_the_dashboard_needs_no_extra(wheel, requirement):
    """`uvx chelamux dashboard` names no extra, so Flask & co. must be unconditional."""
    metadata = email.message_from_string(_dist_info(wheel, "METADATA"))
    assert requirement in (metadata.get_all("Requires-Dist") or [])


def test_the_dashboard_extra_still_resolves(wheel):
    """Existing `chelamux[dashboard]` install lines keep working."""
    metadata = email.message_from_string(_dist_info(wheel, "METADATA"))
    assert "dashboard" in (metadata.get_all("Provides-Extra") or [])


def test_no_node_modules_or_react_source_in_the_wheel(wheel):
    names = wheel.namelist()
    assert not [n for n in names if "node_modules" in n]
    assert not [n for n in names if n.startswith("chela/dashboard/frontend/")]


def test_the_sdist_holds_what_the_wheel_is_built_from_and_no_node_modules(sdist_names):
    assert "scripts/cache-statusline.sh" in sdist_names
    assert "chela/dashboard/static/app/manifest.json" in sdist_names
    assert "README.md" in sdist_names
    assert not [n for n in sdist_names if "node_modules" in n]


# --- install-statusline finds the packaged copy ------------------------------------------

def _fake_layout(tmp_path: Path, packaged: bool) -> Path:
    """`<tmp>/chela/main.py` (+ `chela/scripts/` when packaged) and `<tmp>/scripts/`."""
    (tmp_path / "chela").mkdir()
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / "cache-statusline.sh").write_text("checkout\n")
    if packaged:
        (tmp_path / "chela" / "scripts").mkdir()
        (tmp_path / "chela" / "scripts" / "cache-statusline.sh").write_text("packaged\n")
    return tmp_path / "chela" / "main.py"


def test_statusline_script_prefers_the_packaged_copy(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "__file__", str(_fake_layout(tmp_path, packaged=True)))
    assert main.statusline_script() == tmp_path / "chela" / "scripts" / "cache-statusline.sh"


def test_statusline_script_falls_back_to_the_checkout(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "__file__", str(_fake_layout(tmp_path, packaged=False)))
    assert main.statusline_script() == tmp_path / "scripts" / "cache-statusline.sh"


def test_install_statusline_writes_the_packaged_path(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "__file__", str(_fake_layout(tmp_path, packaged=True)))
    settings = tmp_path / "settings.json"
    main.cmd_install_statusline(argparse.Namespace(settings=str(settings), write=True, force=False))
    assert f'"command": "{tmp_path}/chela/scripts/cache-statusline.sh"' in settings.read_text()
