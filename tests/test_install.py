"""CMX-471: a PyPI install's update check — install kind, PyPI's latest, and every surface.

On a ``uv tool`` / ``uvx`` / pip install the daemon's hourly update check logged a
``NotAGitCheckout`` traceback, and ``/api/update/*`` offered a git pull. These pin the
detection of each install kind, the PyPI comparison (and its cache), and that the daemon,
``/api/update/status``, ``/api/update/apply``, ``chela update`` and ``chela doctor`` each
report the install kind and the matching upgrade command instead.

No test here touches the network: ``urllib.request.urlopen`` is replaced by a recorder.
"""
from __future__ import annotations

import argparse
import io
import json
import logging
import subprocess
from pathlib import Path

import pytest

from chela import capabilities, config, install, main, notify, runtime_truth, update
from chela.dashboard import app as dash
from tests._browser_session import as_browser


@pytest.fixture(autouse=True)
def _state(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "CHELA_DIR", tmp_path / "state")
    real_kind = install.kind     # a test may monkeypatch `kind`; clear the real cache
    real_kind.cache_clear()
    yield
    real_kind.cache_clear()


class _PyPI:
    """Stands in for ``urllib.request.urlopen``: records every call, answers ``body``."""

    def __init__(self, body=None, exc: Exception | None = None):
        self.body = {"info": {"version": "0.20.0"}} if body is None else body
        self.exc = exc
        self.calls: list[tuple] = []

    def __call__(self, url, timeout=None):
        self.calls.append((url, timeout))
        if self.exc is not None:
            raise self.exc
        raw = self.body if isinstance(self.body, bytes) else json.dumps(self.body).encode()
        return io.BytesIO(raw)


@pytest.fixture
def pypi(monkeypatch):
    fake = _PyPI()
    monkeypatch.setattr(install.urllib.request, "urlopen", fake)
    return fake


def _seed(latest="0.20.0", error="", checked_at=1.0):
    """The cache as the daemon's hourly check leaves it."""
    path = config.CHELA_DIR / "pypi-latest.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"checked_at": checked_at, "latest": latest, "error": error}))


def _uv(stdout="", returncode=0, exc: Exception | None = None):
    calls = []

    def run(argv, **kw):
        calls.append((argv, kw))
        if exc is not None:
            raise exc
        return subprocess.CompletedProcess(argv, returncode, stdout, "")
    run.calls = calls
    return run


def _not_git(monkeypatch, kind=install.UV_TOOL):
    def boom(*a, **k):
        raise update.NotAGitCheckout("not a git checkout")
    monkeypatch.setattr(update, "commits_behind", boom)
    monkeypatch.setattr(update, "repo_root", boom)
    monkeypatch.setattr(install, "kind", lambda: kind)


# --- detect: one test per install kind -----------------------------------------------

UV_LIST = "awscli v1.45.14\n- aws\nchelamux v0.19.1\n- chela\nlean v1.0.227\n- lean\n"


def test_detect_git_checkout_with_a_git_dir(tmp_path):
    (tmp_path / ".git").mkdir()
    run = _uv(UV_LIST)
    assert install.detect(tmp_path, prefix="/x/archive-v0/abc", run=run) == "git"
    assert run.calls == []  # a checkout never asks uv


def test_detect_git_worktree_with_a_git_file(tmp_path):
    (tmp_path / ".git").write_text("gitdir: /elsewhere\n")
    assert install.detect(tmp_path, prefix="/venv", run=_uv()) == "git"


@pytest.mark.parametrize("prefix", [
    "/home/u/.cache/uv/archive-v0/Xy12",
    "/home/u/.cache/uv/builds-v0/.tmpAb",
    "/c/archive-v12/h",
])
def test_detect_uvx_from_the_environment_prefix(tmp_path, prefix):
    run = _uv(UV_LIST)   # a uv tool install ALSO exists: the running env still wins
    assert install.detect(tmp_path, prefix=prefix, run=run) == "uvx"
    assert run.calls == []


