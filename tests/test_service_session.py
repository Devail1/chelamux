"""CMX-470: which tmux session a chela process drives, and the exact targets it uses.

* a SERVICE (daemon, dashboard, telegram, collab) ignores ``$TMUX_PANE``: it drives
  ``CHELA_TMUX_SESSION``, else ``chela`` — never the session of the shell that started it;
* an interactive agent still derives its session from its own pane;
* ``chela status`` refuses a missing ``chela`` instead of prefix-matching ``chela-services``;
* ``chela doctor`` warns about a service started in a pane, and about lookalike sessions.

The real-tmux tests run on a scratch socket through a PATH shim (as
``tests/test_terminals_selfheal.py`` does), so chela's bare ``tmux`` calls never reach the
operator's server.
"""
from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys
import types
import uuid

import pytest

from chela import config, discovery, main, runtime_truth
from chela.runtime_truth import OK, WARN, observed

TMUX_BIN = shutil.which("tmux")
needs_tmux = pytest.mark.skipif(TMUX_BIN is None, reason="tmux not installed")


@pytest.fixture(autouse=True)
def _not_a_service(monkeypatch):
    """Every test starts as a plain CLI process; `mark_service` is process-global."""
    monkeypatch.setattr(config, "_SERVICE", None)


def _stub_pane_lookup(monkeypatch, *, stdout="other\n", returncode=0, raises=None):
    calls: list[tuple] = []

    def fake_run(argv, **kw):
        calls.append((argv, kw))
        if raises is not None:
            raise raises
        return types.SimpleNamespace(returncode=returncode, stdout=stdout, stderr="")

    monkeypatch.setattr(config.subprocess, "run", fake_run)
    return calls


# --- session_source: the precedence table ---------------------------------------------

@pytest.mark.parametrize("env, service, pane, want", [
    ("pinned", None, None, ("pinned", "CHELA_TMUX_SESSION")),
    ("pinned", "daemon", "%5", ("pinned", "CHELA_TMUX_SESSION")),
    ("pinned", None, "%5", ("pinned", "CHELA_TMUX_SESSION")),
    (None, "daemon", "%5", ("chela", "service default ($TMUX_PANE ignored)")),
    (None, "dashboard", "%5", ("chela", "service default ($TMUX_PANE ignored)")),
    (None, None, "%5", ("other", "$TMUX_PANE")),
    (None, "daemon", None, ("chela", "default")),
    (None, None, None, ("chela", "default")),
])
def test_session_source_precedence(monkeypatch, env, service, pane, want):
    for var, value in (("CHELA_TMUX_SESSION", env), ("TMUX_PANE", pane)):
        if value is None:
            monkeypatch.delenv(var, raising=False)
        else:
            monkeypatch.setenv(var, value)
    monkeypatch.setattr(config, "_SERVICE", service)
    calls = _stub_pane_lookup(monkeypatch)
    assert config.session_source() == want
    assert config.current_session() == want[0]
    # only the interactive pane case ever asks tmux
    assert bool(calls) is (want[1] == "$TMUX_PANE")


def test_pane_lookup_pins_its_full_argv(monkeypatch):
    monkeypatch.delenv("CHELA_TMUX_SESSION", raising=False)
    monkeypatch.setenv("TMUX_PANE", "%42")
    calls = _stub_pane_lookup(monkeypatch, stdout="myteam\n")
    assert config.session_source() == ("myteam", "$TMUX_PANE")
    assert calls == [(["tmux", "display-message", "-p", "-t", "%42", "#{session_name}"],
                      {"capture_output": True, "text": True, "timeout": 5})]


@pytest.mark.parametrize("kw", [
    {"raises": subprocess.TimeoutExpired(["tmux"], 5)},
    {"raises": FileNotFoundError("tmux")},
    {"returncode": 1, "stdout": ""},
    {"returncode": 0, "stdout": "  \n"},
])
def test_a_failed_pane_lookup_falls_back_to_chela(monkeypatch, kw):
    monkeypatch.delenv("CHELA_TMUX_SESSION", raising=False)
    monkeypatch.setenv("TMUX_PANE", "%42")
    _stub_pane_lookup(monkeypatch, **kw)
    assert config.session_source() == ("chela", "default")


