"""Doctor must read the manifest that RUNS, not the one chela writes.

`chela plugin` renders `$CHELA_DIR/plugin/hooks/hooks.json`. **No agent reads that file.**
`/plugin install` copies the plugin into Claude Code's cache, and that copy is what every
agent loads at startup. The two drifted — the rendered one raised `PermissionRequest` to
120s, the installed one still said 2 — so every gate hook was killed after two seconds, no
gate was ever held, the phone's answer buttons never appeared, and `chela doctor` printed
green all day, because it checked the file chela WRITES.

These tests pin the fix: the installed copy is found by DISCOVERY (so a version bump moves
the cache directory and it still resolves), a drift is an ERROR, and a copy that cannot be
found or cannot be read is *also* an ERROR — never a silent pass, which is the same bug one
level up.
"""
from __future__ import annotations

import json

import pytest

from chela import config, doctor, hooks, main

PORT = 5005


@pytest.fixture
def env(tmp_path, monkeypatch):
    """An isolated $CHELA_DIR and an isolated Claude Code config dir."""
    chela_dir = tmp_path / "chela"
    chela_dir.mkdir()
    monkeypatch.setattr(config, "CHELA_DIR", chela_dir)
    claude = tmp_path / "claude"
    (claude / "plugins").mkdir(parents=True)
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(claude))
    # A dashboard IS listening on PORT — published, pid-checked, the way the real one does
    # it. The plugin facts render their expected manifest against the port that is really
    # bound (CMX-41), so the fixture has to bind one.
    config.publish_dashboard_port(PORT)
    return tmp_path


def _render(port: int = PORT):
    """What `chela plugin` writes — the manifest nobody reads."""
    return hooks.render_plugin(config.CHELA_DIR / "plugin", port=port)


def _install(spec: dict, version: str = "0.1.0", marketplace: str = "chela",
             register: bool = True):
    """A plugin copy where Claude Code puts one, and (optionally) its bookkeeping."""
    root = hooks.plugins_dir() / "cache" / marketplace / "chela" / version
    (root / "hooks").mkdir(parents=True)
    (root / "hooks" / "hooks.json").write_text(json.dumps(spec), encoding="utf-8")
    if register:
        registry = hooks.plugins_dir() / "installed_plugins.json"
        data = {"version": 2, "plugins": {}}
        if registry.exists():
            data = json.loads(registry.read_text())
        data["plugins"].setdefault(f"chela@{marketplace}", []).append(
            {"scope": "user", "installPath": str(root), "version": version})
        registry.write_text(json.dumps(data), encoding="utf-8")
    return root


def _stale(port: int = PORT) -> dict:
    """The manifest that hid all day: PermissionRequest killed after 2 seconds."""
    spec = hooks.hooks_spec(port)
    spec["hooks"]["PermissionRequest"][0]["hooks"][0]["timeout"] = 2
    return spec


def _check(port: int = PORT) -> list[doctor.Finding]:
    """The two plugin facts, audited exactly as `chela doctor` audits them — through the
    registry, with no check of their own. `port` is what the dashboard has BOUND (the
    fixture publishes it), because that is the port the manifest must carry."""
    assert config.live_dashboard_port() == port
    return [
        finding
        for name in ("plugin.rendered", "plugin.installed")
        for f in [doctor.fact(name)]
        if f.applies()
        for finding in doctor.audit(f)
    ]


def _levels(findings, level):
    return [f for f in findings if f.level == level]


def _text(findings) -> str:
    return "\n".join(f"{f.title}\n{f.detail}" for f in findings)


# --- discovery --------------------------------------------------------------------

def test_the_installed_copy_is_discovered_not_constructed(env):
    _install(hooks.hooks_spec(PORT))
    copies = hooks.installed_plugins()
    assert len(copies) == 1
    assert copies[0].version == "0.1.0"
    assert copies[0].hooks == hooks.hooks_spec(PORT)
    assert copies[0].found_via == "installed_plugins.json"


def test_a_version_bump_still_resolves_the_right_cache_dir(env):
    """The cache path CONTAINS the version. A hardcoded path would check a directory that
    no longer exists — silently — the day `plugin.json` is bumped."""
    root = _install(hooks.hooks_spec(PORT), version="9.9.9")
    copies = hooks.installed_plugins()
    assert [c.root for c in copies] == [root]
    assert copies[0].version == "9.9.9"
    assert "9.9.9" in str(copies[0].manifest)


def test_the_cache_is_scanned_when_the_registry_is_unusable(env):
    """Claude Code's bookkeeping is its own and may change shape. Losing it must degrade
    to a scan, not to a pass."""
    root = _install(hooks.hooks_spec(PORT), version="2.0.0", register=False)
    (hooks.plugins_dir() / "installed_plugins.json").write_text("{not json", encoding="utf-8")
    copies = hooks.installed_plugins()
    assert [c.root for c in copies] == [root]
    assert copies[0].found_via == "a scan of the plugin cache"


def test_manifest_drift_is_empty_when_they_agree(env):
    assert hooks.manifest_drift(hooks.hooks_spec(PORT), hooks.hooks_spec(PORT)) == []


# --- doctor -----------------------------------------------------------------------

