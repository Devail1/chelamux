"""CMX-467 — ``chela restart``: restart interactive sessions in place onto a new Claude Code.

Every tmux / process / store touch goes through :class:`chela.restart.Ops`, so these drive the
real :func:`chela.restart.restart_one` / :func:`chela.restart.restart_all` against a fake
fleet with a fake clock: a slow ``/exit`` is a session whose Claude exits N fake seconds after
the keys land.
"""
from __future__ import annotations

import subprocess
import sys
from types import SimpleNamespace

import pytest

from chela import restart, restore
from chela.spawn import SpawnResult

SID = "aaaaaaaa-1111-2222-3333-444444444444"
SID2 = "bbbbbbbb-1111-2222-3333-444444444444"
ORCH_SID = "cccccccc-1111-2222-3333-444444444444"


class Fleet:
    """A fake tmux fleet + clock. ``exit_after`` is how long after ``/exit`` Claude exits."""

    def __init__(self, windows: dict, *, exit_after: float = 5.0, status=None, runs=None,
                 orchestrator=None, topics=None, relaunch_alive=True, rebind_after=0.0,
                 drafts=None):
        self.t = 0.0
        self.windows = {w: dict(v) for w, v in windows.items()}
        self.exit_after = exit_after
        self.status_of = dict(status or {})
        self.runs_rows = list(runs or [])
        self.orch = orchestrator
        self.topics = dict(topics or {})
        self.relaunch_alive = relaunch_alive
        self.rebind_after = rebind_after
        self.drafts = dict(drafts or {})
        self.keys: list[tuple[str, list[str]]] = []
        self.exit_sent_at: dict[str, float] = {}
        self.killed: list[str] = []
        self.relaunched: list[tuple] = []
        self.registered: list[str] = []
        self.next_wid = 90
        self.pending_topics: dict[str, tuple[float, str]] = {}

    # --- Ops -----------------------------------------------------------------------------
    def window(self, wid):
        w = self.windows.get(wid)
        return None if w is None else {"name": w["name"], "cwd": w["cwd"]}

    def status(self, wid):
        s = self.status_of.get(wid, "idle")
        return s(self.t) if callable(s) else s

    def session_of(self, wid):
        w = self.windows.get(wid)
        return w and w.get("sid")

    def send_keys(self, wid, keys):
        self.keys.append((wid, keys))
        if keys == ["Enter"] and self.keys[-2:-1] == [(wid, ["-l", "/exit"])]:
            self.exit_sent_at[wid] = self.t

    def exited(self, wid):
        sent = self.exit_sent_at.get(wid)
        return sent is not None and self.t - sent >= self.exit_after

    def window_alive(self, wid):
        return wid in self.windows

    def kill_window(self, wid):
        self.killed.append(wid)
        self.windows.pop(wid, None)

    def relaunch(self, sid, cwd, *, name=None):
        self.relaunched.append((sid, cwd, name))
        new = f"@{self.next_wid}"
        self.next_wid += 1
        if not self.relaunch_alive:
            return restore.Relaunch(SpawnResult(ok=True, wid=new, name=name), False, False,
                                    False)
        self.windows[new] = {"name": name, "cwd": cwd, "sid": sid}
        old = [t for w, t in self.topics.items() if self.windows.get(w) is None and t]
        if old:
            self.pending_topics[new] = (self.t + self.rebind_after, old[0])
        return restore.Relaunch(SpawnResult(ok=True, wid=new, name=name), True, True, True,
                                " with its launch settings")

    def topic_for(self, wid):
        if wid in self.topics:
            return self.topics[wid]
        due = self.pending_topics.get(wid)
        return due[1] if due and self.t >= due[0] else None

    def register(self, wid):
        self.registered.append(wid)
        return {"ok": True, "orchestrator": wid}

    def sleep(self, s):
        self.t += s

    def ops(self) -> restart.Ops:
        return restart.Ops(
            window=self.window, status=self.status, session_of=self.session_of,
            runs=lambda: self.runs_rows, send_keys=self.send_keys, exited=self.exited,
            window_alive=self.window_alive, kill_window=self.kill_window,
            relaunch=self.relaunch, topic_for=self.topic_for,
            draft=lambda w: self.drafts.get(w, ""),
            orchestrator=lambda: self.orch, register_orchestrator=self.register,
            sleep=self.sleep, clock=lambda: self.t)


def _one(**kw):
    return Fleet({"@3": {"name": "tapparty", "cwd": "/srv/tap", "sid": SID}}, **kw)


# --------------------------------------------------------------------------
# one session: the happy path keeps session, name, cwd, ladder rung and topic
# --------------------------------------------------------------------------

def test_restart_keeps_session_id_name_cwd_and_reports_old_to_new_wid_step_and_topic():
    f = _one(topics={"@3": "4242"})
    r = restart.restart_one("@3", f.ops())
    assert f.keys == [("@3", ["Escape"]), ("@3", ["-l", "/exit"]), ("@3", ["Enter"])]
    assert f.killed == ["@3"]                     # the window was still open (a shell)
    assert f.relaunched == [(SID, "/srv/tap", "tapparty")]
    assert (r.outcome, r.wid, r.new_wid, r.session_id, r.step, r.topic) == (
        "restarted", "@3", "@90", SID, "settings+rc", "4242")
    assert r.line() == (f"✅ tapparty @3 → @90: resumed {SID[:8]} [settings+rc], "
                        "topic 4242 with its launch settings")


def test_a_window_that_closed_by_itself_is_not_killed_again():
    f = _one()
    orig = f.exited

    def exited(wid):
        done = orig(wid)
        if done:
            f.windows.pop(wid, None)     # /exit closed the window
        return done
    ops = f.ops()
    ops.exited = exited
    r = restart.restart_one("@3", ops)
    assert (r.outcome, f.killed) == ("restarted", [])


