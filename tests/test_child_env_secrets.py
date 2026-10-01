"""🔐 CMX-425 — secrets from ``chela.env`` / ``secrets.env`` (``LINEAR_API_KEY``, ``*_TOKEN``, …)
never reach a child the daemon launches: an agent or judge window, a workflow hook, a suite.

Two channels, both closed:

* the env a call site HANDS its child — :func:`chela.envutil.child_env` drops every
  :func:`~chela.envutil.is_secret` name;
* the tmux server's GLOBAL environment — a window inherits THAT, not the env of the client
  that ran ``new-window`` — so every window launch path first ``set-environment -gu``s the
  secret names :func:`~chela.envutil.scrub_tmux_secrets` finds there.

⛔ Values are fake and never printed: every assertion names keys only.
⛔ No live tmux: every tmux call is faked.
"""
from __future__ import annotations

import os
from types import SimpleNamespace

import pytest

from chela import envutil

_SECRETS = {"LINEAR_API_KEY": "x", "FOO_TOKEN": "y", "BAR_SECRET": "z", "DB_PASSWORD": "w"}
# Deliberately forwarded: Claude Code's own credential + one an operator names.
_FORWARD = {"CLAUDE_CODE_OAUTH_TOKEN": "fwd-claude", "GH_TOKEN": "fwd-gh",
            "MY_HOOK_TOKEN": "fwd-op", "CHELA_CHILD_ENV_FORWARD": "MY_HOOK_TOKEN"}
_ORDINARY = {"CHELA_TMUX_SESSION": "chela-test", "CHELA_DIR": "/tmp/chela-x",
             "TOKENIZER": "ok", "KEYBOARD": "ok", "PM2_HOME": "/srv/pm2-home"}


@pytest.fixture
def secret_env(monkeypatch):
    for k, v in {**_SECRETS, **_FORWARD, **_ORDINARY}.items():
        monkeypatch.setenv(k, v)


def _effective(kwargs) -> dict:
    env = kwargs.get("env")
    return dict(os.environ if env is None else env)


def _assert_no_secret(env: dict) -> None:
    leaked = sorted(set(_SECRETS) & set(env))
    assert not leaked, f"child env still carries secrets: {leaked}"


# --- the helper -----------------------------------------------------------

def test_child_env_drops_the_secrets(secret_env):
    """🔴 Corrupt `is_secret` (or drop it from `child_env`'s filter) ⇒ RED."""
    _assert_no_secret(envutil.child_env())


def test_linear_api_key_is_dropped_by_name_even_if_the_pattern_were_narrower():
    assert envutil.is_secret("LINEAR_API_KEY")
    assert "LINEAR_API_KEY" in envutil.SECRET_ENV_VARS


@pytest.mark.parametrize("name", ["LINEAR_API_KEY", "FOO_TOKEN", "TELEGRAM_BOT_TOKEN",
                                  "AWS_SECRET", "DB_PASSWORD", "PASSWORD_FILE", "foo_token"])
def test_secret_shapes(name):
    assert envutil.is_secret(name)


@pytest.mark.parametrize("name", ["PATH", "HOME", "CHELA_TMUX_SESSION", "CHELA_DIR",
                                  "TOKENIZER", "KEYBOARD", "PM2_HOME", "LANG", "SSH_AUTH_SOCK"])
def test_ordinary_names_are_not_secrets(name):
    assert not envutil.is_secret(name)


def test_forwarded_secrets_reach_the_child(secret_env):
    """The intentionally-forwarded set: Claude Code's auth, gh's token, and an operator's
    `CHELA_CHILD_ENV_FORWARD` name. 🔴 Strip them anyway ⇒ RED."""
    env = envutil.child_env()
    for k in ("CLAUDE_CODE_OAUTH_TOKEN", "GH_TOKEN", "MY_HOOK_TOKEN"):
        assert env.get(k) == _FORWARD[k], f"{k} is forwarded on purpose but was stripped"


def test_operator_forward_list_is_what_lets_an_extra_secret_through(monkeypatch, secret_env):
    monkeypatch.delenv("CHELA_CHILD_ENV_FORWARD")
    assert "MY_HOOK_TOKEN" not in envutil.child_env()