TOOL_ENV = "/home/u/.local/share/uv/tools/chelamux"


def _receipt(env: Path, name="chelamux") -> str:
    env.mkdir(parents=True, exist_ok=True)
    (env / "uv-receipt.toml").write_text(
        f'[tool]\nrequirements = [{{ name = "{name}" }}]\n'
        'entrypoints = [{ name = "chela", install-path = "/x/chela", from = "chelamux" }]\n')
    return str(env)


def test_detect_uv_tool_from_its_receipt_without_running_uv(tmp_path):
    run = _uv("")
    env = _receipt(tmp_path / "tools" / "whatever-name")
    assert install.detect(tmp_path, prefix=env, run=run) == "uv-tool"
    assert run.calls == []


@pytest.mark.parametrize("content", [
    '[tool]\nrequirements = [{ name = "awscli" }]\n',
    '[tool]\nrequirements = []\n',
    "[tool]\n",
    "not = [toml",
    '[tool]\nrequirements = ["chelamux"]\n',
])
def test_a_receipt_for_another_tool_or_unreadable_is_not_uv_tool(tmp_path, content):
    env = tmp_path / "venv"
    env.mkdir()
    (env / "uv-receipt.toml").write_text(content)
    assert install.detect(tmp_path, prefix=str(env), run=_uv(UV_LIST)) == "pip"


def test_detect_uv_tool_when_uv_tool_list_shows_chelamux(tmp_path):
    run = _uv(UV_LIST)
    assert install.detect(tmp_path, prefix=TOOL_ENV, run=run) == "uv-tool"
    assert run.calls == [(["uv", "tool", "list"],
                          {"capture_output": True, "text": True, "timeout": 10})]


def test_a_pip_venv_on_a_host_that_also_has_a_uv_tool_install_is_pip(tmp_path):
    """The running interpreter decides, not the host: `uv tool upgrade` would upgrade a
    different copy than the one this process runs from."""
    run = _uv(UV_LIST)
    assert install.detect(tmp_path, prefix="/srv/project/.venv", run=run) == "pip"
    assert run.calls == []


@pytest.mark.parametrize("prefix", [
    "/venv/my-archive-v0/x", "/venv/archive-v0x/x", "/venv/archive-v/x", "/venv/archive",
])
def test_detect_is_not_fooled_by_near_miss_uvx_dirs(tmp_path, prefix):
    assert install.detect(tmp_path, prefix=prefix, run=_uv("")) == "pip"


@pytest.mark.parametrize("listing", [
    "",
    "awscli v1.45.14\n- aws\n",
    "chelamux-extra v1.0\n",
    "xchelamux v1.0\n",
    "- chelamux v0.19.1\n",          # an executable line, not the tool's own
    "chelamux\n",                    # no version: not uv's tool line
])
def test_detect_pip_when_uv_tool_list_does_not_show_chelamux(tmp_path, listing):
    assert install.detect(tmp_path, prefix=TOOL_ENV, run=_uv(listing)) == "pip"


def test_detect_pip_when_uv_tool_list_fails_even_if_it_names_chelamux(tmp_path):
    assert install.detect(tmp_path, prefix=TOOL_ENV,
                          run=_uv(UV_LIST, returncode=2)) == "pip"


@pytest.mark.parametrize("exc", [
    FileNotFoundError("uv"), PermissionError("uv"),
    subprocess.TimeoutExpired(["uv"], 10),
])
def test_detect_pip_when_uv_is_missing_or_hangs(tmp_path, exc):
    assert install.detect(tmp_path, prefix=TOOL_ENV, run=_uv(exc=exc)) == "pip"


def test_kind_detects_once_per_process(monkeypatch):
    calls = []
    monkeypatch.setattr(install, "detect", lambda: calls.append(1) or "uvx")
    assert install.kind() == "uvx"
    assert install.kind() == "uvx"
    assert calls == [1]


def test_kind_is_git_for_this_checkout():
    assert install.kind() == "git"


# --- the upgrade path per kind -------------------------------------------------------