def test_the_topic_is_waited_for_when_chela_telegram_rebinds_a_little_later():
    f = _one(topics={"@3": "77"}, rebind_after=12.0)
    r = restart.restart_one("@3", f.ops())
    assert (r.topic, r.detail) == ("77", " with its launch settings")


def test_a_topic_not_rebound_in_time_is_reported_with_the_one_it_had():
    f = _one(topics={"@3": "77"}, rebind_after=999.0)
    r = restart.restart_one("@3", f.ops(), topic_wait=5)
    assert r.topic is None
    assert r.detail.endswith("(had topic 77; not rebound yet)")
    assert "no topic" in r.line()


def test_a_session_rebound_to_a_different_topic_says_which_it_was():
    f = _one(topics={"@3": "77"})
    f.pending_topics = {}
    ops = f.ops()
    ops.topic_for = lambda w: "77" if w == "@3" else "88"
    r = restart.restart_one("@3", ops)
    assert (r.topic, r.detail.endswith("(was topic 77)")) == ("88", True)


def test_a_session_with_no_topic_does_not_wait_for_one():
    f = _one()
    t0 = []
    ops = f.ops()
    real = ops.topic_for

    def topic_for(w):
        t0.append(f.t)
        return real(w)
    ops.topic_for = topic_for
    r = restart.restart_one("@3", ops)
    assert r.topic is None and t0 == [0.0]       # read once, before the exit — no polling


# --------------------------------------------------------------------------
# busy is never touched
# --------------------------------------------------------------------------

@pytest.mark.parametrize("status", ["busy", "waiting", None])
def test_a_session_that_is_not_idle_is_not_touched(status):
    f = _one(status={"@3": status})
    r = restart.restart_one("@3", f.ops())
    assert (r.outcome, f.keys, f.relaunched, f.killed) == ("busy", [], [], [])
    assert r.detail == f"not idle ({status or 'status unknown'})"
    assert r.line().startswith("⏸ tapparty @3: busy")


def test_wait_waits_for_idle_then_restarts():
    f = _one(status={"@3": lambda t: "idle" if t >= 40 else "busy"})
    r = restart.restart_one("@3", f.ops(), wait_idle=100)
    assert r.outcome == "restarted"
    assert f.exit_sent_at["@3"] >= 40


def test_wait_gives_up_at_its_deadline_without_touching_it():
    f = _one(status={"@3": "busy"})
    r = restart.restart_one("@3", f.ops(), wait_idle=60)
    assert (r.outcome, r.detail, f.keys) == ("busy", "not idle after waiting 60s", [])
    assert f.t == 60


# --------------------------------------------------------------------------
# a slow exit is waited for — and the old 30 s cutoff left it down (negative control)
# --------------------------------------------------------------------------

def test_an_exit_slower_than_30s_is_waited_for():
    f = _one(exit_after=45)
    r = restart.restart_one("@3", f.ops())
    assert r.outcome == "restarted"
    assert f.relaunched == [(SID, "/srv/tap", "tapparty")]


def test_the_default_exit_wait_is_120_seconds():
    assert restart.EXIT_TIMEOUT_S == 120
    f = _one(exit_after=119)
    assert restart.restart_one("@3", f.ops()).outcome == "restarted"
    f = _one(exit_after=125)
    r = restart.restart_one("@3", f.ops())
    assert (r.outcome, f.relaunched) == ("exit-timeout", [])
    assert r.detail == "/exit sent, Claude still running after 120s — left as it is"


def test_negative_control_the_old_30s_cutoff_leaves_the_session_down():
    """The first hand-written restart gave up at 30 s. Claude then exited on its own at
    45 s — and nothing relaunched it: the session was down."""
    f = _one(exit_after=45)
    r = restart.restart_one("@3", f.ops(), exit_timeout=30)
    assert (r.outcome, f.relaunched) == ("exit-timeout", [])
    f.sleep(20)                                    # Claude finishes exiting at t=45
    assert f.exited("@3")
    running = [w for w, v in f.windows.items() if v.get("sid") == SID and not f.exited(w)]
    assert running == []                           # down: no window runs the session


# --------------------------------------------------------------------------
# refusals: never the dispatcher's windows, never without a session
# --------------------------------------------------------------------------

@pytest.mark.parametrize("row, why", [
    ({"task_id": "T-1", "status": "running", "window_id": "@3"},
     "dispatched run T-1 owns this window"),
    ({"task_id": "T-2", "status": "awaiting_review", "judge_window_id": "@3"},
     "the judge of T-2 runs in this window"),
    ({"task_id": "T-3", "status": "running", "worktree_path": "/srv"},
     "its worktree belongs to run T-3, still in flight"),
])
def test_a_dispatched_run_or_judge_is_never_touched(row, why):
    f = _one(runs=[row])
    r = restart.restart_one("@3", f.ops())
    assert (r.outcome, r.detail, f.keys) == ("refused", f"{why} — the dispatcher owns it", [])


def test_a_terminal_run_that_once_owned_the_window_does_not_block_it():
    f = _one(runs=[{"task_id": "T-9", "status": "done", "window_id": "@3",
                    "judge_window_id": "@3", "worktree_path": "/srv/tap"}])
    assert restart.restart_one("@3", f.ops()).outcome == "restarted"


@pytest.mark.parametrize("sid", [None, "", "not-a-session; rm -rf ~"])
def test_no_valid_session_id_is_refused(sid):
    f = _one()
    f.windows["@3"]["sid"] = sid
    r = restart.restart_one("@3", f.ops())
    assert (r.outcome, r.detail, f.keys) == (
        "refused", "no live Claude session id for this window", [])