def test_doctor_ERRORs_when_the_installed_manifest_disagrees(env):
    _render()
    _install(_stale())
    findings = _check()
    errors = _levels(findings, doctor.ERROR)
    assert errors, "a stale installed manifest is a DEAD feature, not a warning"
    body = _text(errors)
    assert "INSTALLED" in body
    assert "PermissionRequest" in body and "timeout" in body
    assert str(hooks.plugins_dir()) in body     # it NAMES the file it read
    assert "STARTUP" in body                    # a running fleet keeps the stale hooks


def test_doctor_ERRORs_when_a_port_drift_reaches_the_installed_copy(env):
    """CMX-41's bug, now checked where it actually bites: the copy agents load."""
    _render(port=PORT)
    _install(hooks.hooks_spec(5001))
    body = _text(_levels(_check(PORT), doctor.ERROR))
    assert "5001" in body and "5005" in body


def test_doctor_ERRORs_when_the_installed_copy_cannot_be_found(env):
    _render()
    findings = _check()
    assert _levels(findings, doctor.ERROR), "no installed copy must never report green"
    assert "NOT INSTALLED" in _text(findings)


def test_doctor_ERRORs_when_the_installed_copy_cannot_be_read(env):
    """The cache may change shape between Claude Code releases. The failure mode when it
    does is a loud 'I cannot verify this' — a silent green here is the bug being fixed."""
    _render()
    root = _install(hooks.hooks_spec(PORT))
    (root / "hooks" / "hooks.json").write_text("{ truncated", encoding="utf-8")
    findings = _check()
    assert _levels(findings, doctor.ERROR)
    assert "cannot verify" in _text(findings)


def test_doctor_ERRORs_when_the_installed_manifest_has_no_hooks_object(env):
    _render()
    root = _install(hooks.hooks_spec(PORT))
    (root / "hooks" / "hooks.json").write_text('{"plugins": []}', encoding="utf-8")
    findings = _check()
    assert _levels(findings, doctor.ERROR)
    assert "cannot verify" in _text(findings)


def test_doctor_is_green_when_installed_and_rendered_agree(env):
    _render()
    _install(hooks.hooks_spec(PORT))
    findings = _check()
    assert not _levels(findings, doctor.ERROR)
    assert not _levels(findings, doctor.WARN)
    body = _text(findings)
    assert "installed plugin matches" in body
    assert str(hooks.plugins_dir()) in body      # and it says WHICH file it read


def test_doctor_ERRORs_when_the_rendered_manifest_is_stale(env):
    """The rendered copy is what a reinstall COPIES — stale here reinstalls stale."""
    directory = _render()
    hooks._write_json(directory / "hooks" / "hooks.json", _stale())
    _install(hooks.hooks_spec(PORT))
    body = _text(_levels(_check(), doctor.ERROR))
    assert "rendered plugin" in body and "STALE" in body


def test_doctor_says_nothing_when_no_plugin_was_ever_rendered(env):
    """`chela plugin` is step one. Not having run it is not a broken install."""
    assert _check() == []


# --- `chela plugin` closes the loop ------------------------------------------------

def test_chela_plugin_names_the_cache_path_when_the_install_is_stale(env, capsys):
    directory = _render()
    _install(_stale())
    main._report_installed_plugin(directory, PORT)
    out = capsys.readouterr().out
    assert "STALE INSTALL" in out
    assert str(hooks.installed_plugins()[0].manifest) in out
    assert "/plugin install chela@chela" in out


def test_chela_plugin_says_so_when_nothing_is_installed(env, capsys):
    directory = _render()
    main._report_installed_plugin(directory, PORT)
    out = capsys.readouterr().out
    assert "cannot find an INSTALLED copy" in out
    assert "/plugin marketplace add" in out


# --- the recap hook: a NEW hook is the version trapdoor, one turn on ------------------
#
# The installed copy is a COPY, made at install time and keyed on the plugin version. Add a
# hook and every already-installed fleet keeps a manifest that simply does not have it —
# and the old drift check read only the FIRST hook of each event, so a SessionStart that
# still said `http` (the transport it never fires over) would have compared green while the
# recap reached nobody. That is CMX-56's bug with a new face; it is checked here.

def _http_session_start(port: int = PORT) -> dict:
    """The manifest as it was BEFORE the recap: SessionStart over http, which never fires."""
    spec = hooks.hooks_spec(port)
    spec["hooks"]["SessionStart"] = [{"hooks": [{
        "type": "http",
        "url": f"http://127.0.0.1:{port}/hooks/SessionStart",
        "timeout": 2,
    }]}]
    return spec


def test_drift_catches_an_installed_copy_with_no_recap_hook(env):
    drift = hooks.manifest_drift(_http_session_start(), hooks.hooks_spec(PORT))
    assert any("SessionStart" in d and "command" in d for d in drift)


def test_doctor_ERRORs_when_the_installed_copy_predates_the_recap(env):
    _render()
    _install(_http_session_start())
    body = _text(_levels(_check(), doctor.ERROR))
    assert "SessionStart" in body and "curl" in body


def test_drift_sees_a_hook_dropped_from_an_entry_that_declares_two(env):
    """The old check read entries[0]["hooks"][0] and nothing else."""
    expected = hooks.hooks_spec(PORT)
    expected["hooks"]["Stop"][0]["hooks"].append({"type": "command", "command": "x",
                                                  "timeout": 1})
    drift = hooks.manifest_drift(hooks.hooks_spec(PORT), expected)
    assert any("Stop" in d and "1 hook(s)" in d for d in drift)