@pytest.mark.parametrize("kind,cmd,text", [
    ("git", "chela update", "run `chela update`"),
    ("uv-tool", "uv tool upgrade chelamux",
     "run `uv tool upgrade chelamux`, then restart the services"),
    ("uvx", "", "uvx always runs the latest; restart the services"),
    ("pip", "pip install --upgrade chelamux",
     "run `pip install --upgrade chelamux` in chela's venv, then restart the services"),
])
def test_command_and_advice_per_kind(kind, cmd, text):
    assert install.command(kind) == cmd
    assert install.advice(kind) == text


@pytest.mark.parametrize("latest,installed,want", [
    ("0.20.0", "0.19.1", True),
    ("0.19.1", "0.19.1", False),
    ("0.19.0", "0.19.1", False),
    ("1.0", "1.0.0", False),
    ("1.0.0", "1.0", False),
    ("1.0.1", "1.0", True),
    ("0.10.0", "0.9.9", True),       # numeric, not lexical
    ("v0.20.0", "0.19.1", True),
    ("0.20.0", "0.20.0.dev1", False),
    ("", "0.19.1", None),
    ("0.20.0", "0.0.0+unknown", True),
    ("garbage", "0.19.1", None),
    ("0.20.0", "unknown", None),
])
def test_is_newer(latest, installed, want):
    assert install.is_newer(latest, installed) is want


# --- PyPI's latest, through the cache -----------------------------------------------

def test_latest_asks_pypi_with_the_exact_url_and_timeout_then_caches(pypi):
    assert install.latest_version(fetch=True, now=1000.0) == ("0.20.0", "")
    assert pypi.calls == [("https://pypi.org/pypi/chelamux/json", 5)]
    cached = json.loads((config.CHELA_DIR / "pypi-latest.json").read_text())
    assert cached == {"checked_at": 1000.0, "latest": "0.20.0", "error": ""}


def test_a_cache_only_read_never_asks_pypi_without_a_cache(pypi):
    assert install.latest_version(now=1.0) == (
        "", "PyPI not checked yet (the daemon checks hourly; `chela update --check` checks now)")
    assert install.latest_version(fetch=False, now=1.0)[0] == ""
    assert pypi.calls == []
    assert not (config.CHELA_DIR / "pypi-latest.json").exists()


def test_a_cache_only_read_returns_a_stale_cache_as_is(pypi):
    _seed("0.20.0", checked_at=1.0)
    assert install.latest_version(now=1.0 + 10 * 3600) == ("0.20.0", "")
    _seed("", error="could not read PyPI: offline", checked_at=1.0)
    assert install.latest_version(now=1.0 + 10 * 3600) == ("", "could not read PyPI: offline")
    assert pypi.calls == []


def test_package_status_defaults_to_the_cache_only(pypi):
    _seed("0.20.0")
    st = install.package_status("pip", installed="0.19.1", now=1.0 + 10 * 3600)
    assert (st.behind, pypi.calls) == (True, [])


def test_the_cache_is_written_atomically(pypi, monkeypatch):
    replaced = []
    real = install.os.replace
    monkeypatch.setattr(install.os, "replace", lambda a, b: replaced.append((Path(a).name, Path(b).name)) or real(a, b))
    install.latest_version(fetch=True, now=1.0)
    assert replaced and replaced[0][1] == "pypi-latest.json" and replaced[0][0] != "pypi-latest.json"
    assert [f.name for f in config.CHELA_DIR.iterdir()] == ["pypi-latest.json"]


def test_a_fresh_cache_is_read_without_a_network_call(pypi):
    install.latest_version(fetch=True, now=1000.0)
    pypi.body = {"info": {"version": "9.9.9"}}
    assert install.latest_version(fetch=True, now=1000.0 + 3599) == ("0.20.0", "")
    assert len(pypi.calls) == 1


def test_a_cache_an_hour_old_is_refreshed(pypi):
    install.latest_version(fetch=True, now=1000.0)
    pypi.body = {"info": {"version": "0.21.0"}}
    assert install.latest_version(fetch=True, now=1000.0 + 3600) == ("0.21.0", "")
    assert len(pypi.calls) == 2