def test_ordinary_vars_are_unchanged(monkeypatch, secret_env):
    """⭐ ACCEPTED: everything that is not a secret passes through byte-for-byte. Per defeat
    shape 402 the probe is the WHOLE env — every var currently set gets a sentinel — not a
    hand-picked sample, and the exact key set is compared too. The expectation is computed
    WITHOUT `is_secret` (a local regex only clears the runner's own real credentials first),
    so a strip widened to eat ordinary vars goes RED instead of agreeing with itself."""
    import re

    real_secret = re.compile(r"(_API_KEY|_TOKEN|_SECRET)$|PASSWORD", re.I)
    for k in list(os.environ):
        if k in _SECRETS or k in _FORWARD:
            continue
        if real_secret.search(k) or envutil.is_leaked(k):
            monkeypatch.delenv(k)
        else:
            monkeypatch.setenv(k, f"probe-{k}")
    expected = {k: v for k, v in os.environ.items() if k not in _SECRETS}
    env = envutil.child_env()
    assert env == expected
    for k in ("PATH", "HOME", *_ORDINARY):
        assert env[k] == f"probe-{k}"


# --- tmux's global environment ----------------------------------------------

_SHOW_ENV = ("PATH=/usr/bin\nLINEAR_API_KEY=x\nFOO_TOKEN=y\n-GONE_TOKEN\n"
             "GH_TOKEN=fwd\nCHELA_TMUX_SESSION=chela\nTOKENIZER=ok\n")


def test_tmux_secret_names_parses_show_environment(secret_env):
    assert envutil.tmux_secret_names(_SHOW_ENV) == ["LINEAR_API_KEY", "FOO_TOKEN"]


def _fake_tmux(monkeypatch, module):
    calls: list[tuple[list, dict]] = []

    def fake_run(argv, *a, **k):
        calls.append((list(argv) if not isinstance(argv, str) else argv, k))
        if isinstance(argv, list) and argv[:3] == ["tmux", "show-environment", "-g"]:
            return SimpleNamespace(stdout=_SHOW_ENV, stderr="", returncode=0)
        if isinstance(argv, list) and argv[:2] == ["tmux", "new-window"]:
            return SimpleNamespace(stdout="@100\n", stderr="", returncode=0)
        return SimpleNamespace(stdout="", stderr="", returncode=0)

    monkeypatch.setattr(module.subprocess, "run", fake_run)
    return calls


def _unset_before_new_window(calls) -> set[str]:
    nw = next(i for i, (c, _) in enumerate(calls)
              if isinstance(c, list) and c[:2] == ["tmux", "new-window"])
    return {c[3] for c, _ in calls[:nw]
            if isinstance(c, list) and c[:3] == ["tmux", "set-environment", "-gu"]}


def _new_window_env(calls) -> dict:
    nw = [k for c, k in calls if isinstance(c, list) and c[:2] == ["tmux", "new-window"]]
    assert len(nw) == 1
    return _effective(nw[0])


# --- every launch path ------------------------------------------------------

def _launch(monkeypatch, tmp_path, hooks, *, judge_window=False):
    import sqlite3

    import chela.dispatcher as dispatcher
    from chela.workflow import WorkflowDef

    calls = _fake_tmux(monkeypatch, dispatcher)
    monkeypatch.setattr(dispatcher, "_kill_windows_named", lambda *_a, **_k: None)
    monkeypatch.setattr(dispatcher, "_wait_for_ready", lambda *a, **k: True)
    monkeypatch.setattr(dispatcher, "_send_seed", lambda *a, **k: True)
    conn = dispatcher.ensure_schema(sqlite3.connect(":memory:"))
    wt = tmp_path / "wt"
    wt.mkdir(exist_ok=True)
    wf = WorkflowDef(path=tmp_path / "WORKFLOW.md",
                     config={"project_key": "CMX", "agent": {}, "hooks": hooks},
                     prompt_template="go {{workspace_path}}")
    dispatcher._launch_agent(wf, "t1", "cmx-1", wt, "go", conn, hook_vars={},
                             fresh_worktree=True, judge_window=judge_window,
                             role="judge" if judge_window else "coding")
    return calls