def test_an_unknown_cwd_is_refused():
    f = _one()
    f.windows["@3"]["cwd"] = None
    r = restart.restart_one("@3", f.ops())
    assert (r.outcome, r.detail, f.keys) == ("refused", "the window's cwd is unknown", [])


@pytest.mark.parametrize("draft, why", [
    ("refactor the foo module and", "its prompt holds an unsent draft — not typing /exit over it"),
    (None, "its prompt could not be read — not typing /exit blind"),
])
def test_an_unsent_draft_or_an_unreadable_prompt_is_never_typed_over(draft, why):
    f = _one(drafts={"@3": draft})
    r = restart.restart_one("@3", f.ops())
    assert (r.outcome, r.detail, f.keys, f.relaunched) == ("refused", why, [], [])


def test_the_draft_is_read_with_escapes_so_ghost_text_reads_empty(monkeypatch):
    from chela import messenger
    seen = []
    monkeypatch.setattr(messenger, "capture_pane",
                        lambda wid, ansi=False: seen.append((wid, ansi)) or "PANE")
    monkeypatch.setattr(messenger, "input_draft", lambda pane: "" if pane == "PANE" else "x")
    assert (restart._draft("@3"), seen) == ("", [("@3", True)])

    def boom(wid, ansi=False):
        raise OSError("tmux gone")
    monkeypatch.setattr(messenger, "capture_pane", boom)
    assert restart._draft("@3") is None


def test_a_dead_window_is_refused():
    r = restart.restart_one("@9", _one().ops())
    assert (r.outcome, r.detail) == ("refused", "not a live window")
    assert r.line() == "⛔ ? @9: refused — not a live window"


def test_a_relaunch_that_never_comes_up_is_reported_down_with_the_command_to_run():
    f = _one(relaunch_alive=False)
    r = restart.restart_one("@3", f.ops())
    assert r.outcome == "down"
    assert r.detail.endswith(f"by hand: cd /srv/tap && claude --resume {SID}")


# --------------------------------------------------------------------------
# the orchestrator: re-registered, and last
# --------------------------------------------------------------------------

def test_restarting_the_orchestrator_re_registers_it_at_its_new_address():
    f = _one(orchestrator="@3")
    r = restart.restart_one("@3", f.ops())
    assert f.registered == ["@90"]
    assert r.detail.endswith(", orchestrator re-registered")


def test_a_failed_re_registration_is_said():
    f = _one(orchestrator="@3")
    ops = f.ops()
    ops.register_orchestrator = lambda w: {"ok": False, "error": "no such window"}
    r = restart.restart_one("@3", ops)
    assert "re-registering the orchestrator FAILED: no such window" in r.detail


def test_a_non_orchestrator_is_not_registered():
    f = _one(orchestrator="@2")
    restart.restart_one("@3", f.ops())
    assert f.registered == []


def _fleet3(**kw):
    return Fleet({
        "@2": {"name": "orch", "cwd": "/srv/o", "sid": ORCH_SID},
        "@3": {"name": "a", "cwd": "/srv/a", "sid": SID},
        "@5": {"name": "b", "cwd": "/srv/b", "sid": SID2},
    }, orchestrator="@2", **kw)


def test_all_restarts_the_orchestrator_last_and_it_comes_back_registered():
    f = _fleet3()
    out = restart.restart_all(f.ops(), wids=["@2", "@3", "@5"], self_wid=None,
                              arm=lambda w: pytest.fail("nothing to arm"))
    assert [r.wid for r in out] == ["@3", "@5", "@2"]
    assert [s for s, _, _ in f.relaunched] == [SID, SID2, ORCH_SID]
    assert all(r.outcome == "restarted" for r in out)
    assert f.registered == [out[-1].new_wid]


def test_all_retries_a_busy_session_at_the_end():
    f = _fleet3(status={"@3": lambda t: "idle" if t > 0 else "busy"})
    out = restart.restart_all(f.ops(), wids=["@2", "@3", "@5"], self_wid=None,
                              arm=lambda w: pytest.fail("nothing to arm"))
    assert [s for s, _, _ in f.relaunched] == [SID2, SID, ORCH_SID]
    assert [r.outcome for r in out] == ["restarted"] * 3


def test_all_leaves_a_still_busy_session_busy():
    f = _fleet3(status={"@3": "busy"})
    out = restart.restart_all(f.ops(), wids=["@2", "@3", "@5"], self_wid=None,
                              arm=lambda w: pytest.fail("nothing to arm"))
    assert [r.outcome for r in out] == ["busy", "restarted", "restarted"]


def test_all_from_the_orchestrator_hands_itself_to_the_helper_last():
    f = _fleet3()
    armed = []
    out = restart.restart_all(
        f.ops(), wids=["@2", "@3", "@5"], self_wid="@2",
        arm=lambda w: armed.append((w, len(f.relaunched))) or restart.Result(w, outcome="armed"))
    assert armed == [("@2", 2)]                    # after both others were relaunched
    assert [r.outcome for r in out] == ["restarted", "restarted", "armed"]
    assert f.keys and all(w != "@2" for w, _ in f.keys)   # never /exit'ed inline


def test_a_busy_orchestrator_is_never_skipped_it_is_armed_to_restart_when_idle():
    f = _fleet3(status={"@2": "busy"})
    armed = []
    out = restart.restart_all(f.ops(), wids=["@2", "@3", "@5"], self_wid=None,
                              arm=lambda w: armed.append(w) or restart.Result(w, outcome="armed"))
    assert armed == ["@2"] and out[-1].outcome == "armed"


