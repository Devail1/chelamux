"""CMX-372 — restore brings a session back with the launch flags and env it ran with.

The fixture is the operator's boot-time stopgap: a game session relaunched as

    env PATH=$HOME/.bun/bin:$PATH DISCORD_STATE_DIR=<state dir> \\
        claude --remote-control <name> --resume <sid> --channels plugin:discord@claude-plugins-official

Recording reads a fake ``/proc`` (argv + environ); replay goes through the real
:func:`chela.restore.resume` and the real :func:`chela.spawn.spawn_window`, with tmux's
``new-window`` and ``send-keys`` replaced by spies — never a real tmux call.
"""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from chela import launch_settings as ls
from chela import restore, sessions, spawn

SID = "aaaaaaaa-1111-2222-3333-444444444444"
CHANNEL = "plugin:discord@claude-plugins-official"
PREV, NOW = "100-1700000000", "200-1800000000"
BASE_PATH = "/usr/local/bin:/usr/bin:/bin"


def _stopgap_argv(name="teeter-proto", sid=SID):
    return ["claude", "--remote-control", name, "--resume", sid, "--channels", CHANNEL]


def _stopgap_environ(bun, state="/home/u/.claude/channels/discord-tetree"):
    return {
        "PATH": f"{bun}:{BASE_PATH}",
        "HOME": "/home/u",
        "DISCORD_STATE_DIR": state,
        "DISCORD_BOT_TOKEN": "tok-SHOULD-NEVER-BE-STORED",
        "GITHUB_TOKEN": "ghp-SHOULD-NEVER-BE-STORED",
        "ANTHROPIC_API_KEY": "sk-SHOULD-NEVER-BE-STORED",
        "MY_SECRET": "secret-SHOULD-NEVER-BE-STORED",
    }


@pytest.fixture
def store(tmp_path, monkeypatch):
    path = tmp_path / "launch-settings.json"
    monkeypatch.setattr(ls, "_STORE", path)
    monkeypatch.setenv("PATH", BASE_PATH)
    monkeypatch.delenv(ls.ENV_EXTRA, raising=False)
    return path


@pytest.fixture
def plugins_dir(tmp_path, monkeypatch):
    cfg = tmp_path / "claude-config"
    (cfg / "plugins").mkdir(parents=True)
    (cfg / "plugins" / "installed_plugins.json").write_text(json.dumps(
        {"version": 2, "plugins": {"discord@claude-plugins-official": []}}))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(cfg))
    return cfg


def _fake_proc(tmp_path, monkeypatch, pid, argv, environ):
    proc = tmp_path / "proc"
    (proc / str(pid)).mkdir(parents=True)
    (proc / str(pid) / "cmdline").write_bytes(b"\0".join(a.encode() for a in argv) + b"\0")
    (proc / str(pid) / "environ").write_bytes(
        b"\0".join(f"{k}={v}".encode() for k, v in environ.items()) + b"\0")
    monkeypatch.setattr(sessions, "PROC", proc)


# --------------------------------------------------------------------------
# record
# --------------------------------------------------------------------------

def test_capture_keeps_the_channel_flag_the_state_dir_and_the_path_prefix_only():
    got = ls.capture(_stopgap_argv(), _stopgap_environ("/home/u/.bun/bin"), BASE_PATH, "")
    assert got == {
        "flags": ["--channels", CHANNEL],
        "env": {"DISCORD_STATE_DIR": "/home/u/.claude/channels/discord-tetree"},
        "path_prefix": ["/home/u/.bun/bin"],
    }


def test_a_token_like_var_is_never_stored(tmp_path, monkeypatch, store):
    """⛔ the whole point of the allowlist: the store's bytes never carry a credential."""
    bun = str(tmp_path / "bun")
    _fake_proc(tmp_path, monkeypatch, 4242, _stopgap_argv(), _stopgap_environ(bun))
    ls.record_windows({"@3": {"session_id": SID, "pid": 4242}})
    raw = store.read_text()
    assert "SHOULD-NEVER-BE-STORED" not in raw
    for name in ("DISCORD_BOT_TOKEN", "GITHUB_TOKEN", "ANTHROPIC_API_KEY", "MY_SECRET", "HOME"):
        assert name not in raw
    assert json.loads(raw)[SID]["env"] == {
        "DISCORD_STATE_DIR": "/home/u/.claude/channels/discord-tetree"}