def test_mark_service_is_what_flips_it(monkeypatch):
    monkeypatch.delenv("CHELA_TMUX_SESSION", raising=False)
    monkeypatch.setenv("TMUX_PANE", "%5")
    _stub_pane_lookup(monkeypatch)
    assert config.service_name() is None
    assert config.current_session() == "other"
    config.mark_service("telegram")
    assert config.service_name() == "telegram"
    assert config.current_session() == "chela"


# --- the services declare themselves, and say why ------------------------------------

def test_declare_service_logs_the_ignored_pane(monkeypatch, caplog):
    monkeypatch.delenv("CHELA_TMUX_SESSION", raising=False)
    monkeypatch.setenv("TMUX_PANE", "%9")
    calls = _stub_pane_lookup(monkeypatch)
    with caplog.at_level(logging.INFO, logger=main.log.name):
        assert main._declare_service("daemon") == "chela"
    assert config.service_name() == "daemon"
    assert calls == []
    [rec] = [r for r in caplog.records if "driving tmux session" in r.getMessage()]
    assert rec.levelno == logging.WARNING
    msg = rec.getMessage()
    assert "chela daemon: driving tmux session 'chela'" in msg
    assert "%9" in msg and "$TMUX_PANE ignored" in msg


def test_declare_service_logs_a_pinned_session_at_info(monkeypatch, caplog):
    monkeypatch.setenv("CHELA_TMUX_SESSION", "fleet")
    monkeypatch.setenv("TMUX_PANE", "%9")
    _stub_pane_lookup(monkeypatch)
    with caplog.at_level(logging.INFO, logger=main.log.name):
        assert main._declare_service("dashboard") == "fleet"
    [rec] = [r for r in caplog.records if "driving tmux session" in r.getMessage()]
    assert rec.levelno == logging.INFO
    assert rec.getMessage() == ("chela dashboard: driving tmux session 'fleet' "
                                "(from CHELA_TMUX_SESSION)")


class _Stop(BaseException):
    pass


@pytest.mark.parametrize("name, call", [
    ("daemon", lambda: main.cmd_run(types.SimpleNamespace())),
    ("collab", lambda: main.cmd_collab(types.SimpleNamespace())),
    ("telegram", lambda: main.cmd_telegram(types.SimpleNamespace(sandbox_relay=None))),
])
def test_each_service_entrypoint_declares_itself(monkeypatch, name, call):
    declared: list[str] = []

    def fake_declare(n):
        declared.append(n)
        raise _Stop

    monkeypatch.setattr(main, "_declare_service", fake_declare)
    with pytest.raises(_Stop):
        call()
    assert declared == [name]


def test_the_dashboard_entrypoint_marks_itself_a_service(monkeypatch):
    from chela.dashboard import app as dashboard_app
    marked: list[str] = []

    def fake_mark(n):
        marked.append(n)
        raise _Stop

    monkeypatch.setattr(config, "mark_service", fake_mark)
    with pytest.raises(_Stop):
        dashboard_app.main()
    assert marked == ["dashboard"]


def test_chela_dashboard_command_declares_the_service(monkeypatch):
    declared: list[str] = []

    def fake_declare(n):
        declared.append(n)
        raise _Stop

    monkeypatch.setattr(main, "_declare_service", fake_declare)
    with pytest.raises(_Stop):
        main.cmd_dashboard(types.SimpleNamespace(host=None, port=None))
    assert declared == ["dashboard"]


# --- chela doctor -----------------------------------------------------------------------

def _report(monkeypatch, *, session="chela", exists=True, sessions=("chela",)):
    return runtime_truth._session_report(
        session, observed({"exists": exists,
                           "sessions": None if sessions is None else list(sessions)}))


def _titles(findings, level=None):
    return [f.title for f in findings if level is None or f.level == level]