def test_all_hands_any_caller_s_own_window_to_the_helper():
    f = _fleet3()
    armed = []
    out = restart.restart_all(f.ops(), wids=["@2", "@3", "@5"], self_wid="@5",
                              arm=lambda w: armed.append(w) or restart.Result(w, outcome="armed"))
    assert armed == ["@5"]
    assert [r.outcome for r in out] == ["restarted", "armed", "restarted"]


def test_all_with_no_orchestrator_registered_restarts_everyone_in_order():
    f = _fleet3()
    f.orch = None
    out = restart.restart_all(f.ops(), wids=["@2", "@3", "@5"], self_wid=None,
                              arm=lambda w: pytest.fail("nothing to arm"))
    assert [r.wid for r in out] == ["@2", "@3", "@5"]


# --------------------------------------------------------------------------
# the detached helper
# --------------------------------------------------------------------------

def test_helper_env_drops_the_caller_s_window_and_pins_the_tmux_session(monkeypatch):
    monkeypatch.setattr(restart.config, "current_session", lambda: "chela")
    env = restart.helper_env({"CHELA_WID": "@2", "TMUX_PANE": "%4", "PATH": "/bin",
                              "CHELA_TMUX_SESSION": "webterm_x"})
    assert env == {"PATH": "/bin", "CHELA_TMUX_SESSION": "chela"}


def test_spawn_helper_starts_a_detached_chela_restart(tmp_path, monkeypatch):
    monkeypatch.setattr(restart.config, "current_session", lambda: "chela")
    seen = {}

    def popen(argv, **kw):
        seen.update(argv=argv, **kw)
        return SimpleNamespace(pid=4321)
    log = tmp_path / "sub" / "restart-helper.log"
    pid, path = restart.spawn_helper(["@2", "--wait"], popen=popen, log_path=log)
    assert (pid, path) == (4321, log)
    assert seen["argv"] == [sys.executable, "-m", "chela.main", "restart", "@2", "--wait",
                            "--helper"]
    assert seen["start_new_session"] is True
    assert seen["stdin"] is subprocess.DEVNULL and seen["stderr"] is subprocess.STDOUT
    assert seen["stdout"].name == str(log)
    assert seen["env"]["CHELA_TMUX_SESSION"] == "chela" and "CHELA_WID" not in seen["env"]
    assert seen["cwd"] == str(restart.Path.home())


def test_arm_helper_waits_for_idle_and_reports_armed(tmp_path):
    argvs = []
    r = restart.arm_helper("@2", log_path=tmp_path / "h.log",
                           popen=lambda argv, **kw: argvs.append(argv) or SimpleNamespace(pid=7))
    assert argvs[0][-3:] == ["@2", "--wait", "--helper"]
    assert (r.outcome, r.wid) == ("armed", "@2")
    assert r.detail.startswith("helper pid 7 restarts it once it is idle")
    assert r.line().startswith("⏳ ? @2: armed")


def test_arm_helper_that_cannot_start_is_refused_not_armed(tmp_path):
    def popen(argv, **kw):
        raise PermissionError("denied")
    r = restart.arm_helper("@2", popen=popen, log_path=tmp_path / "h.log")
    assert (r.outcome, r.detail) == ("refused", "could not start the helper: denied")


def test_helper_log_path_lives_in_the_chela_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(restart.config, "CHELA_DIR", tmp_path)
    assert restart.helper_log_path() == tmp_path / "restart-helper.log"


# --------------------------------------------------------------------------
# which Claude Code is installed / running
# --------------------------------------------------------------------------

@pytest.mark.parametrize("path, version", [
    ("/home/u/.local/share/claude/versions/2.1.330", "2.1.330"),
    ("/home/u/.local/share/claude/versions/2.1.330 (deleted)", "2.1.330"),
    ("/usr/bin/node", None),
    ("/usr/bin/node-22.1.0", None),               # not a Claude Code versions dir
    ("/x/claude/versions/2.1.330/claude", None),  # the version must be the file itself
    ("/opt/2.1.330/bin/claude", None),
    ("", None),
    (None, None),
])
def test_version_of_exe(path, version):
    assert restart.version_of_exe(path) == version


@pytest.mark.parametrize("running, installed, older", [
    ("2.1.329", "2.1.330", True),
    ("2.1.330", "2.1.330", False),
    ("2.1.331", "2.1.330", False),
    ("2.1.99", "2.1.100", True),                  # numeric, not lexical
    (None, "2.1.330", False),
    ("2.1.329", None, False),
])
def test_is_older(running, installed, older):
    assert restart.is_older(running, installed) is older


def test_running_version_reads_proc_exe(monkeypatch):
    monkeypatch.setattr(restart.os, "readlink",
                        lambda p: {"/proc/42/exe": "/x/claude/versions/2.1.329"}[p])
    assert restart.running_version(42) == "2.1.329"
    assert restart.running_version(None) is None

    def gone(p):
        raise FileNotFoundError(p)
    monkeypatch.setattr(restart.os, "readlink", gone)
    assert restart.running_version(43) is None


def test_installed_version_from_the_resolved_path(monkeypatch):
    monkeypatch.setattr(restart.os.path, "realpath", lambda p: "/x/claude/versions/2.1.330")
    assert restart.installed_version(which=lambda n: "/usr/local/bin/claude",
                                     run=lambda *a, **k: pytest.fail("no subprocess")) == "2.1.330"


def test_installed_version_falls_back_to_claude_version(monkeypatch):
    monkeypatch.setattr(restart.os.path, "realpath", lambda p: "/usr/lib/node_modules/cli.js")
    seen = {}

    def run(argv, **kw):
        seen.update(argv=argv, **kw)
        return SimpleNamespace(returncode=0, stdout="2.1.331 (Claude Code)\n")
    assert restart.installed_version(which=lambda n: "/usr/bin/claude", run=run) == "2.1.331"
    assert seen["argv"] == ["/usr/bin/claude", "--version"]
    assert (seen["timeout"], seen["stdin"]) == (10, subprocess.DEVNULL)