def test_an_operator_allowlisted_name_is_kept_but_a_secret_shaped_one_never_is():
    environ = {"GAME_PORT": "8080", "DISCORD_BOT_TOKEN": "t", "DB_PASSWORD": "p", "X": "1"}
    got = ls.capture(["claude", "--model", "opus"], environ, BASE_PATH,
                     "GAME_PORT, DISCORD_BOT_TOKEN DB_PASSWORD bad-name")
    assert got["env"] == {"GAME_PORT": "8080"}
    assert ls.allowed_env("GAME_PORT DISCORD_BOT_TOKEN DB_PASSWORD bad-name") == frozenset(
        {"DISCORD_STATE_DIR", "GAME_PORT"})


def test_the_operator_allowlist_is_read_from_the_environment(monkeypatch):
    monkeypatch.setenv("CHELA_RESTORE_ENV", "GAME_PORT")
    assert ls.allowed_env() == frozenset({"DISCORD_STATE_DIR", "GAME_PORT"})
    monkeypatch.delenv("CHELA_RESTORE_ENV")
    assert ls.allowed_env() == frozenset({"DISCORD_STATE_DIR"})


@pytest.mark.parametrize("args,flags", [
    (["--remote-control", "n", "--resume", SID, "--channels", CHANNEL], ["--channels", CHANNEL]),
    (["--channels=" + CHANNEL], ["--channels", CHANNEL]),
    (["--channels", "a", "b", "--model", "opus", "fix the bug"],
     ["--channels", "a", "b", "--model", "opus"]),
    (["--model=opus", "a prompt"], ["--model", "opus"]),
    (["--model", "opus", "a prompt"], ["--model", "opus"]),
    (["--dangerously-skip-permissions", "a prompt"], ["--dangerously-skip-permissions"]),
    (["--dangerously-skip-permissions=1"], []),
    (["--model"], []),                         # a value-taking flag with no value
    (["--model", "--channels", "x"], ["--channels", "x"]),
    (["--unknown", "value", "--session-id", SID, "-r", SID, "a prompt"], []),
    (["--channels", "line\nbreak"], []),
    ([], []),
])
def test_parse_flags_keeps_only_allowlisted_flags_and_never_a_positional(args, flags):
    assert ls.parse_flags(args) == flags


@pytest.mark.parametrize("argv,args", [
    (["claude", "--model", "x"], ["--model", "x"]),
    (["/home/u/.local/bin/claude", "--model", "x"], ["--model", "x"]),
    (["node", "/opt/claude-code/cli.js", "--model", "x"], ["--model", "x"]),
    (["node", "--claude", "x"], []),
    (["bash"], []),
])
def test_claude_args_starts_after_the_claude_executable(argv, args):
    assert ls._claude_args(argv) == args


def test_a_path_difference_alone_is_not_a_launch_setting():
    assert ls.capture(["claude"], {"PATH": f"/x/bin:{BASE_PATH}"}, BASE_PATH, "") == {}


def test_path_prefix_keeps_absolute_new_entries_once_in_order():
    environ = {"PATH": f"/b:rel:/a:/b::{BASE_PATH}", "DISCORD_STATE_DIR": "/s"}
    assert ls.capture(["claude"], environ, BASE_PATH, "")["path_prefix"] == ["/b", "/a"]


def test_capture_defaults_its_baseline_to_chelas_own_path(monkeypatch):
    monkeypatch.setenv("PATH", "/x/bin:" + BASE_PATH)
    got = ls.capture(["claude"], {"PATH": f"/x/bin:/y/bin:{BASE_PATH}",
                                  "DISCORD_STATE_DIR": "/s"}, None, "")
    assert got["path_prefix"] == ["/y/bin"]


def test_record_windows_skips_unidentified_windows_and_unreadable_processes(store):
    argv = {7: _stopgap_argv(), 8: []}
    out = ls.record_windows(
        {"@1": {"session_id": None, "pid": 7}, "@2": {"session_id": SID, "pid": None},
         "@3": {"session_id": "bbbbbbbb-1111-2222-3333-444444444444", "pid": 8}},
        argv_for=argv.get, environ_for=lambda pid: {"DISCORD_STATE_DIR": "/s"})
    assert out == {}
    assert not store.exists()