@pytest.mark.parametrize("sessions, want", [
    (["chela", "chela-services"], ["chela-services"]),
    (["chela", "chela2", "chelamux", "webterm_chela_1", "other"], ["chela2", "chelamux"]),
    (["chela"], []),
    (["webterm_chela_@1", "xchela"], []),
    (["chela-b", "chela-a"], ["chela-a", "chela-b"]),
])
def test_lookalike_sessions(sessions, want):
    assert runtime_truth.lookalike_sessions("chela", sessions) == want


def test_doctor_warns_on_a_lookalike_session(monkeypatch):
    monkeypatch.setenv("CHELA_TMUX_SESSION", "chela")
    out = _report(monkeypatch, sessions=["chela", "chela-services"])
    assert _titles(out, WARN) == [
        "tmux session(s) 'chela-services' start with 'chela' but aren't it"]
    assert _titles(out, OK) == ["tmux session 'chela' exists (CHELA_TMUX_SESSION)"]


def test_doctor_is_quiet_with_no_lookalike(monkeypatch):
    monkeypatch.setenv("CHELA_TMUX_SESSION", "chela")
    assert _titles(_report(monkeypatch), WARN) == []


def test_doctor_warns_when_it_cannot_list_sessions(monkeypatch):
    monkeypatch.setenv("CHELA_TMUX_SESSION", "chela")
    out = _report(monkeypatch, sessions=None)
    assert _titles(out, WARN) == [
        "cannot list tmux sessions — lookalike session names are unchecked"]


def test_doctor_with_no_server_says_missing_once(monkeypatch):
    monkeypatch.setenv("CHELA_TMUX_SESSION", "chela")
    out = _report(monkeypatch, exists=False, sessions=None)
    assert _titles(out, WARN) == [
        "tmux session 'chela' does NOT EXIST — tmux says so (CHELA_TMUX_SESSION)"]


def test_doctor_warns_a_service_started_in_a_pane(monkeypatch):
    monkeypatch.delenv("CHELA_TMUX_SESSION", raising=False)
    monkeypatch.setenv("TMUX_PANE", "%3")
    calls = _stub_pane_lookup(monkeypatch)
    config.mark_service("daemon")
    out = _report(monkeypatch)
    assert calls == []
    assert _titles(out, WARN) == [
        "service 'daemon' was started inside a tmux pane — ignoring $TMUX_PANE, "
        "driving 'chela'"]
    assert _titles(out, OK) == [
        "tmux session 'chela' exists (service default ($TMUX_PANE ignored))"]


def test_doctor_warns_a_session_derived_from_the_pane(monkeypatch):
    monkeypatch.delenv("CHELA_TMUX_SESSION", raising=False)
    monkeypatch.setenv("TMUX_PANE", "%3")
    _stub_pane_lookup(monkeypatch, stdout="other\n")
    out = _report(monkeypatch, session="other", sessions=["other"])
    assert _titles(out, WARN) == [
        "tmux session 'other' — DERIVED from $TMUX_PANE, not configured"]


def test_doctor_is_quiet_for_a_pinned_service(monkeypatch):
    monkeypatch.setenv("CHELA_TMUX_SESSION", "chela")
    monkeypatch.setenv("TMUX_PANE", "%3")
    config.mark_service("daemon")
    assert _titles(_report(monkeypatch), WARN) == []


def test_session_read_observes_existence_and_the_session_list(monkeypatch):
    monkeypatch.setenv("CHELA_TMUX_SESSION", "chela")
    monkeypatch.setattr(discovery, "session_exists", lambda s: s == "chela")
    monkeypatch.setattr(discovery, "list_sessions", lambda: ["chela", "chela-x"])
    obs = runtime_truth._session_read()
    assert obs.value == {"exists": True, "sessions": ["chela", "chela-x"]}


# --- discovery.list_sessions ------------------------------------------------------------

def test_list_sessions_pins_argv_and_parses(monkeypatch):
    seen = []

    def fake_run(argv, **kw):
        seen.append((argv, kw))
        return types.SimpleNamespace(returncode=0, stdout="chela\n\n chela-services \n")

    monkeypatch.setattr(discovery.subprocess, "run", fake_run)
    assert discovery.list_sessions() == ["chela", "chela-services"]
    assert seen == [(["tmux", "list-sessions", "-F", "#{session_name}"],
                     {"capture_output": True, "text": True, "timeout": 5})]


