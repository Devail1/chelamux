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
import os
import stat
import subprocess
import sys
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
    "chela/dashboard/static/term-focus.js",
    "chela/dashboard/static/term-upload.js",
    "chela/dashboard/static/term-touch.css",
    "chela/mergegate.py",                         # copied into the plugin's hooks/
    "chela/scripts/cache-statusline.sh",          # the statusLine hook
    "chela/scripts/agent-terminals.sh",           # CMX-469: the terminal wall's supervisor
    "chela/scripts/chela-env.sh",                 # ... and the env loader it sources
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


@pytest.mark.parametrize("name", ["agent-terminals.sh", "chela-env.sh"])
def test_the_terminal_wall_scripts_are_the_scripts_copies(wheel, name):
    """CMX-469: a PyPI install had no terminal wall; the wheel now carries both files,
    byte-identical to the checkout's, the supervisor still executable."""
    info = wheel.getinfo(f"chela/scripts/{name}")
    assert wheel.read(info) == (ROOT / "scripts" / name).read_bytes()
    if name == "agent-terminals.sh":
        assert stat.S_IMODE(info.external_attr >> 16) & stat.S_IXUSR


def test_the_wheel_runs_chela_agent_terminals_from_its_own_copy(wheel, tmp_path):
    """`chela agent-terminals --help` answers from the unpacked wheel alone, and the
    supervisor it would run is the one inside that wheel, not a checkout's."""
    site = tmp_path / "site"
    wheel.extractall(site)
    code = ("import sys; sys.argv = ['chela', 'agent-terminals', '--help']\n"
            "from chela import main\n"
            "print('SCRIPT', main.agent_terminals_script())\n"
            "main.main()\n")
    env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    # The test venv's chela is importable too: put the wheel's copy first, so this
    # exercises the wheel's layout.
    out = subprocess.run([sys.executable, "-c", f"import sys; sys.path.insert(0, {str(site)!r})\n" + code],
                         cwd=tmp_path, env=env, timeout=120, capture_output=True, text=True,
                         check=False)
    assert out.returncode == 0, out.stderr
    assert f"SCRIPT {site}/chela/scripts/agent-terminals.sh" in out.stdout
    assert "usage: chela agent-terminals" in out.stdout


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
    assert "scripts/agent-terminals.sh" in sdist_names
    assert "scripts/chela-env.sh" in sdist_names
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


def _install(tmp_path, monkeypatch, *, packaged: bool, write: bool) -> Path:
    monkeypatch.setattr(main, "__file__", str(_fake_layout(tmp_path, packaged=packaged)))
    monkeypatch.setattr(main.config, "CHELA_DIR", tmp_path / "chela-dir")
    settings = tmp_path / "settings.json"
    main.cmd_install_statusline(argparse.Namespace(settings=str(settings), write=write, force=False))
    return settings


def test_install_statusline_wires_a_stable_copy_of_the_packaged_hook(tmp_path, monkeypatch):
    """A `uvx` install lives in uv's cache, which `uv cache prune` deletes: `--write` copies
    the packaged hook under $CHELA_DIR, executable, and points settings.json there."""
    settings = _install(tmp_path, monkeypatch, packaged=True, write=True)
    stable = tmp_path / "chela-dir" / "cache-statusline.sh"
    assert stable.read_text() == "packaged\n"
    assert stable.stat().st_mode & stat.S_IXUSR
    assert f'"command": "{stable}"' in settings.read_text()


def test_install_statusline_points_a_checkout_at_its_own_script(tmp_path, monkeypatch):
    settings = _install(tmp_path, monkeypatch, packaged=False, write=True)
    assert f'"command": "{tmp_path}/scripts/cache-statusline.sh"' in settings.read_text()
    assert not (tmp_path / "chela-dir" / "cache-statusline.sh").exists()


def test_install_statusline_without_write_copies_nothing(tmp_path, monkeypatch, capsys):
    settings = _install(tmp_path, monkeypatch, packaged=True, write=False)
    assert f"{tmp_path}/chela/scripts/cache-statusline.sh" in capsys.readouterr().out
    assert not settings.exists()
    assert not (tmp_path / "chela-dir" / "cache-statusline.sh").exists()


# --- chela agent-terminals (CMX-469) -------------------------------------------------------

def _fake_terminals_layout(tmp_path: Path, packaged: bool) -> Path:
    (tmp_path / "chela").mkdir()
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / "agent-terminals.sh").write_text("checkout\n")
    if packaged:
        (tmp_path / "chela" / "scripts").mkdir()
        (tmp_path / "chela" / "scripts" / "agent-terminals.sh").write_text("packaged\n")
    return tmp_path / "chela" / "main.py"


