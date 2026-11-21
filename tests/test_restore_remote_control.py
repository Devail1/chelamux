"""CMX-460 — a restore relaunch carries Remote Control under the persisted name, and a
refused ``--remote-control`` never keeps the session down.

The real :func:`chela.restore.resume` drives the real :func:`chela.spawn.spawn_window`;
tmux is replaced by the spies from ``tests/test_launch_settings.py``.
"""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from chela import config, restore, session_names, spawn
from tests.test_launch_settings import SID, _resume, _spy_tmux, _verdict

RC_LINE = f"claude --remote-control tapparty --resume {SID}"
PLAIN = f"claude --resume {SID}"


def _all_sends(monkeypatch):
    """Every send-keys text, the setup line included (``_spy_tmux`` drops that one)."""
    sent: list[str] = []
    monkeypatch.setattr(spawn, "_send", lambda target, text: sent.append(text))
    return sent


def _checks(*answers):
    seq = iter(answers)
    return lambda wid, sid: next(seq)


# --------------------------------------------------------------------------
# the persisted name
# --------------------------------------------------------------------------

def test_rc_on_restore_relaunches_with_remote_control_under_the_persisted_name(
        tmp_path, monkeypatch):
    sent = _spy_tmux(monkeypatch, remote_control=True)
    [r] = _resume([_verdict(tmp_path, "old-label")], name_for=lambda sid: "tapparty")
    assert sent == [RC_LINE]
    assert (r.action, r.detail) == (restore.RESUMED, "resumed at @12")


def test_without_a_persisted_name_the_window_name_is_used(tmp_path, monkeypatch):
    proj = tmp_path / "proj"
    proj.mkdir()
    sent = _spy_tmux(monkeypatch, remote_control=True)
    _resume([_verdict(proj)], name_for=lambda sid: None)
    assert sent == [f"claude --remote-control proj --resume {SID}"]


def test_resume_reads_the_session_names_store_by_default(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "CHELA_DIR", tmp_path / "chela")
    assert session_names.record_name(SID, "tapparty")
    sent = _spy_tmux(monkeypatch, remote_control=True)
    _resume([_verdict(tmp_path)])
    assert sent == [RC_LINE]


def test_the_persisted_name_names_the_window_even_with_remote_control_off(
        tmp_path, monkeypatch):
    calls = []
    _resume([_verdict(tmp_path)], name_for=lambda sid: "tapparty",
            remote_control_on=lambda: False,
            spawn_window=lambda cwd, command, **kw: calls.append((command, kw))
            or SimpleNamespace(ok=True, wid="@5", error=None))
    assert calls == [(PLAIN, {"name": "tapparty"})]


# --------------------------------------------------------------------------
# off (the default): the command line is unchanged, and there is no extra attempt
# --------------------------------------------------------------------------

def test_rc_off_the_restore_command_line_is_unchanged(tmp_path, monkeypatch):
    sent = _spy_tmux(monkeypatch, remote_control=False)
    [r] = _resume([_verdict(tmp_path)], name_for=lambda sid: None)
    assert sent == [PLAIN]
    assert r.detail == "resumed at @12"


def test_rc_off_a_dead_plain_launch_is_not_retried(tmp_path, monkeypatch):
    sent = _spy_tmux(monkeypatch, remote_control=False)
    killed, failures = [], []
    [r] = restore.resume(
        [_verdict(tmp_path)], [], resume_blocked=lambda sid: None,
        record_resume_failure=lambda sid, why: failures.append(why),
        clear_resume_failure=lambda sid: None, archive=lambda e: None,
        remove_session=lambda *a: True, name_for=lambda sid: None,
        check_resumed=lambda wid, sid: False, kill_window=killed.append)
    assert sent == [PLAIN]
    assert killed == ["@12"]
    assert r.action == restore.RESUME_FAILED
    assert failures == [f"window @12 opened but claude --resume {SID} never came up alive"]


# --------------------------------------------------------------------------
# a refused --remote-control falls back to a plain launch, and says why
# --------------------------------------------------------------------------