def test_a_cache_from_the_future_is_refreshed(pypi):
    install.latest_version(fetch=True, now=5000.0)
    install.latest_version(fetch=True, now=1000.0)
    assert len(pypi.calls) == 2


@pytest.mark.parametrize("content", ["not json", "{}", '{"checked_at": "x"}', "[]"])
def test_an_unreadable_cache_asks_pypi(pypi, content):
    path = config.CHELA_DIR / "pypi-latest.json"
    path.parent.mkdir(parents=True)
    path.write_text(content)
    assert install.latest_version(fetch=True, now=1000.0) == ("0.20.0", "")
    assert len(pypi.calls) == 1


@pytest.mark.parametrize("exc", [OSError("offline"), RuntimeError("weird"), ValueError("x")])
def test_a_failed_read_is_an_error_and_is_cached_ten_minutes(pypi, exc):
    pypi.exc = exc
    latest, error = install.latest_version(fetch=True, now=1000.0)
    assert latest == ""
    assert error.startswith("could not read PyPI: ")
    pypi.exc = None
    assert install.latest_version(fetch=True, now=1000.0 + 599)[0] == ""      # still the cached failure
    assert len(pypi.calls) == 1
    assert install.latest_version(fetch=True, now=1000.0 + 600) == ("0.20.0", "")
    assert len(pypi.calls) == 2


@pytest.mark.parametrize("body", [{}, {"info": None}, {"info": {"version": ""}},
                                  {"info": {"version": 3}}, b"<html>"])
def test_pypi_naming_no_version_is_an_error_not_a_version(pypi, body):
    pypi.body = body
    latest, error = install.latest_version(fetch=True, now=1.0)
    assert latest == ""
    assert error


def test_an_unwritable_cache_still_answers(pypi, monkeypatch):
    monkeypatch.setattr(config, "CHELA_DIR", Path("/proc/no-such-dir/state"))
    assert install.latest_version(fetch=True, now=1.0) == ("0.20.0", "")


# --- package_status: behind, up to date, unknown -------------------------------------

def test_package_status_behind(pypi):
    st = install.package_status("uv-tool", installed="0.19.1", fetch=True, now=1.0)
    assert (st.ok, st.behind, st.latest, st.error) == (True, True, "0.20.0", "")
    assert st.as_dict() == {
        "ok": True, "git": False, "install": "uv-tool", "version": "0.19.1",
        "latest": "0.20.0", "behind": True, "command": "uv tool upgrade chelamux",
        "advice": "run `uv tool upgrade chelamux`, then restart the services",
    }
    assert install.describe(st) == ("uv-tool install, chelamux 0.19.1; 0.20.0 is on PyPI — "
                                     "run `uv tool upgrade chelamux`, then restart the services")


def test_package_status_up_to_date(pypi):
    st = install.package_status("uvx", installed="0.20.0", fetch=True, now=1.0)
    assert (st.ok, st.behind) == (True, False)
    assert st.as_dict()["behind"] is False
    assert st.as_dict()["command"] == ""
    assert install.describe(st) == "uvx install, chelamux 0.20.0; up to date with PyPI (0.20.0)"


def test_package_status_unknown_when_pypi_unreadable(pypi):
    pypi.exc = OSError("offline")
    st = install.package_status("pip", installed="0.19.1", fetch=True, now=1.0)
    assert st.ok is False
    assert st.behind is None
    d = st.as_dict()
    assert (d["ok"], d["behind"], d["latest"], d["install"]) == (False, None, None, "pip")
    assert d["error"].startswith("could not read PyPI")
    assert install.describe(st).startswith("pip install, chelamux 0.19.1; newer version unknown (")


def test_package_status_unknown_when_versions_cannot_be_compared(pypi):
    pypi.body = {"info": {"version": "garbage"}}
    st = install.package_status("pip", installed="0.19.1", fetch=True, now=1.0)
    assert st.ok is False
    assert st.latest == "garbage"
    assert st.error == "cannot compare '0.19.1' with 'garbage'"