@pytest.mark.parametrize("outcome", [
    SimpleNamespace(returncode=1, stdout="2.1.331"),
    SimpleNamespace(returncode=0, stdout="garbage"),
    OSError("no exec"),
    subprocess.TimeoutExpired("claude", 10),
])
def test_installed_version_unknown_is_none(monkeypatch, outcome):
    monkeypatch.setattr(restart.os.path, "realpath", lambda p: "/usr/lib/cli.js")

    def run(*a, **k):
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome
    assert restart.installed_version(which=lambda n: "/usr/bin/claude", run=run) is None


def test_installed_version_without_claude_on_path_is_none():
    assert restart.installed_version(which=lambda n: None) is None


def test_stale_sessions_lists_older_interactive_ones_only():
    versions = {1: "2.1.329", 2: "2.1.330", 3: None, 4: "2.1.329"}
    out = restart.stale_sessions(
        installed="2.1.330",
        windows={"@1": "old", "@2": "new", "@3": "unknown", "@4": "dispatched"},
        pid_of=lambda w: int(w[1:]), version_of=versions.get,
        runs=[{"task_id": "T", "status": "running", "window_id": "@4"}])
    assert out == [{"wid": "@1", "name": "old", "version": "2.1.329"}]


# --------------------------------------------------------------------------
# the live wiring
# --------------------------------------------------------------------------

def test_exited_is_true_once_the_window_closed_or_the_pane_is_a_shell(monkeypatch):
    live = {"@3"}
    monkeypatch.setattr(restart.discovery, "get_windows_by_id", lambda: {w: "x" for w in live})
    cmds = []

    def tmux(*args):
        cmds.append(args)
        return SimpleNamespace(stdout=tmux.out)
    monkeypatch.setattr(restart, "_tmux", tmux)
    from chela import agent_manager
    pid = {"v": None}
    monkeypatch.setattr(agent_manager, "claude_pid", lambda w: pid["v"])
    for cmd, done in [("claude\n", False), ("node", False), ("bash\n", True), ("zsh", True)]:
        tmux.out = cmd
        assert restart._exited("@3") is done
    pid["v"] = 77                                  # a wrapper shows bash; Claude still runs
    tmux.out = "bash"
    assert restart._exited("@3") is False
    assert cmds[0] == ("display-message", "-p", "-t", "@3", "#{pane_current_command}")
    live.clear()
    assert restart._exited("@3") is True


def test_tmux_argv_and_send_keys(monkeypatch):
    seen = []
    monkeypatch.setattr(restart.subprocess, "run",
                        lambda argv, **kw: seen.append((argv, kw)) or SimpleNamespace())
    restart._send_keys("@3", ["-l", "/exit"])
    assert seen == [(["tmux", "send-keys", "-t", "@3", "-l", "/exit"],
                     {"capture_output": True, "text": True, "timeout": 10})]


def test_native_status_reads_the_session_feed_forced(monkeypatch):
    from chela import agent_manager, sandbox_status
    forced = []
    monkeypatch.setattr(agent_manager, "claude_pid", lambda w: 11)
    monkeypatch.setattr(agent_manager, "session_status_map",
                        lambda force=False: forced.append(force) or {"m": 1})
    monkeypatch.setattr(agent_manager, "session_entry",
                        lambda pid, m: {"status": "idle"} if pid == 11 else None)
    monkeypatch.setattr(sandbox_status, "resolve", lambda w, s: (s, None))
    assert restart._native_status("@3") == "idle"
    assert forced == [True]
    monkeypatch.setattr(agent_manager, "session_entry", lambda pid, m: None)
    assert restart._native_status("@3") is None


def test_session_of_prefers_the_live_session(monkeypatch):
    monkeypatch.setattr(restart.sessions, "live_session_of_window", lambda w: SID)
    monkeypatch.setattr(restart.sessions, "session_of_window", lambda w: SID2)
    assert restart._session_of("@3") == SID
    monkeypatch.setattr(restart.sessions, "live_session_of_window", lambda w: None)
    assert restart._session_of("@3") == SID2


def test_topic_for_reads_the_bindings_and_an_unreadable_file_is_no_topic(monkeypatch):
    from chela.telegram import bindings
    monkeypatch.setattr(bindings.BindingRegistry, "load",
                        classmethod(lambda cls, *a, **k: SimpleNamespace(
                            thread_for_window=lambda w: "55" if w == "@3" else None)))
    assert restart._topic_for("@3") == "55"

    def boom(cls, *a, **k):
        raise RuntimeError("corrupt")
    monkeypatch.setattr(bindings.BindingRegistry, "load", classmethod(boom))
    assert restart._topic_for("@3") is None


def test_interactive_windows_are_the_claude_ones_in_id_order(monkeypatch):
    from chela import agent_manager
    monkeypatch.setattr(restart.discovery, "get_windows_by_id",
                        lambda: {"@10": "a", "@2": "b", "@7": "shell"})
    monkeypatch.setattr(agent_manager, "claude_pid", lambda w: None if w == "@7" else 1)
    assert restart.interactive_windows() == ["@2", "@10"]