def test_a_refused_remote_control_relaunches_without_it_and_logs_why(
        tmp_path, monkeypatch, caplog):
    _spy_tmux(monkeypatch, remote_control=True)
    sent = _all_sends(monkeypatch)
    killed = []
    with caplog.at_level("WARNING", logger="chela.restore"):
        [r] = _resume([_verdict(tmp_path)], name_for=lambda sid: "tapparty",
                      check_resumed=_checks(False, True), kill_window=killed.append)
    launches = [t for t in sent if not t.startswith(" ")]
    setups = [t for t in sent if t.startswith(" ")]
    assert launches == [RC_LINE, PLAIN]
    # the fallback shell does not source the Remote Control wrapper either
    assert [spawn.RC_SHELL_FILE.name in s for s in setups] == [True, False]
    assert killed == ["@12"]
    assert r.action == restore.RESUMED
    assert r.detail == ("resumed at @12, without Remote Control: the launch with "
                        "--remote-control failed (window @12 opened but never came up alive)")
    assert "did not come back with --remote-control" in caplog.text
    assert "relaunching without it" in caplog.text


def test_a_remote_control_launch_that_fails_to_spawn_falls_back_without_killing(tmp_path):
    results = iter([SimpleNamespace(ok=False, wid=None, error="boom"),
                    SimpleNamespace(ok=True, wid="@5", error=None)])
    calls, killed = [], []
    [r] = _resume([_verdict(tmp_path)], name_for=lambda sid: None,
                  remote_control_on=lambda: True, kill_window=killed.append,
                  spawn_window=lambda cwd, command, **kw: calls.append((command, kw))
                  or next(results))
    assert calls == [(PLAIN, {}), (PLAIN, {"allow_remote_control": False})]
    assert killed == []
    assert r.detail == ("resumed at @5, without Remote Control: the launch with "
                        "--remote-control failed (boom)")


def test_when_the_plain_launch_without_remote_control_also_dies_it_is_failed(tmp_path):
    failures, killed, calls = [], [], []
    [r] = restore.resume(
        [_verdict(tmp_path)], [], resume_blocked=lambda sid: None,
        record_resume_failure=lambda sid, why: failures.append(why),
        clear_resume_failure=lambda sid: None, archive=lambda e: None,
        remove_session=lambda *a: True, name_for=lambda sid: None,
        remote_control_on=lambda: True,
        spawn_window=lambda cwd, command, **kw: calls.append(kw) or SimpleNamespace(
            ok=True, wid=f"@{len(calls)}", error=None),
        check_resumed=lambda wid, sid: False, kill_window=killed.append)
    assert calls == [{}, {"allow_remote_control": False}]
    assert killed == ["@1", "@2"]
    assert r.action == restore.RESUME_FAILED
    assert failures == [f"window @2 opened but claude --resume {SID} never came up alive"]


def test_a_final_spawn_failure_after_a_refusal_is_failed_with_its_error(tmp_path):
    results = iter([SimpleNamespace(ok=True, wid="@1", error=None),
                    SimpleNamespace(ok=False, wid=None, error="tmux gone")])
    failures, killed = [], []
    [r] = restore.resume(
        [_verdict(tmp_path)], [], resume_blocked=lambda sid: None,
        record_resume_failure=lambda sid, why: failures.append(why),
        clear_resume_failure=lambda sid: None, archive=lambda e: None,
        remove_session=lambda *a: True, name_for=lambda sid: None,
        remote_control_on=lambda: True,
        spawn_window=lambda cwd, command, **kw: next(results),
        check_resumed=lambda wid, sid: False, kill_window=killed.append)
    assert killed == ["@1"]
    assert (r.action, r.detail) == (restore.RESUME_FAILED, "tmux gone")
    assert failures == ["tmux gone"]


def _settings_and_rc(tmp_path, *alive):
    calls, killed = [], []
    [r] = _resume([_verdict(tmp_path)], name_for=lambda sid: "tapparty",
                  remote_control_on=lambda: True,
                  launch_settings_for=lambda sid: {"flags": ["--model", "opus"]},
                  replay_settings=lambda s: (["--model", "opus"], {}, [], []),
                  check_resumed=_checks(*alive), kill_window=killed.append,
                  spawn_window=lambda cwd, command, **kw: calls.append((command, kw))
                  or SimpleNamespace(ok=True, wid=f"@{len(calls)}", error=None))
    return r, calls, killed


SETTINGS = {"name": "tapparty", "launch_env": None, "path_prefix": None}
NO_RC = {"allow_remote_control": False}