def test_agent_terminals_script_prefers_the_packaged_copy(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "__file__", str(_fake_terminals_layout(tmp_path, packaged=True)))
    assert main.agent_terminals_script() == tmp_path / "chela" / "scripts" / "agent-terminals.sh"


def test_agent_terminals_script_falls_back_to_the_checkout(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "__file__", str(_fake_terminals_layout(tmp_path, packaged=False)))
    assert main.agent_terminals_script() == tmp_path / "scripts" / "agent-terminals.sh"


class _Exec(Exception):
    pass


@pytest.fixture
def execs(tmp_path, monkeypatch):
    """`os.execve` recorded instead of run; `bash` at /x/bash, ttyd on PATH unless removed."""
    calls = []

    def fake_execve(path, argv, env):
        calls.append((path, argv, env))
        raise _Exec

    script = tmp_path / "agent-terminals.sh"
    script.write_text("#!/bin/sh\n")
    monkeypatch.setattr(main, "agent_terminals_script", lambda: script)
    monkeypatch.setattr(main.os, "execve", fake_execve)
    tools = {"bash": "/x/bash", "ttyd": "/x/ttyd"}
    monkeypatch.setattr(main.shutil, "which", lambda name: tools.get(name))
    monkeypatch.delenv("TTYD", raising=False)
    return calls, script, tools


def test_agent_terminals_execs_bash_on_the_script_with_this_python(execs, monkeypatch, capsys):
    calls, script, _ = execs
    monkeypatch.setenv("PYTHON", "/usr/bin/python3")     # a stray one does not win
    monkeypatch.setenv("CHELA_TMUX_SESSION", "kept")
    monkeypatch.setenv("TMUX", "/tmp/tmux-1000/default,1,0")
    monkeypatch.setenv("TMUX_PANE", "%3")
    monkeypatch.setenv("CHELA_TERM_HEAL", "true")        # nor does an exported heal
    with pytest.raises(_Exec):
        main.cmd_agent_terminals(argparse.Namespace())
    [(path, argv, env)] = calls
    assert path == "/x/bash"
    assert argv == ["/x/bash", str(script)]
    assert env["PYTHON"] == sys.executable
    assert env["CHELA_TMUX_SESSION"] == "kept"
    assert "TMUX" not in env and "TMUX_PANE" not in env
    assert env["CHELA_TERM_HEAL"] == "false"
    assert capsys.readouterr().err == ""


def test_agent_terminals_warns_without_ttyd_and_still_runs(execs, capsys):
    calls, _, tools = execs
    del tools["ttyd"]
    with pytest.raises(_Exec):
        main.cmd_agent_terminals(argparse.Namespace())
    assert len(calls) == 1
    err = capsys.readouterr().err
    assert "warning: ttyd is not on PATH" in err
    assert "brew install ttyd" in err


def test_agent_terminals_checks_the_ttyd_that_TTYD_names(execs, monkeypatch, capsys):
    calls, _, tools = execs
    monkeypatch.setenv("TTYD", "/opt/ttyd")
    with pytest.raises(_Exec):
        main.cmd_agent_terminals(argparse.Namespace())
    assert "warning: /opt/ttyd is not on PATH" in capsys.readouterr().err
    tools["/opt/ttyd"] = "/opt/ttyd"
    with pytest.raises(_Exec):
        main.cmd_agent_terminals(argparse.Namespace())
    assert capsys.readouterr().err == ""


def test_agent_terminals_without_bash_exits_1(execs, capsys):
    calls, _, tools = execs
    del tools["bash"]
    with pytest.raises(SystemExit) as e:
        main.cmd_agent_terminals(argparse.Namespace())
    assert e.value.code == 1
    assert not calls
    assert "bash is not on PATH" in capsys.readouterr().err


def test_agent_terminals_without_its_script_exits_1(execs, capsys):
    calls, script, _ = execs
    script.unlink()
    with pytest.raises(SystemExit) as e:
        main.cmd_agent_terminals(argparse.Namespace())
    assert e.value.code == 1
    assert not calls
    assert f"{script} is missing" in capsys.readouterr().err


def test_chela_agent_terminals_dispatches_to_the_command(monkeypatch):
    seen = []
    monkeypatch.setattr(main, "cmd_agent_terminals", lambda args: seen.append(args.command))
    monkeypatch.setattr(sys, "argv", ["chela", "agent-terminals"])
    main.main()
    assert seen == ["agent-terminals"]


def test_an_unpatched_agent_terminals_run_in_a_test_is_refused(monkeypatch):
    """The 2026-11-22 incident shape: a test reaching `chela agent-terminals` for real. The
    conftest fence refuses the exec, so the pytest worker never becomes the supervisor."""
    from conftest import LiveExecEscape

    monkeypatch.setattr(sys, "argv", ["chela", "agent-terminals"])
    with pytest.raises(LiveExecEscape):
        main.main()