def test_live_ops_wires_restore_s_relaunch_core_and_inbox_register(monkeypatch):
    from chela import inbox
    calls = []
    monkeypatch.setattr(inbox, "register", lambda w, source="watch": calls.append((w, source)))
    monkeypatch.setattr(inbox, "load", lambda: {"orchestrator": "@2"})
    ops = restart.live_ops()
    assert ops.relaunch is restore.relaunch_session
    assert ops.kill_window is restore._default_kill_window
    assert (ops.draft, ops.exited, ops.status) == (restart._draft, restart._exited, restart._native_status)
    assert ops.orchestrator() == "@2"
    ops.register_orchestrator("@90")
    assert calls == [("@90", "restart")]
    monkeypatch.setattr(restart.discovery, "get_windows_by_id", lambda: {"@3": "tap"})
    monkeypatch.setattr(restart.discovery, "get_window_cwd_by_id", lambda w: "/srv/tap")
    assert ops.window("@3") == {"name": "tap", "cwd": "/srv/tap"}
    assert ops.window("@4") is None


# --------------------------------------------------------------------------
# the CLI
# --------------------------------------------------------------------------

def _cli(monkeypatch, argv, *, self_wid=None, results=None, all_results=None):
    from chela import inbox
    from chela import main as main_mod
    seen = {"one": [], "all": [], "arm": [], "enqueued": [], "spawned": []}
    monkeypatch.setattr(restart, "live_ops", lambda: "OPS")
    monkeypatch.setattr(main_mod.orchestrator, "self_wid", lambda: self_wid)
    monkeypatch.setattr(restart, "interactive_windows", lambda: ["@2", "@3"])

    def one(wid, ops, *, wait_idle=0.0):
        seen["one"].append((wid, ops, wait_idle))
        return (results or {}).get(wid, restart.Result(wid, outcome="restarted"))

    def all_(ops, *, wids, self_wid, arm):
        seen["all"].append((ops, wids, self_wid, arm))
        return all_results or []
    monkeypatch.setattr(restart, "restart_one", one)
    monkeypatch.setattr(restart, "restart_all", all_)
    monkeypatch.setattr(restart, "arm_helper",
                        lambda w: seen["arm"].append(w) or restart.Result(w, outcome="armed"))
    monkeypatch.setattr(restart, "spawn_helper",
                        lambda args: seen["spawned"].append(args) or (99, "/log"))
    monkeypatch.setattr(inbox, "enqueue",
                        lambda kind, summary, payload: seen["enqueued"].append((kind, summary)))
    monkeypatch.setattr(sys, "argv", ["chela", "restart", *argv])
    code = 0
    try:
        main_mod.main()
    except SystemExit as e:
        code = e.code or 0
    return code, seen


def test_cli_restarts_one_window_without_waiting(monkeypatch, capsys):
    code, seen = _cli(monkeypatch, ["3"])
    assert (code, seen["one"], seen["arm"], seen["enqueued"]) == (0, [("@3", "OPS", 0.0)], [], [])
    assert capsys.readouterr().out.startswith("✅ ? @3")


def test_cli_wait_waits_up_to_the_idle_budget(monkeypatch):
    assert restart.WAIT_IDLE_S == 1800
    _, seen = _cli(monkeypatch, ["@3", "--wait"])
    assert seen["one"] == [("@3", "OPS", 1800.0)]


@pytest.mark.parametrize("outcome", ["busy", "refused", "exit-timeout", "down"])
def test_cli_one_window_not_restarted_exits_1(monkeypatch, outcome):
    code, _ = _cli(monkeypatch, ["@3"], results={"@3": restart.Result("@3", outcome=outcome)})
    assert code == 1


def test_cli_own_window_is_handed_to_the_helper(monkeypatch):
    code, seen = _cli(monkeypatch, ["--self"], self_wid="@2")
    assert (code, seen["arm"], seen["one"]) == (0, ["@2"], [])


def test_cli_all_passes_the_interactive_windows_and_the_caller(monkeypatch):
    code, seen = _cli(monkeypatch, ["--all"], self_wid="@2", all_results=[
        restart.Result("@3", outcome="restarted"), restart.Result("@4", outcome="refused")])
    [(ops, wids, self_wid, arm)] = seen["all"]
    assert (code, ops, wids, self_wid) == (0, "OPS", ["@2", "@3"], "@2")
    assert arm is restart.arm_helper


def test_cli_all_a_session_left_busy_exits_1(monkeypatch):
    code, _ = _cli(monkeypatch, ["--all"], all_results=[restart.Result("@3", outcome="busy")])
    assert code == 1


def test_cli_helper_never_re_arms_itself_and_reports_to_the_inbox(monkeypatch):
    code, seen = _cli(monkeypatch, ["@2", "--wait", "--helper"], self_wid="@2")
    assert (code, seen["arm"], [w for w, _, _ in seen["one"]]) == (0, [], ["@2"])
    [(kind, summary)] = seen["enqueued"]
    assert kind == "restart" and summary.startswith("chela restart: ✅")


def test_cli_detach_spawns_the_helper_with_the_target(monkeypatch, capsys):
    _, seen = _cli(monkeypatch, ["--all", "--detach"])
    _, seen2 = _cli(monkeypatch, ["5", "--detach"])
    assert (seen["spawned"], seen2["spawned"]) == ([["--all"]], [["@5", "--wait"]])
    assert seen["one"] == seen["all"] == []
    assert "detached helper pid 99" in capsys.readouterr().out


def test_cli_several_windows_go_through_restart_all(monkeypatch):
    code, seen = _cli(monkeypatch, ["3", "@5"], all_results=[
        restart.Result("@3", outcome="restarted")])
    [(_, wids, _, _)] = seen["all"]
    assert (code, wids, seen["one"]) == (0, ["@3", "@5"], [])


def test_cli_detach_self_always_waits(monkeypatch):
    _, seen = _cli(monkeypatch, ["--self", "--detach"], self_wid="@2")
    _, seen2 = _cli(monkeypatch, ["3", "5", "--detach"])
    assert (seen["spawned"], seen2["spawned"]) == ([["@2", "--wait"]], [["@3", "@5"]])