def test_record_windows_records_then_clears_when_the_session_runs_without_extras(store):
    env = {"DISCORD_STATE_DIR": "/s"}
    ls.record_windows({"@3": {"session_id": SID, "pid": 7}},
                      argv_for=lambda pid: _stopgap_argv(), environ_for=lambda pid: env,
                      now=1.0)
    assert json.loads(store.read_text()) == {
        SID: {"flags": ["--channels", CHANNEL], "env": env, "seen": 1.0}}
    ls.record_windows({"@3": {"session_id": SID, "pid": 9}},
                      argv_for=lambda pid: ["claude", "--resume", SID],
                      environ_for=lambda pid: {"PATH": BASE_PATH})
    assert json.loads(store.read_text()) == {}


def test_record_windows_writes_only_when_something_changed(store, monkeypatch):
    saves = []
    real_save = ls._save
    monkeypatch.setattr(ls, "_save", lambda data, path=None: saves.append(1) or real_save(
        data, path))
    kw = {"argv_for": lambda pid: _stopgap_argv(),
          "environ_for": lambda pid: {"PATH": BASE_PATH}, "now": 1.0}
    ls.record_windows({"@3": {"session_id": SID, "pid": 7}}, **kw)
    ls.record_windows({"@3": {"session_id": SID, "pid": 7}}, **{**kw, "now": 2.0})
    assert saves == [1]
    ls.record_windows({"@3": {"session_id": SID, "pid": 7}},
                      **{**kw, "argv_for": lambda pid: ["claude", "--model", "opus"]})
    assert saves == [1, 1]
    assert json.loads(store.read_text())[SID]["flags"] == ["--model", "opus"]


def test_an_unreadable_process_keeps_its_record(store):
    """A process that exited mid-read (environ or argv unreadable) is unknown, not "no
    extras" — its record must survive."""
    store.write_text(json.dumps({SID: {"env": {"DISCORD_STATE_DIR": "/s"}, "seen": 1}}))
    w = {"@3": {"session_id": SID, "pid": 7}}
    ls.record_windows(w, argv_for=lambda pid: ["claude"], environ_for=lambda pid: {})
    ls.record_windows(w, argv_for=lambda pid: [], environ_for=lambda pid: {"PATH": "/bin"})
    assert json.loads(store.read_text())[SID]["env"] == {"DISCORD_STATE_DIR": "/s"}


def test_the_cap_never_evicts_a_session_live_this_tick(store, monkeypatch):
    monkeypatch.setattr(ls, "_MAX_ENTRIES", 2)
    store.write_text(json.dumps({SID: {"flags": ["--model", "a"], "seen": 1},
                                 "mid": {"flags": ["--model", "b"], "seen": 2},
                                 "new": {"flags": ["--model", "c"], "seen": 3}}))
    ls.record_windows({"@3": {"session_id": SID, "pid": 7}},
                      argv_for=lambda pid: ["claude", "--model", "a"],
                      environ_for=lambda pid: {"PATH": BASE_PATH})
    assert set(json.loads(store.read_text())) == {SID, "new"}


def test_the_store_is_private_to_its_owner(store):
    store.write_text("{}")
    store.chmod(0o644)
    ls.record_windows({"@3": {"session_id": SID, "pid": 7}},
                      argv_for=lambda pid: _stopgap_argv(),
                      environ_for=lambda pid: {"PATH": BASE_PATH})
    assert store.stat().st_mode & 0o777 == 0o600


def test_record_windows_keeps_the_store_bounded_dropping_the_oldest(store, monkeypatch):
    monkeypatch.setattr(ls, "_MAX_ENTRIES", 2)
    store.write_text(json.dumps({"old": {"flags": ["--model", "a"], "seen": 1},
                                 "mid": {"flags": ["--model", "b"], "seen": 2}}))
    ls.record_windows({"@3": {"session_id": SID, "pid": 7}},
                      argv_for=lambda pid: _stopgap_argv(),
                      environ_for=lambda pid: {"PATH": BASE_PATH}, now=3.0)
    assert set(json.loads(store.read_text())) == {"mid", SID}