@pytest.mark.parametrize("judge_window", [False, True], ids=["agent", "judge"])
def test_agent_and_judge_spawn_carry_no_secret(monkeypatch, tmp_path, secret_env, judge_window):
    """🔴 Drop `scrub_tmux_secrets()` from the dispatcher's `_new_window` path, or the strip
    from `child_env` ⇒ RED: the window would inherit LINEAR_API_KEY / FOO_TOKEN."""
    calls = _launch(monkeypatch, tmp_path,
                    {"after_create": "echo created", "before_run": "echo before"},
                    judge_window=judge_window)
    assert _unset_before_new_window(calls) >= {"LINEAR_API_KEY", "FOO_TOKEN"}
    assert "GH_TOKEN" not in _unset_before_new_window(calls)
    env = _new_window_env(calls)
    _assert_no_secret(env)
    assert env.get("GH_TOKEN") == "fwd-gh"
    assert env.get("CHELA_TMUX_SESSION") == "chela-test"
    for hook in ("echo created", "echo before"):
        ks = [k for c, k in calls if c == hook]
        assert len(ks) == 1
        _assert_no_secret(_effective(ks[0]))
        assert _effective(ks[0]).get("MY_HOOK_TOKEN") == "fwd-op"


def test_after_done_hook_carries_no_secret(monkeypatch, tmp_path, secret_env):
    import chela.dispatcher as dispatcher
    from chela.workflow import WorkflowDef

    seen: list[dict] = []
    monkeypatch.setattr(dispatcher.subprocess, "Popen", lambda cmd, **k: seen.append(k))
    dispatcher._fire_after_done(WorkflowDef(
        path=tmp_path / "WORKFLOW.md",
        config={"project_key": "CMX", "agent": {}, "hooks": {"after_done": "true"}},
        prompt_template=""))
    assert len(seen) == 1
    _assert_no_secret(_effective(seen[0]))


def test_suite_run_carries_no_secret(monkeypatch, tmp_path, secret_env):
    import chela.judge as judge

    seen: list[dict] = []

    def fake_run(cmd, **k):
        seen.append(k)
        return SimpleNamespace(stdout="1 passed\n", stderr="", returncode=0)

    monkeypatch.setattr(judge.subprocess, "run", fake_run)
    judge.run_suite("true", tmp_path)
    assert len(seen) == 1
    env = _effective(seen[0])
    _assert_no_secret(env)
    assert env.get("PATH") == os.environ["PATH"]


def test_human_spawn_window_scrubs_tmux_secrets(monkeypatch, tmp_path, secret_env):
    from chela import agent_manager, discovery, spawn

    monkeypatch.setattr(discovery, "ensure_session", lambda *a, **k: True)
    monkeypatch.setattr(discovery, "get_all_windows", lambda: {})
    monkeypatch.setattr(agent_manager, "lock_window_name", lambda *a, **k: None)
    calls = _fake_tmux(monkeypatch, spawn)
    assert spawn.spawn_window(tmp_path).ok
    assert _unset_before_new_window(calls) == {"LINEAR_API_KEY", "FOO_TOKEN"}
    _assert_no_secret(_new_window_env(calls))


def test_share_sandbox_window_scrubs_tmux_secrets(monkeypatch, tmp_path, secret_env):
    from chela import agent_manager, discovery, share_sandbox, spawn

    monkeypatch.setattr(share_sandbox, "preflight", lambda real: "")
    monkeypatch.setattr(share_sandbox, "launcher_argv", lambda sid, real: ["docker", "run"])
    monkeypatch.setattr(discovery, "ensure_session", lambda *a, **k: True)
    monkeypatch.setattr(discovery, "get_all_windows", lambda: {})
    monkeypatch.setattr(agent_manager, "lock_window_name", lambda *a, **k: None)
    calls = _fake_tmux(monkeypatch, spawn)
    assert spawn.spawn_sandbox_window(tmp_path).ok
    assert _unset_before_new_window(calls) == {"LINEAR_API_KEY", "FOO_TOKEN"}
    _assert_no_secret(_new_window_env(calls))


def test_orchestrator_autolaunch_window_scrubs_tmux_secrets(monkeypatch, tmp_path, secret_env):
    from chela.personas import autolaunch

    calls = _fake_tmux(monkeypatch, autolaunch)
    try:
        autolaunch._spawn_orchestrator_window(str(tmp_path))
    except Exception:
        pass        # only the tmux calls up to and including new-window matter here
    assert _unset_before_new_window(calls) == {"LINEAR_API_KEY", "FOO_TOKEN"}
    _assert_no_secret(_new_window_env(calls))
