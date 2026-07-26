"""The env file is the single source of truth, and the dashboard port must survive a
process boundary.

The bug these cover: ``chela dashboard --port 5005`` put the port in *one process's*
environment, so ``chela plugin`` — a different process — read no ``CHELA_DASHBOARD_PORT``,
fell back to 5001, and baked a dead URL into the hooks manifest. Every hook then POSTed
into a closed socket and failed open, so the feature did nothing and said nothing. The
decisive test is therefore the one that actually crosses a process boundary
(:func:`test_live_port_is_readable_from_another_process`) — an in-process assertion could
not have caught it.
"""
from __future__ import annotations

import importlib
import json
import os
import subprocess
import sys

import pytest

from chela import config, dispatcher, doctor, hooks, runtime_truth


@pytest.fixture
def chela_dir(tmp_path, monkeypatch):
    """A temp CHELA_DIR + a reloaded config, so nothing here reads the real ~/.chela."""
    monkeypatch.setenv("CHELA_DIR", str(tmp_path))
    monkeypatch.delenv("CHELA_ENV_FILE", raising=False)   # the suite disables it globally
    monkeypatch.delenv("CHELA_DASHBOARD_PORT", raising=False)
    monkeypatch.delenv("CHELA_TMUX_SESSION", raising=False)
    # The landmine itself: pytest may be running inside a tmux pane, and
    # current_session() would then DERIVE that pane's session — a `webterm_*` mirror,
    # observed the first time this test ran. A service must never inherit one either;
    # scripts/run-chela.sh strips both vars for exactly this reason.
    monkeypatch.delenv("TMUX_PANE", raising=False)
    monkeypatch.delenv("TMUX", raising=False)
    # `dispatcher.DB_PATH` is latched at import time, NOT re-derived from `config.CHELA_DIR`
    # above — so without this, `doctor.check()`'s `pr.checks` fact reads whatever DB some
    # OTHER test file (or a leftover from a prior worker run) left at the process-wide
    # default path, finds a leaked `awaiting_review` row, and shells out to real `gh` to ask
    # about it. In CI, with no `GH_TOKEN`, that comes back CANNOT-VERIFY (correctly — an
    # unread check state must never read as green) and this test goes red for a PR it never
    # touched. Give this fixture its own DB, exactly like `tests/test_contract.py`'s
    # `_own_runs_db` does for the same reason.
    monkeypatch.setattr(dispatcher, "DB_PATH", tmp_path / "scheduler.db")
    importlib.reload(config)
    importlib.reload(doctor)
    importlib.reload(hooks)
    yield tmp_path
    monkeypatch.undo()
    importlib.reload(config)
    importlib.reload(doctor)
    importlib.reload(hooks)


# --- the env file -----------------------------------------------------------------

def test_parses_comments_export_and_quotes(tmp_path):
    env = tmp_path / "chela.env"
    env.write_text(
        "# a comment\n"
        "\n"
        "CHELA_TMUX_SESSION=myteam\n"
        "export CHELA_DASHBOARD_PORT=5005\n"
        'CHELA_IGNORE_WINDOWS="__main__,scratch"\n'
        "CHELA_DASH_HOST='127.0.0.1'\n"
        "not a var\n"
    )
    assert config.parse_env_file(env) == {
        "CHELA_TMUX_SESSION": "myteam",
        "CHELA_DASHBOARD_PORT": "5005",
        "CHELA_IGNORE_WINDOWS": "__main__,scratch",
        "CHELA_DASH_HOST": "127.0.0.1",
    }


def test_missing_file_falls_back_to_defaults(chela_dir):
    """A fresh install has no env file at all. That is not an error."""
    assert config.parse_env_file(chela_dir / "chela.env") == {}
    assert config.load_env_file(chela_dir / "chela.env") == {}
    assert config.dashboard_port() == config.DEFAULT_DASHBOARD_PORT
    assert config.current_session() == "chela"


def test_an_exported_value_beats_the_file(chela_dir, monkeypatch):
    (chela_dir / "chela.env").write_text("CHELA_TMUX_SESSION=from-file\n")
    monkeypatch.setenv("CHELA_TMUX_SESSION", "from-env")
    config.load_env_file(chela_dir / "chela.env")
    assert os.environ["CHELA_TMUX_SESSION"] == "from-env"
    assert config.current_session() == "from-env"


def test_the_file_configures_a_bare_process(chela_dir, monkeypatch):
    """Nothing exported: the file alone has to be enough (`chela` in a plain shell)."""
    monkeypatch.delenv("CHELA_TMUX_SESSION", raising=False)
    (chela_dir / "chela.env").write_text("CHELA_TMUX_SESSION=myteam\nCHELA_DASHBOARD_PORT=5005\n")
    config.load_env_file(chela_dir / "chela.env")
    assert config.current_session() == "myteam"
    assert config.dashboard_port() == 5005