@pytest.mark.parametrize("raises, returncode", [
    (subprocess.TimeoutExpired(["tmux"], 5), 0),
    (FileNotFoundError("tmux"), 0),
    (None, 1),
])
def test_list_sessions_unknown_is_none(monkeypatch, raises, returncode):
    def fake_run(argv, **kw):
        if raises is not None:
            raise raises
        return types.SimpleNamespace(returncode=returncode, stdout="chela\n")

    monkeypatch.setattr(discovery.subprocess, "run", fake_run)
    assert discovery.list_sessions() is None


# --- chela status refuses a missing session ---------------------------------------------

@pytest.mark.parametrize("session", ["chela", "fleet"])
def test_status_refuses_a_missing_session(monkeypatch, capsys, session):
    monkeypatch.setenv("CHELA_TMUX_SESSION", session)
    asked: list = []
    monkeypatch.setattr(discovery, "session_exists", lambda s=None: asked.append(s) or False)
    monkeypatch.setattr(discovery, "get_all_windows",
                        lambda: pytest.fail("listed windows of a missing session"))
    with pytest.raises(SystemExit) as exc:
        main.cmd_status(types.SimpleNamespace(sessions=False))
    assert exc.value.code == 1
    assert asked in ([session], [None])          # it asked about THE session, not another
    assert f"no session {session}:" in capsys.readouterr().err


def test_status_lists_an_existing_session(monkeypatch, capsys):
    monkeypatch.setenv("CHELA_TMUX_SESSION", "fleet")
    asked: list = []
    monkeypatch.setattr(discovery, "session_exists", lambda s=None: asked.append(s) or True)
    monkeypatch.setattr(discovery, "get_all_windows", lambda: {})
    main.cmd_status(types.SimpleNamespace(sessions=False))
    assert asked in (["fleet"], [None])
    assert "No windows found in tmux session 'fleet'." in capsys.readouterr().out


# --- against a REAL tmux server (scratch socket) -----------------------------------------

@pytest.fixture
def scratch_tmux(tmp_path, monkeypatch):
    """A private tmux server reached by bare `tmux` through a PATH shim."""
    sock = f"chela-cmx470-{uuid.uuid4().hex[:8]}"
    shim = tmp_path / "shim"
    shim.mkdir()
    (shim / "tmux").write_text(f'#!/bin/sh\nexec {TMUX_BIN} -L {sock} "$@"\n')
    (shim / "tmux").chmod(0o755)
    monkeypatch.setenv("PATH", f"{shim}:{os.environ['PATH']}")
    monkeypatch.delenv("TMUX", raising=False)
    monkeypatch.delenv("TMUX_PANE", raising=False)

    def tmux(*args):
        return subprocess.run([TMUX_BIN, "-L", sock, *args], capture_output=True,
                              text=True, timeout=10)

    yield tmux
    tmux("kill-server")


@needs_tmux
def test_real_tmux_status_refuses_with_only_chela_services(scratch_tmux, tmp_path):
    scratch_tmux("new-session", "-d", "-s", "chela-services", "-n", "svc")
    env = {k: v for k, v in os.environ.items()
           if k not in ("CHELA_TMUX_SESSION", "TMUX", "TMUX_PANE")}
    env["CHELA_DIR"] = str(tmp_path / "chela-dir")
    proc = subprocess.run([sys.executable, "-m", "chela.main", "status"], env=env,
                          capture_output=True, text=True, timeout=60, stdin=subprocess.DEVNULL)
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "no session chela" in proc.stderr
    assert "svc" not in proc.stdout


@needs_tmux
def test_real_tmux_exact_targets_never_reach_a_lookalike(scratch_tmux, monkeypatch):
    scratch_tmux("new-session", "-d", "-s", "chela-services", "-n", "svc")
    monkeypatch.setenv("CHELA_TMUX_SESSION", "chela")
    assert discovery.session_exists("chela") is False
    assert discovery.get_windows_by_id() == {}
    # the same lookup with a bare target WOULD have matched it — the bug this closes
    assert scratch_tmux("has-session", "-t", "chela").returncode == 0