def test_package_status_defaults_to_the_detected_kind(pypi, monkeypatch):
    monkeypatch.setattr(install, "kind", lambda: "pip")
    assert install.package_status(installed="0.19.1", fetch=True, now=1.0).install == "pip"


@pytest.fixture
def old_install(monkeypatch):
    monkeypatch.setattr(install, "__version__", "0.19.1")
    real = install.package_status
    monkeypatch.setattr(install, "package_status",
                        lambda *a, **k: real(*a, **{"installed": "0.19.1", **k}))


# --- the daemon: one INFO line, never a traceback -------------------------------------

def test_daemon_check_on_a_uv_tool_install_logs_one_info_line_and_no_traceback(
        pypi, monkeypatch, caplog, old_install):
    _not_git(monkeypatch)
    sent = []
    monkeypatch.setattr(notify, "enabled", lambda: True)
    monkeypatch.setattr(notify, "send", lambda msg, title="": sent.append((title, msg)))
    caplog.set_level(logging.DEBUG)
    assert update.daemon_tick(0) == 1
    mine = [r for r in caplog.records if r.name in ("chela.update", "chela.install")]
    assert [(r.levelname, r.getMessage()) for r in mine] == [
        ("INFO", "update check: uv-tool install, chelamux 0.19.1; 0.20.0 is "
                 "on PyPI — run `uv tool upgrade chelamux`, then restart the services")]
    assert not any(r.exc_info for r in caplog.records)
    assert pypi.calls == [("https://pypi.org/pypi/chelamux/json", 5)]   # the daemon DOES ask
    assert sent == [("chela: update available",
                     "chelamux 0.20.0 is on PyPI (installed 0.19.1) — run "
                     "`uv tool upgrade chelamux`, then restart the services")]


def test_package_check_notifies_only_on_the_edge(pypi, monkeypatch, old_install):
    _not_git(monkeypatch)
    sent = []
    monkeypatch.setattr(notify, "enabled", lambda: True)
    monkeypatch.setattr(notify, "send", lambda msg, title="": sent.append(title))
    assert update.check_and_notify(1) == 1
    assert sent == []
    assert update.check_and_notify(0) == 1
    assert sent == ["chela: update available"]


def test_package_check_never_notifies_when_notify_is_off(pypi, monkeypatch, old_install):
    _not_git(monkeypatch)
    sent = []
    monkeypatch.setattr(notify, "enabled", lambda: False)
    monkeypatch.setattr(notify, "send", lambda *a, **k: sent.append(1))
    assert update.check_and_notify(0) == 1
    assert sent == []


def test_package_check_up_to_date_returns_zero(pypi, monkeypatch):
    _not_git(monkeypatch, install.PIP)
    pypi.body = {"info": {"version": "0.0.1"}}
    sent = []
    monkeypatch.setattr(notify, "enabled", lambda: True)
    monkeypatch.setattr(notify, "send", lambda *a, **k: sent.append(1))
    assert update.check_and_notify(1) == 0
    assert sent == []


@pytest.mark.parametrize("prev", [0, 1, update.UNKNOWN_BEHIND])
def test_package_check_keeps_the_edge_when_pypi_is_unreadable(pypi, monkeypatch, prev, caplog):
    _not_git(monkeypatch, install.UVX)
    pypi.exc = OSError("offline")
    caplog.set_level(logging.INFO)
    assert update.check_and_notify(prev) == prev
    assert not any(r.exc_info for r in caplog.records)
    assert any("newer version unknown" in r.getMessage() and r.levelname == "INFO"
               for r in caplog.records)


def test_git_install_check_still_reads_the_checkout(monkeypatch):
    monkeypatch.setattr(install, "kind", lambda: install.GIT)
    monkeypatch.setattr(update, "commits_behind",
                        lambda fetch=True: update.UpdateStatus(ok=True, behind=3, branch="dev"))
    monkeypatch.setattr(install, "package_status",
                        lambda *a, **k: pytest.fail("a checkout never asks PyPI"))
    assert update.check_and_notify(3) == 3