def test_read_environ_reads_proc_and_is_empty_when_it_cannot(tmp_path, monkeypatch):
    _fake_proc(tmp_path, monkeypatch, 5, ["claude"], {"A": "1", "B": "x=y"})
    assert ls.read_environ(5) == {"A": "1", "B": "x=y"}
    assert ls.read_environ(6) == {}


@pytest.mark.parametrize("content", [None, "not json", "[1]", json.dumps({SID: "x"})])
def test_for_session_is_empty_when_there_is_no_usable_record(store, content):
    if content is not None:
        store.write_text(content)
    assert ls.for_session(SID) == {}
    assert ls.for_session(None) == {}


@pytest.mark.parametrize("content,expected", [
    (json.dumps({"plugins": {"a@m": [], "b@m": []}}), {"a@m", "b@m"}),
    (json.dumps({"plugins": []}), None),
    ("[]", None),
    ("not json", None),
    (None, None),
])
def test_installed_plugins(tmp_path, monkeypatch, content, expected):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    (tmp_path / "plugins").mkdir()
    if content is not None:
        (tmp_path / "plugins" / "installed_plugins.json").write_text(content)
    assert ls.installed_plugins() == expected


# --------------------------------------------------------------------------
# replay
# --------------------------------------------------------------------------

REC = {"flags": ["--channels", CHANNEL], "env": {"DISCORD_STATE_DIR": "/s"},
       "path_prefix": ["/bun"]}


def test_replay_returns_what_was_recorded_when_everything_still_exists():
    assert ls.replay(REC, plugins={"discord@claude-plugins-official"}, isdir=lambda p: True) == (
        ["--channels", CHANNEL], {"DISCORD_STATE_DIR": "/s"}, ["/bun"], [])


def test_replay_drops_everything_when_the_channel_plugin_is_not_installed():
    flags, env, path, notes = ls.replay(REC, plugins={"other@m"}, isdir=lambda p: True)
    assert (flags, env, path) == ([], {}, [])
    assert notes == ["plugin discord@claude-plugins-official is not installed — resuming "
                     "without the recorded launch settings"]


def test_replay_reads_the_installed_plugins_by_default(plugins_dir):
    assert ls.replay(REC, isdir=lambda p: True)[0] == ["--channels", CHANNEL]
    (plugins_dir / "plugins" / "installed_plugins.json").write_text(
        json.dumps({"plugins": {}}))
    assert ls.replay(REC, isdir=lambda p: True)[0] == []


def test_an_unknown_plugin_list_is_not_read_as_missing(monkeypatch):
    monkeypatch.setattr(ls, "installed_plugins", lambda: None)
    assert ls.replay(REC, isdir=lambda p: True)[0] == ["--channels", CHANNEL]


def test_a_non_plugin_channel_needs_no_installed_plugin():
    rec = {"flags": ["--channels", "server:local", "--model", "opus"]}
    assert ls.replay(rec, plugins=set())[0] == ["--channels", "server:local", "--model", "opus"]


def test_replay_drops_a_vanished_path_entry_and_a_no_longer_allowlisted_name():
    rec = {**REC, "env": {"DISCORD_STATE_DIR": "/s", "GITHUB_TOKEN": "t", "OLD": "1"},
           "path_prefix": ["/bun", "/gone", "rel", 3]}
    flags, env, path, notes = ls.replay(
        rec, plugins={"discord@claude-plugins-official"}, isdir=lambda p: p == "/bun")
    assert env == {"DISCORD_STATE_DIR": "/s"}
    assert path == ["/bun"]
    assert notes == ["env not replayed (not allowlisted): GITHUB_TOKEN, OLD",
                     "PATH entry '/gone' is no longer a directory — left out",
                     "PATH entry 'rel' is no longer a directory — left out",
                     "PATH entry 3 is no longer a directory — left out"]


def test_replay_of_a_malformed_record_is_empty():
    assert ls.replay({"flags": "x", "env": [], "path_prefix": {}}) == ([], {}, [], [])


# --------------------------------------------------------------------------
# spawn: the env prefix
# --------------------------------------------------------------------------

