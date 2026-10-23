"""CMX-319: ``--pause --all`` also stops ``pause_all.extra_services`` (``~/.chela/config.json``)
and ``--resume`` starts them again in ``pause_all.resume_order``.

pm2 is a FAKE ``pm2`` executable first on PATH: it logs each argv and the env it was run with,
and keeps each service's status in a JSON file, so the tests see what ``env -i`` really passed
and what a real ``jlist`` would answer. No agent is running (``dispatcher.list_runs`` is []),
so every pm2 call in a test is the service half of the pause.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time

import pytest

from chela import config, dispatcher, fleet, hold, inbox, main, messenger, userconfig
from chela.telegram import fleetcmd

FAKE_PM2 = f"""#!{sys.executable}
import json, os, sys
home = os.environ["HOME"]
with open(os.path.join(home, "pm2.log"), "a") as f:
    f.write(json.dumps({{"argv": sys.argv[1:], "env": dict(os.environ)}}) + "\\n")
db = os.path.join(home, "pm2.json")
procs = json.load(open(db))
cmd, *rest = sys.argv[1:]
if cmd == "jlist":
    if os.path.exists(os.path.join(home, "jlist-broken")):
        print("[PM2] Spawning PM2 daemon")
        sys.exit(0)
    print(json.dumps([{{"name": n, "pm2_env": {{"status": s}}}} for n, s in procs.items()]))
    sys.exit(0)
name = rest[0]
fail = os.path.join(home, "fail-" + cmd + "-" + name)
if os.path.exists(fail) or name not in procs:
    sys.exit(1)