@pytest.mark.parametrize("auto,kind,want", [
    (True, "git", "sweep"),
    (True, "uv-tool", "check"),
    (True, "uvx", "check"),
    (True, "pip", "check"),
    (False, "git", "check"),
    (False, "uv-tool", "check"),
])
def test_daemon_tick_sweeps_only_a_git_checkout_with_auto_update_on(monkeypatch, auto, kind, want):
    did = []
    monkeypatch.setattr(update, "auto_apply_enabled", lambda: auto)
    monkeypatch.setattr(install, "kind", lambda: kind)
    monkeypatch.setattr(update, "auto_apply_sweep", lambda: did.append("sweep"))
    monkeypatch.setattr(update, "check_and_notify", lambda prev: did.append("check") or 7)
    got = update.daemon_tick(5)
    assert did == [want]
    assert got == (5 if want == "sweep" else 7)


def test_auto_update_on_a_package_install_says_it_cannot_upgrade(monkeypatch, caplog):
    monkeypatch.setattr(update, "auto_apply_enabled", lambda: True)
    monkeypatch.setattr(install, "kind", lambda: "uv-tool")
    monkeypatch.setattr(update, "check_and_notify", lambda prev: prev)
    caplog.set_level(logging.INFO, logger="chela.update")
    update.daemon_tick(0)
    assert [r.getMessage() for r in caplog.records if r.name == "chela.update"] == [
        "auto-update (CHELA_AUTO_UPDATE) cannot upgrade a uv-tool install; checking PyPI only"]


# --- /api/update/status and /api/update/apply ----------------------------------------

@pytest.fixture
def client():
    return as_browser(dash.app.test_client())


def test_update_status_on_a_uv_tool_install(pypi, monkeypatch, client, old_install):
    _not_git(monkeypatch)
    _seed("0.20.0", checked_at=1.0)          # however old: a poll never asks PyPI itself
    data = client.get("/api/update/status").get_json()
    assert pypi.calls == []
    assert {k: data[k] for k in ("ok", "git", "install", "latest", "behind", "command")} == {
        "ok": True, "git": False, "install": "uv-tool", "latest": "0.20.0", "behind": True,
        "command": "uv tool upgrade chelamux"}
    assert data["advice"] == "run `uv tool upgrade chelamux`, then restart the services"


def test_update_status_on_an_up_to_date_uvx_install(pypi, monkeypatch, client):
    _not_git(monkeypatch, install.UVX)
    _seed("0.0.1")
    data = client.get("/api/update/status").get_json()
    assert (data["install"], data["behind"], data["command"]) == ("uvx", False, "")


def test_update_status_before_any_check_is_unknown_and_offline(pypi, monkeypatch, client):
    _not_git(monkeypatch, install.PIP)
    data = client.get("/api/update/status").get_json()
    assert pypi.calls == []
    assert (data["ok"], data["install"], data["latest"], data["behind"]) == (False, "pip", None, None)
    assert data["error"].startswith("PyPI not checked yet")


def test_update_status_on_a_git_checkout_says_git(client, monkeypatch):
    monkeypatch.setattr(update, "commits_behind",
                        lambda fetch=True: update.UpdateStatus(ok=False, error="boom"))
    assert client.get("/api/update/status").get_json()["install"] == "git"


@pytest.mark.parametrize("kind,text", [
    ("uv-tool", "uv-tool install: run `uv tool upgrade chelamux`, then restart the services"),
    ("uvx", "uvx install: uvx always runs the latest; restart the services"),
])
def test_update_apply_on_a_package_install_names_the_upgrade_path(monkeypatch, client, kind, text):
    _not_git(monkeypatch, kind)
    resp = client.post("/api/update/apply")
    assert resp.status_code == 400
    assert resp.get_json() == {"ok": False, "install": kind, "error": text}