def test_env_prefix_literal():
    assert spawn._env_prefix(None, None) == ""
    assert spawn._env_prefix({}, []) == ""
    assert spawn._env_prefix({"DISCORD_STATE_DIR": "/a b"}, ["/x", "/y"]) == (
        "env PATH=/x:/y:\"$PATH\" DISCORD_STATE_DIR='/a b' ")
    assert spawn._env_prefix({"A": "1"}, None) == "env A=1 "


class _Proc:
    def __init__(self, stdout):
        self.returncode, self.stdout, self.stderr = 0, stdout, ""


def _spy_tmux(monkeypatch, wid="@12", remote_control=True):
    """Replace every tmux touch in spawn_window: new-window answers ``wid``, send-keys is
    recorded. Returns the list of sent lines (setup line excluded)."""
    sent: list[str] = []
    runs: list[list[str]] = []
    monkeypatch.setattr(spawn.discovery, "ensure_session", lambda: True)
    monkeypatch.setattr(spawn.discovery, "get_all_windows", lambda: {})
    monkeypatch.setattr(spawn.envutil, "scrub_tmux_secrets", lambda: [])
    monkeypatch.setattr(spawn.agent_manager, "lock_window_name", lambda *a, **k: None)
    monkeypatch.setattr(spawn.rc_rename, "mark_launched", lambda *a, **k: None)
    monkeypatch.setattr(spawn.config, "remote_control_enabled", lambda: remote_control)
    monkeypatch.setattr(spawn.subprocess, "run",
                        lambda argv, **k: runs.append(argv) or _Proc(wid))
    monkeypatch.setattr(spawn, "_send",
                        lambda target, text: None if text.startswith(" ") else sent.append(text))
    return sent


def _resume(verdicts, **kw):
    kw.setdefault("check_resumed", lambda wid, sid: True)
    return restore.resume(
        verdicts, [], resume_blocked=lambda sid: None, record_resume_failure=lambda *a: None,
        clear_resume_failure=lambda sid: None, archive=lambda e: None,
        remove_session=lambda *a: True, **kw)


def _verdict(cwd, label="teeter-proto"):
    return restore.Verdict("session-ids", "@3", PREV, "MANUAL", SID, None, str(cwd), label)


# --------------------------------------------------------------------------
# ACCEPTANCE — the real restore path, the stopgap's exact launch line
# --------------------------------------------------------------------------

def test_a_recorded_discord_session_comes_back_with_its_channel_and_state_dir(
        tmp_path, monkeypatch, store, plugins_dir):
    """Recorded from the live process, replayed by the real restore + real spawn_window.
    The line sent into the pane is the stopgap's launch line (discord-relaunch.sh)."""
    game = tmp_path / "teeter-proto"
    game.mkdir()
    bun = tmp_path / "bun" / "bin"
    bun.mkdir(parents=True)
    state = "/home/u/.claude/channels/discord-tetree"
    _fake_proc(tmp_path, monkeypatch, 4242, _stopgap_argv(), _stopgap_environ(bun, state))
    ls.record_windows({"@3": {"session_id": SID, "pid": 4242}})

    sent = _spy_tmux(monkeypatch)
    [r] = _resume([_verdict(game)])

    assert sent == [f'env PATH={bun}:"$PATH" DISCORD_STATE_DIR={state} '
                    f"claude --remote-control teeter-proto --resume {SID} --channels {CHANNEL}"]
    assert (r.action, r.detail) == (restore.RESUMED, "resumed at @12 with its launch settings")


def test_a_default_state_dir_session_comes_back_with_its_channel_only(
        tmp_path, monkeypatch, store, plugins_dir):
    """glide-proto in the stopgap: the default state dir, so no DISCORD_STATE_DIR."""
    game = tmp_path / "glide-proto"
    game.mkdir()
    environ = _stopgap_environ("/nonexistent/bun")
    del environ["DISCORD_STATE_DIR"]
    _fake_proc(tmp_path, monkeypatch, 77, _stopgap_argv("glide-proto"), environ)
    ls.record_windows({"@4": {"session_id": SID, "pid": 77}})
    sent = _spy_tmux(monkeypatch)
    _resume([_verdict(game, "glide-proto")])
    assert sent == [f"claude --remote-control glide-proto --resume {SID} --channels {CHANNEL}"]