@needs_tmux
def test_real_tmux_a_service_inside_another_session_drives_chela(scratch_tmux, monkeypatch):
    scratch_tmux("new-session", "-d", "-s", "chela", "-n", "agent-1")
    scratch_tmux("new-session", "-d", "-s", "other", "-n", "shell")
    pane = scratch_tmux("display-message", "-p", "-t", "=other:", "#{pane_id}").stdout.strip()
    assert pane.startswith("%")
    monkeypatch.delenv("CHELA_TMUX_SESSION", raising=False)
    monkeypatch.setenv("TMUX_PANE", pane)
    # an interactive agent in that pane still derives its own session ...
    assert config.current_session() == "other"
    assert sorted(discovery.get_all_windows()) == ["shell"]
    # ... a service started there drives chela
    config.mark_service("daemon")
    assert config.current_session() == "chela"
    assert sorted(discovery.get_all_windows()) == ["agent-1"]


# --- scripts/agent-terminals.sh: its OWN functions, run against a real tmux --------------

_AGENT_TERMINALS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                "scripts", "agent-terminals.sh")


def _sh_functions(*names: str) -> str:
    """The named one-line / multi-line function definitions, cut verbatim out of the real
    script (it runs its supervisor loop when sourced, so it can't just be sourced)."""
    import re
    text = open(_AGENT_TERMINALS).read()
    out = []
    for name in names:
        m = (re.search(rf"^{name}\(\) \{{.*\}}[^\n]*$", text, re.M)
             or re.search(rf"^{name}\(\) \{{\n.*?^\}}$", text, re.M | re.S))
        assert m, f"{name}() not found in agent-terminals.sh"
        out.append(m.group(0))
    return "\n".join(out)


def _run_sh(body: str, **env) -> subprocess.CompletedProcess:
    full = {k: v for k, v in os.environ.items() if k not in ("TMUX", "TMUX_PANE")}
    full.update(env)
    return subprocess.run(["bash", "-c", body], env=full, capture_output=True, text=True,
                          timeout=30, stdin=subprocess.DEVNULL)


def _sessions(tmux) -> set[str]:
    out = tmux("list-sessions", "-F", "#{session_name}").stdout
    return {s for s in out.split() if s}


@needs_tmux
def test_real_tmux_despawn_never_kills_a_prefix_sibling_viewer(scratch_tmux):
    """@1's viewer is already gone; @10's is live. despawn @1 must leave @10's alone —
    a bare `kill-session -t webterm_chela__1` prefix-matches `webterm_chela__10`."""
    scratch_tmux("new-session", "-d", "-s", "webterm_chela__10")
    scratch_tmux("new-session", "-d", "-s", "webterm_chela__2")
    body = (_sh_functions("at_get", "at_del", "sanitize", "despawn")
            + '\nWEBTERM_PREFIX=webterm_chela_\nTRACKED="@1 @10 @2"\n'
            + 'despawn @1\ndespawn @2\n')
    r = _run_sh(body)
    assert r.returncode == 0, r.stderr
    assert _sessions(scratch_tmux) == {"webterm_chela__10"}


@pytest.mark.parametrize("present, ok", [
    (["chela-services"], False),
    (["chela-services", "chela"], True),
    (["chela"], True),
])
@needs_tmux
def test_real_tmux_session_present_is_exact(scratch_tmux, present, ok):
    """session_present only ASKS (CMX-473: the supervisor never creates the session) — and
    must not take `chela-services` for `chela`."""
    for name in present:
        scratch_tmux("new-session", "-d", "-s", name)
    body = (_sh_functions("tmux_tmpdir_missing", "session_present")
            + "\nsession_present\n")
    r = _run_sh(body, TMUX_SESSION="chela")
    assert (r.returncode == 0) is ok, r.stderr
    assert _sessions(scratch_tmux) == set(present)   # never created anything