def test_a_refused_remote_control_keeps_the_recorded_settings(tmp_path, caplog):
    """Settings AND Remote Control: a refusal drops Remote Control, never the settings."""
    with caplog.at_level("WARNING", logger="chela.restore"):
        r, calls, killed = _settings_and_rc(tmp_path, False, True)
    assert calls == [(f"{PLAIN} --model opus", SETTINGS),
                     (f"{PLAIN} --model opus", {**SETTINGS, **NO_RC})]
    assert killed == ["@1"]
    assert r.detail == ("resumed at @2 with its launch settings, without Remote Control: the "
                        "launch with --remote-control failed (window @1 opened but never came "
                        "up alive)")
    assert "relaunching without it" in caplog.text
    assert "falling back to a plain --resume" not in caplog.text


def test_settings_and_remote_control_every_fallback_in_order(tmp_path, caplog):
    with caplog.at_level("WARNING", logger="chela.restore"):
        r, calls, killed = _settings_and_rc(tmp_path, False, False, False, True)
    assert calls == [(f"{PLAIN} --model opus", SETTINGS),
                     (f"{PLAIN} --model opus", {**SETTINGS, **NO_RC}),
                     (PLAIN, {"name": "tapparty"}),
                     (PLAIN, {"name": "tapparty", **NO_RC})]
    assert killed == ["@1", "@2", "@3"]
    assert r.detail == (
        "resumed at @4, plain --resume: its launch settings failed (window @2 opened but "
        "never came up alive), without Remote Control: the launch with --remote-control "
        "failed (window @1 opened but never came up alive)")
    assert caplog.text.count("relaunching without it") == 2
    assert caplog.text.count("falling back to a plain --resume") == 1


def test_bad_settings_alone_keep_remote_control(tmp_path):
    r, calls, killed = _settings_and_rc(tmp_path, False, False, True)
    assert calls[2] == (PLAIN, {"name": "tapparty"})
    assert r.detail == ("resumed at @3, plain --resume: its launch settings failed (window @2 "
                        "opened but never came up alive)")


def test_resume_reads_the_remote_control_setting_by_default(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "remote_control_enabled", lambda: True)
    calls = []
    _resume([_verdict(tmp_path)], name_for=lambda sid: None,
            check_resumed=_checks(False, True), kill_window=lambda wid: None,
            spawn_window=lambda cwd, command, **kw: calls.append(kw)
            or SimpleNamespace(ok=True, wid="@5", error=None))
    assert calls == [{}, {"allow_remote_control": False}]


# --------------------------------------------------------------------------
# spawn_window: allow_remote_control=False beats the setting and a recorded name
# --------------------------------------------------------------------------

@pytest.mark.parametrize("allow, setting, rc_name, expected", [
    (True, True, None, "claude --remote-control proj"),
    (True, False, None, "claude"),
    (True, False, "kept", "claude --remote-control kept"),
    (False, True, None, "claude"),
    (False, True, "kept", "claude"),
    (False, False, "kept", "claude"),
])
def test_spawn_remote_control_precedence(tmp_path, monkeypatch, allow, setting, rc_name,
                                         expected):
    proj = tmp_path / "proj"
    proj.mkdir()
    _spy_tmux(monkeypatch, remote_control=setting)
    marked = []
    monkeypatch.setattr(spawn.rc_rename, "mark_launched", lambda *a: marked.append(a))
    monkeypatch.setattr(spawn, "_pin_session_id", lambda command, sid: (command, None))
    sent = _all_sends(monkeypatch)
    assert spawn.spawn_window(proj, command="claude", remote_control_name=rc_name,
                              allow_remote_control=allow).ok
    assert [t for t in sent if not t.startswith(" ")] == [expected]
    wrapped = any(spawn.RC_SHELL_FILE.name in t for t in sent if t.startswith(" "))
    assert wrapped is (allow and setting)
    assert bool(marked) is (expected != "claude")


# --------------------------------------------------------------------------
# session_names.name_for
# --------------------------------------------------------------------------

def test_name_for(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "CHELA_DIR", tmp_path)
    assert session_names.name_for(SID) is None              # no store
    (tmp_path / "session-names.json").write_text(json.dumps({
        SID: {"name": "tapparty", "ts": 1e12},
        "only-title": {"title": "t", "ts": 1e12},
        "bad": {"name": 7},
        "empty": {"name": ""},
    }))
    assert session_names.name_for(SID) == "tapparty"
    assert session_names.name_for("only-title") is None
    assert session_names.name_for("bad") is None
    assert session_names.name_for("empty") is None
    assert session_names.name_for("missing") is None
    assert session_names.name_for(None) is None
    assert session_names.name_for("") is None