def test_a_session_with_no_recorded_settings_restores_exactly_as_today(tmp_path, monkeypatch,
                                                                        store):
    game = tmp_path / "five"
    game.mkdir()
    sent = _spy_tmux(monkeypatch)
    calls = []
    real = spawn.spawn_window

    def spy(cwd, command):           # today's exact call shape: no extra keyword at all
        calls.append((cwd, command))
        return real(cwd, command=command)

    [r] = _resume([_verdict(game, "five")], spawn_window=spy)
    assert calls == [(str(game), f"claude --resume {SID}")]
    assert sent == [f"claude --remote-control five --resume {SID}"]
    assert r.detail == "resumed at @12"


def test_the_tmux_restart_path_replays_the_settings_too(tmp_path, store):
    """The boot pass (the "tmux restarted — came back" notice) runs the same resume."""
    store.write_text(json.dumps({SID: REC}))
    game = tmp_path / "teeter-proto"
    spawned, notices = [], []

    def spawn_spy(cwd, command, **kw):
        spawned.append((cwd, command, kw))
        return SimpleNamespace(ok=True, wid="@9", error=None)

    marker = tmp_path / "m.json"
    marker.write_text(json.dumps({"epoch": PREV}))
    resume_fn = lambda rows, runs: _resume(rows, spawn_window=spawn_spy,  # noqa: E731
                                           replay_settings=lambda s: ls.replay(
                                               s, plugins={"discord@claude-plugins-official"},
                                               isdir=lambda p: True))
    restore.boot_pass(NOW, resume_enabled=True, gather=lambda: ([_verdict(game)], []),
                      marker=marker, resume_fn=resume_fn,
                      notify=lambda *a: notices.append(a[1]), roster_epochs=lambda now: [],
                      wid_for_session=lambda sid: None, closed_lookup=lambda eps: {})
    assert spawned == [(str(game), f"claude --resume {SID} --channels {CHANNEL}",
                        {"launch_env": {"DISCORD_STATE_DIR": "/s"}, "path_prefix": ["/bun"]})]
    assert notices == ["tmux restarted — came back: teeter-proto (resumed at @9 with its "
                       "launch settings); not resumed: none"]


# --------------------------------------------------------------------------
# fallback — the extras must never keep a session down
# --------------------------------------------------------------------------

def _replaying(rec=REC):
    return {"launch_settings_for": lambda sid: rec,
            "replay_settings": lambda s: ls.replay(
                s, plugins={"discord@claude-plugins-official"}, isdir=lambda p: True)}


def test_a_replayed_launch_that_dies_falls_back_to_a_plain_resume(tmp_path, caplog):
    spawned, killed = [], []

    def spawn_spy(cwd, command, **kw):
        spawned.append((command, kw))
        return SimpleNamespace(ok=True, wid=f"@{len(spawned) + 10}", error=None)

    alive = {"@11": False, "@12": True}
    with caplog.at_level("WARNING", logger="chela.restore"):
        [r] = _resume([_verdict(tmp_path)], spawn_window=spawn_spy,
                      check_resumed=lambda wid, sid: alive[wid],
                      kill_window=killed.append, **_replaying())
    assert spawned == [
        (f"claude --resume {SID} --channels {CHANNEL}",
         {"launch_env": {"DISCORD_STATE_DIR": "/s"}, "path_prefix": ["/bun"]}),
        (f"claude --resume {SID}", {}),
    ]
    assert killed == ["@11"]
    assert r.action == restore.RESUMED
    assert r.detail == ("resumed at @12, plain --resume: its launch settings failed "
                        "(window @11 opened but never came up alive)")
    assert "falling back to a plain --resume" in caplog.text


def test_a_replayed_launch_that_fails_to_spawn_falls_back_without_killing(tmp_path):
    results = iter([SimpleNamespace(ok=False, wid=None, error="boom"),
                    SimpleNamespace(ok=True, wid="@5", error=None)])
    spawned, killed = [], []
    [r] = _resume([_verdict(tmp_path)],
                  spawn_window=lambda cwd, command, **kw: spawned.append(command) or next(
                      results),
                  kill_window=killed.append, **_replaying())
    assert spawned == [f"claude --resume {SID} --channels {CHANNEL}", f"claude --resume {SID}"]
    assert killed == []
    assert r.detail == "resumed at @5, plain --resume: its launch settings failed (boom)"