def test_cli_another_restart_running_is_refused_as_busy(monkeypatch, tmp_path):
    import contextlib

    @contextlib.contextmanager
    def held(timeout=0.0):
        held.timeouts.append(timeout)
        raise restart.Busy("another chela restart is running")
        yield
    held.timeouts = []
    monkeypatch.setattr(restart, "one_at_a_time", held)
    code, seen = _cli(monkeypatch, ["@3"])
    assert (code, seen["one"], held.timeouts) == (1, [], [0.0])
    code, seen = _cli(monkeypatch, ["@3", "--helper"])
    assert held.timeouts[-1] == restart.WAIT_IDLE_S        # a helper waits its turn
    [(kind, summary)] = seen["enqueued"]
    assert summary == "chela restart: ⏸ ? @3: busy — another chela restart is running"


def test_cli_without_a_target_is_a_usage_error(monkeypatch):
    code, seen = _cli(monkeypatch, [], self_wid=None)
    assert (code, seen["one"]) == (2, [])


# --------------------------------------------------------------------------
# the dashboard routes
# --------------------------------------------------------------------------

@pytest.fixture
def dash(monkeypatch):
    from chela.dashboard import app as dash_mod
    monkeypatch.setattr(dash_mod, "_browser_refusal", lambda: None)
    monkeypatch.setattr(dash_mod.discovery, "get_windows_by_id", lambda: {"@3": "tap"})
    return dash_mod


def test_claude_version_route_lists_stale_sessions(dash, monkeypatch):
    monkeypatch.setattr(restart, "installed_version", lambda: "2.1.330")
    monkeypatch.setattr(dash.dispatcher, "list_runs", lambda: [])
    monkeypatch.setattr(dash.agent_manager, "claude_pid", lambda w: 7)
    monkeypatch.setattr(restart, "running_version", lambda pid: "2.1.329")
    body = dash.app.test_client().get("/api/sessions/claude-version").get_json()
    assert body == {"ok": True, "installed": "2.1.330",
                    "stale": [{"wid": "@3", "name": "tap", "version": "2.1.329"}]}


def test_claude_version_route_with_unknown_installed_offers_nothing(dash, monkeypatch):
    monkeypatch.setattr(restart, "installed_version", lambda: None)
    monkeypatch.setattr(restart, "stale_sessions", lambda **k: pytest.fail("not read"))
    body = dash.app.test_client().get("/api/sessions/claude-version").get_json()
    assert body == {"ok": True, "installed": None, "stale": []}


@pytest.mark.parametrize("payload, args", [
    ({"wid": "@3"}, ["@3", "--wait"]),
    ({"wids": ["@3"]}, ["@3", "--wait"]),
    ({"wids": ["@3", "@5", "@3"]}, ["@3", "@5"]),
    ({"all": True}, ["--all"]),
])
def test_restart_route_starts_the_detached_helper(dash, monkeypatch, payload, args):
    monkeypatch.setattr(dash.discovery, "get_windows_by_id", lambda: {"@3": "a", "@5": "b"})
    monkeypatch.setattr(restart, "running", lambda: False)
    spawned = []
    monkeypatch.setattr(restart, "spawn_helper", lambda a: spawned.append(a) or (321, "/l"))
    resp = dash.app.test_client().post("/api/sessions/restart", json=payload)
    assert (resp.status_code, spawned) == (200, [args])
    assert resp.get_json() == {"ok": True, "started": True, "pid": 321,
                               "detail": "Restarting; the result lands in the inbox"}


@pytest.mark.parametrize("payload, status", [
    ({}, 400), ({"wid": "3"}, 400), ({"wid": "@3; rm"}, 400), ({"wid": "@3\n"}, 400),
    ({"all": "yes"}, 400), ({"wid": 3}, 400), ({"wid": "@4"}, 404),
    ({"wids": []}, 400), ({"wids": "@3"}, 400), ({"wids": ["@3", "x"]}, 400),
    ({"wids": ["@3", "@4"]}, 404),
])
def test_restart_route_refuses_a_bad_target(dash, monkeypatch, payload, status):
    monkeypatch.setattr(restart, "spawn_helper", lambda a: pytest.fail("never spawned"))
    resp = dash.app.test_client().post("/api/sessions/restart", json=payload)
    assert (resp.status_code, resp.get_json()["ok"]) == (status, False)


def test_restart_route_requires_the_browser_proof(dash, monkeypatch):
    monkeypatch.setattr(dash, "_browser_refusal",
                        lambda: (dash.jsonify({"ok": False, "error": "no proof"}), 403))
    monkeypatch.setattr(restart, "spawn_helper", lambda a: pytest.fail("never spawned"))
    resp = dash.app.test_client().post("/api/sessions/restart", json={"all": True})
    assert resp.status_code == 403


def test_restart_route_refuses_while_a_restart_runs(dash, monkeypatch):
    monkeypatch.setattr(restart, "running", lambda: True)
    monkeypatch.setattr(restart, "spawn_helper", lambda a: pytest.fail("never spawned"))
    resp = dash.app.test_client().post("/api/sessions/restart", json={"all": True})
    assert (resp.status_code, resp.get_json()["error"]) == (
        409, "a restart is already running; its result lands in the inbox")


def test_restart_route_that_cannot_start_says_so(dash, monkeypatch):
    monkeypatch.setattr(restart, "running", lambda: False)
    def boom(a):
        raise OSError("no fork")
    monkeypatch.setattr(restart, "spawn_helper", boom)
    resp = dash.app.test_client().post("/api/sessions/restart", json={"all": True})
    assert (resp.status_code, resp.get_json()["error"]) == (
        500, "could not start the restart: no fork")