def test_a_junk_env_file_does_not_crash(chela_dir):
    (chela_dir / "chela.env").write_text("\x00\x01 garbage ===\nCHELA_DASHBOARD_PORT=5005\n")
    config.load_env_file(chela_dir / "chela.env")
    assert config.dashboard_port() == 5005


# --- the published port -----------------------------------------------------------

def test_live_port_beats_the_configured_one(chela_dir, monkeypatch):
    """`--port 5005` on a dashboard whose env says 5001: the plugin must follow 5005."""
    monkeypatch.setenv("CHELA_DASHBOARD_PORT", "5001")
    config.publish_dashboard_port(5005, "127.0.0.1")
    assert config.dashboard_port() == 5001            # what the config asked for
    assert config.live_dashboard_port() == 5005       # what is actually being served
    assert hooks.hook_url("SessionStart") == "http://127.0.0.1:5005/hooks/SessionStart"


def test_no_dashboard_running_falls_back_to_the_configured_port(chela_dir, monkeypatch):
    monkeypatch.setenv("CHELA_DASHBOARD_PORT", "5005")
    assert config.live_dashboard() is None
    assert config.live_dashboard_port() == 5005


def test_a_dead_dashboard_is_not_a_live_one(chela_dir, monkeypatch):
    """A crashed dashboard's leftover file must not keep pointing hooks at a dead port."""
    monkeypatch.setenv("CHELA_DASHBOARD_PORT", "5001")
    dead = subprocess.Popen([sys.executable, "-c", "pass"])
    dead.wait()                                        # a pid that has certainly exited
    config.dashboard_port_file().write_text(
        json.dumps({"port": 5005, "host": "127.0.0.1", "pid": dead.pid})
    )
    assert config.live_dashboard() is None
    assert config.live_dashboard_port() == 5001


def test_a_corrupt_port_file_is_ignored(chela_dir):
    config.dashboard_port_file().write_text("{not json")
    assert config.live_dashboard() is None
    assert config.live_dashboard_port() == config.DEFAULT_DASHBOARD_PORT


def test_clear_dashboard_port_is_idempotent(chela_dir):
    config.publish_dashboard_port(5005)
    config.clear_dashboard_port()
    config.clear_dashboard_port()                     # already gone: must not raise
    assert config.live_dashboard() is None