def test_when_the_plain_fallback_also_dies_the_session_is_reported_failed(tmp_path):
    failures, killed = [], []
    [r] = restore.resume(
        [_verdict(tmp_path)], [], resume_blocked=lambda sid: None,
        record_resume_failure=lambda sid, why: failures.append(why),
        clear_resume_failure=lambda sid: None, archive=lambda e: None,
        remove_session=lambda *a: True,
        spawn_window=lambda cwd, command, **kw: SimpleNamespace(ok=True, wid="@7", error=None),
        check_resumed=lambda wid, sid: False, kill_window=killed.append, **_replaying())
    assert r.action == restore.RESUME_FAILED
    assert killed == ["@7", "@7"]
    assert failures == [f"window @7 opened but claude --resume {SID} never came up alive"]


def test_a_missing_plugin_resumes_plain_and_says_why(tmp_path, caplog):
    spawned = []
    with caplog.at_level("WARNING", logger="chela.restore"):
        [r] = _resume([_verdict(tmp_path)],
                      spawn_window=lambda cwd, command, **kw: spawned.append((command, kw))
                      or SimpleNamespace(ok=True, wid="@5", error=None),
                      launch_settings_for=lambda sid: REC,
                      replay_settings=lambda s: ls.replay(s, plugins=set()))
    assert spawned == [(f"claude --resume {SID}", {})]
    assert r.detail == ("resumed at @5, plain --resume: plugin discord@claude-plugins-official "
                        "is not installed — resuming without the recorded launch settings")
    assert "is not installed" in caplog.text


def test_a_replayed_launch_that_came_up_alive_is_not_checked_again(tmp_path):
    """One liveness check per launch: a confirmed replay goes straight to the success path."""
    checks = []

    def check(wid, sid):
        checks.append(wid)
        return len(checks) == 1          # alive the first time it is asked, never again

    [r] = _resume([_verdict(tmp_path)],
                  spawn_window=lambda cwd, command, **kw: SimpleNamespace(
                      ok=True, wid="@5", error=None),
                  check_resumed=check, kill_window=lambda wid: None, **_replaying())
    assert checks == ["@5"]
    assert (r.action, r.detail) == (restore.RESUMED, "resumed at @5 with its launch settings")


def test_resume_reads_the_recorded_settings_by_default(tmp_path, store, monkeypatch):
    store.write_text(json.dumps({SID: {"flags": ["--model", "opus"]}}))
    spawned = []
    [r] = _resume([_verdict(tmp_path)],
                  spawn_window=lambda cwd, command, **kw: spawned.append((command, kw))
                  or SimpleNamespace(ok=True, wid="@5", error=None))
    assert spawned == [(f"claude --resume {SID} --model opus",
                        {"launch_env": None, "path_prefix": None})]


# --------------------------------------------------------------------------
# the reconcile tick records them
# --------------------------------------------------------------------------

def test_the_reconcile_tick_records_the_snapshots_windows(monkeypatch):
    from tests.test_telegram_reconcile import _run_one_tick
    seen = []
    monkeypatch.setattr(ls, "record_windows", lambda windows: seen.append(windows))
    windows = {"@9": {"session_id": SID, "pid": 7}}
    _run_one_tick(monkeypatch, roster_returns={"windows": windows})
    assert seen == [windows]


def test_the_reconcile_tick_survives_a_recording_failure(monkeypatch):
    from tests.test_telegram_reconcile import _run_one_tick

    def boom(windows):
        raise RuntimeError("disk full")
    monkeypatch.setattr(ls, "record_windows", boom)
    seen = _run_one_tick(monkeypatch, roster_returns={"windows": {}})
    assert seen["stop"].waited == [7]          # the tick ran to its end


def test_the_reconcile_tick_records_nothing_without_a_snapshot(monkeypatch):
    from tests.test_telegram_reconcile import _run_one_tick
    seen = []
    monkeypatch.setattr(ls, "record_windows", lambda windows: seen.append(windows))
    _run_one_tick(monkeypatch, roster_returns=None)
    assert seen == []