# --- chela update / chela doctor -----------------------------------------------------

def test_chela_update_check_on_a_package_install(pypi, monkeypatch, capsys, old_install):
    _not_git(monkeypatch)
    main.cmd_update(argparse.Namespace(check=True))       # behind is not a failure of --check
    assert capsys.readouterr().out == (
        "update: uv-tool install, chelamux 0.19.1; 0.20.0 is on PyPI — run "
        "`uv tool upgrade chelamux`, then restart the services\n")


def test_chela_update_on_a_behind_package_install_exits_1(pypi, monkeypatch, capsys, old_install):
    _not_git(monkeypatch)
    with pytest.raises(SystemExit) as e:
        main.cmd_update(argparse.Namespace(check=False))
    assert e.value.code == 1
    assert "uv tool upgrade chelamux" in capsys.readouterr().out


@pytest.mark.parametrize("check", [True, False])
def test_chela_update_on_an_up_to_date_package_install_exits_0(pypi, monkeypatch, capsys, check):
    _not_git(monkeypatch, install.PIP)
    pypi.body = {"info": {"version": "0.0.1"}}
    main.cmd_update(argparse.Namespace(check=check))
    assert "up to date with PyPI (0.0.1)" in capsys.readouterr().out


@pytest.mark.parametrize("check", [True, False])
def test_chela_update_exits_1_when_pypi_is_unreadable(pypi, monkeypatch, check):
    _not_git(monkeypatch, install.PIP)
    pypi.exc = OSError("offline")
    with pytest.raises(SystemExit) as e:
        main.cmd_update(argparse.Namespace(check=check))
    assert e.value.code == 1


def test_update_available_capability_on_a_package_install(pypi, monkeypatch, old_install):
    _not_git(monkeypatch)
    _seed("0.20.0")
    cap = capabilities._update_available_capability()
    assert pypi.calls == []                  # daemon boot / doctor never wait on PyPI
    assert cap.on is True
    assert cap.fix == "uv tool upgrade chelamux"
    assert cap.detail.startswith("uv-tool install, chelamux 0.19.1; 0.20.0 is on PyPI")
    assert cap.as_dict()["install"] == "uv-tool"
    assert cap.as_dict()["behind"] is True


def test_update_available_capability_uvx_falls_back_to_advice(pypi, monkeypatch):
    _not_git(monkeypatch, install.UVX)
    _seed("0.0.1")
    cap = capabilities._update_available_capability()
    assert cap.on is False
    assert cap.fix == "uvx always runs the latest; restart the services"


def test_doctor_fact_applies_only_to_a_package_install(monkeypatch):
    assert not runtime_truth.fact("install.package_current").applies()
    _not_git(monkeypatch)
    assert runtime_truth.fact("install.package_current").applies()


def _findings(name="install.package_current"):
    return runtime_truth.audit(runtime_truth.fact(name))


def test_doctor_shows_kind_and_a_newer_version(pypi, monkeypatch, old_install):
    _not_git(monkeypatch)
    _seed("0.20.0")
    [f] = _findings()
    assert pypi.calls == []
    assert f.level == runtime_truth.WARN
    assert f.title.startswith("uv-tool install, chelamux 0.19.1; 0.20.0 is on PyPI")


def test_doctor_shows_kind_when_up_to_date(pypi, monkeypatch):
    _not_git(monkeypatch, install.PIP)
    _seed("0.0.1")
    [f] = _findings()
    assert f.level == runtime_truth.OK
    assert f.title.startswith("pip install, chelamux ")


@pytest.mark.parametrize("seed", [True, False])
def test_doctor_unreadable_or_unchecked_pypi_is_a_warn_not_an_error(pypi, monkeypatch, seed):
    _not_git(monkeypatch, install.UVX)
    if seed:
        _seed("", error="could not read PyPI: offline")
    findings = _findings()
    assert pypi.calls == []
    assert findings and all(f.level == runtime_truth.WARN for f in findings)
    assert "uvx install" in findings[0].title + findings[0].detail