def test_live_port_is_readable_from_another_process(chela_dir):
    """THE regression test. `chela plugin` is not the dashboard's process — a port that
    only exists in the dashboard's own os.environ is invisible to it, which is exactly
    how the hooks manifest came to name a port nobody was listening on."""
    config.publish_dashboard_port(5005, "127.0.0.1")   # as the dashboard does at startup
    env = {**os.environ, "CHELA_DIR": str(chela_dir), "CHELA_ENV_FILE": ""}
    env.pop("CHELA_DASHBOARD_PORT", None)              # the other process was told nothing
    result = subprocess.run(
        [sys.executable, "-c",
         "from chela import hooks; print(hooks.hook_url('PreToolUse'))"],
        capture_output=True, text=True, env=env, timeout=60,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "http://127.0.0.1:5005/hooks/PreToolUse"


def test_an_explicit_port_still_renders(chela_dir):
    """`chela plugin --port N` keeps working — it just isn't the source of truth."""
    config.publish_dashboard_port(5005)
    spec = hooks.hooks_spec(port=6001)["hooks"]
    assert spec["Stop"][0]["hooks"][0]["url"] == "http://127.0.0.1:6001/hooks/Stop"
    # SessionStart is the one COMMAND hook (it never fires over http) — the port is baked
    # into the curl it shells, and a manifest whose recap posts to a dead port is CMX-41
    # again, one transport over.
    assert ("http://127.0.0.1:6001/hooks/SessionStart"
            in spec["SessionStart"][0]["hooks"][0]["command"])

    rendered = hooks.render_plugin(chela_dir / "plugin", port=6001)
    manifest = json.loads((rendered / "hooks" / "hooks.json").read_text())
    assert manifest["hooks"]["PreToolUse"][0]["hooks"][0]["url"].endswith(":6001/hooks/PreToolUse")


# --- doctor: the drift has to be loud ----------------------------------------------

def _levels(findings, level):
    return [f for f in findings if f.level == level]


def test_doctor_flags_a_port_the_config_does_not_know_about(chela_dir, monkeypatch):
    monkeypatch.setenv("CHELA_DASHBOARD_PORT", "5001")
    config.publish_dashboard_port(5005)               # the dashboard bound something else
    errors = _levels(doctor.check(), doctor.ERROR)
    assert any("5005" in f.title and "5001" in f.title for f in errors)


def _install_plugin(spec: dict, version: str = "0.1.0") -> None:
    """A plugin copy where `/plugin install` puts one — the manifest agents really read.
    Doctor refuses to pass without it (CMX-56), so an "everything agrees" test has to
    include the copy that actually runs."""
    root = hooks.plugins_dir() / "cache" / "chela" / "chela" / version
    (root / "hooks").mkdir(parents=True)
    (root / "hooks" / "hooks.json").write_text(json.dumps(spec), encoding="utf-8")
    (hooks.plugins_dir() / "installed_plugins.json").write_text(json.dumps({
        "version": 2,
        "plugins": {"chela@chela": [{"scope": "user", "installPath": str(root),
                                     "version": version}]},
    }), encoding="utf-8")


def test_doctor_flags_a_plugin_baked_against_a_stale_port(chela_dir, monkeypatch):
    """CMX-41, exactly: the manifest says 5001, the dashboard serves 5005."""
    monkeypatch.setenv("CHELA_DASHBOARD_PORT", "5005")
    config.publish_dashboard_port(5005)
    hooks.render_plugin(chela_dir / "plugin", port=5001)
    _install_plugin(hooks.hooks_spec(5001))
    errors = _levels(doctor.check(), doctor.ERROR)
    # Both copies are stale, and both are named: the one chela renders (a reinstall would
    # only copy the dead URL forward) and the one agents load (the URL that is dead now).
    assert any("rendered plugin" in f.title and "5001" in f.detail for f in errors)
    assert any("INSTALLED" in f.title and "5001" in f.detail for f in errors)


def test_doctor_is_quiet_when_everything_agrees(chela_dir, monkeypatch):
    (chela_dir / "chela.env").write_text("CHELA_DASHBOARD_PORT=5005\n")
    monkeypatch.setenv("CHELA_DASHBOARD_PORT", "5005")
    config.publish_dashboard_port(5005)
    # agents.native_status_feed: publishing the port above makes `config.live_dashboard()`
    # non-None, so the fact would otherwise ask `claude` for real — CANNOT VERIFY on CI,
    # where `claude` is not on PATH (correctly; see runtime_truth._native_status_probe's
    # docstring). Stub the seam, the same idiom cmx-167 used for `_gh_auth_status`.
    monkeypatch.setattr(runtime_truth, "_native_status_probe", lambda: (True, "0.1s"))
    hooks.render_plugin(chela_dir / "plugin", port=5005)
    _install_plugin(hooks.hooks_spec(5005))
    assert _levels(doctor.check(), doctor.ERROR) == []


def test_chela_dir_isolates_the_dispatcher_db(chela_dir):
    """`dispatcher.DB_PATH` is latched at import time, NOT re-derived from `config.CHELA_DIR`
    on reload — so unless this fixture repoints it, `doctor.check()`'s `pr.checks` fact reads
    whatever DB some OTHER test (or a stale run on this host) left at the frozen default
    path, finds a leftover `awaiting_review` row, and shells out to real `gh` about it. In
    CI, with no `GH_TOKEN`, that comes back CANNOT-VERIFY (correctly) and fails this file's
    `test_doctor_is_quiet_when_everything_agrees` for a PR it never touched. Same fix as
    `tests/test_contract.py`'s `_own_runs_db`.
    """
    assert dispatcher.DB_PATH == chela_dir / "scheduler.db"
    assert not dispatcher.DB_PATH.exists()
    assert doctor.audit(doctor.fact("pr.checks")) == []


def test_doctor_flags_a_stale_env_var(chela_dir, monkeypatch):
    """`pm2 restart --update-env` MERGES: a var you removed is still in the process."""
    (chela_dir / "chela.env").write_text("CHELA_TMUX_SESSION=chela\n")
    monkeypatch.setenv("CHELA_TMUX_SESSION", "ccbot")          # the drift, verbatim
    warnings = _levels(doctor.check(), doctor.WARN)
    assert any("ccbot" in f.title and "chela" in f.title for f in warnings)


def test_doctor_rejects_an_env_file_that_relocates_itself(chela_dir):
    (chela_dir / "chela.env").write_text("CHELA_DIR=/somewhere/else\n")
    errors = _levels(doctor.check(), doctor.ERROR)
    assert any("CHELA_DIR" in f.title for f in errors)