# --------------------------------------------------------------------------
# restore.relaunch_session — the core restart and restore share
# --------------------------------------------------------------------------

def _relaunch(*, alive, rc=True, settings=None, name_for=lambda s: None, name=None):
    spawned, killed = [], []

    def spawn_window(cwd, **kw):
        spawned.append((cwd, kw))
        return SpawnResult(ok=True, wid=f"@{50 + len(spawned)}", name=kw.get("name"))
    answers = iter(alive)
    r = restore.relaunch_session(
        SID, "/srv/tap", name=name, spawn_window=spawn_window,
        check_resumed=lambda wid, sid: next(answers), kill_window=killed.append,
        launch_settings_for=lambda sid: settings,
        replay_settings=lambda s: (["--channels", "x"], {}, [], []),
        name_for=name_for, remote_control_on=lambda: rc)
    return r, spawned, killed


@pytest.mark.parametrize("settings, rc, alive, step", [
    ({"k": 1}, True, [True], "settings+rc"),
    ({"k": 1}, True, [False, True], "settings"),
    ({"k": 1}, True, [False, False, True], "plain+rc"),
    ({"k": 1}, True, [False, False, False, True], "plain"),
    (None, True, [True], "plain+rc"),
    (None, False, [True], "plain"),
    ({"k": 1}, False, [True], "settings"),
])
def test_relaunch_ladder_rung_is_reported(settings, rc, alive, step):
    r, spawned, killed = _relaunch(alive=alive, rc=rc, settings=settings)
    assert (r.alive, r.step, len(spawned)) == (True, step, len(alive))
    assert killed == [f"@{51 + i}" for i in range(len(alive) - 1)]


def test_relaunch_window_name_falls_back_to_the_given_one_and_a_persisted_name_wins():
    _, spawned, _ = _relaunch(alive=[True], name="tapparty")
    assert spawned[0][1]["name"] == "tapparty"
    _, spawned, _ = _relaunch(alive=[True], name="tapparty", name_for=lambda s: "kept")
    assert spawned[0][1]["name"] == "kept"
    _, spawned, _ = _relaunch(alive=[True])
    assert "name" not in spawned[0][1]


def test_relaunch_that_never_comes_up_closes_every_window_it_opened():
    r, spawned, killed = _relaunch(alive=[False, False, False, False], settings={"k": 1})
    assert (r.alive, r.step, len(spawned)) == (False, "plain", 4)
    assert killed == ["@51", "@52", "@53", "@54"]


def test_relaunch_whose_spawn_failed_closes_nothing():
    killed = []
    r = restore.relaunch_session(
        SID, "/srv", spawn_window=lambda cwd, **kw: SpawnResult(ok=False, error="no tmux"),
        check_resumed=lambda w, s: pytest.fail("nothing to check"), kill_window=killed.append,
        launch_settings_for=lambda s: None, name_for=lambda s: None,
        remote_control_on=lambda: False)
    assert (r.alive, r.result.error, killed) == (False, "no tmux", [])


# --------------------------------------------------------------------------
# one restart at a time
# --------------------------------------------------------------------------

def test_one_at_a_time_refuses_a_second_holder_and_frees_on_exit(tmp_path):
    lock = tmp_path / "sub" / "restart.lock"
    assert restart.running(lock) is False
    with restart.one_at_a_time(path=lock):
        assert restart.running(lock) is True
        with pytest.raises(restart.Busy, match="another chela restart is running"):
            with restart.one_at_a_time(path=lock):
                pass
    assert restart.running(lock) is False


def test_one_at_a_time_waits_up_to_its_timeout(tmp_path):
    lock = tmp_path / "restart.lock"
    t = [0.0]
    with restart.one_at_a_time(path=lock):
        with pytest.raises(restart.Busy):
            with restart.one_at_a_time(30, path=lock, clock=lambda: t[0],
                                       sleep=lambda s: t.__setitem__(0, t[0] + s)):
                pass
    assert t[0] == 30


def test_lock_path_lives_in_the_chela_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(restart.config, "CHELA_DIR", tmp_path)
    assert restart.lock_path() == tmp_path / "restart.lock"


def test_relaunch_with_no_seams_uses_the_real_defaults(monkeypatch):
    """restart's live_ops calls relaunch_session bare — every default must be the real one."""
    seen = {}
    monkeypatch.setattr(restore.launch_settings, "for_session",
                        lambda sid: seen.setdefault("settings_for", sid) and {"rec": 1})
    monkeypatch.setattr(restore.launch_settings, "replay",
                        lambda s: seen.setdefault("replay", s) and (["--channels", "x"], {}, [], []))
    monkeypatch.setattr(restore.session_names, "name_for", lambda sid: "kept")
    monkeypatch.setattr(restore.config, "remote_control_enabled", lambda: False)

    def spawn_window(cwd, **kw):
        seen["spawn"] = (cwd, kw)
        return SpawnResult(ok=True, wid="@61", name=kw.get("name"))
    monkeypatch.setattr(restore.spawn_mod, "spawn_window", spawn_window)
    answers = iter([False, True])
    monkeypatch.setattr(restore, "_default_check_resumed", lambda wid, sid: next(answers))
    killed = []
    monkeypatch.setattr(restore, "_default_kill_window", killed.append)
    r = restore.relaunch_session(SID, "/srv/tap")
    assert (seen["settings_for"], seen["replay"]) == (SID, {"rec": 1})
    # the settings rung failed and was closed; the plain rung (no RC: it is off) came up
    assert seen["spawn"] == ("/srv/tap", {"command": f"claude --resume {SID}", "name": "kept"})
    assert (r.alive, r.step, killed) == (True, "plain", ["@61"])