procs[name] = "online" if cmd == "start" else "stopped"
json.dump(procs, open(db, "w"))
"""

CONFIG = {"extra_services": ["game-agent", "game-tracker", "game-mirror"],
          "resume_order": ["game-mirror", "game-agent", "game-tracker"]}


@pytest.fixture
def pm2(tmp_path, monkeypatch):
    home, bindir = tmp_path / "home", tmp_path / "bin"
    home.mkdir()
    bindir.mkdir()
    exe = bindir / "pm2"
    exe.write_text(FAKE_PM2)
    exe.chmod(0o755)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("PATH", f"{bindir}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("PM2_HOME", str(tmp_path / "pm2home"))
    monkeypatch.setattr(config, "CHELA_DIR", tmp_path / "chela")
    monkeypatch.setattr(userconfig, "_PATH", tmp_path / "chela" / "config.json")
    monkeypatch.setattr(dispatcher, "list_runs", lambda: [])
    monkeypatch.setattr(inbox, "_selfcheck_scan", lambda: [])
    monkeypatch.setattr(messenger, "send_escape", lambda wid: True)
    monkeypatch.setattr(messenger, "send_message", lambda *a: True)

    class World:
        def set(self, **procs):
            (home / "pm2.json").write_text(json.dumps(procs))

        def status(self):
            return json.loads((home / "pm2.json").read_text())

        def calls(self):
            log = home / "pm2.log"
            if not log.exists():
                return []
            return [json.loads(line) for line in log.read_text().splitlines()]

        def argvs(self):
            return [c["argv"] for c in self.calls()]

        def clear(self):
            (home / "pm2.log").unlink(missing_ok=True)

        def fail(self, cmd, name):
            (home / f"fail-{cmd}-{name}").touch()

        def break_jlist(self):
            (home / "jlist-broken").touch()

        def configure(self, value):
            userconfig.set_("pause_all", value)

    w = World()
    w.set(**{"game-agent": "online", "game-tracker": "online", "game-mirror": "online",
             "unrelated": "online"})
    return w


def _cli(**over):
    args = dict(pause=False, resume=False, hold_status=False, all=False, reason="", ttl="1h")
    return main.cmd_dispatch_hold(argparse.Namespace(**{**args, **over}))


# 1. pause stops each configured running service; resume starts them in resume_order.

def test_pause_stops_each_configured_service_and_resume_starts_them_in_resume_order(pm2):
    pm2.configure(CONFIG)
    state = fleet.pause_all(by="@0")
    assert pm2.argvs() == [["jlist"], ["stop", "game-agent"], ["stop", "game-tracker"],
                           ["stop", "game-mirror"]]
    assert state["services"] == {"game-agent": "stopped", "game-tracker": "stopped",
                                 "game-mirror": "stopped"}
    assert fleet.read()["services"] == state["services"]
    assert pm2.status() == {"game-agent": "stopped", "game-tracker": "stopped",
                            "game-mirror": "stopped", "unrelated": "online"}
    pm2.clear()
    resumed = fleet.resume_all()
    assert pm2.argvs() == [["start", "game-mirror"], ["start", "game-agent"],
                           ["start", "game-tracker"]]
    assert list(resumed["services"].items()) == [
        ("game-mirror", True), ("game-agent", True), ("game-tracker", True)]
    assert set(pm2.status().values()) == {"online"}


def test_the_stop_and_start_order_follows_the_config_not_a_sort(pm2):
    pm2.configure({"extra_services": ["c", "a", "b"], "resume_order": ["b", "c", "a"]})
    pm2.set(a="online", b="online", c="online")
    fleet.pause_all()
    assert pm2.argvs()[1:] == [["stop", "c"], ["stop", "a"], ["stop", "b"]]
    pm2.clear()
    fleet.resume_all()
    assert pm2.argvs() == [["start", "b"], ["start", "c"], ["start", "a"]]


def test_a_stopped_service_resume_order_omits_starts_last_and_an_unknown_one_is_ignored(pm2):
    pm2.configure({"extra_services": ["a", "b", "c"], "resume_order": ["ghost", "c", "a"]})
    pm2.set(a="online", b="online", c="online", ghost="stopped")
    fleet.pause_all()
    pm2.clear()
    assert list(fleet.resume_all()["services"]) == ["c", "a", "b"]
    assert pm2.argvs() == [["start", "c"], ["start", "a"], ["start", "b"]]
    assert pm2.status()["ghost"] == "stopped"


def test_without_resume_order_services_start_in_extra_services_order(pm2):
    pm2.configure({"extra_services": ["game-mirror", "game-agent"]})
    fleet.pause_all()
    pm2.clear()
    fleet.resume_all()
    assert pm2.argvs() == [["start", "game-mirror"], ["start", "game-agent"]]


# 2. the negative control: a service already down before the pause stays down after it.

@pytest.mark.parametrize("before", ["stopped", "errored", "absent"])
def test_a_service_already_down_is_never_stopped_nor_started(pm2, before):
    pm2.configure(CONFIG)
    procs = {"game-agent": "online", "game-tracker": before, "game-mirror": "online"}
    pm2.set(**{n: s for n, s in procs.items() if s != "absent"})
    state = fleet.pause_all()
    assert ["stop", "game-tracker"] not in pm2.argvs()
    assert state["services"]["game-tracker"] == "not running"
    pm2.clear()
    resumed = fleet.resume_all()
    assert pm2.argvs() == [["start", "game-mirror"], ["start", "game-agent"]]
    assert "game-tracker" not in resumed["services"]
    assert pm2.status().get("game-tracker") == (None if before == "absent" else before)


@pytest.mark.parametrize("status", ["online", "launching", "waiting restart"])
def test_each_up_status_counts_as_running(pm2, status):
    pm2.configure({"extra_services": ["a"]})
    pm2.set(a=status)
    assert fleet.pause_all()["services"] == {"a": "stopped"}


# 3. a TTL self-release restarts them too.

def test_ttl_expiry_restarts_the_stopped_services_in_order(pm2):
    pm2.configure(CONFIG)
    hold.take(ttl_seconds=1)
    fleet.pause_all()
    pm2.clear()
    lines = fleet.tick(hold.active(now=time.time() + 5))
    assert pm2.argvs() == [["start", "game-mirror"], ["start", "game-agent"],
                           ["start", "game-tracker"]]
    assert lines[-3:] == ["fleet resume: service game-mirror started",
                          "fleet resume: service game-agent started",
                          "fleet resume: service game-tracker started"]
    assert fleet.read() is None


def test_a_live_hold_leaves_the_services_stopped(pm2):
    pm2.configure(CONFIG)
    held = hold.take(ttl_seconds=600)
    fleet.pause_all()
    pm2.clear()
    assert fleet.tick(held) == [] and pm2.argvs() == []


# 4. an empty or missing config changes nothing: no pm2 call at all.

@pytest.mark.parametrize("cfg", ["missing", None, {}, {"extra_services": []},
                                 {"extra_services": "game-agent"}, "game-agent",
                                 {"resume_order": ["game-agent"]},
                                 {"extra_services": ["", "  ", 7]}])
def test_an_empty_or_missing_config_changes_nothing(pm2, cfg):
    if cfg != "missing":
        userconfig._PATH.parent.mkdir(parents=True, exist_ok=True)
        userconfig._PATH.write_text(json.dumps({"pause_all": cfg}))
    state = fleet.pause_all()
    assert "services" not in state
    fleet.resume_all()
    assert pm2.argvs() == []
    assert set(pm2.status().values()) == {"online"}


def test_an_unreadable_config_file_changes_nothing(pm2):
    userconfig._PATH.parent.mkdir(parents=True, exist_ok=True)
    userconfig._PATH.write_text("{not json")
    assert fleet.extra_services() == ([], [])
    assert "services" not in fleet.pause_all() and pm2.argvs() == []


def test_a_config_read_that_raises_reads_as_no_services(pm2, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("bad")
    monkeypatch.setattr(userconfig, "get", boom)
    assert fleet.extra_services() == ([], [])


def test_extra_services_strips_and_dedupes_names(pm2):
    pm2.configure({"extra_services": [" a ", "b", "a", 3], "resume_order": ["b", "b"]})
    assert fleet.extra_services() == (["a", "b"], ["b", "a"])


# 5. pm2 failures fail closed: nothing it cannot account for is started on resume.

def test_an_unreadable_jlist_stops_nothing_and_resume_starts_nothing(pm2, capsys):
    pm2.configure(CONFIG)
    pm2.break_jlist()
    state = fleet.pause_all()
    assert pm2.argvs() == [["jlist"]]
    assert state["services"] == dict.fromkeys(CONFIG["extra_services"], "unknown")
    pm2.clear()
    assert fleet.resume_all()["services"] == {} and pm2.argvs() == []


@pytest.mark.parametrize("cp", [subprocess.CompletedProcess([], 1, "[]", ""),
                                subprocess.CompletedProcess([], 0, "{}", ""), None])
def test_a_failed_or_non_list_jlist_is_unknown(pm2, monkeypatch, cp):
    monkeypatch.setattr(fleet, "_pm2", lambda *a: cp)
    assert fleet._pm2_running() is None


def test_a_jlist_entry_that_is_not_a_dict_is_skipped(pm2, monkeypatch):
    out = json.dumps(["x", {"name": "a", "pm2_env": {"status": "online"}},
                      {"name": "b", "pm2_env": None}])
    monkeypatch.setattr(fleet, "_pm2", lambda *a: subprocess.CompletedProcess([], 0, out, ""))
    assert fleet._pm2_running() == {"a"}


def test_a_stop_that_fails_is_recorded_failed_not_started_on_resume_and_retried(pm2, caplog):
    pm2.configure(CONFIG)
    pm2.fail("stop", "game-tracker")
    state = fleet.pause_all()
    assert state["services"]["game-tracker"] == "failed"
    assert "pm2 stop game-tracker failed" in caplog.text
    assert "  service game-tracker: failed" in fleet.status_lines()
    pm2.clear()
    (userconfig._PATH.parent.parent / "home" / "fail-stop-game-tracker").unlink()
    again = fleet.pause_all()                      # re-pause retries the failed one only
    assert pm2.argvs() == [["jlist"], ["stop", "game-tracker"]]
    assert again["services"]["game-tracker"] == "stopped"


def test_a_re_pause_with_every_service_stopped_only_re_reads_pm2(pm2):
    pm2.configure(CONFIG)
    fleet.pause_all()
    pm2.clear()
    assert fleet.pause_all()["services"] == dict.fromkeys(CONFIG["extra_services"], "stopped")
    assert pm2.argvs() == [["jlist"]]


def test_a_re_pause_stops_again_one_restarted_by_hand_and_keeps_it_owed(pm2):
    pm2.configure(CONFIG)
    fleet.pause_all()
    pm2.set(**{**pm2.status(), "game-agent": "online"})
    pm2.clear()
    assert fleet.pause_all()["services"]["game-agent"] == "stopped"
    assert pm2.argvs() == [["jlist"], ["stop", "game-agent"]]


def test_a_re_pause_with_pm2_unreadable_keeps_the_earlier_record(pm2):
    pm2.configure(CONFIG)
    pm2.set(**{"game-agent": "online", "game-tracker": "stopped", "game-mirror": "online"})
    fleet.pause_all()
    pm2.break_jlist()
    assert fleet.pause_all()["services"] == {"game-agent": "stopped",
                                             "game-tracker": "not running",
                                             "game-mirror": "stopped"}


def test_a_re_pause_records_a_failed_stop_that_since_landed_as_stopped(pm2):
    pm2.configure({"extra_services": ["a"]})
    pm2.set(a="online")
    pm2.fail("stop", "a")
    assert fleet.pause_all()["services"] == {"a": "failed"}
    pm2.set(a="stopped")
    assert fleet.pause_all()["services"] == {"a": "stopped"}


@pytest.mark.parametrize("at_resume, started", [("stopped", True), ("online", False)])
def test_a_failed_stop_is_started_on_resume_only_when_pm2_now_says_it_is_down(
        pm2, at_resume, started):
    pm2.configure({"extra_services": ["a", "b"]})
    pm2.set(a="online", b="online")
    pm2.fail("stop", "a")
    fleet.pause_all()
    pm2.set(a=at_resume, b="stopped")
    pm2.clear()
    resumed = fleet.resume_all()["services"]
    assert resumed == ({"a": True, "b": True} if started else {"b": True})
    assert pm2.argvs() == [["jlist"], *([["start", "a"]] if started else []), ["start", "b"]]


def test_a_failed_stop_is_left_alone_on_resume_when_pm2_is_unreadable(pm2, caplog):
    pm2.configure({"extra_services": ["a", "b"]})
    pm2.set(a="online", b="online")
    pm2.fail("stop", "a")
    fleet.pause_all()
    pm2.break_jlist()
    pm2.clear()
    assert fleet.resume_all()["services"] == {"b": True}
    assert pm2.argvs() == [["jlist"], ["start", "b"]]
    assert "pm2 unreadable: check a by hand" in caplog.text


def test_option_like_and_chela_service_names_are_dropped(pm2, caplog):
    pm2.configure({"extra_services": ["-s", "chela-daemon", "a"],
                   "resume_order": ["chela-dashboard", "a"]})
    assert fleet.extra_services() == (["a"], ["a"])
    assert "pause_all.extra_services: ignoring -s, chela-daemon" in caplog.text
    assert "pause_all.resume_order: ignoring chela-dashboard" in caplog.text


def test_a_start_that_fails_is_reported_and_the_rest_still_start(pm2, caplog):
    pm2.configure(CONFIG)
    hold.take(ttl_seconds=1)
    fleet.pause_all()
    pm2.fail("start", "game-agent")
    lines = fleet.tick(hold.active(now=time.time() + 5))
    assert "fleet resume: service game-agent did NOT start: pm2 start it by hand" in lines
    assert "fleet resume: service game-tracker started" in lines
    assert pm2.status()["game-tracker"] == "online"
    assert "pm2 start game-agent failed" in caplog.text


# 6. every pm2 call runs under env -i with HOME, PATH (and PM2_HOME) only.

def test_pm2_runs_under_env_i_and_never_sees_a_leaked_pm2_env_var(pm2, monkeypatch):
    monkeypatch.setenv("name", "chela-daemon")      # what pm2 leaks into its children
    monkeypatch.setenv("status", "online")
    monkeypatch.setenv("CHELA_SECRET", "x")
    pm2.configure(CONFIG)
    fleet.pause_all()
    fleet.resume_all()
    calls = pm2.calls()
    assert len(calls) == 7
    for c in calls:
        env = {k: v for k, v in c["env"].items() if k != "LC_CTYPE"}  # python's own coercion
        assert env == {"HOME": os.environ["HOME"], "PATH": os.environ["PATH"],
                       "PM2_HOME": os.environ["PM2_HOME"]}


def test_pm2_argv_and_timeout_are_pinned(monkeypatch):
    seen = []
    monkeypatch.setattr(subprocess, "run", lambda argv, **kw: seen.append((argv, kw)) or
                        subprocess.CompletedProcess(argv, 0, "", ""))
    monkeypatch.setenv("HOME", "/h")
    monkeypatch.setenv("PATH", "/p")
    monkeypatch.setenv("PM2_HOME", "/pm")
    assert fleet._pm2("stop", "svc").returncode == 0
    monkeypatch.delenv("PM2_HOME")
    fleet._pm2("start", "svc")
    assert seen == [
        (["env", "-i", "HOME=/h", "PATH=/p", "PM2_HOME=/pm", "pm2", "stop", "svc"],
         {"capture_output": True, "text": True, "timeout": 60, "check": False}),
        (["env", "-i", "HOME=/h", "PATH=/p", "pm2", "start", "svc"],
         {"capture_output": True, "text": True, "timeout": 60, "check": False})]


@pytest.mark.parametrize("exc", [subprocess.TimeoutExpired(["pm2"], 60),
                                 PermissionError("denied"), FileNotFoundError("env")])
def test_a_pm2_call_that_raises_or_hangs_reads_as_failed(monkeypatch, caplog, exc):
    def run(*a, **k):
        raise exc
    monkeypatch.setattr(subprocess, "run", run)
    assert fleet._pm2("stop", "svc") is None
    assert "fleet pause: stop svc failed" in caplog.text


# 7. the surfaces: --hold-status, the dashboard snapshot, the CLI and Telegram resumes.

def test_hold_status_lists_the_stopped_services(pm2, capsys):
    pm2.configure(CONFIG)
    pm2.set(**{"game-agent": "online", "game-tracker": "stopped", "game-mirror": "online"})
    _cli(pause=True, all=True)
    capsys.readouterr()
    _cli(hold_status=True)
    out = capsys.readouterr().out.splitlines()
    assert out[-3:] == ["  service game-agent: stopped (pm2 stop)",
                        "  service game-tracker: not running",
                        "  service game-mirror: stopped (pm2 stop)"]


def test_the_dashboard_snapshot_lists_the_services_in_config_order(pm2):
    pm2.configure(CONFIG)
    pm2.set(**{"game-agent": "online", "game-tracker": "stopped", "game-mirror": "online"})
    fleet.pause_all()
    assert fleet.snapshot()["services"] == [
        {"name": "game-agent", "state": "stopped"},
        {"name": "game-tracker", "state": "not running"},
        {"name": "game-mirror", "state": "stopped"}]


def test_cli_resume_starts_them_and_says_which(pm2, capsys):
    pm2.configure(CONFIG)
    pm2.fail("start", "game-tracker")
    _cli(pause=True, all=True)
    capsys.readouterr()
    _cli(resume=True)
    out = capsys.readouterr().out
    assert "  service game-mirror: started\n  service game-agent: started\n" in out
    assert "  service game-tracker: ⚠ did NOT start — pm2 start it by hand" in out
    assert hold.read() is None


def test_telegram_resumeall_starts_them_and_says_which(pm2):
    pm2.configure(CONFIG)
    pm2.fail("start", "game-agent")
    assert "service game-mirror: stopped (pm2 stop)" in fleetcmd.pause_all()
    reply = fleetcmd.resume_all().splitlines()
    assert reply[1:4] == ["service game-mirror: started",
                          "service game-agent: ⚠️ did NOT start. pm2 start it.",
                          "service game-tracker: started"]
    assert pm2.status()["game-mirror"] == "online"
